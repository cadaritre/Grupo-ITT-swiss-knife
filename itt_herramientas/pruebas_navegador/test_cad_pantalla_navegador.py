"""CAD · el editor usa todo el alto y «Pantalla completa» (pedido del dueño, 16-11-2026), con el editor real, en Chromium y WebKit.

En cada tamaño: el editor llega hasta abajo de la ventana; en pantalla completa quedan la tira (nombre, estado, Guardar, Versiones,
Salir), la barra de Civil y el editor en todo lo demás; el lienzo del editor toma el tamaño nuevo a la densidad real (sin borroso);
se guarda desde ahí; Esc nunca sale (es del editor: cancelar y deseleccionar), solo el botón o el atajo; se recuerda en el dispositivo. Corre bajo el candado de la máquina compartida.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio
from itt_herramientas.models import VersionPlano

from .test_cad_navegador import LARGO, _persona, cad, dibujar, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
TAMANOS = [(1280, 800), (1920, 1080), (768, 1024), (375, 812)]

MEDIDAS = """() => {
  const q = (s) => { const e = document.querySelector(s); if (!e) return null; const r = e.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height, visible: r.width > 0 && r.height > 0 && getComputedStyle(e).display !== 'none'}; };
  const f = document.getElementById('cad-editor'); let lienzo = null;
  try { const c = f.contentDocument.querySelector('canvas'); const r = c.getBoundingClientRect(); lienzo = {w: r.width, h: r.height, pw: c.width, ph: c.height}; } catch (e) {}
  return {alto: innerHeight, ancho: innerWidth, dpr: devicePixelRatio, editor: q('#cad-lienzo'), marco: q('#cad-editor'), civil: q('#cad-extensiones'),
    lateral: q('.tv-lateral'), cabecera: q('.tv-cabecera'), autoria: q('#cad-autoria'), guardar: q('#cad-guardar'), versiones: q('#cad-boton-versiones'),
    boton: q('#cad-completa'), canvas: lienzo, completa: document.documentElement.classList.contains('cad-completa'), nativa: !!document.fullscreenElement};
}"""


def medidas(p):
    return p.evaluate(MEDIDAS)


def hasta_abajo(m):
    """El editor llega hasta abajo de la ventana; si lo de arriba no deja ni 360 px (teléfono), mide 360 y la página se desplaza."""
    e = m["editor"]
    if m["alto"] - e["top"] - 12 >= 360:
        return e["bottom"] <= m["alto"] + 1 and m["alto"] - e["bottom"] <= 16
    return abs(e["height"] - 360) <= 2


def esperar_lienzo(p, m):
    """El lienzo del editor ocupa todo el marco, a la densidad real de la pantalla (el editor ya tomó el tamaño nuevo)."""
    esperar(p, """(() => { const f = document.getElementById('cad-editor'); const c = f.contentDocument.querySelector('canvas');
      if (!c) return false; const r = c.getBoundingClientRect(), g = f.getBoundingClientRect();
      return Math.abs(r.width - g.width) < 2 && Math.abs(r.height - g.height) < 2 && Math.abs(c.width - r.width * devicePixelRatio) < 3; })()""", LARGO)


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
@pytest.mark.parametrize("tam", TAMANOS, ids=lambda t: f"{t[0]}x{t[1]}")
def test_el_editor_ocupa_el_alto_y_la_pantalla_completa_deja_civil_y_guardar(cad, navegar, crear_usuario, roles, motor, tam):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio con un nombre largo para la tira", "", "dxf")
    w, h = tam
    n = navegar(ana, motor, ancho=w, alto=h)                    # ventana angosta con ratón: en un teléfono táctil (sin el modo táctil) solo se ve
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    expect(p.locator("#cad-extensiones")).to_be_visible(timeout=LARGO)          # la barra de Civil
    # Normal: el editor llega hasta abajo de la ventana (no una caja fija).
    p.evaluate("window.scrollTo(0, 0)")
    p.wait_for_timeout(900)
    m = medidas(p)
    assert hasta_abajo(m), m
    esperar_lienzo(p, m)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-pantalla-{motor}-{w}x{h}-1-normal.png", caret="initial")
    # Pantalla completa: fuera lateral, cabecera y datos del plano; quedan la tira, Civil y el editor en todo lo demás.
    p.locator("#cad-completa").click()
    esperar(p, "document.documentElement.classList.contains('cad-completa')", LARGO)
    p.wait_for_timeout(600)
    m = medidas(p)
    assert not (m["lateral"] and m["lateral"]["visible"]) and not (m["cabecera"] and m["cabecera"]["visible"]) and not m["autoria"]["visible"], m
    assert m["guardar"]["visible"] and m["versiones"]["visible"] and m["civil"]["visible"], m
    assert m["civil"]["bottom"] <= m["editor"]["top"] + 1, m
    assert abs(m["editor"]["bottom"] - m["alto"]) <= 2 and m["editor"]["width"] >= m["ancho"] - 2, m
    assert m["editor"]["top"] < 200, m                                          # la tira y Civil ocupan poco
    expect(p.locator("#cad-completa")).to_contain_text("Salir de pantalla completa")
    esperar_lienzo(p, m)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-pantalla-{motor}-{w}x{h}-2-completa.png", caret="initial")
    # Se guarda desde la pantalla completa.
    dibujar(p, "CIRCLE 390000,3168000 12")
    esperar_estado(p, "sucio")
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    assert VersionPlano.objects.filter(plano=p0).count() == 2
    # Se recuerda en este dispositivo.
    p.reload()
    esperar_estado(p, "guardado")
    assert medidas(p)["completa"]
    # Esc no sale (con la página maximizada, que es como se reabre: la del navegador pide un toque); el botón sí.
    p.locator("body").press("Escape")
    p.wait_for_timeout(400)
    assert medidas(p)["completa"]
    p.locator("#cad-completa").click()
    esperar(p, "!document.documentElement.classList.contains('cad-completa')", LARGO)
    p.wait_for_timeout(600)
    m = medidas(p)
    assert m["autoria"]["visible"] and hasta_abajo(m), m
    esperar_lienzo(p, m)
    assert not n.errores or motor == "webkit" and all("Refused to apply a stylesheet" in e for e in n.errores), n.errores


def test_la_pantalla_completa_del_navegador_y_su_esc(cad, navegar, crear_usuario, roles):
    """Con la API del navegador (Chromium): el botón la pide; si el navegador sale (Esc sostenida), la página sigue maximizada y lo dice una
    vez; el atajo desde el editor la quita."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, "chromium", ancho=1280, alto=800)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    p.locator("#cad-completa").click()
    esperar(p, "!!document.fullscreenElement && document.documentElement.classList.contains('cad-completa')", LARGO)
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push(t); return a && a(t, k); }; }")
    p.evaluate("document.exitFullscreen()")                                     # lo que hace el navegador con Esc sostenida
    esperar(p, "!document.fullscreenElement", LARGO)
    p.wait_for_timeout(300)
    assert p.evaluate("document.documentElement.classList.contains('cad-completa')")   # la página sigue maximizada
    assert p.evaluate("localStorage.getItem('tv.cad.completa')") == "1"
    assert p.evaluate("window.__avisos") == ["Esc salió de la pantalla del navegador; el editor sigue maximizado."]
    # El atajo también desde el editor (el foco en su marco): Ctrl+Mayús+F la quita.
    p.locator("#cad-editor").click(position={"x": 400, "y": 300})
    p.keyboard.press("Control+Shift+F")
    esperar(p, "!document.documentElement.classList.contains('cad-completa')", LARGO)
    assert p.evaluate("localStorage.getItem('tv.cad.completa')") == "0"
    # y otra vez la pone; el aviso de Esc no se repite
    p.keyboard.press("Control+Shift+F")
    esperar(p, "document.documentElement.classList.contains('cad-completa')", LARGO)
    p.evaluate("document.exitFullscreen().catch(() => {})")
    p.wait_for_timeout(500)
    assert len(p.evaluate("window.__avisos")) == 1
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_esc_nunca_saca_de_la_pantalla_completa(cad, navegar, crear_usuario, roles, motor):
    """Pedido del dueño (producción 1.2.10): Esc en el editor deselecciona y la pantalla sigue completa. Chromium bloquea Esc para el
    navegador (keyboard.lock): Esc llega al editor. WebKit (sin bloqueo): la página sigue maximizada y el siguiente Esc deselecciona."""
    from .test_civil_navegador import pedir

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Esc", "", "dxf")
    n = navegar(ana, motor, ancho=1280, alto=800)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p, "CIRCLE 390000,3168000 12")
    p.locator("#cad-completa").click()
    esperar(p, "document.documentElement.classList.contains('cad-completa')", LARGO)
    p.wait_for_timeout(800)
    nativa = p.evaluate("!!document.fullscreenElement")
    if motor == "chromium":
        assert nativa and p.evaluate("!!(navigator.keyboard && navigator.keyboard.lock)")
    for vez in range(2):
        h = pedir(p, {"op": "query", "type": "Circle", "detail": "summary", "limit": 1})["entities"][0]["handle"]
        pedir(p, {"op": "select", "handles": [h]})
        assert pedir(p, {"op": "state"})["selection"] == [h]
        p.locator("#cad-editor").click(position={"x": 700, "y": 520})            # el foco en el editor, en vacío
        pedir(p, {"op": "select", "handles": [h]})
        p.keyboard.press("Escape")
        p.wait_for_timeout(500)
        assert p.evaluate("document.documentElement.classList.contains('cad-completa')"), vez   # nunca sale
        if motor == "chromium":
            assert p.evaluate("!!document.fullscreenElement"), vez                # Esc llegó al editor, no al navegador
        if vez == 1 or motor == "chromium":
            assert pedir(p, {"op": "state"})["selection"] == [], vez               # y deseleccionó
    assert not n.errores or motor == "webkit" and all("Refused to apply a stylesheet" in e for e in n.errores), n.errores
