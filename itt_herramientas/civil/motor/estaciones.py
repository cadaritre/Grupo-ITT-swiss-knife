"""Estaciones de secciones y de la guitarra del perfil: cada N m, en un tramo, más las adicionales y las críticas.

Como en CivilCAD, la separación es libre (5, 10, 20 o cualquier valor) y las estaciones del intervalo son cadenamientos cerrados (múltiplos
del intervalo desde 0+000). A ellas se suman:
- los extremos del tramo pedido (y del eje, si caen dentro);
- las adicionales que escribe o señala la persona;
- las críticas del eje en planta: PC y PT de las curvas circulares, TE, EC, CE y ET de las espirales, y el PI (el de una curva, por su
  cadenamiento TE + STe; el de un quiebre sin curva, en el vértice);
- las críticas de la rasante: PCV, PIV y PTV de cada curva vertical y los PIV sin curva (cambios de pendiente).

Dos estaciones a menos de 1 mm son la misma: se queda el cadenamiento exacto del punto crítico y se juntan sus tipos.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .eje import Eje, EjePI, TramoEspiral, formato_estacion

__all__ = ["Estacion", "puntos_criticos", "lista_estaciones", "MAXIMO_ESTACIONES", "TOLERANCIA"]

#: Más estaciones que esto es casi siempre un error de captura (intervalo de 0.01 m en un eje de 10 km).
MAXIMO_ESTACIONES = 5000
TOLERANCIA = 0.001  # m

#: Orden en que se escriben los tipos de una estación («PT PC», «PCV»…).
ORDEN_TIPOS = ("inicio", "fin", "PI", "PC", "PT", "TE", "EC", "CE", "ET", "PCV", "PIV", "PTV", "adicional", "intervalo")
CRITICOS = {"PI", "PC", "PT", "TE", "EC", "CE", "ET", "PCV", "PIV", "PTV"}


@dataclass
class Estacion:
    estacion: float
    tipos: tuple[str, ...] = ()

    @property
    def rotulo(self) -> str:
        """Los tipos críticos que se escriben junto al cadenamiento («PC», «PIV PCV»…); vacío si es una estación del intervalo."""
        return " ".join(t for t in self.tipos if t in CRITICOS)


def _recta(t) -> bool:
    return t.centro is None and not isinstance(t, TramoEspiral)


def _rumbo(t) -> float:
    return math.atan2(t.b[1] - t.a[1], t.b[0] - t.a[0])


def puntos_criticos(eje: Eje, rasante=None, curvas: bool = True, verticales: bool = True) -> list[tuple[float, str]]:
    """[(estación, tipo)] de los puntos críticos del eje y de la rasante, ordenados."""
    salida: list[tuple[float, str]] = []
    if curvas:
        tramos = eje.tramos
        for a, b in zip(tramos, tramos[1:]):                      # quiebres sin curva: el PI está en el vértice
            if _recta(a) and _recta(b) and abs(math.remainder(_rumbo(b) - _rumbo(a), 2 * math.pi)) > 1e-9:
                salida.append((eje.inicio + b.inicio, "PI"))
        if isinstance(eje, EjePI):
            for c, el in zip(eje.curvas, [e for e in eje.elementos if e]):
                if c.le > 0:
                    salida += [(c.te, "TE"), (c.ec, "EC"), (c.ce, "CE"), (c.et, "ET")]
                else:
                    salida += [(c.te, "PC"), (c.et, "PT")]
                salida.append((c.te + el["ste"], "PI"))
        else:
            for t in tramos:
                if t.centro is not None:
                    salida += [(eje.inicio + t.inicio, "PC"), (eje.inicio + t.inicio + t.largo, "PT")]
    if verticales and rasante is not None:
        pivs = rasante.pivs
        for k, p in enumerate(pivs):
            if 0 < k < len(pivs) - 1:
                salida.append((p.estacion, "PIV"))
                if p.curva:
                    salida += [(p.estacion - p.curva / 2, "PCV"), (p.estacion + p.curva / 2, "PTV")]
    return sorted(salida)


def lista_estaciones(
    eje: Eje,
    intervalo: float,
    desde: float | None = None,
    hasta: float | None = None,
    adicionales: Iterable[float] = (),
    curvas: bool = True,
    rasante=None,
    verticales: bool = True,
    avisos: list[str] | None = None,
) -> list[Estacion]:
    """Estaciones de `desde` a `hasta` (por omisión todo el eje), cada `intervalo` m, con las adicionales y las críticas."""
    if not intervalo or not intervalo > 0:
        raise ValueError("La separación de las estaciones debe ser mayor que cero.")
    e0 = eje.inicio if desde is None else float(desde)
    e1 = eje.fin if hasta is None else float(hasta)
    if e1 < e0:
        e0, e1 = e1, e0
    if e1 < eje.inicio - TOLERANCIA or e0 > eje.fin + TOLERANCIA:
        raise ValueError(f"El tramo pedido queda fuera del eje ({formato_estacion(eje.inicio)} a {formato_estacion(eje.fin)}).")
    e0, e1 = max(e0, eje.inicio), min(e1, eje.fin)
    if e1 - e0 < TOLERANCIA:
        raise ValueError("El tramo pedido no tiene longitud.")
    cuantas = (e1 - e0) / intervalo
    if cuantas > MAXIMO_ESTACIONES:
        raise ValueError(f"Con estaciones cada {intervalo:g} m salen {math.ceil(cuantas):,} estaciones; el máximo es {MAXIMO_ESTACIONES:,}. Aumenta la separación o acorta el tramo.")
    marcas: list[tuple[float, str, int]] = []           # (estación, tipo, prioridad: 0 exacta de un punto, 1 del intervalo)
    marcas.append((e0, "inicio", 0))
    marcas.append((e1, "fin", 0))
    k = math.ceil(e0 / intervalo - 1e-9)
    while k * intervalo < e1 - TOLERANCIA / 2:
        x = k * intervalo
        if x > e0 + TOLERANCIA / 2:
            marcas.append((x, "intervalo", 1))
        k += 1
    fuera = 0
    for x in adicionales:
        x = float(x)
        if e0 - TOLERANCIA <= x <= e1 + TOLERANCIA:
            marcas.append((min(max(x, e0), e1), "adicional", 0))
        else:
            fuera += 1
    if fuera and avisos is not None:
        avisos.append(f"{fuera} estación{'es' if fuera > 1 else ''} adicional{'es' if fuera > 1 else ''} queda{'n' if fuera > 1 else ''} fuera del tramo y no se usa{'n' if fuera > 1 else ''}.")
    for x, tipo in puntos_criticos(eje, rasante, curvas, verticales):
        if e0 - TOLERANCIA <= x <= e1 + TOLERANCIA:
            marcas.append((min(max(x, e0), e1), tipo, 0))
    marcas.sort(key=lambda m: (m[0], m[2]))
    grupos: list[list[tuple[float, str, int]]] = []
    for m in marcas:
        if grupos and m[0] - grupos[-1][0][0] < TOLERANCIA:
            grupos[-1].append(m)
        else:
            grupos.append([m])
    salida = []
    for g in grupos:
        exactas = [m for m in g if m[2] == 0 and m[1] not in ("inicio", "fin")] or [m for m in g if m[2] == 0] or g
        estacion = round(exactas[0][0], 6)
        tipos = sorted({m[1] for m in g}, key=ORDEN_TIPOS.index)
        salida.append(Estacion(estacion, tuple(tipos)))
    if len(salida) > MAXIMO_ESTACIONES:
        raise ValueError(f"Salen {len(salida):,} estaciones; el máximo es {MAXIMO_ESTACIONES:,}.")
    return salida


def valores(lista: Sequence[Estacion]) -> list[float]:
    return [e.estacion for e in lista]


def estacion_de_punto(eje, x: float, y: float) -> float:
    """Estación del punto del eje más cercano a (x, y): proyecta sobre el eje con los arcos partidos cada 0.2 m."""
    pts = eje.densificar(0.2)
    mejor, est = math.inf, pts[0][2]
    for (ax, ay, ae), (bx, by, be) in zip(pts, pts[1:]):
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 <= 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / l2))
        d = math.hypot(x - (ax + t * dx), y - (ay + t * dy))
        if d < mejor:
            mejor, est = d, ae + t * (be - ae)
    return est
