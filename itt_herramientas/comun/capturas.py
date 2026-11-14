"""Acceso a las fotos de campo (`Captura`) de UNA cuenta: ruta del archivo y miniaturas con caché."""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image
from tvplataforma import ajustes

from ..models import Captura

_SHA = re.compile(r"^[0-9a-f]{64}$")
LADO_MINIATURA = 240


def raiz() -> Path:
    return Path(ajustes.valor("TV_HERRAMIENTAS_ARCHIVOS"))


def ruta(cuenta_id: int, sha256: str) -> Path | None:
    """Archivo de la foto de ESTA cuenta, o None si no existe (nunca la de otra cuenta)."""
    if not _SHA.match(sha256 or ""):
        return None
    captura = Captura.objects.filter(cuenta_id=cuenta_id, sha256=sha256).first()
    if captura is None:
        return None
    archivo = raiz() / captura.ruta
    return archivo if archivo.is_file() else None


def miniatura(cuenta_id: int, sha256: str) -> Path | None:
    """JPEG de `LADO_MINIATURA` px (se genera una vez y se guarda)."""
    origen = ruta(cuenta_id, sha256)
    if origen is None:
        return None
    destino = raiz() / "miniaturas" / str(cuenta_id) / sha256[:2] / f"{sha256}.jpg"
    if not destino.is_file():
        destino.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(origen) as imagen:
            imagen.draft("RGB", (LADO_MINIATURA * 2, LADO_MINIATURA * 2))
            imagen = imagen.convert("RGB")
            imagen.thumbnail((LADO_MINIATURA, LADO_MINIATURA))
            temporal = destino.with_suffix(".tmp")
            imagen.save(temporal, "JPEG", quality=75)
            temporal.replace(destino)
    return destino
