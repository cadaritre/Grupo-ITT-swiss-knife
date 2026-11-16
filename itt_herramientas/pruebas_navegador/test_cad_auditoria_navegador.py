"""CAD en el navegador: hallazgos de la auditoría del 15-11-2026 que viven en la página de la biblioteca (lista.js)."""
from __future__ import annotations

import json
import time

import pytest

from itt_herramientas.cad import servicio

from .test_cad_navegador import _persona, navegar  # noqa: F401 (fixture)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_b7_la_espera_del_zip_termina_si_la_tarea_no_responde(navegar, crear_usuario, roles, motor):
    """B-7: con un 404 (o algo que no es JSON) del seguimiento de la tarea, la espera termina con un aviso y el botón vuelve."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    for i in range(2):
        servicio.crear_vacio(ana, f"Predio {i}", "", "dwg")
    n = navegar(ana, motor)
    p = n.ir("/cad/")
    esperar(p, "!!document.getElementById('cad-sel-zip')", 15000)
    tarea = "11111111-2222-3333-4444-555555555555"
    p.route("**/cad/zip/**", lambda r: r.fulfill(status=202, content_type="application/json",
                                                  body=json.dumps({"tarea": tarea, "terminada": False, "estado": "pendiente"})))
    p.route(lambda u: tarea in u and "formato=json" in u, lambda r: r.fulfill(status=404, content_type="text/html", body="<h1>No existe</h1>"))
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a && a(t, k); }; }")
    p.locator("[data-cad-sel]").first.check()
    t = time.monotonic()
    p.locator("#cad-sel-zip").click()
    try:
        esperar(p, "window.__avisos.some(a => a[1] === 'error')", 15000)
    except AssertionError:
        print("\nB-7:", p.evaluate("window.__avisos"), p.evaluate("document.getElementById('cad-zip-estado').textContent"),
              p.evaluate("document.getElementById('cad-sel-zip').disabled"), n.errores, flush=True)
        raise
    assert time.monotonic() - t < 15
    avisos = p.evaluate("window.__avisos")
    assert any("Se perdió el seguimiento" in a[0] for a in avisos), avisos
    esperar(p, "!document.getElementById('cad-sel-zip').hasAttribute('aria-busy')", 5000)
    assert not n.errores, n.errores
