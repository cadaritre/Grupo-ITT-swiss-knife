"""CAD en la computadora con el editor real: Esc como en AutoCAD en cada camino y varios planos en una ventana (pestañas).

Esc (docs/cad.md, «Esc»): un Esc cancela la orden en curso y, sin orden, quita la selección y los pinzamientos, con el foco en el lienzo,
en la línea de órdenes con texto escrito, en nuestra cabecera, después de cerrar una hoja nuestra, en «Pantalla completa» (con el
botón y con el atajo), y nunca sale de la pantalla completa ni de la página. Chromium con el agente de Windows (el navegador del dueño) y
WebKit.

Pestañas (docs/cad.md, «Varios planos en una ventana»): abrir otro plano desde la hoja, dos y tres pestañas, editar en las dos, Guardar
todo (cada una su versión), una pestaña cuyo bloqueo tomó otra persona, cerrar con cambios (pregunta solo esa), recargar (vuelven las
mismas), copiar y pegar entre pestañas, el máximo. Corre bajo el candado de la máquina compartida.
"""
from __future__ import annotations

import os

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio
from itt_herramientas.models import VersionPlano

from .test_cad_navegador import LARGO, Navegador, _persona, cad, esperar_estado  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
MOTORES = ["chromium", "webkit"]


@pytest.fixture
def abrir(playwright_, servidor, sesion_de):
    abiertos = []

    def _abrir(usuario, motor="chromium", **kw):
        if motor == "chromium":
            kw.setdefault("user_agent", WINDOWS)
        n = Navegador(playwright_, motor, servidor, sesion_de(usuario), **kw)
        abiertos.append(n)
        return n
    yield _abrir
    for n in abiertos:
        n.cerrar()


def pedir(p, peticion, ms=60000):
    return p.evaluate("([q, t]) => TVCad.pedir(q, {tiempoMs: t})", [peticion, ms])


def estado(p):
    s = pedir(p, {"op": "state"})
    return {"seleccion": s.get("selection") or [], "orden": s.get("command"), "documento": s.get("document_id")}


def pestanas(p):
    return p.evaluate("TVCadPestanas.lista()")


def esperar_pestanas(p, n, cargadas=True):
    esperar(p, f"TVCadPestanas.lista().length === {n} && TVCadPestanas.lista().every(t => !{str(cargadas).lower()} || t.cargado)", LARGO)


def esperar_pestana(p, nombre, estado_):
    esperar(p, f"TVCadPestanas.lista().some(t => t.nombre === {nombre!r} && t.estado === {estado_!r})", LARGO)


def circulo(p, x=390000, y=3168000, r=12):
    esperar(p, "window.TVCad && TVCad.estaListo()")
    assert p.evaluate("c => TVCad.ejecutar(c).then(r => r.ok !== false)", f"CIRCLE {x},{y} {r}")
    pedir(p, {"op": "run", "cmd": "ZOOM E"})


def un_circulo(p):
    q = pedir(p, {"op": "query", "type": "Circle", "detail": "summary", "limit": 1})
    return q["entities"][0]["handle"] if q.get("entities") else None


def cuantos(p, tipo="Circle"):
    return pedir(p, {"op": "query", "type": tipo, "detail": "summary", "limit": 1}).get("count", 0)


def completa(p):
    return p.evaluate("document.documentElement.classList.contains('cad-completa')")


def esc_y_esperar(p, como="teclado"):
    p.keyboard.press("Escape")
    p.wait_for_timeout(500)


def abrir_desde_la_hoja(p, nombre):
    p.locator("#cad-abrir-otro").click()
    hoja = p.locator("#cad-elegir")
    expect(hoja).to_be_visible()
    p.locator("#cad-elegir-q").fill(nombre)
    # La búsqueda se rehace 250 ms después de escribir y reemplaza la lista: se pulsa en la lista ya filtrada, no en la de antes (un clic
    # sobre un botón que se está reemplazando se pierde).
    esperar(p, "(() => { const b = [...document.querySelectorAll('#cad-elegir-lista .cad-elegir__plano')]; const e = document.getElementById('cad-elegir-estado').textContent;"
               f" return !e.startsWith('Buscando') && b.length > 0 && b.every(x => x.textContent.includes({nombre!r})); }})()", LARGO)
    boton = hoja.locator(".cad-elegir__plano", has_text=nombre).first
    expect(boton).to_be_visible(timeout=LARGO)
    boton.click()
    expect(hoja).to_be_hidden()


# -- Esc ----------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_un_esc_cancela_y_deselecciona_en_cada_camino(cad, abrir, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Esc", "", "dxf")
    n = abrir(ana, motor, ancho=1280, alto=800)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    circulo(p)
    h = un_circulo(p)
    lienzo = p.locator("#cad-editor")
    # Con SwiftShader el editor abre su «Aviso de gráficos» junto al dibujo; Esc lo cierra primero, como cualquier diálogo del editor.
    lienzo.click(position={"x": 700, "y": 200})
    for _ in range(3):
        if not pedir(p, {"op": "state"}).get("tool_windows"):
            break
        esc_y_esperar(p)
    assert not pedir(p, {"op": "state"}).get("tool_windows")

    def seleccionado():
        pedir(p, {"op": "select", "handles": [h]})
        assert estado(p)["seleccion"] == [h]

    caminos = []
    # 1. Foco en el lienzo, con selección.
    lienzo.click(position={"x": 700, "y": 200})
    seleccionado(); esc_y_esperar(p)
    caminos.append(("lienzo", estado(p)["seleccion"]))
    # 2. Foco en nuestra cabecera (un clic en el nombre).
    p.locator("#cad-nombre").click()
    seleccionado(); esc_y_esperar(p)
    caminos.append(("cabecera", estado(p)["seleccion"]))
    # 3. Una hoja nuestra abierta: el primer Esc la cierra (y no toca el dibujo); el siguiente es del editor.
    p.locator("#cad-boton-versiones").click()
    expect(p.locator("#cad-versiones")).to_be_visible()
    seleccionado(); esc_y_esperar(p)
    expect(p.locator("#cad-versiones")).to_be_hidden()
    assert estado(p)["seleccion"] == [h], "Esc cerró la hoja; el dibujo no se tocó"
    esc_y_esperar(p)
    caminos.append(("tras la hoja", estado(p)["seleccion"]))
    # 4. Una orden con coordenadas escritas en la línea de órdenes: un solo Esc la termina (AutoCAD: «*Cancelar*»).
    lienzo.click(position={"x": 700, "y": 200})
    p.keyboard.type("LINE"); p.keyboard.press("Enter"); p.wait_for_timeout(300)
    lienzo.click(position={"x": 760, "y": 240}); p.wait_for_timeout(300)
    p.keyboard.type("10,10"); p.wait_for_timeout(200)
    assert estado(p)["orden"], "LINE sigue en curso"
    esc_y_esperar(p)
    caminos.append(("orden con texto", estado(p)["orden"]))
    # 5. Una orden en curso y el foco en nuestra página (clic en el estado del plano).
    lienzo.click(position={"x": 700, "y": 200})
    p.keyboard.type("CIRCLE"); p.keyboard.press("Enter"); p.wait_for_timeout(300)
    assert estado(p)["orden"]
    p.locator("#cad-estado").click(); p.locator("body").press("Escape"); p.wait_for_timeout(600)
    caminos.append(("orden, foco en la página", estado(p)["orden"]))
    # 6. Pantalla completa con el botón: el foco quedaba en el botón.
    p.locator("#cad-completa").click()
    esperar(p, "document.documentElement.classList.contains('cad-completa')", LARGO)
    p.wait_for_timeout(500)
    seleccionado(); esc_y_esperar(p)
    caminos.append(("pantalla completa, botón", estado(p)["seleccion"]))
    assert completa(p)
    if motor == "chromium":
        assert p.evaluate("!!document.fullscreenElement"), "Esc no sacó de la pantalla del navegador"
    # 7. Pantalla completa, foco en el lienzo, con una orden y luego con selección.
    lienzo.click(position={"x": 700, "y": 200})
    p.keyboard.type("LINE"); p.keyboard.press("Enter"); p.wait_for_timeout(300); p.keyboard.type("5,5")
    esc_y_esperar(p)
    caminos.append(("pantalla completa, orden", estado(p)["orden"]))
    seleccionado(); esc_y_esperar(p)
    caminos.append(("pantalla completa, selección", estado(p)["seleccion"]))
    assert completa(p)
    # 8. Atajo de pantalla completa con el foco en el editor (F11 lo reenvía la página del editor): sale; Esc no.
    lienzo.click(position={"x": 700, "y": 200})
    p.keyboard.press("F11")
    esperar(p, "!document.documentElement.classList.contains('cad-completa')", LARGO)
    for nombre, valor in caminos:
        assert not valor, (nombre, valor, caminos)
    assert p.url.endswith(f"/cad/{p0.pk}/"), "Esc nunca navega"
    assert estado(p)["documento"]


# -- Pestañas -----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_dos_y_tres_pestanas_guardar_todo_y_recargar(cad, abrir, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    a = servicio.crear_vacio(ana, "Predio Norte", "Agrícola Norte", "dxf")
    b = servicio.crear_vacio(ana, "Predio Sur", "Constructora Sur", "dwg")
    c = servicio.crear_vacio(ana, "Predio Este", "", "dxf")
    n = abrir(ana, motor, ancho=1366, alto=860)
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    expect(p.locator("#cad-pestanas")).to_be_visible()
    esperar_pestanas(p, 1)
    # Abrir otro plano desde la hoja: nueva pestaña, con su cabecera.
    abrir_desde_la_hoja(p, "Predio Sur")
    esperar_pestanas(p, 2)
    esperar_estado(p, "guardado")
    expect(p.locator("#cad-nombre")).to_have_text("Predio Sur")
    expect(p.locator("#cad-subtitulo")).to_contain_text("Constructora Sur")
    assert p.url.endswith(f"/cad/{b.pk}/")
    # Editar en las dos.
    circulo(p, 500000, 2000000, 5)
    esperar_pestana(p, "Predio Sur", "sucio")
    p.locator(".cad-pestana", has_text="Predio Norte").click()
    esperar(p, "TVCadPestanas.lista().find(t => t.activa).nombre === 'Predio Norte'", LARGO)
    expect(p.locator("#cad-nombre")).to_have_text("Predio Norte")
    assert cuantos(p) == 0, "la pestaña de Norte muestra su propio dibujo"
    circulo(p)
    esperar_pestana(p, "Predio Norte", "sucio")
    assert p.locator(".cad-pestana[data-estado=sucio]").count() == 2
    # La tercera.
    abrir_desde_la_hoja(p, "Predio Este")
    esperar_pestanas(p, 3)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-pestanas-{motor}-3.png")
    # Guardar todo con el atajo: una versión de cada plano con cambios, la del que no cambió no.
    p.locator("#cad-editor").click(position={"x": 500, "y": 400})
    p.keyboard.press(("Meta" if motor == "webkit" else "Control") + "+Shift+S")
    esperar(p, "TVCadPestanas.lista().every(t => t.estado === 'guardado')", LARGO)
    assert VersionPlano.objects.filter(plano=a).count() == 2 and VersionPlano.objects.filter(plano=b).count() == 2
    assert VersionPlano.objects.filter(plano=c).count() == 1
    assert b"CIRCLE" in servicio.ruta_version(VersionPlano.objects.get(plano=a, numero=2)).read_bytes()
    # Cada plano conserva su bloqueo de su pestaña.
    for x in (a, b, c):
        x.refresh_from_db()
        assert servicio.bloqueo_activo(x)
    assert len({x.bloqueo_sesion for x in (a, b, c)}) == 3
    # Recargar: vuelven las mismas tres, la activa es la misma.
    activa = [t for t in pestanas(p) if t["activa"]][0]["nombre"]
    p.reload()
    esperar_pestanas(p, 3)
    assert [t["nombre"] for t in pestanas(p)] == ["Predio Norte", "Predio Sur", "Predio Este"]
    esperar(p, f"TVCadPestanas.lista().find(t => t.activa).nombre === {activa!r}", LARGO)
    expect(p.locator("#cad-nombre")).to_have_text(activa)
    assert all(t["editable"] for t in pestanas(p))
    assert not [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)], n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_guardar_todo_dice_cual_no_se_guardo_y_cerrar_pregunta_solo_esa(cad, abrir, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "admin")
    a = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    b = servicio.crear_vacio(ana, "Predio Sur", "", "dxf")
    n = abrir(ana, motor)
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    p.evaluate("id => TVCadPestanas.abrir(id)", str(b.pk))
    esperar_pestanas(p, 2)
    circulo(p)
    p.evaluate("id => TVCadPestanas.activar(id)", str(a.pk))
    circulo(p)
    esperar(p, "TVCadPestanas.lista().every(t => t.sucio)", LARGO)
    # Otra persona toma el control de Sur.
    servicio.bloqueo(b.pk, beto, "sesion-de-beto-00001", "forzar")
    r = p.evaluate("TVCadPestanas.guardarTodo()")
    filas = {f["id"]: f for f in r["filas"]}
    assert filas[str(a.pk)]["ok"] and not filas[str(b.pk)]["ok"], r
    assert "Beto" in filas[str(b.pk)]["motivo"], r
    expect(p.locator("#cad-guardar-todo")).to_be_visible()
    expect(p.locator("#cad-guardar-todo-titulo")).to_have_text("Se guardaron 1 de 2 planos")
    expect(p.locator("#cad-guardar-todo-lista li[data-estado=no]")).to_contain_text("Predio Sur")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-guardar-todo-{motor}.png")
    p.locator("#cad-guardar-todo [data-tv-cerrar]").click()
    assert VersionPlano.objects.filter(plano=a).count() == 2 and VersionPlano.objects.filter(plano=b).count() == 1
    # Cerrar Sur (con cambios): pregunta por Sur, solo por Sur; «Cancelar» la deja.
    sur = p.locator(".cad-pestana", has_text="Predio Sur")
    sur.locator(".cad-pestana__cerrar").click()
    expect(p.locator("#cad-cerrar")).to_be_visible()
    expect(p.locator("#cad-cerrar-titulo")).to_have_text("¿Guardar los cambios de «Predio Sur»?")
    p.locator("#cad-cerrar [data-tv-cerrar]").click()
    esperar_pestanas(p, 2)
    # «Cerrar sin guardar»: se cierra Sur y su bloqueo queda libre de esta página; Norte sigue, con su bloqueo.
    sur.locator(".cad-pestana__cerrar").click()
    p.locator("#cad-cerrar-sin").click()
    esperar_pestanas(p, 1)
    expect(p.locator("#cad-nombre")).to_have_text("Predio Norte")
    assert [t["nombre"] for t in pestanas(p)] == ["Predio Norte"]
    a.refresh_from_db()
    assert servicio.bloqueo_activo(a)
    # Salir con cambios en una sola pestaña: la hoja de siempre.
    circulo(p, 390100, 3168100, 3)
    esperar_estado(p, "sucio")
    p.locator("#cad-volver").click()
    expect(p.locator("#cad-salir")).to_be_visible()
    p.locator("#cad-salir [data-tv-cerrar]").click()


@pytest.mark.parametrize("motor", MOTORES)
def test_copiar_y_pegar_entre_pestanas(cad, abrir, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    a = servicio.crear_vacio(ana, "Origen", "", "dxf")
    b = servicio.crear_vacio(ana, "Destino", "", "dxf")
    n = abrir(ana, motor)
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    circulo(p, 390000, 3168000, 7)
    h = un_circulo(p)
    pedir(p, {"op": "select", "handles": [h]})
    assert p.evaluate("TVCad.ejecutar('COPYCLIP').then(r => r.ok !== false)")
    p.evaluate("id => TVCadPestanas.abrir(id)", str(b.pk))
    esperar_pestanas(p, 2)
    esperar(p, "TVCad.estado().plano.nombre === 'Destino'", LARGO)
    assert cuantos(p) == 0
    r = p.evaluate("TVCad.ejecutar('PASTECLIP 390000,3168000').then(r => r.ok !== false)")
    assert r
    esperar(p, "TVCad.pedir({op: 'query', type: 'Circle', detail: 'summary', limit: 1}).then(q => q.count === 1)", LARGO)
    esperar_pestana(p, "Destino", "sucio")
    # El origen no cambió.
    p.evaluate("id => TVCadPestanas.activar(id)", str(a.pk))
    esperar(p, "TVCad.estado().plano.nombre === 'Origen'", LARGO)
    assert cuantos(p) == 1


def test_el_maximo_de_pestanas_se_dice_claro(cad, abrir, crear_usuario, roles, settings):
    settings.HERRAMIENTAS_CAD_PESTANAS_MAX = 2
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    planos = [servicio.crear_vacio(ana, f"Plano {i}", "", "dxf") for i in range(3)]
    n = abrir(ana, "chromium")
    p = n.ir(f"/cad/{planos[0].pk}/")
    esperar_estado(p, "guardado")
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push(t); return a && a(t, k); }; }")
    p.evaluate("id => TVCadPestanas.abrir(id)", str(planos[1].pk))
    esperar_pestanas(p, 2)
    assert p.evaluate("id => TVCadPestanas.abrir(id)", str(planos[2].pk)) is None
    assert "Ya tienes 2 planos abiertos, el máximo en una ventana. Cierra uno para abrir otro." in p.evaluate("window.__avisos")
    esperar_pestanas(p, 2)


def test_la_memoria_del_editor_pone_tope_al_tamano_de_lo_abierto(cad, abrir, crear_usuario, roles, settings):
    """Entre todas las pestañas, a lo más HERRAMIENTAS_CAD_PESTANAS_BYTES de planos (la memoria del editor en el navegador)."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    a = servicio.crear_vacio(ana, "Chico", "", "dxf")
    b = servicio.crear_vacio(ana, "Otro", "", "dxf")
    settings.HERRAMIENTAS_CAD_PESTANAS_BYTES = a.bytes + b.bytes - 1
    n = abrir(ana, "chromium")
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    p.evaluate("() => { window.__avisos = []; const x = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push(t); return x && x(t, k); }; }")
    assert p.evaluate("id => TVCadPestanas.abrir(id)", str(b.pk)) is None
    assert "Los planos abiertos ya ocupan casi toda la memoria del editor en el navegador. Cierra uno para abrir «Otro»." in p.evaluate("window.__avisos")
    esperar_pestanas(p, 1)


def test_la_misma_pestana_no_se_abre_dos_veces_y_solo_lectura_se_ve(cad, abrir, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    a = servicio.crear_vacio(ana, "Mío", "", "dxf")
    b = servicio.crear_vacio(beto, "De Beto", "", "dxf")
    servicio.bloqueo(b.pk, beto, "sesion-de-beto-00001", "tomar")
    n = abrir(ana, "chromium")
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    p.evaluate("id => TVCadPestanas.abrir(id)", str(b.pk))
    esperar_pestanas(p, 2)
    esperar_estado(p, "lectura")
    expect(p.locator("#cad-aviso-lectura-texto")).to_contain_text("Beto Ruiz está editando este plano")
    expect(p.locator(".cad-pestana", has_text="De Beto")).to_have_attribute("data-estado", "lectura")
    p.evaluate("id => TVCadPestanas.abrir(id)", str(a.pk))
    esperar_pestanas(p, 2)
    expect(p.locator("#cad-nombre")).to_have_text("Mío")
    expect(p.locator("#cad-aviso-lectura")).to_be_hidden()


DIBUJOS = os.environ.get("TV_CAD_DIBUJOS", "")


@pytest.mark.skipif(not DIBUJOS, reason="medición: TV_CAD_DIBUJOS con los planos de tresvizo/pruebas/generar.py --grande del fork")
def test_memoria_con_varios_planos(cad, abrir, crear_usuario, roles, settings):
    """Para fijar el máximo de pestañas: la memoria del editor (WebAssembly, que nunca se devuelve) con cada plano que se abre."""
    from pathlib import Path

    settings.TV_HERRAMIENTAS_LIMITES = {"cad": {"bytes": 200 * 1048576}}
    settings.HERRAMIENTAS_CAD_PESTANAS_BYTES = 1024 * 1048576
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    base = Path(DIBUJOS)
    nombres = ["topo-utm13.dxf", "topo-utm14.dxf", "topo-origen.dxf", "topo-utm13-50k.dxf", "topo-utm13.dxf", "topo-utm14.dxf",
               "topo-origen.dxf", "topo-utm13.dxf"]
    planos = [servicio.crear_desde_ruta(ana, base / x, f"{i + 1} {x}") for i, x in enumerate(nombres)]
    n = abrir(ana, "chromium")
    n.contexto.add_init_script("addEventListener('TrunkApplicationStarted', (e) => { window.__wasm = e.detail && e.detail.wasm; })")
    p = n.ir(f"/cad/{planos[0].pk}/")
    esperar_estado(p, "guardado")
    memoria = "(() => { try { const w = document.getElementById('cad-editor').contentWindow.__wasm; return w.memory.buffer.byteLength; } catch (e) { return -1; } })()"
    filas = [(1, planos[0].bytes, p.evaluate(memoria))]
    for k, x in enumerate(planos[1:], start=2):
        p.evaluate("id => TVCadPestanas.abrir(id)", str(x.pk))
        esperar_pestanas(p, k)
        p.wait_for_timeout(1500)
        filas.append((k, x.bytes, p.evaluate(memoria)))
    lineas = [f"{k} pestañas · último {b / 1048576:.1f} MB · memoria del editor {m / 1048576:.0f} MB" for k, b, m in filas]
    print("\n".join(lineas))
    salida = os.environ.get("TV_CAD_MEDICION", "")
    if salida:
        Path(salida).write_text("\n".join(lineas) + "\n", encoding="utf-8")
    assert filas[-1][2] < 4 * 1024 ** 3
