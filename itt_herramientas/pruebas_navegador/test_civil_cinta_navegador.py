"""Civil en la cinta del editor (docs/civil.md, «En la cinta»): la pestaña «Civil» con cada acción de `MENUS`, un clic o el comando escrito
abren su hoja (una paleta junto al dibujo), apagada sin el control del plano y por pestaña de plano, el grupo «Imágenes», Esc primero
para la hoja, y la barra de siempre con un editor sin pestañas propias.

Necesita una compilación del editor con `ribbon_define` (tv-v2026.45-13 en adelante) en TV_CAD_EDITOR_PRUEBAS; la prueba de la barra de
respaldo usa la oficial (la -12, sin la operación) de TV_CAD_EDITOR_OFICIAL. Corre bajo el candado pesado, un navegador a la vez:
    TV_CAD_EDITOR_PRUEBAS=<compilación> pytest itt_herramientas/pruebas_navegador/test_civil_cinta_navegador.py -k chromium
Las capturas de la cinta (claro y oscuro, escala 1 y 1.5) salen con CINTA_CAPTURAS=carpeta.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from itt_herramientas.cad import editor as cad_editor
from itt_herramientas.cad import servicio
from itt_herramientas.civil import pagina

from . import cinta
from .test_civil_navegador import cerrar_aviso_gpu
from .test_cad_navegador import EDITOR, LARGO, Navegador, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
MOTORES = ["chromium", "webkit"]
OFICIAL = Path(os.environ.get("TV_CAD_EDITOR_OFICIAL", str(Path.home() / ".cache" / "tresvizo-cad" / "editor")))
CAPTURAS = os.environ.get("CINTA_CAPTURAS", "")
X0, Y0 = 390000.0, 3168000.0

#: Lo que ve la página tras abrir una acción: hojas abiertas, el aviso de «elige en el dibujo», avisos y selectores de archivo.
VISTO = """() => ({
  hojas: [...document.querySelectorAll('dialog[open]')].map(d => d.id),
  paletas: [...document.querySelectorAll('dialog.civil-hoja--paleta[open]')].map(d => d.id),
  chip: !document.getElementById('civil-pick').hidden,
  avisos: (window.__avisos || []).length,
  archivos: window.__archivos || 0,
})"""


def preparar(p):
    """Avisos contados y el selector de archivos del sistema sustituido (cuenta cada vez que se abriría)."""
    p.evaluate("""() => {
      window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a && a(t, k); };
      window.__archivos = 0;
      if (window.showOpenFilePicker) window.showOpenFilePicker = async () => { window.__archivos++; throw new DOMException('cancelado', 'AbortError'); };
    }""")
    p.on("filechooser", lambda fc: p.evaluate("() => { window.__archivos = (window.__archivos || 0) + 1; }"))


def cerrar_lo_abierto(p):
    """Deja la página como estaba: la hoja con su Esc, el aviso de elegir con «Cancelar», otras hojas con su botón de cerrar."""
    v = p.evaluate(VISTO)
    if v["chip"]:
        p.locator("#civil-pick-cancelar").click()
        esperar(p, "document.getElementById('civil-pick').hidden", LARGO)
    for _ in range(4):
        v = p.evaluate(VISTO)
        if not v["hojas"]:
            break
        p.locator(f"#{v['hojas'][-1]} [data-tv-cerrar]").first.click()
        p.wait_for_timeout(150)
    esperar(p, "!document.querySelector('dialog[open]')", LARGO)
    esperar(p, cinta.libre(p), LARGO)


def abrir_plano(navegar, persona, plano, motor, **kw):
    n = navegar(persona, motor, **kw)
    p = n.ir(f"/cad/{plano.pk}/")
    cinta.esperar_civil(p)
    return n, p


def comandos_esperados():
    c = pagina.cinta()
    return {c["escala"]["comando"]} | {a["comando"] for g in c["grupos"] for a in g["acciones"]}


@pytest.mark.parametrize("motor", MOTORES)
def test_la_pestana_civil_tiene_cada_accion_y_cada_una_abre_lo_suyo(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio cinta", "", "dxf")
    n, p = abrir_plano(navegar, ana, plano, motor, ancho=1920, alto=1040)
    esperar_estado(p, "guardado")
    p.locator("#cad-completa").click()                                       # el editor a lo ancho de la pantalla
    esperar(p, "document.documentElement.classList.contains('cad-completa')", LARGO)
    p.wait_for_timeout(1500)
    assert cinta.en_cinta(p), "con esta compilación Civil va en la cinta del editor"
    assert not p.locator(".civil-barra").count(), "sin la barra de Civil encima del editor"
    assert p.locator("#cad-extensiones").is_hidden()
    preparar(p)
    r = cinta.mostrar_pestana(p)
    assert r["tabs"][0]["title"] == "Civil"
    grupos = r["tabs"][0]["groups"]
    assert grupos[0] == "Escala" and grupos[1:] == [m["nombre"] for m in pagina.MENUS], grupos
    assert {c["command"] for c in r["commands"]} == comandos_esperados()
    assert all(c["enabled"] for c in r["commands"]), [c for c in r["commands"] if not c["enabled"]]
    assert next(c for c in r["commands"] if c["command"] == "CVESCALA")["label"] == "1:1000 · Zona 14N"

    como = {}
    for comando, clave in sorted(p.evaluate("TVCivilCinta.comandos()").items()):
        antes = p.evaluate(VISTO)
        como[clave] = cinta.accion(p, clave)
        abrio = f"""(() => {{ const v = ({VISTO})();
              return v.hojas.length > 0 || v.chip || v.avisos > {antes['avisos']} || v.archivos > {antes['archivos']}; }})()"""
        try:
            esperar(p, abrio, 8000)
        except AssertionError:
            # un clic que el lienzo no tomó (WebKit, mientras se redibuja la cinta): la persona vuelve a pulsar
            como[clave] = cinta.accion(p, clave)
            try:
                esperar(p, abrio, LARGO)
            except AssertionError:
                raise AssertionError(f"{comando} ({clave}, por {como[clave]}) no abrió nada: {p.evaluate(VISTO)}") from None
        v = p.evaluate(VISTO)
        if v["hojas"] and v["hojas"][0].startswith("civil-hoja"):
            assert v["paletas"] == v["hojas"], f"{clave}: la hoja es una paleta junto al dibujo ({v})"
            caja = p.locator(f"#{v['hojas'][0]}").bounding_box()
            marco = p.locator("#cad-editor").bounding_box()
            area = cinta.pedir(p, {"op": "state"})["viewport_rect"]
            assert caja["y"] >= marco["y"] + area[1] - 1, f"{clave}: la paleta no tapa la cinta ({caja}, {area})"
            assert caja["x"] + caja["width"] <= marco["x"] + area[0] + area[2] + 1
        cerrar_lo_abierto(p)
    # A 1920 px en pantalla completa ningún grupo se compacta: todo botón se ve y se pulsó; lo de la lista de un botón dividido se escribió.
    r = cinta.items(p)
    en_listas = {c for t in r["tabs"] for s in t["splits"] for c in s["commands"]}
    caras = {s["id"].rsplit(":", 1)[1] for t in r["tabs"] for s in t["splits"]}
    sueltas = {c["command"] for c in r["commands"]} - en_listas
    comandos = p.evaluate("TVCivilCinta.comandos()")
    for comando in sueltas | caras:
        assert como[comandos[comando]] == "clic", f"{comando}: su botón no se veía a 1920 px ({como})"
    assert all(s["rect"] for t in r["tabs"] for s in t["splits"]), r["tabs"]
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_un_comando_escrito_abre_su_hoja(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio comandos", "", "dxf")
    n, p = abrir_plano(navegar, ana, plano, motor)
    esperar_estado(p, "guardado")
    preparar(p)
    cinta.escribir_comando(p, "CVCURVAS")
    esperar(p, "document.getElementById('civil-hoja-curvas').open", LARGO)
    cerrar_lo_abierto(p)
    cinta.escribir_comando(p, "cvtriangular")                                # como en AutoCAD, en minúsculas también
    esperar(p, "document.getElementById('civil-hoja-triangular').open", LARGO)
    cerrar_lo_abierto(p)
    # la Tecla Intro sola repite la última, como cualquier comando
    cinta.foco_en_editor(p)
    p.keyboard.press("Enter")
    esperar(p, "document.getElementById('civil-hoja-triangular').open", LARGO)
    cerrar_lo_abierto(p)
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_sin_el_control_del_plano_solo_lo_que_lee(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    plano = servicio.crear_vacio(beto, "De Beto", "", "dxf")
    servicio.bloqueo(plano.pk, beto, "sesion-de-beto-00001", "tomar")
    n, p = abrir_plano(navegar, ana, plano, motor, ancho=1920, alto=1040)
    esperar_estado(p, "lectura")
    preparar(p)
    esperar(p, "TVCad.pedir({op: 'ribbon_items'}).then(r => r.commands.some(c => !c.enabled))", LARGO)
    r = cinta.mostrar_pestana(p)
    estado = {c["command"]: c for c in r["commands"]}
    for comando in ("CVTRIANGULAR", "CVIMPORTARPUNTOS", "CVCURVAS", "CVIMAGEN"):
        assert not estado[comando]["enabled"], estado[comando]
        assert estado[comando]["reason"] == "Para dibujar, toma el control del plano."
    for comando in ("CVEXPORTARPUNTOS", "CVREPORTEPUNTOS", "CVLOCALIZAR", "CVMEMORIA", "CVREFERENCIAS", "CVESCALA"):
        assert estado[comando]["enabled"], estado[comando]
    # Un clic en un botón apagado no hace nada; escrito, el editor dice por qué y Civil no abre nada.
    rect = estado["CVTRIANGULAR"]["rect"]
    assert rect, "el botón apagado se ve"
    cinta.clic_en_marco(p, rect)
    p.wait_for_timeout(800)
    assert not p.evaluate(VISTO)["hojas"]
    cinta.escribir_comando(p, "CVTRIANGULAR")
    p.wait_for_timeout(800)
    assert not p.evaluate(VISTO)["hojas"]
    historia = cinta.pedir(p, {"op": "history"})["entries"]
    assert any("CVTRIANGULAR: Para dibujar, toma el control del plano." in e["text"] for e in historia), historia[-5:]
    # Lo que solo lee sí abre.
    cinta.accion(p, "exportar")
    esperar(p, "document.getElementById('civil-hoja-exportar').open", LARGO)
    cerrar_lo_abierto(p)
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_las_pestanas_de_planos_cambian_lo_que_se_puede_usar(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    a = servicio.crear_vacio(ana, "Mío", "", "dxf")
    b = servicio.crear_vacio(beto, "De Beto", "", "dxf")
    servicio.bloqueo(b.pk, beto, "sesion-de-beto-00001", "tomar")
    ua = {"user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"} if motor == "chromium" else {}
    n, p = abrir_plano(navegar, ana, a, motor, **ua)
    esperar_estado(p, "guardado")
    tri = "TVCad.pedir({op: 'ribbon_items'}).then(r => r.commands.find(c => c.command === 'CVTRIANGULAR').enabled)"
    esperar(p, tri, LARGO)
    doc_a = p.evaluate("TVCad.estado().documento")
    p.evaluate("id => TVCadPestanas.abrir(id)", str(b.pk))
    esperar(p, "TVCadPestanas.lista().length === 2 && TVCadPestanas.lista().every(t => t.cargado)", LARGO)
    esperar_estado(p, "lectura")
    esperar(p, f"{tri}.then(x => !x)", LARGO)
    doc_b = p.evaluate("TVCad.estado().documento")
    assert doc_a != doc_b
    # cada plano guarda lo suyo en el editor: el de atrás sigue con todo
    r = cinta.pedir(p, {"op": "ribbon_items", "document_id": doc_a})
    assert next(c for c in r["commands"] if c["command"] == "CVTRIANGULAR")["enabled"]
    p.evaluate("id => TVCadPestanas.abrir(id)", str(a.pk))
    esperar_estado(p, "guardado")
    esperar(p, tri, LARGO)
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_grupo_imagenes(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio imágenes", "", "dxf")
    n, p = abrir_plano(navegar, ana, plano, motor, ancho=1920, alto=1040)
    esperar_estado(p, "guardado")
    preparar(p)
    r = cinta.mostrar_pestana(p)
    assert "Imágenes" in r["tabs"][0]["groups"]
    grupo = [c["command"] for c in r["commands"] if c["group"] == "Imágenes"]
    assert {"CVIMAGEN", "CVREFERENCIAS"} <= set(grupo), grupo
    # «Insertar imagen georreferenciada»: el mismo camino de la carpeta del proyecto (elegir el archivo)
    cinta.accion(p, "imagen")
    esperar(p, "(window.__archivos || 0) > 0", LARGO)
    # «Referencias externas»: el panel de las referencias del plano
    cinta.accion(p, "referencias")
    esperar(p, "document.getElementById('cad-referencias').open", LARGO)
    p.locator("#cad-referencias [data-tv-cerrar]").first.click()
    # el gancho para otra fuente de imágenes: una acción registrada por otro módulo se enciende sola
    assert p.evaluate("TVCivil.tieneAccion('imagen') && TVCivil.tieneAccion('referencias')")
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_esc_cierra_primero_la_hoja_y_luego_es_del_editor(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio Esc", "", "dxf")
    n, p = abrir_plano(navegar, ana, plano, motor, ancho=1920, alto=1040)
    esperar_estado(p, "guardado")
    h = cinta.pedir(p, {"op": "entities_create", "entities": [{"type": "Circle", "center": [X0, Y0], "radius": 5}]})["result"]["handles"][0]
    seleccion = lambda: cinta.pedir(p, {"op": "state"}).get("selection") or []  # noqa: E731
    for donde in ("editor", "pagina"):
        cinta.pedir(p, {"op": "select", "handles": [h]})
        cinta.accion(p, "triangular")
        esperar(p, "document.getElementById('civil-hoja-triangular').open", LARGO)
        if donde == "editor":
            cinta.foco_en_editor(p)                                          # el foco en el dibujo
        else:
            p.locator("#civil-hoja-triangular h2").click()                   # el foco en la hoja
        p.keyboard.press("Escape")
        esperar(p, "!document.getElementById('civil-hoja-triangular').open", LARGO)
        p.wait_for_timeout(400)
        assert seleccion() == [h], f"{donde}: el primer Esc solo cerró la hoja"
        cinta.foco_en_editor(p)
        p.keyboard.press("Escape")
        esperar(p, "TVCad.pedir({op: 'state'}).then(s => !(s.selection || []).length)", LARGO)
    assert not n.errores, n.errores


@pytest.fixture
def editor_oficial(cad, settings):
    if OFICIAL.resolve() == Path(EDITOR).resolve() or not (OFICIAL / cad_editor.version() / "index.html").is_file():
        pytest.skip(f"La compilación oficial (sin la cinta de Civil) en TV_CAD_EDITOR_OFICIAL: {OFICIAL}")
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(OFICIAL)
    return OFICIAL


@pytest.mark.parametrize("motor", MOTORES)
def test_sin_la_operacion_en_el_editor_vuelve_la_barra(editor_oficial, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio barra", "", "dxf")
    n, p = abrir_plano(navegar, ana, plano, motor)
    esperar_estado(p, "guardado")
    assert p.evaluate("document.documentElement.dataset.civil") == "barra"
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    menus = p.evaluate("[...document.querySelectorAll('.civil-barra [data-civil-menu]')].map(d => d.dataset.civilMenu)")
    assert menus == [m["clave"] for m in pagina.MENUS], menus
    cinta.accion(p, "curvas")                                                 # por su menú
    esperar(p, "document.getElementById('civil-hoja-curvas').open", LARGO)
    assert not p.locator("#civil-hoja-curvas.civil-hoja--paleta").count(), "con la barra, la hoja de siempre"
    p.keyboard.press("Escape")
    esperar(p, "!document.getElementById('civil-hoja-curvas').open", LARGO)
    assert not n.errores, n.errores


@pytest.mark.skipif(not CAPTURAS, reason="solo con CINTA_CAPTURAS=carpeta: la cinta en claro y oscuro, a escala 1 y 1.5")
@pytest.mark.parametrize("escala", [1, 1.5])
@pytest.mark.parametrize("tema", ["light", "dark"])
def test_capturas_cinta_chromium(cad, playwright_, servidor, sesion_de, crear_usuario, roles, tema, escala):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Levantamiento", "", "dxf")
    navegador = playwright_.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    try:
        ctx = navegador.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=escala, color_scheme=tema, locale="es-MX")
        ctx.add_cookies([sesion_de(ana)])
        p = ctx.new_page()
        p.set_default_timeout(LARGO)
        p.goto(servidor.url + f"/cad/{plano.pk}/")
        cinta.esperar_civil(p)
        esperar_estado(p, "guardado")
        cinta.pedir(p, {"op": "run", "cmd": f"COLORSCHEME {'LIGHT' if tema == 'light' else 'DARK'}"})   # el tema del editor
        cerrar_aviso_gpu(p)                                                  # sin GPU en la prueba: el aviso del editor taparía la cinta
        p.evaluate("TVCivilCinta.mostrarPestana()")
        esperar(p, "TVCad.pedir({op: 'ribbon_items'}).then(r => r.active_tab === 'host:civil' && r.commands.some(c => c.rect))", LARGO)
        p.wait_for_timeout(1200)
        r = cinta.items(p)
        marco = p.locator("#cad-editor").bounding_box()
        area = cinta.pedir(p, {"op": "state"})["viewport_rect"]
        recorte = {"x": marco["x"], "y": marco["y"], "width": marco["width"], "height": area[1] + 4}
        nombre = f"{CAPTURAS}/cinta-{tema}-{escala}"
        p.screenshot(path=f"{nombre}.png", clip=recorte)
        if escala == 1:                                                      # la ayuda de un botón grande, con su comando
            tri = next(c for c in r["commands"] if c["command"] == "CVTRIANGULAR")["rect"]
            b = p.locator("#cad-editor").bounding_box()
            p.mouse.move(b["x"] + tri[0] + tri[2] / 2, b["y"] + tri[1] + tri[3] / 2)
            p.wait_for_timeout(1200)
            p.screenshot(path=f"{nombre}-ayuda.png", clip={**recorte, "height": recorte["height"] + 90})
        # una hoja abierta como paleta junto al dibujo
        p.evaluate("TVCivil.accion('curvas')")
        esperar(p, "document.getElementById('civil-hoja-curvas').open && !document.getElementById('civil-hoja-curvas').hasAttribute('data-colocando')", LARGO)
        p.wait_for_timeout(600)
        p.screenshot(path=f"{nombre}-paleta.png")
    finally:
        navegador.close()
