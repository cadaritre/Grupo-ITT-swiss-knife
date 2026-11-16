"""Curvas de nivel de una triangulación: cálculo, suavizado sin cruces, etiquetas y dibujo.

La entrada es un `Tin` o cualquier conjunto de caras triangulares leídas del dibujo
(3DFACE), porque el TIN del dibujo es la fuente de verdad: después de invertir o borrar
triángulos se regeneran las curvas desde las caras que hay.

Las curvas se encadenan por la identidad de las aristas del TIN, no por coordenadas
aproximadas. Un vértice cuya elevación es exactamente la de la curva se considera un poco
por encima (convención topológica), así una curva nunca pasa «por dentro» de un vértice ni
se duplica; la curva conserva su elevación nominal.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from .entidades import Capa, Dibujo, EstiloTexto, Polilinea, Polilinea3D, Texto
from .escala import Escala

__all__ = [
    "Curva",
    "topologia_de_caras",
    "niveles",
    "curvas_de_nivel",
    "suavizar",
    "Etiqueta",
    "etiquetar",
    "dibujar_curvas",
    "CAPAS_CURVAS",
]

CAPAS_CURVAS = {
    "maestras": Capa("CURVAS_MAESTRAS", 30, grosor_mm=0.35, descripcion="Curvas de nivel maestras"),
    "intermedias": Capa("CURVAS_INTERMEDIAS", 8, grosor_mm=0.18, descripcion="Curvas de nivel intermedias"),
    "etiquetas": Capa("CURVAS_ETIQUETAS", 30, descripcion="Elevación de las curvas de nivel"),
}


@dataclass
class Curva:
    elevacion: float
    vertices: np.ndarray  # (k, 2)
    cerrada: bool
    maestra: bool
    triangulos: list[int] = field(default_factory=list, repr=False)

    @property
    def longitud(self) -> float:
        v = self.vertices
        if self.cerrada:
            v = np.vstack([v, v[:1]])
        return float(np.hypot(*np.diff(v, axis=0).T).sum())


def topologia_de_caras(caras, decimales: int = 6) -> tuple[np.ndarray, np.ndarray]:
    """Caras (m, 3, 3) → (puntos únicos (n, 3), triángulos (m, 3)).

    Dos caras comparten un vértice cuando sus coordenadas X, Y, Z coinciden a `decimales`.
    Una cara de 4 vértices con el cuarto repetido (3DFACE triangular) se toma como triángulo;
    una cara cuadrilátera real se parte por su diagonal 1-3.
    """
    tri = []
    for cara in caras:
        c = [tuple(map(float, p)) for p in cara]
        if len(c) == 4 and c[3] != c[2]:
            tri.append((c[0], c[1], c[2]))
            tri.append((c[0], c[2], c[3]))
        else:
            tri.append((c[0], c[1], c[2]))
    indice: dict[tuple[float, float, float], int] = {}
    puntos = []
    salida = np.empty((len(tri), 3), dtype=np.int64)
    for k, t in enumerate(tri):
        for j, p in enumerate(t):
            clave = (round(p[0], decimales), round(p[1], decimales), round(p[2], decimales))
            i = indice.get(clave)
            if i is None:
                i = len(puntos)
                indice[clave] = i
                puntos.append(p)
            salida[k, j] = i
    puntos = np.array(puntos, dtype=float)
    degenerados = np.array([len(set(t)) < 3 for t in salida.tolist()], dtype=bool)
    return puntos, salida[~degenerados]


def niveles(zmin: float, zmax: float, intervalo: float, base: float = 0.0, maximo: int = 20_000) -> list[float]:
    """Elevaciones base + k·intervalo dentro de [zmin, zmax]."""
    if not intervalo > 0 or not math.isfinite(intervalo):
        raise ValueError("El intervalo debe ser positivo.")
    k0 = math.ceil((zmin - base) / intervalo - 1e-9)
    k1 = math.floor((zmax - base) / intervalo + 1e-9)
    if k1 - k0 + 1 > maximo:
        raise ValueError(f"Serían {k1 - k0 + 1:,} curvas; aumenta el intervalo.")
    return [round(base + k * intervalo, 10) for k in range(k0, k1 + 1)]


def _es_maestra(z: float, intervalo: float, cada: int, base: float) -> bool:
    if cada <= 1:
        return True
    k = round((z - base) / intervalo)
    return k % cada == 0


def curvas_de_nivel(
    tin_o_caras,
    intervalo: float,
    maestra_cada: int = 5,
    base: float = 0.0,
    z_min: float | None = None,
    z_max: float | None = None,
) -> list[Curva]:
    """Curvas de nivel de un `Tin` (o de caras (m, 3, 3)) cada `intervalo`.

    Es maestra la curva cuya elevación es múltiplo de `intervalo · maestra_cada` (desde `base`).
    """
    if hasattr(tin_o_caras, "triangulos"):
        puntos, tri = tin_o_caras.puntos, tin_o_caras.triangulos
    else:
        puntos, tri = topologia_de_caras(tin_o_caras)
    if len(tri) == 0:
        return []
    z = puntos[:, 2]
    lo = z.min() if z_min is None else max(z_min, z.min())
    hi = z.max() if z_max is None else min(z_max, z.max())
    origen = puntos[:, :2].mean(axis=0)
    xy = puntos[:, :2] - origen
    zt = z[tri]
    tmin, tmax = zt.min(axis=1), zt.max(axis=1)
    salida: list[Curva] = []
    for nivel in niveles(lo, hi, intervalo, base):
        candidatos = np.nonzero((tmin <= nivel) & (tmax >= nivel))[0]
        if len(candidatos) == 0:
            continue
        # vértices en el nivel se consideran por encima
        arriba = z >= nivel
        segmentos: list[tuple[tuple[int, int], tuple[int, int], int]] = []
        for k in candidatos.tolist():
            a, b, c = tri[k].tolist()
            cruces = []
            for p, q in ((a, b), (b, c), (c, a)):
                if arriba[p] != arriba[q]:
                    cruces.append((min(p, q), max(p, q)))
            if len(cruces) == 2:
                segmentos.append((cruces[0], cruces[1], k))
        if not segmentos:
            continue
        for cadena, cerrada, tris in _encadenar(segmentos):
            pts = np.array([_punto(xy, z, e, nivel) for e in cadena]) + origen
            if len(pts) < 2 or np.ptp(pts, axis=0).max() <= 1e-9 * (1.0 + np.abs(origen).max()):
                continue  # curva que se reduce a un punto (una cima exactamente en el nivel)
            salida.append(Curva(nivel, pts, cerrada, _es_maestra(nivel, intervalo, maestra_cada, base), tris))
    return salida


def _punto(xy, z, arista, nivel):
    i, j = arista  # i < j: siempre se interpola desde el mismo extremo, bit a bit igual en ambos triángulos
    t = (nivel - z[i]) / (z[j] - z[i])
    return xy[i] + t * (xy[j] - xy[i])


def _encadenar(segmentos):
    por_arista: dict[tuple[int, int], list[int]] = defaultdict(list)
    for s, (e1, e2, _) in enumerate(segmentos):
        por_arista[e1].append(s)
        por_arista[e2].append(s)
    usado = [False] * len(segmentos)

    def recorrer(inicio_arista, s):
        cadena, tris = [inicio_arista], []
        actual = inicio_arista
        while s is not None and not usado[s]:
            usado[s] = True
            e1, e2, k = segmentos[s]
            tris.append(k)
            siguiente = e2 if e1 == actual else e1
            cadena.append(siguiente)
            actual = siguiente
            s = next((t for t in por_arista[actual] if not usado[t]), None)
        return cadena, tris

    salida = []
    # primero las abiertas: arrancan en una arista de borde (un solo segmento)
    for arista, lista in por_arista.items():
        if len(lista) == 1 and not usado[lista[0]]:
            cadena, tris = recorrer(arista, lista[0])
            salida.append((cadena, False, tris))
    for s in range(len(segmentos)):
        if not usado[s]:
            cadena, tris = recorrer(segmentos[s][0], s)
            cerrada = cadena[0] == cadena[-1]
            if cerrada:
                cadena = cadena[:-1]
            salida.append((cadena, cerrada, tris))
    return salida


# --- suavizado ------------------------------------------------------------------------------

def _chaikin(v: np.ndarray, cerrada: bool, iteraciones: int) -> np.ndarray:
    for _ in range(iteraciones):
        if cerrada:
            a, b = v, np.roll(v, -1, axis=0)
            q, r = 0.75 * a + 0.25 * b, 0.25 * a + 0.75 * b
            v = np.empty((2 * len(a), 2))
            v[0::2], v[1::2] = q, r
        else:
            if len(v) < 3:
                return v
            a, b = v[:-1], v[1:]
            q, r = 0.75 * a + 0.25 * b, 0.25 * a + 0.75 * b
            medio = np.empty((2 * len(a), 2))
            medio[0::2], medio[1::2] = q, r
            v = np.vstack([v[:1], medio[1:-1], v[-1:]])
    return v


def suavizar(curvas: Sequence[Curva], iteraciones: int = 2) -> tuple[list[Curva], int]:
    """Suaviza por recorte de esquinas (Chaikin) y garantiza que ninguna curva toque a otra.

    Si una curva suavizada cruza o toca a otra (suavizada o no), las dos se dejan sin suavizar.
    Los extremos de las curvas abiertas no se mueven (siguen en el borde del TIN).
    Devuelve (curvas, número de curvas que se quedaron sin suavizar).
    """
    import shapely

    if iteraciones <= 0 or not curvas:
        return list(curvas), 0
    lisas = [Curva(c.elevacion, _chaikin(c.vertices, c.cerrada, iteraciones), c.cerrada, c.maestra, c.triangulos) for c in curvas]

    def geom(c):
        v = c.vertices
        if c.cerrada:
            v = np.vstack([v, v[:1]])
        return shapely.LineString(v) if len(v) >= 2 else shapely.Point(v[0])

    finales = list(lisas)
    revertidas: set[int] = set()
    for _ in range(4):
        geoms = [geom(c) for c in finales]
        arbol = shapely.STRtree(geoms)
        pares = arbol.query(geoms, predicate="intersects")
        conflicto = {int(i) for i, j in zip(*pares) if i != j} | {int(j) for i, j in zip(*pares) if i != j}
        conflicto -= revertidas
        if not conflicto:
            break
        for i in conflicto:
            finales[i] = curvas[i]
            revertidas.add(i)
    return finales, len(revertidas)


# --- etiquetas ------------------------------------------------------------------------------

@dataclass
class Etiqueta:
    texto: str
    x: float
    y: float
    rotacion: float  # grados, siempre legible (−90, 90]
    curva: int


def _formato_elev(z: float, decimales: int) -> str:
    texto = f"{z:.{decimales}f}"
    return "0" if decimales == 0 and texto == "-0" else texto


def etiquetar(
    curvas: Sequence[Curva],
    separacion: float,
    altura: float,
    decimales: int = 0,
    solo_maestras: bool = True,
    longitud_minima: float | None = None,
    hueco: bool = True,
) -> tuple[list[Etiqueta], list[Curva]]:
    """Etiquetas a lo largo de las curvas cada `separacion` (unidades de dibujo).

    Una curva más corta que `longitud_minima` (por omisión 4 alturas) no se etiqueta. Con
    `hueco` la curva se interrumpe bajo el texto: devuelve las piezas que se dibujan.
    """
    if separacion <= 0 or altura <= 0:
        raise ValueError("Separación y altura deben ser positivas.")
    minimo = 4 * altura if longitud_minima is None else longitud_minima
    etiquetas: list[Etiqueta] = []
    piezas: list[Curva] = []
    for ic, c in enumerate(curvas):
        v = np.vstack([c.vertices, c.vertices[:1]]) if c.cerrada else c.vertices
        d = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(v, axis=0).T))])
        total = d[-1]
        if (solo_maestras and not c.maestra) or total < minimo or len(v) < 2:
            piezas.append(c)
            continue
        texto = _formato_elev(c.elevacion, decimales)
        ancho = altura * (0.8 * len(texto) + 1.0)
        n = max(1, int(total // separacion))
        posiciones = [total * (k + 0.5) / n for k in range(n)] if not c.cerrada else [total * k / n for k in range(n)]
        cortes = []
        for s in posiciones:
            s = min(max(s, ancho / 2), total - ancho / 2) if not c.cerrada else s
            x, y, ang = _en(v, d, s)
            etiquetas.append(Etiqueta(texto, x, y, ang, ic))
            cortes.append((s - ancho / 2, s + ancho / 2))
        if hueco:
            piezas += _partir(c, v, d, cortes)
        else:
            piezas.append(c)
    return etiquetas, piezas


def _en(v, d, s):
    i = int(np.searchsorted(d, s, side="right") - 1)
    i = min(max(i, 0), len(v) - 2)
    seg = d[i + 1] - d[i]
    t = 0.0 if seg == 0 else (s - d[i]) / seg
    p = v[i] + t * (v[i + 1] - v[i])
    ang = math.degrees(math.atan2(v[i + 1][1] - v[i][1], v[i + 1][0] - v[i][0]))
    if ang > 90:
        ang -= 180
    elif ang <= -90:
        ang += 180
    return float(p[0]), float(p[1]), ang


def _subcurva(v, d, s0, s1):
    total = d[-1]
    s0, s1 = max(0.0, s0), min(total, s1)
    if s1 - s0 <= 1e-9:
        return None
    dentro = [v[k] for k in range(len(v)) if s0 < d[k] < s1]
    a = _en(v, d, s0)[:2]
    b = _en(v, d, s1)[:2]
    return np.array([a, *dentro, b])


def _partir(c: Curva, v, d, cortes):
    total = d[-1]
    cortes = sorted(cortes)
    piezas = []
    if c.cerrada:
        # la curva cerrada se recorre dos veces para que el tramo que cruza el inicio salga entero
        tramos = [(cortes[k][1], cortes[k + 1][0]) for k in range(len(cortes) - 1)] + [(cortes[-1][1], cortes[0][0] + total)]
        doble_v = np.vstack([v, v[1:] + 0])
        doble_d = np.concatenate([d, d[1:] + total])
        for s0, s1 in tramos:
            pts = _subcurva(doble_v, doble_d, s0, s1)
            if pts is not None:
                piezas.append(Curva(c.elevacion, pts, False, c.maestra, c.triangulos))
        return piezas
    tramos = [(0.0, cortes[0][0])] + [(cortes[k][1], cortes[k + 1][0]) for k in range(len(cortes) - 1)] + [(cortes[-1][1], total)]
    for s0, s1 in tramos:
        pts = _subcurva(v, d, s0, s1)
        if pts is not None:
            piezas.append(Curva(c.elevacion, pts, False, c.maestra, c.triangulos))
    return piezas


# --- dibujo ---------------------------------------------------------------------------------

def dibujar_curvas(
    curvas: Sequence[Curva],
    escala: Escala | None = None,
    separacion_mm: float = 80.0,
    decimales: int = 0,
    solo_maestras: bool = True,
    hueco: bool = True,
    tres_d: bool = False,
    estilo: str = "CIVIL",
    sufijo: str = "",
) -> Dibujo:
    """Dibujo de las curvas: polilíneas con su elevación en las capas de maestras e intermedias y etiquetas.

    `separacion_mm` es la distancia entre etiquetas en el papel. `sufijo` separa las capas de otra superficie
    (las curvas de proyecto van en CURVAS_MAESTRAS_PROYECTO, etc.).
    """
    esc = escala or Escala()
    d = Dibujo()
    capas = {k: Capa(c.nombre + sufijo, c.color if not sufijo else 6 if k != "intermedias" else 9, c.tipo_linea, c.grosor_mm, c.descripcion + (" (proyecto)" if sufijo else ""))
             for k, c in CAPAS_CURVAS.items()}
    for c in capas.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    h = esc.altura("curva_etiqueta")
    separacion = separacion_mm * float(esc.denominador) / 1000.0 * esc.unidades_por_metro
    etiquetas, piezas = etiquetar(curvas, separacion, h, decimales, solo_maestras, hueco=hueco)
    for c in piezas:
        capa = capas["maestras" if c.maestra else "intermedias"].nombre
        datos = {"TVCIVIL": [(1000, "curva"), (1040, float(c.elevacion))]}
        if tres_d:
            d.agregar(Polilinea3D(capa, vertices=[(float(x), float(y), float(c.elevacion)) for x, y in c.vertices], cerrada=c.cerrada, datos=datos))
        else:
            d.agregar(Polilinea(capa, vertices=[(float(x), float(y)) for x, y in c.vertices], cerrada=c.cerrada, elevacion=float(c.elevacion), datos=datos))
    for e in etiquetas:
        d.agregar(Texto(capas["etiquetas"].nombre, valor=e.texto, posicion=(e.x, e.y), altura=h, rotacion=e.rotacion, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
    return d
