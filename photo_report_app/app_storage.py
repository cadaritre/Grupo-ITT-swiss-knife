from __future__ import annotations

import ctypes
import filecmp
from ctypes import wintypes
import json
import shutil
import threading
from copy import deepcopy
from datetime import datetime
from pathlib import Path


APP_FOLDER_NAME = "Grupo ITT App"

CATEGORY_NAMES = {
    "quotes": "Cotizaciones",
    "reports": "Reportes Fotograficos",
    "sketches": "Croquis",
    "conversions": "Conversiones",
    "dictionaries": "Diccionarios",
    "config": "Configuracion",
    "backups": "Respaldos",
    "cache": "Cache",
}

DEFAULT_SETTINGS = {
    "version": 3,
    "reports": {
        "include_map": True,
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
        "quote_palette": {
            "primary": "#07356F",
            "accent": "#0B7FAB",
            "pale": "#EAF3F8",
            "bar_text": "#FFFFFF",
            "ink": "#263746",
        },
    },
}


def documents_folder() -> Path:
    """Return the Windows Documents known folder without assuming its display name."""
    if hasattr(ctypes, "windll"):
        try:
            class GUID(ctypes.Structure):
                _fields_ = (
                    ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
                )

            folder_id = GUID(
                0xFDD39AD0, 0x238F, 0x46AF,
                (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7),
            )
            path_pointer = ctypes.c_wchar_p()
            result = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(folder_id),
                0,
                None,
                ctypes.byref(path_pointer),
            )
            if result == 0 and path_pointer.value:
                path = Path(path_pointer.value)
                ctypes.windll.ole32.CoTaskMemFree(path_pointer)
                return path
        except Exception:
            pass
    candidates = (Path.home() / "Documents", Path.home() / "Documentos")
    return next((path for path in candidates if path.exists()), candidates[0])


APP_DATA_ROOT = documents_folder() / APP_FOLDER_NAME


def ensure_app_folders() -> Path:
    APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for folder in CATEGORY_NAMES.values():
        (APP_DATA_ROOT / folder).mkdir(parents=True, exist_ok=True)
    return APP_DATA_ROOT


def category_dir(category: str) -> Path:
    ensure_app_folders()
    try:
        folder = CATEGORY_NAMES[category]
    except KeyError as exc:
        raise ValueError(f"Categoría de almacenamiento desconocida: {category}") from exc
    return APP_DATA_ROOT / folder


def cache_dir(section: str | None = None) -> Path:
    target = category_dir("cache")
    if section:
        target /= section
    target.mkdir(parents=True, exist_ok=True)
    return target


def _merged(defaults: dict, stored: dict) -> dict:
    result = deepcopy(defaults)
    for key, value in stored.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = value
    return result


class SettingsStore:
    def __init__(self):
        self.path = category_dir("config") / "settings.json"
        self._lock = threading.RLock()
        self._data = self._load()
        if not self.path.exists():
            self.save()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return _merged(DEFAULT_SETTINGS, raw if isinstance(raw, dict) else {})
        except (OSError, ValueError):
            return deepcopy(DEFAULT_SETTINGS)

    def get(self, dotted_key: str, default=None):
        with self._lock:
            value = self._data
            for part in dotted_key.split("."):
                if not isinstance(value, dict) or part not in value:
                    return default
                value = value[part]
            return deepcopy(value)

    def set(self, dotted_key: str, value) -> None:
        with self._lock:
            target = self._data
            parts = dotted_key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
            self.save()

    def update(self, values: dict[str, object]) -> None:
        with self._lock:
            for key, value in values.items():
                target = self._data
                parts = key.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
            self.save()

    def save(self) -> Path:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
            return self.path


SETTINGS = SettingsStore()


def _same_tree(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except ValueError:
        return False


def _available_destination(folder: Path, filename: str) -> Path:
    target = folder / filename
    if not target.exists():
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return folder / f"{target.stem}_{stamp}{target.suffix}"


def preserve_artifact(path: str | Path, category: str) -> Path:
    """Keep an internal copy when the user intentionally exports elsewhere."""
    source = Path(path)
    folder = category_dir(category)
    if not source.exists() or _same_tree(source, folder):
        return source
    target = _available_destination(folder, source.name)
    shutil.copy2(source, target)
    return target


def managed_input_copy(path: str | Path, category: str, subfolder: str = "Recursos") -> Path:
    """Copy an external dependency into the managed project tree and return that stable path."""
    source = Path(path)
    folder = category_dir(category) / subfolder
    folder.mkdir(parents=True, exist_ok=True)
    if _same_tree(source, folder):
        return source
    target = folder / source.name
    if target.exists():
        try:
            if filecmp.cmp(source, target, shallow=False):
                return target
        except OSError:
            pass
        target = _available_destination(folder, source.name)
    shutil.copy2(source, target)
    return target


def backup_editable(path: str | Path, section: str) -> Path | None:
    source = Path(path)
    if not source.exists():
        return None
    folder = category_dir("backups") / section
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = folder / f"{source.stem}_{stamp}{source.suffix}"
    shutil.copy2(source, target)
    return target
