"""CAD y Civil juntos, donde se encuentran las funciones de la ronda del 18-11 (con la compilación del editor que trae la cinta y las
plantillas, en TV_CAD_EDITOR_PRUEBAS):

- la cinta «Civil» abre las hojas de KML (importar y exportar), de perfil y de secciones (con la superficie de proyecto y la guitarra)
  y la de la imagen satelital, que registra su acción con `TVCivil.registrarAccion`; un comando escrito abre la de KML;
- una presentación desde «Mis plantillas» y luego «Guardar todo» con dos pestañas: cada plano guarda lo suyo, el de la plantilla con su
  presentación, su marco y sus atributos.

El cambio de versión con el editor guardado en la computadora (-12 → esta compilación) es la prueba de test_cad_guardado_navegador.py con
ITT_CAD_ANTERIOR / ITT_CAD_NUEVA. Chromium y WebKit, un navegador a la vez, bajo el candado de la máquina.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio
from itt_herramientas.models import VersionPlano

from . import cinta
from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_cad_pestanas_navegador import abrir, abrir_desde_la_hoja, circulo, esperar_pestana, esperar_pestanas  # noqa: F401 (fixtures)
from .test_cad_plantillas_navegador import elegir_e_insertar, menu_de_pestana, plano_de, plantilla, presentaciones
from .test_civil_cinta_navegador import VISTO, abrir_plano, cerrar_lo_abierto, preparar
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
MOTORES = ["chromium", "webkit"]

#: Las hojas de las otras ramas que la cinta debe abrir: acción de MENUS → su hoja.
HOJAS = {
    "importar_kml": "civil-hoja-importar-kml",
    "exportar_kml": "civil-hoja-exportar-kml",
    "perfil": "civil-hoja-perfil",
    "secciones": "civil-hoja-secciones",
    "satelite": "civil-hoja-satelite",
}


@pytest.mark.parametrize("motor", MOTORES)
def test_la_cinta_abre_kml_perfil_secciones_y_satelital(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio integración", "", "dxf")
    n, p = abrir_plano(navegar, ana, plano, motor, ancho=1920, alto=1040)
    esperar_estado(p, "guardado")
    p.locator("#cad-completa").click()                                       # a 1920 px en pantalla completa ningún grupo se compacta
    esperar(p, "document.documentElement.classList.contains('cad-completa')", LARGO)
    p.wait_for_timeout(1500)
    assert cinta.en_cinta(p), "con esta compilación Civil va en la cinta del editor"
    preparar(p)
    r = cinta.mostrar_pestana(p)
    por_comando = {c["command"]: c for c in r["commands"]}
    # la satelital está en «Imágenes», registrada por satelite.js y encendida; KML en «Plano»
    assert p.evaluate("TVCivil.tieneAccion('satelite')")
    assert por_comando["CVSATELITE"]["group"] == "Imágenes" and por_comando["CVSATELITE"]["enabled"], por_comando["CVSATELITE"]
    assert por_comando["CVIMPORTARKML"]["group"] == "Plano" and por_comando["CVEXPORTARKML"]["group"] == "Plano"
    for clave, hoja in HOJAS.items():
        como = cinta.accion(p, clave)
        assert como == "clic", f"{clave}: su botón no se veía a 1920 px"
        try:
            esperar(p, f"document.getElementById({hoja!r}).open", 8000)
        except AssertionError:                                               # un clic que el lienzo no tomó mientras se redibuja la cinta
            cinta.accion(p, clave)
            esperar(p, f"document.getElementById({hoja!r}).open", LARGO)
        v = p.evaluate(VISTO)
        assert v["paletas"] == [hoja], f"{clave}: la hoja es una paleta junto al dibujo ({v})"
        if clave == "perfil":
            # la superficie de proyecto (CC) y la guitarra (CB) en la misma hoja; el renglón de la superficie solo con el interruptor
            expect(p.locator(f"#{hoja} [name=con_superficie]")).to_be_attached()
            expect(p.locator(f"#{hoja} [name=g_terreno]")).to_be_attached()
            renglon = lambda n: p.locator(f"#{hoja} [name={n}]").locator("xpath=..")
            assert renglon("g_proyecto").get_attribute("hidden") is not None and renglon("g_corte").get_attribute("hidden") is not None
            p.locator(f"#{hoja} label[for=civil-per-consup]").click()
            esperar(p, f"!document.querySelector('#{hoja} [name=g_proyecto]').parentElement.hidden", LARGO)
            assert renglon("g_corte").get_attribute("hidden") is None and renglon("g_rasante").get_attribute("hidden") is not None
        if clave == "secciones":
            expect(p.locator(f"#{hoja} [name=con_superficie]")).to_be_attached()
            expect(p.locator(f"#{hoja} [name=columnas]")).to_be_attached()
        cerrar_lo_abierto(p)
    # escrito en la línea de comandos, como un complemento de AutoCAD
    assert cinta.accion(p, "importar_kml", como="comando") == "comando"
    esperar(p, "document.getElementById('civil-hoja-importar-kml').open", LARGO)
    cerrar_lo_abierto(p)
    assert not _errores_propios(n, motor), n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_presentacion_desde_plantilla_y_guardar_todo_con_dos_pestanas(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    norte = servicio.crear_vacio(ana, "Predio Norte", "Obra", "dxf")
    sur = servicio.crear_vacio(ana, "Predio Sur", "Obra", "dwg")
    marco = plano_de(ana, plantilla(tmp_path / "marco.dxf", "PROYECTO INTEGRADO"), "Marco ITT", "Plantillas")
    servicio.marcar_plantilla(ana.pk, marco, True)
    n = abrir(ana, motor, ancho=1366, alto=860)
    p = n.ir(f"/cad/{norte.pk}/")
    esperar_estado(p, "guardado")
    esperar_pestanas(p, 1)
    esperar(p, "TVCadPlantillas.estado().menu === true", LARGO)
    # La presentación «A1 Marco» de «Mis plantillas» en Norte.
    assert menu_de_pestana(p, presentaciones(p)[-1], "from_template")
    fila = p.locator("#cad-plantilla-mias .cad-elegir__plano", has_text="Marco ITT")
    expect(fila).to_be_visible(timeout=LARGO)
    fila.click()
    elegir_e_insertar(p, "A1 Marco")
    esperar(p, "TVCad.pedir({op:'layouts'}).then(r => r.layouts.includes('A1 Marco'))", LARGO)
    esperar_pestana(p, "Predio Norte", "sucio")
    # Sur en otra pestaña, con un cambio.
    abrir_desde_la_hoja(p, "Predio Sur")
    esperar_pestanas(p, 2)
    esperar_estado(p, "guardado")
    expect(p.locator("#cad-nombre")).to_have_text("Predio Sur")
    assert "A1 Marco" not in presentaciones(p), "la presentación es solo del plano donde se insertó"
    circulo(p, 500000, 2000000, 5)
    esperar_pestana(p, "Predio Sur", "sucio")
    # Guardar todo: una versión nueva de cada uno, Norte con su presentación, su marco y sus atributos.
    p.locator("#cad-editor").click(position={"x": 500, "y": 400})
    p.keyboard.press(("Meta" if motor == "webkit" else "Control") + "+Shift+S")
    esperar(p, "TVCadPestanas.lista().every(t => t.estado === 'guardado')", LARGO)
    assert VersionPlano.objects.filter(plano=norte).count() == 2 and VersionPlano.objects.filter(plano=sur).count() == 2
    guardado = servicio.ruta_version(VersionPlano.objects.get(plano=norte, numero=2)).read_bytes()
    assert b"A1 Marco" in guardado and b"MARCO" in guardado and b"PROYECTO INTEGRADO" in guardado
    assert not re.search(rb"\n\s*0\r?\nCIRCLE\r?\n", guardado), "el círculo de Sur no entró en Norte"
    # Reabrir Norte: la presentación sigue ahí.
    p.locator(".cad-pestana", has_text="Predio Norte").click()
    esperar(p, "TVCadPestanas.lista().find(t => t.activa).nombre === 'Predio Norte'", LARGO)
    p.reload()
    esperar_pestanas(p, 2)
    esperar(p, "TVCad.pedir({op:'layouts'}).then(r => r.layouts.includes('A1 Marco'))", LARGO)
    assert not _errores_propios(n, motor), n.errores
