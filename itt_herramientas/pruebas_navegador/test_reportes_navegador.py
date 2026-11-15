"""Reportes fotográficos en el navegador móvil (Chromium/Pixel y WebKit/iPhone): captura sin conexión con hora, ubicación y nota,
orden, cierre y reapertura, subida reanudable y PDF con vista previa. Cada prueba corre en los dos motores."""
from __future__ import annotations

import io
import json
import os

import pytest
from playwright.sync_api import expect

from itt_herramientas.models import Captura, Reporte

from .conftest import foto
from .test_pwa_navegador import TIEMPO, contexto, esperar, fallar, sin_fallas

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
UBICACION = {"latitude": 28.6330, "longitude": -106.0691, "accuracy": 8}


@pytest.fixture
def reportera(crear_usuario, roles):
    u = crear_usuario("rita")
    roles.conceder(u, "herramientas", "reportes")
    return u


def abrir(ctx, servidor, ruta="/reportes/"):
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    if os.environ.get("ITT_DEPURAR"):
        pagina.on("console", lambda m: print("CON", m.type, m.text[:300]))
        pagina.on("pageerror", lambda e: print("ERR", str(e)[:300]))
    pagina.goto(servidor.url + ruta)
    pagina.wait_for_selector("html[data-rep-lista]", timeout=TIEMPO)
    pagina.evaluate("() => { ITT.tus.tiempos.corto = 4000; ITT.tus.tiempos.trozo = 8000; }")
    return pagina


def con_service_worker(pagina):
    pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
    if not pagina.evaluate("!!navigator.serviceWorker.controller"):
        pagina.reload()
        pagina.wait_for_selector("html[data-rep-lista]", timeout=TIEMPO)
        esperar(pagina, "!!navigator.serviceWorker.controller", timeout=TIEMPO)


def sin_pendientes(pagina, timeout=TIEMPO * 3):
    esperar(pagina, "ITT.cola.resumen().then(r => r.total === 0)", timeout=timeout)


def sin_desborde(pagina):
    assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "la página se desborda horizontalmente"


def jpeg(n, gps=False, fecha="2026:11:13 09:30:00"):
    return foto((1200, 800), color=f"#{(n * 37) % 200 + 30:02x}{(n * 91) % 200 + 30:02x}{(n * 53) % 200 + 30:02x}", gps=gps, fecha=fecha.replace("30:00", f"{n % 60:02d}:00"))


def tomar(pagina, n, **kw):
    base = int(pagina.evaluate("document.documentElement.dataset.guardadas || '0'"))
    pagina.set_input_files("#rep-camara", [{"name": f"IMG_{n}.jpg", "mimeType": "image/jpeg", "buffer": jpeg(n, **kw)}])
    esperar(pagina, f"Number(document.documentElement.dataset.guardadas || 0) >= {base + 1}", timeout=TIEMPO)


def elegir(pagina, numeros, **kw):
    base = int(pagina.evaluate("document.documentElement.dataset.guardadas || '0'"))
    pagina.set_input_files("#rep-galeria", [{"name": f"G_{n}.jpg", "mimeType": "image/jpeg", "buffer": jpeg(n, **kw)} for n in numeros])
    esperar(pagina, f"Number(document.documentElement.dataset.guardadas || 0) >= {base + len(numeros)}", timeout=TIEMPO * 4)


def nuevo_reporte(pagina, titulo="Avance de obra"):
    pagina.locator("#rep-nuevo").click()
    pagina.wait_for_selector("#rep-vista-editor:not([hidden])")
    pagina.fill("#r-titulo", titulo)


def fotos_locales(pagina):
    return pagina.evaluate("ITT.cola.borradores().then(b => b.find(x => x.tipo === 'reporte').datos.fotos)")


def test_cuarenta_fotos_sin_conexion_notas_cierre_subida_y_pdf(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera), accept_downloads=True, geolocation=UBICACION, permissions=["geolocation"])
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        sin_desborde(pagina)
        ctx.set_offline(True)
        nuevo_reporte(pagina)
        pagina.locator("#rep-ubicacion").click()
        esperar(pagina, "/Ubicación activa/.test(document.querySelector('#rep-geo').textContent)", timeout=TIEMPO)
        # 10 con la cámara (con la ubicación del teléfono) y 30 de la galería (sin GPS en el EXIF).
        for n in range(1, 11):
            tomar(pagina, n)
        elegir(pagina, range(11, 41))
        assert pagina.locator("#rep-fotos > li").count() == 40
        # Notas en 10 fotos.
        for i in range(10):
            pagina.fill(f"#nota-{i * 4}", f"Nota de la foto {i * 4 + 1}")
        # Reordenar: la última sube una posición; ubicar una de la galería a mano.
        sha_ultima = fotos_locales(pagina)[39]["sha256"]
        pagina.locator("#rep-fotos > li").nth(39).locator("[data-itt=mover]").click()
        pagina.fill("#mover-posicion", "39")
        pagina.locator("#form-mover button[type=submit]").click()
        esperar(pagina, f"ITT.cola.borradores().then(b => b.find(x => x.tipo === 'reporte').datos.fotos[38].sha256 === '{sha_ultima}')")
        pagina.locator("#rep-fotos > li").nth(20).locator("[data-itt=ubicar]").click()
        expect(pagina.locator("#dlg-ubicar")).to_be_visible()
        pagina.fill("#u-lat", "28.640001")
        pagina.fill("#u-lon", "-106.08")
        pagina.locator("#u-guardar").click()
        expect(pagina.locator("#dlg-ubicar")).to_be_hidden()
        pagina.wait_for_timeout(700)
        locales = fotos_locales(pagina)
        assert locales[20]["lat"] == 28.640001 and locales[20]["lon"] == -106.08 and locales[20]["sin_ubicacion"] is False
        assert all(f["lat"] == pytest.approx(28.633, abs=1e-4) for f in locales[:10])         # las de cámara llevan la ubicación del teléfono
        assert all(f["tomada"] for f in locales)
        assert sum(1 for f in locales if f["nota"]) == 10
        assert pagina.evaluate("ITT.cola.resumen().then(r => r.items.length)") == 40
        expect(pagina.locator("#rep-resumen-barra")).to_contain_text("40 fotos · 40 por subir")
        sin_desborde(pagina)
        antes = [(f["sha256"], f["nota"], f["tomada"], f["lat"], f["lon"]) for f in locales]
        # --- Cierre forzado y reapertura sin conexión: las 40 siguen, en orden y con hora, ubicación y nota ---
        ctx.close()
        ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera), accept_downloads=True, geolocation=UBICACION, permissions=["geolocation"])
        ctx.set_offline(True)
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/reportes/")
        pagina.wait_for_selector("html[data-rep-lista]", timeout=TIEMPO)
        expect(pagina.locator("#rep-lista > li")).to_have_count(1)
        assert pagina.evaluate("ITT.cola.resumen().then(r => [r.items.length, r.borradores])") == [40, 1]
        pagina.locator("#rep-lista a.cot-tarjeta__enlace").click()
        pagina.wait_for_selector("#rep-vista-editor:not([hidden])")
        assert pagina.locator("#rep-fotos > li").count() == 40
        assert [(f["sha256"], f["nota"], f["tomada"], f["lat"], f["lon"]) for f in fotos_locales(pagina)] == antes
        # --- Vuelve la señal: sube las 40 sin duplicar y el contador llega a 0 ---
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora()")
        sin_pendientes(pagina)
        assert Captura.objects.count() == 40 and len({c.sha256 for c in Captura.objects.all()}) == 40
        r = Reporte.objects.get()
        assert [f["sha256"] for f in r.datos["fotos"]] == [a[0] for a in antes] and r.fotos == 40
        # --- PDF: sin croquis (la primera foto en la portada) = 40 páginas ---
        if not pagina.evaluate("document.getElementById('rep-paso-datos').open"):
            pagina.locator("#rep-paso-datos summary").click()                                # con fotos, los datos del reporte abren plegados
        pagina.locator("#r-croquis").uncheck()
        pagina.locator("#rep-pdf").click()
        expect(pagina.locator("#dlg-pdf")).to_be_visible()
        esperar(pagina, "document.querySelectorAll('#pdf-paginas canvas').length >= 1", timeout=TIEMPO * 6)
        assert pagina.locator("#pdf-compartir").is_enabled()
        with pagina.expect_download() as descarga:
            pagina.locator("#pdf-descargar").click()
        ruta = tmp_path / "reporte.pdf"
        descarga.value.save_as(str(ruta))
        pymupdf = pytest.importorskip("pymupdf")
        with pymupdf.open(str(ruta)) as documento:
            assert documento.page_count == 40
            assert "Nota de la foto 1" in documento[0].get_text() + documento[1].get_text()
        assert descarga.value.suggested_filename == "Avance de obra.pdf"
        pagina.locator("#pdf-compartir").click()
        esperar(pagina, "!!document.documentElement.dataset.repCompartido")
        assert pagina.evaluate("document.documentElement.dataset.repCompartido") in {"descargado", "compartido"}
    finally:
        ctx.close()


def test_sin_permiso_de_ubicacion_las_fotos_van_sin_ubicacion_y_se_ubican_a_mano(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera))                      # sin permiso de geolocalización
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        pagina.locator("#rep-ubicacion").click()
        tomar(pagina, 1)
        tomar(pagina, 2)
        locales = fotos_locales(pagina)
        assert [f["sin_ubicacion"] for f in locales] == [True, True] and all(f["lat"] is None for f in locales)
        assert pagina.locator("#rep-fotos > li[data-ubicada='']").count() == 2
        expect(pagina.locator("#rep-resumen-barra")).to_contain_text("2 sin ubicación")
        pagina.locator("#rep-fotos > li").nth(1).locator("[data-itt=ubicar]").click()
        pagina.fill("#u-lat", "91")
        pagina.fill("#u-lon", "10")
        pagina.locator("#u-guardar").click()
        expect(pagina.locator("#u-error")).to_be_visible()                                  # coordenada inválida: no se guarda
        pagina.fill("#u-lat", "28.5")
        pagina.fill("#u-lon", "-106.1")
        pagina.locator("#u-guardar").click()
        expect(pagina.locator("#dlg-ubicar")).to_be_hidden()
        assert pagina.locator("#rep-fotos > li").nth(1).locator(".rep-ubicacion").inner_text().startswith("28.500000, -106.100000")
        expect(pagina.locator("#rep-resumen-barra")).to_contain_text("1 sin ubicación")
        # Dejar sin ubicación otra vez.
        pagina.locator("#rep-fotos > li").nth(1).locator("[data-itt=ubicar]").click()
        pagina.locator("#u-quitar").click()
        pagina.wait_for_timeout(500)
        assert fotos_locales(pagina)[1]["lat"] is None
    finally:
        ctx.close()


def test_corte_a_mitad_de_la_subida_se_reanuda_sin_duplicar(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera))
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        fallar(pagina, "cortar_datos")
        elegir(pagina, range(1, 6))
        pagina.evaluate("ITT.subirAhora()")
        pagina.wait_for_timeout(1500)
        assert pagina.evaluate("ITT.cola.resumen().then(r => r.items.length)") > 0                  # nada se borra sin confirmación
        sin_fallas(pagina)
        pagina.evaluate("ITT.subirAhora()")
        sin_pendientes(pagina)
        assert Captura.objects.count() == 5 and Reporte.objects.get().fotos == 5
    finally:
        ctx.close()


def test_pdf_espera_a_que_suban_las_fotos(motor, tmp_path, servidor, sesion_de, reportera):
    """«Generar PDF» con fotos aún en el teléfono: espera a que lleguen (no falla) y luego genera."""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera))
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 4))
        pagina.locator("#r-croquis").uncheck()
        pagina.locator("#rep-pdf").click()
        expect(pagina.locator("#dlg-pdf")).to_be_visible()
        esperar(pagina, "document.querySelectorAll('#pdf-paginas canvas').length >= 1", timeout=TIEMPO * 6)
        assert Captura.objects.count() == 3
        assert Reporte.objects.get().salida_revision is not None
    finally:
        ctx.close()


def test_soltar_archivos_en_la_computadora(motor, tmp_path, servidor, sesion_de, reportera):
    if motor["nombre"] != "chromium":
        pytest.skip("El arrastre de archivos sintético solo se verifica en Chromium")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera))
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        import base64
        datos = [base64.b64encode(jpeg(n)).decode() for n in (1, 2, 3)]
        pagina.evaluate("""(datos) => { const dt = new DataTransfer(); datos.forEach((d, i) => dt.items.add(new File([Uint8Array.from(atob(d), c => c.charCodeAt(0))], `D_${i}.jpg`, {type: 'image/jpeg'})));
            document.querySelector('#rep-soltar').dispatchEvent(new DragEvent('drop', {dataTransfer: dt, bubbles: true, cancelable: true})); }""", datos)
        esperar(pagina, "document.querySelectorAll('#rep-fotos > li').length === 3", timeout=TIEMPO * 2)
    finally:
        ctx.close()


def test_diseno_sin_desborde_y_objetivos_tactiles(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(reportera))
    try:
        pagina = abrir(ctx, servidor)
        sin_desborde(pagina)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 4))
        sin_desborde(pagina)
        for selector in ("#rep-pdf", "#rep-mas", "#rep-fotos > li:first-child [data-itt=ubicar]", "#rep-fotos > li:first-child [data-itt=mover]", "#rep-fotos > li:first-child .rep-asa"):
            caja = pagina.locator(selector).bounding_box()
            assert caja and caja["height"] >= 43.5, selector
    finally:
        ctx.close()
