"""Teselas de mapa servidas por la app: el navegador no pide nada a OpenStreetMap (CSP) y el servidor las guarda en disco.

Usa `dominio.teselas` (caché con vigencia, revalidación y agente identificable: política de uso de OSM) y comparte su caché con
el croquis y el reporte. Además limita las descargas NUEVAS por cuenta y por hora; leer de la caché no cuenta.
"""
from __future__ import annotations

from django.core.cache import cache

from ..dominio import location_sketch as ls
from ..dominio import teselas as dominio_teselas
from ..dominio.almacenamiento import cache_dir

#: nombre público de la capa -> nombre de la capa en el dominio
CAPAS = {"osm": ls.DEFAULT_MAP_LAYER, "topo": "Topográfico - OpenTopoMap"}
DESCARGAS_NUEVAS_POR_HORA = 1500


class SinTesela(Exception):
    """La tesela no existe o no se pudo obtener."""


class DemasiadasDescargas(SinTesela):
    """Se superó el volumen de teselas nuevas por hora de la cuenta."""


def _carpeta(capa: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in CAPAS[capa]).strip("_")


def _en_cache_vigente(capa: str, z: int, x: int, y: int) -> bool:
    import time

    ruta = cache_dir("map_tiles") / _carpeta(capa) / str(z) / str(x) / f"{y}.png"
    return ruta.is_file() and dominio_teselas._leer_meta(ruta).get("expira", 0) > time.time()


def obtener(capa: str, z: int, x: int, y: int, cuenta_id: int | None = None, **opciones) -> tuple[bytes, float]:
    """(bytes, segundos de vigencia restantes). Lanza `SinTesela`."""
    import time

    if capa not in CAPAS:
        raise SinTesela("capa desconocida")
    config = ls.MAP_LAYERS[CAPAS[capa]]
    if not 0 <= z <= config["max_zoom"] or not (0 <= x < 2**z and 0 <= y < 2**z):
        raise SinTesela("fuera de rango")
    if cuenta_id is not None and not _en_cache_vigente(capa, z, x, y):
        clave = f"tesela:{cuenta_id}:{int(time.time() // 3600)}"
        cache.add(clave, 0, 3700)
        if cache.incr(clave) > DESCARGAS_NUEVAS_POR_HORA:
            raise DemasiadasDescargas("demasiadas teselas nuevas en una hora")
    url = config["url"].format(z=z, x=x, y=y, s=("a", "b", "c")[(x + y) % 3])
    try:
        datos = dominio_teselas.obtener(_carpeta(capa), url, z, x, y, **opciones)
    except dominio_teselas.TeselaNoDisponible as exc:
        raise SinTesela(str(exc)) from exc
    ruta = cache_dir("map_tiles") / _carpeta(capa) / str(z) / str(x) / f"{y}.png"
    restante = max(0.0, dominio_teselas._leer_meta(ruta).get("expira", 0) - time.time())
    return datos, restante
