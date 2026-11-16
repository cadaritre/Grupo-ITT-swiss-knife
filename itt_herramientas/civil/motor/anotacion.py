"""Anotar en el plano: rumbo y distancia de cada lado y números de vértice, y la cuadrícula UTM con sus coordenadas al margen.

Todo en mm de papel por la escala (`Escala.altura`). Los textos siempre se leen derechos (giro entre −90° y 90°).

- `anotar_lados`: sobre cada lado su rumbo («N 45°30'15" E») y bajo él su distancia, alineados con el lado y centrados; en un lado
  curvo, la cuerda y debajo «Δ … R … LC …».
- `numerar_vertices`: el número o la letra de cada vértice hacia fuera del polígono.
- `cuadricula_utm`: líneas o cruces cada `intervalo` m dentro de un marco, con las coordenadas en sus cuatro márgenes (E abajo y
  arriba, N a la izquierda y a la derecha, giradas) y, con zona, la latitud y longitud de las esquinas.
"""

from __future__ import annotations

import math
from typing import Sequence

from .angulos import azimut, formato_dms, formato_rumbo
from .cuadro import _arco, etiqueta_vertice
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Texto
from .escala import Escala
from .superficie import formato_miles

__all__ = ["CAPAS_ANOTACION", "CAPAS_RETICULA", "anotar_lados", "numerar_vertices", "cuadricula_utm", "intervalo_reticula"]

CAPAS_ANOTACION = {
    "rumbo": Capa("LADOS_RUMBO", 7, descripcion="Rumbo de cada lado"),
    "distancia": Capa("LADOS_DISTANCIA", 7, descripcion="Distancia de cada lado"),
    "vertices": Capa("VERTICES_POLIGONO", 1, descripcion="Número de vértice en el plano"),
}
CAPAS_RETICULA = {
    "lineas": Capa("RETICULA_UTM", 8, descripcion="Cuadrícula UTM"),
    "marco": Capa("RETICULA_MARCO", 7, grosor_mm=0.35, descripcion="Marco de la cuadrícula UTM"),
    "textos": Capa("RETICULA_TEXTOS", 7, descripcion="Coordenadas de la cuadrícula UTM"),
}
ESTILO = "CIVIL"


def _derecho(grados: float) -> float:
    """Giro de un texto para que se lea derecho: entre −90° y 90°."""
    g = (grados + 180.0) % 360.0 - 180.0
    if g > 90.0:
        g -= 180.0
    elif g <= -90.0:
        g += 180.0
    return g


def _vertices(pl: dict) -> tuple[list[tuple[float, float]], list[float], bool]:
    v = [(float(p[0]), float(p[1])) for p in pl.get("vertices") or []]
    b = [float(x or 0.0) for x in (pl.get("combas") or [])][: len(v)]
    b += [0.0] * (len(v) - len(b))
    return v, b, bool(pl.get("cerrada"))


def anotar_lados(polilineas: Sequence[dict], escala: Escala | None = None, decimales_distancia: int = 2, decimales_segundos: int = 0,
                 oeste: str = "W", separador_miles: str = "", curvas: bool = True) -> Dibujo:
    """Rumbo y distancia de cada lado de las polilíneas (y líneas) dadas."""
    esc = escala or Escala()
    h = esc.altura("lado_rumbo_distancia")
    d = Dibujo()
    for c in CAPAS_ANOTACION.values():
        d.capa(c)
    d.estilo(EstiloTexto(ESTILO, "romans.shx", 0.85))
    n = 0
    for pl in polilineas:
        v, b, cerrada = _vertices(pl)
        tramos = len(v) if cerrada else len(v) - 1
        for i in range(max(0, tramos)):
            a, c = v[i], v[(i + 1) % len(v)]
            largo = math.hypot(c[0] - a[0], c[1] - a[1])
            if largo <= 1e-9:
                continue
            az = azimut(a[0], a[1], c[0], c[1])
            giro = _derecho(90.0 - az)
            ang = math.radians(giro)
            nx, ny = -math.sin(ang), math.cos(ang)  # normal «arriba» del texto ya derecho
            mx, my = (a[0] + c[0]) / 2.0, (a[1] + c[1]) / 2.0
            if b[i] and curvas:
                theta, r, _, lc = _arco(a, c, b[i])
                abajo = f"Δ={formato_dms(abs(math.degrees(theta)), decimales_segundos)} R={formato_miles(r, decimales_distancia, separador_miles)} LC={formato_miles(lc, decimales_distancia, separador_miles)}"
            else:
                abajo = formato_miles(largo, decimales_distancia, separador_miles)
            sep = h * 0.6
            d.agregar(Texto(CAPAS_ANOTACION["rumbo"].nombre, valor=formato_rumbo(az, decimales_segundos, oeste), posicion=(mx + nx * sep, my + ny * sep),
                            altura=h, rotacion=giro, estilo=ESTILO, alineacion_h="centro", alineacion_v="base"))
            d.agregar(Texto(CAPAS_ANOTACION["distancia"].nombre, valor=abajo, posicion=(mx - nx * sep, my - ny * sep),
                            altura=h, rotacion=giro, estilo=ESTILO, alineacion_h="centro", alineacion_v="arriba"))
            n += 1
    if not n:
        raise ValueError("Elige polilíneas o líneas con lados.")
    return d


def numerar_vertices(polilineas: Sequence[dict], escala: Escala | None = None, numeracion: str = "numeros", primer_numero: int = 1) -> Dibujo:
    """El número (o letra) de cada vértice, hacia fuera de su polilínea; la numeración sigue de una polilínea a la siguiente."""
    esc = escala or Escala()
    h = esc.altura("vertice_numero")
    d = Dibujo()
    capa = d.capa(CAPAS_ANOTACION["vertices"])
    d.estilo(EstiloTexto(ESTILO, "romans.shx", 0.85))
    k = 0
    for pl in polilineas:
        v, _, cerrada = _vertices(pl)
        if cerrada and len(v) > 1 and v[0] == v[-1]:
            v = v[:-1]
        if not v:
            continue
        cx, cy = sum(p[0] for p in v) / len(v), sum(p[1] for p in v) / len(v)
        for i, (px, py) in enumerate(v):
            if cerrada or len(v) < 3:
                dx, dy = px - cx, py - cy
            else:  # abierta: hacia fuera de la bisectriz del vértice
                a, c = v[max(0, i - 1)], v[min(len(v) - 1, i + 1)]
                dx, dy = -(c[1] - a[1]), c[0] - a[0]
            nrm = math.hypot(dx, dy) or 1.0
            d.agregar(Texto(capa, valor=etiqueta_vertice(k, numeracion, primer_numero), posicion=(px + dx / nrm * h, py + dy / nrm * h), altura=h,
                            estilo=ESTILO, alineacion_h="centro", alineacion_v="medio"))
            k += 1
    if not k:
        raise ValueError("Elige polilíneas con vértices.")
    return d


#: Intervalos «redondos» de la cuadrícula, en metros.
_INTERVALOS = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000)


def intervalo_reticula(escala: Escala, ancho: float, alto: float) -> float:
    """El intervalo que deja las líneas a unos 5 cm de papel y al menos dos por lado."""
    objetivo = 50.0 * float(escala.denominador) / 1000.0
    candidatos = [i for i in _INTERVALOS if min(ancho, alto) / i >= 2] or [_INTERVALOS[0]]
    return float(min(candidatos, key=lambda i: abs(math.log(i / objetivo))))


def cuadricula_utm(caja: Sequence[float], intervalo: float | None = None, escala: Escala | None = None, tipo: str = "lineas",
                   marco: bool = True, zona: int | None = None, hemisferio: str = "N", decimales_geo: int = 2, oeste: str = "W") -> Dibujo:
    """Cuadrícula UTM en la caja [xmin, ymin, xmax, ymax] (redondeada hacia fuera al intervalo)."""
    esc = escala or Escala()
    x0, y0, x1, y1 = (float(v) for v in caja)
    if not (x1 > x0 and y1 > y0):
        raise ValueError("Señala dos esquinas distintas.")
    paso = float(intervalo) if intervalo else intervalo_reticula(esc, x1 - x0, y1 - y0)
    if paso <= 0:
        raise ValueError("El intervalo debe ser positivo.")
    if (x1 - x0) / paso > 400 or (y1 - y0) / paso > 400:
        raise ValueError("Con ese intervalo saldrían demasiadas líneas. Usa uno mayor.")
    if tipo not in ("lineas", "cruces"):
        raise ValueError("Elige líneas o cruces.")
    x0, y0 = math.floor(x0 / paso) * paso, math.floor(y0 / paso) * paso
    x1, y1 = math.ceil(x1 / paso) * paso, math.ceil(y1 / paso) * paso
    h = esc.altura("cuadro_dato")
    f = float(esc.denominador) / 1000.0
    d = Dibujo()
    for c in CAPAS_RETICULA.values():
        d.capa(c)
    d.estilo(EstiloTexto(ESTILO, "romans.shx", 0.85))
    cl, cm, ct = (CAPAS_RETICULA[k].nombre for k in ("lineas", "marco", "textos"))
    xs = [x0 + i * paso for i in range(int(round((x1 - x0) / paso)) + 1)]
    ys = [y0 + i * paso for i in range(int(round((y1 - y0) / paso)) + 1)]
    if tipo == "lineas":
        for x in xs[1:-1]:
            d.agregar(Linea(cl, inicio=(x, y0, 0.0), fin=(x, y1, 0.0)))
        for y in ys[1:-1]:
            d.agregar(Linea(cl, inicio=(x0, y, 0.0), fin=(x1, y, 0.0)))
    else:
        brazo = 2.5 * f
        for x in xs[1:-1]:
            for y in ys[1:-1]:
                d.agregar(Linea(cl, inicio=(x - brazo, y, 0.0), fin=(x + brazo, y, 0.0)), Linea(cl, inicio=(x, y - brazo, 0.0), fin=(x, y + brazo, 0.0)))
        tic = 2.0 * f   # marcas cortas en el marco
        for x in xs[1:-1]:
            d.agregar(Linea(cl, inicio=(x, y0, 0.0), fin=(x, y0 + tic, 0.0)), Linea(cl, inicio=(x, y1, 0.0), fin=(x, y1 - tic, 0.0)))
        for y in ys[1:-1]:
            d.agregar(Linea(cl, inicio=(x0, y, 0.0), fin=(x0 + tic, y, 0.0)), Linea(cl, inicio=(x1, y, 0.0), fin=(x1 - tic, y, 0.0)))
    if marco:
        d.agregar(Polilinea(cm, vertices=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)], cerrada=True))
    dec = 0 if paso >= 1 else 2
    margen = h * 0.8
    for x in xs:
        t = f"E {formato_miles(x, dec, ',')}"
        d.agregar(Texto(ct, valor=t, posicion=(x, y0 - margen), altura=h, estilo=ESTILO, alineacion_h="centro", alineacion_v="arriba"),
                  Texto(ct, valor=t, posicion=(x, y1 + margen), altura=h, estilo=ESTILO, alineacion_h="centro", alineacion_v="base"))
    for y in ys:
        t = f"N {formato_miles(y, dec, ',')}"
        d.agregar(Texto(ct, valor=t, posicion=(x0 - margen, y), altura=h, rotacion=90.0, estilo=ESTILO, alineacion_h="centro", alineacion_v="base"),
                  Texto(ct, valor=t, posicion=(x1 + margen, y), altura=h, rotacion=90.0, estilo=ESTILO, alineacion_h="centro", alineacion_v="arriba"))
    if zona:
        from .geodesia import datos_geo, formato_latitud, formato_longitud

        for (x, y), (ah, av) in (((x0, y0), ("derecha", "arriba")), ((x1, y0), ("izquierda", "arriba")), ((x0, y1), ("derecha", "base")), ((x1, y1), ("izquierda", "base"))):
            g = datos_geo(x, y, zona, hemisferio)
            dx = -margen * 3 if ah == "derecha" else margen * 3
            dy = -margen * 3 if av == "arriba" else margen * 3
            d.agregar(Texto(ct, valor=formato_latitud(g.latitud, decimales_geo), posicion=(x + dx, y + dy), altura=h * 0.9, estilo=ESTILO, alineacion_h=ah, alineacion_v=av),
                      Texto(ct, valor=formato_longitud(g.longitud, decimales_geo, oeste), posicion=(x + dx, y + dy - (1.6 * h if av == "arriba" else -1.6 * h)),
                            altura=h * 0.9, estilo=ESTILO, alineacion_h=ah, alineacion_v=av))
    return d
