from __future__ import annotations

from copy import deepcopy

from .app_storage import SETTINGS


REPORT_TEXT_DEFAULTS = {
    "header_title": "Reporte fotográfico",
    "document_tag": "REPORTE",
    "location_sketch": "CROQUIS DE UBICACIÓN",
    "map_number_note": "Los números del croquis corresponden al orden de las fotografías.",
    "map_no_gps_note": "Las fotografías seleccionadas no contienen ubicación GPS legible.",
    "photo_label": "FOTOGRAFÍA",
    "captured_label": "Capturada",
    "gps_label": "GPS",
    "page_label": "Página",
    "cover_date_template": "{day} de {month} de {year}",
    "months": "enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre",
}


def report_texts() -> dict[str, str]:
    values = deepcopy(REPORT_TEXT_DEFAULTS)
    stored = SETTINGS.get("documents.report_texts", {})
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in values and isinstance(value, str):
                values[key] = value
    return values


def report_months(texts: dict[str, str] | None = None) -> tuple[str, ...]:
    values = texts or report_texts()
    months = tuple(part.strip() for part in values["months"].split("|") if part.strip())
    if len(months) != 12:
        months = tuple(REPORT_TEXT_DEFAULTS["months"].split("|"))
    return months


def fill_template(template: str, **values: object) -> str:
    """Replace supported placeholders without treating other braces as errors."""
    result = str(template)
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result
