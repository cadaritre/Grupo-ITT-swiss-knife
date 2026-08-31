from __future__ import annotations

import filecmp
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from .app_storage import SETTINGS, category_dir


CLOUD_PARENT_FOLDER = "GRUPO ITT REPORTES"
CLOUD_CATEGORY_FOLDERS = {
    "quotes": "Cotizaciones",
    "reports": "Reportes fotograficos",
    "conversions": "Poligonos KML",
}
_ALLOWED_SUFFIXES = {
    "quotes": {".pdf", ".json"},
    "reports": {".pdf"},
    "conversions": {".kml", ".kmz"},
}
_INVALID_WINDOWS_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_SYNC_LOCK = threading.RLock()


def sanitize_folder_name(value: str, fallback: str = "Usuario sin nombre") -> str:
    cleaned = _INVALID_WINDOWS_NAME.sub(" ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    cleaned = cleaned or fallback
    if cleaned.upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"{cleaned} usuario"
    return cleaned


def installer_operator_name() -> str:
    """Read the employee name captured by the MSI, if this is an installed copy."""
    try:
        import winreg
    except ImportError:
        return ""
    key_path = r"Software\Grupo ITT\Herramientas Grupo ITT"
    access_flags = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in access_flags:
            try:
                with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | view) as key:
                    value, _value_type = winreg.QueryValueEx(key, "OperatorName")
                if str(value).strip():
                    return str(value).strip()
            except OSError:
                continue
    return ""


def effective_operator_name() -> str:
    return str(SETTINGS.get("cloud.operator_name", "") or installer_operator_name()).strip()


def cloud_configuration() -> dict[str, object]:
    return {
        "enabled": bool(SETTINGS.get("cloud.enabled", True)),
        "operator_name": effective_operator_name(),
        "drive_url": str(SETTINGS.get("cloud.drive_url", "") or "").strip(),
        "sync_root": str(SETTINGS.get("cloud.sync_root", "") or "").strip(),
        "parent_folder": CLOUD_PARENT_FOLDER,
    }


def eligible_export(path: str | Path, category: str) -> bool:
    return category in _ALLOWED_SUFFIXES and Path(path).suffix.lower() in _ALLOWED_SUFFIXES[category]


def cloud_employee_root(operator_name: str | None = None, sync_root: str | Path | None = None) -> Path | None:
    employee = str(operator_name if operator_name is not None else effective_operator_name()).strip()
    configured_root = str(sync_root if sync_root is not None else SETTINGS.get("cloud.sync_root", "") or "").strip()
    if not employee or not configured_root:
        return None
    return Path(configured_root).expanduser() / CLOUD_PARENT_FOLDER / sanitize_folder_name(employee)


def ensure_cloud_folders(operator_name: str | None = None, sync_root: str | Path | None = None) -> Path:
    root = cloud_employee_root(operator_name, sync_root)
    if root is None:
        raise ValueError("Falta el nombre del usuario o la carpeta local sincronizada con Drive.")
    configured_root = Path(str(sync_root if sync_root is not None else SETTINGS.get("cloud.sync_root", ""))).expanduser()
    if not configured_root.exists() or not configured_root.is_dir():
        raise FileNotFoundError(f"La carpeta sincronizada no está disponible: {configured_root}")
    for folder in CLOUD_CATEGORY_FOLDERS.values():
        (root / folder).mkdir(parents=True, exist_ok=True)
    return root


def _same_file(first: Path, second: Path) -> bool:
    try:
        return first.stat().st_size == second.stat().st_size and filecmp.cmp(first, second, shallow=False)
    except OSError:
        return False


def _collision_safe_destination(folder: Path, source: Path) -> Path:
    target = folder / source.name
    if not target.exists() or _same_file(source, target):
        return target
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = folder / f"{source.stem}_{stamp}{source.suffix}"
    counter = 2
    while candidate.exists() and not _same_file(source, candidate):
        candidate = folder / f"{source.stem}_{stamp}_{counter}{source.suffix}"
        counter += 1
    return candidate


def _copy_once(source: Path, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = _collision_safe_destination(folder, source)
    if not target.exists():
        shutil.copy2(source, target)
    return target


def _queue_export(source: Path, category: str) -> Path:
    queue_folder = category_dir("cloud_queue") / CLOUD_CATEGORY_FOLDERS[category]
    return _copy_once(source, queue_folder)


def sync_export(path: str | Path, category: str) -> Path | None:
    """Copy an eligible export to the local Drive mirror, or queue it while offline."""
    with _SYNC_LOCK:
        source = Path(path)
        if not source.exists() or not eligible_export(source, category):
            return None
        if not bool(SETTINGS.get("cloud.enabled", True)):
            return None
        employee_root = cloud_employee_root()
        if employee_root is None:
            # An unconfigured installation keeps its normal local backup only.
            return None
        try:
            ensure_cloud_folders()
            target = _copy_once(source, employee_root / CLOUD_CATEGORY_FOLDERS[category])
            flush_cloud_queue()
            return target
        except (OSError, ValueError):
            return _queue_export(source, category)


def flush_cloud_queue() -> int:
    """Move pending copies into the Drive mirror. Returns the number delivered."""
    with _SYNC_LOCK:
        if not bool(SETTINGS.get("cloud.enabled", True)):
            return 0
        employee_root = cloud_employee_root()
        if employee_root is None:
            return 0
        try:
            ensure_cloud_folders()
        except (OSError, ValueError):
            return 0
        delivered = 0
        queue_root = category_dir("cloud_queue")
        for category, folder_name in CLOUD_CATEGORY_FOLDERS.items():
            pending_folder = queue_root / folder_name
            if not pending_folder.exists():
                continue
            for pending in tuple(pending_folder.iterdir()):
                if not pending.is_file() or not eligible_export(pending, category):
                    continue
                try:
                    _copy_once(pending, employee_root / folder_name)
                    pending.unlink()
                    delivered += 1
                except OSError:
                    continue
        return delivered
