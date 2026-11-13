"""Convertidor DXF ↔ KML/KMZ en el navegador móvil: subida reanudable, vista previa en MapLibre, capas y descarga."""
from __future__ import annotations

import zipfile

import pytest
from playwright.sync_api import expect

from itt_herramientas.convertidor import servicio
from itt_herramientas.models import Conversion
from paridad import casos

from .test_pwa_navegador import TIEMPO, contexto, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def convertidora(crear_usuario, roles):
    u = crear_usuario("conv")
    roles.conceder(u, "herramientas", "convertidor")
    return u


@pytest.fixture(scope="module")
def geo(tmp_path_factory):
    return casos.generar_entradas(tmp_path_factory.mktemp("geo_navegador"))


def subir(pagina, ruta):
    pagina.set_input_files("tv-subida input[type=file]", str(ruta))
    pagina.wait_for_url("**/convertidor/*/", timeout=TIEMPO)


def test_dxf_a_kmz_de_punta_a_punta_en_el_telefono(motor, tmp_path, servidor, sesion_de, convertidora, geo):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(convertidora), accept_downloads=True)
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/convertidor/")
        expect(pagina.locator("h1")).to_contain_text("Convertidor")
        subir(pagina, geo["convertidor.dxf"])
        expect(pagina.locator("[data-itt=resumen]")).to_contain_text("elementos", timeout=TIEMPO)
        assert pagina.locator("input[data-itt=capa]").count() == 6
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        # Vista previa: necesita WebGL. Chromium sin cabeza lo tiene; si el motor no, la página lo dice y la conversión sigue funcionando.
        if motor["nombre"] == "chromium":
            esperar(pagina, "document.documentElement.dataset.mapaListo === '1'", timeout=TIEMPO * 2)
            expect(pagina.locator("#itt-mapa canvas")).to_be_visible()
        else:
            esperar(pagina, "document.documentElement.dataset.mapaListo === '1' || /no puede|no está disponible|No se pudo/.test(document.querySelector('#itt-mapa-info').textContent)", timeout=TIEMPO * 2)
        # Capas: solo PREDIO y CURVAS.
        pagina.locator("[data-itt=ninguna]").click()
        for capa in ("PREDIO", "CURVAS"):
            pagina.locator(f"input[data-itt=capa][value='{capa}']").check()
        pagina.select_option("#formato", "kmz")
        pagina.locator("#itt-convertir").click()
        expect(pagina.locator("[data-itt=descargar]")).to_be_visible(timeout=TIEMPO)
        with pagina.expect_download() as descarga:
            pagina.locator("[data-itt=descargar]").click()
        archivo = tmp_path / descarga.value.suggested_filename
        descarga.value.save_as(archivo)
        assert archivo.name == "convertidor_convertido.kmz"
        with zipfile.ZipFile(archivo) as zf:
            kml = zf.read("doc.kml").decode()
        assert "PREDIO" in kml and "CURVAS" in kml and "TANQUES" not in kml
        assert Conversion.objects.get().salida_formato == "kmz"
    finally:
        ctx.close()


def test_kml_a_dxf_y_archivo_grande_manda_al_modulo_local(motor, tmp_path, servidor, sesion_de, convertidora, geo, settings):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(convertidora), accept_downloads=True)
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/convertidor/")
        subir(pagina, geo["capas.kmz"])
        expect(pagina.locator("[data-itt=resumen]")).to_contain_text("elementos", timeout=TIEMPO)
        assert pagina.locator("#formato option").all_inner_texts() == ["DXF"]
        pagina.locator("#itt-convertir").click()
        with pagina.expect_download() as descarga:
            pagina.locator("[data-itt=descargar]").click()
        assert descarga.value.suggested_filename == "capas_convertido.dxf"
        # Un archivo que supera los límites se rechaza ANTES de subirlo, con el mensaje del módulo local.
        settings.TV_HERRAMIENTAS_LIMITES = {"convertidor": {"bytes": 500, "entidades": 200_000}}
        pagina.goto(servidor.url + "/convertidor/")
        pagina.set_input_files("tv-subida input[type=file]", str(geo["convertidor.dxf"]))
        expect(pagina.locator("#itt-convertidor-error, .tv-subida__item[data-estado=error]").first).to_be_visible(timeout=TIEMPO)
    finally:
        ctx.close()


def test_diseno_del_convertidor_a_360_px(motor, tmp_path, servidor, sesion_de, convertidora, geo):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(convertidora), viewport={"width": 360, "height": 740})
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/convertidor/")
        subir(pagina, geo["convertidor.dxf"])
        expect(pagina.locator("[data-itt=resumen]")).to_contain_text("elementos", timeout=TIEMPO)
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        for selector in ("#itt-convertir", "select", "[data-itt=todas]"):
            for caja in [e.bounding_box() for e in pagina.locator(selector).all() if e.is_visible()]:
                assert caja["height"] >= 43.5, (selector, caja)       # las casillas se tocan por su etiqueta de 44 px (abajo)
        for fila in pagina.locator(".itt-capas label").all():
            assert fila.bounding_box()["height"] >= 43.5
    finally:
        ctx.close()
