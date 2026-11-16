"""Parches de M (15-11-2026) en las piezas sin conexión: lo que se puede comprobar sin navegador (lo demás, en
pruebas_navegador/test_pwa_webkit_navegador.py y en el e2e del Portal)."""
from __future__ import annotations

from pathlib import Path

from itt_herramientas.cad import editor
from itt_herramientas.offline import sw


def test_el_service_worker_guarda_el_manifiesto_y_los_colores_del_perfil_con_red_primero():
    c = sw.contenido()
    assert "redLuegoCache" in c and '"/perfil.css"' in c and '"/manifest.webmanifest"' in c
    # nunca datos de una cuenta: /sesion/ y /subidas/ siguen sin pasar por la caché
    assert 'url.pathname.startsWith("/subidas/") || url.pathname === "/sesion/"' in c


def test_el_editor_en_desarrollo_se_guarda_como_privado_igual_que_en_produccion(tmp_path):
    for nombre in ("OpenCADStudio-0123456789abcdef_bg.wasm", "ocs-control.js"):
        assert editor.cabeceras(Path(tmp_path / nombre))["Cache-Control"].startswith("private,")


def test_la_cola_no_se_queda_colgada_ni_repite_el_fallo_de_la_ventana_privada():
    cola = (Path(sw.ESTATICOS) / "offline" / "cola.js").read_text()
    assert "TOPE_MS" in cola and "sinFotos" in cola and "ev.target.error" in cola
