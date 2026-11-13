"""Ajustes y carpeta de caché del dominio, sin efectos al importar.

El escritorio leía y escribía `Documentos\\Grupo ITT App\\Configuracion\\settings.json` en el momento de importar
(`SETTINGS = SettingsStore()`) y buscaba la carpeta Documentos con la API de Windows. Nada de eso existe aquí:

- `SETTINGS` es un intermediario de solo lectura. Cada solicitud o tarea activa sus ajustes con
  `ajustes_activos(...)` (por ejemplo, los del perfil corporativo de la cuenta) y el dominio los lee con
  `SETTINGS.get("clave.punteada", por_omision)`, igual que en el escritorio. Fuera de ese bloque valen los
  valores por omisión. Usa `contextvars`, así que es seguro con hilos y tareas concurrentes.
- `cache_dir()` crea la carpeta de caché cuando se usa, no al importar. La raíz se fija una vez con
  `configurar_cache()` (en Django, el ajuste `TV_MAPAS_CACHE`).
"""
from __future__ import annotations

import contextlib
import contextvars
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterator

DEFAULT_SETTINGS = {
    "version": 5,
    "reports": {
        "include_map": True,
        "include_credentials": False,
        "open_pdf": True,
    },
    "geospatial": {
        "utm_zone": 13,
        "hemisphere": "N",
        "map_layer": "Calles - OpenStreetMap",
        "labels": True,
        "hatches": True,
        "clamp_to_ground": True,
    },
    "sketches": {
        "map_layer": "Calles - OpenStreetMap",
        "contour_interval": "Automática",
    },
    "triangulation": {
        "draw_preview": True,
    },
    "pointclouds": {
        "preview_limit": 90000,
    },
    "cloud": {
        "enabled": True,
        "operator_name": "",
        "drive_url": "",
        "sync_root": "",
        "parent_folder": "GRUPO ITT REPORTES",
    },
    "spelling": {
        "languages": ["es", "en"],
    },
    "branding": {
        "profile": "grupo_itt",
        "tresvizo": {
            "name": "TresVizo",
            "website": "https://www.tresvizo.com/",
            "phone": "614 100 2069",
            "signer": "ING. EDGAR TREVIZO",
            "custom_logo": "",
        },
    },
    "documents": {
        "report_texts": {},
        "quote_texts": {
            "es": {},
            "en": {},
        },
        "quote_palette": {
            "primary": "#07356F",
            "accent": "#0B7FAB",
            "pale": "#EAF3F8",
            "bar_text": "#FFFFFF",
            "ink": "#263746",
        },
    },
}


def fusionar(defaults: dict, stored: dict) -> dict:
    """Mezcla recursiva de `stored` sobre `defaults` (los valores guardados ganan)."""
    result = deepcopy(defaults)
    for key, value in stored.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = fusionar(result[key], value)
        else:
            result[key] = value
    return result


class Ajustes:
    """Ajustes en memoria con la misma lectura por clave punteada que `SettingsStore` del escritorio."""

    def __init__(self, valores: dict | None = None):
        self._data = fusionar(DEFAULT_SETTINGS, valores if isinstance(valores, dict) else {})

    def get(self, dotted_key: str, default: Any = None) -> Any:
        value: Any = self._data
        for part in dotted_key.split("."):
            if not isinstance(value, dict) or part not in value:
                return default
            value = value[part]
        return deepcopy(value)

    def como_dict(self) -> dict:
        return deepcopy(self._data)


_POR_OMISION = Ajustes()
_ACTIVOS: contextvars.ContextVar[Ajustes] = contextvars.ContextVar("itt_ajustes", default=_POR_OMISION)


class _AjustesActivos:
    """`SETTINGS`: lee los ajustes activos del contexto actual."""

    def get(self, dotted_key: str, default: Any = None) -> Any:
        return _ACTIVOS.get().get(dotted_key, default)

    def como_dict(self) -> dict:
        return _ACTIVOS.get().como_dict()


SETTINGS = _AjustesActivos()


@contextlib.contextmanager
def ajustes_activos(valores: dict | Ajustes | None) -> Iterator[Ajustes]:
    """Activa `valores` (se mezclan sobre los de fábrica) mientras dure el bloque, en este contexto."""
    ajustes = valores if isinstance(valores, Ajustes) else Ajustes(valores)
    token = _ACTIVOS.set(ajustes)
    try:
        yield ajustes
    finally:
        _ACTIVOS.reset(token)


_cache_lock = threading.Lock()
_cache_root: Path | None = None


def configurar_cache(raiz: str | Path | None) -> None:
    """Fija la raíz de la caché (mapas, Overpass, terreno). `None` vuelve a la carpeta temporal del sistema."""
    global _cache_root
    with _cache_lock:
        _cache_root = Path(raiz) if raiz else None


_agente: str | None = None


def configurar_agente(agente: str | None) -> None:
    """Agente de usuario identificable para OSM, Overpass y Terrarium (en Django, `TV_MAPAS_AGENTE`)."""
    global _agente
    _agente = (agente or "").strip() or None


def agente_usuario(por_omision: str) -> str:
    """El agente configurado o, si no hay, el que traía el escritorio para esa consulta."""
    return _agente or por_omision


def cache_root() -> Path:
    with _cache_lock:
        return _cache_root or Path(tempfile.gettempdir()) / "itt_herramientas" / "cache"


def cache_dir(section: str | None = None) -> Path:
    """Carpeta de caché (se crea al pedirla). `section` es un subdirectorio relativo."""
    target = cache_root()
    if section:
        parts = Path(section).parts
        if Path(section).is_absolute() or ".." in parts:
            raise ValueError(f"Sección de caché inválida: {section!r}")
        target = target.joinpath(*parts)
    target.mkdir(parents=True, exist_ok=True)
    return target
