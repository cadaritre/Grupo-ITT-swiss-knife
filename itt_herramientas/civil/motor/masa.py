"""Curva masa con línea compensadora: puntos de compensación, volúmenes que se mueven, acarreo medio, sobreacarreo, préstamo y
desperdicio.

La curva masa es la ordenada acumulada O(s) = Σ (corte · abundamiento − terraplén) a lo largo del cadenamiento (`volumenes`). Una
línea compensadora horizontal O = c corta la curva en los **puntos de compensación**: entre dos seguidos el material se equilibra.

- Volumen que se mueve en un tramo: V = max |O − c| en el tramo. Si la curva va arriba de la línea el material va hacia adelante
  (del corte al terraplén siguiente); abajo, hacia atrás.
- Distancia media de acarreo: d = ∫ |O − c| ds / V (el área del «lazo» entre la curva y la línea, dividida entre su altura).
- Sobreacarreo: V · max(0, d − acarreo libre), en m³·hm (hectómetros), como lo cobra la obra; el acarreo libre por omisión es 20 m.
- En los extremos, lo que falta para llegar a la línea es **préstamo** (falta material) o **desperdicio** (sobra).

Línea compensadora automática: la que hace mínimo ∫ |O − c| ds (el momento total de acarreo), que es la mediana de O ponderada por
la longitud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from .eje import formato_estacion
from .entidades import Capa, Dibujo, EstiloTexto, Linea, Polilinea, Texto
from .escala import Escala
from .superficie import formato_miles

__all__ = ["Lazo", "Compensacion", "linea_optima", "compensar", "dibujar_curva_masa", "CAPAS_MASA"]

CAPAS_MASA = {
    "curva": Capa("MASA_CURVA", 1, grosor_mm=0.35, descripcion="Curva masa"),
    "linea": Capa("MASA_COMPENSADORA", 3, grosor_mm=0.25, descripcion="Línea compensadora"),
    "reticula": Capa("MASA_RETICULA", 8, descripcion="Retícula de la curva masa"),
    "textos": Capa("MASA_TEXTOS", 7, descripcion="Textos de la curva masa"),
}


@dataclass
class Lazo:
    desde: float  # estación del punto de compensación inicial
    hasta: float
    volumen: float  # m³ que se mueven
    distancia_media: float  # m
    sentido: str  # "adelante" o "atrás"
    sobreacarreo: float  # m³·hm


@dataclass
class Compensacion:
    linea: float  # ordenada de la línea compensadora
    puntos: list[float]  # estaciones de los puntos de compensación
    lazos: list[Lazo]
    prestamo: float  # m³ que faltan (se traen de banco)
    desperdicio: float  # m³ que sobran (se tiran)
    acarreo_libre: float
    avisos: list[str] = field(default_factory=list)

    @property
    def compensado(self) -> float:
        return sum(l.volumen for l in self.lazos)

    @property
    def sobreacarreo(self) -> float:
        return sum(l.sobreacarreo for l in self.lazos)


def _curva(estaciones: Sequence[float], ordenadas: Sequence[float]) -> list[tuple[float, float]]:
    pts = sorted(zip(map(float, estaciones), map(float, ordenadas)))
    if len(pts) < 2 or pts[-1][0] - pts[0][0] <= 0:
        raise ValueError("La curva masa necesita al menos dos estaciones distintas.")
    return pts


def linea_optima(estaciones: Sequence[float], ordenadas: Sequence[float]) -> float:
    """La ordenada c que hace mínimo ∫|O(s) − c| ds sobre la curva poligonal: la mediana de O ponderada por longitud."""
    pts = _curva(estaciones, ordenadas)
    total = pts[-1][0] - pts[0][0]

    def bajo(c):  # longitud con O < c
        n = 0.0
        for (s0, o0), (s1, o1) in zip(pts, pts[1:]):
            L = s1 - s0
            if o0 == o1:
                n += L if o0 < c else 0.0
                continue
            t = (c - o0) / (o1 - o0)
            t = min(max(t, 0.0), 1.0)
            n += L * (t if o1 > o0 else 1.0 - t)
        return n

    a, b = min(o for _, o in pts), max(o for _, o in pts)
    for _ in range(100):
        m = (a + b) / 2
        if bajo(m) < total / 2:
            a = m
        else:
            b = m
    return (a + b) / 2


def compensar(estaciones: Sequence[float], ordenadas: Sequence[float], linea: float | None = None, acarreo_libre: float = 20.0) -> Compensacion:
    pts = _curva(estaciones, ordenadas)
    c = linea_optima(estaciones, ordenadas) if linea is None else float(linea)
    if acarreo_libre < 0:
        raise ValueError("El acarreo libre no puede ser negativo.")
    # puntos de compensación: cruces de O(s) con c (un tramo que coincide con la línea cuenta por sus extremos)
    eps = 1e-9 * max(1.0, max(abs(o) for _, o in pts))
    cruces: list[float] = [pts[0][0]] if abs(pts[0][1] - c) <= eps else []     # un extremo sobre la línea también compensa
    for (s0, o0), (s1, o1) in zip(pts, pts[1:]):
        d0, d1 = o0 - c, o1 - c
        if abs(d0) <= eps and (not cruces or cruces[-1] != s0) and s0 != pts[0][0]:
            cruces.append(s0)
        if d0 * d1 < 0:
            cruces.append(s0 + (s1 - s0) * d0 / (d0 - d1))
    if abs(pts[-1][1] - c) <= eps and (not cruces or cruces[-1] != pts[-1][0]):
        cruces.append(pts[-1][0])
    lazos: list[Lazo] = []
    for a, b in zip(cruces, cruces[1:]):
        tramo = _sub(pts, a, b)
        alto = max(abs(o - c) for _, o in tramo)
        if alto <= 1e-9:
            continue
        area = _integral_abs(tramo, c)
        d = area / alto
        arriba = sum(o - c for _, o in tramo) > 0
        lazos.append(Lazo(a, b, alto, d, "adelante" if arriba else "atrás", alto * max(0.0, d - acarreo_libre) / 100.0))
    prestamo = desperdicio = 0.0
    d_ini, d_fin = pts[0][1] - c, pts[-1][1] - c
    d_ini, d_fin = (0.0 if abs(d_ini) <= eps else d_ini), (0.0 if abs(d_fin) <= eps else d_fin)
    # inicio: si la curva baja hacia la línea falta material (préstamo); si sube, sobra (desperdicio)
    if d_ini > 0:
        prestamo += d_ini
    elif d_ini < 0:
        desperdicio += -d_ini
    if d_fin > 0:
        desperdicio += d_fin
    elif d_fin < 0:
        prestamo += -d_fin
    avisos = [] if cruces else ["La línea compensadora no corta la curva masa: no hay tramos que se compensen."]
    return Compensacion(c, cruces, lazos, prestamo, desperdicio, acarreo_libre, avisos)


def _interp(pts, s):
    for (s0, o0), (s1, o1) in zip(pts, pts[1:]):
        if s0 <= s <= s1:
            return o0 if s1 == s0 else o0 + (s - s0) / (s1 - s0) * (o1 - o0)
    return pts[-1][1]


def _sub(pts, a, b):
    return [(a, _interp(pts, a))] + [(s, o) for s, o in pts if a < s < b] + [(b, _interp(pts, b))]


def _integral_abs(pts, c):
    total = 0.0
    for (s0, o0), (s1, o1) in zip(pts, pts[1:]):
        d0, d1, L = o0 - c, o1 - c, s1 - s0
        if d0 * d1 >= 0:
            total += abs(d0 + d1) / 2 * L
        else:
            t = d0 / (d0 - d1)
            total += abs(d0) * t * L / 2 + abs(d1) * (1 - t) * L / 2
    return total


def dibujar_curva_masa(estaciones: Sequence[float], ordenadas: Sequence[float], comp: Compensacion, origen: tuple[float, float],
                       escala: Escala | None = None, m3_por_mm: float | None = None, estilo: str = "CIVIL") -> Dibujo:
    """La curva masa con su línea compensadora, puntos de compensación, lazos rotulados y un resumen, desde la esquina inferior
    izquierda `origen`. Horizontal a la escala del plano; vertical, `m3_por_mm` m³ por mm de papel (automático: unos 10 cm de alto)."""
    esc = escala or Escala()
    pts = _curva(estaciones, ordenadas)
    f = float(esc.denominador) / 1000.0
    h = esc.altura("cuadro_dato")
    os_ = [o for _, o in pts] + [comp.linea]
    omin, omax = min(os_), max(os_)
    rango = max(omax - omin, 1.0)
    if not m3_por_mm:
        m3_por_mm = rango / 100.0
    k = f / m3_por_mm                                   # unidades de dibujo por m³
    x0, y0 = origen
    X = lambda s: x0 + (s - pts[0][0])
    Y = lambda o: y0 + 4 * h + (o - omin) * k
    d = Dibujo()
    for cp in CAPAS_MASA.values():
        d.capa(cp)
    d.estilo(EstiloTexto(estilo, "romans.shx", 0.85))
    cr, cl, ct, ctx = (CAPAS_MASA[n].nombre for n in ("curva", "linea", "reticula", "textos"))
    ancho = pts[-1][0] - pts[0][0]
    alto = (omax - omin) * k + 8 * h
    d.agregar(Polilinea(ct, vertices=[(x0, y0), (x0 + ancho, y0), (x0 + ancho, y0 + alto), (x0, y0 + alto)], cerrada=True))
    d.agregar(Polilinea(cr, vertices=[(X(s), Y(o)) for s, o in pts], datos={"TVCIVIL": [(1000, "curva_masa")]}))
    d.agregar(Linea(cl, inicio=(X(pts[0][0]), Y(comp.linea), 0.0), fin=(X(pts[-1][0]), Y(comp.linea), 0.0)))
    for s in comp.puntos:
        d.agregar(Linea(ct, inicio=(X(s), Y(comp.linea) - h, 0.0), fin=(X(s), Y(comp.linea) + h, 0.0)))
        d.agregar(Texto(ctx, valor=formato_estacion(s), posicion=(X(s), Y(comp.linea) - 1.5 * h), altura=h * 0.8, rotacion=90.0, estilo=estilo,
                        alineacion_h="derecha", alineacion_v="medio"))
    for lz in comp.lazos:
        sm = (lz.desde + lz.hasta) / 2
        o = _interp(pts, sm)
        texto = f"{formato_miles(lz.volumen, 0, ',')} m³ · {lz.distancia_media:.0f} m · SA {formato_miles(lz.sobreacarreo, 1, ',')} m³-hm"
        d.agregar(Texto(ctx, valor=texto, posicion=(X(sm), (Y(o) + Y(comp.linea)) / 2), altura=h * 0.8, estilo=estilo, alineacion_h="centro", alineacion_v="medio"))
    paso = max(1, int(round(ancho / 100.0 / 5)) * 100) if ancho > 500 else 100 if ancho > 100 else 20
    s = (int(pts[0][0] // paso) + 1) * paso
    while s < pts[-1][0]:
        d.agregar(Linea(ct, inicio=(X(s), y0, 0.0), fin=(X(s), y0 + h * 0.6, 0.0)))
        d.agregar(Texto(ctx, valor=formato_estacion(s), posicion=(X(s), y0 + h), altura=h * 0.7, estilo=estilo, alineacion_h="centro", alineacion_v="abajo"))
        s += paso
    resumen = [f"CURVA MASA {formato_estacion(pts[0][0])} A {formato_estacion(pts[-1][0])}",
               f"LÍNEA COMPENSADORA: ORDENADA {formato_miles(comp.linea, 0, ',')} m³ · ACARREO LIBRE {comp.acarreo_libre:g} m",
               f"COMPENSADO {formato_miles(comp.compensado, 0, ',')} m³ · SOBREACARREO {formato_miles(comp.sobreacarreo, 1, ',')} m³-hm",
               f"PRÉSTAMO {formato_miles(comp.prestamo, 0, ',')} m³ · DESPERDICIO {formato_miles(comp.desperdicio, 0, ',')} m³"]
    for i, t in enumerate(resumen):
        d.agregar(Texto(ctx, valor=t, posicion=(x0, y0 + alto + (len(resumen) - i) * 1.8 * h), altura=h * (1.2 if i == 0 else 1.0), estilo=estilo))
    return d
