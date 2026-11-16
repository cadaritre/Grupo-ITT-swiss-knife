"""Cuadro de construcción de un polígono (y su dibujo como tabla de líneas y textos).

Columnas: LADO (EST, PV), RUMBO, DISTANCIA, V, COORDENADAS (Y, X) y, opcionales, AZIMUT y
ÁNGULO INTERIOR. Al pie, la SUPERFICIE en m² y en ha-a-ca.

Lados curvos: un tramo de polilínea con comba (bulge) es un arco. Su renglón da el rumbo y la
distancia de la cuerda, y debajo un renglón de curva con DELTA, RADIO, LONGITUD DE CURVA y
SUBTANGENTE, más el centro de la curva como vértice auxiliar (C1, C2, …) con sus coordenadas.
La superficie incluye los segmentos circulares; el perímetro usa la longitud de los arcos.
Los ángulos interiores se miden entre cuerdas.

Signo de la comba (convención DXF): positiva = arco antihorario de un vértice al siguiente.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

from .angulos import angulo_interior, azimut, formato_dms, formato_rumbo
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Texto
from .escala import Escala
from .superficie import formato_ha_a_ca, formato_miles

__all__ = [
    "DatosCurva",
    "Lado",
    "Cuadro",
    "OpcionesCuadro",
    "area_con_arcos",
    "etiqueta_vertice",
    "calcular_cuadro",
    "dibujar_cuadro",
    "CAPAS_CUADRO",
]

CAPAS_CUADRO = {
    "lineas": Capa("CUADRO_LINEAS", 7, descripcion="Retícula del cuadro de construcción"),
    "textos": Capa("CUADRO_TEXTOS", 7, descripcion="Textos del cuadro de construcción"),
    "titulo": Capa("CUADRO_TITULO", 7, descripcion="Título y superficie del cuadro"),
    "vertices": Capa("VERTICES_POLIGONO", 1, descripcion="Número de vértice en el plano"),
}


def _signed_area_poligono(xy: Sequence[tuple[float, float]]) -> float:
    n = len(xy)
    x0, y0 = xy[0]
    s = 0.0
    for i in range(n):
        xa, ya = xy[i][0] - x0, xy[i][1] - y0
        xb, yb = xy[(i + 1) % n][0] - x0, xy[(i + 1) % n][1] - y0
        s += xa * yb - xb * ya
    return s / 2.0


def _arco(a, b, comba):
    """Datos de un arco por comba: (theta con signo, radio, centro, longitud)."""
    theta = 4.0 * math.atan(comba)
    cx, cy = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0
    dx, dy = b[0] - a[0], b[1] - a[1]
    c = math.hypot(dx, dy)
    r = c * (1.0 + comba * comba) / (4.0 * abs(comba))
    desplaz = c * (1.0 - comba * comba) / (4.0 * comba)  # hacia la izquierda de la cuerda
    nx, ny = -dy / c, dx / c
    centro = (cx + nx * desplaz, cy + ny * desplaz)
    return theta, r, centro, r * abs(theta)


def area_con_arcos(vertices: Sequence[tuple[float, float]], combas: Sequence[float] | None = None) -> float:
    """Área con signo (positiva = antihorario) de un polígono cerrado con tramos curvos."""
    area = _signed_area_poligono(vertices)
    if combas:
        n = len(vertices)
        for i, b in enumerate(combas):
            if b:
                theta, r, _, _ = _arco(vertices[i], vertices[(i + 1) % n], b)
                area += r * r / 2.0 * (theta - math.sin(theta))
    return area


def etiqueta_vertice(indice: int, estilo: str = "numeros", inicio: int = 1) -> str:
    """1, 2, 3… o A, B, …, Z, AA, AB… (índice desde 0)."""
    if estilo == "numeros":
        return str(inicio + indice)
    if estilo == "letras":
        k = indice + 1
        texto = ""
        while k:
            k, resto = divmod(k - 1, 26)
            texto = chr(65 + resto) + texto
        return texto
    raise ValueError("Numeración «numeros» o «letras».")


@dataclass
class DatosCurva:
    delta: float  # grados, sin signo
    radio: float
    longitud: float
    subtangente: float
    cuerda: float
    centro: tuple[float, float]
    etiqueta_centro: str
    sentido: str  # "horario" o "antihorario"


@dataclass
class Lado:
    est: str
    pv: str
    azimut: float
    rumbo: str
    distancia: float
    v: str
    x: float
    y: float
    angulo_interior: float | None = None  # en el vértice PV
    curva: DatosCurva | None = None


@dataclass
class Cuadro:
    vertice_inicial: tuple[str, float, float]
    lados: list[Lado]
    superficie: float  # m², positiva
    perimetro: float
    sentido: str
    vertices: list[tuple[float, float]]  # en el orden del cuadro
    combas: list[float]
    suma_angulos: float | None = None

    @property
    def superficie_ha(self) -> str:
        return formato_ha_a_ca(self.superficie)


@dataclass
class OpcionesCuadro:
    sentido: str = "horario"  # "horario", "antihorario" o "dibujo"
    inicio: int = 0  # índice del vértice inicial en el orden ya orientado
    numeracion: str = "numeros"
    primer_numero: int = 1
    etiquetas: Sequence[str] | None = None  # etiquetas propias por vértice (orden original)
    decimales_distancia: int = 3
    decimales_coordenadas: int = 3
    decimales_segundos: int = 0
    decimales_superficie: int = 3
    oeste: str = "W"
    separador_miles: str = ""
    azimut: bool = False
    angulos_interiores: bool = False
    repetir_cierre: bool = True
    titulo: str = "CUADRO DE CONSTRUCCIÓN"


def _orientar(vertices, combas, sentido):
    v = [tuple(map(float, p[:2])) for p in vertices]
    b = list(combas) if combas else [0.0] * len(v)
    if len(b) != len(v):
        raise ValueError("Debe haber una comba por vértice.")
    area = area_con_arcos(v, b)
    if area == 0.0:
        raise ValueError("El polígono no tiene superficie.")
    antihorario = area > 0
    invertir = (sentido == "horario" and antihorario) or (sentido == "antihorario" and not antihorario)
    if sentido not in ("horario", "antihorario", "dibujo"):
        raise ValueError("Sentido «horario», «antihorario» o «dibujo».")
    indices = list(range(len(v)))
    if invertir:
        n = len(v)
        indices = [0] + list(range(n - 1, 0, -1))
        # el tramo i→i+1 con comba b[i] pasa a ser i+1→i con comba −b[i], guardada en i+1
        b = [-b[(k - 1) % n] for k in indices]
        v = [v[k] for k in indices]
    return v, b, indices, (area > 0) != invertir


def _limpiar(vertices, combas, tolerancia=1e-9):
    """Quita el vértice final repetido (polilíneas cerradas «a mano») y vértices duplicados seguidos."""
    v = list(vertices)
    b = list(combas) if combas else [0.0] * len(v)
    if len(v) > 2 and math.hypot(v[0][0] - v[-1][0], v[0][1] - v[-1][1]) <= tolerancia:
        v.pop()
        b.pop()
    salida_v, salida_b = [], []
    for p, c in zip(v, b):
        if salida_v and math.hypot(p[0] - salida_v[-1][0], p[1] - salida_v[-1][1]) <= tolerancia:
            continue
        salida_v.append(p)
        salida_b.append(c)
    if len(salida_v) < 3:
        raise ValueError("Un polígono necesita al menos tres vértices distintos.")
    return salida_v, salida_b


def _contorno_denso(vertices, combas, pasos_arco=16):
    pts = []
    n = len(vertices)
    for i in range(n):
        a, b = vertices[i], vertices[(i + 1) % n]
        pts.append(a)
        if combas[i]:
            theta, r, centro, _ = _arco(a, b, combas[i])
            ang0 = math.atan2(a[1] - centro[1], a[0] - centro[0])
            for k in range(1, pasos_arco):
                t = ang0 + theta * k / pasos_arco
                pts.append((centro[0] + r * math.cos(t), centro[1] + r * math.sin(t)))
    return pts


def _validar_simple(vertices, combas):
    import shapely

    pts = _contorno_denso(vertices, combas)
    anillo = shapely.LinearRing(pts)
    if not anillo.is_simple:
        raise ValueError("El polígono se cruza consigo mismo: revisa el orden de los vértices.")


def calcular_cuadro(vertices: Sequence[Sequence[float]], combas: Sequence[float] | None = None, opciones: OpcionesCuadro | None = None) -> Cuadro:
    """Calcula el cuadro de construcción de un polígono cerrado."""
    op = opciones or OpcionesCuadro()
    v0, b0 = _limpiar([tuple(map(float, p[:2])) for p in vertices], combas)
    _validar_simple(v0, b0)
    v, b, indices, antihorario = _orientar(v0, b0, op.sentido)
    n = len(v)
    if not 0 <= op.inicio < n:
        raise ValueError("Vértice inicial fuera del polígono.")
    if op.etiquetas is not None:
        if len(op.etiquetas) != len(v0):
            raise ValueError("Debe haber una etiqueta por vértice.")
        etiquetas_orig = list(op.etiquetas)
        etiquetas = [etiquetas_orig[k] for k in indices]
        etiquetas = etiquetas[op.inicio:] + etiquetas[: op.inicio]
    else:
        etiquetas = [etiqueta_vertice(k, op.numeracion, op.primer_numero) for k in range(n)]
    v = v[op.inicio:] + v[: op.inicio]
    b = b[op.inicio:] + b[: op.inicio]
    sentido = "antihorario" if antihorario else "horario"
    lados: list[Lado] = []
    perimetro = 0.0
    curvas = 0
    suma = 0.0
    for i in range(n):
        a, c = v[i], v[(i + 1) % n]
        dist = math.hypot(c[0] - a[0], c[1] - a[1])
        az = azimut(a[0], a[1], c[0], c[1])
        curva = None
        if b[i]:
            curvas += 1
            theta, r, centro, longitud = _arco(a, c, b[i])
            delta = abs(math.degrees(theta))
            curva = DatosCurva(delta, r, longitud, r * math.tan(abs(theta) / 2.0), dist, centro, f"C{curvas}", "antihorario" if theta > 0 else "horario")
            perimetro += longitud
        else:
            perimetro += dist
        ang = None
        if op.angulos_interiores:
            ang = angulo_interior(a, c, v[(i + 2) % n], horario=not antihorario)
            suma += ang
        lados.append(Lado(etiquetas[i], etiquetas[(i + 1) % n], az, formato_rumbo(az, op.decimales_segundos, op.oeste), dist, etiquetas[(i + 1) % n], c[0], c[1], ang, curva))
    area = abs(area_con_arcos(v, b))
    return Cuadro((etiquetas[0], v[0][0], v[0][1]), lados, area, perimetro, sentido, v, b, suma if op.angulos_interiores else None)


# --- Dibujo de la tabla -------------------------------------------------------------------

@dataclass
class _Columna:
    clave: str
    titulo: str
    ancho_mm: float
    grupo: str | None = None


def _columnas(op: OpcionesCuadro) -> list[_Columna]:
    cols = [_Columna("est", "EST", 11, "LADO"), _Columna("pv", "PV", 11, "LADO"), _Columna("rumbo", "RUMBO", 30), _Columna("distancia", "DISTANCIA", 21)]
    if op.azimut:
        cols.append(_Columna("azimut", "AZIMUT", 24))
    if op.angulos_interiores:
        cols.append(_Columna("angulo", "ÁNG. INT.", 24))
    cols += [_Columna("v", "V", 10), _Columna("y", "Y", 27, "COORDENADAS"), _Columna("x", "X", 27, "COORDENADAS")]
    return cols


def filas_texto(cuadro: Cuadro, op: OpcionesCuadro | None = None) -> list[dict[str, str]]:
    """Renglones del cuadro como texto ya formateado (lo mismo que se dibuja)."""
    op = op or OpcionesCuadro()
    dc, dd = op.decimales_coordenadas, op.decimales_distancia
    coord = lambda val: formato_miles(val, dc, op.separador_miles)
    filas: list[dict[str, str]] = [{"v": cuadro.vertice_inicial[0], "y": coord(cuadro.vertice_inicial[2]), "x": coord(cuadro.vertice_inicial[1])}]
    for k, lado in enumerate(cuadro.lados):
        ultimo = k == len(cuadro.lados) - 1
        fila = {"est": lado.est, "pv": lado.pv, "rumbo": lado.rumbo, "distancia": formato_miles(lado.distancia, dd, op.separador_miles), "v": lado.v}
        if op.azimut:
            fila["azimut"] = formato_dms(lado.azimut, op.decimales_segundos)
        if op.angulos_interiores and lado.angulo_interior is not None:
            fila["angulo"] = formato_dms(lado.angulo_interior, op.decimales_segundos)
        if lado.curva:
            cv = lado.curva
            filas.append({
                "est": "CURVA", "pv": "",
                "rumbo": f"Δ = {formato_dms(cv.delta, op.decimales_segundos)}  R = {formato_miles(cv.radio, dd, op.separador_miles)}",
                "distancia": f"LC = {formato_miles(cv.longitud, dd, op.separador_miles)}  ST = {formato_miles(cv.subtangente, dd, op.separador_miles)}",
                "v": cv.etiqueta_centro, "y": coord(cv.centro[1]), "x": coord(cv.centro[0]),
            })
        if not ultimo or op.repetir_cierre:
            fila["y"], fila["x"] = coord(lado.y), coord(lado.x)
        filas.append(fila)
    return filas


def dibujar_cuadro(
    cuadro: Cuadro,
    esquina: tuple[float, float],
    escala: Escala | None = None,
    op: OpcionesCuadro | None = None,
    estilo: str = "CIVIL",
    numerar_vertices: bool = True,
) -> Dibujo:
    """Dibuja el cuadro como retícula de líneas y textos centrados, con la esquina superior izquierda en `esquina`.

    Los tamaños salen de la escala del dibujo: alturas de `Escala.alturas_mm` y anchos de
    columna en mm de papel. Con `numerar_vertices` también rotula cada vértice en el plano.
    """
    op = op or OpcionesCuadro()
    esc = escala or Escala()
    f = float(esc.denominador) / 1000.0 * esc.unidades_por_metro  # mm de papel → unidades
    d = Dibujo()
    for c in CAPAS_CUADRO.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    capa_l, capa_t, capa_ti = CAPAS_CUADRO["lineas"].nombre, CAPAS_CUADRO["textos"].nombre, CAPAS_CUADRO["titulo"].nombre
    cols = _columnas(op)
    h_dato, h_enc, h_tit, h_sup = esc.altura("cuadro_dato"), esc.altura("cuadro_encabezado"), esc.altura("cuadro_titulo"), esc.altura("cuadro_superficie")
    alto_fila = h_dato * 2.0
    anchos = [c.ancho_mm * f for c in cols]
    x0, y0 = esquina
    xs = [x0]
    for a in anchos:
        xs.append(xs[-1] + a)
    ancho_total = xs[-1] - x0

    def texto(valor, x, y, h, capa=capa_t, alin="centro"):
        if valor:
            d.agregar(Texto(capa, valor=valor, posicion=(x, y), altura=h, estilo=estilo, alineacion_h=alin, alineacion_v="medio"))

    def hline(y, xa=x0, xb=None):
        d.agregar(Linea(capa_l, inicio=(xa, y, 0.0), fin=(xb if xb is not None else xs[-1], y, 0.0)))

    def vline(x, ya, yb):
        d.agregar(Linea(capa_l, inicio=(x, ya, 0.0), fin=(x, yb, 0.0)))

    y = y0
    # título
    alto_tit = h_tit * 2.2
    texto(op.titulo, x0 + ancho_total / 2, y - alto_tit / 2, h_tit, capa_ti)
    hline(y)
    y -= alto_tit
    hline(y)
    # encabezado de dos niveles
    alto_enc = h_enc * 2.0
    y_enc1, y_enc2 = y, y - alto_enc
    grupos: dict[str, list[int]] = {}
    for k, c in enumerate(cols):
        if c.grupo:
            grupos.setdefault(c.grupo, []).append(k)
    for g, ks in grupos.items():
        texto(g, (xs[ks[0]] + xs[ks[-1] + 1]) / 2, y_enc1 - alto_enc / 2, h_enc)
        hline(y_enc2, xs[ks[0]], xs[ks[-1] + 1])
    for k, c in enumerate(cols):
        if c.grupo:
            texto(c.titulo, (xs[k] + xs[k + 1]) / 2, y_enc2 - alto_enc / 2, h_enc)
        else:
            texto(c.titulo, (xs[k] + xs[k + 1]) / 2, y_enc1 - alto_enc, h_enc)
    y = y - 2 * alto_enc
    y_datos = y
    hline(y)
    # datos
    for fila in filas_texto(cuadro, op):
        for k, c in enumerate(cols):
            texto(fila.get(c.clave, ""), (xs[k] + xs[k + 1]) / 2, y - alto_fila / 2, h_dato)
        y -= alto_fila
    hline(y)
    # verticales: las de grupo arrancan bajo la línea del grupo
    for k in range(len(xs)):
        dentro_de_grupo = 0 < k < len(cols) and cols[k - 1].grupo is not None and cols[k - 1].grupo == cols[k].grupo
        vline(xs[k], y_enc2 if dentro_de_grupo else y_enc1, y)
    vline(x0, y0, y_enc1)
    vline(xs[-1], y0, y_enc1)
    # superficie
    alto_sup = h_sup * 2.2
    m2 = formato_miles(cuadro.superficie, op.decimales_superficie, op.separador_miles)
    texto(f"SUPERFICIE = {m2} m²     ({formato_ha_a_ca(cuadro.superficie)})", x0 + ancho_total / 2, y - alto_sup / 2, h_sup, capa_ti)
    vline(x0, y, y - alto_sup)
    vline(xs[-1], y, y - alto_sup)
    hline(y - alto_sup)
    if numerar_vertices:
        hv = esc.altura("vertice_numero")
        etiquetas = [cuadro.vertice_inicial[0]] + [l.v for l in cuadro.lados[:-1]]
        cx = sum(p[0] for p in cuadro.vertices) / len(cuadro.vertices)
        cy = sum(p[1] for p in cuadro.vertices) / len(cuadro.vertices)
        for et, (px, py) in zip(etiquetas, cuadro.vertices):
            dx, dy = px - cx, py - cy
            n = math.hypot(dx, dy) or 1.0
            d.agregar(Texto(CAPAS_CUADRO["vertices"].nombre, valor=et, posicion=(px + dx / n * hv, py + dy / n * hv), altura=hv, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
    return d
