"""Acceso a las fotos de campo (`Captura`): ruta del archivo y miniaturas con caché.

Una foto es de la cuenta que la subió, pero los documentos son de la empresa: la foto de un reporte o una cotización compartida se lee
de quien la subió, si esa persona hizo o editó el documento (`ruta_autorizada`).
"""
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


def editores(documento) -> set[int]:
    """Las cuentas que hicieron o guardaron alguna revisión de `documento` (autor, quien lo modificó y su historial)."""
    from ..models import VersionDocumento
    from .documentos import tipo_de

    cuentas = {documento.cuenta_id, getattr(documento, "modificado_por_id", None)}
    cuentas |= set(VersionDocumento.objects.filter(tipo=tipo_de(documento), documento_id=documento.pk).values_list("cuenta_id", flat=True))
    return {c for c in cuentas if c is not None}


def ruta_autorizada(sha256: str, documento) -> Path | None:
    """Archivo de una foto que usa un documento del equipo, solo si la subió alguien que hizo o editó ESE documento (primero su autor).

    Meter el sha256 de una foto ajena en un documento propio no la descubre: la foto tiene que ser de quien trabajó en el documento.
    Quien llama comprueba antes que el documento existe, no está en la papelera, la usa y la persona lo puede ver.
    """
    if not _SHA.match(sha256 or "") or documento is None:
        return None
    cuentas = editores(documento)
    filas = sorted(Captura.objects.filter(sha256=sha256, cuenta_id__in=cuentas), key=lambda c: (c.cuenta_id != documento.cuenta_id, c.recibida))
    for captura in filas:
        archivo = raiz() / captura.ruta
        if archivo.is_file():
            return archivo
    return None


def miniatura(cuenta_id: int | None, sha256: str, documento=None) -> Path | None:
    """JPEG de `LADO_MINIATURA` px (se genera una vez y se guarda). Con `documento`: la foto autorizada por ese documento (`ruta_autorizada`)."""
    origen = ruta_autorizada(sha256, documento) if documento is not None else ruta(cuenta_id, sha256)
    if origen is None:
        return None
    destino = raiz() / "miniaturas" / (str(cuenta_id) if documento is None else "documentos") / sha256[:2] / f"{sha256}.jpg"
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
