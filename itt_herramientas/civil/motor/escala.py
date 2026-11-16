"""Escala del dibujo y alturas de texto.

El dibujo está en metros (1 unidad = 1 m). Un texto que debe medir `h` milímetros en el
papel impreso a escala 1:E mide `h · E / 1000` unidades en el dibujo. También se acepta
la altura como número de plantilla de rotulación (tipo Leroy): el número es la altura en
milésimas de pulgada (80 → 0.080" = 2.032 mm).

Las alturas por omisión de cada elemento de la extensión viven en `ALTURAS_MM`; la
página puede cambiarlas por dibujo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

__all__ = [
    "ESCALAS_COMUNES",
    "ALTURAS_MM",
    "leer_escala",
    "altura_dibujo",
    "altura_papel_mm",
    "mm_de_leroy",
    "leroy_de_mm",
    "Escala",
]

#: Escalas que ofrece la lista desplegable (denominador E de 1:E).
ESCALAS_COMUNES: tuple[int, ...] = (
    50, 100, 200, 250, 500, 750, 1000, 1250, 1500, 2000, 2500, 3000, 4000, 5000,
    7500, 10000, 15000, 20000, 25000, 50000,
)

#: Altura en el papel (mm) de cada tipo de texto; valores de oficio para planos topográficos.
ALTURAS_MM: dict[str, float] = {
    "punto_numero": 1.8,
    "punto_elevacion": 1.8,
    "punto_descripcion": 1.5,
    "punto_simbolo": 1.2,  # diámetro del símbolo del punto
    "curva_etiqueta": 2.0,
    "cuadro_titulo": 3.0,
    "cuadro_encabezado": 2.2,
    "cuadro_dato": 2.0,
    "cuadro_superficie": 2.5,
    "vertice_numero": 2.5,
    "lado_rumbo_distancia": 2.0,
}

PULGADA_MM = 25.4


def leer_escala(texto: str | int | float) -> int | float:
    """Lee «1:1000», «1/500», «1000» o 1000 y devuelve el denominador E (> 0)."""
    if isinstance(texto, (int, float)):
        valor = float(texto)
    else:
        m = re.fullmatch(r"\s*(?:1\s*[:/]\s*)?([0-9]+(?:[.,][0-9]+)?)\s*", texto)
        if not m:
            raise ValueError(f"Escala no reconocida: {texto!r} (usa 1:1000).")
        valor = float(m.group(1).replace(",", "."))
    if not valor > 0:
        raise ValueError("La escala debe ser mayor que cero.")
    return int(valor) if valor.is_integer() else valor


def altura_dibujo(mm_papel: float, escala: int | float, unidades_por_metro: float = 1.0) -> float:
    """Altura en unidades de dibujo de un texto de `mm_papel` mm a escala 1:`escala`."""
    if mm_papel <= 0:
        raise ValueError("La altura debe ser mayor que cero.")
    return mm_papel * float(escala) / 1000.0 * unidades_por_metro


def altura_papel_mm(altura_unidades: float, escala: int | float, unidades_por_metro: float = 1.0) -> float:
    """Inverso de `altura_dibujo`."""
    return altura_unidades / unidades_por_metro * 1000.0 / float(escala)


def mm_de_leroy(numero: float) -> float:
    """Número de plantilla (milésimas de pulgada) → milímetros."""
    return numero / 1000.0 * PULGADA_MM


def leroy_de_mm(mm: float) -> float:
    return mm / PULGADA_MM * 1000.0


@dataclass(frozen=True)
class Escala:
    """Ajuste de escala de un dibujo: denominador y alturas en el papel por tipo de texto."""

    denominador: int | float = 1000
    alturas_mm: dict[str, float] = field(default_factory=lambda: dict(ALTURAS_MM))
    unidades_por_metro: float = 1.0

    def __post_init__(self):
        object.__setattr__(self, "denominador", leer_escala(self.denominador))
        faltan = set(ALTURAS_MM) - set(self.alturas_mm)
        if faltan:
            completas = dict(ALTURAS_MM)
            completas.update(self.alturas_mm)
            object.__setattr__(self, "alturas_mm", completas)
        for clave, valor in self.alturas_mm.items():
            if not valor > 0:
                raise ValueError(f"Altura no válida para {clave}: {valor}")

    def altura(self, tipo: str) -> float:
        """Altura en el dibujo del tipo de texto dado."""
        try:
            mm = self.alturas_mm[tipo]
        except KeyError as exc:
            raise KeyError(f"Tipo de texto desconocido: {tipo}") from exc
        return altura_dibujo(mm, self.denominador, self.unidades_por_metro)

    def con(self, **alturas_mm: float) -> "Escala":
        nuevas = dict(self.alturas_mm)
        nuevas.update(alturas_mm)
        return replace(self, alturas_mm=nuevas)

    @property
    def etiqueta(self) -> str:
        return f"1:{self.denominador:g}" if isinstance(self.denominador, float) else f"1:{self.denominador}"
