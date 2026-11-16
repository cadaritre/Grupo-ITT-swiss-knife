"""Ángulos topográficos: azimut, rumbo en la convención mexicana y grados-minutos-segundos.

Convenciones del paquete:
- Coordenadas planas X = Este, Y = Norte (como en el dibujo CAD).
- Azimut: desde el Norte, en sentido horario, 0 ≤ az < 360.
- Rumbo: cuadrante + ángulo agudo desde el Norte o el Sur, «N 45°30'15" E».
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = [
    "azimut",
    "dms",
    "formato_dms",
    "Rumbo",
    "rumbo_de_azimut",
    "formato_rumbo",
    "leer_rumbo",
    "leer_dms",
    "angulo_interior",
]


def azimut(x1: float, y1: float, x2: float, y2: float) -> float:
    """Azimut en grados decimales del punto 1 al 2 (0 = Norte, horario)."""
    dx, dy = x2 - x1, y2 - y1
    if dx == 0.0 and dy == 0.0:
        raise ValueError("Los dos puntos coinciden: no hay dirección.")
    az = math.degrees(math.atan2(dx, dy))
    if az < 0.0:
        az += 360.0
    if az >= 360.0:
        az -= 360.0
    return az


def dms(grados: float, decimales_segundos: int = 0) -> tuple[int, int, float]:
    """Descompone un ángulo no negativo en (grados, minutos, segundos) ya redondeados.

    El redondeo se hace sobre el total de segundos, así 29°59'59.9996" con 0 decimales
    da 30°00'00" y nunca 29°59'60".
    """
    if grados < 0:
        raise ValueError("dms() recibe ángulos no negativos.")
    escala = 10 ** decimales_segundos
    total = round(grados * 3600.0 * escala)
    g, resto = divmod(total, 3600 * escala)
    m, s = divmod(resto, 60 * escala)
    return int(g), int(m), s / escala


def formato_dms(grados: float, decimales_segundos: int = 0, ancho_grados: int = 0) -> str:
    """«45°30'15"» (los minutos y segundos siempre con dos cifras)."""
    signo = "-" if grados < 0 else ""
    g, m, s = dms(abs(grados), decimales_segundos)
    if decimales_segundos > 0:
        seg = f"{s:0{3 + decimales_segundos}.{decimales_segundos}f}"
    else:
        seg = f"{int(s):02d}"
    return f"{signo}{g:0{ancho_grados}d}°{m:02d}'{seg}\""


@dataclass(frozen=True)
class Rumbo:
    """Rumbo de cuadrante: `ns` es «N» o «S», `ew` es «E» o «W», `angulo` en grados (0–90)."""

    ns: str
    angulo: float
    ew: str

    @property
    def azimut(self) -> float:
        a = self.angulo
        if self.ns == "N" and self.ew == "E":
            az = a
        elif self.ns == "S" and self.ew == "E":
            az = 180.0 - a
        elif self.ns == "S":
            az = 180.0 + a
        else:
            az = 360.0 - a
        return az % 360.0


def rumbo_de_azimut(az: float) -> Rumbo:
    """Convierte un azimut a rumbo de cuadrante (sin redondear)."""
    az = az % 360.0
    if az <= 90.0:
        return Rumbo("N", az, "E")
    if az <= 180.0:
        return Rumbo("S", 180.0 - az, "E")
    if az <= 270.0:
        return Rumbo("S", az - 180.0, "W")
    return Rumbo("N", 360.0 - az, "W")


def formato_rumbo(az: float, decimales_segundos: int = 0, oeste: str = "W", ancho_grados: int = 0) -> str:
    """Rumbo en la convención mexicana: «N 45°30'15" E».

    El cuadrante se decide con el ángulo ya redondeado, para que un azimut de 89°59'59.8"
    con 0 decimales salga «N 90°00'00" E» y no «N 89°59'60" E». Un rumbo que redondea a 0°
    toma el cuadrante Este del lado Norte o Sur que corresponde.
    `oeste` permite «O» en vez de «W».
    """
    r = rumbo_de_azimut(az)
    texto = formato_dms(r.angulo, decimales_segundos, ancho_grados)
    ew = oeste if r.ew == "W" else "E"
    return f"{r.ns} {texto} {ew}"


_RE_RUMBO = re.compile(
    r"^\s*([NS])\s*(\d+(?:\.\d+)?)\s*(?:[°ºd:\s-]\s*(\d+(?:\.\d+)?)\s*(?:['’m:\s-]\s*(\d+(?:\.\d+)?)\s*(?:\"|”|''|s)?)?)?\s*([EWO])\s*$",
    re.IGNORECASE,
)


def leer_dms(g: str, m: str | None = None, s: str | None = None) -> float:
    gv, mv, sv = float(g), float(m or 0), float(s or 0)
    if mv >= 60 or sv >= 60:
        raise ValueError("Minutos y segundos deben ser menores que 60.")
    return gv + mv / 60.0 + sv / 3600.0


def leer_rumbo(texto: str) -> float:
    """Lee «N 45°30'15" E», «S45-30-15W», «N 45 30 15 O» y devuelve el azimut en grados."""
    m = _RE_RUMBO.match(texto)
    if not m:
        raise ValueError(f"Rumbo no reconocido: {texto!r}")
    ns, g, mi, se, ew = m.groups()
    ang = leer_dms(g, mi, se)
    if ang > 90.0:
        raise ValueError(f"Un rumbo no pasa de 90°: {texto!r}")
    ew = ew.upper()
    return Rumbo(ns.upper(), ang, "W" if ew in ("W", "O") else "E").azimut


def angulo_interior(previo: tuple[float, float], vertice: tuple[float, float], siguiente: tuple[float, float], horario: bool) -> float:
    """Ángulo interior en el vértice de un polígono recorrido en el sentido dado (grados, 0–360).

    Para un polígono recorrido en sentido horario el interior queda a la derecha.
    """
    az_atras = azimut(vertice[0], vertice[1], previo[0], previo[1])
    az_adelante = azimut(vertice[0], vertice[1], siguiente[0], siguiente[1])
    if horario:
        ang = az_atras - az_adelante
    else:
        ang = az_adelante - az_atras
    return ang % 360.0
