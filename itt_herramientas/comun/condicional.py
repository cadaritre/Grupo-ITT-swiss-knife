"""Peticiones condicionales (`If-None-Match`) detrás de Caddy.

Caddy comprime las respuestas de Django (`encode zstd gzip`) y, al hacerlo, puede marcar su `ETag` con la codificación (`"abc-gzip"`) o
volverla débil (`W/"abc"`). El navegador devuelve esa forma en `If-None-Match`: aquí se compara sin el `W/` ni el sufijo, para que un 304
siga saliendo con la respuesta comprimida.
"""
from __future__ import annotations

import re

from django.utils.http import parse_etags

_SUFIJO = re.compile(r'-(?:gzip|zstd|br)"$')


def pedidas(request) -> set[str]:
    """Las ETag de `If-None-Match`, normalizadas (`"abc"`)."""
    return {_SUFIJO.sub('"', e.removeprefix("W/")) for e in parse_etags(request.headers.get("If-None-Match", ""))}


def coincide(request, etiqueta: str) -> bool:
    return bool(etiqueta) and etiqueta in pedidas(request)
