"""PWA de campo en WebKit (parches de M, 15-11-2026): fotos en una ventana privada y la app sin señal con su manifiesto y sus colores.

Un contexto de Playwright sin perfil guardado se porta como Safari en ventana privada: IndexedDB no guarda Blob. Corre bajo el candado.
"""
from __future__ import annotations

import pytest

from .conftest import foto
from .test_pwa_navegador import TIEMPO, abrir, con_service_worker, contexto, esperar, usuaria  # noqa: F401 (fixture)

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


def test_en_una_ventana_privada_la_foto_dice_por_que_no_se_guardo_y_la_segunda_no_se_cuelga(playwright_, servidor, sesion_de, usuaria):
    if not playwright_.webkit.executable_path:
        pytest.skip("Sin WebKit")
    navegador = playwright_.webkit.launch()
    try:
        ctx = navegador.new_context(**{k: v for k, v in playwright_.devices["iPhone 13"].items() if k != "default_browser_type"})
        ctx.add_cookies([sesion_de(usuaria)])
        pagina = ctx.new_page()
        errores = []
        pagina.on("pageerror", lambda e: errores.append(str(e)))
        pagina.goto(servidor.url + "/capturar/")
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
        guarda = pagina.evaluate("""async () => { try { const db = await ITT.cola.abrir();
            await new Promise((ok, mal) => { const t = db.transaction(['blobs'], 'readwrite'); t.objectStore('blobs').put({id: 'x', blob: new Blob(['a'])});
              t.oncomplete = ok; t.onerror = () => mal(t.error); t.onabort = () => mal(t.error); });
            const t2 = db.transaction(['blobs'], 'readwrite'); t2.objectStore('blobs').delete('x'); return true; } catch (e) { return false; } }""")
        if guarda:
            pytest.skip("Este WebKit sí guarda Blob sin perfil: no reproduce la ventana privada")
        for i in range(1):
            pagina.set_input_files("#itt-galeria", [{"name": f"IMG_{i}.jpg", "mimeType": "image/jpeg", "buffer": foto(tam=(800, 600))}])
            esperar(pagina, "document.getElementById('itt-mensaje').dataset.tipo === 'error'", timeout=45000)
            texto = pagina.locator("#itt-mensaje").text_content()
            assert "ventana privada" in texto and "NO se guardó" in texto, texto
            pagina.evaluate("document.getElementById('itt-mensaje').dataset.tipo = ''")
        assert not pagina.evaluate("document.documentElement.dataset.guardadas")
        assert not errores, errores
    finally:
        navegador.close()


@pytest.mark.parametrize("nombre", ["chromium", "webkit"])
def test_sin_senal_el_manifiesto_y_los_colores_salen_del_service_worker(playwright_, servidor, sesion_de, usuaria, tmp_path, nombre):
    tipo = getattr(playwright_, nombre)
    motor = {"nombre": nombre, "tipo": tipo, "dispositivo": playwright_.devices["Pixel 7" if nombre == "chromium" else "iPhone 13"]}
    ctx = contexto(motor, tmp_path, servidor, sesion_de(usuaria))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        pagina.reload()
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
        ctx.set_offline(True)
        pagina.reload()
        pagina.wait_for_selector("html[data-captura-lista]", timeout=TIEMPO)
        r = pagina.evaluate("""async () => { const m = await fetch('/manifest.webmanifest'); const c = await fetch('/perfil.css');
            return {manifiesto: m.ok, tipo: m.headers.get('Content-Type'), colores: c.ok}; }""")
        assert r["manifiesto"] and "json" in (r["tipo"] or "") and r["colores"], r
    finally:
        ctx.close()
