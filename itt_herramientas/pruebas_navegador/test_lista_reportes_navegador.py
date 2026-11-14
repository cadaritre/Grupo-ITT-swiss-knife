"""Lista de reportes a 360 y 1280 px: cliente sugerido al crear, filtro por cliente en la URL, selección y ZIP con los PDF."""
from __future__ import annotations

import os
import uuid
import zipfile

import pytest
from playwright.sync_api import expect

from itt_herramientas.reportes import servicio as rep
from itt_herramientas.pruebas.test_reportes import crear_foto, datos as datos_reporte, foto

from .test_pwa_navegador import TIEMPO, contexto

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")


@pytest.mark.parametrize("ancho", [360, 1280])
def test_cliente_sugerido_filtro_y_zip(motor, tmp_path, servidor, sesion_de, crear_usuario, roles, settings, ancho):
    rita = crear_usuario("rita")
    rita.first_name, rita.last_name, rita.email = "Rita", "Ochoa", "rita@grupoitt.com"
    rita.save()
    roles.conceder(rita, "herramientas", "reportes")
    for n, (titulo, cliente) in enumerate((("Bodega", "Agrícola Norte"), ("Zanja", "Constructora Sur"), ("Almacén", "Agrícola Norte")), 1):
        rep.sincronizar(rita, uuid.uuid4(), None, None, datos_reporte([foto(crear_foto(rita, n, settings))], titulo=titulo, cliente=cliente), "grupo_itt")

    telefono = ancho < 768
    ctx = contexto(motor, tmp_path, servidor, sesion_de(rita), viewport={"width": ancho, "height": 900}, is_mobile=telefono, has_touch=telefono, accept_downloads=True)
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/reportes/")
        pagina.wait_for_selector("html[data-rep-lista]", timeout=TIEMPO)
        expect(pagina.locator("#rep-lista > li")).to_have_count(3, timeout=TIEMPO)
        expect(pagina.locator("#rep-lista")).to_contain_text("Rita Ochoa")                     # autoría en cada tarjeta
        # Nuevo reporte: el campo «Cliente» sugiere los de la cuenta (sin repetidos) y se elige con el dedo.
        pagina.locator("#rep-nuevo").click()
        pagina.wait_for_selector("#rep-vista-editor:not([hidden])")
        expect(pagina.locator("#rep-autor")).to_contain_text("Rita Ochoa")
        pagina.locator("#r-cliente").click()
        pagina.locator("#r-cliente").press_sequentially("agri")
        opciones = pagina.locator(".itt-sugerencias [role=option]")
        expect(opciones).to_have_count(1)
        expect(opciones.first).to_have_text("Agrícola Norte")
        assert pagina.locator("#r-cliente").get_attribute("aria-expanded") == "true"
        opciones.first.click() if not telefono else opciones.first.tap()
        expect(pagina.locator("#r-cliente")).to_have_value("Agrícola Norte")
        expect(pagina.locator(".itt-sugerencias")).to_be_hidden()
        pagina.locator("#r-titulo").fill("Visita nueva")
        pagina.locator("#rep-volver").click()
        expect(pagina.locator("#rep-lista > li")).to_have_count(4)
        # Filtrar por cliente: el filtro queda en la URL y «Atrás» lo quita.
        if telefono:
            pagina.locator("#rep-filtros-boton").click()
        pagina.locator("#rep-f-cliente").select_option("Agrícola Norte")
        expect(pagina.locator("#rep-lista > li")).to_have_count(3)
        assert "cliente=" in pagina.url
        expect(pagina.locator("#rep-filtros-cuenta")).to_have_text("1")
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        # Elegir dos y bajarlos en un ZIP (el que no tiene PDF se genera antes).
        if telefono:
            pagina.locator("#rep-seleccionar").click()
        for titulo in ("Bodega", "Almacén"):
            pagina.locator("#rep-lista > li", has_text=titulo).locator(".itt-casilla input").check()
        expect(pagina.locator("#rep-sel-cuenta")).to_have_text("2 seleccionados")
        if CAPTURAS:
            pagina.screenshot(path=f"{CAPTURAS}/reportes-lista-{motor['nombre']}-{ancho}.png", full_page=True)
        assert pagina.locator("#rep-sel-zip").bounding_box()["height"] >= 43.5
        with pagina.expect_download(timeout=TIEMPO * 3) as descarga:
            pagina.locator("#rep-sel-zip").click()
        assert descarga.value.suggested_filename.startswith("Reportes 20") and descarga.value.suggested_filename.endswith(".zip")
        with zipfile.ZipFile(descarga.value.path()) as zf:
            assert sorted(zf.namelist()) == ["Almacén.pdf", "Bodega.pdf"]
        expect(pagina.locator("#rep-seleccion")).to_be_hidden()
        # Nada coincide: estado vacío con su salida.
        pagina.locator("#rep-q").fill("no existe")
        expect(pagina.locator("#rep-sin-resultados")).to_be_visible()
        pagina.locator("#rep-sin-resultados-quitar").click()
        expect(pagina.locator("#rep-lista > li")).to_have_count(4)
        pagina.go_back()                                                                         # «Atrás» devuelve los filtros de antes
        expect(pagina.locator("#rep-f-cliente")).to_have_value("Agrícola Norte")
        expect(pagina.locator("#rep-q")).to_have_value("no existe")
    finally:
        ctx.close()
