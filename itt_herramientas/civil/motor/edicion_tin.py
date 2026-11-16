"""Edición del TIN que vive en el dibujo como caras 3D (3DFACE).

El TIN del dibujo es la fuente de verdad: la página lee las caras, el motor calcula la
edición y la página reemplaza las caras en un solo paso de deshacer. Después se regeneran las
curvas desde las caras (`tvcivil.curvas.curvas_de_nivel` acepta caras).

- `invertir`: cambia la diagonal común de dos triángulos vecinos.
- `quitar_fuera` / `quitar_dentro`: borra triángulos fuera o dentro de un polígono.
- `quitar_largos`: borra triángulos con alguna arista en planta mayor que un largo.
- `agregar`: añade un triángulo que no se encime con los existentes (para cerrar un hueco).
- `insertar_puntos`: mete puntos en la triangulación que ya está en el dibujo: cada punto parte el triángulo que lo contiene (o los dos
  del lado sobre el que cae) y después los vecinos se ajustan con inversiones de Delaunay, solo alrededor de los puntos nuevos.
- `revisar`: encuentra lo que rompe un TIN editado a mano (caras sin superficie, repetidas o encimadas, lados con más de dos
  triángulos) y los triángulos muy alargados.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

__all__ = [
    "Cara",
    "ErrorEdicion",
    "normalizar_cara",
    "invertir",
    "quitar_fuera",
    "quitar_dentro",
    "quitar_largos",
    "agregar",
    "Insercion",
    "insertar_puntos",
    "Revision",
    "revisar",
    "caras_de_consulta",
    "caras_de_tin",
]

Punto3 = tuple[float, float, float]
Cara = tuple[Punto3, Punto3, Punto3]


class ErrorEdicion(ValueError):
    """Edición rechazada; el mensaje explica por qué, en palabras de quien dibuja."""


def _p(v) -> Punto3:
    if isinstance(v, dict):
        return (float(v["x"]), float(v["y"]), float(v.get("z", 0.0)))
    return (float(v[0]), float(v[1]), float(v[2]) if len(v) > 2 else 0.0)


def _igual(a: Punto3, b: Punto3, tol: float) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _orient(a, b, c) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _eps(*pts) -> float:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    tam = max(max(xs) - min(xs), max(ys) - min(ys), 1e-12)
    return 1e-12 * tam * tam


def normalizar_cara(vertices: Sequence, tol: float = 1e-9) -> Cara:
    """3 o 4 vértices (el cuarto repetido, como guarda CAD un triángulo) → triángulo antihorario."""
    v = [_p(x) for x in vertices]
    if len(v) == 4:
        if _igual(v[3], v[2], tol) or _igual(v[3], v[0], tol):
            v = v[:3]
        else:
            raise ErrorEdicion("La cara tiene cuatro vértices distintos: no es un triángulo del TIN.")
    if len(v) != 3:
        raise ErrorEdicion("Una cara necesita tres vértices.")
    if any(_igual(v[i], v[j], tol) for i, j in ((0, 1), (1, 2), (0, 2))) or abs(_orient(*v)) <= _eps(*v):
        raise ErrorEdicion("La cara no tiene superficie en planta.")
    if _orient(*v) < 0:
        v = [v[0], v[2], v[1]]
    return (v[0], v[1], v[2])


def invertir(cara_a: Sequence, cara_b: Sequence, tol: float = 1e-6) -> tuple[Cara, Cara]:
    """Invierte la diagonal común de dos triángulos vecinos.

    Valida que compartan exactamente una arista (dos vértices, comparados en planta con `tol`)
    y que el cuadrilátero que forman sea estrictamente convexo; si no, la inversión dejaría
    triángulos encimados o sin superficie y se rechaza con el motivo. Las elevaciones de los
    cuatro vértices se conservan tal cual.
    """
    a = normalizar_cara(cara_a)
    b = normalizar_cara(cara_b)
    comunes = [(i, j) for i in range(3) for j in range(3) if _igual(a[i], b[j], tol)]
    if len(comunes) == 3:
        raise ErrorEdicion("Elegiste el mismo triángulo dos veces.")
    if len(comunes) < 2:
        raise ErrorEdicion("Los triángulos no son vecinos: no comparten una arista.")
    if len({i for i, _ in comunes}) != 2 or len({j for _, j in comunes}) != 2:
        raise ErrorEdicion("Los triángulos comparten vértices repetidos; revisa el TIN.")
    ia = {i for i, _ in comunes}
    ib = {j for _, j in comunes}
    c = a[({0, 1, 2} - ia).pop()]
    d = b[({0, 1, 2} - ib).pop()]
    # arista p→q con c a la izquierda (orden antihorario de la cara a)
    k = ({0, 1, 2} - ia).pop()
    p, q = a[(k + 1) % 3], a[(k + 2) % 3]
    eps = _eps(p, q, c, d)
    if _orient(p, q, d) >= -eps:
        raise ErrorEdicion("Los triángulos están encimados: el segundo no queda del otro lado de la arista común.")
    lado_p, lado_q = _orient(c, d, p), _orient(c, d, q)
    if abs(lado_p) <= eps or abs(lado_q) <= eps:
        raise ErrorEdicion("No se puede invertir: un vértice queda alineado con la nueva diagonal y saldría un triángulo sin superficie.")
    if not (lado_p * lado_q < 0):
        raise ErrorEdicion("No se puede invertir: los dos triángulos forman un cuadrilátero cóncavo y la nueva diagonal saldría de él.")
    return normalizar_cara((c, p, d)), normalizar_cara((c, d, q))


def _poligono(contorno):
    import shapely

    pts = [(float(x[0]), float(x[1])) for x in contorno]
    pol = shapely.Polygon(pts)
    if not pol.is_valid:
        raise ErrorEdicion("El polígono se cruza consigo mismo.")
    return pol


def _centroides(caras: Sequence[Cara]):
    return [((c[0][0] + c[1][0] + c[2][0]) / 3.0, (c[0][1] + c[1][1] + c[2][1]) / 3.0) for c in caras]


def quitar_fuera(caras: Iterable[Sequence], contorno: Sequence) -> tuple[list[Cara], list[int]]:
    """Separa los triángulos cuyo centroide queda fuera del polígono. Devuelve (quedan, índices quitados)."""
    import shapely

    lista = [normalizar_cara(c) for c in caras]
    pol = _poligono(contorno)
    cen = _centroides(lista)
    dentro = shapely.contains_xy(pol, [x for x, _ in cen], [y for _, y in cen])
    return [c for c, ok in zip(lista, dentro) if ok], [i for i, ok in enumerate(dentro) if not ok]


def quitar_dentro(caras: Iterable[Sequence], contorno: Sequence) -> tuple[list[Cara], list[int]]:
    import shapely

    lista = [normalizar_cara(c) for c in caras]
    pol = _poligono(contorno)
    cen = _centroides(lista)
    dentro = shapely.contains_xy(pol, [x for x, _ in cen], [y for _, y in cen])
    return [c for c, ok in zip(lista, dentro) if not ok], [i for i, ok in enumerate(dentro) if ok]


def quitar_largos(caras: Iterable[Sequence], largo_maximo: float) -> tuple[list[Cara], list[int]]:
    if not largo_maximo > 0:
        raise ErrorEdicion("El largo máximo debe ser positivo.")
    lista = [normalizar_cara(c) for c in caras]
    quedan, quitados = [], []
    for i, c in enumerate(lista):
        largo = max(math.hypot(c[j][0] - c[(j + 1) % 3][0], c[j][1] - c[(j + 1) % 3][1]) for j in range(3))
        if largo > largo_maximo:
            quitados.append(i)
        else:
            quedan.append(c)
    return quedan, quitados


def agregar(caras: Sequence[Sequence], nueva: Sequence, tol: float = 1e-6) -> Cara:
    """Valida un triángulo nuevo: con superficie y sin encimarse con ninguno de los existentes."""
    import shapely

    t = normalizar_cara(nueva)
    pol = shapely.Polygon([(p[0], p[1]) for p in t])
    for c in caras:
        cc = normalizar_cara(c)
        otro = shapely.Polygon([(p[0], p[1]) for p in cc])
        if pol.intersection(otro).area > tol * max(pol.area, 1.0):
            raise ErrorEdicion("El triángulo nuevo se encima con uno existente.")
    return t


@dataclass
class Insercion:
    quitar: list[int]  # índices de las caras de entrada que se reemplazan
    nuevas: list[Cara]  # las caras que las reemplazan
    fuera: list[int]  # índices de los puntos que no caen dentro de la triangulación
    repetidos: list[int]  # índices de los puntos que ya son vértices


def _en_circulo(a, b, c, p) -> bool:
    """p dentro del círculo de a, b, c (antihorario)."""
    m = [[a[0] - p[0], a[1] - p[1]], [b[0] - p[0], b[1] - p[1]], [c[0] - p[0], c[1] - p[1]]]
    det = ((m[0][0] ** 2 + m[0][1] ** 2) * (m[1][0] * m[2][1] - m[2][0] * m[1][1])
           - (m[1][0] ** 2 + m[1][1] ** 2) * (m[0][0] * m[2][1] - m[2][0] * m[0][1])
           + (m[2][0] ** 2 + m[2][1] ** 2) * (m[0][0] * m[1][1] - m[1][0] * m[0][1]))
    return det > _eps(a, b, c) * 1e-3


def insertar_puntos(caras: Sequence[Sequence], puntos: Sequence[Sequence], legalizar: bool = True, tol: float = 1e-6) -> Insercion:
    """Inserta puntos (x, y, z) en un TIN de caras. Los puntos fuera del TIN y los que ya son vértices se informan y no se usan.

    Con `legalizar`, tras cada inserción se invierten las aristas vecinas que no cumplen Delaunay (Lawson), sin tocar nada lejos del punto.
    Las elevaciones de los vértices no cambian; la del punto nuevo es la suya.
    """
    tris: dict[int, Cara] = {i: normalizar_cara(c) for i, c in enumerate(caras)}
    originales = set(tris)
    siguiente = len(caras)
    fuera: list[int] = []
    repetidos: list[int] = []
    clave = lambda p: (round(p[0] / tol), round(p[1] / tol))
    vertices = {clave(p) for t in tris.values() for p in t}

    def lado_de(t: Cara, a, b):
        ka, kb = clave(a), clave(b)
        return any({clave(t[i]), clave(t[(i + 1) % 3])} == {ka, kb} for i in range(3))

    def vecino(tid: int, a, b):
        for j, t in tris.items():
            if j != tid and lado_de(t, a, b):
                return j
        return None

    def legal(tid: int, p, pendientes: list):
        pendientes.append(tid)
        while pendientes:
            t_id = pendientes.pop()
            if t_id not in tris:
                continue
            t = tris[t_id]
            k = next((i for i in range(3) if clave(t[i]) == clave(p)), None)
            if k is None:
                continue
            a, b = t[(k + 1) % 3], t[(k + 2) % 3]   # arista opuesta al punto nuevo
            j = vecino(t_id, a, b)
            if j is None:
                continue
            otro = next(q for q in tris[j] if clave(q) not in (clave(a), clave(b)))
            if _en_circulo(p, a, b, otro) and _orient(p, a, otro) * _orient(p, b, otro) < 0:
                del tris[t_id], tris[j]
                nonlocal siguiente
                n1, n2 = normalizar_cara([p, a, otro]), normalizar_cara([p, otro, b])
                tris[siguiente], tris[siguiente + 1] = n1, n2
                pendientes += [siguiente, siguiente + 1]
                siguiente += 2

    for ip, q in enumerate(puntos):
        p = (float(q[0]), float(q[1]), float(q[2]) if len(q) > 2 and q[2] is not None else 0.0)
        if clave(p) in vertices:
            repetidos.append(ip)
            continue
        dentro = sobre = None
        for tid, t in tris.items():
            o = [_orient(t[i], t[(i + 1) % 3], p) for i in range(3)]
            e = _eps(*t)
            if all(x > e for x in o):
                dentro = tid
                break
            if all(x > -e for x in o):
                lado = next(i for i in range(3) if abs(o[i]) <= e)
                sobre = (tid, lado)
                break
        if dentro is None and sobre is None:
            fuera.append(ip)
            continue
        nuevos: list[int] = []
        if dentro is not None:
            a, b, c = tris.pop(dentro)
            for tri in ((a, b, p), (b, c, p), (c, a, p)):
                tris[siguiente] = normalizar_cara(tri)
                nuevos.append(siguiente)
                siguiente += 1
        else:
            tid, i = sobre
            t = tris[tid]
            a, b, c = t[i], t[(i + 1) % 3], t[(i + 2) % 3]   # p está sobre a-b
            j = vecino(tid, a, b)
            del tris[tid]
            for tri in ((a, p, c), (p, b, c)):
                tris[siguiente] = normalizar_cara(tri)
                nuevos.append(siguiente)
                siguiente += 1
            if j is not None:
                d = next(q for q in tris[j] if clave(q) not in (clave(a), clave(b)))
                del tris[j]
                for tri in ((b, p, d), (p, a, d)):
                    tris[siguiente] = normalizar_cara(tri)
                    nuevos.append(siguiente)
                    siguiente += 1
        vertices.add(clave(p))
        if legalizar:
            for n in nuevos:
                legal(n, p, [])
    quitar = sorted(originales - set(tris))
    nuevas = [t for k, t in sorted(tris.items()) if k not in originales]
    return Insercion(quitar, nuevas, fuera, repetidos)


@dataclass
class Revision:
    """Índices de las caras con cada problema (una cara puede tener varios) y los lados con más de dos triángulos."""

    sin_superficie: list[int]
    repetidas: list[int]
    encimadas: list[int]
    alargadas: list[int]
    lados_de_mas: list[tuple[tuple[float, float], tuple[float, float]]]

    @property
    def caras(self) -> list[int]:
        return sorted(set(self.sin_superficie) | set(self.repetidas) | set(self.encimadas) | set(self.alargadas))

    @property
    def total(self) -> int:
        return len(self.caras) + len(self.lados_de_mas)


def revisar(caras: Sequence[Sequence], angulo_minimo: float = 2.0, tol: float = 1e-6) -> Revision:
    """Revisa un TIN de caras: lo que lo rompe y los triángulos con un ángulo menor que `angulo_minimo` grados.

    - sin superficie en planta (o con cuatro vértices distintos): no son triángulos del TIN;
    - repetidas: los mismos tres vértices en planta que otra cara (se marca cada repetición después de la primera);
    - encimadas: se traslapan en planta con otra cara más que `tol` de su área (las dos se marcan);
    - lados con más de dos triángulos: un TIN tiene uno o dos por lado.
    """
    import shapely

    validas: list[tuple[int, Cara]] = []
    sin: list[int] = []
    for i, c in enumerate(caras):
        try:
            validas.append((i, normalizar_cara(c)))
        except ErrorEdicion:
            sin.append(i)
    clave_v = lambda p: (round(p[0] / tol), round(p[1] / tol))
    vistas: dict[tuple, int] = {}
    repetidas: list[int] = []
    unicas: list[tuple[int, Cara]] = []
    for i, c in validas:
        k = tuple(sorted(clave_v(p) for p in c))
        if k in vistas:
            repetidas.append(i)
        else:
            vistas[k] = i
            unicas.append((i, c))
    # encimadas: traslape de interiores entre caras distintas (vecinas de lado o de vértice se tocan con área cero)
    pols = [shapely.Polygon([(p[0], p[1]) for p in c]) for _, c in unicas]
    arbol = shapely.STRtree(pols)
    pares_a, pares_b = arbol.query(pols, predicate="intersects")
    encimadas: set[int] = set()
    for a, b in zip(pares_a.tolist(), pares_b.tolist()):
        if a >= b:
            continue
        inter = pols[a].intersection(pols[b]).area
        if inter > tol * min(pols[a].area, pols[b].area) and inter > tol * tol:
            encimadas.update((unicas[a][0], unicas[b][0]))
    # lados con más de dos triángulos
    lados: dict[tuple, list] = {}
    for i, c in unicas:
        for j in range(3):
            a, b = c[j], c[(j + 1) % 3]
            lados.setdefault(tuple(sorted((clave_v(a), clave_v(b)))), [a, b, 0])[2] += 1
    de_mas = [((a[0], a[1]), (b[0], b[1])) for a, b, n in lados.values() if n > 2]
    # alargadas: el ángulo menor del triángulo en planta
    alargadas: list[int] = []
    limite = math.radians(angulo_minimo)
    for i, c in unicas:
        angs = []
        for j in range(3):
            o, p, q = c[j], c[(j + 1) % 3], c[(j + 2) % 3]
            u, v = (p[0] - o[0], p[1] - o[1]), (q[0] - o[0], q[1] - o[1])
            angs.append(abs(math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])))
        if min(angs) < limite:
            alargadas.append(i)
    return Revision(sin, repetidas, sorted(encimadas), alargadas, de_mas)


def caras_de_consulta(entidades: Iterable[dict]) -> list[tuple[str, Cara]]:
    """Caras de la respuesta de `query` del editor (type 3DFACE, detail full) → [(handle, cara)]."""
    salida = []
    for e in entidades:
        pr = e.get("properties", e)
        v = [pr["first_corner"], pr["second_corner"], pr["third_corner"], pr["fourth_corner"]]
        salida.append((e.get("handle", ""), normalizar_cara(v)))
    return salida


def caras_de_tin(tin) -> list[Cara]:
    return [tuple(tuple(map(float, tin.puntos[i])) for i in t) for t in tin.triangulos.tolist()]
