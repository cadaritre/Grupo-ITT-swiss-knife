"""Fuentes del dominio: Liberation Sans (SIL OFL 1.1) incluida en el paquete.

Reemplaza la búsqueda de Arial en la carpeta de fuentes de Windows del escritorio. Liberation Sans es métricamente compatible con
Arial, así que los textos ocupan el mismo ancho y los PDF paginan igual. Se registra al usarla, no al importar.
"""
from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

CARPETA = Path(__file__).resolve().parent / "fuentes"
REGULAR = CARPETA / "LiberationSans-Regular.ttf"
NEGRITA = CARPETA / "LiberationSans-Bold.ttf"

PDF_REGULAR = "GrupoITT-Regular"
PDF_NEGRITA = "GrupoITT-Bold"

_lock = threading.Lock()
_registradas = False


def registrar_pdf() -> tuple[str, str]:
    """Registra la fuente en ReportLab (una sola vez) y devuelve (regular, negrita)."""
    global _registradas
    with _lock:
        if not _registradas:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont

            pdfmetrics.registerFont(TTFont(PDF_REGULAR, str(REGULAR)))
            pdfmetrics.registerFont(TTFont(PDF_NEGRITA, str(NEGRITA)))
            _registradas = True
    return PDF_REGULAR, PDF_NEGRITA


@lru_cache(maxsize=64)
def fuente_pil(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Fuente de Pillow para dibujar etiquetas sobre mapas e imágenes."""
    return ImageFont.truetype(str(NEGRITA if bold else REGULAR), size)
