"""Archivos que se generan a pedido desde un plano (PDF, conversión DWG ↔ DXF): son de quien los pidió y se borran al día siguiente."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from ..comun.errores import ErrorUsuario
from ..models import Plano, VersionPlano
from . import nativo, servicio

_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def _version(plano_id, numero) -> tuple[Plano, VersionPlano]:
    p = Plano.objects.filter(pk=plano_id).first()
    if p is None:
        raise ErrorUsuario("Este plano ya no existe.")
    v = VersionPlano.objects.filter(plano=p, numero=numero or p.revision).first()
    if v is None or not servicio.ruta_version(v).is_file():
        raise ErrorUsuario("Esa versión del plano ya no existe.")
    return p, v


def _ruta(cuenta_id: int, token: str, extension: str) -> Path:
    return servicio.carpeta_salidas() / f"{int(cuenta_id)}-{token}.{extension}"


def _registrar_nombre(ruta: Path, nombre: str) -> None:
    ruta.with_suffix(ruta.suffix + ".nombre").write_text(nombre, encoding="utf-8")


def de_la_cuenta(cuenta_id: int, token: str) -> tuple[Path, str] | None:
    if not _TOKEN.match(token or ""):
        return None
    for archivo in servicio.carpeta_salidas().glob(f"{int(cuenta_id)}-{token}.*"):
        if archivo.suffix == ".nombre":
            continue
        marca = archivo.with_suffix(archivo.suffix + ".nombre")
        nombre = marca.read_text(encoding="utf-8") if marca.is_file() else archivo.name
        return archivo, nombre
    return None


def _sufijo(p: Plano, v: VersionPlano) -> str:
    return "" if v.numero == p.revision else f" (versión {v.numero})"


def imprimir(plano_id, numero, cuenta_id, opciones: dict) -> tuple[str, str]:
    p, v = _version(plano_id, numero)
    token = uuid.uuid4().hex
    destino = _ruta(cuenta_id, token, "pdf")
    temporal = destino.with_suffix(".tmp.pdf")
    try:
        nativo.imprimir(servicio.ruta_version(v), temporal, opciones.get("papel", nativo.PAPEL_POR_OMISION), opciones.get("orientacion", "landscape"),
                        opciones.get("escala", "ajustar"), opciones.get("area", "modelo"), bool(opciones.get("grosores", True)))
        temporal.replace(destino)
    finally:
        temporal.unlink(missing_ok=True)
    nombre = f"{p.nombre}{_sufijo(p, v)}.pdf"
    _registrar_nombre(destino, nombre)
    return token, nombre


def convertir(plano_id, numero, cuenta_id, formato: str, permitir_perdida: bool) -> tuple[str, str, int]:
    p, v = _version(plano_id, numero)
    if formato == v.formato:
        raise ErrorUsuario(f"El plano ya es {formato.upper()}.")
    token = uuid.uuid4().hex
    destino = _ruta(cuenta_id, token, formato)
    temporal = servicio.carpeta_temporal() / f"{token}.{formato}"
    try:
        r = nativo.convertir(servicio.ruta_version(v), temporal, formato, permitir_perdida)
        temporal.replace(destino)
    finally:
        temporal.unlink(missing_ok=True)
        temporal.with_suffix(".bak").unlink(missing_ok=True)
    nombre = f"{p.nombre}{_sufijo(p, v)}.{formato}"
    _registrar_nombre(destino, nombre)
    return token, nombre, r["perdidos"]
