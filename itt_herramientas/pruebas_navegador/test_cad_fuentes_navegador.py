"""Fuentes SHX en el editor real: la página registra las fuentes de la empresa al abrir y un TEXT con estilo `txt` cambia de ancho.

La fuente de prueba es nuestra (cada mayúscula es un trazo de 15 unidades a altura 6): «AAAA» con ella mide unas 4 veces lo que con la
fuente de reemplazo, así que el encuadre (Zoom Extensión) lo mide. Bajo el candado pesado, un navegador a la vez.
"""
from __future__ import annotations

import pytest

from itt_herramientas.cad import fuentes, servicio

from ..pruebas.test_cad_fuentes import shx_de_prueba
from .test_cad_navegador import LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


def encuadre(p):
    p.evaluate("TVCad.pedir({op: 'action', name: 'zoom_extents'})")
    p.wait_for_timeout(1000)
    return p.evaluate("TVCad.pedir({op: 'state'}).then(s => s.camera.distance)")


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_la_fuente_txt_de_la_empresa_cambia_el_texto(cad, navegar, crear_usuario, roles, motor):
    jefa = _persona(crear_usuario, roles, "jefa", "Jefa Mora", "cad", "admin")
    plano = servicio.crear_vacio(jefa, "Textos", "", "dxf")
    n = navegar(jefa, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    p.evaluate("TVCad.pedir({op: 'entities_create', entities: [{type: 'Text', value: 'AAAA', position: [0, 0], height: 1, style: 'Standard'}]})")
    antes = encuadre(p)
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    fuentes.guardar("txt.shx", shx_de_prueba(), jefa)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    despues = encuadre(p)
    assert despues / antes > 2.0, (antes, despues)
    assert p.locator("#cad-aviso-fuentes").is_hidden()
    assert fuentes.FuenteCad.objects.get().error == ""
    assert not n.errores, n.errores


def test_el_motivo_de_una_fuente_rechazada_va_en_espanol(cad, navegar, crear_usuario, roles):
    """Auditoría B-9: el motivo que ve quien administra en «Fuentes» es un texto fijo en español, no el mensaje (en inglés) del editor."""
    import re

    jefa = _persona(crear_usuario, roles, "jefa", "Jefa Mora", "cad", "admin")
    plano = servicio.crear_vacio(jefa, "Textos", "", "dxf")
    fuentes.guardar("txt.shx", shx_de_prueba(), jefa)
    n = navegar(jefa, "chromium")
    # El servidor la aceptó, pero al navegador le llega un archivo que el editor no puede leer.
    n.contexto.route(re.compile(r".*/cad/fuentes/txt\.shx/(\?.*)?$"), lambda r: r.fulfill(status=200, body=b"esto no es una fuente de dibujo" * 8))
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "!document.getElementById('cad-aviso-fuentes').hidden", LARGO)
    for _ in range(50):
        f = fuentes.FuenteCad.objects.get()
        if f.error:
            break
        p.wait_for_timeout(200)
    assert f.error == "El editor no pudo leer este archivo de fuente."
