"""Triangulación (TIN) de Delaunay con líneas de quiebre y límite exterior e islas.

Proceso:
1. Puntos: elimina duplicados en XY (primero que llega gana, a 6 decimales, como el TIN de
   Herramientas) y filtra coordenadas no finitas.
2. Delaunay de los puntos y de los vértices de las líneas de quiebre (GEOS, por Shapely, igual
   que Herramientas para que sin quiebres el resultado sea el mismo).
3. Líneas de quiebre: se cortan entre sí (el cruce toma la Z interpolada en la primera línea y
   se avisa si la otra difiere) y cada tramo se fuerza como arista del TIN por intercambio de
   diagonales (Sloan); si un tramo pasa exactamente por un punto, se parte en él. Después se
   restituye la condición de Delaunay en las aristas no forzadas (Lawson).
4. Límite e islas: si el límite trae Z se usa; si es 2D se «cuelga» del TIN: sus vértices y sus
   cruces con las aristas toman la Z interpolada, de modo que la superficie dentro del límite no
   cambia. Se fuerzan sus tramos y se eliminan los triángulos de fuera y de las islas.
5. Filtros opcionales de Herramientas: arista máxima en planta y área mínima.

Los predicados geométricos se evalúan en coordenadas locales (restando el origen del conjunto)
para no perder precisión con coordenadas UTM.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Sequence

import numpy as np

__all__ = ["Tin", "EstadisticasTin", "crear_tin", "LineaQuiebre"]

Punto3 = tuple[float, float, float]


@dataclass
class EstadisticasTin:
    puntos_leidos: int = 0
    invalidos: int = 0
    duplicados: int = 0
    vertices_quiebre: int = 0
    cruces_quiebre: int = 0
    vertices_limite: int = 0
    triangulos_fuera: int = 0
    filtrados_arista: int = 0
    filtrados_area: int = 0


@dataclass
class LineaQuiebre:
    """Polilínea 3D que el TIN debe respetar. Un vértice con z=None toma la Z del terreno."""

    vertices: Sequence[tuple[float, float, float | None]]
    nombre: str = ""


@dataclass
class Tin:
    puntos: np.ndarray  # (n, 3) absolutos
    triangulos: np.ndarray  # (m, 3) índices, antihorarios
    aristas_forzadas: set[tuple[int, int]]
    estadisticas: EstadisticasTin
    avisos: list[str] = field(default_factory=list)
    _indice: object = None

    # --- consultas -----------------------------------------------------------------------
    def area_planta(self) -> float:
        p = self.puntos[self.triangulos]
        return float(np.abs(_cruz2(p)).sum() / 2.0)

    def area_superficie(self) -> float:
        p = self.puntos[self.triangulos]
        u, v = p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]
        return float(np.linalg.norm(np.cross(u, v), axis=1).sum() / 2.0)

    def aristas(self) -> set[tuple[int, int]]:
        t = self.triangulos
        e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
        e.sort(axis=1)
        return set(map(tuple, np.unique(e, axis=0).tolist()))

    def borde(self) -> list[tuple[int, int]]:
        """Aristas que pertenecen a un solo triángulo."""
        cuenta: dict[tuple[int, int], int] = defaultdict(int)
        for a, b, c in self.triangulos.tolist():
            for e in ((a, b), (b, c), (c, a)):
                cuenta[(min(e), max(e))] += 1
        return [e for e, k in cuenta.items() if k == 1]

    def elevacion(self, x: float, y: float) -> float | None:
        """Z interpolada en (x, y), o None fuera del TIN."""
        t = self._triangulo_en(x, y)
        if t is None:
            return None
        return _interpolar(self.puntos[self.triangulos[t]], x, y)

    def _triangulo_en(self, x, y):
        if self._indice is None:
            self._indice = _IndiceTriangulos(self.puntos, self.triangulos)
        return self._indice.buscar(x, y)


def _cruz2(p):
    return (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])


def _interpolar(tri, x, y):
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = tri
    det = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    l1 = ((y2 - y3) * (x - x3) + (x3 - x2) * (y - y3)) / det
    l2 = ((y3 - y1) * (x - x3) + (x1 - x3) * (y - y3)) / det
    l3 = 1.0 - l1 - l2
    return float(l1 * z1 + l2 * z2 + l3 * z3)


class _IndiceTriangulos:
    """Rejilla uniforme de triángulos para localizar puntos."""

    def __init__(self, puntos, triangulos):
        self.p = puntos
        self.t = triangulos
        tri = puntos[triangulos][:, :, :2]
        self.mn = tri.min(axis=1)
        self.mx = tri.max(axis=1)
        x0, y0 = self.mn.min(axis=0)
        x1, y1 = self.mx.max(axis=0)
        n = max(1, int(math.sqrt(len(triangulos))))
        self.x0, self.y0 = x0, y0
        self.cx = max((x1 - x0) / n, 1e-9)
        self.cy = max((y1 - y0) / n, 1e-9)
        self.n = n
        self.celdas: dict[tuple[int, int], list[int]] = defaultdict(list)
        i0 = np.floor((self.mn - [x0, y0]) / [self.cx, self.cy]).astype(int)
        i1 = np.floor((self.mx - [x0, y0]) / [self.cx, self.cy]).astype(int)
        for k in range(len(triangulos)):
            for i in range(i0[k, 0], i1[k, 0] + 1):
                for j in range(i0[k, 1], i1[k, 1] + 1):
                    self.celdas[(i, j)].append(k)

    def buscar(self, x, y):
        i = int(math.floor((x - self.x0) / self.cx))
        j = int(math.floor((y - self.y0) / self.cy))
        for k in self.celdas.get((i, j), ()):
            a, b, c = self.p[self.t[k]][:, :2]
            d1 = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])
            d2 = (c[0] - b[0]) * (y - b[1]) - (c[1] - b[1]) * (x - b[0])
            d3 = (a[0] - c[0]) * (y - c[1]) - (a[1] - c[1]) * (x - c[0])
            eps = -1e-9 * (self.cx + self.cy) ** 2
            if d1 >= eps and d2 >= eps and d3 >= eps:
                return k
        return None


# --- Triangulación con restricciones --------------------------------------------------------

class _Malla:
    """Triangulación mutable: triángulos antihorarios y mapa arista → triángulos."""

    def __init__(self, xy: list[list[float]], triangulos: Iterable[Sequence[int]], escala: float):
        self.xy = xy
        self.tri: dict[int, tuple[int, int, int]] = {}
        self.arista: dict[tuple[int, int], set[int]] = defaultdict(set)
        self.del_vertice: dict[int, set[int]] = defaultdict(set)
        self.forzadas: set[tuple[int, int]] = set()
        self.siguiente = 0
        self.eps = 1e-12 * escala * escala
        for t in triangulos:
            self._agregar(*t)

    # orientación > 0 si c está a la izquierda de a→b
    def orient(self, a, b, c) -> float:
        pa, pb, pc = self.xy[a], self.xy[b], self.xy[c]
        return (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0])

    def orient_p(self, a, b, p) -> float:
        pa, pb = self.xy[a], self.xy[b]
        return (pb[0] - pa[0]) * (p[1] - pa[1]) - (pb[1] - pa[1]) * (p[0] - pa[0])

    def _agregar(self, a, b, c) -> int:
        if self.orient(a, b, c) < 0:
            b, c = c, b
        k = self.siguiente
        self.siguiente += 1
        self.tri[k] = (a, b, c)
        for e in ((a, b), (b, c), (c, a)):
            self.arista[(min(e), max(e))].add(k)
        for v in (a, b, c):
            self.del_vertice[v].add(k)
        return k

    def _quitar(self, k):
        a, b, c = self.tri.pop(k)
        for e in ((a, b), (b, c), (c, a)):
            s = self.arista[(min(e), max(e))]
            s.discard(k)
            if not s:
                del self.arista[(min(e), max(e))]
        for v in (a, b, c):
            self.del_vertice[v].discard(k)

    def opuesto(self, k, a, b):
        return next(v for v in self.tri[k] if v != a and v != b)

    def voltear(self, a, b):
        """Cambia la diagonal a-b del cuadrilátero por c-d. Devuelve (c, d)."""
        t1, t2 = tuple(self.arista[(min(a, b), max(a, b))])
        c, d = self.opuesto(t1, a, b), self.opuesto(t2, a, b)
        self._quitar(t1)
        self._quitar(t2)
        self._agregar(a, c, d)
        self._agregar(b, d, c)
        return c, d

    def convexo(self, a, b, c, d) -> bool:
        """¿El cuadrilátero con diagonal a-b y vértices opuestos c, d es estrictamente convexo?"""
        return (self.orient(c, d, a) > self.eps and self.orient(c, d, b) < -self.eps) or (self.orient(c, d, a) < -self.eps and self.orient(c, d, b) > self.eps)

    def incirculo(self, a, b, c, d) -> float:
        """> 0 si d está dentro del círculo de a, b, c (antihorarios)."""
        ax, ay = self.xy[a][0] - self.xy[d][0], self.xy[a][1] - self.xy[d][1]
        bx, by = self.xy[b][0] - self.xy[d][0], self.xy[b][1] - self.xy[d][1]
        cx, cy = self.xy[c][0] - self.xy[d][0], self.xy[c][1] - self.xy[d][1]
        det = (ax * ax + ay * ay) * (bx * cy - cx * by) - (bx * bx + by * by) * (ax * cy - cx * ay) + (cx * cx + cy * cy) * (ax * by - bx * ay)
        if self.orient(a, b, c) < 0:
            det = -det
        return det

    def legalizar(self, pendientes: list[tuple[int, int]]):
        """Lawson: voltea aristas no forzadas que violan Delaunay hasta que no quede ninguna."""
        vistas = 0
        while pendientes:
            a, b = pendientes.pop()
            clave = (min(a, b), max(a, b))
            if clave in self.forzadas or clave not in self.arista or len(self.arista[clave]) != 2:
                continue
            t1, t2 = tuple(self.arista[clave])
            c, d = self.opuesto(t1, a, b), self.opuesto(t2, a, b)
            tri = self.tri[t1]
            if self.incirculo(tri[0], tri[1], tri[2], d) > self._umbral_incirculo(a, b, c, d) and self.convexo(a, b, c, d):
                self.voltear(a, b)
                pendientes.extend([(a, c), (c, b), (b, d), (d, a)])
            vistas += 1
            if vistas > 50_000_000:
                raise RuntimeError("La legalización del TIN no converge.")

    def _umbral_incirculo(self, a, b, c, d):
        xs = [self.xy[v][0] for v in (a, b, c, d)]
        ys = [self.xy[v][1] for v in (a, b, c, d)]
        tam = max(max(xs) - min(xs), max(ys) - min(ys))
        return 1e-12 * tam ** 4

    # --- forzar una arista -------------------------------------------------------------
    def _en_segmento(self, a, b, v) -> bool:
        if abs(self.orient(a, b, v)) > self.eps * 10:
            return False
        pa, pb, pv = self.xy[a], self.xy[b], self.xy[v]
        dot = (pv[0] - pa[0]) * (pb[0] - pa[0]) + (pv[1] - pa[1]) * (pb[1] - pa[1])
        largo = (pb[0] - pa[0]) ** 2 + (pb[1] - pa[1]) ** 2
        return 0 < dot < largo

    def _cruces(self, a, b):
        """Aristas que cruza el segmento a→b, o ("vertice", v) si pasa por un vértice v."""
        for k in self.del_vertice[a]:
            t = self.tri[k]
            i = t.index(a)
            p, q = t[(i + 1) % 3], t[(i + 2) % 3]
            if p == b or q == b:
                return []
            if self._en_segmento(a, b, p):
                return ("vertice", p)
            if self._en_segmento(a, b, q):
                return ("vertice", q)
            if self.orient(a, b, p) < 0 < self.orient(a, b, q) and self.orient(a, p, b) > 0 and self.orient(a, b, q) > 0:
                break
        else:
            raise RuntimeError("No se encontró el abanico de salida de la línea de quiebre.")
        cruces = [(p, q)]
        anterior = k
        while True:
            clave = (min(p, q), max(p, q))
            otros = self.arista[clave] - {anterior}
            if not otros:
                raise RuntimeError("La línea de quiebre sale del TIN.")
            k2 = next(iter(otros))
            w = self.opuesto(k2, p, q)
            if w == b:
                return cruces
            if self._en_segmento(a, b, w):
                return ("vertice", w)
            if self.orient(a, b, w) < 0:
                p = w
            else:
                q = w
            cruces.append((p, q))
            anterior = k2

    def forzar(self, a, b, profundidad=0, legalizar=True):
        if a == b:
            return
        if profundidad > 10_000:
            raise RuntimeError("Línea de quiebre demasiado fragmentada.")
        clave = (min(a, b), max(a, b))
        if clave in self.arista:
            self.forzadas.add(clave)
            return
        cruces = self._cruces(a, b)
        if isinstance(cruces, tuple):
            v = cruces[1]
            self.forzar(a, v, profundidad + 1, legalizar)
            self.forzar(v, b, profundidad + 1, legalizar)
            return
        nuevas = []
        cola = list(cruces)
        vueltas = 0
        while cola:
            p, q = cola.pop(0)
            clave_pq = (min(p, q), max(p, q))
            if clave_pq not in self.arista:
                continue
            t1, t2 = tuple(self.arista[clave_pq])
            c, d = self.opuesto(t1, p, q), self.opuesto(t2, p, q)
            if not self.convexo(p, q, c, d):
                cola.append((p, q))
                vueltas += 1
                if vueltas > 100 * (len(cruces) + 10):
                    raise RuntimeError("No se pudo forzar una línea de quiebre (geometría degenerada).")
                continue
            c, d = self.voltear(p, q)
            cruza = c not in (a, b) and d not in (a, b) and (self.orient(a, b, c) * self.orient(a, b, d) < 0)
            if cruza:
                cola.append((c, d))
            else:
                nuevas.append((c, d))
        self.forzadas.add(clave)
        if legalizar:
            self.legalizar([e for e in nuevas if (min(e), max(e)) != clave])

    # --- insertar un punto ---------------------------------------------------------------
    def _clasificar(self, k, p):
        a, b, c = self.tri[k]
        o = (self.orient_p(a, b, p), self.orient_p(b, c, p), self.orient_p(c, a, p))
        if all(x >= -self.eps for x in o):
            aristas = [(a, b), (b, c), (c, a)]
            cero = [i for i, x in enumerate(o) if abs(x) <= self.eps]
            if len(cero) == 1:
                return "arista", aristas[cero[0]]
            if len(cero) >= 2:
                comunes = set(aristas[cero[0]]) & set(aristas[cero[1]])
                return "vertice", next(iter(comunes))
            return "triangulo", k
        return None, o

    def localizar(self, p) -> tuple[str, object]:
        """Triángulo o arista que contiene p (caminata desde el último triángulo, con respaldo exhaustivo)."""
        k = getattr(self, "ultimo", None)
        if k not in self.tri:
            k = next(iter(self.tri))
        for _ in range(4 * len(self.tri) + 10):
            tipo, dato = self._clasificar(k, p)
            if tipo is not None:
                self.ultimo = k
                return tipo, dato
            a, b, c = self.tri[k]
            aristas = [(a, b), (b, c), (c, a)]
            i = min(range(3), key=lambda j: dato[j])
            e = aristas[i]
            otros = self.arista[(min(e), max(e))] - {k}
            if not otros:
                break
            k = next(iter(otros))
        for k in self.tri:
            tipo, dato = self._clasificar(k, p)
            if tipo is not None:
                return tipo, dato
        return "fuera", None

    def insertar(self, v, donde, legalizar=True) -> list[tuple[int, int]]:
        tipo, dato = donde
        pend = []
        if tipo == "triangulo":
            a, b, c = self.tri[dato]
            self._quitar(dato)
            for e in ((a, b), (b, c), (c, a)):
                self._agregar(e[0], e[1], v)
                pend.append(e)
        elif tipo == "arista":
            a, b = dato
            clave = (min(a, b), max(a, b))
            forzada = clave in self.forzadas
            for k in list(self.arista[clave]):
                c = self.opuesto(k, a, b)
                self._quitar(k)
                self._agregar(a, v, c)
                self._agregar(v, b, c)
                pend += [(a, c), (c, b)]
            if forzada:
                self.forzadas.discard(clave)
                self.forzadas.add((min(a, v), max(a, v)))
                self.forzadas.add((min(v, b), max(v, b)))
        if legalizar:
            self.legalizar(pend)
        self.ultimo = next(iter(self.del_vertice[v]))
        return pend


def _limpiar_puntos(puntos, decimales, est: EstadisticasTin):
    salida, vistos = [], {}
    for p in puntos:
        est.puntos_leidos += 1
        x, y, z = (float(p[0]), float(p[1]), float(p[2]))
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            est.invalidos += 1
            continue
        clave = (round(x, decimales), round(y, decimales))
        if clave in vistos:
            est.duplicados += 1
            continue
        vistos[clave] = len(salida)
        salida.append((x, y, z))
    return salida, vistos


def _delaunay_geos(xy: np.ndarray) -> np.ndarray:
    import shapely

    geom = shapely.delaunay_triangles(shapely.multipoints(xy))
    partes = shapely.get_parts(geom)
    coords = shapely.get_coordinates(partes).reshape(len(partes), 4, 2)[:, :3]
    indice = {(float(x), float(y)): i for i, (x, y) in enumerate(xy.tolist())}
    tri = np.empty((len(partes), 3), dtype=np.int64)
    for k, t in enumerate(coords.tolist()):
        for j, (x, y) in enumerate(t):
            i = indice.get((x, y))
            if i is None:
                i = int(np.argmin((xy[:, 0] - x) ** 2 + (xy[:, 1] - y) ** 2))
            tri[k, j] = i
    return tri


def crear_tin(
    puntos: Iterable[Sequence[float]],
    quiebres: Sequence[LineaQuiebre] = (),
    limite: Sequence[Sequence[float]] | None = None,
    islas: Sequence[Sequence[Sequence[float]]] = (),
    arista_maxima: float | None = None,
    area_minima: float = 1e-9,
    decimales_duplicados: int = 6,
    tolerancia_union: float = 1e-6,
) -> Tin:
    """Crea el TIN. `limite` e `islas` son polígonos [(x, y[, z]), …] (abiertos o cerrados)."""
    est = EstadisticasTin()
    avisos: list[str] = []
    base, indice_xy = _limpiar_puntos(puntos, decimales_duplicados, est)
    todos: list[list[float]] = [list(p) for p in base]

    def indice_de(x, y, z, origen):
        clave = (round(x, decimales_duplicados), round(y, decimales_duplicados))
        k = indice_xy.get(clave)
        if k is not None:
            if z is not None and abs(todos[k][2] - z) > tolerancia_union:
                avisos.append(f"{origen}: el vértice ({x:.3f}, {y:.3f}) coincide con un punto de elevación {todos[k][2]:.3f} y trae {z:.3f}; se usa la del punto.")
            return k
        indice_xy[clave] = len(todos)
        todos.append([x, y, z])
        return len(todos) - 1

    # líneas de quiebre: nodos (vértices y cruces); cada vértice necesita elevación
    tramos = _nodos_quiebre(quiebres, est, avisos)
    secuencias = []
    for nombre, vertices in tramos:
        etiqueta = f"Línea de quiebre {nombre}".strip()
        sec = []
        for x, y, z in vertices:
            i = indice_de(x, y, z, etiqueta)
            if todos[i][2] is None:
                raise ValueError(f"{etiqueta}: el vértice ({x:.3f}, {y:.3f}) no tiene elevación ni coincide con un punto. Usa una polilínea 3D o une puntos existentes.")
            if not sec or sec[-1] != i:
                sec.append(i)
        secuencias.append(sec)
    est.vertices_quiebre = len(todos) - len(base)
    if len(todos) < 3:
        raise ValueError("Se necesitan al menos tres puntos con elevación y distintos en planta.")
    arr = np.array([[p[0], p[1]] for p in todos], dtype=float)
    origen = arr.mean(axis=0)
    local = (arr - origen).tolist()
    escala = float(np.ptp(arr, axis=0).max()) or 1.0
    tri_ids = _delaunay_geos(arr)
    if len(tri_ids) == 0:
        raise ValueError("Los puntos están alineados: no forman triángulos.")
    malla = _Malla(local, tri_ids.tolist(), escala)
    for sec in secuencias:
        for a, b in zip(sec, sec[1:]):
            malla.forzar(a, b)
    # límite e islas
    poligonos = []
    if limite is not None:
        poligonos.append(("Límite", limite, False))
    for k, isla in enumerate(islas, start=1):
        poligonos.append((f"Isla {k}", isla, True))
    anillos = []
    if poligonos:
        casco = _casco(malla, todos)
    for nombre, anillo, es_isla in poligonos:
        sec = _colgar_anillo(malla, todos, indice_de, anillo, nombre, origen, casco, avisos, est)
        anillos.append((sec, es_isla))
    # triángulos finales
    triangulos = np.array(list(malla.tri.values()), dtype=np.int64)
    puntos_abs = np.array([[p[0], p[1], p[2]] for p in todos], dtype=float)
    if anillos:
        triangulos = _recortar(triangulos, puntos_abs, anillos, est)
    p = puntos_abs[triangulos]
    if area_minima is not None:
        malos = np.abs(_cruz2(p)) / 2.0 <= area_minima
        est.filtrados_area = int(malos.sum())
        triangulos, p = triangulos[~malos], p[~malos]
    if arista_maxima is not None:
        largo = np.max(np.stack([np.hypot(*(p[:, 0, :2] - p[:, 1, :2]).T), np.hypot(*(p[:, 1, :2] - p[:, 2, :2]).T), np.hypot(*(p[:, 2, :2] - p[:, 0, :2]).T)]), axis=0)
        malos = largo > arista_maxima
        est.filtrados_arista = int(malos.sum())
        triangulos = triangulos[~malos]
    if len(triangulos) == 0:
        raise ValueError("Todos los triángulos quedaron fuera por el límite o los filtros.")
    return Tin(puntos_abs, triangulos, set(malla.forzadas), est, avisos)


def _nodos_quiebre(quiebres: Sequence[LineaQuiebre], est: EstadisticasTin, avisos: list[str]):
    """Corta las líneas de quiebre entre sí. Devuelve [(nombre, [(x, y, z|None), …])]."""
    lineas = []
    for q in quiebres:
        v = [(float(p[0]), float(p[1]), None if len(p) < 3 or p[2] is None else float(p[2])) for p in q.vertices]
        v = [p for k, p in enumerate(v) if k == 0 or (p[0], p[1]) != (v[k - 1][0], v[k - 1][1])]
        if len(v) >= 2:
            lineas.append([q.nombre, v])
    if len(lineas) < 1:
        return []
    # cruces: lista de (línea, tramo, t, x, y, z)
    insertos: dict[tuple[int, int], list[tuple[float, float, float, float | None]]] = defaultdict(list)
    segmentos = []
    for li, (_, v) in enumerate(lineas):
        for si in range(len(v) - 1):
            segmentos.append((li, si, v[si], v[si + 1]))
    if len(segmentos) > 1:
        import shapely

        geoms = shapely.linestrings([[(a[0], a[1]), (b[0], b[1])] for _, _, a, b in segmentos])
        arbol = shapely.STRtree(geoms)
        pares = arbol.query(geoms, predicate="intersects")
        for i, j in zip(*pares):
            if i >= j:
                continue
            li, si, a, b = segmentos[i]
            lj, sj, c, d = segmentos[j]
            r = _cruce(a, b, c, d)
            if r is None:
                continue
            t, u = r
            extremos_i, extremos_j = t in (0.0, 1.0), u in (0.0, 1.0)
            if li == lj and abs(si - sj) == 1 and extremos_i and extremos_j:
                continue  # tramos consecutivos de la misma línea
            x = a[0] + t * (b[0] - a[0])
            y = a[1] + t * (b[1] - a[1])
            zi = None if a[2] is None or b[2] is None else a[2] + t * (b[2] - a[2])
            zj = None if c[2] is None or d[2] is None else c[2] + u * (d[2] - c[2])
            z = zi if zi is not None else zj
            if zi is not None and zj is not None and abs(zi - zj) > 1e-6:
                avisos.append(f"Las líneas de quiebre se cruzan en ({x:.3f}, {y:.3f}) con elevaciones {zi:.3f} y {zj:.3f}; se usa {zi:.3f}.")
            if not extremos_i:
                insertos[(li, si)].append((t, x, y, z))
            if not extremos_j:
                insertos[(lj, sj)].append((u, x, y, z))
            est.cruces_quiebre += 1
    salida = []
    for li, (nombre, v) in enumerate(lineas):
        nueva = [v[0]]
        for si in range(len(v) - 1):
            for t, x, y, z in sorted(insertos.get((li, si), [])):
                nueva.append((x, y, z))
            nueva.append(v[si + 1])
        salida.append((nombre, nueva))
    return salida


def _cruce(a, b, c, d):
    """Parámetros (t, u) del cruce de los segmentos a-b y c-d, o None (paralelos o sin cruce)."""
    rx, ry = b[0] - a[0], b[1] - a[1]
    sx, sy = d[0] - c[0], d[1] - c[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-15 * max(rx * rx + ry * ry, sx * sx + sy * sy, 1e-300):
        return None
    qx, qy = c[0] - a[0], c[1] - a[1]
    t = (qx * sy - qy * sx) / den
    u = (qx * ry - qy * rx) / den
    eps = 1e-12
    if -eps <= t <= 1 + eps and -eps <= u <= 1 + eps:
        return min(1.0, max(0.0, t)), min(1.0, max(0.0, u))
    return None


def _z_en(malla, todos, donde, p):
    tipo, dato = donde
    if tipo == "triangulo":
        verts = malla.tri[dato]
    else:
        a, b = dato
        verts = (a, b, malla.opuesto(next(iter(malla.arista[(min(a, b), max(a, b))])), a, b))
    tri = [(malla.xy[v][0], malla.xy[v][1], todos[v][2]) for v in verts]
    return _interpolar(tri, p[0], p[1])


def _casco(malla: _Malla, todos):
    """Polígono (Shapely) que cubre la malla actual, en coordenadas absolutas."""
    import shapely

    tri = np.array(list(malla.tri.values()))
    pts = np.array([[p[0], p[1]] for p in todos])
    return shapely.union_all(shapely.polygons(pts[tri]))


def _colgar_anillo(malla: _Malla, todos, indice_de, anillo, nombre, origen, casco, avisos, est):
    """Inserta el límite (o una isla) y lo fuerza; devuelve la secuencia de vértices del anillo.

    Con Z en todos sus vértices, el límite es dato: se inserta como una línea de quiebre.
    Sin Z se cuelga del terreno: sus vértices y sus cruces con las aristas actuales toman la Z
    interpolada y se insertan sin voltear aristas, así la superficie no cambia. Si el límite
    sale de la zona con puntos, se recorta a ella y se avisa.
    """
    import shapely

    v = [(float(p[0]), float(p[1]), None if len(p) < 3 or p[2] is None else float(p[2])) for p in anillo]
    if len(v) > 2 and (v[0][0], v[0][1]) == (v[-1][0], v[-1][1]):
        v.pop()
    if len(v) < 3:
        raise ValueError(f"{nombre}: necesita al menos tres vértices.")
    con_z = all(p[2] is not None for p in v)
    poligono = shapely.Polygon([(p[0], p[1]) for p in v])
    if not poligono.is_valid:
        raise ValueError(f"{nombre}: el polígono se cruza consigo mismo.")
    if not con_z and not casco.buffer(1e-7 * (casco.length or 1.0)).contains(poligono):
        recorte = poligono.intersection(casco)
        if recorte.is_empty:
            raise ValueError(f"{nombre}: queda fuera de la zona con puntos.")
        if recorte.geom_type != "Polygon":
            recorte = max(shapely.get_parts(recorte), key=lambda g: g.area if g.geom_type == "Polygon" else 0.0)
        avisos.append(f"{nombre}: sale de la zona con puntos; se recortó a ella.")
        coords = list(recorte.exterior.coords)[:-1]
        v = [(x, y, None) for x, y in coords]
    if con_z:
        sec = []
        for x, y, z in v:
            i = indice_de(x, y, z, nombre)
            if i == len(malla.xy):
                malla.xy.append([x - origen[0], y - origen[1]])
                donde = malla.localizar(malla.xy[i])
                if donde[0] in ("fuera", "vertice"):
                    raise ValueError(f"{nombre}: el vértice ({x:.3f}, {y:.3f}) queda fuera de la zona con puntos.")
                malla.insertar(i, donde)
                est.vertices_limite += 1
            if not sec or sec[-1] != i:
                sec.append(i)
        if sec[0] == sec[-1]:
            sec.pop()
        for a, b in zip(sec, sec[1:] + sec[:1]):
            malla.forzar(a, b)
        return sec
    # 2D: todos los nodos se calculan sobre la superficie original antes de insertar nada
    nodos: list[tuple[float, float]] = []
    aristas = list(malla.arista.keys())
    pts = np.array([[p[0], p[1]] for p in todos])
    lineas = shapely.linestrings(pts[np.array(aristas)])
    arbol = shapely.STRtree(lineas)
    n = len(v)
    for k in range(n):
        a, b = v[k], v[(k + 1) % n]
        nodos.append((a[0], a[1]))
        seg = shapely.LineString([(a[0], a[1]), (b[0], b[1])])
        cruces = []
        for j in arbol.query(seg, predicate="intersects"):
            i1, i2 = aristas[j]
            r = _cruce((a[0] - origen[0], a[1] - origen[1]), (b[0] - origen[0], b[1] - origen[1]), malla.xy[i1], malla.xy[i2])
            if r is None:
                continue
            t, _ = r
            if 1e-10 < t < 1 - 1e-10:
                cruces.append((t, a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        nodos += [(x, y) for _, x, y in sorted(cruces)]
    sec = []
    for x, y in nodos:
        local = [x - origen[0], y - origen[1]]
        donde = malla.localizar(local)
        if donde[0] == "fuera":
            raise ValueError(f"{nombre}: el vértice ({x:.3f}, {y:.3f}) queda fuera de la zona con puntos.")
        if donde[0] == "vertice":
            i = donde[1]
        else:
            i = indice_de(x, y, None, nombre)
            if i == len(malla.xy):
                malla.xy.append(local)
                todos[i][2] = _z_en(malla, todos, donde, local)
                malla.insertar(i, donde, legalizar=False)
                est.vertices_limite += 1
        if not sec or sec[-1] != i:
            sec.append(i)
    if len(sec) > 1 and sec[0] == sec[-1]:
        sec.pop()
    for a, b in zip(sec, sec[1:] + sec[:1]):
        malla.forzar(a, b, legalizar=False)
    return sec


def _recortar(triangulos, puntos, anillos, est):
    import shapely

    ext = [s for s, isla in anillos if not isla]
    islas = [s for s, isla in anillos if isla]
    c = puntos[triangulos][:, :, :2].mean(axis=1)
    dentro = np.ones(len(triangulos), dtype=bool)
    if ext:
        zona = shapely.union_all([shapely.Polygon(puntos[s][:, :2]) for s in ext])
        dentro &= shapely.contains_xy(zona, c[:, 0], c[:, 1])
    for s in islas:
        dentro &= ~shapely.contains_xy(shapely.Polygon(puntos[s][:, :2]), c[:, 0], c[:, 1])
    est.triangulos_fuera = int((~dentro).sum())
    return triangulos[dentro]
