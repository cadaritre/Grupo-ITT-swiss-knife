"""Importar el dominio no toca el disco ni el sistema: lo exige la web (varios procesos, sin carpeta de usuario)."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import itt_herramientas.dominio as dominio

MODULOS = sorted(
    f"itt_herramientas.dominio.{p.stem}"
    for p in Path(dominio.__file__).parent.glob("*.py")
    if p.stem != "__init__"
)

SCRIPT = textwrap.dedent(
    """
    import importlib, os, sys
    for nombre in {modulos!r}:
        importlib.import_module(nombre)
    prohibidos = [m for m in ("tkinter", "tkinterdnd2", "open3d", "laspy", "pye57") if m in sys.modules]
    print("PROHIBIDOS", prohibidos)
    import ctypes
    print("WINDLL", hasattr(ctypes, "windll"))
    """
)


def _importar_en_proceso_limpio(tmp_path: Path) -> list[str]:
    casa = tmp_path / "casa"
    casa.mkdir()
    temporal = tmp_path / "tmp"
    temporal.mkdir()
    entorno = {**os.environ, "HOME": str(casa), "USERPROFILE": str(casa), "TMPDIR": str(temporal), "TMP": str(temporal), "TEMP": str(temporal)}
    resultado = subprocess.run(
        [sys.executable, "-I", "-c", SCRIPT.format(modulos=MODULOS)],
        capture_output=True, text=True, env=entorno, cwd=tmp_path, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert "PROHIBIDOS []" in resultado.stdout, resultado.stdout
    return sorted(str(p.relative_to(tmp_path)) for carpeta in (casa, temporal) for p in carpeta.rglob("*"))


def test_importar_todo_el_dominio_no_crea_archivos_ni_carga_tk(tmp_path):
    assert len(MODULOS) >= 15
    assert _importar_en_proceso_limpio(tmp_path) == []


def test_el_dominio_no_menciona_rutas_de_windows():
    ruta = Path(dominio.__file__).parent
    for archivo in ruta.glob("*.py"):
        texto = archivo.read_text(encoding="utf-8")
        for malo in ("C:/Windows", "C:\\Windows", "windll", "SHGetKnownFolderPath", "_MEIPASS", "os.startfile"):
            assert malo not in texto, f"{archivo.name} contiene {malo}"
