"""Lo común a las hojas de perfil y de secciones: escalas H y V, pasos de retícula y la marca del bloque.

**Escalas.** El plano se imprime a su escala 1:E. Un perfil o una hoja de secciones a escala horizontal 1:Eh y vertical 1:Ev se dibuja con
factores E/Eh y E/Ev sobre las medidas reales, así al imprimir el plano a 1:E el perfil sale a sus escalas y los textos, a sus mm de papel.
Con Eh = E (lo de siempre) un metro de cadenamiento es una unidad del dibujo.

**Bloque.** Todo lo que dibuja una orden de perfil o de secciones lleva en su XDATA `TVCIVIL` el tipo y el identificador del bloque
(`[1000 tipo, 1000 "bloque:<id>", …]`; el id es el handle del eje en el plano). Volver a dibujar con otras opciones encuentra así lo anterior
y lo reemplaza. El título guarda además las opciones (JSON en cadenas de 250 caracteres) para volver a abrir la hoja con ellas.
"""

from __future__ import annotations

import json
import math

from .entidades import Dibujo

__all__ = ["factor", "paso_bonito", "pasos_en", "marcar_bloque", "opciones_xdata", "leer_opciones_xdata", "PASOS"]

#: Pasos de retícula que se eligen solos (m): 1, 2, 2.5 y 5 por potencias de diez.
PASOS = (1.0, 2.0, 2.5, 5.0)


def factor(plano: float, escala: float | None) -> float:
    """Factor del dibujo para una escala 1:`escala` en un plano 1:`plano` (None = la del plano)."""
    if escala is None:
        return 1.0
    if not escala > 0:
        raise ValueError("La escala debe ser positiva.")
    return float(plano) / float(escala)


def paso_bonito(rango: float, divisiones: float, minimo: float = 0.0) -> float:
    """El paso redondo (1, 2, 2.5 o 5 × 10ⁿ m) más chico que deja a lo más `divisiones` en `rango` y no baja de `minimo`."""
    bruto = max(rango / max(divisiones, 1.0), minimo, 1e-6)
    base = 10.0 ** math.floor(math.log10(bruto))
    for f in PASOS + (10.0,):
        if f * base >= bruto * (1 - 1e-9):
            return f * base
    return 10.0 * base


def pasos_en(a: float, b: float, paso: float) -> list[float]:
    """Múltiplos de `paso` en [a, b] (con tolerancia de una millonésima del paso)."""
    k0 = math.ceil(a / paso - 1e-6)
    k1 = math.floor(b / paso + 1e-6)
    return [round(k * paso, 9) for k in range(k0, k1 + 1)]


def decimales_de(paso: float, minimo: int = 0) -> int:
    """Decimales para rotular múltiplos de `paso` (2.5 → 1, 0.25 → 2)."""
    for d in range(0, 4):
        if abs(paso * 10 ** d - round(paso * 10 ** d)) < 1e-6:
            return max(d, minimo)
    return max(3, minimo)


def marcar_bloque(dib: Dibujo, tipo: str, bloque: str | None, desde: int = 0) -> None:
    """XDATA `TVCIVIL` de bloque en las entidades `desde` en adelante: conserva su tipo y agrega el id del bloque."""
    if not bloque:
        return
    marca = (1000, f"bloque:{bloque}")
    for e in dib.entidades[desde:]:
        propio = list((e.datos or {}).get("TVCIVIL") or [(1000, tipo)])
        e.datos = dict(e.datos or {})
        e.datos["TVCIVIL"] = [propio[0], marca, *propio[1:]]


def opciones_xdata(opciones: dict) -> list[tuple[int, object]]:
    texto = json.dumps(opciones, ensure_ascii=False, separators=(",", ":"))
    return [(1000, texto[i:i + 250]) for i in range(0, len(texto), 250)] or [(1000, "{}")]


def leer_opciones_xdata(valores: list) -> dict:
    """Las opciones guardadas en el título: el XDATA desde el tercer valor (tras el tipo y el bloque)."""
    try:
        partes = []
        for v in valores[2:]:
            if isinstance(v, dict):
                v = v.get("value")
            elif hasattr(v, "value"):
                v = v.value
            elif isinstance(v, (list, tuple)):
                v = v[1]
            partes.append(str(v))
        return json.loads("".join(partes))
    except (ValueError, TypeError):
        return {}
