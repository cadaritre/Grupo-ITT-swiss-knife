"""PWA de campo en WebKit (parches de M, 15-11-2026): fotos en una ventana privada y la app sin señal con su manifiesto y sus colores.

Un contexto de Playwright sin perfil guardado se porta como Safari en ventana privada: IndexedDB no guarda Blob. Corre bajo el candado.
"""
from __future__ import annotations

import pytest

from .test_pwa_navegador import TIEMPO, abrir, con_service_worker, contexto, usuaria  # noqa: F401 (fixture)

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


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
