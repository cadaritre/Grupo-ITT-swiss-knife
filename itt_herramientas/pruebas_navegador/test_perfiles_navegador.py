"""Cada perfil corporativo es un espacio de documentos aparte, en el navegador (Chromium/Pixel y WebKit/iPhone), en modo producción.

- Cambiar de perfil cambia la lista; lo pendiente sin conexión de un perfil sube a ese perfil aunque mientras tanto se haya cambiado al
  otro (aquí, desde otro dispositivo) y la app instalada, sin señal, sigue mostrando solo el espacio del perfil activo.
- «Copiar a TresVizo» desde una cotización de Grupo ITT: folio de la serie de TresVizo y la página se abre en ese perfil.
- Un enlace a un documento del otro perfil dice de qué perfil es y ofrece cambiar.

Capturas en `ITT_CAPTURAS` (si se define).
"""
from __future__ import annotations

import os
import uuid

import pytest
from django.utils import timezone
from playwright.sync_api import expect

from itt_herramientas.comun import perfiles
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.models import Cotizacion, Perfil

from .test_documentos_equipo_navegador import captura, produccion, sin_desborde  # noqa: F401  (fixture)
from .test_pwa_navegador import TIEMPO, contexto, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
HOY = timezone.localdate().year


@pytest.fixture
def personas(crear_usuario, roles):
    def persona(usuario, nombre, *extra):
        u = crear_usuario(usuario)
        u.first_name, u.last_name = nombre.split(" ", 1)
        u.save()
        roles.conceder(u, "herramientas", "cotizaciones", *extra)
        return u
    return persona("ana", "Ana López"), persona("dora", "Dora Ruiz", "cambiar_perfil")


def _cotizacion(usuario, perfil, titulo):
    d = perfiles.nueva_cotizacion(Perfil.objects.get(clave=perfil)).to_dict()
    d.update(project_title=titulo, client_name="Cliente de prueba", items=[{"title": "Levantamiento", "unit": "Lote", "quantity": 1, "unit_price": 9000}], images=[])
    return cot.sincronizar(usuario, uuid.uuid4(), None, None, d, perfil)


def abrir(ctx, servidor, ruta="/cotizaciones/"):
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    if os.environ.get("ITT_DEPURAR"):
        pagina.on("console", lambda m: print("CON", m.type, m.text[:300]))
        pagina.on("pageerror", lambda e: print("ERR", str(e)[:300]))
    pagina.goto(servidor.url + ruta)
    pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
    return pagina


def lista(pagina) -> str:
    return pagina.locator("#cot-lista").inner_text()


def test_cambiar_de_perfil_cambia_la_lista_y_sin_conexion_no_se_mezclan(motor, tmp_path, servidor, sesion_de, personas, produccion):
    ana, dora = personas
    _cotizacion(ana, "grupo_itt", "Deslinde ITT")
    _cotizacion(dora, "tresvizo", "Deslinde TV")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(dora))
    try:
        pagina = abrir(ctx, servidor)
        esperar(pagina, "navigator.serviceWorker.ready.then(() => true)")
        if not pagina.evaluate("!!navigator.serviceWorker.controller"):
            pagina.reload()
            pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
            esperar(pagina, "!!navigator.serviceWorker.controller")
        expect(pagina.locator("#cot-lista")).to_contain_text("Deslinde ITT", timeout=TIEMPO)
        assert "Deslinde TV" not in lista(pagina)
        expect(pagina.locator("#cot-espacio")).to_contain_text("Perfil: Grupo ITT")
        expect(pagina.locator("#cot-espacio")).to_contain_text("Cambiar a TresVizo")
        sin_desborde(pagina)
        captura(pagina, "perfiles-01-lista-grupo-itt", motor)
        # Sin señal, una cotización nueva de Grupo ITT queda pendiente en el teléfono.
        ctx.set_offline(True)
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        pagina.fill("#f-project_title", "Pendiente ITT")
        pagina.wait_for_timeout(700)
        pagina.locator("#cot-volver").click()
        pagina.wait_for_selector("#cot-vista-lista:not([hidden])")
        expect(pagina.locator("#cot-lista")).to_contain_text("Pendiente ITT")
        # Mientras, en otro dispositivo, Dora cambia a TresVizo. Vuelve la señal: lo pendiente sube a Grupo ITT y la página se abre en TresVizo.
        perfiles.cambiar_perfil(dora, "tresvizo")
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora && ITT.subirAhora()")
        expect(pagina.locator("#cot-espacio")).to_contain_text("Perfil: TresVizo", timeout=TIEMPO * 2)
        pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
        expect(pagina.locator("#cot-lista")).to_contain_text("Deslinde TV", timeout=TIEMPO)
        assert "Deslinde ITT" not in lista(pagina) and "Pendiente ITT" not in lista(pagina)
        pendiente = Cotizacion.objects.get(proyecto="Pendiente ITT")
        assert pendiente.perfil == "grupo_itt" and pendiente.folio == f"COT-{HOY}-0002"          # su perfil y la serie de su perfil
        esperar(pagina, "ITT.cola.resumen().then(r => r.total === 0)", timeout=TIEMPO * 2)
        captura(pagina, "perfiles-02-lista-tresvizo", motor)
        # Con el botón de la lista vuelve a Grupo ITT.
        pagina.locator("#cot-espacio button").click()
        expect(pagina.locator("#cot-espacio")).to_contain_text("Perfil: Grupo ITT", timeout=TIEMPO)
        expect(pagina.locator("#cot-lista")).to_contain_text("Pendiente ITT", timeout=TIEMPO)
        expect(pagina.locator("#cot-lista")).to_contain_text("Deslinde ITT")
        assert "Deslinde TV" not in lista(pagina)
        # La app sin señal: se abre desde el teléfono y sigue en el espacio de Grupo ITT, aunque TresVizo también está guardado en él.
        esperar(pagina, "ITT.cola.borradores().then(b => b.some(x => (x.perfil || (x.resumen || {}).perfil) === 'tresvizo'))")
        ctx.set_offline(True)
        pagina.reload()
        pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
        expect(pagina.locator("#cot-lista")).to_contain_text("Deslinde ITT", timeout=TIEMPO)
        assert "Deslinde TV" not in lista(pagina)
        assert pagina.locator("#cot-lista > li").count() == 2
        captura(pagina, "perfiles-03-sin-conexion", motor)
    finally:
        ctx.close()


def test_copiar_a_tresvizo_y_abrir_un_enlace_del_otro_perfil(motor, tmp_path, servidor, sesion_de, personas, produccion):
    ana, dora = personas
    a = _cotizacion(ana, "grupo_itt", "Para copiar")
    t = _cotizacion(dora, "tresvizo", "Solo TresVizo")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(dora))
    try:
        pagina = abrir(ctx, servidor, f"/cotizaciones/#/s/{a['servidor_id']}")
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])", timeout=TIEMPO)
        esperar(pagina, "!document.querySelector('.cot-formulario').inert")
        pagina.locator("#cot-mas").click()
        boton = pagina.locator("#cot-mas-copiar button")
        expect(boton).to_have_text("Copiar a TresVizo")
        captura(pagina, "perfiles-04-mas-acciones", motor)
        boton.click()
        expect(pagina.locator("#confirmar-texto")).to_contain_text(f"Se copió a TresVizo como COT-{HOY}-0002", timeout=TIEMPO)
        captura(pagina, "perfiles-05-copiada", motor)
        pagina.locator("#confirmar-si").click()                                            # «Cambiar a TresVizo»
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])", timeout=TIEMPO)
        expect(pagina.locator("#f-quote_number")).to_have_value(f"COT-{HOY}-0002", timeout=TIEMPO)
        expect(pagina.locator("#f-project_title")).to_have_value("Para copiar")
        copia = Cotizacion.objects.get(perfil="tresvizo", proyecto="Para copiar")
        assert copia.folio == f"COT-{HOY}-0002" and Cotizacion.objects.get(pk=a["servidor_id"]).perfil == "grupo_itt"
        # Un enlace (búsqueda, aviso) a una cotización de Grupo ITT estando en TresVizo: lo dice y ofrece cambiar.
        pagina.goto(servidor.url + f"/cotizaciones/#/s/{a['servidor_id']}")
        expect(pagina.locator("#confirmar-texto")).to_contain_text("Este documento es del perfil Grupo ITT", timeout=TIEMPO)
        captura(pagina, "perfiles-06-enlace-de-otro-perfil", motor)
        pagina.locator("#confirmar-no").click()                                            # «Seguir aquí»: la lista de TresVizo
        pagina.wait_for_selector("#cot-vista-lista:not([hidden])", timeout=TIEMPO)
        expect(pagina.locator("#cot-lista")).to_contain_text("Solo TresVizo", timeout=TIEMPO)
        assert t["perfil"] == "tresvizo"
    finally:
        ctx.close()
