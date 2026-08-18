from __future__ import annotations

import importlib
import pkgutil
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from .app_storage import APP_DATA_ROOT

TOOL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class ToolSpec:
    """Declarative contract used by the home screen and the MSI build."""

    tool_id: str
    title: str
    description: str
    order: int
    icon_text: str
    icon_color: str
    factory_path: str | None = None
    icon_asset: str | None = None
    action_label: str = "Abrir herramienta"
    version: str = "1.0.0"
    data_category: str | None = None
    data_folder: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.factory_path)

    def validate(self) -> None:
        if not TOOL_ID_PATTERN.fullmatch(self.tool_id):
            raise ValueError(f"Identificador inválido: {self.tool_id!r}")
        if not self.title.strip() or not self.description.strip():
            raise ValueError(f"La herramienta {self.tool_id!r} necesita título y descripción.")
        if self.factory_path and ":" not in self.factory_path:
            raise ValueError(f"factory_path inválido para {self.tool_id!r}: {self.factory_path!r}")
        if self.data_folder:
            folder = Path(self.data_folder)
            if folder.is_absolute() or ".." in folder.parts:
                raise ValueError(f"data_folder debe ser relativo a Grupo ITT App: {self.data_folder!r}")

    def ensure_data_dir(self) -> Path | None:
        if not self.data_folder:
            return None
        target = APP_DATA_ROOT / self.data_folder
        target.mkdir(parents=True, exist_ok=True)
        return target

    def load_factory(self):
        if not self.factory_path:
            return None
        module_name, attribute = self.factory_path.split(":", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, attribute)
        if not callable(factory):
            raise TypeError(f"{self.factory_path} no es una clase o función invocable.")
        return factory

    def create_screen(self, parent, logo_path: Path, on_home: Callable):
        factory = self.load_factory()
        if factory is None:
            raise RuntimeError(f"{self.title} todavía no está disponible.")
        return factory(parent, logo_path, on_home)


@dataclass(frozen=True)
class RegistrySnapshot:
    tools: tuple[ToolSpec, ...]
    errors: tuple[str, ...]


@lru_cache(maxsize=1)
def discover_tools() -> RegistrySnapshot:
    """Discover lightweight manifests under photo_report_app.tools.

    Tool implementations are referenced as strings and imported only when opened.
    """
    package = importlib.import_module("photo_report_app.tools")
    tools = []
    errors = []
    seen = set()
    modules = sorted(
        pkgutil.iter_modules(package.__path__, package.__name__ + "."),
        key=lambda item: item.name.casefold(),
    )
    for module_info in modules:
        try:
            module = importlib.import_module(module_info.name)
            spec = getattr(module, "TOOL_SPEC")
            if not isinstance(spec, ToolSpec):
                raise TypeError("TOOL_SPEC debe ser una instancia de ToolSpec.")
            spec.validate()
            if spec.tool_id in seen:
                raise ValueError(f"Identificador duplicado: {spec.tool_id}")
            seen.add(spec.tool_id)
            spec.ensure_data_dir()
            tools.append(spec)
        except Exception as exc:
            errors.append(f"{module_info.name}: {exc}")
    return RegistrySnapshot(tuple(sorted(tools, key=lambda item: (item.order, item.title.casefold()))), tuple(errors))


def registered_tools() -> tuple[ToolSpec, ...]:
    return discover_tools().tools


def registry_errors() -> tuple[str, ...]:
    return discover_tools().errors


def get_tool(tool_id: str) -> ToolSpec:
    for spec in registered_tools():
        if spec.tool_id == tool_id:
            return spec
    raise KeyError(f"Herramienta no registrada: {tool_id}")
