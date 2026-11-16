"""CAD: regresiones de la auditoría J (15-11-2026) que no necesitan navegador. Las de navegador están en
pruebas_navegador/test_cad_auditoria_navegador.py."""
from __future__ import annotations

import re
from pathlib import Path

from itt_herramientas.cad import editor

from .conftest import url
from .test_cad import dibujante, editor_falso, plano  # noqa: F401 (fixtures)

ESTATICOS = Path(editor.__file__).resolve().parents[2] / "static" / "herramientas" / "cad"


def test_b13_el_menu_mas_acciones_es_una_lista_de_botones_y_los_toques_miden_44(cliente_de, dibujante, plano, editor_falso):
    """Un `role="menu"` promete flechas, Inicio y Fin y lleva formularios dentro: el menú es un grupo de botones y enlaces normales."""
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    assert 'role="menu"' not in html and 'role="menuitem"' not in html
    assert re.search(r'class="cad-menu__lista" role="group" aria-label="Más acciones"', html)
    css = (ESTATICOS / "cad.css").read_text()
    regla = re.search(r"\.cad-espacio__detalle > summary \{([^}]*)\}", css).group(1)
    assert "min-height: 44px" in regla


def _preparar_falso(tmp_path):
    import hashlib
    import importlib.util
    import zipfile

    spec = importlib.util.spec_from_file_location("preparar", Path(editor.__file__).parent / "preparar.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    zip_ruta = tmp_path / "web.zip"
    with zipfile.ZipFile(zip_ruta, "w") as z:
        z.writestr("app/index.html", "<script type=module>import init, * as b from '/app/OpenCADStudio-0123456789abcdef.js';"
                                     "await init({ module_or_path: '/app/OpenCADStudio-fedcba9876543210_bg.wasm' });</script>")
        z.writestr("app/OpenCADStudio-0123456789abcdef.js", "export default async () => ({});" + " " * 3000)
        z.writestr("app/OpenCADStudio-fedcba9876543210_bg.wasm", b"\0asm" + b"\1" * 5000)
        z.writestr("app/ocs-parse-worker.js", "x")
        z.writestr("app/worker_pkg/ocs_web_worker.js", "x")
        z.writestr("app/worker_pkg/ocs_web_worker_bg.wasm", b"\0asm")
    falso = {**prep.datos(), "web": {"archivo": "web.zip", "sha256": hashlib.sha256(zip_ruta.read_bytes()).hexdigest(), "bytes": zip_ruta.stat().st_size}}
    prep.datos = lambda: falso
    final = prep.preparar_editor(tmp_path / "salida", tmp_path / "cache", zip_ruta)
    return prep, final


def test_b8_verificar_comprueba_los_gz_que_entrega_caddy_y_el_conjunto_exacto(tmp_path):
    import json

    import pytest

    prep, final = _preparar_falso(tmp_path)
    manifiesto = json.loads((final / "MANIFIESTO.json").read_text())
    assert "OpenCADStudio-fedcba9876543210_bg.wasm.gz" in manifiesto["comprimidos"]
    prep.verificar(tmp_path / "salida")
    # Un .gz cambiado (lo que recibe casi todo navegador) ya no pasa.
    gz = final / "OpenCADStudio-fedcba9876543210_bg.wasm.gz"
    original = gz.read_bytes()
    gz.write_bytes(original[:-4] + b"\0\0\0\0")
    with pytest.raises(SystemExit, match="cambió"):
        prep.verificar(tmp_path / "salida")
    gz.write_bytes(original)
    # Un archivo de más tampoco: Caddy lo serviría sin que nadie lo haya comprobado.
    (final / "extra.js").write_text("alert(1)")
    with pytest.raises(SystemExit, match="no son del editor"):
        prep.verificar(tmp_path / "salida")
    (final / "extra.js").unlink()
    (final / "worker_pkg" / "ocs_web_worker.js").unlink()
    with pytest.raises(SystemExit, match="faltan"):
        prep.verificar(tmp_path / "salida")
