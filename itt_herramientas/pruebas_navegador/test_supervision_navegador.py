"""«Documentos de todas las cuentas» en el navegador, a 360 y 1280 px: filtrar, abrir de solo lectura y descargar el PDF."""
from __future__ import annotations

import os
import uuid

import pytest
from playwright.sync_api import expect

from itt_herramientas.comun import perfiles
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.reportes import servicio as rep

from .test_pwa_navegador import TIEMPO, contexto

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")


def _persona(crear_usuario, roles, usuario, nombre, correo, *rol):
    u = crear_usuario(usuario)
    u.first_name, u.last_name = nombre.split(" ", 1)
    u.email = correo
    u.save()
    roles.conceder(u, "herramientas", *rol)
    return u


@pytest.mark.parametrize("ancho", [360, 1280])
def test_supervisar_filtrar_abrir_y_descargar(motor, tmp_path, servidor, sesion_de, crear_usuario, roles, ancho):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "ana@grupoitt.com", "cotizaciones")
    beto = _persona(crear_usuario, roles, "beto", "Beto Sáenz", "beto@grupoitt.com", "reportes")
    sofia = _persona(crear_usuario, roles, "sofia", "Sofía Ruiz", "sofia@grupoitt.com", "supervision")
    d = perfiles.nueva_cotizacion(perfiles.perfil_activo(ana)).to_dict()
    d.update(quote_number="COT-2026-0042", project_title="Deslinde Rancho Viejo", client_name="Agrícola Norte", items=[{"title": "Levantamiento", "unit": "Lote", "quantity": 1, "unit_price": 12500}], images=[])
    cot.sincronizar(ana, uuid.uuid4(), None, None, d, "grupo_itt")
    rep.sincronizar(beto, uuid.uuid4(), None, None, {"titulo": "Visita de obra", "fecha": "2026-11-13", "fotos": []}, "grupo_itt")

    ctx = contexto(motor, tmp_path, servidor, sesion_de(sofia), viewport={"width": ancho, "height": 900}, is_mobile=ancho < 768, has_touch=ancho < 768, accept_downloads=True)
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/supervision/")
        expect(pagina.locator("h1")).to_have_text("Documentos de todas las cuentas")
        expect(pagina.locator("#sup-cuantos")).to_have_text("2 documentos")
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert pagina.evaluate("getComputedStyle(document.documentElement).colorScheme") in ("light", "normal")          # claro por omisión
        if CAPTURAS:
            pagina.screenshot(path=f"{CAPTURAS}/supervision-{motor['nombre']}-{ancho}.png", full_page=True)
        # Filtrar por autor: la página se recarga con el filtro en la URL.
        with pagina.expect_navigation(timeout=TIEMPO):
            pagina.select_option("#sup-autor", label="Beto Sáenz")
        assert "autor=" in pagina.url
        expect(pagina.locator("#sup-cuantos")).to_have_text("1 documento")
        expect(pagina.locator(".sup-tabla tbody tr")).to_have_count(1)
        expect(pagina.locator(".sup-tabla")).to_contain_text("Visita de obra")
        # Buscar algo que no existe: estado vacío con la salida.
        pagina.fill("#sup-q", "nada así")
        with pagina.expect_navigation(timeout=TIEMPO):
            pagina.press("#sup-q", "Enter")
        expect(pagina.get_by_text("Nada coincide con estos filtros.")).to_be_visible()
        with pagina.expect_navigation(timeout=TIEMPO):
            pagina.get_by_role("link", name="Quitar filtros").first.click()
        expect(pagina.locator("#sup-cuantos")).to_have_text("2 documentos")
        # Abrir la cotización de solo lectura: autor, conceptos y total; sin campos que se puedan editar.
        with pagina.expect_navigation(timeout=TIEMPO):
            pagina.locator("a.sup-doc", has_text="Deslinde Rancho Viejo").click()
        expect(pagina.locator(".itt-autoria")).to_contain_text("Ana López")
        expect(pagina.locator(".itt-autoria")).to_contain_text("ana@grupoitt.com")
        expect(pagina.locator(".sup-totales")).to_contain_text("$14,500.00")
        assert pagina.locator("#contenido input, #contenido textarea, #contenido select").count() == 0
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        boton = pagina.get_by_role("link", name="Descargar PDF")
        assert boton.bounding_box()["height"] >= 43.5
        with pagina.expect_download(timeout=TIEMPO) as descarga:
            boton.click()
        assert descarga.value.suggested_filename == "COTIZACION COT-2026-0042 - Deslinde Rancho Viejo.pdf"
        if CAPTURAS:
            pagina.screenshot(path=f"{CAPTURAS}/supervision-cotizacion-{motor['nombre']}-{ancho}.png", full_page=True)
    finally:
        ctx.close()
