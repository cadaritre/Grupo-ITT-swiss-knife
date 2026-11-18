"""CAD · «Modo táctil», quinta vuelta (dueño, 17-11-2026): deslizar desde el borde de la pantalla nunca saca del plano.

- La página no rebota ni recarga (`overscroll-behavior: none`) y el área de dibujo va de borde a borde con `touch-action: none`.
- Un toque que empieza en la franja de un borde del área de dibujo cancela su `touchstart` (Safari de iOS no arranca su gesto de
  atrás/adelante); el arrastre con el dedo desde el borde mueve el plano como cualquier otro.
- La guarda del historial: «atrás» (el gesto o botón del sistema; aquí `history.back()`) nunca sale: cierra la hoja, el teclado o el
  aviso abierto; si no, cancela la herramienta; si no, pregunta «¿Salir del plano?» y la pregunta se queda aunque se vuelva a ir atrás.
  Salir no deja entradas apiladas y «atrás» desde la página siguiente vuelve al plano. Los cambios sin guardar se protegen como siempre.
  «Modo completo» quita la guarda. Igual en la página sin conexión.

Plano propio sintético en UTM (E 395 000, N 3 175 000). Corre bajo el candado de la máquina compartida, un navegador a la vez.
"""
from __future__ import annotations

import re

import pytest
from playwright.sync_api import expect

from .test_cad_navegador import LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_cad_tactil_navegador import (  # noqa: F401
    GESTOS, INSTALADA, X0, Y0, _herramienta, _listo_sin_conexion, _llevar_al_telefono, plano_utm, tactil, telefono)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
MOTORES = ["chromium", "webkit"]
PREGUNTA = "¿Salir del plano?"


def _abrir_desde_la_lista(navegar, usuario, motor, p0):
    """La lista de planos y luego el plano, como en el teléfono: «atrás» tiene adónde ir."""
    n = navegar(usuario, motor, ancho=375, alto=812, movil=True)
    n.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
    p = n.ir("/cad/")
    p.wait_for_load_state("networkidle")                              # WebKit da por error lo que se corta al cambiar de página
    p.goto(n.servidor.url + f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    p.evaluate(GESTOS)
    return n, p


def _atras(p):
    """«Atrás» del navegador (lo mismo que hace el gesto del borde o el botón del sistema) y espera a que la página lo atienda."""
    p.evaluate("""() => new Promise((ok) => { window.__sigue = 1;
      addEventListener('popstate', () => setTimeout(ok, 80), {once: true}); history.back(); setTimeout(ok, 3000); })""")
    assert p.evaluate("window.__sigue === 1"), "«atrás» recargó o cambió la página"


def _pregunta(p):
    return p.locator("#tt-hoja").is_visible() and p.locator("#tt-hoja-titulo").text_content() == PREGUNTA


def _sin_errores(n, motor):
    propios = [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)]
    assert not propios, propios


@pytest.mark.parametrize("motor", MOTORES)
def test_atras_nunca_sale_del_plano_y_salir_no_apila_entradas(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = _abrir_desde_la_lista(navegar, ana, motor, p0)
    url, largo = p.url, p.evaluate("history.length")
    assert p.evaluate("TVCadTactil.guarda()")
    # Primero «atrás» es un paso atrás en la interfaz: la hoja abierta se cierra…
    p.locator("#tt-mas").click()
    expect(p.locator("#tt-hoja")).to_be_visible()
    _atras(p)
    expect(p.locator("#tt-hoja")).to_be_hidden()
    # … la herramienta en marcha se cancela …
    _herramienta(p, "dibujar", "Línea")
    assert p.evaluate("TVCadTactil.herramienta()")
    _atras(p)
    assert p.evaluate("TVCadTactil.herramienta()") is None and not _pregunta(p)
    # … y con nada abierto pregunta; la pregunta se queda aunque se vaya atrás diez veces, sin entradas de más.
    for _ in range(10):
        _atras(p)
    assert _pregunta(p)
    assert p.url == url and p.evaluate("history.length") == largo and p.evaluate("TVCadTactil.guarda()")
    assert p.evaluate("TVCadTactil.listo()")
    p.locator("#tt-hoja").get_by_role("button", name="Quedarme").click()
    expect(p.locator("#tt-hoja")).to_be_hidden()
    assert p.url == url
    # «Salir»: la lista de planos reemplaza la entrada de la guarda (el historial no crece) …
    _atras(p)
    assert _pregunta(p)
    p.locator("#tt-hoja").get_by_role("button", name="Salir", exact=True).click()
    p.wait_for_url(re.compile(r"/cad/$"))
    p.wait_for_load_state("networkidle")
    assert p.evaluate("history.length") == largo
    # … y «atrás» desde ahí vuelve al plano, con la guarda puesta otra vez.
    p.go_back()
    p.wait_for_url(url)
    esperar(p, "window.TVCadTactil && TVCadTactil.listo() && TVCadTactil.guarda()", LARGO)
    assert p.evaluate("history.length") == largo
    # Recargar el plano no pone otra entrada encima, y «atrás» sigue sin salir ni recargar.
    p.reload()
    esperar(p, "window.TVCadTactil && TVCadTactil.listo() && TVCadTactil.guarda()", LARGO)
    assert p.evaluate("history.length") == largo
    _atras(p)
    assert _pregunta(p) and p.url == url
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_con_cambios_sin_guardar_salir_pregunta_como_siempre(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = _abrir_desde_la_lista(navegar, ana, motor, p0)
    url, largo = p.url, p.evaluate("history.length")
    assert p.evaluate(f"TVCad.ejecutar('CIRCLE {X0 + 40},{Y0 + 40} 3').then(r => r.ok !== false)")
    esperar_estado(p, "sucio")
    _atras(p)
    assert _pregunta(p)
    p.locator("#tt-hoja").get_by_role("button", name="Salir", exact=True).click()
    expect(p.locator("#cad-salir")).to_be_visible()                  # la pregunta de siempre por los cambios sin guardar
    _atras(p)                                                          # «atrás» la cierra: se queda en el plano, sin perder nada
    expect(p.locator("#cad-salir")).to_be_hidden()
    assert p.url == url and p.evaluate("history.length") == largo
    esperar_estado(p, "sucio")
    _atras(p)
    p.locator("#tt-hoja").get_by_role("button", name="Salir", exact=True).click()
    p.locator("#cad-salir-sin").click()
    p.wait_for_url(re.compile(r"/cad/$"))
    p.wait_for_load_state("networkidle")
    assert p.evaluate("history.length") == largo
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_modo_completo_quita_la_guarda(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = _abrir_desde_la_lista(navegar, ana, motor, p0)
    p.evaluate("TVCadTactil.salir()")
    esperar(p, "!TVCadTactil.activo() && !TVCadTactil.guarda()", 5000)
    p.evaluate("history.back()")
    p.wait_for_url(re.compile(r"/cad/$"))
    p.wait_for_load_state("networkidle")
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_el_borde_de_la_pantalla_mueve_el_plano(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = _abrir_desde_la_lista(navegar, ana, motor, p0)
    url, largo = p.url, p.evaluate("history.length")
    # La página no rebota ni recarga; el área de dibujo va de borde a borde y maneja sus gestos.
    css = p.evaluate("""() => { const c = document.getElementById('tt-capa'), r = c.getBoundingClientRect(), s = (e) => getComputedStyle(e);
      return {html: s(document.documentElement).overscrollBehaviorX + ' ' + s(document.documentElement).overscrollBehaviorY,
              body: s(document.body).overscrollBehaviorX + ' ' + s(document.body).overscrollBehaviorY,
              capa: s(c).touchAction, izq: r.left, der: r.right, ancho: document.documentElement.clientWidth}; }""")
    assert css["html"] == "none none" and css["body"] == "none none" and css["capa"] == "none", css
    assert css["izq"] <= 0 and css["der"] >= css["ancho"], css
    # Un toque que empieza en la franja del borde cancela su touchstart (en Safari así no arranca su gesto de atrás/adelante); en medio no.
    p.evaluate("""() => { window.__bordes = []; addEventListener('touchstart', (e) => __bordes.push(e.defaultPrevented)); }""")
    for x in (4, css["ancho"] / 2, css["ancho"] - 4):
        p.touchscreen.tap(x, 320)                                     # toques de verdad del motor (WebKit no deja crear Touch a mano)
        p.wait_for_timeout(450)
    cancelado = p.evaluate("__bordes")
    assert cancelado == [True, False, True], cancelado
    # Arrastrar desde el borde izquierdo hacia la derecha (y desde el derecho hacia la izquierda) mueve el plano y nada más.
    ancho = css["ancho"]
    for x0, dx in ((1, 140), (ancho - 1, -140)):
        v0 = p.evaluate("TVCadTactil.vista()")
        p.evaluate("([x, dx]) => __g.arrastrar(x, 300, dx, 0)", [x0, dx])
        p.wait_for_timeout(600)
        v1 = p.evaluate("TVCadTactil.vista()")
        assert abs((v0["cx"] - v1["cx"]) / v0["k"] - dx) < 3, (v0, v1)
        assert p.url == url and p.evaluate("history.length") == largo and not _pregunta(p)
    _sin_errores(n, motor)


@pytest.mark.parametrize("motor", MOTORES)
def test_sin_conexion_atras_tampoco_sale_del_plano(tactil, telefono, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n = telefono(ana, motor)
    n.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
    n.contexto.add_init_script(INSTALADA)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    _llevar_al_telefono(n, p, p0)
    p.goto(n.servidor.url + "/cad/")
    consola = []
    p.on("console", lambda m: consola.append(f"{m.type}: {m.text[:160]}"))
    n.contexto.set_offline(True)
    p.goto(n.servidor.url + f"/cad/{p0.pk}/")
    _listo_sin_conexion(p, consola)
    url, largo = p.url, p.evaluate("history.length")
    assert p.evaluate("TVCadTactil.guarda()")
    for _ in range(10):
        _atras(p)
    assert _pregunta(p) and p.url == url and p.evaluate("history.length") == largo
    p.locator("#tt-hoja").get_by_role("button", name="Salir", exact=True).click()
    p.wait_for_url(re.compile(r"/cad/sin-conexion/$"))
    assert p.evaluate("history.length") == largo
    assert not n.errores, n.errores
