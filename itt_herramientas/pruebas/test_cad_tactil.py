"""CAD · «Modo táctil» y CAD sin conexión detrás de TV_CAD_TACTIL (apagado por omisión): con el ajuste apagado nada cambia."""
from __future__ import annotations

import json

import pytest

from itt_herramientas.cad import editor, guardado, tactil
from itt_herramientas.offline import sw

from .conftest import url
from .test_cad import dibujante, editor_falso, plano, supervisora  # noqa: F401 (fixtures)


def _config(html):
    return json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])


def test_apagado_por_omision_la_pagina_y_el_service_worker_son_los_de_siempre(cliente_de, dibujante, plano, editor_falso, settings, monkeypatch):
    monkeypatch.delenv("TV_CAD_TACTIL", raising=False)
    assert not hasattr(settings, "TV_CAD_TACTIL") or settings.TV_CAD_TACTIL in (None, False)
    assert tactil.activo() is False
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    assert _config(html)["tactil"] is None
    assert "tactil.js" not in html and 'id="tt"' not in html and "cad-tactil-entrar" not in html and "campo.js" not in html
    # El service worker sirve el editor guardado en el equipo (siempre), pero no la parte sin conexión.
    contenido = sw.contenido()
    assert "itt-cad-editor-" in contenido and "itt-cad-pagina" not in contenido and "/cad/sin-conexion/" not in contenido
    c = cliente_de(dibujante)
    assert c.get(url("cad_sin_conexion")).status_code == 404


@pytest.mark.parametrize("valor,esperado", [("1", True), ("true", True), ("si", True), ("0", False), ("", False), ("no", False)])
def test_el_ajuste_tambien_se_lee_del_entorno(valor, esperado, settings, monkeypatch):
    if hasattr(settings, "TV_CAD_TACTIL"):
        del settings.TV_CAD_TACTIL
    monkeypatch.setenv("TV_CAD_TACTIL", valor)
    assert tactil.activo() is esperado


def test_encendido_la_pagina_lleva_la_capa_tactil_y_el_paquete_del_editor(cliente_de, dibujante, plano, editor_falso, settings):
    settings.TV_CAD_TACTIL = True
    (editor_falso / "MANIFIESTO.json").write_text(json.dumps({"version": editor.version(), "archivos": {
        "index.html": "a" * 64, "OpenCADStudio-0123456789abcdef_bg.wasm": "b" * 64, "worker_pkg/ocs_web_worker.js": "c" * 64,
        "fonts/chinese.ttf": "d" * 64, "fonts/latin.ttf": "e" * 64}}))
    (editor_falso / "fonts").mkdir()
    (editor_falso / "fonts" / "chinese.ttf").write_bytes(b"x" * 100)
    (editor_falso / "fonts" / "latin.ttf").write_bytes(b"x" * 10)
    (editor_falso / "OpenCADStudio-0123456789abcdef_bg.wasm.gz").write_bytes(b"gz")
    guardado._paquete.cache_clear()
    c = cliente_de(dibujante)
    html = c.get(url("cad_plano", pk=plano.pk)).content.decode()
    t = _config(html)["tactil"]
    assert t["version"] == editor.version() and t["cuenta"] == dibujante.pk and t["descarga"] > 0
    assert 'id="tt"' in html and "herramientas/cad/tactil.js" in html and "herramientas/cad/campo.js" in html and 'id="cad-tactil-entrar"' in html
    p = c.get(url("cad_editor_paquete")).json()
    assert p["version"] == editor.version() and p["base"] == f"/cad/editor/{editor.version()}/"
    wasm = next(a for a in p["archivos"] if a["ruta"].endswith(".wasm"))
    assert wasm["sha256"] == "b" * 64 and wasm["descarga"] == 2 and wasm["bytes"] == 8
    rutas = {a["ruta"] for a in p["archivos"]}
    assert "MANIFIESTO.json" not in rutas and "fonts/chinese.ttf" not in rutas and "fonts/latin.ttf" in rutas
    # La página sin conexión: sin datos de ninguna cuenta (se guarda en el teléfono), con la capa táctil.
    r = c.get(url("cad_sin_conexion"))
    assert r.status_code == 200
    cuerpo = r.content.decode()
    assert dibujante.email not in cuerpo and "Ana" not in cuerpo and 'data-cad-campo="1"' in cuerpo and "sin-conexion.js" in cuerpo
    assert _config(cuerpo)["tactil"]["urls"]["paquete"] == url("cad_editor_paquete")
    # El service worker sirve el editor descargado y la página sin conexión; con otro número de versión.
    contenido = sw.contenido()
    assert "itt-cad-editor-" in contenido and "itt-cad-pagina" in contenido and "stopImmediatePropagation" in contenido
    settings.TV_CAD_TACTIL = False
    assert sw.contenido() != contenido


def test_la_pagina_sin_conexion_pide_sesion(client, settings, editor_falso):
    settings.TV_CAD_TACTIL = True
    r = client.get(url("cad_sin_conexion"))
    assert r.status_code in (302, 401, 404)


def test_la_lista_dice_cuantos_planos_hay_en_el_telefono_solo_con_el_ajuste(cliente_de, dibujante, settings, monkeypatch):
    monkeypatch.delenv("TV_CAD_TACTIL", raising=False)
    settings.TV_CAD_TACTIL = False
    html = cliente_de(dibujante).get(url("cad")).content.decode()
    assert "cad-campo-tarjeta" not in html and "campo.js" not in html
    settings.TV_CAD_TACTIL = True
    html = cliente_de(dibujante).get(url("cad")).content.decode()
    assert 'id="cad-campo-tarjeta"' in html and "herramientas/cad/campo.js" in html
