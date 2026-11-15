"""Reportes: fotos por página y orden de las fotos en el navegador (Chromium/Pixel y WebKit/iPhone, a 360 y 1280 px).

Elegir 1, 2, 4 o 6 fotos por página y ver el PDF con esas páginas; arrastrar con el ratón y con el dedo (Escape cancela);
«Mover → Al principio» y «A la posición…»; vista «Ordenar» agrupada como el PDF (la misma regla que el generador) y un
orden hecho sin señal que sigue ahí al recargar y llega al servidor.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from itt_herramientas.dominio.pdf_generator import photo_pages
from itt_herramientas.models import Reporte

from .test_pwa_navegador import TIEMPO, contexto, esperar
from .test_reportes_navegador import abrir, con_service_worker, elegir, fotos_locales, nuevo_reporte, sin_desborde, sin_pendientes

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")


@pytest.fixture
def reportera(crear_usuario, roles):
    u = crear_usuario("rita")
    roles.conceder(u, "herramientas", "reportes")
    return u


def ctx_de(motor, tmp_path, servidor, sesion, ancho, **extra):
    telefono = ancho < 768
    opciones = {"viewport": {"width": ancho, "height": 800}, "is_mobile": telefono, "has_touch": telefono, **extra}
    return contexto(motor, tmp_path, servidor, sesion, **opciones)


def captura(pagina, nombre):
    if CAPTURAS:
        pagina.screenshot(path=os.path.join(CAPTURAS, nombre), full_page=True)


def orden(pagina):
    return [f["sha256"] for f in fotos_locales(pagina)]


def esperar_orden(pagina, esperado):
    esperar(pagina, f"ITT.cola.borradores().then(b => b.find(x => x.tipo === 'reporte').datos.fotos.map(f => f.sha256).join() === {','.join(esperado)!r})")


def abrir_datos(pagina):
    if not pagina.evaluate("document.getElementById('rep-paso-datos').open"):
        pagina.locator("#rep-paso-datos summary").click()


def a_la_vista(pagina, locator, margen=150):
    """Deja `locator` arriba de la ventana con `margen` px libres (fuera de la franja que desplaza la lista sola)."""
    locator.evaluate("(el, m) => { el.scrollIntoView({ block: 'start' }); scrollBy(0, -m); }", margen)
    pagina.wait_for_timeout(100)


def arrastrar_raton(pagina, origen, destino, soltar=True):
    """Arrastra con el ratón desde el centro de `origen` hasta un punto `destino` (x, y) de la ventana."""
    caja = origen.bounding_box()
    pagina.mouse.move(caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2)
    pagina.mouse.down()
    pagina.mouse.move(*destino, steps=12)
    if soltar:
        pagina.mouse.up()


def arrastrar_dedo(motor, ctx, pagina, origen, destino):
    """Arrastre con el dedo. Chromium: toques reales por CDP. WebKit de Playwright no tiene toques: eventos de puntero táctiles."""
    caja = origen.bounding_box()
    x0, y0 = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
    pasos = [(x0 + (destino[0] - x0) * k / 12, y0 + (destino[1] - y0) * k / 12) for k in range(1, 13)]
    if motor["nombre"] == "chromium":
        cdp = ctx.new_cdp_session(pagina)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x0, "y": y0}]})
        for x, y in pasos:
            cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        cdp.detach()
        return
    pagina.evaluate("""([x0, y0, pasos]) => {
        const op = (x, y) => ({ pointerId: 7, pointerType: "touch", isPrimary: true, clientX: x, clientY: y, bubbles: true, cancelable: true, button: 0 });
        document.elementFromPoint(x0, y0).dispatchEvent(new PointerEvent("pointerdown", op(x0, y0)));
        for (const [x, y] of pasos) document.elementFromPoint(x, y).dispatchEvent(new PointerEvent("pointermove", op(x, y)));
        const [xf, yf] = pasos[pasos.length - 1];
        document.elementFromPoint(xf, yf).dispatchEvent(new PointerEvent("pointerup", op(xf, yf)));
    }""", [x0, y0, pasos])


@pytest.mark.parametrize("ancho", [360, 1280])
def test_fotos_por_pagina_cambia_las_paginas_del_pdf(motor, tmp_path, servidor, sesion_de, reportera, ancho):
    ctx = ctx_de(motor, tmp_path, servidor, sesion_de(reportera), ancho, accept_downloads=True)
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 10))
        abrir_datos(pagina)
        expect(pagina.locator("#r-por-pagina-1")).to_be_checked()                          # por omisión, una por página (como siempre)
        for n in (1, 2, 4, 6):
            caja = pagina.locator(f"label[for=r-por-pagina-{n}]").bounding_box()
            assert caja["height"] >= 43.5 and caja["width"] >= 43.5, n
        pagina.locator("#r-croquis").uncheck()
        pagina.locator("label[for=r-por-pagina-4]").click()
        expect(pagina.locator("#r-por-pagina-4")).to_be_checked()
        esperar(pagina, "ITT.cola.borradores().then(b => b.find(x => x.tipo === 'reporte').datos.fotos_por_pagina === 4)")
        # La lista marca dónde empieza cada página del PDF: la foto 1 en la portada, luego de 4 en 4.
        marcas = pagina.locator("#rep-fotos > li[data-pagina]")
        expect(marcas).to_have_count(3)
        assert [marcas.nth(k).get_attribute("data-pagina") for k in range(3)] == ["Portada", "Página 2", "Página 3"]
        assert pagina.locator("#rep-fotos > li").nth(5).get_attribute("data-pagina") == "Página 3"
        # El teclado recorre las opciones con las flechas (grupo de radios).
        pagina.locator("#r-por-pagina-4").focus()
        pagina.keyboard.press("ArrowRight")
        expect(pagina.locator("#r-por-pagina-6")).to_be_checked()
        pagina.locator("label[for=r-por-pagina-4]").click()
        sin_desborde(pagina)
        captura(pagina, f"fotos-por-pagina-{motor['nombre']}-{ancho}.png")
        pagina.locator("#rep-pdf").click()
        expect(pagina.locator("#dlg-pdf")).to_be_visible()
        esperar(pagina, "document.querySelectorAll('#pdf-paginas canvas').length === 3", timeout=TIEMPO * 6)   # portada + 8 fotos en 2 páginas
        assert Reporte.objects.get().datos["fotos_por_pagina"] == 4
        with pagina.expect_download() as descarga:
            pagina.locator("#pdf-descargar").click()
        ruta = tmp_path / "reporte.pdf"
        descarga.value.save_as(str(ruta))
        pymupdf = pytest.importorskip("pymupdf")
        with pymupdf.open(str(ruta)) as doc:
            assert doc.page_count == 3
            assert doc[1].get_text().count("FOTOGRAFÍA") == 4 and doc[2].get_text().count("FOTOGRAFÍA") == 4
    finally:
        ctx.close()


def test_arrastrar_con_el_raton_y_escape_cancela(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = ctx_de(motor, tmp_path, servidor, sesion_de(reportera), 1280, viewport={"width": 1280, "height": 1300})
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 7))
        antes = orden(pagina)
        tarjetas = pagina.locator("#rep-fotos > li")
        a_la_vista(pagina, tarjetas.nth(0))
        # Escape a medio arrastre: nada cambia.
        destino = tarjetas.nth(0).bounding_box()
        arrastrar_raton(pagina, tarjetas.nth(3).locator(".rep-asa"), (destino["x"] + 40, destino["y"] + 6), soltar=False)
        expect(pagina.locator(".rep-fantasma")).to_have_count(1)
        expect(pagina.locator(".rep-destino")).to_be_visible()
        pagina.keyboard.press("Escape")
        pagina.mouse.up()
        expect(pagina.locator(".rep-fantasma")).to_have_count(0)
        pagina.wait_for_timeout(500)
        assert orden(pagina) == antes
        # La 4 arriba de todo.
        a_la_vista(pagina, tarjetas.nth(0))
        destino = tarjetas.nth(0).bounding_box()
        arrastrar_raton(pagina, tarjetas.nth(3).locator(".rep-asa"), (destino["x"] + 40, destino["y"] + 6))
        esperar_orden(pagina, [antes[3], *antes[:3], *antes[4:]])
        expect(pagina.locator("#rep-anuncio")).to_have_text("Foto 4 movida a la posición 1")
        expect(tarjetas.nth(0).locator("strong")).to_have_text("Foto 01")
        assert tarjetas.nth(0).get_attribute("data-sha") == antes[3]
        # La 1 (ahora la 2) hasta debajo de la 4.
        a_la_vista(pagina, tarjetas.nth(1), 60)
        cuarta = tarjetas.nth(3).bounding_box()
        arrastrar_raton(pagina, tarjetas.nth(1).locator(".rep-asa"), (cuarta["x"] + 40, cuarta["y"] + cuarta["height"] - 4))
        esperar_orden(pagina, [antes[3], antes[1], antes[2], antes[0], *antes[4:]])
    finally:
        ctx.close()


def test_arrastrar_con_el_dedo_en_el_telefono(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = ctx_de(motor, tmp_path, servidor, sesion_de(reportera), 360)
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 5))
        antes = orden(pagina)
        tarjetas = pagina.locator("#rep-fotos > li")
        a_la_vista(pagina, tarjetas.nth(0))
        asa = tarjetas.nth(2).locator(".rep-asa")
        assert pagina.evaluate("getComputedStyle(document.querySelector('#rep-fotos .rep-asa')).touchAction") == "none"
        caja = asa.bounding_box()
        assert caja["height"] >= 43.5
        destino = tarjetas.nth(0).bounding_box()
        arrastrar_dedo(motor, ctx, pagina, asa, (destino["x"] + 60, destino["y"] + 8))
        esperar_orden(pagina, [antes[2], antes[0], antes[1], antes[3]])
        sin_desborde(pagina)
    finally:
        ctx.close()


@pytest.mark.parametrize("ancho", [360, 1280])
def test_mover_al_principio_y_a_una_posicion_con_45_fotos(motor, tmp_path, servidor, sesion_de, reportera, ancho):
    ctx = ctx_de(motor, tmp_path, servidor, sesion_de(reportera), ancho)
    try:
        pagina = abrir(ctx, servidor)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 46))
        antes = orden(pagina)
        tarjetas = pagina.locator("#rep-fotos > li")
        expect(tarjetas).to_have_count(45)
        pagina.fill("#nota-39", "Nota de la 40")
        # «Mover» en la foto 40 → «Al principio».
        tarjetas.nth(39).locator("[data-itt=mover]").click()
        expect(pagina.locator("#dlg-mover")).to_be_visible()
        expect(pagina.locator("#dlg-mover-titulo")).to_have_text("Mover la foto 40")
        captura(pagina, f"mover-{motor['nombre']}-{ancho}.png")
        pagina.locator("#mover-principio").click()
        expect(pagina.locator("#dlg-mover")).to_be_hidden()
        esperar_orden(pagina, [antes[39], *antes[:39], *antes[40:]])
        expect(pagina.locator("#rep-anuncio")).to_have_text("Foto 40 movida a la posición 1")
        expect(tarjetas.nth(0)).to_be_in_viewport()
        assert pagina.evaluate("document.activeElement.closest('#rep-fotos > li').dataset.sha") == antes[39]   # el foco sigue a la foto
        assert pagina.evaluate("document.activeElement.dataset.itt") == "mover"
        # La nota viajó con su foto y se sigue editando en su lugar nuevo.
        expect(pagina.locator("#nota-0")).to_have_value("Nota de la 40")
        pagina.fill("#nota-0", "Nota de la 40, editada")
        esperar(pagina, "ITT.cola.borradores().then(b => b.find(x => x.tipo === 'reporte').datos.fotos[0].nota === 'Nota de la 40, editada')")
        expect(pagina.locator("#nota-39")).to_have_value("")
        # «A la posición…»: fuera de rango no se mueve; 10 sí.
        tarjetas.nth(0).locator("[data-itt=mover]").click()
        expect(pagina.locator("#mover-principio")).to_be_disabled()
        for malo in ("0", "46", "abc"):
            pagina.fill("#mover-posicion", malo) if malo != "abc" else pagina.evaluate("document.getElementById('mover-posicion').value = ''")
            pagina.locator("#form-mover button[type=submit]").click()
            expect(pagina.locator("#mover-error")).to_have_text("Escribe un número del 1 al 45.")
            expect(pagina.locator("#dlg-mover")).to_be_visible()
        pagina.fill("#mover-posicion", "10")
        pagina.keyboard.press("Enter")
        expect(pagina.locator("#dlg-mover")).to_be_hidden()
        esperar_orden(pagina, [*antes[:9], antes[39], *antes[9:39], *antes[40:]])
        expect(pagina.locator("#rep-anuncio")).to_have_text("Foto 1 movida a la posición 10")
        expect(tarjetas.nth(9)).to_be_in_viewport()
        expect(pagina.locator("#nota-9")).to_have_value("Nota de la 40, editada")
        # «Al final».
        tarjetas.nth(9).locator("[data-itt=mover]").click()
        pagina.locator("#mover-final").click()
        esperar_orden(pagina, [*antes[:9], *antes[9:39], *antes[40:], antes[39]])
        # Ubicar y quitar siguen apuntando a su foto.
        tarjetas.nth(44).locator("[data-itt=ubicar]").click()
        pagina.fill("#u-lat", "28.5")
        pagina.fill("#u-lon", "-106.1")
        pagina.locator("#u-guardar").click()
        esperar(pagina, "ITT.cola.borradores().then(b => b.find(x => x.tipo === 'reporte').datos.fotos[44].lat === 28.5)")
        assert fotos_locales(pagina)[44]["sha256"] == antes[39]
        sin_desborde(pagina)
    finally:
        ctx.close()


@pytest.mark.parametrize("ancho", [360, 1280])
def test_vista_ordenar_agrupada_como_el_pdf(motor, tmp_path, servidor, sesion_de, reportera, ancho):
    ctx = ctx_de(motor, tmp_path, servidor, sesion_de(reportera), ancho)
    try:
        pagina = abrir(ctx, servidor)
        # El reparto del editor es el del generador: todas las opciones, con y sin croquis, de 1 a 13 fotos y 150.
        js = pagina.evaluate("""() => { const r = {}; for (const p of [1, 2, 4, 6]) for (const c of [false, true]) for (const n of [...Array(13).keys()].map(k => k + 1).concat([150]))
            r[`${p}-${c}-${n}`] = ITT.ordenFotos.paginas(n, p, c); return r; }""")
        for p in (1, 2, 4, 6):
            for c in (False, True):
                for n in [*range(1, 14), 150]:
                    assert js[f"{p}-{str(c).lower()}-{n}"] == photo_pages(n, p, c), (p, c, n)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 14))
        antes = orden(pagina)
        abrir_datos(pagina)
        pagina.locator("label[for=r-por-pagina-6]").click()
        expect(pagina.locator("#rep-ordenar")).to_be_visible()
        pagina.locator("#rep-ordenar").click()
        expect(pagina.locator("#rep-ordenar")).to_have_attribute("aria-pressed", "true")
        expect(pagina.locator("#rep-fotos")).to_be_hidden()
        grupos = pagina.locator("#rep-rejilla .rep-grupo")
        expect(grupos).to_have_count(3)                                                          # con croquis: 6 + 6 + 1, desde la página 2
        assert [grupos.nth(k).locator(".rep-grupo__titulo").inner_text() for k in range(3)] == ["Página 2", "Página 3", "Página 4"]
        assert [grupos.nth(k).locator(".rep-miniatura").count() for k in range(3)] == [6, 6, 1]
        pagina.locator("#r-croquis").uncheck()                                                   # sin croquis: la primera sola en la portada
        expect(grupos.first.locator(".rep-grupo__titulo")).to_have_text("Portada")
        assert [grupos.nth(k).locator(".rep-grupo__titulo").inner_text() for k in range(3)] == ["Portada", "Página 2", "Página 3"]
        assert [grupos.nth(k).locator(".rep-miniatura").count() for k in range(3)] == [1, 6, 6]
        sin_desborde(pagina)
        captura(pagina, f"ordenar-{motor['nombre']}-{ancho}.png")
        # Tocar una miniatura abre «Mover».
        miniaturas = pagina.locator("#rep-rejilla .rep-miniatura__boton")
        miniaturas.nth(12).click()
        expect(pagina.locator("#dlg-mover")).to_be_visible()
        expect(pagina.locator("#dlg-mover-titulo")).to_have_text("Mover la foto 13")
        pagina.locator("#mover-principio").click()
        esperar_orden(pagina, [antes[12], *antes[:12]])
        expect(pagina.locator("#rep-rejilla .rep-miniatura").first).to_have_attribute("data-sha", antes[12])
        assert pagina.evaluate("document.activeElement.closest('.rep-miniatura').dataset.sha") == antes[12]
        # Arrastrar una miniatura (ratón en escritorio; en el teléfono, dedo tras mantener presionado).
        if ancho >= 768:
            destino = miniaturas.nth(0).bounding_box()
            arrastrar_raton(pagina, miniaturas.nth(3), (destino["x"] + 4, destino["y"] + destino["height"] / 2))
            esperar_orden(pagina, [antes[2], antes[12], *antes[:2], *antes[3:12]])
        pagina.locator("#rep-ordenar").click()
        expect(pagina.locator("#rep-fotos")).to_be_visible()
    finally:
        ctx.close()


def test_orden_sin_conexion_persiste_al_recargar_y_llega_al_servidor(motor, tmp_path, servidor, sesion_de, reportera):
    ctx = ctx_de(motor, tmp_path, servidor, sesion_de(reportera), 360)
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        ctx.set_offline(True)
        nuevo_reporte(pagina)
        elegir(pagina, range(1, 6))
        antes = orden(pagina)
        pagina.locator("#rep-fotos > li").nth(4).locator("[data-itt=mover]").click()
        pagina.locator("#mover-principio").click()
        esperado = [antes[4], *antes[:4]]
        esperar_orden(pagina, esperado)
        pagina.wait_for_timeout(600)
        assert "#/r/" in pagina.url
        pagina.reload()                                                                           # sin señal: la abre el service worker, en el mismo reporte
        pagina.wait_for_selector("html[data-rep-lista]", timeout=TIEMPO)
        pagina.wait_for_selector("#rep-vista-editor:not([hidden])")
        expect(pagina.locator("#rep-fotos > li")).to_have_count(5)
        assert [pagina.locator("#rep-fotos > li").nth(k).get_attribute("data-sha") for k in range(5)] == esperado
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora()")
        sin_pendientes(pagina)
        esperar(pagina, "ITT.cola.borradores().then(b => !b.find(x => x.tipo === 'reporte').sucio)", timeout=TIEMPO * 2)
        assert [f["sha256"] for f in Reporte.objects.get().datos["fotos"]] == esperado
    finally:
        ctx.close()
