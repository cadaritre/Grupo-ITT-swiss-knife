"""Cotizaciones en el navegador móvil (Chromium/Pixel y WebKit/iPhone): borradores sin conexión, totales idénticos a Python,
copia por conflicto, ortografía, fotos de referencia y PDF. Cada prueba corre en los dos motores."""
from __future__ import annotations

import json
import os
import time

import pytest
from playwright.sync_api import expect

from itt_herramientas.dominio.quotation_models import QuoteData
from itt_herramientas.models import Captura, Cotizacion

from .conftest import foto
from .test_pwa_navegador import TIEMPO, contexto, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def usuaria(crear_usuario, roles):
    u = crear_usuario("ana")
    roles.conceder(u, "herramientas", "cotizaciones")
    return u


def abrir(ctx, servidor, ruta="/cotizaciones/"):
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    if os.environ.get("ITT_DEPURAR"):
        pagina.on("console", lambda m: print("CON", m.type, m.text[:300]))
        pagina.on("pageerror", lambda e: print("ERR", str(e)[:300]))
        pagina.on("requestfailed", lambda r: print("FAIL", r.method, r.url[-70:], r.failure))
    pagina.goto(servidor.url + ruta)
    pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
    return pagina


def con_service_worker(pagina):
    pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
    if not pagina.evaluate("!!navigator.serviceWorker.controller"):
        pagina.reload()
        pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
        esperar(pagina, "!!navigator.serviceWorker.controller", timeout=TIEMPO)


def sin_pendientes(pagina, timeout=TIEMPO * 2):
    esperar(pagina, "ITT.cola.resumen().then(r => r.total === 0)", timeout=timeout)


def borradores(pagina):
    return pagina.evaluate("ITT.cola.borradores()")


def sin_desborde(pagina):
    assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "la página se desborda horizontalmente"


def poner(pagina, selector, texto):
    pagina.fill(selector, texto)


def agregar_concepto(pagina, titulo, cantidad, precio, descripcion="Descripción del trabajo"):
    pagina.locator("#cot-agregar").click()
    expect(pagina.locator("#dlg-concepto")).to_be_visible()
    pagina.fill("#c-title", titulo)
    pagina.fill("#c-description", descripcion)
    pagina.fill("#c-subitems", "Primer alcance\nSegundo alcance")
    pagina.fill("#c-quantity", str(cantidad))
    pagina.fill("#c-unit_price", str(precio))
    pagina.locator("#dlg-concepto button[type=submit]").click()
    expect(pagina.locator("#dlg-concepto")).to_be_hidden()


def agregar_de_machote(pagina, indice):
    pagina.locator("#cot-desde-machote").click()
    expect(pagina.locator("#dlg-plantillas")).to_be_visible()
    pagina.locator("#dlg-plantillas .cot-plantilla").nth(indice).click()
    expect(pagina.locator("#dlg-plantillas")).to_be_hidden()


def datos_de(pagina, indice=0):
    return sorted(borradores(pagina), key=lambda b: b["creada"])[indice]["datos"]


def total_python(datos: dict) -> float:
    return QuoteData.from_dict(datos).total


def test_quince_conceptos_sin_conexion_duplicar_cerrar_y_sincronizar(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        sin_desborde(pagina)
        ctx.set_offline(True)
        # --- Todo sin conexión ---
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        pagina.fill("#f-client_name", "Constructora del Norte S.A.")
        pagina.fill("#f-project_title", "Levantamiento de predio")
        pagina.fill("#f-contact", "Ing. Pérez")
        pagina.fill("#f-phone", "6141234567")
        pagina.fill("#f-location", "Chihuahua")
        for i in range(10):
            agregar_de_machote(pagina, i)                              # machotes de fábrica o propios, sin red
        for n in range(5):
            agregar_concepto(pagina, f"Concepto manual {n + 1}", n + 1.5, 1234.567 * (n + 1))
        assert pagina.locator("#cot-conceptos > li").count() == 15
        # Los totales de la pantalla son los de Python para esos mismos datos.
        esperar(pagina, "document.documentElement.dataset.cotTotal !== undefined && ITT.cola.borradores().then(b => b.length === 1)")
        pagina.wait_for_timeout(600)                                   # el guardado diferido (300 ms) ya escribió
        datos = datos_de(pagina)
        esperado = total_python(datos)
        assert float(pagina.evaluate("document.documentElement.dataset.cotTotal")) == esperado
        texto_total = pagina.locator("#cot-total").inner_text()
        assert texto_total == f"$ {esperado:,.2f}"
        sin_desborde(pagina)
        # Contador de pendientes: el borrador sin subir cuenta.
        assert pagina.evaluate("ITT.cola.resumen().then(r => r.borradores)") == 1
        # --- Duplicar y editar el duplicado, sin conexión ---
        pagina.locator("#cot-mas").click()
        pagina.locator("#mas-duplicar").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        esperar(pagina, "ITT.cola.borradores().then(b => b.length === 2)")
        pagina.fill("#f-project_title", "Levantamiento de predio (segunda propuesta)")
        pagina.locator("#cot-conceptos .cot-concepto__abrir").first.click()
        pagina.fill("#c-unit_price", "99.995")
        pagina.locator("#dlg-concepto button[type=submit]").click()
        pagina.wait_for_timeout(600)
        copia = datos_de(pagina, 1)
        assert copia["quote_number"] != datos["quote_number"] and copia["items"][0]["unit_price"] == 99.995
        assert float(pagina.evaluate("document.documentElement.dataset.cotTotal")) == total_python(copia)
        assert len(copia["items"]) == 15 and copia["spelling_checked"] is False
        # --- Cierre forzado y reapertura SIN conexión ---
        ctx.close()
        ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
        ctx.set_offline(True)
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/cotizaciones/")
        pagina.wait_for_selector("html[data-cot-lista]", timeout=TIEMPO)
        expect(pagina.locator("#cot-lista > li")).to_have_count(2)
        assert pagina.evaluate("ITT.cola.resumen().then(r => r.borradores)") == 2
        # --- Vuelve la señal: sincroniza ---
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora()")
        sin_pendientes(pagina)
        filas = {str(f.id): f for f in Cotizacion.objects.all()}
        assert len(filas) == 2
        locales = borradores(pagina)
        assert {b["servidor_id"] for b in locales} == set(filas)
        for local in locales:
            fila = filas[local["servidor_id"]]
            assert local["sucio"] is False
            # Lo guardado en el servidor es lo editado en el teléfono (el servidor solo normaliza 1 -> 1.0).
            assert QuoteData.from_dict(fila.datos).to_dict() == QuoteData.from_dict(local["datos"]).to_dict()
            assert float(fila.total) == total_python(fila.datos)
        assert len({f.folio for f in filas.values()}) == 2
        # El total que se mostraba sin conexión es el que guardó el servidor.
        assert any(f"$ {float(f.total):,.2f}" == texto_total for f in filas.values())
    finally:
        ctx.close()


def test_conflicto_guarda_copia_y_no_pisa(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        pagina.fill("#f-project_title", "Proyecto original")
        agregar_concepto(pagina, "Uno", 1, 100)
        sin_pendientes(pagina)
        original = Cotizacion.objects.get()
        # El teléfono todavía no se entera del cambio de la oficina: sin esto, el ciclo de sincronización bajaría la versión nueva
        # antes de editar (y entonces no habría conflicto, que también es correcto, pero no es lo que esta prueba verifica).
        pagina.evaluate("""() => { const f0 = window.fetch.bind(window); window.fetch = (u, o) => (/\\/cotizaciones\\/api\\/lista\\//.test(String((u && u.url) || u)) ? Promise.reject(new TypeError('Failed to fetch')) : f0(u, o)); }""")
        # Otro dispositivo cambia la cotización en el servidor.
        datos = dict(original.datos, project_title="Cambiado en la oficina")
        Cotizacion.objects.filter(pk=original.pk).update(datos=datos, revision=2)
        # El teléfono edita partiendo de la revisión 1.
        pagina.fill("#f-client_name", "Cliente desde el teléfono")
        pagina.locator("#confirmar-si").wait_for(state="visible", timeout=TIEMPO * 2)
        expect(pagina.locator("#confirmar-texto")).to_contain_text("se guardaron como una copia")
        pagina.locator("#confirmar-si").click()
        sin_pendientes(pagina)
        assert Cotizacion.objects.count() == 2
        original.refresh_from_db()
        assert original.datos["project_title"] == "Cambiado en la oficina" and original.revision == 2
        copia = Cotizacion.objects.exclude(pk=original.pk).get()
        assert copia.datos["client_name"] == "Cliente desde el teléfono" and copia.copia_de_id == original.pk
        assert copia.datos["project_title"].endswith("(desde el teléfono)")
        # El teléfono quedó con las dos, y la abierta es la copia.
        locales = {b["servidor_id"] for b in borradores(pagina)}
        assert locales == {str(original.pk), str(copia.pk)}
        assert str(copia.pk) in pagina.url
    finally:
        ctx.close()


def test_ortografia_paso_a_paso_con_diccionario(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        pagina.fill("#f-project_title", "Levantamiento topografico xyzzyq")
        pagina.locator("#paso-condiciones summary").click()
        pagina.fill("#f-notes", "Se requiere anticipo xyzzyq para programar.")
        assert pagina.locator("#cot-ortografia-estado").inner_text().startswith("⚠")
        pagina.locator("#cot-ortografia").click()
        expect(pagina.locator("#dlg-ortografia")).to_be_visible(timeout=TIEMPO)
        expect(pagina.locator("#o-palabra")).to_have_text("topografico")
        pagina.fill("#o-reemplazo", "topográfico")
        pagina.locator("#o-cambiar").click()
        expect(pagina.locator("#o-palabra")).to_have_text("xyzzyq")
        pagina.locator("#o-agregar").click()                           # al diccionario: ignora también la segunda aparición
        expect(pagina.locator("#dlg-ortografia")).to_be_hidden(timeout=TIEMPO)
        assert pagina.input_value("#f-project_title") == "Levantamiento topográfico xyzzyq"
        assert pagina.locator("#cot-ortografia-estado").inner_text().startswith("✓")
        # Editar un texto vuelve a marcarla pendiente.
        pagina.fill("#f-notes", "Se requiere anticipo para programar los trabajos.")
        assert pagina.locator("#cot-ortografia-estado").inner_text().startswith("⚠")
        pagina.wait_for_timeout(600)
        assert datos_de(pagina)["spelling_checked"] is False
    finally:
        ctx.close()


def test_pdf_con_vista_previa_y_compartir(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria), accept_downloads=True)
    try:
        pagina = abrir(ctx, servidor)
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        pagina.fill("#f-client_name", "Cliente")
        pagina.fill("#f-project_title", "Proyecto")
        # Sin conceptos no se exporta (igual que el escritorio).
        pagina.locator("#cot-pdf").click()
        expect(pagina.locator("#cot-mensaje")).to_contain_text("al menos un concepto")
        agregar_concepto(pagina, "Levantamiento", 2, 1500)
        pagina.locator("#cot-pdf").click()
        expect(pagina.locator("#confirmar-texto")).to_contain_text("revisión ortográfica")     # ortografía pendiente
        pagina.locator("#confirmar-si").click()
        expect(pagina.locator("#dlg-pdf")).to_be_visible()
        try:
            esperar(pagina, "document.querySelectorAll('#pdf-paginas canvas').length >= 1", timeout=TIEMPO * 4)
        except AssertionError as exc:
            raise AssertionError(f"{exc} · estado del PDF: {pagina.locator('#pdf-estado').inner_text()!r}") from None
        assert pagina.locator("#pdf-compartir").is_enabled()
        sin_desborde(pagina)
        assert "COTIZACION " in pagina.locator("#pdf-estado").inner_text()
        # Las páginas no están en blanco: el lienzo tiene tinta.
        assert pagina.evaluate("""() => { const c = document.querySelector('#pdf-paginas canvas'); const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            let oscuros = 0; for (let i = 0; i < d.length; i += 4) if (d[i] < 128) oscuros++; return oscuros > 200; }""")
        with pagina.expect_download() as descarga:
            pagina.locator("#pdf-descargar").click()
        assert descarga.value.suggested_filename.startswith("COTIZACION ")
        ruta = tmp_path / "descargada.pdf"
        descarga.value.save_as(str(ruta))
        assert ruta.read_bytes().startswith(b"%PDF")
        # Compartir: donde el navegador no puede compartir archivos se descarga (Web Share con archivos no existe en todos).
        pagina.locator("#pdf-compartir").click()
        esperar(pagina, "!!document.documentElement.dataset.cotCompartido")
        assert pagina.evaluate("document.documentElement.dataset.cotCompartido") in {"descargado", "compartido"}
    finally:
        ctx.close()


def test_foto_de_referencia_sin_conexion_llega_y_sale_en_el_pdf(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        agregar_concepto(pagina, "Con foto", 1, 10)
        pagina.locator("#paso-imagenes summary").click()
        ctx.set_offline(True)
        pagina.set_input_files("#cot-foto-galeria", [{"name": "vista.jpg", "mimeType": "image/jpeg", "buffer": foto((1600, 1000))}])
        esperar(pagina, "document.querySelectorAll('#cot-imagenes > li').length === 1")
        pagina.wait_for_timeout(600)
        sha = datos_de(pagina)["images"][0]["path"]
        assert sha.startswith("captura:") and len(sha) == 72
        assert Captura.objects.count() == 0
        assert pagina.evaluate("ITT.cola.resumen().then(r => r.items.length)") == 1
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora()")
        sin_pendientes(pagina)
        assert Captura.objects.get().sha256 == sha[8:]
        # El PDF ya incluye la foto: no quedan imágenes pendientes.
        respuesta = pagina.evaluate("""async () => { const r = await ITT.cot.pedir(ITT.cot.urls.pdf, {metodo: 'POST', crudo: true, json: {datos: (await ITT.cola.borradores())[0].datos}}); return r.headers.get('X-Imagenes-Pendientes'); }""")
        assert respuesta == "0"
    finally:
        ctx.close()


def test_pantallas_sin_desborde_y_contador(motor, tmp_path, servidor, sesion_de, usuaria):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        sin_desborde(pagina)
        pagina.locator("a[href='#/machotes']").first.click()
        pagina.wait_for_selector("#cot-vista-machotes:not([hidden])")
        sin_desborde(pagina)
        pagina.goto(servidor.url + "/cotizaciones/#/")
        pagina.locator("#cot-nueva").click()
        pagina.wait_for_selector("#cot-vista-editor:not([hidden])")
        for paso in ("#paso-condiciones", "#paso-imagenes"):
            pagina.locator(f"{paso} summary").click()
        sin_desborde(pagina)
        # Objetivos táctiles de al menos 44 px en la barra inferior.
        for selector in ("#cot-ortografia", "#cot-mas", "#cot-pdf", "#cot-agregar"):
            caja = pagina.locator(selector).bounding_box()
            assert caja and caja["height"] >= 43.5, selector
    finally:
        ctx.close()
