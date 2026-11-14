"""Descarga de teselas de mapa con caché en disco y respeto a la política de uso de OpenStreetMap.

Reglas (https://operations.osmfoundation.org/policies/tiles/):
  - agente de usuario identificable y con una URL de contacto (`almacenamiento.configurar_agente`);
  - se respetan `Cache-Control: max-age` y `Expires`; sin ellos, 7 días. Pasado ese plazo se revalida con `If-None-Match` /
    `If-Modified-Since` (un 304 no baja la imagen otra vez);
  - sin precarga masiva: solo se pide la tesela que alguien está viendo o que un PDF necesita, nunca rangos de zoom;
  - si el servidor no responde y hay una copia vieja, se usa la copia vieja (mejor un mapa algo viejo que ninguno).
"""
from __future__ import annotations

import email.utils
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .almacenamiento import agente_usuario, cache_dir

VIGENCIA_POR_OMISION = 7 * 24 * 3600
VIGENCIA_MAXIMA = 30 * 24 * 3600
_MAX_AGE = re.compile(r"max-age=(\d+)")


class TeselaNoDisponible(Exception):
    """No se pudo obtener la tesela (sin red, sin copia en caché)."""


def vigencia(cabeceras, ahora: float | None = None) -> float:
    """Momento (epoch) hasta el que la tesela es válida según las cabeceras de la respuesta."""
    ahora = time.time() if ahora is None else ahora
    control = (cabeceras.get("Cache-Control") or "").lower()
    if "no-store" in control or "no-cache" in control:
        return ahora                                           # se revalida siempre; el servidor decide (304 barato)
    m = _MAX_AGE.search(control)
    if m:
        return ahora + min(int(m.group(1)), VIGENCIA_MAXIMA)
    expira = cabeceras.get("Expires")
    if expira:
        try:
            return min(email.utils.parsedate_to_datetime(expira).timestamp(), ahora + VIGENCIA_MAXIMA)
        except (TypeError, ValueError):
            pass
    return ahora + VIGENCIA_POR_OMISION


def _leer_meta(ruta: Path) -> dict:
    try:
        return json.loads(ruta.with_suffix(".meta").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _escribir(ruta: Path, datos: bytes | None, meta: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    if datos is not None:
        temporal = ruta.with_suffix(".tmp")
        temporal.write_bytes(datos)
        os.replace(temporal, ruta)
    ruta.with_suffix(".meta").write_text(json.dumps(meta), encoding="utf-8")


def obtener(capa_clave: str, url: str, z: int, x: int, y: int, timeout: float = 5, abrir=None) -> bytes:
    """Bytes PNG de la tesela. `capa_clave`: carpeta de la caché (la misma para el croquis, el reporte y el navegador)."""
    abrir = abrir or urllib.request.urlopen
    ruta = cache_dir("map_tiles") / capa_clave / str(z) / str(x) / f"{y}.png"
    meta = _leer_meta(ruta) if ruta.is_file() else {}
    ahora = time.time()
    if ruta.is_file() and meta.get("expira", 0) > ahora:
        return ruta.read_bytes()
    cabeceras = {"User-Agent": agente_usuario("ITTHerramientas/1.0")}
    if ruta.is_file():                                          # vencida: se revalida, no se baja de nuevo
        if meta.get("etag"):
            cabeceras["If-None-Match"] = meta["etag"]
        if meta.get("modificada"):
            cabeceras["If-Modified-Since"] = meta["modificada"]
    try:
        with abrir(urllib.request.Request(url, headers=cabeceras), timeout=timeout) as r:
            datos = r.read(2_000_000)
            recibidas = r.headers
    except urllib.error.HTTPError as exc:
        if exc.code == 304 and ruta.is_file():
            _escribir(ruta, None, {**meta, "expira": vigencia(exc.headers, ahora)})
            return ruta.read_bytes()
        if ruta.is_file():
            return ruta.read_bytes()
        raise TeselaNoDisponible(f"HTTP {exc.code}") from exc
    except Exception as exc:
        if ruta.is_file():
            return ruta.read_bytes()
        raise TeselaNoDisponible(str(exc)) from exc
    if not datos.startswith(b"\x89PNG"):
        if ruta.is_file():
            return ruta.read_bytes()
        raise TeselaNoDisponible("la respuesta no es una imagen")
    _escribir(ruta, datos, {"expira": vigencia(recibidas, ahora), "etag": recibidas.get("ETag", ""), "modificada": recibidas.get("Last-Modified", ""), "bajada": ahora})
    return datos
