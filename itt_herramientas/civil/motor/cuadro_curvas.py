"""Cuadro de curvas: tabla aparte de las curvas circulares de una o varias polilíneas (lados con comba).

Columnas: CURVA (clave C1, C2, …), DELTA, RADIO, LONG. CURVA, SUBTANGENTE, CUERDA y RUMBO DE CUERDA, más las
coordenadas del centro. Cada curva se rotula con su clave en el plano, en el punto medio del arco, del lado de fuera.

Mismas fórmulas que los lados curvos del cuadro de construcción (`cuadro._arco`): Δ = 4·atan(comba), R = c(1+b²)/(4|b|),
LC = R·Δ, ST = R·tan(Δ/2). El rumbo de la cuerda va del inicio al final del tramo, en el sentido en que está dibujado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .angulos import azimut, formato_dms, formato_rumbo
from .cuadro import CAPAS_CUADRO, _arco
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Texto
from .escala import Escala
from .superficie import formato_miles

__all__ = ["Curva", "CAPA_CLAVES", "curvas_de_polilineas", "dibujar_cuadro_curvas"]

CAPA_CLAVES = Capa("CUADRO_CURVAS_CLAVE", 1, descripcion="Clave de curva en el plano")


@dataclass
class Curva:
    clave: str
    delta: float  # grados, sin signo
    radio: float
    longitud: float
    subtangente: float
    cuerda: float
    azimut_cuerda: float
    inicio: tuple[float, float]
    fin: tuple[float, float]
    centro: tuple[float, float]
    medio: tuple[float, float]  # punto medio del arco
    sentido: str  # "horario" o "antihorario"


def curvas_de_polilineas(polilineas: Sequence[dict], primer_numero: int = 1, prefijo: str = "C") -> list[Curva]:
    """Las curvas (tramos con comba) de las polilíneas, en orden: polilínea por polilínea, tramo por tramo.

    Cada polilínea: {"vertices": [[x, y], …], "combas": [b0, b1, …], "cerrada": bool}. La comba de un vértice es la del
    tramo que sale de él (convención DXF: positiva = antihorario).
    """
    salida: list[Curva] = []
    for pl in polilineas:
        v = [(float(p[0]), float(p[1])) for p in pl.get("vertices") or []]
        b = [float(x or 0.0) for x in (pl.get("combas") or [])] + [0.0] * len(v)
        n = len(v)
        tramos = n if pl.get("cerrada") else n - 1
        for i in range(max(0, tramos)):
            a, c, comba = v[i], v[(i + 1) % n], b[i]
            cuerda = math.hypot(c[0] - a[0], c[1] - a[1])
            if not comba or cuerda <= 1e-9:
                continue
            theta, r, centro, longitud = _arco(a, c, comba)
            # punto medio del arco: desde el centro, en la dirección del medio de la cuerda girada hacia el arco
            mx, my = (a[0] + c[0]) / 2.0, (a[1] + c[1]) / 2.0
            flecha = cuerda / 2.0 * comba  # sagita con signo, a la derecha de la cuerda si es positiva
            nx, ny = (c[1] - a[1]) / cuerda, -(c[0] - a[0]) / cuerda
            medio = (mx + nx * flecha, my + ny * flecha)
            salida.append(Curva(f"{prefijo}{primer_numero + len(salida)}", abs(math.degrees(theta)), r, longitud, r * math.tan(abs(theta) / 2.0),
                                cuerda, azimut(a[0], a[1], c[0], c[1]), a, c, centro, medio, "antihorario" if theta > 0 else "horario"))
    return salida


_COLUMNAS = (("clave", "CURVA", 14), ("delta", "DELTA", 24), ("radio", "RADIO", 20), ("longitud", "LONG. CURVA", 22), ("subtangente", "SUBTANGENTE", 22),
             ("cuerda", "CUERDA", 20), ("rumbo", "RUMBO DE CUERDA", 30), ("y", "CENTRO Y", 27), ("x", "CENTRO X", 27))


def filas_texto(curvas: Sequence[Curva], decimales: int = 3, decimales_segundos: int = 0, decimales_coordenadas: int = 3, oeste: str = "W",
                separador_miles: str = "") -> list[dict[str, str]]:
    m = lambda x, d=decimales: formato_miles(x, d, separador_miles)
    return [{"clave": c.clave, "delta": formato_dms(c.delta, decimales_segundos), "radio": m(c.radio), "longitud": m(c.longitud), "subtangente": m(c.subtangente),
             "cuerda": m(c.cuerda), "rumbo": formato_rumbo(c.azimut_cuerda, decimales_segundos, oeste), "y": m(c.centro[1], decimales_coordenadas),
             "x": m(c.centro[0], decimales_coordenadas)} for c in curvas]


def dibujar_cuadro_curvas(curvas: Sequence[Curva], esquina: tuple[float, float], escala: Escala | None = None, titulo: str = "CUADRO DE CURVAS",
                          rotular: bool = True, estilo: str = "CIVIL", **formato) -> Dibujo:
    """La tabla con la esquina superior izquierda en `esquina` (retícula de líneas y textos centrados, tamaños por la escala) y, con
    `rotular`, la clave de cada curva en el plano junto al punto medio de su arco."""
    if not curvas:
        raise ValueError("No hay curvas: elige polilíneas con lados curvos.")
    esc = escala or Escala()
    f = float(esc.denominador) / 1000.0 * esc.unidades_por_metro
    d = Dibujo()
    for c in CAPAS_CUADRO.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    capa_l, capa_t, capa_ti = CAPAS_CUADRO["lineas"].nombre, CAPAS_CUADRO["textos"].nombre, CAPAS_CUADRO["titulo"].nombre
    h_dato, h_enc, h_tit = esc.altura("cuadro_dato"), esc.altura("cuadro_encabezado"), esc.altura("cuadro_titulo")
    xs = [float(esquina[0])]
    for _, _, ancho in _COLUMNAS:
        xs.append(xs[-1] + ancho * f)
    x0, y0, x1 = xs[0], float(esquina[1]), xs[-1]

    def texto(valor, x, y, h, capa=capa_t):
        if valor:
            d.agregar(Texto(capa, valor=valor, posicion=(x, y), altura=h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))

    def hline(y):
        d.agregar(Linea(capa_l, inicio=(x0, y, 0.0), fin=(x1, y, 0.0)))

    y = y0
    alto_tit, alto_enc, alto_fila = h_tit * 2.2, h_enc * 2.4, h_dato * 2.0
    hline(y)
    texto(titulo, (x0 + x1) / 2, y - alto_tit / 2, h_tit, capa_ti)
    y -= alto_tit
    hline(y)
    y_enc = y
    for k, (_, nombre, _) in enumerate(_COLUMNAS):
        texto(nombre, (xs[k] + xs[k + 1]) / 2, y - alto_enc / 2, h_enc)
    y -= alto_enc
    hline(y)
    for fila in filas_texto(curvas, **formato):
        for k, (clave, _, _) in enumerate(_COLUMNAS):
            texto(fila[clave], (xs[k] + xs[k + 1]) / 2, y - alto_fila / 2, h_dato)
        y -= alto_fila
    hline(y)
    for k, x in enumerate(xs):
        d.agregar(Linea(capa_l, inicio=(x, y_enc if 0 < k < len(xs) - 1 else y0, 0.0), fin=(x, y, 0.0)))
    if rotular:
        capa_c = d.capa(CAPA_CLAVES)
        hv = esc.altura("vertice_numero")
        for c in curvas:
            dx, dy = c.medio[0] - c.centro[0], c.medio[1] - c.centro[1]
            n = math.hypot(dx, dy) or 1.0
            d.agregar(Texto(capa_c, valor=c.clave, posicion=(c.medio[0] + dx / n * hv, c.medio[1] + dy / n * hv), altura=hv, estilo=estilo,
                            alineacion_h="centro", alineacion_v="medio"))
    return d
