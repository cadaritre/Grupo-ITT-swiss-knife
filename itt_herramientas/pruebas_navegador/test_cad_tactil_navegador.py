"""CAD · «Modo táctil» con el editor real, en un teléfono y una tableta emulados (toque, `hasTouch`), Chromium y WebKit.

Plano propio en UTM (X≈395 000, Y≈3 175 000). Los gestos se mandan a la capa táctil como eventos de puntero táctiles; el editor
recibe lo que la capa le pide por su canal de control. Comprueba: entrar al modo, mover y acercar exactos, el puntero con referencias,
cada herramienta de la v1 como un solo paso de deshacer, guardar como versión y reabrir, solo lectura, y las capturas de cada pantalla.
Corre bajo el candado de la máquina compartida, un navegador a la vez.
"""
from __future__ import annotations

import io
import math
import os
import re
import uuid

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio
from itt_herramientas.models import VersionPlano

from .test_cad_navegador import LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
X0, Y0 = 395000.0, 3175000.0
TAMANOS = {"telefono": (375, 812), "grande": (430, 932), "tableta": (768, 1024), "horizontal": (812, 375)}


@pytest.fixture
def tactil(cad, settings):
    settings.TV_CAD_TACTIL = True
    return cad


def plano_utm(usuario, nombre="Lotes Norte"):
    """Cuadro de 20 m, dos ejes que se cruzan en su centro, un círculo, un arco y un texto; capas LOTES y EJES."""
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    d.layers.add("LOTES", color=3)
    d.layers.add("EJES", color=1)
    m = d.modelspace()
    m.add_lwpolyline([(X0, Y0), (X0 + 20, Y0), (X0 + 20, Y0 + 20), (X0, Y0 + 20)], close=True, dxfattribs={"layer": "LOTES"})
    m.add_line((X0 - 10, Y0 + 10), (X0 + 30, Y0 + 10), dxfattribs={"layer": "EJES"})
    m.add_line((X0 + 10, Y0 - 10), (X0 + 10, Y0 + 30), dxfattribs={"layer": "EJES"})
    m.add_circle((X0 + 40, Y0 + 10), 5)
    m.add_text("LOTE 1", dxfattribs={"height": 1.5, "insert": (X0 + 2, Y0 + 2)})
    s = io.StringIO()
    d.write(s)
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(s.getvalue().encode())
    return servicio.crear(usuario, t, nombre, "Fracc. Norte", {"tipo": "prueba"})


# Gestos sobre la capa táctil (eventos de puntero táctiles, como los de un dedo).
GESTOS = r"""
(() => {
  if (window.__g) return;
  const capa = () => document.getElementById("tt-capa");
  const r = () => document.getElementById("tt-zona").getBoundingClientRect();
  const espera = (ms) => new Promise((ok) => setTimeout(ok, ms));
  function pe(tipo, x, y, id) {
    const z = r();
    capa().dispatchEvent(new PointerEvent(tipo, {bubbles: true, cancelable: true, composed: true, pointerId: id, pointerType: "touch", isPrimary: id === 1,
      clientX: z.left + x, clientY: z.top + y, button: 0, buttons: tipo === "pointerup" ? 0 : 1, width: 20, height: 20, pressure: tipo === "pointerup" ? 0 : 0.5}));
  }
  async function arrastrar(x, y, dx, dy, pasos = 12) {
    pe("pointerdown", x, y, 1);
    for (let i = 1; i <= pasos; i++) { pe("pointermove", x + dx * i / pasos, y + dy * i / pasos, 1); await espera(16); }
    pe("pointerup", x + dx, y + dy, 1);
  }
  async function pellizcar(cx, cy, d0, d1, pasos = 12) {
    pe("pointerdown", cx - d0 / 2, cy, 1); pe("pointerdown", cx + d0 / 2, cy, 2);
    for (let i = 1; i <= pasos; i++) { const d = d0 + (d1 - d0) * i / pasos; pe("pointermove", cx - d / 2, cy, 1); pe("pointermove", cx + d / 2, cy, 2); await espera(16); }
    pe("pointerup", cx - d1 / 2, cy, 1); pe("pointerup", cx + d1 / 2, cy, 2);
  }
  async function tocar(x, y) { pe("pointerdown", x, y, 1); await espera(40); pe("pointerup", x, y, 1); }
  async function toques(x, y, n, pausa = 90) { for (let i = 0; i < n; i++) { if (i) await espera(pausa); await tocar(x, y); } }
  // Toque y, otra vez en el mismo lugar, arrastre (la selección con recuadro); `quieto`: ms con el dedo quieto al final antes de soltar.
  async function tocarYArrastrar(x, y, dx, dy, {pausa = 90, pasos = 12, quieto = 0, x2 = x, y2 = y} = {}) {
    await tocar(x, y); await espera(pausa);
    pe("pointerdown", x2, y2, 1);
    for (let i = 1; i <= pasos; i++) { pe("pointermove", x2 + dx * i / pasos, y2 + dy * i / pasos, 1); await espera(16); }
    if (quieto) { const t = performance.now(); while (performance.now() - t < quieto) { pe("pointermove", x2 + dx, y2 + dy, 1); await espera(16); } }
    pe("pointerup", x2 + dx, y2 + dy, 1);
  }
  async function mantener(x, y) { pe("pointerdown", x, y, 1); await espera(600); }
  async function soltar(x, y) { pe("pointerup", x, y, 1); }
  async function mover(x, y) { pe("pointermove", x, y, 1); }
  window.__g = {pe, arrastrar, pellizcar, tocar, toques, tocarYArrastrar, mantener, soltar, mover};
})()
"""


def abrir(navegar, usuario, motor, tam="telefono", tema="claro", p0=None):
    w, h = TAMANOS[tam]
    n = navegar(usuario, motor, ancho=w, alto=h, movil=True)
    n.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
    if tema == "oscuro":
        from tvplataforma.models import Preferencias

        pref = Preferencias.de(usuario)
        pref.tema = "oscuro"
        pref.save()
    p = n.ir(f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    p.evaluate(GESTOS)
    return n, p


def camara(p):
    return p.evaluate("TVCad.pedir({op: 'state'}).then(s => ({x: s.camera.target[0], y: s.camera.target[1], d: s.camera.distance}))")


def entidades(p):
    """Objetos del dibujo (sin los que viven dentro de las definiciones de bloques)."""
    return p.evaluate("TVCad.pedir({op: 'query', detail: 'summary', limit: 5000}).then(r => r.entities.filter(e => e.space === '*Model_Space').length)")


def punto_en_pantalla(p, x, y):
    return p.evaluate("([x, y]) => TVCadTactil.aPantalla(x, y)", [x, y])


def llevar_punta(p, x, y):
    """Toca donde está el punto del mundo (con una herramienta activa, el toque lleva la PUNTA del puntero ahí) y espera la referencia."""
    sx, sy = punto_en_pantalla(p, x, y)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    p.wait_for_timeout(250)


def poner(p):
    p.locator("#tt-confirmar").click()
    p.wait_for_timeout(150)


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_mover_acercar_y_ver_todo_son_exactos_en_utm(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    expect(p.locator("#tt")).to_be_visible()
    expect(p.locator("#tt-nombre")).to_have_text("Lotes Norte")
    assert p.locator(".tv-cabecera").is_hidden()
    v0 = p.evaluate("TVCadTactil.vista()")
    # Un dedo: 120 × 80 px. El editor termina exactamente donde se soltó.
    p.evaluate("__g.arrastrar(150, 300, 120, 80)")
    p.wait_for_timeout(900)
    c = camara(p)
    assert abs(c["x"] - (v0["cx"] - 120 * v0["k"])) < v0["k"] * 0.5 and abs(c["y"] - (v0["cy"] + 80 * v0["k"])) < v0["k"] * 0.5
    # Dos dedos: ×2 alrededor de los dedos; el punto bajo los dedos no se mueve.
    v1 = p.evaluate("TVCadTactil.vista()")
    centro = p.evaluate("TVCadTactil.aMundo(180, 300)")
    p.evaluate("__g.pellizcar(180, 300, 80, 160)")
    p.wait_for_timeout(900)
    v2 = p.evaluate("TVCadTactil.vista()")
    assert abs(v1["k"] / v2["k"] - 2.0) < 0.02
    despues = p.evaluate("TVCadTactil.aPantalla(%r, %r)" % tuple(centro))
    assert abs(despues[0] - 180) < 1.5 and abs(despues[1] - 300) < 1.5
    # Dos toques ya no ven todo (pedido del dueño 17-11); tres toques sí.
    p.evaluate("__g.toques(200, 300, 2)")
    p.wait_for_timeout(1000)
    assert p.evaluate("TVCadTactil.vista()")["k"] == v2["k"]
    p.evaluate("__g.toques(200, 300, 3)")
    p.wait_for_timeout(1200)
    v3 = p.evaluate("TVCadTactil.vista()")
    assert v3["k"] > v2["k"]
    assert not n.errores, n.errores


@pytest.mark.parametrize("tam", ["telefono", "horizontal"])
def test_al_abrir_se_ve_todo_lo_dibujado_con_margen(tactil, navegar, crear_usuario, roles, tam):
    """iOS: en un plano chico el círculo salía cortado a la derecha al abrir. Se ajusta a la extensión real de los objetos."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, "chromium", tam=tam, p0=p0)
    v = p.evaluate("TVCadTactil.vista()")
    for x, y in ((X0 - 10, Y0 - 10), (X0 + 45, Y0 + 30), (X0 + 45, Y0 + 5), (X0 + 10, Y0 + 30)):   # esquinas de lo dibujado; borde del círculo
        sx, sy = punto_en_pantalla(p, x, y)
        assert v["ox"] + 4 < sx < v["ox"] + v["w"] - 4 and v["oy"] + 4 < sy < v["oy"] + v["h"] - 4, (x, y, sx, sy, v)
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_puntero_con_referencias_polilinea_y_un_deshacer(tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    antes = entidades(p)
    p.locator('.tt-pestanas [data-grupo="dibujar"]').click()
    p.locator("#tt-hoja").get_by_role("button", name="Polilínea").click()
    expect(p.locator("#tt-accion")).to_be_visible()
    # La punta a 0.3 m de la esquina: la referencia «Extremo» la lleva al punto exacto.
    for x, y in ((X0 + 0.2, Y0 + 0.2), (X0 + 20.2, Y0 - 0.1), (X0 + 10.1, Y0 + 10.2)):
        llevar_punta(p, x, y)
        poner(p)
    puntos = p.evaluate("TVCadTactil.puntos()")
    assert puntos[0] == [X0, Y0] and puntos[1] == [X0 + 20, Y0] and puntos[2] == [X0 + 10, Y0 + 10]
    p.locator("#tt-terminar").click()
    expect(p.locator("#tt-pestanas")).to_be_visible()
    # Sin herramienta no queda la marca de la referencia en la pantalla (iOS: el cuadro magenta se quedaba tras «Terminar»).
    assert p.locator("#tt-marca").get_attribute("visibility") == "hidden" and p.locator("#tt-lectura-snap").is_hidden()
    assert entidades(p) == antes + 1
    p.locator("#tt-deshacer").click()
    p.wait_for_timeout(400)
    assert entidades(p) == antes
    assert not n.errores, n.errores


def plano_con_presentacion(usuario, nombre="Lotes con hoja"):
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    d.modelspace().add_lwpolyline([(X0, Y0), (X0 + 20, Y0), (X0 + 20, Y0 + 20), (X0, Y0 + 20)], close=True)
    d.layouts.new("Hoja A1 plano")
    s = io.StringIO()
    d.write(s)
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(s.getvalue().encode())
    return servicio.crear(usuario, t, nombre, "", {"tipo": "prueba"})


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_modelo_y_presentaciones_y_sin_avisos_del_editor(tactil, navegar, crear_usuario, roles, motor):
    """La hoja «Modelo y presentaciones» (layouts y layout_set, nombres con espacios): en una presentación solo se ve y se mide.
    Y en solo lienzo el editor no deja ningún aviso propio encima (el de «sin GPU» de Chromium sin tarjeta gráfica)."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_con_presentacion(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    st = p.evaluate("TVCad.pedir({op: 'state'})")
    assert st.get("canvas_only") is True and not st.get("modal") and not st.get("tool_windows"), {k: st.get(k) for k in ("canvas_only", "modal", "tool_windows")}
    p.locator("#tt-mas").click()
    p.locator("#tt-hoja").get_by_role("button", name="Modelo y presentaciones").click()
    p.locator("#tt-hoja").get_by_role("button", name="Hoja A1 plano").click()
    expect(p.locator("#tt-aviso")).to_contain_text("Hoja A1 plano")
    esperar(p, "document.querySelector('.tt-pestanas [data-grupo=\"dibujar\"]').getAttribute('aria-disabled') === 'true'", LARGO)
    assert p.locator('.tt-pestanas [data-grupo="medir"]').get_attribute("aria-disabled") == "false"
    assert p.evaluate("TVCad.pedir({op: 'layouts'}).then(r => String(r.current && (r.current.name || r.current)))") == "Hoja A1 plano"
    p.locator("#tt-mas").click()
    p.locator("#tt-hoja").get_by_role("button", name="Modelo y presentaciones").click()
    p.locator("#tt-hoja").get_by_role("button", name="Modelo").click()
    esperar(p, "document.querySelector('.tt-pestanas [data-grupo=\"dibujar\"]').getAttribute('aria-disabled') === 'false'", LARGO)
    assert not n.errores, n.errores
    if CAPTURAS:                                      # después de mirar la consola: en WebKit la captura de Playwright inyecta estilos
        p.screenshot(path=f"{CAPTURAS}/tactil-{motor}-sin-avisos.png", caret="initial")


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_mover_el_puntero_no_mueve_la_vista_ni_un_cambio_de_alto_la_escala(tactil, navegar, crear_usuario, roles, motor):
    """iOS: al arrastrar el asa (línea, segundo punto) la vista se corría y se acercaba, y al terminar volvía. La vista solo se mueve
    cuando la persona la mueve; si cambia el alto del área (barra de Safari, teclado), quedan el mismo centro y la misma escala."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _herramienta(p, "dibujar", "Línea")
    llevar_punta(p, X0, Y0); poner(p)
    v0, c0 = p.evaluate("TVCadTactil.vista()"), camara(p)
    ax, ay = p.evaluate("(() => { const v = TVCadTactil.puntero(); const [x, y] = TVCadTactil.aPantalla(v.mundo[0], v.mundo[1]); return [x, y + 88]; })()")
    # El dedo baja a 30 px del centro del asa y arrastra: se mueve el puntero, no el dibujo.
    p.evaluate("([x, y]) => __g.arrastrar(x + 30, y, -90, -60)", [ax, ay])
    p.wait_for_timeout(700)
    v1, c1 = p.evaluate("TVCadTactil.vista()"), camara(p)
    assert (v1["cx"], v1["cy"], v1["k"]) == (v0["cx"], v0["cy"], v0["k"]) and (c1["x"], c1["y"], c1["d"]) == (c0["x"], c0["y"], c0["d"])
    assert p.evaluate("TVCadTactil.puntero().mundo")[0] < X0
    # Cambia el alto del área: lo dibujado no se mueve (la esquina de arriba queda en su lugar, misma escala) y el editor dibuja lo
    # mismo que la capa.
    arriba0 = p.evaluate("(() => { const v = TVCadTactil.vista(); return TVCadTactil.aMundo(v.ox, v.oy); })()")
    tam = p.viewport_size
    p.set_viewport_size({"width": tam["width"], "height": tam["height"] - 70})
    p.wait_for_timeout(1500)
    v2, c2 = p.evaluate("TVCadTactil.vista()"), camara(p)
    arriba2 = p.evaluate("(() => { const v = TVCadTactil.vista(); return TVCadTactil.aMundo(v.ox, v.oy); })()")
    assert abs(v2["k"] / v0["k"] - 1) < 1e-6 and abs(arriba2[0] - arriba0[0]) < v0["k"] and abs(arriba2[1] - arriba0[1]) < v0["k"], (arriba0, arriba2)
    assert abs(c2["x"] - v2["cx"]) < v2["k"] and abs(c2["y"] - v2["cy"]) < v2["k"]
    assert not n.errores, n.errores


def test_circulo_escribiendo_centro_y_radio_en_la_lectura(tactil, navegar, crear_usuario, roles):
    """Lo que hacía «Círculo por coordenada»: X e Y del centro y el radio se escriben en la lectura del círculo."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, "chromium", p0=p0)
    p.locator('.tt-pestanas [data-grupo="dibujar"]').click()
    assert p.locator("#tt-hoja").get_by_role("button", name="Círculo por coordenada").count() == 0
    p.locator("#tt-hoja").get_by_role("button", name="Círculo", exact=True).click()
    _escribir(p, "x", "395030.5"); p.locator('#tt-din [data-d="siguiente"]').click(); _din(p, "3175012.25")
    p.locator('#tt-din [data-d="poner"]').click()
    expect(p.locator('.tt-valor[data-campo="r"]')).to_be_visible()
    _escribir(p, "r", "2.5")
    p.locator('#tt-din [data-d="poner"]').click()
    p.wait_for_timeout(500)
    r = p.evaluate("TVCad.pedir({op: 'query', type: 'Circle', detail: 'geometry'}).then(r => r.entities)")
    nuevo = [c for c in r if abs(c["radius"] - 2.5) < 1e-9]
    assert nuevo and abs(nuevo[0]["center"][0] - 395030.5) < 1e-9 and abs(nuevo[0]["center"][1] - 3175012.25) < 1e-9
    assert not n.errores, n.errores


def test_guardar_es_version_nueva_y_reabre_en_utm(tactil, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, "chromium", p0=p0)
    p.locator('.tt-pestanas [data-grupo="dibujar"]').click()
    p.locator("#tt-hoja").get_by_role("button", name="Rectángulo").click()
    llevar_punta(p, X0 + 20, Y0 + 20); poner(p)
    llevar_punta(p, X0 + 40, Y0 + 30); poner(p)
    esperar_estado(p, "sucio")
    expect(p.locator("#tt")).to_have_attribute("data-estado", "sucio")
    p.locator("#tt-guardar").click()
    esperar_estado(p, "guardado")
    assert VersionPlano.objects.filter(plano=p0).count() == 2
    p = n.ir(f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    r = p.evaluate("TVCad.pedir({op: 'query', type: 'Polyline', detail: 'geometry'}).then(r => r.entities)")
    assert any([X0 + 40, Y0 + 30] in [v[:2] for v in e.get("vertices", [])] for e in r)
    assert not n.errores, n.errores


def _herramienta(p, grupo, nombre):
    p.locator(f'.tt-pestanas [data-grupo="{grupo}"]').click()
    p.locator("#tt-hoja").get_by_role("button", name=nombre, exact=True).click()


def _teclear(p, texto):
    for ch in texto:
        p.locator(f'#tt-num [data-t="{ch}"]').click()


def _din(p, texto):
    """Teclea en el teclado de la entrada dinámica («-» es el botón ±)."""
    for ch in texto:
        p.locator(f'#tt-din [data-d="{"signo" if ch == "-" else ch}"]').click()


def _escribir(p, campo, texto):
    """Toca el valor de la lectura y lo escribe."""
    p.locator(f'.tt-valor[data-campo="{campo}"] .tt-valor__b').click()
    expect(p.locator("#tt-din")).to_be_visible()
    _din(p, texto)


def _revision(p):
    return p.evaluate("TVCad.pedir({op: 'state'}).then(s => s.documents.find(d => !d.start).revision)")


def test_cada_herramienta_cambia_el_dibujo_en_un_solo_paso(tactil, navegar, crear_usuario, roles):
    """Dibujar, cotas, texto y editar: cada uno agrega o cambia lo esperado y un «Deshacer» lo quita entero."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, "chromium", tam="tableta", p0=p0)

    def un_paso(accion, cambio=1):
        antes = entidades(p)
        accion()
        p.wait_for_timeout(400)
        assert entidades(p) == antes + cambio, accion.__name__
        p.locator("#tt-deshacer").click()
        p.wait_for_timeout(400)
        assert entidades(p) == antes, f"deshacer {accion.__name__}"

    def linea():
        _herramienta(p, "dibujar", "Línea")
        llevar_punta(p, X0, Y0); poner(p); llevar_punta(p, X0 + 20, Y0 + 20); poner(p); llevar_punta(p, X0 + 20, Y0); poner(p)
        p.locator("#tt-terminar").click()
    un_paso(linea, 2)

    def circulo_radio():
        _herramienta(p, "dibujar", "Círculo")
        llevar_punta(p, X0 + 10, Y0 + 10); poner(p)
        _escribir(p, "r", "4"); p.locator('#tt-din [data-d="poner"]').click()
    un_paso(circulo_radio)

    def arco():
        _herramienta(p, "dibujar", "Arco por 3 puntos")
        llevar_punta(p, X0, Y0); poner(p); llevar_punta(p, X0 + 10, Y0 + 10); poner(p); llevar_punta(p, X0 + 20, Y0); poner(p)
    un_paso(arco)

    def perpendicular():
        _herramienta(p, "dibujar", "Perpendicular")
        llevar_punta(p, X0 + 5, Y0 + 15); poner(p)
        llevar_punta(p, X0 + 12, Y0 + 0.3); p.locator("#tt-confirmar").click(); p.wait_for_timeout(300)
    un_paso(perpendicular)

    def cota():
        _herramienta(p, "cota", "Cota alineada")
        llevar_punta(p, X0, Y0); poner(p); llevar_punta(p, X0 + 20, Y0 + 20); poner(p); llevar_punta(p, X0 + 5, Y0 + 25); poner(p)
    un_paso(cota)

    def texto():
        _herramienta(p, "texto", "Texto")
        llevar_punta(p, X0 + 2, Y0 + 5); poner(p)
        p.locator("#tt-texto").fill("Pozo 3")
        p.locator('#tt-num [data-t="listo"]').click()
    un_paso(texto)

    # Editar: elegir el círculo existente tocándolo y moverlo 10 m al este con la coordenada escrita.
    sx, sy = punto_en_pantalla(p, X0 + 45, Y0 + 10)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    expect(p.locator("#tt-seleccion")).to_be_visible()
    rev = _revision(p)
    p.locator('#tt-seleccion [data-editar="mover"]').click()
    llevar_punta(p, X0 + 40, Y0 + 10); poner(p)
    _escribir(p, "x", "395050")
    p.locator('#tt-din [data-d="siguiente"]').click(); _din(p, "3175010")
    p.locator('#tt-din [data-d="poner"]').click()
    p.wait_for_timeout(500)
    c = p.evaluate("TVCad.pedir({op: 'query', type: 'Circle', detail: 'geometry'}).then(r => r.entities.filter(e => e.space === '*Model_Space'))")
    assert any(abs(e["center"][0] - (X0 + 50)) < 1e-9 for e in c), (c, p.evaluate("TVCadTactil.seleccion()"), p.locator("#tt-aviso").text_content())
    assert _revision(p) > rev
    # Desfasar el cuadro 2 m hacia afuera: una polilínea nueva de 24 × 24. Primero se ve todo (el cuadro quedó fuera de la vista).
    p.locator("#tt-seleccion-quitar").click()
    p.evaluate("__g.toques(300, 300, 3)")
    p.wait_for_timeout(1200)
    sx, sy = punto_en_pantalla(p, X0 + 20, Y0 + 5)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    try:
        expect(p.locator("#tt-seleccion")).to_be_visible(timeout=5000)
    except AssertionError:
        raise AssertionError(("sin selección", p.evaluate("([x, y]) => TVCadTactil.cercanos(x, y)", [sx, sy]), p.evaluate("TVCadTactil.vista()"), [sx, sy],
                              p.evaluate("TVCadTactil.puntero()"), p.evaluate("TVCadTactil.herramienta()"))) from None
    p.locator('#tt-seleccion [data-editar="desfasar"]').click()
    llevar_punta(p, X0 + 25, Y0 + 5)
    _escribir(p, "dist", "2"); p.locator('#tt-din [data-d="poner"]').click()
    p.wait_for_timeout(400)
    pl = p.evaluate("TVCad.pedir({op: 'query', type: 'Polyline', detail: 'geometry'}).then(r => r.entities.filter(e => e.space === '*Model_Space'))")
    assert any([X0 - 2, Y0 - 2] in [v[:2] for v in e["vertices"]] for e in pl), pl
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_al_cambiar_la_ventana_la_capa_y_el_editor_se_reacomodan(tactil, navegar, crear_usuario, roles, motor):
    """Android: al cerrarse el diálogo «Instalar app» quedó una copia de la barra de abajo y de la lectura a media pantalla. Tras
    cambiar la ventana, el editor ocupa exactamente el área de la capa, la barra queda abajo y la vista sigue en su lugar."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, p0=p0)
    _herramienta(p, "dibujar", "Línea")
    llevar_punta(p, X0, Y0)
    for alto in (640, 812, 700):
        p.set_viewport_size({"width": 375, "height": alto})
        p.wait_for_timeout(900)
        r = p.evaluate("""() => { const q = (s) => document.querySelector(s).getBoundingClientRect();
          const z = q('#tt-zona'), l = q('#cad-lienzo'), a = q('#tt-abajo'), x = q('#tt-lectura');
          return {zona: [z.top, z.left, z.width, z.height], lienzo: [l.top, l.left, l.width, l.height], abajo: a.bottom, alto: innerHeight,
                  lectura: x.bottom <= z.bottom + 0.5 && x.top >= z.top - 0.5}; }""")
        assert all(abs(a - b) < 0.6 for a, b in zip(r["zona"], r["lienzo"])), r
        assert abs(r["abajo"] - r["alto"]) < 0.6 and r["lectura"], r
        v, c = p.evaluate("TVCadTactil.vista()"), camara(p)
        assert abs(c["x"] - v["cx"]) < v["k"] and abs(c["y"] - v["cy"]) < v["k"], (c, v)
    assert not n.errores, n.errores


def test_solo_lectura_deja_ver_y_medir(tactil, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    p0 = plano_utm(ana)
    na, pa = abrir(navegar, ana, "chromium", p0=p0)
    nb, pb = abrir(navegar, beto, "chromium", p0=p0)
    expect(pb.locator("#tt")).to_have_attribute("data-estado", "lectura")
    assert pb.locator('.tt-pestanas [data-grupo="dibujar"]').get_attribute("aria-disabled") == "true"
    assert pb.locator('.tt-pestanas [data-grupo="medir"]').get_attribute("aria-disabled") == "false"
    expect(pb.locator("#cad-aviso-lectura")).to_contain_text("Ana López está editando este plano")
    pb.locator('.tt-pestanas [data-grupo="medir"]').click()
    pb.locator("#tt-hoja").get_by_role("button", name="Distancia", exact=True).click()
    antes = pb.evaluate("[TVCadTactil.puntero(), TVCadTactil.vista(), TVCadTactil.aPantalla(%r, %r)]" % (X0, Y0))
    llevar_punta(pb, X0, Y0)
    a = pb.evaluate("TVCadTactil.puntero()")
    assert abs(a["mundo"][0] - X0) < 1 and abs(a["mundo"][1] - Y0) < 1, (antes, a, pb.evaluate("TVCadTactil.vista()"))
    poner(pb)
    llevar_punta(pb, X0 + 20, Y0 + 20)
    b = pb.evaluate("TVCadTactil.puntero()"); poner(pb)
    try:
        expect(pb.locator("#tt-tarjeta")).to_contain_text("28.284", timeout=5000)
    except AssertionError:
        raise AssertionError((a, b, pb.locator("#tt-tarjeta").text_content(), pb.evaluate("TVCadTactil.vista()"))) from None
    assert not nb.errores, nb.errores


@pytest.mark.skipif(not CAPTURAS, reason="solo con ITT_CAPTURAS")
@pytest.mark.parametrize("tam", sorted(TAMANOS))
@pytest.mark.parametrize("tema", ["claro", "oscuro"])
def test_capturas_de_cada_pantalla(tactil, navegar, crear_usuario, roles, tam, tema):
    motor = os.environ.get("ITT_MOTOR", "chromium")
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n, p = abrir(navegar, ana, motor, tam=tam, tema=tema, p0=p0)
    base = f"{CAPTURAS}/tactil-{motor}-{tam}-{tema}"
    # caret="initial": si no, Playwright mete una hoja de estilos para ocultar el cursor de texto y la CSP de la página (bien) la rechaza.
    donde = []
    p.on("console", lambda m: donde.append((m.text[:60], m.location)) if "Refused" in m.text else None)
    p.wait_for_timeout(800)
    p.screenshot(path=f"{base}-1-navegar.png", caret="initial")
    p.locator('.tt-pestanas [data-grupo="dibujar"]').click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-2-dibujar.png", caret="initial")
    p.locator("#tt-hoja").get_by_role("button", name="Polilínea").click()
    llevar_punta(p, X0 + 0.2, Y0 + 0.2); poner(p)
    llevar_punta(p, X0 + 20.3, Y0 + 0.1)
    p.wait_for_timeout(400)
    p.screenshot(path=f"{base}-3-puntero.png", caret="initial")
    p.locator('.tt-valor[data-campo="d"] .tt-valor__b').click()
    for ch in "12.5":
        p.locator(f'#tt-din [data-d="{ch}"]').click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-4-numerica.png", caret="initial")
    p.locator('#tt-din [data-d="cerrar"]').click()
    p.locator("#tt-cancelar").click()
    p.locator('.tt-pestanas [data-grupo="medir"]').click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-5-medir.png", caret="initial")
    p.locator("#tt-hoja").get_by_role("button", name="Área y perímetro").click()
    for x, y in ((X0, Y0), (X0 + 20, Y0), (X0 + 20, Y0 + 20)):
        llevar_punta(p, x + 0.1, y + 0.1); poner(p)
    p.locator("#tt-terminar").click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-6-resultado.png", caret="initial")
    p.locator("#tt-tarjeta-cerrar").click()
    p.locator('.tt-pestanas [data-grupo="capas"]').click()
    p.wait_for_timeout(500)
    p.screenshot(path=f"{base}-7-capas.png", caret="initial")
    p.locator("#tt-hoja [data-tt-cerrar]").click()
    sx, sy = punto_en_pantalla(p, X0 + 10, Y0)
    p.evaluate("([x, y]) => __g.tocar(x, y)", [sx, sy])
    p.wait_for_timeout(900)
    p.screenshot(path=f"{base}-8-seleccion.png", caret="initial")
    p.locator("#tt-mas").click()
    p.wait_for_timeout(300)
    p.screenshot(path=f"{base}-9-mas.png", caret="initial")
    # WebKit: cada captura de Playwright inyecta una hoja de estilos (sin línea, «line 0») que la CSP rechaza; las pruebas sin capturas
    # comprueban que la página no tiene errores de CSP propios.
    propios = [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)]
    assert not propios, (propios, donde)


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_la_lupa_de_la_biblioteca_no_tapa_el_texto_del_buscador(tactil, navegar, crear_usuario, roles, motor):
    """iOS Safari (402 pt): la lupa tapaba la primera letra del texto del buscador («Ꝗuscar»)."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano_utm(ana)
    n = navegar(ana, motor, ancho=402, alto=874, movil=True)
    p = n.ir("/cad/")
    r = p.evaluate("""() => { const i = document.getElementById('cad-q'), l = document.querySelector('.cad-buscador > .tv-icono');
      const cs = getComputedStyle(i); return {texto: i.getBoundingClientRect().left + parseFloat(cs.paddingLeft), lupa: l.getBoundingClientRect().right,
      apariencia: cs.webkitAppearance || cs.appearance}; }""")
    assert r["texto"] >= r["lupa"] + 4 and r["apariencia"] == "none", r
    assert not n.errores, n.errores


# -- CAD sin conexión -------------------------------------------------------------------------------------------------------------

class TelefonoInstalado:
    """Como la app instalada: un perfil que se guarda (launch_persistent_context). Un contexto sin perfil de WebKit se porta como una
    ventana privada (no conserva Cache Storage al cambiar de página), y el CAD sin conexión solo se ofrece en la app instalada."""

    def __init__(self, playwright_, motor, servidor, cookie, carpeta, ancho=375, alto=812):
        tipo = getattr(playwright_, motor)
        args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if motor == "chromium" else []
        self.contexto = tipo.launch_persistent_context(str(carpeta), headless=True, args=args, viewport={"width": ancho, "height": alto},
                                                       is_mobile=motor == "chromium", has_touch=True, locale="es-MX", accept_downloads=True)
        self.contexto.add_cookies([cookie])
        self.errores = []
        self.pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        self.pagina.set_default_timeout(LARGO)
        self.pagina.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        self.servidor = servidor

    def ir(self, ruta):
        self.pagina.goto(self.servidor.url + ruta)
        return self.pagina


@pytest.fixture
def telefono(playwright_, servidor, sesion_de, tmp_path):
    abiertos = []

    def abrir_(usuario, motor="chromium"):
        t = TelefonoInstalado(playwright_, motor, servidor, sesion_de(usuario), tmp_path / f"perfil-{motor}-{len(abiertos)}")
        abiertos.append(t)
        return t
    yield abrir_
    for t in abiertos:
        t.contexto.close()


INSTALADA = """(() => { const mm = window.matchMedia.bind(window);
  window.matchMedia = (q) => /display-mode:\\s*standalone/.test(q) ? { matches: true, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} } : mm(q); })()"""


def _listo_sin_conexion(p, consola):
    """Espera la capa táctil en la página sin conexión; si no llega, dice qué se ve (para depurar sin red)."""
    try:
        esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    except AssertionError:
        try:
            vista = p.evaluate("""() => ({url: location.href, titulo: document.title, sw: !!navigator.serviceWorker.controller,
              mensaje: (document.getElementById('sc-mensaje-texto') || {}).textContent, estado: (document.getElementById('cad-estado-texto') || {}).textContent,
              marco: (document.getElementById('cad-editor') || {}).src, cuerpo: document.body.innerText.slice(0, 120)})""")
            vista["caches"] = p.evaluate("""async () => { const r = {}; for (const n of await caches.keys()) r[n] = (await (await caches.open(n)).keys()).length;
              const pg = await caches.open('itt-cad-pagina'); r.pagina = (await pg.keys()).map(q => q.url).slice(0, 12); return r; }""")
        except Exception as exc:  # noqa: BLE001
            vista = str(exc)
        raise AssertionError(f"La página sin conexión no abrió el plano: {vista}; consola: {consola[-15:]}") from None


def _llevar_al_telefono(n, p, p0):
    """«Más › Sin conexión»: descargar el CAD (app instalada) y marcar el plano como disponible sin conexión."""
    p.locator("#tt-mas").click()
    p.locator("#tt-hoja").get_by_role("button", name="Sin conexión").click()
    # Antes de bajar se dice cuánto se descarga y cuánto ocupa (después decía otra cifra y confundía).
    expect(p.locator("#tt-hoja")).to_contain_text(re.compile(r"Se descarga una sola vez \(unos \d+ MB; ocupa unos \d+ MB en el teléfono\)"))
    p.locator("#tt-hoja").get_by_role("button", name="Descargar CAD en este teléfono").click()
    expect(p.locator("#tt-aviso")).to_contain_text("el CAD funciona sin conexión", timeout=LARGO * 3)
    p.locator("#tt-hoja").get_by_role("switch", name="Disponible sin conexión").check()
    expect(p.locator("#tt-aviso")).to_contain_text("quedó en este teléfono", timeout=LARGO)
    esperar(p, "navigator.serviceWorker.getRegistration().then(r => !!(r && r.active))", LARGO)
    # La página sin conexión quedó en su caché (con sus estilos y scripts).
    guardada = p.evaluate("""async () => { const c = await caches.open('itt-cad-pagina'); const k = (await c.keys()).map(r => new URL(r.url).pathname);
      if (k.includes('/cad/sin-conexion/')) return k.length;
      try { await TVCadCampo.guardarPagina(); } catch (e) { return 'error: ' + e; }
      return 'otra vez: ' + JSON.stringify((await (await caches.open('itt-cad-pagina')).keys()).map(r => r.url)); }""")
    assert isinstance(guardada, int) and guardada > 5, guardada


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_sin_conexion_se_dibuja_en_el_telefono_y_al_volver_es_version_nueva(tactil, telefono, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    n = telefono(ana, motor)
    n.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
    n.contexto.add_init_script(INSTALADA)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    _llevar_al_telefono(n, p, p0)
    p.goto(n.servidor.url + "/cad/")                                         # suelta el bloqueo de esta página
    p0.refresh_from_db()
    assert not servicio.bloqueo_activo(p0)                                   # llevarlo al teléfono no tomó el bloqueo
    tras = p.evaluate("caches.open('itt-cad-pagina').then(c => c.keys()).then(k => k.length)")
    assert tras > 5, f"la página sin conexión se perdió al cambiar de página: {tras}"
    # Modo avión: el mismo enlace del plano abre la página sin conexión con el editor descargado.
    consola = []
    p.on("console", lambda m: consola.append(f"{m.type}: {m.text[:160]}"))
    n.contexto.set_offline(True)
    p.goto(n.servidor.url + f"/cad/{p0.pk}/")
    _listo_sin_conexion(p, consola)
    expect(p.locator("#tt-nombre")).to_have_text("Lotes Norte")
    p.evaluate(GESTOS)
    p.locator('.tt-pestanas [data-grupo="dibujar"]').click()
    p.locator("#tt-hoja").get_by_role("button", name="Círculo", exact=True).click()
    _escribir(p, "x", "395050"); p.locator('#tt-din [data-d="siguiente"]').click(); _din(p, "3175010")
    p.locator('#tt-din [data-d="poner"]').click()
    _escribir(p, "r", "3"); p.locator('#tt-din [data-d="poner"]').click()
    expect(p.locator("#tt")).to_have_attribute("data-estado", "sucio")
    p.locator("#tt-guardar").click()
    expect(p.locator("#tt-sub-texto")).to_have_text("Guardado en el teléfono")
    assert VersionPlano.objects.filter(plano=p0).count() == 1               # sin conexión nunca es una versión
    # Vuelve la señal: la página del plano ofrece guardar los cambios del teléfono como versión nueva.
    n.contexto.set_offline(False)
    p.goto(n.servidor.url + f"/cad/{p0.pk}/")
    boton = p.locator("#cad-campo-subir")
    expect(boton).to_have_text("Guardar como versión nueva", timeout=LARGO)
    boton.click()
    esperar(p, "!document.getElementById('cad-aviso-campo')", LARGO)
    p0.refresh_from_db()
    assert p0.revision == 2 and b"CIRCLE" in servicio.ruta_version(servicio.version_vigente(p0)).read_bytes()
    assert not n.errores, n.errores


def test_sin_conexion_si_otro_guardo_mientras_tanto_es_una_copia(tactil, telefono, crear_usuario, roles):
    from itt_herramientas.models import Plano

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    p0 = plano_utm(ana)
    n = telefono(ana)
    n.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
    n.contexto.add_init_script(INSTALADA)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    _llevar_al_telefono(n, p, p0)
    p.goto(n.servidor.url + "/cad/")
    consola = []
    p.on("console", lambda m: consola.append(f"{m.type}: {m.text[:160]}"))
    n.contexto.set_offline(True)
    p.goto(n.servidor.url + f"/cad/sin-conexion/?plano={p0.pk}")
    _listo_sin_conexion(p, consola)
    p.evaluate("TVCad.ejecutar('CIRCLE 395060,3175010 4')")
    expect(p.locator("#tt")).to_have_attribute("data-estado", "sucio")
    p.locator("#tt-guardar").click()
    expect(p.locator("#tt-sub-texto")).to_have_text("Guardado en el teléfono")
    # Mientras tanto Beto guarda la versión 2.
    s = uuid.uuid4().hex[:20]
    servicio.bloqueo(p0.pk, beto, s, "tomar")
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(servicio.ruta_version(servicio.version_vigente(p0)).read_bytes())
    servicio.guardar(p0.pk, beto, s, 1, t)
    servicio.bloqueo(p0.pk, beto, s, "soltar")
    n.contexto.set_offline(False)
    p.goto(n.servidor.url + "/cad/sin-conexion/")
    p.get_by_role("button", name="Subir cambios").click()
    expect(p.locator("#tv-avisos")).to_contain_text("tus cambios quedaron en «Lotes Norte (copia)»", timeout=LARGO)
    p0.refresh_from_db()
    assert p0.revision == 2                                                  # nada se pisó
    copia = Plano.objects.get(nombre="Lotes Norte (copia)")
    assert b"CIRCLE" in servicio.ruta_version(servicio.version_vigente(copia)).read_bytes()
    assert not n.errores, n.errores


def test_planos_en_este_telefono_dice_sin_conexion_si_no_se_llega_al_servidor(tactil, navegar, crear_usuario, roles):
    """iOS: con el servidor detenido decía «Con conexión» porque solo miraba navigator.onLine."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    n = navegar(ana, "chromium", ancho=375, alto=812, movil=True)
    p = n.ir("/cad/sin-conexion/")
    expect(p.locator("#sc-red")).to_have_text("Con conexión")
    n.contexto.route("**/sesion/", lambda r: r.abort())
    p.reload()
    expect(p.locator("#sc-red")).to_contain_text("Sin conexión con el servidor")
    assert not n.errores, n.errores

