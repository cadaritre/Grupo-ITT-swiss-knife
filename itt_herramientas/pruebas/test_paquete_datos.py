"""Cada archivo de datos que lee el código viaja en la rueda: si falta en package-data, en producción no existe (dio un 500 antes)."""
from __future__ import annotations

import tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
PAQUETE = RAIZ / "itt_herramientas"
#: lo que no es dato del paquete: código, pruebas y cachés
FUERA = ("pruebas", "pruebas_navegador", "__pycache__", "migrations")


def test_los_archivos_de_datos_estan_en_package_data():
    patrones = tomllib.loads((RAIZ / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["itt_herramientas"]
    cubiertos = {p.resolve() for patron in patrones for p in PAQUETE.glob(patron) if p.is_file()}
    faltan = [str(p.relative_to(PAQUETE)) for p in PAQUETE.rglob("*")
              if p.is_file() and p.suffix not in (".py", ".pyc") and not any(x in p.relative_to(PAQUETE).parts for x in FUERA)
              and not p.name.startswith(".") and p.resolve() not in cubiertos]
    assert not faltan, f"Agrega a package-data de pyproject.toml: {faltan}"
