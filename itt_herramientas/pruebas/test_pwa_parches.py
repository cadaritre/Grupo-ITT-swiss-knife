"""Parches de M (15-11-2026) en las piezas sin conexión: lo que se puede comprobar sin navegador (lo demás, en
pruebas_navegador/test_pwa_webkit_navegador.py y en el e2e del Portal)."""
from __future__ import annotations

from pathlib import Path

from itt_herramientas.cad import editor
from itt_herramientas.offline import sw


def test_el_editor_en_desarrollo_se_guarda_como_privado_igual_que_en_produccion(tmp_path):
    for nombre in ("OpenCADStudio-0123456789abcdef_bg.wasm", "ocs-control.js"):
        assert editor.cabeceras(Path(tmp_path / nombre))["Cache-Control"].startswith("private,")
