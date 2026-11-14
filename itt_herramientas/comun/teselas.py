"""Teselas de mapa servidas por la app: el navegador no pide nada a OpenStreetMap (CSP) y el servidor las guarda en disco.

Cumple la política de uso de teselas de OSM: agente identificable, caché y bajo volumen. Comparte la caché del dominio
(`map_tiles/<capa>/<z>/<x>/<y>.png`) con el croquis y el reporte, así una tesela se descarga una sola vez.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

from ..dominio import location_sketch as ls
from ..dominio.almacenamiento import agente_usuario, cache_dir

#: nombre público de la capa -> nombre de la capa en el dominio
CAPAS = {"osm": ls.DEFAULT_MAP_LAYER, "topo": "Topográfico - OpenTopoMap"}


class SinTesela(Exception):
    """La tesela no existe o no se pudo obtener."""


def _carpeta(capa: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in CAPAS[capa]).strip("_")


def obtener(capa: str, z: int, x: int, y: int, descargar=None) -> bytes:
    if capa not in CAPAS:
        raise SinTesela("capa desconocida")
    config = ls.MAP_LAYERS[CAPAS[capa]]
    if not 0 <= z <= config["max_zoom"] or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise SinTesela("fuera de rango")
    destino: Path = cache_dir("map_tiles") / _carpeta(capa) / str(z) / str(x) / f"{y}.png"
    if destino.is_file():
        return destino.read_bytes()
    url = config["url"].format(z=z, x=x, y=y, s=("a", "b", "c")[(x + y) % 3])
    try:
        if descargar is not None:
            datos = descargar(url)
        else:
            peticion = urllib.request.Request(url, headers={"User-Agent": agente_usuario("ITTHerramientas/1.0")})
            with urllib.request.urlopen(peticion, timeout=5) as respuesta:
                datos = respuesta.read(2_000_000)
    except Exception as exc:
        raise SinTesela(str(exc)) from exc
    if not datos.startswith(b"\x89PNG"):
        raise SinTesela("respuesta que no es una imagen")
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_suffix(".tmp")
    temporal.write_bytes(datos)
    temporal.replace(destino)
    return datos
