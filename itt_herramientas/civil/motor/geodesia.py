"""Coordenadas UTM ↔ geográficas, factor de escala y convergencia de meridianos (para cuadros RAN y UTM-GPS y la cuadrícula).

Proyección UTM sobre el elipsoide GRS80, el del marco oficial de México (ITRF2008, INEGI). Con WGS84 la diferencia del elipsoide es
de una décima de milímetro, así que da igual para coordenadas de levantamientos con GNSS.

- `factor de escala` (k): de la proyección en el punto. Distancia UTM = distancia sobre el elipsoide × k.
- `convergencia`: ángulo del norte de cuadrícula al norte geográfico, en grados; positiva al este del meridiano central (en el
  hemisferio norte). Azimut geográfico = azimut de cuadrícula + convergencia.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from .angulos import dms

__all__ = ["DatosGeo", "ZONAS_MEXICO", "datos_geo", "geo_a_utm", "formato_latitud", "formato_longitud", "zona_de_longitud", "factor_medio"]

#: Zonas UTM que cubren México (de Baja California a Quintana Roo).
ZONAS_MEXICO = (11, 12, 13, 14, 15, 16)


@dataclass(frozen=True)
class DatosGeo:
    latitud: float  # grados, + norte
    longitud: float  # grados, + este
    factor: float  # factor de escala de la proyección en el punto
    convergencia: float  # grados


def _validar(zona: int, hemisferio: str) -> None:
    if not (isinstance(zona, int) and 1 <= zona <= 60):
        raise ValueError("La zona UTM va de 1 a 60.")
    if hemisferio not in ("N", "S"):
        raise ValueError("El hemisferio es N o S.")


@lru_cache(maxsize=16)
def _proj(zona: int, hemisferio: str):
    from pyproj import Proj

    return Proj(proj="utm", zone=zona, ellps="GRS80", south=hemisferio == "S")


def datos_geo(x: float, y: float, zona: int, hemisferio: str = "N") -> DatosGeo:
    """Latitud, longitud, factor de escala y convergencia de un punto UTM."""
    _validar(zona, hemisferio)
    p = _proj(zona, hemisferio)
    lon, lat = p(float(x), float(y), inverse=True)
    if not (math.isfinite(lat) and math.isfinite(lon)):
        raise ValueError("Las coordenadas no son UTM de esa zona.")
    f = p.get_factors(lon, lat)
    # convergencia γ = Δλ·sen φ (aprox.): la de PROJ, en grados; al oeste del meridiano central es negativa en el hemisferio norte
    return DatosGeo(lat, lon, float(f.meridional_scale), float(f.meridian_convergence))


def geo_a_utm(latitud: float, longitud: float, zona: int, hemisferio: str = "N") -> tuple[float, float]:
    _validar(zona, hemisferio)
    x, y = _proj(zona, hemisferio)(float(longitud), float(latitud))
    return float(x), float(y)


def zona_de_longitud(longitud: float) -> int:
    return int((longitud + 180.0) // 6.0) % 60 + 1


def factor_medio(puntos, zona: int, hemisferio: str = "N") -> float:
    """Factor de escala medio de un polígono (promedio de sus vértices: dentro de un predio varía menos de 1e-6)."""
    ks = [datos_geo(x, y, zona, hemisferio).factor for x, y in puntos]
    return sum(ks) / len(ks)


def _formato(grados: float, positivo: str, negativo: str, decimales: int) -> str:
    g, m, s = dms(abs(grados), decimales)
    seg = f"{s:0{3 + decimales if decimales else 2}.{decimales}f}"
    return f"{g}°{m:02d}'{seg}\" {positivo if grados >= 0 else negativo}"


def formato_latitud(grados: float, decimales: int = 4) -> str:
    """«19°25'42.1234\" N»."""
    return _formato(grados, "N", "S", decimales)


def formato_longitud(grados: float, decimales: int = 4, oeste: str = "W") -> str:
    """«99°08'12.3456\" W» (u «O» para oeste)."""
    return _formato(grados, "E", oeste, decimales)
