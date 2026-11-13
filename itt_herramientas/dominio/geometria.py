"""Geometría adaptativa del convertidor: curvas por error de cuerda, rectas que solo se subdividen si la reproyección las dobla,
y limpieza que nunca toca un vértice original.

Todo trabaja en metros (UTM) y devuelve puntos `(x, y, z, original)`:
`original` es verdadero para los vértices del dibujo fuente (esquinas levantadas) y falso para los que genera la aproximación
de una curva o la subdivisión por reproyección. La limpieza solo puede quitar los generados (los exactamente alineados) y los
duplicados exactos; un vértice original jamás se mueve ni se descarta.
"""
from __future__ import annotations

import math
from typing import Callable, Iterable, Sequence

Punto = tuple[float, float, float, bool]

#: Tolerancias predefinidas del convertidor (error máximo de cuerda, en metros).
TOLERANCIAS = {"fina": 0.001, "topografica": 0.01, "dibujo": 0.05, "ligera": 0.25}
TOLERANCIA_POR_OMISION = TOLERANCIAS["topografica"]
TOLERANCIA_MINIMA = 0.0005
#: Un arco nunca se reduce a menos de un segmento por cada 45° (aunque la tolerancia lo permitiría): debe seguir viéndose redondo.
PASO_MAXIMO_RAD = math.pi / 4
#: Solo se comprueba si la reproyección dobla un segmento recto cuando mide más que esto (por debajo la desviación es de micras).
LARGO_MINIMO_REPROYECCION = 250.0


def tolerancia_valida(valor: float | None) -> float:
    """Acota la tolerancia a [0.5 mm, 5 m]; `None` da la de fábrica (1 cm)."""
    if valor is None:
        return TOLERANCIA_POR_OMISION
    return min(5.0, max(TOLERANCIA_MINIMA, float(valor)))


def segmentos_de_arco(radio: float, barrido: float, tolerancia: float) -> int:
    """Segmentos iguales para que ninguna cuerda se separe del arco más que `tolerancia` (flecha ≤ tolerancia)."""
    barrido = abs(barrido)
    if radio <= 0 or barrido <= 0:
        return 1
    if tolerancia >= radio:
        paso = math.pi
    else:
        paso = 2.0 * math.acos(1.0 - tolerancia / radio)      # ángulo máximo de una cuerda con flecha = tolerancia
    paso = min(paso, PASO_MAXIMO_RAD)
    return max(1, math.ceil(barrido / paso - 1e-12))


def puntos_de_arco(cx: float, cy: float, radio: float, a0: float, barrido: float, z0: float, z1: float, tolerancia: float) -> list[Punto]:
    """Puntos INTERIORES de un arco (sin sus extremos), sobre el arco exacto, con Z lineal entre `z0` y `z1`."""
    n = segmentos_de_arco(radio, barrido, tolerancia)
    salida = []
    for i in range(1, n):
        a = a0 + barrido * i / n
        salida.append((cx + radio * math.cos(a), cy + radio * math.sin(a), z0 + (z1 - z0) * i / n, False))
    return salida


def interiores_de_bulge(p0: Sequence[float], p1: Sequence[float], bulge: float, tolerancia: float) -> list[Punto]:
    """Puntos interiores del arco entre `p0` y `p1` dado el `bulge` de DXF (tan(ángulo/4); positivo = antihorario)."""
    if abs(bulge) < 1e-12:
        return []
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    cuerda = math.hypot(dx, dy)
    if cuerda == 0:
        return []
    desplazamiento = (cuerda / 2.0) * (1.0 - bulge * bulge) / (2.0 * bulge)
    cx = (p0[0] + p1[0]) / 2.0 - dy / cuerda * desplazamiento
    cy = (p0[1] + p1[1]) / 2.0 + dx / cuerda * desplazamiento
    radio = math.hypot(p0[0] - cx, p0[1] - cy)
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    barrido = 4.0 * math.atan(bulge)
    z0 = p0[2] if len(p0) > 2 else 0.0
    z1 = p1[2] if len(p1) > 2 else 0.0
    return puntos_de_arco(cx, cy, radio, a0, barrido, z0, z1, tolerancia)


def puntos_de_circulo(cx: float, cy: float, cz: float, radio: float, tolerancia: float) -> list[Punto]:
    """Polígono de un círculo (cerrado: el último repite el primero). Mínimo 8 lados."""
    n = max(8, segmentos_de_arco(radio, 2 * math.pi, tolerancia))
    puntos = [(cx + radio * math.cos(2 * math.pi * i / n), cy + radio * math.sin(2 * math.pi * i / n), cz, i == 0) for i in range(n)]
    puntos.append(puntos[0])
    return puntos


def puntos_de_arco_dxf(cx: float, cy: float, cz: float, radio: float, inicio_grados: float, fin_grados: float, tolerancia: float) -> list[Punto]:
    """Un ARC de DXF: sus dos extremos son originales; los intermedios, generados."""
    a0 = math.radians(inicio_grados)
    barrido = math.radians((fin_grados - inicio_grados) % 360.0) or 2 * math.pi
    inicio = (cx + radio * math.cos(a0), cy + radio * math.sin(a0), cz, True)
    fin = (cx + radio * math.cos(a0 + barrido), cy + radio * math.sin(a0 + barrido), cz, True)
    return [inicio, *puntos_de_arco(cx, cy, radio, a0, barrido, cz, cz, tolerancia), fin]


# -- Limpieza ---------------------------------------------------------------------------------------------------

def _alineado(a: Punto, b: Punto, c: Punto) -> bool:
    """¿`b` está exactamente sobre la recta a–c (en planta y en elevación)? Con un margen de 1e-12 relativo para el redondeo."""
    abx, aby = b[0] - a[0], b[1] - a[1]
    acx, acy = c[0] - a[0], c[1] - a[1]
    largo = math.hypot(acx, acy)
    if largo == 0:
        return False
    cruz = abs(abx * acy - aby * acx)
    if cruz > 1e-12 * largo * max(1.0, math.hypot(abx, aby)):
        return False
    t = (abx * acx + aby * acy) / (largo * largo)
    if t < 0 or t > 1:
        return False
    z_esperada = a[2] + (c[2] - a[2]) * t
    return abs(b[2] - z_esperada) <= 1e-9


def limpiar(puntos: Iterable[Punto]) -> list[Punto]:
    """Quita duplicados exactos consecutivos (conservando el original si uno de los dos lo es) y los puntos GENERADOS que
    están exactamente alineados con sus vecinos. Los vértices originales no se mueven ni se descartan (salvo duplicados exactos)."""
    sin_duplicados: list[Punto] = []
    for p in puntos:
        if sin_duplicados and p[:3] == sin_duplicados[-1][:3]:
            if p[3] and not sin_duplicados[-1][3]:
                sin_duplicados[-1] = p
            continue
        sin_duplicados.append(p)
    if len(sin_duplicados) < 3:
        return sin_duplicados
    salida: list[Punto] = [sin_duplicados[0]]
    for i in range(1, len(sin_duplicados) - 1):
        actual = sin_duplicados[i]
        if not actual[3] and _alineado(salida[-1], actual, sin_duplicados[i + 1]):
            continue
        salida.append(actual)
    salida.append(sin_duplicados[-1])
    return salida


# -- Reproyección -----------------------------------------------------------------------------------------------

def _metros(dlon: float, dlat: float, lat: float) -> float:
    return math.hypot(dlon * 111_320.0 * math.cos(math.radians(lat)), dlat * 110_540.0)


def _medio_geodesico(lon1: float, lat1: float, lon2: float, lat2: float) -> tuple[float, float]:
    """Punto medio del círculo máximo entre dos puntos: así dibuja Google Earth una línea de un KML entre dos vértices."""
    def vector(lon, lat):
        lo, la = math.radians(lon), math.radians(lat)
        return math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)

    a, b = vector(lon1, lat1), vector(lon2, lat2)
    x, y, z = a[0] + b[0], a[1] + b[1], a[2] + b[2]
    return math.degrees(math.atan2(y, x)), math.degrees(math.atan2(z, math.hypot(x, y)))


def subdividir_si_se_dobla(a: Punto, b: Punto, a_geografica: Callable[[float, float], tuple[float, float]], tolerancia: float, nivel: int = 0) -> list[Punto]:
    """Puntos intermedios que hacen falta para que el segmento recto UTM a–b siga siendo fiel tras reproyectarlo.

    Se compara el punto medio reproyectado con el medio geodésico de sus extremos (cómo dibuja Google Earth la línea del KML): si se
    separan más que `tolerancia` se parte por la mitad y se repite en cada mitad. A escala de levantamiento (segmentos de pocos kilómetros) no agrega nada.
    """
    if nivel >= 12 or math.hypot(b[0] - a[0], b[1] - a[1]) < LARGO_MINIMO_REPROYECCION:
        return []
    medio = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, False)
    lon_m, lat_m = a_geografica(medio[0], medio[1])
    lon_a, lat_a = a_geografica(a[0], a[1])
    lon_b, lat_b = a_geografica(b[0], b[1])
    lon_g, lat_g = _medio_geodesico(lon_a, lat_a, lon_b, lat_b)
    if _metros(lon_m - lon_g, lat_m - lat_g, lat_m) <= tolerancia:
        return []
    return subdividir_si_se_dobla(a, medio, a_geografica, tolerancia, nivel + 1) + [medio] + subdividir_si_se_dobla(medio, b, a_geografica, tolerancia, nivel + 1)


def densificar_para_reproyectar(puntos: Sequence[Punto], a_geografica: Callable[[float, float], tuple[float, float]], tolerancia: float) -> list[Punto]:
    salida: list[Punto] = []
    for i, p in enumerate(puntos):
        if i:
            salida.extend(subdividir_si_se_dobla(puntos[i - 1], p, a_geografica, tolerancia))
        salida.append(p)
    return salida
