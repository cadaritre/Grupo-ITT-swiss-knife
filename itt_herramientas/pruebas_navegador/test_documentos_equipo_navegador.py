"""Documentos compartidos con el equipo en el navegador (Chromium/Pixel y WebKit/iPhone), EN MODO PRODUCCIÓN: estáticos con huella
(collectstatic + manifiesto) y DEBUG apagado.

- Dos personas, dos contextos de navegador, la misma cotización: la segunda la abre en solo lectura, toma el control, y lo que la primera
  no había guardado queda en su borrador de recuperación.
- Un reporte editado sin conexión se sincroniza después de que otra persona lo cambió: se fusiona sin perder nada.
- Historial: ver una versión y restaurarla.

Capturas en `ITT_CAPTURAS` (si se define).
"""
from __future__ import annotations

import os
import uuid

import pytest
from django.contrib.staticfiles import finders
from django.core.management import call_command
from playwright.sync_api import expect

from itt_herramientas.comun import documentos, perfiles
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.models import BorradorRecuperacion, Cotizacion, Reporte, VersionDocumento
from itt_herramientas.reportes import servicio as rep

from .test_pwa_navegador import TIEMPO, contexto, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")


@pytest.fixture
def produccion(settings, tmp_path_factory, monkeypatch):
    """Estáticos como en producción (nombres con huella, `manifest_strict`) y DEBUG apagado. El servidor de pruebas sirve los archivos
    recolectados (con huella) agregando STATIC_ROOT a los buscadores."""
    monkeypatch.setattr(documentos, "LATIDO_SEGUNDOS", 2)              # el editor late cada 2 s (15 en producción): la prueba no espera
    if os.environ.get("TV_PRUEBAS_PRODUCCION") == "1":                  # ya lo pone `_modo_produccion` (conftest) para toda la sesión
        yield settings.STATIC_ROOT
        return
    raiz = tmp_path_factory.mktemp("estaticos")
    settings.DEBUG = False
    settings.STATIC_ROOT = raiz
    settings.STORAGES = {**settings.STORAGES, "staticfiles": {"BACKEND": "itt_herramientas.pruebas.test_estaticos_produccion.AlmacenDePrueba"}}
    call_command("collectstatic", interactive=False, verbosity=0)
    previos = list(getattr(settings, "STATICFILES_DIRS", []))
    settings.STATICFILES_DIRS = [*previos, str(raiz)]
    finders.get_finder.cache_clear()
    yield raiz
    # Los buscadores se guardan en caché con la lista de carpetas: se devuelve la lista y se vacía la caché aquí mismo, antes de que
    # `settings` restaure lo demás, para que ninguna prueba siguiente sirva estáticos desde esta copia.
    settings.STATICFILES_DIRS = previos
    finders.get_finder.cache_clear()


@pytest.fixture
def equipo(crear_usuario, roles):
    def persona(usuario, nombre):
        u = crear_usuario(usuario)
        u.first_name, u.last_name = nombre.split(" ", 1)
        u.save()
        roles.conceder(u, "herramientas", "cotizaciones", "reportes")
        return u
    return persona("ana", "Ana López"), persona("beto", "Beto Sáenz")


def captura(pagina, nombre, motor):
    if CAPTURAS:
        os.makedirs(CAPTURAS, exist_ok=True)
        pagina.wait_for_timeout(450)                                     # que terminen las animaciones de las hojas
        pagina.screenshot(path=f"{CAPTURAS}/{motor['nombre']}-{nombre}.png", full_page=False)


def abrir(ctx, servidor, ruta, listo):
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    if os.environ.get("ITT_DEPURAR"):
        pagina.on("console", lambda m: print("CON", m.type, m.text[:300]))
        pagina.on("pageerror", lambda e: print("ERR", str(e)[:300]))
        pagina.on("response", lambda r: r.status >= 400 and print("HTTP", r.status, r.url[-90:]))
    pagina.goto(servidor.url + ruta)
    try:
        pagina.wait_for_selector(listo, timeout=TIEMPO)
    except Exception:
        if os.environ.get("ITT_DEPURAR"):
            print("URL", pagina.url)
            print("HTML", pagina.evaluate("document.body ? document.body.innerText.slice(0, 1500) : ''"))
        raise
    return pagina


def editable(pagina, zona):
    return pagina.evaluate(f"!document.querySelector('{zona}').inert")


def sin_desborde(pagina):
    assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "la página se desborda horizontalmente"


def _cotizacion(usuario, titulo):
    d = perfiles.nueva_cotizacion(perfiles.perfil_activo(usuario)).to_dict()
    d.update(project_title=titulo, client_name="Agrícola Norte", items=[{"title": "Levantamiento", "unit": "Lote", "quantity": 1, "unit_price": 12500}], images=[])
    return cot.sincronizar(usuario, uuid.uuid4(), None, None, d, "grupo_itt")


def test_dos_personas_la_misma_cotizacion_y_tomar_el_control(motor, tmp_path, servidor, sesion_de, equipo, produccion):
    ana, beto = equipo
    a = _cotizacion(ana, "Deslinde Rancho Viejo")
    ruta = f"/cotizaciones/#/s/{a['servidor_id']}"
    ctx_a = contexto(motor, tmp_path / "ana", servidor, sesion_de(ana))
    ctx_b = contexto(motor, tmp_path / "beto", servidor, sesion_de(beto))
    try:
        pa = abrir(ctx_a, servidor, ruta, "#cot-vista-editor:not([hidden])")
        assert "/static/herramientas/offline/compartido." in pa.content()                     # el estático con huella (producción)
        esperar(pa, "!document.querySelector('.cot-formulario').inert")
        expect(pa.locator("#f-quote_number")).to_have_value(a["folio"])
        assert pa.locator("#f-quote_number").get_attribute("readonly") is not None            # el folio lo asigna el servidor
        # Beto la abre: solo lectura, con quién la edita y desde cuándo.
        pb = abrir(ctx_b, servidor, ruta, "#cot-vista-editor:not([hidden])")
        expect(pb.locator("#cot-bloqueo")).to_be_visible(timeout=TIEMPO)
        expect(pb.locator("#cot-bloqueo")).to_contain_text("La está editando Ana López desde las")
        assert not editable(pb, ".cot-formulario")
        expect(pb.locator("#cot-autor")).to_contain_text("Ana López")
        sin_desborde(pb)
        captura(pb, "01-solo-lectura", motor)
        # Ana escribe, pero su cambio todavía no llega al servidor (se corta su subida justo antes de que Beto tome el control).
        # (Se corta en `fetch`: en WebKit las peticiones que pasan por el service worker no las ve `page.route`.)
        pa.evaluate("""() => { window.__fetch = window.fetch.bind(window); window.fetch = (u, o) => (/\\/cotizaciones\\/api\\/sincronizar\\//.test(String((u && u.url) || u)) ? Promise.reject(new TypeError('Failed to fetch')) : window.__fetch(u, o)); }""")
        pa.fill("#f-project_title", "Cambio de Ana sin guardar")
        pa.wait_for_timeout(700)
        # Beto toma el control explícitamente.
        pb.locator("#cot-tomar-control").click()
        expect(pb.locator("#confirmar-texto")).to_contain_text("Ana López dejará de editarla")
        captura(pb, "02-tomar-el-control", motor)
        pb.locator("#confirmar-si").click()
        esperar(pb, "!document.querySelector('.cot-formulario').inert")
        expect(pb.locator("#cot-bloqueo")).to_be_hidden()
        # A Ana se le dice en su siguiente latido; lo que no había subido queda en su borrador de recuperación.
        pa.evaluate("() => { window.fetch = window.__fetch; }")
        pa.evaluate("ITT.subirAhora && ITT.subirAhora()")
        expect(pa.locator("#confirmar-texto")).to_contain_text("Beto Sáenz tomó el control", timeout=TIEMPO)
        captura(pa, "03-perdio-el-control", motor)
        pa.locator("#confirmar-si").click()
        esperar(pa, "document.querySelector('.cot-formulario').inert")
        expect(pa.locator("#cot-bloqueo")).to_contain_text("La está editando Beto Sáenz")
        esperar(pa, "ITT.cola.resumen().then(x => x.total === 0)", timeout=TIEMPO * 2)
        assert BorradorRecuperacion.objects.get(cuenta_id=ana.pk).datos["project_title"] == "Cambio de Ana sin guardar"
        assert Cotizacion.objects.get(pk=a["servidor_id"]).proyecto == "Deslinde Rancho Viejo"
        expect(pa.locator("#f-project_title")).to_have_value("Deslinde Rancho Viejo")
        expect(pa.locator("#cot-recuperacion")).to_be_visible(timeout=TIEMPO)
        expect(pa.locator("#cot-recuperacion")).to_contain_text("Tienes cambios sin guardar")
        captura(pa, "04-borrador-de-recuperacion", motor)
        # Beto edita; el cambio llega al servidor con su nombre.
        pb.fill("#f-client_name", "Agrícola del Norte")
        esperar(pb, f"fetch('/cotizaciones/api/{a['servidor_id']}/').then(r => r.json()).then(d => d.cliente === 'Agrícola del Norte')", timeout=TIEMPO * 2)
        c = Cotizacion.objects.get(pk=a["servidor_id"])
        assert c.modificado_por_nombre == "Beto Sáenz" and c.folio == a["folio"]
        # Beto sale de la cotización: el bloqueo se suelta y Ana vuelve a editar sola, ya con lo de Beto.
        pb.goto(servidor.url + "/cotizaciones/#/")
        esperar(pa, "!document.querySelector('.cot-formulario').inert", timeout=TIEMPO * 2)
        expect(pa.locator("#f-client_name")).to_have_value("Agrícola del Norte")
        expect(pa.locator("#cot-autor")).to_contain_text("Modificada por Beto Sáenz")
        # La lista de Beto dice quién la hizo y quién la modificó.
        pb.wait_for_selector("#cot-lista .cot-tarjeta", timeout=TIEMPO)
        expect(pb.locator("#cot-lista .cot-tarjeta").first).to_contain_text("Ana López")
        sin_desborde(pb)
        captura(pb, "05-lista-del-equipo", motor)
    finally:
        ctx_a.close()
        ctx_b.close()


def test_reporte_sin_conexion_se_fusiona_con_lo_que_otro_cambio(motor, tmp_path, servidor, sesion_de, equipo, produccion):
    ana, beto = equipo
    r = rep.sincronizar(beto, uuid.uuid4(), None, None, {"titulo": "Visita de obra", "cliente": "", "fecha": "2026-11-13", "fotos": []}, "grupo_itt")
    ctx = contexto(motor, tmp_path / "ana", servidor, sesion_de(ana))
    try:
        pagina = abrir(ctx, servidor, "/reportes/", "html[data-rep-lista]")
        pagina.wait_for_selector("#rep-lista .cot-tarjeta", timeout=TIEMPO)              # el reporte de Beto ya está en el teléfono de Ana
        expect(pagina.locator("#rep-lista")).to_contain_text("Beto Sáenz")
        # Ana lo abre una vez con señal: desde ahí se guarda en su teléfono (los que no abre, solo los conoce por su resumen).
        pagina.locator("#rep-lista .cot-tarjeta__enlace").first.click()
        pagina.wait_for_selector("#rep-vista-editor:not([hidden])")
        esperar(pagina, "!document.querySelector('#rep-paso-datos').inert")
        pagina.locator("#rep-volver").click()
        pagina.wait_for_selector("#rep-vista-lista:not([hidden])")
        # Se queda sin señal en campo y edita el cliente.
        ctx.set_offline(True)
        pagina.locator("#rep-lista .cot-tarjeta__enlace").first.click()
        pagina.wait_for_selector("#rep-vista-editor:not([hidden])")
        assert editable(pagina, "#rep-paso-datos")                                      # sin conexión se edita en el teléfono
        pagina.fill("#r-cliente", "Constructora Sur")
        pagina.wait_for_timeout(700)
        # Mientras, en la oficina, Beto cambia el título.
        rep.sincronizar(beto, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 1, {"titulo": "Visita de obra (revisada)", "cliente": "", "fecha": "2026-11-13", "fotos": []}, "grupo_itt")
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora && ITT.subirAhora()")
        esperar(pagina, "ITT.cola.resumen().then(x => x.total === 0)", timeout=TIEMPO * 2)
        hecho = Reporte.objects.get(pk=r["servidor_id"])
        assert (hecho.titulo, hecho.cliente, hecho.revision) == ("Visita de obra (revisada)", "Constructora Sur", 3)
        assert Reporte.objects.count() == 1                                             # sin copia: no chocaba
        assert VersionDocumento.objects.get(documento_id=hecho.pk, revision=3).nota.startswith("Fusionada")
        expect(pagina.locator("#r-titulo")).to_have_value("Visita de obra (revisada)", timeout=TIEMPO)
        expect(pagina.locator("#r-cliente")).to_have_value("Constructora Sur")
        expect(pagina.locator("#rep-autor")).to_contain_text("Modificado por Ana López")            # quien guardó la fusión
        sin_desborde(pagina)
        captura(pagina, "06-reporte-fusionado", motor)
    finally:
        ctx.close()


def test_historial_ver_y_restaurar(motor, tmp_path, servidor, sesion_de, equipo, produccion):
    from datetime import timedelta

    from django.utils import timezone

    ana, beto = equipo
    a = _cotizacion(ana, "Primera versión")
    pk = a["servidor_id"]
    VersionDocumento.objects.filter(documento_id=pk).update(inicio=timezone.now() - timedelta(hours=1))
    datos = Cotizacion.objects.get(pk=pk).datos
    cot.sincronizar(beto, uuid.uuid4(), uuid.UUID(pk), 1, {**datos, "project_title": "Segunda versión"}, "grupo_itt")
    ctx = contexto(motor, tmp_path / "ana", servidor, sesion_de(ana))
    try:
        pagina = abrir(ctx, servidor, f"/cotizaciones/#/s/{pk}", "#cot-vista-editor:not([hidden])")
        esperar(pagina, "!document.querySelector('.cot-formulario').inert")
        expect(pagina.locator("#f-project_title")).to_have_value("Segunda versión")
        pagina.locator("#cot-mas").click()
        pagina.locator("#mas-historial").click()
        expect(pagina.locator("#dlg-historial")).to_be_visible()
        expect(pagina.locator(".itt-historial__fila")).to_have_count(2, timeout=TIEMPO)
        expect(pagina.locator(".itt-historial__fila").first).to_contain_text("Beto Sáenz")
        sin_desborde(pagina)
        captura(pagina, "07-historial", motor)
        pagina.locator('.itt-historial__fila[data-version="1"] button').click()
        expect(pagina.locator(".itt-historial__vista")).to_contain_text("Primera versión")
        captura(pagina, "08-version-de-solo-lectura", motor)
        pagina.locator("#historial-restaurar").click()
        pagina.locator("#confirmar-si").click()
        expect(pagina.locator("#f-project_title")).to_have_value("Primera versión", timeout=TIEMPO)
        c = Cotizacion.objects.get(pk=pk)
        assert c.revision == 3 and c.proyecto == "Primera versión" and c.folio == a["folio"]
        assert VersionDocumento.objects.get(documento_id=pk, revision=3).nota.startswith("Restaurada de la versión 1")
        captura(pagina, "09-restaurada", motor)
    finally:
        ctx.close()


def test_folios_repetidos_y_papelera(motor, tmp_path, servidor, sesion_de, equipo, crear_usuario, produccion):
    """El propietario ve los folios repetidos de antes de compartir y decide; la papelera recupera lo eliminado."""
    from django.utils import timezone

    ana, beto = equipo
    propietario = crear_usuario("propietario", superusuario=True)
    a = _cotizacion(ana, "Deslinde Rancho Viejo")
    b = _cotizacion(beto, "Topografía Bodega Norte")
    Cotizacion.objects.filter(pk=b["servidor_id"]).update(folio=a["folio"], folio_repetido=True)       # como quedaron tras la migración
    r = rep.sincronizar(ana, uuid.uuid4(), None, None, {"titulo": "Visita eliminada", "cliente": "", "fecha": "2026-11-13", "fotos": []}, "grupo_itt")
    Reporte.objects.filter(pk=r["servidor_id"]).update(eliminado=timezone.now(), eliminado_por_id=ana.pk, eliminado_por_nombre="Ana López")
    ctx = contexto(motor, tmp_path / "dueno", servidor, sesion_de(propietario))
    try:
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/supervision/")
        expect(pagina.locator("#sup-repetidos")).to_contain_text(a["folio"])
        expect(pagina.locator("#sup-repetidos")).to_contain_text("Topografía Bodega Norte")
        sin_desborde(pagina)
        captura(pagina, "10-folios-repetidos", motor)
        with pagina.expect_navigation(timeout=TIEMPO):
            pagina.get_by_role("button", name="Asignar folio nuevo").click()
        nuevo = Cotizacion.objects.get(pk=b["servidor_id"])
        assert not nuevo.folio_repetido and nuevo.folio != a["folio"]
        expect(pagina.locator(".tv-mensajes")).to_contain_text(f"ahora es {nuevo.folio}")
        expect(pagina.locator("#sup-repetidos")).to_have_count(0)
        pagina.goto(servidor.url + "/documentos/papelera/")
        expect(pagina.locator("#papelera")).to_contain_text("Visita eliminada")
        sin_desborde(pagina)
        captura(pagina, "11-papelera", motor)
        with pagina.expect_navigation(timeout=TIEMPO):
            pagina.get_by_role("button", name="Recuperar").click()
        assert Reporte.objects.get(pk=r["servidor_id"]).eliminado is None
        expect(pagina.locator(".tv-mensajes")).to_contain_text("volvió a su herramienta")
    finally:
        ctx.close()
