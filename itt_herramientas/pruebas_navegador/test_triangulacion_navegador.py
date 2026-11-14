"""Triangulación en el navegador móvil (Chromium/Pixel y WebKit/iPhone): subida, superficie, pendientes y escurrimiento, visor con zoom y paneo,
tablas de rangos editables y exportación del DXF."""
from __future__ import annotations

import numpy as np
import pytest
from playwright.sync_api import expect

from itt_herramientas.models import Triangulacion

from .test_pwa_navegador import TIEMPO, contexto, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def topografa(crear_usuario, roles):
    u = crear_usuario("topo")
    roles.conceder(u, "herramientas", "triangulacion")
    return u


@pytest.fixture(scope="module")
def entradas(tmp_path_factory):
    from paridad import casos

    return casos.generar_entradas(tmp_path_factory.mktemp("tri_navegador"))


def sin_desborde(pagina):
    assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "la página se desborda horizontalmente"


def esperar_vista(pagina, minimo):
    esperar(pagina, f"Number(document.documentElement.dataset.triVista || 0) >= {minimo} && !document.documentElement.dataset.triCargando", timeout=TIEMPO * 2)


def enviar(pagina, selector):
    """Pulsa un botón de envío y espera a que la página nueva (con su resultado) termine de cargar."""
    with pagina.expect_navigation(timeout=TIEMPO * 2):
        pagina.locator(selector).click()
    pagina.wait_for_selector("html[data-tri-lista]", timeout=TIEMPO)


def pintados(pagina) -> float:
    """Fracción del lienzo que no es el color de fondo."""
    return pagina.evaluate("""() => { const c = document.querySelector('#tri-lienzo'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
        let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i] !== 0x13 || d[i + 1] !== 0x23 || d[i + 2] !== 0x31) n++; return n / (d.length / 4); }""")


def test_de_la_subida_al_dxf_en_el_telefono(motor, tmp_path, servidor, sesion_de, topografa, entradas):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(topografa), accept_downloads=True)
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/triangulacion/")
        expect(pagina.locator("h1")).to_contain_text("Triangulación")
        pagina.set_input_files("tv-subida input[type=file]", str(entradas["puntos.dxf"]))
        pagina.wait_for_url("**/triangulacion/*/", timeout=TIEMPO)
        pagina.wait_for_selector("html[data-tri-lista]", timeout=TIEMPO)
        sin_desborde(pagina)
        # --- Superficie: puntos -> TIN ---
        pagina.fill("#arista_max", "120")
        enviar(pagina, "[data-panel=superficie] button[type=submit]")
        esperar_vista(pagina, 1)
        assert pintados(pagina) > 0.2                                                      # la superficie rasterizada cubre buena parte del visor
        expect(pagina.locator("#tri-info")).to_contain_text("triángulos")
        # --- Zoom con la rueda y paneo con el ratón: piden una vista nueva al servidor ---
        antes = int(pagina.evaluate("document.documentElement.dataset.triVista"))
        caja = pagina.locator("#tri-lienzo").bounding_box()
        pagina.evaluate("(c) => document.querySelector('#tri-lienzo').dispatchEvent(new WheelEvent('wheel', { deltaY: -400, clientX: c[0], clientY: c[1], bubbles: true, cancelable: true }))",
                        [caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2])          # la rueda no existe en WebKit móvil: se envía el evento
        esperar_vista(pagina, antes + 1)
        mitad = antes + 1
        pagina.mouse.move(caja["x"] + 80, caja["y"] + 80)
        pagina.mouse.down()
        pagina.mouse.move(caja["x"] + 160, caja["y"] + 130, steps=4)
        pagina.mouse.up()
        esperar_vista(pagina, mitad + 1)
        pagina.locator("[data-itt=ajustar]").click()
        esperar_vista(pagina, mitad + 2)
        assert pintados(pagina) > 0.2
        # --- Pendientes: tabla editable de rangos ---
        pagina.locator("[data-pestana=pendientes]").click()
        assert pagina.locator("[data-panel=pendientes] .tri-rango").count() == 6
        pagina.locator("[data-panel=pendientes] [data-itt=agregar-rango]").click()
        assert pagina.locator("[data-panel=pendientes] .tri-rango").count() == 7
        pagina.locator("[data-panel=pendientes] [data-itt=quitar-rango]").last.click()
        assert pagina.locator("[data-panel=pendientes] .tri-rango").count() == 6
        pagina.locator("[data-panel=pendientes] .tri-rango input[name=r_color]").first.fill("#00aa00")
        enviar(pagina, "[data-panel=pendientes] button[type=submit]")
        t = Triangulacion.objects.get()
        assert t.pendientes["rangos"][0]["color_hex"] == "#00AA00" and t.pendientes["area_total"] > 0
        esperar_vista(pagina, 1)
        expect(pagina.locator("#tri-leyenda-pendientes")).to_be_visible()
        expect(pagina.locator("#tri-leyenda-pendientes tbody tr")).to_have_count(6)
        assert pintados(pagina) > 0.2
        # --- Escurrimiento: flechas sobre la superficie ---
        pagina.locator("[data-pestana=escurrimiento]").click()
        pagina.fill("#pendiente_min", "0.5")
        enviar(pagina, "[data-panel=escurrimiento] button[type=submit]")
        esperar_vista(pagina, 1)
        t.refresh_from_db()
        assert t.escurrimiento["flechas"] > 0
        expect(pagina.locator("#tri-leyenda-flujo")).to_be_visible()
        # --- Exportar DXF de pendientes y compartirlo ---
        pagina.select_option("#tipo", "pendientes")
        enviar(pagina, "#tri-exportar button[type=submit]")
        pagina.wait_for_selector("#tri-salida", timeout=TIEMPO)
        with pagina.expect_download() as d:
            pagina.locator("#tri-salida a").click()
        ruta = tmp_path / "pendientes.dxf"
        d.value.save_as(str(ruta))
        import ezdxf
        capas = {c.dxf.name for c in ezdxf.readfile(str(ruta)).layers}
        assert "TABLA_PENDIENTES" in capas
        assert d.value.suggested_filename == "puntos_ZONIFICACION_PENDIENTES.dxf"
        pagina.locator("#tri-salida [data-itt=compartir]").click()
        esperar(pagina, "!!document.documentElement.dataset.triCompartido")
        assert pagina.evaluate("document.documentElement.dataset.triCompartido") in {"compartido", "descargado"}
        sin_desborde(pagina)
        for selector in ("[data-pestana=superficie]", "[data-itt=ajustar]", "#tri-exportar button[type=submit]"):
            caja = pagina.locator(selector).bounding_box()
            assert caja and caja["height"] >= 43.5, selector
    finally:
        ctx.close()


def test_sin_superficie_los_resultados_piden_calcularla(motor, tmp_path, servidor, sesion_de, topografa, entradas):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(topografa))
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/triangulacion/")
        pagina.set_input_files("tv-subida input[type=file]", str(entradas["puntos.dxf"]))
        pagina.wait_for_url("**/triangulacion/*/", timeout=TIEMPO)
        pagina.wait_for_selector("html[data-tri-lista]", timeout=TIEMPO)
        expect(pagina.locator("#tri-mensaje")).to_contain_text("Calcula la superficie")
        pagina.locator("[data-pestana=pendientes]").click()
        assert pagina.locator("[data-panel=pendientes] button[type=submit]").is_disabled()
    finally:
        ctx.close()
