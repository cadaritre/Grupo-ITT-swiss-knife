"""Página de Google Drive en el navegador móvil (Chromium y WebKit): elegir la carpeta con un Picker FALSO. El Google real (selector y OAuth)
no se puede probar sin credenciales: aquí se prueba todo lo nuestro (token para el selector, carpeta elegida, jerarquía, mensajes)."""
from __future__ import annotations

import urllib.parse

import pytest
from playwright.sync_api import expect

from itt_herramientas.drive import google, servicio
from itt_herramientas.models import ConexionDrive
from itt_herramientas.pruebas.google_falso import GoogleFalso

from .test_pwa_navegador import TIEMPO, contexto

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]

PICKER_FALSO = """
(() => {
  const registro = window.__picker = { vistas: 0, token: null, clave: null, app: null };
  class Vista { constructor() { registro.vistas++; } setIncludeFolders() { return this; } setSelectFolderEnabled() { return this; } setMimeTypes() { return this; } setEnableDrives() { return this; } }
  class Constructor {
    addView() { return this; } setOAuthToken(t) { registro.token = t; return this; } setDeveloperKey(k) { registro.clave = k; return this; } setAppId(a) { registro.app = a; return this; }
    setTitle() { return this; } setCallback(f) { this.f = f; return this; }
    build() { const f = this.f; return { setVisible() { setTimeout(() => f({ action: window.__elegir_accion || "picked", docs: [{ id: window.__elegir_id }] }), 50); } }; }
  }
  window.google = { picker: { DocsView: Vista, PickerBuilder: Constructor, ViewId: { FOLDERS: "folders" }, Action: { PICKED: "picked", CANCEL: "cancel" } } };
})();
"""


@pytest.fixture
def persona(crear_usuario, roles):
    u = crear_usuario("Ana")
    roles.conceder(u, "herramientas", "reportes")
    return u


@pytest.fixture
def falso(settings, monkeypatch):
    settings.TV_DRIVE_CLIENT_ID, settings.TV_DRIVE_CLIENT_SECRET = "cliente-de-prueba", "secreto"
    settings.HERRAMIENTAS_DRIVE_API_KEY, settings.HERRAMIENTAS_DRIVE_APP_ID = "clave-api", "123456"
    f = GoogleFalso()
    monkeypatch.setattr(servicio, "_transporte", f)
    return f


def conectada(falso, persona):
    verificador, desafio = google.pkce()
    tokens = servicio.cliente().canjear(falso.codigo(desafio, "https://x/cb"), "https://x/cb", verificador)
    return servicio.conectar(persona.pk, tokens)


def test_elegir_la_carpeta_con_el_selector(motor, tmp_path, servidor, sesion_de, persona, falso):
    conectada(falso, persona)
    raiz = falso.carpeta("GRUPO ITT REPORTES")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(persona))
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.add_init_script(PICKER_FALSO)
        pagina.goto(servidor.url + "/drive/")
        expect(pagina.locator("#drive-sin-carpeta")).to_be_visible()
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        assert pagina.locator("#drive-elegir").bounding_box()["height"] >= 43.5
        # Una carpeta que la app no puede ver: el servidor lo dice y no guarda nada.
        ajena = falso.carpeta("Ajena", conceder=False)
        pagina.evaluate(f"window.__elegir_id = {ajena!r}")
        pagina.locator("#drive-elegir").click()
        expect(pagina.locator("#drive-mensaje")).to_contain_text("selector de Google", timeout=TIEMPO)
        assert ConexionDrive.objects.get().carpeta_raiz_id == ""
        # Cancelar no hace nada y deja volver a intentar.
        pagina.evaluate("window.__elegir_accion = 'cancel'")
        pagina.locator("#drive-elegir").click()
        expect(pagina.locator("#drive-elegir")).to_be_enabled(timeout=TIEMPO)
        # La carpeta buena.
        pagina.evaluate(f"window.__elegir_accion = 'picked'; window.__elegir_id = {raiz!r}")
        with pagina.expect_navigation(timeout=TIEMPO * 2):
            pagina.locator("#drive-elegir").click()
        expect(pagina.locator("#drive-carpeta-ok")).to_contain_text("GRUPO ITT REPORTES / Ana")
        c = ConexionDrive.objects.get()
        assert c.carpeta_raiz_id == raiz and set(c.carpetas) == {"persona", "cotizaciones", "reportes", "poligonos"}
        assert falso.ruta_de(c.carpetas["reportes"]) == "GRUPO ITT REPORTES/Ana/Reportes fotograficos"
        assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        ctx.close()


def test_el_selector_recibe_el_token_de_acceso_la_clave_y_el_numero_de_proyecto(motor, tmp_path, servidor, sesion_de, persona, falso):
    conectada(falso, persona)
    raiz = falso.carpeta("GRUPO ITT REPORTES")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(persona))
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.add_init_script(PICKER_FALSO)
        pagina.goto(servidor.url + "/drive/")
        pagina.evaluate(f"window.__elegir_accion = 'cancel'; window.__elegir_id = {raiz!r}")
        pagina.locator("#drive-elegir").click()
        import time
        limite = time.monotonic() + 10
        while time.monotonic() < limite and not pagina.evaluate("window.__picker.token"):
            time.sleep(0.1)
        r = pagina.evaluate("window.__picker")
        assert r["token"] in falso.accesos and r["clave"] == "clave-api" and r["app"] == "123456" and r["vistas"] == 2
        assert next(iter(falso.refrescos)) != r["token"]                                          # el selector nunca recibe el token de actualización
    finally:
        ctx.close()


def test_sin_conexion_ni_cliente_la_pagina_lo_explica(motor, tmp_path, servidor, sesion_de, persona, settings):
    settings.TV_DRIVE_CLIENT_ID = ""
    settings.TV_DRIVE_CLIENT_SECRET = ""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(persona))
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/drive/")
        expect(pagina.locator("main")).to_contain_text("no está activado")
        assert pagina.locator("text=Conectar con Google").count() == 0
    finally:
        ctx.close()
