"""Superficies: m² y el formato agrario hectáreas-áreas-centiáreas (ha-a-ca).

1 ha = 100 a = 10 000 m²; 1 a = 100 ca; 1 ca = 1 m².
"""

from __future__ import annotations

__all__ = ["ha_a_ca", "formato_ha_a_ca", "formato_m2", "formato_miles"]


def ha_a_ca(m2: float, decimales_ca: int = 2) -> tuple[int, int, float]:
    """Descompone una superficie en (ha, a, ca), redondeando las centiáreas sin dejar «100 ca»."""
    if m2 < 0:
        raise ValueError("La superficie no puede ser negativa.")
    escala = 10 ** decimales_ca
    total = round(m2 * escala)
    ha, resto = divmod(total, 10_000 * escala)
    a, ca = divmod(resto, 100 * escala)
    return int(ha), int(a), ca / escala


def formato_ha_a_ca(m2: float, decimales_ca: int = 2, unidad: str = "ha") -> str:
    """«1-23-45.67 ha»: hectáreas, áreas (2 cifras) y centiáreas (2 cifras enteras)."""
    ha, a, ca = ha_a_ca(m2, decimales_ca)
    if decimales_ca > 0:
        ca_txt = f"{ca:0{3 + decimales_ca}.{decimales_ca}f}"
    else:
        ca_txt = f"{int(ca):02d}"
    sufijo = f" {unidad}" if unidad else ""
    return f"{ha}-{a:02d}-{ca_txt}{sufijo}"


def formato_miles(valor: float, decimales: int, separador_miles: str = ",") -> str:
    """Número con separador de miles opcional (cadena vacía = sin separador)."""
    return f"{valor:,.{decimales}f}".replace(",", separador_miles)


def formato_m2(m2: float, decimales: int = 2, separador_miles: str = ",") -> str:
    """«12,345.67 m²»."""
    return f"{formato_miles(m2, decimales, separador_miles)} m²"
