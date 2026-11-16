"""Lotificación de una manzana: partir un polígono en lotes y su cuadro de áreas.

El frente es un lado del polígono (el que da a la calle). Los lotes se cortan con rectas perpendiculares al frente:
- `por_cantidad`: n lotes de la misma superficie;
- `por_superficie`: lotes de una superficie dada (el último se queda con lo que sobra, o se reparte si queda chico);
- `por_frente`: lotes con un ancho de frente dado, medido sobre el frente (el último con lo que sobra).

Cada corte se busca por bisección sobre la distancia a lo largo del frente, con el área exacta del recorte (shapely), así funciona
con manzanas irregulares (no solo rectángulos). El dibujo trae los lotes (polilíneas cerradas), su número y superficie al centro y el
cuadro de áreas con el total.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Texto
from .escala import Escala
from .superficie import formato_ha_a_ca, formato_miles

__all__ = ["Lote", "lotificar", "dibujar_lotes", "CAPAS_LOTES"]

CAPAS_LOTES = {
    "lotes": Capa("LOTES", 4, grosor_mm=0.25, descripcion="Lotes"),
    "textos": Capa("LOTES_TEXTOS", 7, descripcion="Número y superficie de los lotes"),
    "tabla": Capa("LOTES_TABLA", 7, descripcion="Cuadro de áreas de los lotes"),
}


@dataclass
class Lote:
    numero: str
    vertices: list[tuple[float, float]]
    superficie: float
    frente: float  # longitud sobre el lado de frente


def _poligono(vertices):
    import shapely

    pol = shapely.Polygon([(float(p[0]), float(p[1])) for p in vertices])
    if not pol.is_valid or pol.area <= 0:
        raise ValueError("La manzana debe ser una polilínea cerrada que no se cruce.")
    return pol


def lotificar(vertices: Sequence[Sequence[float]], lado_frente: int, metodo: str, valor: float, primer_numero: int = 1, prefijo: str = "",
              minimo: float = 0.5) -> list[Lote]:
    """Parte la manzana en lotes. `lado_frente`: índice del lado (del vértice i al i+1). `minimo`: fracción del lote tipo bajo la cual
    el sobrante del final se une al lote anterior en lugar de quedar como lote chico."""
    import shapely

    pol = _poligono(vertices)
    v = [tuple(map(float, p[:2])) for p in vertices]
    n = len(v)
    if not 0 <= lado_frente < n:
        raise ValueError("Elige el lado del frente.")
    a, b = v[lado_frente], v[(lado_frente + 1) % n]
    largo = math.hypot(b[0] - a[0], b[1] - a[1])
    if largo <= 0:
        raise ValueError("El lado del frente no tiene longitud.")
    ux, uy = (b[0] - a[0]) / largo, (b[1] - a[1]) / largo
    # proyección de toda la manzana sobre la dirección del frente: los cortes recorren ese rango
    proy = [((x - a[0]) * ux + (y - a[1]) * uy) for x, y in pol.exterior.coords]
    t0, t1 = min(proy), max(proy)
    grande = (pol.bounds[2] - pol.bounds[0] + pol.bounds[3] - pol.bounds[1]) * 4 + largo

    def recorte(t_ini: float, t_fin: float):
        """La parte de la manzana entre dos rectas perpendiculares al frente (en las distancias t_ini y t_fin a lo largo de él)."""
        caja = shapely.Polygon([(a[0] + ux * t - uy * s, a[1] + uy * t + ux * s) for t, s in ((t_ini, -grande), (t_fin, -grande), (t_fin, grande), (t_ini, grande))])
        return pol.intersection(caja)

    def corte_por_area(t_ini, objetivo):
        lo, hi = t_ini, t1
        for _ in range(80):
            m = (lo + hi) / 2
            if recorte(t_ini, m).area < objetivo:
                lo = m
            else:
                hi = m
        return (lo + hi) / 2

    total = pol.area
    cortes = [t0]
    if metodo == "por_cantidad":
        k = int(valor)
        if not 1 <= k <= 500:
            raise ValueError("El número de lotes va de 1 a 500.")
        for i in range(1, k):
            cortes.append(corte_por_area(cortes[-1], total / k))
    elif metodo == "por_superficie":
        if not valor > 0:
            raise ValueError("La superficie del lote debe ser positiva.")
        if total / valor > 500:
            raise ValueError("Saldrían más de 500 lotes. Revisa la superficie.")
        while recorte(cortes[-1], t1).area > valor * (1 + minimo):
            cortes.append(corte_por_area(cortes[-1], valor))
    elif metodo == "por_frente":
        if not valor > 0:
            raise ValueError("El frente del lote debe ser positivo.")
        if (t1 - t0) / valor > 500:
            raise ValueError("Saldrían más de 500 lotes. Revisa el frente.")
        t = max(t0, 0.0) + valor
        while t1 - t > valor * minimo:
            cortes.append(t)
            t += valor
    else:
        raise ValueError("Elige cómo partir la manzana.")
    cortes.append(t1)
    lotes = []
    for i, (ti, tf) in enumerate(zip(cortes, cortes[1:])):
        g = recorte(ti, tf)
        if g.is_empty or g.area <= 1e-9:
            continue
        if g.geom_type != "Polygon":
            g = max(getattr(g, "geoms", [g]), key=lambda x: x.area)
        coords = list(g.exterior.coords)[:-1]
        lotes.append(Lote(f"{prefijo}{primer_numero + len(lotes)}", [(float(x), float(y)) for x, y in coords], float(g.area),
                          max(0.0, min(tf, largo) - max(ti, 0.0))))
    return lotes


def dibujar_lotes(lotes: Sequence[Lote], esquina_tabla: tuple[float, float] | None, escala: Escala | None = None, decimales: int = 3,
                  titulo: str = "CUADRO DE ÁREAS", estilo: str = "CIVIL") -> Dibujo:
    import shapely

    esc = escala or Escala()
    d = Dibujo()
    for c in CAPAS_LOTES.values():
        d.capa(c)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    cl, ct, ctab = (CAPAS_LOTES[k].nombre for k in ("lotes", "textos", "tabla"))
    h = esc.altura("vertice_numero")
    for l in lotes:
        d.agregar(Polilinea(cl, vertices=l.vertices, cerrada=True, datos={"TVCIVIL": [(1000, "lote"), (1000, l.numero)]}))
        c = shapely.Polygon(l.vertices).representative_point()
        d.agregar(Texto(ct, valor=f"LOTE {l.numero}", posicion=(c.x, c.y + h * 0.7), altura=h, estilo=estilo, alineacion_h="centro", alineacion_v="medio"),
                  Texto(ct, valor=f"{formato_miles(l.superficie, decimales, ',')} m²", posicion=(c.x, c.y - h * 0.7), altura=h * 0.8, estilo=estilo,
                        alineacion_h="centro", alineacion_v="medio"))
    if esquina_tabla is not None:
        f = float(esc.denominador) / 1000.0
        hd = esc.altura("cuadro_dato")
        anchos = [25 * f, 35 * f, 30 * f]
        x0, y0 = esquina_tabla
        xs = [x0, x0 + anchos[0], x0 + anchos[0] + anchos[1], x0 + sum(anchos)]
        fila = hd * 2.0
        filas = [("LOTE", "SUPERFICIE (m²)", "FRENTE (m)")] + [(l.numero, formato_miles(l.superficie, decimales, ","), formato_miles(l.frente, 2, ",")) for l in lotes]
        total = sum(l.superficie for l in lotes)
        filas.append(("TOTAL", formato_miles(total, decimales, ","), ""))
        d.agregar(Texto(ctab, valor=titulo, posicion=((xs[0] + xs[-1]) / 2, y0 + fila * 0.6), altura=hd * 1.3, estilo=estilo, alineacion_h="centro", alineacion_v="abajo"))
        for i, r in enumerate(filas):
            y = y0 - i * fila
            d.agregar(Linea(ctab, inicio=(xs[0], y, 0.0), fin=(xs[-1], y, 0.0)))
            for k, texto in enumerate(r):
                d.agregar(Texto(ctab, valor=texto, posicion=((xs[k] + xs[k + 1]) / 2, y - fila / 2), altura=hd, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
        yb = y0 - len(filas) * fila
        d.agregar(Linea(ctab, inicio=(xs[0], yb, 0.0), fin=(xs[-1], yb, 0.0)))
        for x in xs:
            d.agregar(Linea(ctab, inicio=(x, y0, 0.0), fin=(x, yb, 0.0)))
        d.agregar(Texto(ctab, valor=f"({formato_ha_a_ca(total)})", posicion=((xs[0] + xs[-1]) / 2, yb - fila * 0.6), altura=hd, estilo=estilo, alineacion_h="centro", alineacion_v="arriba"))
    return d
