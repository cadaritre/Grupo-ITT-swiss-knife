"""CAD con el editor real: presentaciones desde plantilla (docs/cad.md, «Presentaciones desde plantilla»).

Clic derecho en la pestaña de una presentación del editor → «Desde plantilla…» → cada fuente (Mis plantillas, De la biblioteca, De esta
computadora) → elegir presentaciones → insertadas con su marco, atributos, ventanas y página, en un paso de deshacer; «Guardar como
plantilla…» → aparece en «Mis plantillas»; lo de esta computadora no se sube; solo lectura y bloqueo ajeno sin las opciones; con dos
pestañas, la de la vista. Chromium (con el agente de Windows del dueño) y WebKit, un motor a la vez, bajo el candado de la máquina.

La plantilla es sintética (un marco genérico con atributos), hecha aquí con ezdxf.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import ezdxf
import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio
from itt_herramientas.models import Plano, PlantillaCad

from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, esperar_estado  # noqa: F401 (fixtures)
from .test_cad_pestanas_navegador import WINDOWS, abrir, abrir_desde_la_hoja, esperar_pestanas, pedir  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
MOTORES = ["chromium", "webkit"]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
MARCA_LOCAL = "PLANTILLA-LOCAL-NO-SUBIR"


def plantilla(ruta: Path, nota: str = "PROYECTO DE PRUEBA") -> Path:
    """Un marco genérico: bloque MARCO con atributos, ventanas «planta» (bloqueada, capa COTAS inutilizada) y «detalle», ISO A1 y A3."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.styles.add("ROTULO", font="romans.shx")
    for nombre, color in [("MARCO", 7), ("ROTULO", 2), ("VENTANAS", 8), ("COTAS", 1)]:
        doc.layers.add(nombre, color=color)
    blk = doc.blocks.new("MARCO")
    blk.add_lwpolyline([(0, 0), (821, 0), (821, 574), (0, 574)], close=True, dxfattribs={"layer": "MARCO"})
    blk.add_lwpolyline([(580, 0), (821, 0), (821, 90), (580, 90)], close=True, dxfattribs={"layer": "MARCO"})
    for i, tag in enumerate(["PROYECTO", "PLANO", "ESCALA"]):
        blk.add_attdef(tag, (600, 70 - 15 * i), tag.title(), dxfattribs={"height": 3.5, "style": "ROTULO", "layer": "ROTULO"})
    for nombre, (w, h), papel in [("A1 Marco", (841, 594), "ISO_A1_(841.00_x_594.00_MM)"), ("A3 Vertical", (297, 420), "ISO_A3_(297.00_x_420.00_MM)")]:
        lay = doc.layouts.new(nombre)
        lay.page_setup(size=(w, h), margins=(0, 0, 0, 0), units="mm", offset=(5, 7), scale=1, name=papel)
        ins = lay.add_blockref("MARCO", (10, 10), dxfattribs={"layer": "MARCO", "xscale": w / 841, "yscale": h / 594})
        ins.add_auto_attribs({"PROYECTO": nota, "PLANO": "PLANTA", "ESCALA": "1:500"})
        vp = lay.add_viewport(center=(w * 0.35, h * 0.5), size=(w * 0.5, h * 0.6), view_center_point=(100, 60), view_height=200,
                              dxfattribs={"layer": "VENTANAS"})
        vp.dxf.flags = vp.dxf.flags | 16384
        vp.frozen_layers = ["COTAS"]
        lay.add_viewport(center=(w * 0.8, h * 0.6), size=(w * 0.2, h * 0.2), view_center_point=(50, 30), view_height=8,
                         dxfattribs={"layer": "VENTANAS"})
    for n in [n for n in doc.layouts.names() if n.startswith("Layout")]:
        doc.layouts.delete(n)
    doc.saveas(ruta)
    return ruta


def plano_de(usuario, ruta: Path, nombre: str, proyecto: str = "") -> Plano:
    temporal = servicio.carpeta_temporal() / f"{nombre}.tmp"
    temporal.write_bytes(ruta.read_bytes())
    return servicio.crear(usuario, temporal, nombre, proyecto)


def presentaciones(p):
    return pedir(p, {"op": "layouts"})["layouts"]


def sin_avisos_del_editor(p):
    """Con SwiftShader el editor abre su «Aviso de gráficos» junto al dibujo (encima de la barra de estado): se acepta, como haría la
    persona, antes de usar los menús del editor."""
    for _ in range(4):
        st = pedir(p, {"op": "state"})
        if not st.get("modal") and not st.get("tool_windows"):
            return
        pedir(p, {"op": "action", "name": "close_modal"})
        p.wait_for_timeout(300)


def menu_de_pestana(p, pestana: str, item: str):
    """Clic derecho en la pestaña `pestana` del editor y clic en `item` (from_template | save_as_template) de su menú."""
    sin_avisos_del_editor(p)
    marco = p.locator("#cad-editor").bounding_box()
    limite = time.monotonic() + 30
    while True:
        r = pedir(p, {"op": "layouts"})
        if pestana in r.get("tabs", {}):
            break
        assert time.monotonic() < limite, f"sin pestaña {pestana}: {r}"
        p.wait_for_timeout(300)
    x, y, w, h = r["tabs"][pestana]
    cx, cy = marco["x"] + x + w / 2, marco["y"] + y + h / 2
    menu = {}
    for intento in range(3):
        # El editor dibuja el menú en su siguiente cuadro: un movimiento del ratón lo pide.
        p.mouse.move(cx, cy)
        p.mouse.click(cx, cy, button="right")
        limite = time.monotonic() + 6
        while time.monotonic() < limite:
            p.mouse.move(cx + 2, cy + 1)
            menu = pedir(p, {"op": "layouts"}).get("tab_menu", {})
            if item in menu:
                break
            p.wait_for_timeout(250)
        if item in menu:
            break
        p.keyboard.press("Escape")
        p.wait_for_timeout(500)
    if item not in menu:
        if CAPTURAS:
            p.screenshot(path=str(Path(CAPTURAS) / f"fallo-menu-{pestana}-{item}.png"))
        return False
    if CAPTURAS:
        p.screenshot(path=str(Path(CAPTURAS) / f"plantilla-menu-{item}.png"))
    x, y, w, h = menu[item]
    p.mouse.click(marco["x"] + x + w / 2, marco["y"] + y + h / 2)
    return True


def sin_menu(p, pestana: str) -> bool:
    """El menú de la pestaña no ofrece plantillas (solo lectura, modo táctil)."""
    sin_avisos_del_editor(p)
    marco = p.locator("#cad-editor").bounding_box()
    x, y, w, h = pedir(p, {"op": "layouts"})["tabs"][pestana]
    p.mouse.click(marco["x"] + x + w / 2, marco["y"] + y + h / 2, button="right")
    p.wait_for_timeout(1200)
    menu = pedir(p, {"op": "layouts"}).get("tab_menu", {})
    p.keyboard.press("Escape")
    return "from_template" not in menu


def elegir_e_insertar(p, *nombres):
    hoja = p.locator("#cad-plantilla")
    expect(p.locator("#cad-plantilla-presentaciones")).to_be_visible(timeout=LARGO)
    for x in hoja.locator(".cad-plantilla__presentacion").all():
        casilla = x.locator("input")
        quiero = any(x.locator("strong").inner_text() == n for n in nombres)
        if casilla.is_checked() != quiero:
            casilla.click()
    if CAPTURAS:
        p.screenshot(path=str(Path(CAPTURAS) / "plantilla-presentaciones.png"))
    p.locator("#cad-plantilla-insertar").click()
    expect(hoja).to_be_hidden(timeout=LARGO)


def objetos_en(p, presentacion):
    """Cuántos objetos dibujados tiene el espacio papel de `presentacion` (y su caja, en mm de papel)."""
    pedir(p, {"op": "layout_set", "name": presentacion})
    return pedir(p, {"op": "extents", "space": "current"})


@pytest.mark.parametrize("motor", MOTORES)
def test_desde_plantilla_por_las_tres_fuentes(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    destino = servicio.crear_vacio(ana, "Predio Plantillas", "Obra", "dxf")
    marco = plano_de(ana, plantilla(tmp_path / "marco.dxf"), "Marco ITT", "Plantillas")
    otra = plano_de(ana, plantilla(tmp_path / "otra.dxf", "OTRA"), "Marco de la biblioteca", "Obra")
    servicio.marcar_plantilla(ana.pk, marco, True)
    local = plantilla(tmp_path / f"{MARCA_LOCAL}.dxf", MARCA_LOCAL)
    n = abrir(ana, motor, ancho=1366, alto=860)
    p = n.ir(f"/cad/{destino.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "TVCadPlantillas.estado().menu === true", LARGO)
    antes = presentaciones(p)
    hoja = p.locator("#cad-plantilla")

    # 1. Mis plantillas: el marco de siempre, dos toques.
    assert menu_de_pestana(p, antes[-1], "from_template")
    expect(hoja).to_be_visible()
    fila = p.locator("#cad-plantilla-mias .cad-elegir__plano", has_text="Marco ITT")
    expect(fila).to_be_visible(timeout=LARGO)
    if CAPTURAS:
        p.wait_for_timeout(600)                                          # que termine de entrar la hoja
        p.screenshot(path=str(Path(CAPTURAS) / f"plantilla-hoja-{motor}.png"))
    fila.click()
    expect(p.locator("#cad-plantilla-capas li")).to_have_count(2, timeout=LARGO)
    expect(p.locator("#cad-plantilla-capas")).to_contain_text("ISO A1 · 841 × 594 mm")
    expect(p.locator("#cad-plantilla-capas svg").first).to_be_attached()
    elegir_e_insertar(p, "A1 Marco")
    esperar(p, "TVCad.pedir({op:'layouts'}).then(r => r.layouts.includes('A1 Marco'))", LARGO)
    caja = objetos_en(p, "A1 Marco")
    assert caja["count"] >= 1 and caja["max"][0] > 800 and caja["max"][1] > 500, caja      # el marco A1 (821 × 574 desde 10,10)
    esperar_estado(p, "sucio")
    assert PlantillaCad.objects.get(cuenta_id=ana.pk, plano=marco).usada is not None
    # Un solo paso de deshacer.
    pedir(p, {"op": "undo"})
    assert "A1 Marco" not in presentaciones(p)
    pedir(p, {"op": "redo"})
    assert "A1 Marco" in presentaciones(p)

    # 2. De la biblioteca: buscar, la estrella la agrega a Mis plantillas, insertar A3.
    assert menu_de_pestana(p, "A1 Marco", "from_template")
    p.locator("label", has_text="De la biblioteca").click()
    p.locator("#cad-plantilla-q").fill("biblioteca")
    fila = hoja.locator(".cad-plantilla__fila", has_text="Marco de la biblioteca")
    expect(fila).to_be_visible(timeout=LARGO)
    if CAPTURAS:
        p.screenshot(path=str(Path(CAPTURAS) / f"plantilla-biblioteca-{motor}.png"))
    fila.locator(".cad-plantilla__estrella").click()
    expect(fila.locator(".cad-plantilla__estrella")).to_have_attribute("aria-pressed", "true")
    assert PlantillaCad.objects.filter(cuenta_id=ana.pk, plano=otra).exists()
    fila.locator(".cad-elegir__plano").click()
    elegir_e_insertar(p, "A3 Vertical")
    esperar(p, "TVCad.pedir({op:'layouts'}).then(r => r.layouts.includes('A3 Vertical'))", LARGO)

    # 3. De esta computadora: nada se sube; el nombre ya usado entra como «A1 Marco (2)».
    cuerpos = []
    p.on("request", lambda r: cuerpos.append((r.url, r.post_data_buffer or b"")))
    assert menu_de_pestana(p, "A3 Vertical", "from_template")
    p.locator("label", has_text="De esta computadora").click()
    if CAPTURAS:
        p.wait_for_timeout(300)
        p.screenshot(path=str(Path(CAPTURAS) / f"plantilla-computadora-{motor}.png"))
    p.locator("#cad-plantilla-archivo").set_input_files(str(local))
    elegir_e_insertar(p, "A1 Marco")
    esperar(p, "TVCad.pedir({op:'layouts'}).then(r => r.layouts.includes('A1 Marco (2)'))", LARGO)
    for url, cuerpo in cuerpos:
        assert MARCA_LOCAL.encode() not in cuerpo and MARCA_LOCAL not in url, url
    # (Guardar sube el plano con lo insertado, como cualquier cambio: ahí sí viaja, dentro del plano.)
    assert not _errores_propios(n, motor), n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_guardar_como_plantilla_aparece_en_mis_plantillas(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    con_marco = plano_de(ana, plantilla(tmp_path / "con_marco.dxf"), "Planta con marco", "Obra")
    n = abrir(ana, motor, ancho=1366, alto=860)
    p = n.ir(f"/cad/{con_marco.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "TVCadPlantillas.estado().menu === true", LARGO)
    assert menu_de_pestana(p, "A1 Marco", "save_as_template")
    hoja = p.locator("#cad-plantilla-guardar")
    expect(hoja).to_be_visible()
    expect(p.locator("#cad-plantilla-nombre")).to_have_value("A1 Marco")
    p.locator("#cad-plantilla-nombre").fill("Mi marco A1")
    p.locator("#cad-plantilla-guardar-ir").click()
    expect(hoja).to_be_hidden(timeout=LARGO)
    nuevo = Plano.objects.get(nombre="Mi marco A1")
    assert nuevo.proyecto == "Plantillas" and PlantillaCad.objects.filter(cuenta_id=ana.pk, plano=nuevo).exists()
    # Solo esa presentación, sin espacio modelo.
    guardado = tmp_path / "guardado.dwg"
    guardado.write_bytes(servicio.ruta_version(servicio.version_vigente(nuevo)).read_bytes())
    assert guardado.read_bytes()[:4] == b"AC10"
    assert menu_de_pestana(p, "A1 Marco", "from_template")
    fila = p.locator("#cad-plantilla-mias .cad-elegir__plano", has_text="Mi marco A1")
    expect(fila).to_be_visible(timeout=LARGO)
    fila.click()
    expect(p.locator("#cad-plantilla-capas li")).to_have_count(1, timeout=LARGO)
    expect(p.locator("#cad-plantilla-capas input").first).to_be_checked()        # una sola: ya va marcada
    p.keyboard.press("Escape")


@pytest.mark.parametrize("motor", MOTORES)
def test_solo_lectura_y_bloqueo_ajeno_sin_plantillas(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    sara = _persona(crear_usuario, roles, "sara", "Sara Díaz", "supervision")
    con_marco = plano_de(ana, plantilla(tmp_path / "m.dxf"), "Planta", "Obra")
    # Supervisión: la página no trae la hoja y el editor no ofrece las opciones.
    ns = abrir(sara, motor)
    ps = ns.ir(f"/cad/{con_marco.pk}/")
    esperar_estado(ps, "lectura")
    assert ps.locator("#cad-plantilla").count() == 0
    esperar(ps, "TVCad.estaListo()", LARGO)
    ns.cerrar()
    # Beto tiene el bloqueo: Ana lo ve en solo lectura y sin plantillas; al tomarlo ella, aparecen.
    nb = abrir(beto, motor)
    pb = nb.ir(f"/cad/{con_marco.pk}/")
    esperar_estado(pb, "guardado")
    na = abrir(ana, motor, ancho=1366, alto=860)
    pa = na.ir(f"/cad/{con_marco.pk}/")
    esperar_estado(pa, "lectura")
    esperar(pa, "TVCadPlantillas.estado().menu === false", LARGO)
    assert sin_menu(pa, "A1 Marco")
    # La orden escrita (LAYOUT T) tampoco abre la hoja: la página la niega en solo lectura.
    pedir(pa, {"op": "run", "cmd": "LAYOUT T"})
    pa.wait_for_timeout(800)
    expect(pa.locator("#cad-plantilla")).to_be_hidden()
    nb.cerrar()


@pytest.mark.parametrize("motor", MOTORES)
def test_con_dos_pestanas_inserta_en_la_de_la_vista(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    a = servicio.crear_vacio(ana, "Plano A", "", "dxf")
    b = servicio.crear_vacio(ana, "Plano B", "", "dxf")
    marco = plano_de(ana, plantilla(tmp_path / "marco.dxf"), "Marco ITT", "Plantillas")
    servicio.marcar_plantilla(ana.pk, marco, True)
    n = abrir(ana, motor, ancho=1366, alto=860)
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    abrir_desde_la_hoja(p, "Plano B")
    esperar_pestanas(p, 2)
    esperar(p, "TVCadPlantillas.estado().menu === true", LARGO)
    antes_b = presentaciones(p)
    assert menu_de_pestana(p, antes_b[-1], "from_template")
    p.locator("#cad-plantilla-mias .cad-elegir__plano", has_text="Marco ITT").click()
    elegir_e_insertar(p, "A1 Marco")
    esperar(p, "TVCad.pedir({op:'layouts'}).then(r => r.layouts.includes('A1 Marco'))", LARGO)
    # «Sin guardar» sale del estado del editor, que la página lee cada segundo.
    esperar(p, "TVCadPestanas.lista()[1].sucio === true", LARGO)
    lista = p.evaluate("TVCadPestanas.lista()")
    assert [t["sucio"] for t in lista] == [False, True], lista
    p.evaluate("id => TVCadPestanas.activar(id)", str(a.pk))
    esperar(p, f"TVCad.estado().plano && TVCad.estado().plano.id === {str(a.pk)!r}", LARGO)
    assert "A1 Marco" not in presentaciones(p)
