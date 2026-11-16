"""Civil en el navegador con el editor real: importar 5 000 puntos PNEZD, triangular, invertir dos triángulos, curvas, cuadro de
construcción, perfil y secciones de un eje, volumen entre superficies, deshacer, y guardar en DWG y volver a abrir. En coordenadas UTM
(X ≈ 390 000, Y ≈ 3 168 000), a 1280 y 360 px. Las capturas de cada hoja y de lo dibujado salen con ITT_CAPTURAS=carpeta.

Corre bajo el candado pesado de la máquina, un navegador a la vez:
    TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor pytest itt_herramientas/pruebas_navegador/test_civil_navegador.py -k chromium
"""
from __future__ import annotations

import io
import os
import time

import numpy as np
import pytest

from itt_herramientas.cad import servicio
from itt_herramientas.models import VersionPlano

from .test_cad_navegador import CAPTURAS, LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
X0, Y0 = 390000.0, 3168000.0
MOTORES = ["chromium", "webkit"]


def terreno(x, y):
    return 100 + 0.04 * x + 0.02 * y + 3 * np.sin(x / 40) * np.cos(y / 55)


def archivo_pnezd(n=5000, semilla=3, lado=300.0, f=terreno, prefijo=""):
    rng = np.random.default_rng(semilla)
    xy = np.vstack([rng.uniform(0, lado, (n - 4, 2)), [[0, 0], [lado, 0], [lado, lado], [0, lado]]])
    filas = [f"{prefijo}{i + 1},{Y0 + y:.3f},{X0 + x:.3f},{f(x, y):.3f},TN" for i, (x, y) in enumerate(xy)]
    return ("\r\n".join(filas) + "\r\n").encode()


def pedir(p, peticion, ms=120000):
    return p.evaluate("([q, t]) => TVCad.pedir(q, {tiempoMs: t})", [peticion, ms])


def cuenta(p, tipo=None, capa=None):
    q = {"op": "query", "limit": 1, "detail": "summary"}
    if tipo:
        q["type"] = tipo
    if capa:
        q["layer"] = capa
    return pedir(p, q).get("count", 0)


def apagar(p, *capas):
    for capa in capas:
        pedir(p, {"op": "set_properties", "collection": "layers", "name": capa, "updates": [{"path": "/flags/off", "value": True}]})


def menu(p, clave, accion):
    p.locator(f'[data-civil-menu="{clave}"] > summary').click()
    try:
        p.locator(f'[data-civil-menu="{clave}"] [data-civil="{accion}"]').click(timeout=15000)
    except Exception:
        p.screenshot(path=os.path.expanduser(f"~/.tresvizo/agentes/G/salida/depurar-menu-{clave}.png"))
        raise


def esperar_aviso(p, texto, timeout=LARGO):
    """Espera un aviso de la página que contenga `texto`; si no llega, dice cuáles llegaron (y guarda una captura)."""
    try:
        esperar(p, f"window.__avisos.some(a => a[0].includes({texto!r}))", timeout)
    except AssertionError:
        p.screenshot(path=os.path.expanduser("~/.tresvizo/agentes/G/salida/depurar-aviso.png"))
        raise AssertionError(f"No llegó el aviso «{texto}». Avisos: {p.evaluate('window.__avisos')}") from None


def foto(p, nombre, ancho, tema="claro"):
    # Solo en Chromium: en WebKit la captura de Playwright inyecta un estilo que la CSP de la página (con razón) rechaza
    if CAPTURAS and p.context.browser.browser_type.name == "chromium":
        p.wait_for_timeout(500)
        p.screenshot(path=f"{CAPTURAS}/civil-{ancho}-{tema}-{nombre}.png")


def esperar_libre(p, timeout=LARGO * 2):
    esperar(p, "!document.querySelector('.civil-barra[aria-busy]')", timeout)


def cerrar_aviso_gpu(p):
    """Sin GPU (la prueba dibuja por software) el editor muestra un aviso que tapa el lienzo; una persona con GPU no lo ve."""
    for _ in range(3):
        st = pedir(p, {"op": "state"})
        if "GpuWarning" not in (st.get("tool_windows") or []) and not st.get("modal"):
            return
        try:
            pedir(p, {"op": "action", "name": "close_modal"})
        except Exception:
            pass
        st = pedir(p, {"op": "state"})
        if "GpuWarning" in (st.get("tool_windows") or []):
            caja = p.locator("#cad-editor").bounding_box()
            cx, cy = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
            p.mouse.click(cx - 186, cy + 91)                                # «Aceptar» del aviso, centrado en el marco
            p.wait_for_timeout(400)


class Mapa:
    """Pantalla ↔ mundo del lienzo. Dentro del marco de la página el cursor del editor se actualiza con los clics (no al pasar el ratón,
    en Playwright), así que se hacen clics en zona vacía (Esc después de cada uno) y se lee state.cursor; el primero solo da el foco."""

    def __init__(self, p):
        self.p = p
        cerrar_aviso_gpu(p)
        # sin referencias a objetos: si no, el clic de calibración se pega a la geometría cercana y el mapa sale chueco
        pedir(p, {"op": "sysvar", "set": {"osmode": 0}})
        caja = p.locator("#cad-editor").bounding_box()
        lecturas = []
        for fx, fy in ((0.5, 0.55), (0.45, 0.5), (0.65, 0.7)):
            sx, sy = caja["x"] + caja["width"] * fx, caja["y"] + min(caja["height"], 600) * fy
            p.mouse.click(sx, sy)
            p.wait_for_timeout(300)
            lecturas.append((sx, sy, pedir(p, {"op": "state"})["cursor"]["world"]))
            p.keyboard.press("Escape")
            p.wait_for_timeout(150)
        (sx1, sy1, w1), (sx2, sy2, w2) = lecturas[1:]
        assert w1 != w2, "el cursor del editor no siguió a los clics"
        self.base = (sx1, sy1, w1[0], w1[1])
        self.k = ((w2[0] - w1[0]) / (sx2 - sx1), (w2[1] - w1[1]) / (sy2 - sy1))
        pedir(p, {"op": "select", "clear": True})

    def pantalla(self, x, y):
        sx1, sy1, wx1, wy1 = self.base
        return sx1 + (x - wx1) / self.k[0], sy1 + (y - wy1) / self.k[1]


def enfocar(p, caja):
    """Acerca la vista a la caja [x0, y0, x1, y1] (view_focus no está en la web: ZOOM ventana por comando)."""
    x0, y0, x1, y1 = caja
    m = max(x1 - x0, y1 - y0) * 0.6
    pedir(p, {"op": "run", "cmd": f"ZOOM W {x0 - m},{y0 - m} {x1 + m},{y1 + m}"})
    if pedir(p, {"op": "state"}).get("command"):
        pedir(p, {"op": "cancel"})
    p.wait_for_timeout(800)
    return Mapa(p)


def clic_lento(p, x, y):
    """Clic como el de una persona: se acerca, baja y sube (getpoint no siempre toma un clic instantáneo)."""
    p.mouse.move(x - 15, y - 15)
    p.mouse.move(x, y, steps=6)
    p.wait_for_timeout(150)
    p.mouse.down()
    p.wait_for_timeout(80)
    p.mouse.up()


def elegir_en_dibujo(p, puntos_mundo, mapa=None):
    """Mientras Civil espera la elección (aviso visible), la persona hace clic en cada punto y pulsa Enter."""
    esperar(p, "!document.getElementById('civil-pick').hidden", LARGO)
    m = mapa or Mapa(p)
    for x, y in puntos_mundo:
        clic_lento(p, *m.pantalla(x, y))
        p.wait_for_timeout(300)
    # como una persona: Enter cuando lo elegido ya se ve seleccionado (y estable)
    p.wait_for_timeout(500)
    limite = time.monotonic() + 5
    previa = None
    while time.monotonic() < limite:
        sel = pedir(p, {"op": "state"}).get("selection")
        if sel and sel == previa:
            break
        previa = sel
        p.wait_for_timeout(250)
    if os.environ.get("CIVIL_DEPURAR"):
        st = pedir(p, {"op": "state"})
        print("\nelegir:", [m.pantalla(x, y) for x, y in puntos_mundo], "sel", st.get("selection"), "op", st.get("operation"), "cursor", st.get("cursor"))
    p.keyboard.press("Enter")
    if os.environ.get("CIVIL_DEPURAR"):
        p.wait_for_timeout(1500)
        st = pedir(p, {"op": "state"})
        print("tras Enter: op", st.get("operation"), "avisos", p.evaluate("window.__avisos || []"), "pick", p.evaluate("document.getElementById('civil-pick').hidden"), flush=True)


@pytest.mark.parametrize("motor", MOTORES)
def test_flujo_civil_completo(cad, navegar, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Levantamiento Civil", "Predio Sur", "dwg")
    n = navegar(ana, motor)
    if os.environ.get("CIVIL_DEPURAR"):
        n.pagina.on("console", lambda m: print("consola:", m.type, m.text[:500], flush=True) if m.type in ("error", "warning") else None)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    foto(p, "00-barra", 1280)
    p.locator('[data-civil-menu="puntos"] > summary').click()
    foto(p, "01-menu-puntos", 1280)
    p.keyboard.press("Escape")
    # a 1280 px la barra entera cabe sin desplazarse, y cada menú se abre bajo su botón
    assert p.evaluate("(() => { const b = document.querySelector('.civil-barra'); return b.scrollWidth <= b.clientWidth + 1; })()")
    p.locator('[data-civil-menu="volumenes"] > summary').click()
    boton = p.locator('[data-civil-menu="volumenes"] > summary').bounding_box()
    lista = p.locator('[data-civil-menu="volumenes"] .civil-menu__lista').bounding_box()
    assert lista["y"] >= boton["y"] + boton["height"] - 1 and lista["y"] < boton["y"] + boton["height"] + 12, (boton, lista)
    assert boton["x"] - lista["width"] <= lista["x"] <= boton["x"] + 1 and lista["x"] + lista["width"] <= 1280, (boton, lista)
    p.keyboard.press("Escape")

    # 1. Importar 5 000 puntos PNEZD
    archivo = tmp_path / "levantamiento.txt"
    archivo.write_bytes(archivo_pnezd())
    menu(p, "puntos", "importar")
    p.locator("#civil-imp-archivo").set_input_files(str(archivo))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    assert "5,000 puntos" in p.locator("#civil-imp-vista").inner_text().replace(" ", ",").replace(" ", ",") or "5000" in p.locator("#civil-imp-vista").inner_text().replace(",", "").replace(" ", "")
    foto(p, "02-importar", 1280)
    t = time.monotonic()
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO * 2)
    importar_s = time.monotonic() - t
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 5000
    ins = pedir(p, {"op": "query", "type": "INSERT", "layer": "PUNTOS_TERRENO", "limit": 1, "detail": "full", "fields": ["position", "attributes"]})["entities"][0]
    assert ins["position"][0] > 389000 and ins["attributes"]["DESC"] == "TN"
    esperar_estado(p, "sucio")
    foto(p, "03-puntos-dibujados", 1280)
    # deshacer quita la importación entera y rehacer la devuelve (un solo paso)
    pedir(p, {"op": "undo"})
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 0
    pedir(p, {"op": "redo"})
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 5000

    # 2. Triangular
    menu(p, "terreno", "triangular")
    foto(p, "04-triangular", 1280)
    t = time.monotonic()
    p.locator('[data-civil-form="triangular"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-triangular').open", LARGO * 3)
    triangular_s = time.monotonic() - t
    caras = cuenta(p, "3DFACE", "TRIANGULACION")
    assert 9500 < caras < 10000
    pedir(p, {"op": "action", "name": "zoom_extents"})
    foto(p, "05-triangulacion", 1280)

    # 3. Invertir dos triángulos vecinos, eligiéndolos en el dibujo
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); }; }")
    rec = pedir(p, {"op": "records", "collection": "entities", "type": "3DFACE", "paths": ["/first_corner", "/second_corner", "/third_corner"], "limit": 10000})["records"]

    def area(r):
        (a, b, c) = [(v["x"], v["y"]) for v in r["values"].values()]
        return abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2

    def convexo(a, b):
        va = [(round(v["x"], 6), round(v["y"], 6)) for v in a["values"].values()]
        vb = [(round(v["x"], 6), round(v["y"], 6)) for v in b["values"].values()]
        comun = set(va) & set(vb)
        if len(comun) != 2:
            return False
        (p1, p2), c, d = sorted(comun), next(q for q in va if q not in comun), next(q for q in vb if q not in comun)
        o = lambda u, v, w: (v[0] - u[0]) * (w[1] - u[1]) - (v[1] - u[1]) * (w[0] - u[0])
        return o(c, d, p1) * o(c, d, p2) < 0 and o(p1, p2, c) * o(p1, p2, d) < 0

    buenos = [r for r in rec if area(r) > 4 and all(60 < v["x"] - X0 < 240 and 60 < v["y"] - Y0 < 240 for v in r["values"].values())]
    par = next(((a, b) for i, a in enumerate(buenos) for b in buenos[i + 1:] if convexo(a, b)), None)
    assert par, (len(rec), len(buenos), rec[:1])
    centro = lambda r: (sum(v["x"] for v in r["values"].values()) / 3, sum(v["y"] for v in r["values"].values()) / 3)
    antes = [sorted((round(v["x"], 3), round(v["y"], 3)) for v in r["values"].values()) for r in par]
    # como haría quien edita el TIN: apaga las capas de los puntos para ver los triángulos
    apagar(p, "PUNTOS_TERRENO", "PUNTOS_NUMERO", "PUNTOS_ELEVACION", "PUNTOS_DESCRIPCION")
    xs = [v["x"] for r in par for v in r["values"].values()]
    ys = [v["y"] for r in par for v in r["values"].values()]
    mapa = enfocar(p, (min(xs), min(ys), max(xs), max(ys)))
    comun = sorted({(v["x"], v["y"]) for v in par[0]["values"].values()} & {(v["x"], v["y"]) for v in par[1]["values"].values()})
    medio = ((comun[0][0] + comun[1][0]) / 2, (comun[0][1] + comun[1][1]) / 2)
    menu(p, "terreno", "invertir")
    esperar(p, "!document.getElementById('civil-pick').hidden", LARGO)
    clic_lento(p, *mapa.pantalla(*medio))                                  # señala el lado común
    if os.environ.get("CIVIL_DEPURAR"):
        p.wait_for_timeout(800)
        st = pedir(p, {"op": "state"})
        print("\ntras señalar: op", st.get("operation"), "cursor", st.get("cursor"), "medio", medio, flush=True)

    def leer():
        r = pedir(p, {"op": "records", "collection": "entities", "type": "3DFACE", "handles": [par[0]["handle"], par[1]["handle"]], "paths": ["/first_corner", "/second_corner", "/third_corner"]})["records"]
        return [sorted((round(v["x"], 3), round(v["y"], 3)) for v in x["values"].values()) for x in r]
    limite = time.monotonic() + 60
    while leer() == antes and time.monotonic() < limite:
        p.wait_for_timeout(300)
    print("\ntras invertir:", leer() != antes, p.evaluate("window.__avisos"), p.evaluate("document.getElementById('civil-pick').hidden"), flush=True)
    esperar(p, "!document.getElementById('civil-pick').hidden", LARGO)     # la orden se repite: pide el siguiente par
    foto(p, "06-invertir", 1280)
    p.keyboard.press("Escape")                                              # termina la orden repetitiva
    esperar_libre(p)
    nuevos = leer()
    print("\navisos", p.evaluate("window.__avisos"), flush=True)
    assert nuevos != antes and {pt for c in nuevos for pt in c} == {pt for c in antes for pt in c}

    # 3b. Revisar la triangulación (dos veces: las marcas de la anterior se reemplazan, no se acumulan)
    for vez in range(2):
        menu(p, "terreno", "revisar")
        p.locator('[data-civil-form="revisar"] button[type=submit]').click()
        esperar(p, "!document.querySelector('[data-civil-form=revisar] [data-civil-resultado]').hidden", LARGO * 2)
        esperar_libre(p)
        marcas = cuenta(p, None, "TRIANGULACION_REVISION")
        if vez == 0:
            foto(p, "06b-revisar", 1280)
            primeras = marcas
        p.locator("#civil-hoja-revisar [data-tv-cerrar]").first.click()
    texto_rev = p.locator('[data-civil-form="revisar"] [data-civil-resultado]').inner_text()
    assert marcas == primeras and ("Sin problemas" in texto_rev or "muy delgados" in texto_rev), texto_rev
    assert "repetidos" not in texto_rev and "encimados" not in texto_rev    # una triangulación recién hecha (e invertida) está sana

    # 4. Curvas de nivel
    menu(p, "curvas", "curvas")
    foto(p, "07-curvas", 1280)
    p.locator('[data-civil-form="curvas"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-curvas').open", LARGO * 3)
    assert cuenta(p, None, "CURVAS_MAESTRAS") > 0 and cuenta(p, None, "CURVAS_INTERMEDIAS") > 0 and cuenta(p, "Text", "CURVAS_ETIQUETAS") > 0
    pedir(p, {"op": "action", "name": "zoom_extents"})
    foto(p, "08-curvas-dibujadas", 1280)

    # 5. Cuadro de construcción de un lote dibujado (con un lado curvo)
    lote = pedir(p, {"op": "entities_create", "entities": [{"type": "LwPolyline", "vertices": [[X0 + 50, Y0 + 50], [X0 + 50, Y0 + 130], [X0 + 140, Y0 + 130], [X0 + 140, Y0 + 50]], "closed": True, "layer": "LOTE"}]})["result"]["handles"][0]
    pedir(p, {"op": "set_properties", "collection": "entities", "handle": lote, "updates": [{"path": "/vertices/1/bulge", "value": -0.2}]})
    # para tocar solo el lote y el eje (las marcas de la revisión también quedan encima)
    apagar(p, "CURVAS_MAESTRAS", "CURVAS_INTERMEDIAS", "CURVAS_ETIQUETAS", "TRIANGULACION", *(["TRIANGULACION_REVISION"] if primeras else []))
    mapa_lote = enfocar(p, (X0 + 50, Y0 + 50, X0 + 140, Y0 + 130))
    menu(p, "cuadros", "cuadro")
    p.locator('[data-civil-form="cuadro"] [data-civil-elegir="poligono"]').click()
    elegir_en_dibujo(p, [(X0 + 50, Y0 + 90)], mapa_lote)
    esperar(p, "document.querySelector('[data-civil-form=cuadro] [data-civil-cuenta=poligono]').classList.contains('civil-elegido')", LARGO)
    foto(p, "09-cuadro", 1280)
    p.locator('[data-civil-form="cuadro"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-cuadro').open", LARGO)
    assert "4 vértices" in p.locator('[data-civil-form="cuadro"] [data-civil-cuenta="poligono"]').inner_text()
    textos = [e["value"] for e in pedir(p, {"op": "query", "type": "Text", "layer": "CUADRO_TITULO", "detail": "geometry"})["entities"]]
    assert "CUADRO DE CONSTRUCCIÓN" in textos and any(t.startswith("SUPERFICIE =") for t in textos)
    assert cuenta(p, "Line", "CUADRO_LINEAS") > 10

    # 5b. Cuadro de curvas del mismo lote (su lado curvo)
    mapa_lote = enfocar(p, (X0 + 50, Y0 + 50, X0 + 140, Y0 + 130))          # la vista, otra vez sobre el lote
    menu(p, "cuadros", "cuadro_curvas")
    p.locator('[data-civil-form="cuadro_curvas"] [data-civil-elegir="curvas"]').click()
    elegir_en_dibujo(p, [(X0 + 50, Y0 + 90)], mapa_lote)
    try:
        esperar(p, "document.querySelector('[data-civil-cuenta=curvas]').classList.contains('civil-elegido')", LARGO)
    except AssertionError:
        p.screenshot(path=os.path.expanduser("~/.tresvizo/agentes/G/salida/depurar-cuadro-curvas.png"))
        print("\ncuadro de curvas:", p.evaluate("window.__avisos"), pedir(p, {"op": "state"}).get("selection"), flush=True)
        raise
    assert "1 curva" in p.locator('[data-civil-cuenta="curvas"]').inner_text()
    foto(p, "09b-cuadro-curvas", 1280)
    p.locator('[data-civil-form="cuadro_curvas"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-cuadro-curvas').open", LARGO)
    claves = [e["value"] for e in pedir(p, {"op": "query", "type": "Text", "layer": "CUADRO_CURVAS_CLAVE", "detail": "geometry"})["entities"]]
    titulos = [e["value"] for e in pedir(p, {"op": "query", "type": "Text", "layer": "CUADRO_TITULO", "detail": "geometry"})["entities"]]
    assert claves == ["C1"] and "CUADRO DE CURVAS" in titulos

    # 6. Perfil y secciones de un eje con rasante
    eje = pedir(p, {"op": "entities_create", "entities": [{"type": "LwPolyline", "vertices": [[X0 + 20, Y0 + 150], [X0 + 280, Y0 + 160]], "closed": False, "layer": "EJE"}]})["result"]["handles"][0]
    mapa_eje = enfocar(p, (X0 + 20, Y0 + 150, X0 + 280, Y0 + 160))
    menu(p, "ejes", "perfil")
    p.locator('[data-civil-form="perfil"] [data-civil-elegir="eje"]').click()
    elegir_en_dibujo(p, [(X0 + 150, Y0 + 155)], mapa_eje)
    esperar(p, "document.querySelector('[data-civil-form=perfil] [data-civil-cuenta=eje]').classList.contains('civil-elegido')", LARGO)
    p.locator("#civil-per-conras").check()
    filas = p.locator('[data-civil-form="perfil"] [data-civil-pivs] tr')
    filas.nth(0).locator("[name=piv_z]").fill("112")
    p.locator('[data-civil-form="perfil"] [data-civil-agregar-piv]').click()
    filas.nth(1).locator("[name=piv_e]").fill("0+260.19")
    filas.nth(1).locator("[name=piv_z]").fill("118")
    foto(p, "10-perfil", 1280)
    p.locator('[data-civil-form="perfil"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-perfil').open", LARGO * 3)
    assert cuenta(p, None, "PERFIL_TERRENO") >= 1 and cuenta(p, None, "PERFIL_RASANTE") == 1

    apagar(p, "EJE_ESTACIONES")                                             # las marcas del perfil están sobre el eje
    mapa_eje = enfocar(p, (X0 + 20, Y0 + 150, X0 + 280, Y0 + 160))
    menu(p, "ejes", "secciones")
    p.locator('[data-civil-form="secciones"] [data-civil-elegir="eje"]').click()
    elegir_en_dibujo(p, [(X0 + 169.5, Y0 + 155.75)], mapa_eje)   # entre dos marcas de estación del perfil
    try:
        esperar(p, "document.querySelector('[data-civil-form=secciones] [data-civil-cuenta=eje]').classList.contains('civil-elegido')", LARGO)
    except AssertionError:
        p.screenshot(path=os.path.expanduser("~/.tresvizo/agentes/G/salida/depurar-secciones.png"))
        print("\nsecciones:", p.evaluate("window.__avisos"), pedir(p, {"op": "state"}).get("selection"), flush=True)
        raise
    p.locator("#civil-sec-conras").check()
    fs = p.locator('[data-civil-form="secciones"] [data-civil-pivs] tr')
    fs.nth(0).locator("[name=piv_z]").fill("112")
    p.locator('[data-civil-form="secciones"] [data-civil-agregar-piv]').click()
    fs.nth(1).locator("[name=piv_e]").fill("0+260.19")
    fs.nth(1).locator("[name=piv_z]").fill("118")
    foto(p, "11-secciones", 1280)
    p.locator('[data-civil-form="secciones"] button[type=submit]').click()
    esperar(p, "!document.querySelector('[data-civil-form=secciones] [data-civil-resultado]').hidden", LARGO * 3)
    assert "Corte" in p.locator('[data-civil-form="secciones"] [data-civil-resultado]').inner_text()
    p.locator("#civil-hoja-secciones [data-tv-cerrar]").first.click()
    assert cuenta(p, None, "SECCION_PROYECTO") == 14 and cuenta(p, "Text", "VOLUMEN_TABLA") > 10

    # 7. Volumen entre superficies: una plataforma de proyecto a 110 m
    plat = tmp_path / "plataforma.txt"
    plat.write_text("\r\n".join(f"P{i},{Y0 + y},{X0 + x},110.000,PLAT" for i, (x, y) in enumerate([(100, 100), (200, 100), (200, 200), (100, 200), (150, 150)])) + "\r\n")
    menu(p, "puntos", "importar")
    p.locator('[data-civil-form="importar"] input[name=tipo][value=proyecto]').check()
    p.locator("#civil-imp-archivo").set_input_files(str(plat))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO)
    menu(p, "puntos", "renumerar")
    p.locator('[data-civil-form="renumerar"] input[name=cuales][value=proyecto]').check()
    p.locator("#civil-ren-inicio").fill("101")
    p.locator("#civil-ren-prefijo").fill("PL-")
    p.locator("#civil-ren-orden").select_option("oeste_este")
    foto(p, "02b-renumerar", 1280)
    p.locator('[data-civil-form="renumerar"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-renumerar').open", LARGO)
    esperar_libre(p)
    pl = pedir(p, {"op": "query", "type": "INSERT", "layer": "PUNTOS_PROYECTO", "detail": "full", "fields": ["position", "attributes"]})["entities"]
    numeros = {e["attributes"]["NUM"]: round(e["position"][0] - X0) for e in pl}
    assert sorted(numeros) == ["PL-101", "PL-102", "PL-103", "PL-104", "PL-105"] and numeros["PL-101"] == 100 and numeros["PL-105"] == 200, numeros
    assert {e["attributes"]["DESC"] for e in pl} == {"PLAT"}
    menu(p, "terreno", "triangular")
    p.locator('[data-civil-form="triangular"] input[name=tipo][value=proyecto]').check()
    p.locator('[data-civil-form="triangular"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-triangular').open", LARGO)
    assert cuenta(p, "3DFACE", "TRIANGULACION_PROYECTO") == 4
    menu(p, "volumenes", "volumen")
    p.locator('[data-civil-form="volumen"] button[type=submit]').click()
    esperar(p, "!document.querySelector('[data-civil-form=volumen] [data-civil-resultado]').hidden", LARGO * 3)
    texto = p.locator('[data-civil-form="volumen"] [data-civil-resultado]').inner_text()
    texto = texto.lower()
    assert "corte" in texto and "terraplén" in texto and "10,000.00 m²" in texto  # zona común de 100 × 100
    foto(p, "12-volumen", 1280)
    p.locator("#civil-hoja-volumen [data-tv-cerrar]").first.click()

    # 7c. Escala 1:500 con «Ajustar los puntos ya dibujados»: los 5 005 puntos se vuelven a dibujar a la mitad, con sus rótulos
    esperar_libre(p)
    p.locator("[data-civil=escala]").click()
    p.locator("#civil-escala-den").select_option("500")
    p.locator('[data-civil-form="escala"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-escala').open", LARGO * 3)
    esperar_libre(p)
    assert p.locator("#civil-escala-texto").inner_text() == "1:500"
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 5000 and cuenta(p, "INSERT", "PUNTOS_PROYECTO") == 5
    e = pedir(p, {"op": "query", "type": "INSERT", "layer": "PUNTOS_PROYECTO", "limit": 1, "detail": "full"})["entities"][0]
    escala_x = e.get("properties", {}).get("x_scale") or (e.get("properties", {}).get("scale") or {}).get("x")
    assert escala_x == pytest.approx(0.5) and e["attributes"]["NUM"].startswith("PL-"), e

    # 8. Guardar en DWG y volver a abrir: todo sigue ahí (cuando Civil terminó de dibujar la línea de ceros)
    esperar_libre(p)
    p.locator("#cad-guardar").click()
    try:
        esperar_estado(p, "guardado", LARGO * 3)
    except AssertionError:
        print("\nguardar:", p.evaluate("TVCad.ocupado()"), p.evaluate("window.__avisos"), p.evaluate("document.getElementById('cad-estado-texto').textContent"), flush=True)
        raise
    v = VersionPlano.objects.filter(plano=plano).order_by("-numero").first()
    assert v.formato == "dwg" and servicio.ruta_version(v).stat().st_size > 100_000
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 5000
    assert cuenta(p, "3DFACE", "TRIANGULACION") == caras
    ins = pedir(p, {"op": "query", "type": "INSERT", "layer": "PUNTOS_TERRENO", "limit": 1, "detail": "full", "fields": ["attributes"]})["entities"][0]
    assert ins["attributes"]["DESC"] == "TN"
    esperar(p, "document.getElementById('civil-escala-texto').textContent === '1:500'", LARGO)   # la escala viaja con el plano
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)
    print(f"\n{motor}: importar 5000 puntos {importar_s:.1f} s · triangular {triangular_s:.1f} s")


@pytest.mark.parametrize("motor", MOTORES)
def test_civil_v11(cad, navegar, crear_usuario, roles, motor, tmp_path):
    """v1.1 por la página: cuadro RAN, rumbo y distancia, numerar, memoria (PDF), reporte (DOCX), localizar, insertar puntos y cuadrícula."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio v1.1", "", "dwg")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); }; }")
    # puntos y triangulación
    archivo = tmp_path / "puntos.txt"
    archivo.write_bytes(archivo_pnezd(300, lado=150.0))
    menu(p, "puntos", "importar")
    p.locator("#civil-imp-archivo").set_input_files(str(archivo))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO)
    menu(p, "terreno", "triangular")
    p.locator('[data-civil-form="triangular"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-triangular').open", LARGO * 2)
    caras = cuenta(p, "3DFACE", "TRIANGULACION")
    apagar(p, "PUNTOS_TERRENO", "PUNTOS_NUMERO", "PUNTOS_ELEVACION", "PUNTOS_DESCRIPCION", "TRIANGULACION")

    # localizar el punto 7: la vista va a él y queda seleccionado
    menu(p, "puntos", "localizar")
    p.locator("#civil-loc-num").fill("7")
    p.locator('[data-civil-form="localizar"] button[type=submit]').click()
    esperar_aviso(p, "Punto 7:")
    sel = pedir(p, {"op": "state"})["selection"]
    ins = pedir(p, {"op": "query", "handles": sel, "detail": "full"})["entities"][0]
    assert len(sel) == 1 and ins["attributes"]["NUM"] == "7"

    # un lote: cuadro RAN, rumbo y distancia, numerar vértices y memoria, eligiendo el lote con el ratón
    lote = pedir(p, {"op": "entities_create", "entities": [{"type": "LwPolyline", "vertices": [[X0 + 40, Y0 + 40], [X0 + 40, Y0 + 110], [X0 + 120, Y0 + 110], [X0 + 120, Y0 + 40]], "closed": True, "layer": "LOTE"}]})["result"]["handles"][0]
    assert lote
    mapa = enfocar(p, (X0 + 40, Y0 + 40, X0 + 120, Y0 + 110))
    menu(p, "cuadros", "cuadro")
    p.locator('[data-civil-form="cuadro"] input[name=formato][value=ran]').check()
    p.locator('[data-civil-form="cuadro"] [data-civil-elegir="poligono"]').click()
    elegir_en_dibujo(p, [(X0 + 40, Y0 + 75)], mapa)
    esperar(p, "document.querySelector('[data-civil-form=cuadro] [data-civil-cuenta=poligono]').classList.contains('civil-elegido')", LARGO)
    p.locator('[data-civil-form="cuadro"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-cuadro').open", LARGO)
    titulos = [e["value"] for e in pedir(p, {"op": "query", "type": "Text", "layer": "CUADRO_TEXTOS", "detail": "geometry"})["entities"]]
    assert "LATITUD" in titulos and "COORDENADAS GEOGRÁFICAS" in titulos

    mapa = enfocar(p, (X0 + 40, Y0 + 40, X0 + 120, Y0 + 110))
    menu(p, "cuadros", "rumbos")
    p.locator('[data-civil-form="rumbos"] [data-civil-elegir="lineas"]').click()
    elegir_en_dibujo(p, [(X0 + 40, Y0 + 75)], mapa)
    esperar(p, "document.querySelector('[data-civil-form=rumbos] [data-civil-cuenta=lineas]').classList.contains('civil-elegido')", LARGO)
    p.locator('[data-civil-form="rumbos"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-rumbos').open", LARGO)
    rumbos = sorted(e["value"] for e in pedir(p, {"op": "query", "type": "Text", "layer": "LADOS_RUMBO", "detail": "geometry"})["entities"])
    assert rumbos == sorted(["N 0°00'00\" E", "N 90°00'00\" E", "S 0°00'00\" E", "S 90°00'00\" W"]), rumbos

    apagar(p, "LADOS_RUMBO", "LADOS_DISTANCIA", "VERTICES_POLIGONO")
    mapa = enfocar(p, (X0 + 40, Y0 + 40, X0 + 120, Y0 + 110))
    menu(p, "cuadros", "memoria")
    p.locator('[data-civil-form="memoria"] [data-civil-elegir="poligono"]').click()
    elegir_en_dibujo(p, [(X0 + 40, Y0 + 75)], mapa)
    esperar(p, "document.querySelectorAll('[data-civil-colindancias-lista] input').length === 4", LARGO)
    p.locator("#civil-mem-predio").fill("El Mezquite")
    p.locator("#civil-mem-col-0").fill("Calle Juárez")
    foto(p, "21b-memoria-colindancias", 1280)
    with p.expect_download(timeout=LARGO) as descarga:
        p.locator('[data-civil-form="memoria"] button[type=submit]').click()
    d = descarga.value
    assert d.suggested_filename == "Memoria descriptiva El Mezquite.pdf" and open(d.path(), "rb").read(5) == b"%PDF-"
    p.locator("#civil-hoja-memoria [data-tv-cerrar]").first.click()

    # reporte de puntos en Word
    menu(p, "puntos", "reporte")
    p.locator('[data-civil-form="reporte"] input[name=formato][value=docx]').check()
    p.locator("#civil-rep-geo").check()
    with p.expect_download(timeout=LARGO) as descarga:
        p.locator('[data-civil-form="reporte"] button[type=submit]').click()
    d = descarga.value
    import zipfile
    with zipfile.ZipFile(d.path()) as z:
        xml = z.read("word/document.xml").decode()
    assert d.suggested_filename.endswith(".docx") and "LATITUD" in xml and xml.count("<w:tr>") == 301
    p.locator("#civil-hoja-reporte [data-tv-cerrar]").first.click()

    # insertar un punto de proyecto en la triangulación del terreno
    plat = tmp_path / "uno.txt"
    plat.write_text(f"P1,{Y0 + 75.3},{X0 + 75.7},140.000,NUEVO\r\n")
    menu(p, "puntos", "importar")
    p.locator('[data-civil-form="importar"] input[name=tipo][value=proyecto]').check()
    p.locator("#civil-imp-archivo").set_input_files(str(plat))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO)
    apagar(p, "LOTE", "CUADRO_LINEAS", "CUADRO_TEXTOS", "CUADRO_TITULO")
    mapa = enfocar(p, (X0 + 70, Y0 + 70, X0 + 80, Y0 + 80))
    menu(p, "terreno", "insertar")
    p.locator('[data-civil-form="insertar"] button[type=submit]').click()
    elegir_en_dibujo(p, [(X0 + 75.7, Y0 + 75.3)], mapa)
    esperar_aviso(p, "insertado", LARGO * 2)
    assert cuenta(p, "3DFACE", "TRIANGULACION") == caras + 2

    # cuadrícula de todo el plano, dos veces: la segunda reemplaza a la primera
    for _ in range(2):
        menu(p, "plano", "cuadricula")
        p.locator('[data-civil-form="cuadricula"] button[type=submit]').click()
        esperar(p, "!document.getElementById('civil-hoja-cuadricula').open", LARGO)
        esperar_libre(p)
        marcos = cuenta(p, None, "RETICULA_MARCO")
    assert marcos == 1 and cuenta(p, "Text", "RETICULA_TEXTOS") > 8
    assert not [a for a in p.evaluate("window.__avisos") if a[1] == "error"], p.evaluate("window.__avisos")
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)


@pytest.mark.parametrize("motor", ["chromium"])
def test_civil_en_360_px_no_rompe_la_pagina(cad, navegar, crear_usuario, roles, motor):
    """En el teléfono los planos se abren para ver: Civil no estorba y la página no se desborda."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio", "", "dxf")
    n = navegar(ana, motor, ancho=360, alto=780, movil=True)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    p.wait_for_timeout(800)
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    foto(p, "00-telefono", 360)
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", ["chromium"])
def test_sin_elegir_en_el_dibujo_usa_la_seleccion_y_lo_dice(cad, navegar, crear_usuario, roles, motor):
    """Con un editor sin user_select (gui_required), Civil usa lo seleccionado y lo pide con «Listo»."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, "Predio", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    lote = pedir(p, {"op": "entities_create", "entities": [{"type": "LwPolyline", "vertices": [[X0, Y0], [X0, Y0 + 100], [X0 + 100, Y0 + 100], [X0 + 100, Y0]], "closed": True}]})["result"]["handles"][0]
    # Se simula el editor oficial: user_select y getpoint responden gui_required
    p.evaluate("""() => { const m = document.getElementById('cad-editor').contentWindow.ocsControl; const req = m.request.bind(m);
      m.request = (q, o) => (q.op === 'user_select' || q.op === 'getpoint') ? Promise.resolve({ok: false, code: 'gui_required', error: 'gui'}) : req(q, o); }""")
    menu(p, "cuadros", "cuadro")
    p.locator('[data-civil-form="cuadro"] [data-civil-elegir="poligono"]').click()
    esperar(p, "!document.getElementById('civil-pick').hidden && !document.getElementById('civil-pick-listo').hidden", LARGO)
    assert "Listo" in p.locator("#civil-pick").inner_text()
    pedir(p, {"op": "select", "handles": [lote]})
    p.locator("#civil-pick-listo").click()
    esperar(p, "document.querySelector('[data-civil-form=cuadro] [data-civil-cuenta=poligono]').classList.contains('civil-elegido')", LARGO)
    assert "4 vértices" in p.locator('[data-civil-form="cuadro"] [data-civil-cuenta="poligono"]').inner_text()
    assert not n.errores, n.errores




@pytest.mark.skipif(not CAPTURAS, reason="solo con ITT_CAPTURAS=carpeta: capturas de las hojas de Civil y de lo dibujado")
@pytest.mark.parametrize("tema", ["claro", "oscuro"])
@pytest.mark.parametrize("ancho", [1280, 360])
def test_capturas_civil(cad, navegar, crear_usuario, roles, tema, ancho, tmp_path):
    from tvplataforma.models import Preferencias

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    Preferencias.objects.create(usuario_id=ana.pk, tema=tema)
    plano = servicio.crear_vacio(ana, "Levantamiento Civil", "Predio Sur", "dwg")
    movil = ancho < 768
    n = navegar(ana, "chromium", ancho=ancho, alto=780 if movil else 900, movil=movil)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    if movil:
        # en el teléfono los planos se abren para ver: Civil no aparece y la página no se desborda
        p.wait_for_timeout(800)
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
        foto(p, "00-telefono", ancho, tema)
        return
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    cerrar_aviso_gpu(p)
    foto(p, "00-barra", ancho, tema)
    for clave, nombre in (("puntos", "01-menu-puntos"), ("terreno", "01-menu-triangulacion"), ("ejes", "01-menu-perfiles")):
        p.locator(f'[data-civil-menu="{clave}"] > summary').click()
        foto(p, nombre, ancho, tema)
        p.keyboard.press("Escape")
    archivo = tmp_path / "levantamiento.txt"
    archivo.write_bytes(archivo_pnezd(400, lado=150.0))
    menu(p, "puntos", "importar")
    p.locator("#civil-imp-archivo").set_input_files(str(archivo))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    p.locator('[data-civil-form="importar"] details.civil-mas').evaluate("d => d.open = true")
    foto(p, "02-importar", ancho, tema)
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO)
    pedir(p, {"op": "action", "name": "zoom_extents"})
    foto(p, "03-puntos", ancho, tema)
    for clave, accion, hoja, nombre in (("puntos", "exportar", "civil-hoja-exportar", "04-exportar"), ("terreno", "borrar", "civil-hoja-borrar", "06-borrar"), ("terreno", "revisar", "civil-hoja-revisar", "06b-revisar"),
                                        ("cuadros", "cuadro", "civil-hoja-cuadro", "09-cuadro"), ("cuadros", "cuadro_curvas", "civil-hoja-cuadro-curvas", "09b-cuadro-curvas"),
                                        ("puntos", "renumerar", "civil-hoja-renumerar", "02b-renumerar"), ("puntos", "localizar", "civil-hoja-localizar", "16-localizar"),
                                        ("puntos", "reporte", "civil-hoja-reporte", "17-reporte"), ("terreno", "insertar", "civil-hoja-insertar", "18-insertar"),
                                        ("cuadros", "rumbos", "civil-hoja-rumbos", "19-rumbos"), ("cuadros", "numerar", "civil-hoja-numerar", "20-numerar"),
                                        ("cuadros", "memoria", "civil-hoja-memoria", "21-memoria"), ("plano", "cuadricula", "civil-hoja-cuadricula", "22-cuadricula"), ("ejes", "perfil", "civil-hoja-perfil", "10-perfil"),
                                        ("ejes", "secciones", "civil-hoja-secciones", "11-secciones"), ("volumenes", "volumen", "civil-hoja-volumen", "12-volumen")):
        menu(p, clave, accion)
        p.locator(f"#{hoja} details.civil-mas").evaluate_all("ds => ds.forEach(d => d.open = true)")
        p.locator(f"#{hoja} input[name=con_rasante]").evaluate_all("cs => cs.forEach(c => { c.checked = true; c.dispatchEvent(new Event('change', {bubbles: true})); })")
        foto(p, nombre, ancho, tema)
        p.locator(f"#{hoja} [data-tv-cerrar]").first.click()
    p.locator("[data-civil=escala]").click()
    foto(p, "13-escala", ancho, tema)
    p.locator("#civil-hoja-escala [data-tv-cerrar]").first.click()
    menu(p, "terreno", "triangular")
    foto(p, "05-triangular", ancho, tema)
    p.locator('[data-civil-form="triangular"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-triangular').open", LARGO * 2)
    menu(p, "curvas", "curvas")
    foto(p, "07-curvas", ancho, tema)
    p.locator('[data-civil-form="curvas"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-curvas').open", LARGO * 2)
    # lo dibujado: cuadro, perfil y secciones por la vía de la página (sin elegir en el dibujo)
    lote = {"vertices": [[X0 + 30, Y0 + 30], [X0 + 30, Y0 + 110], [X0 + 120, Y0 + 110], [X0 + 120, Y0 + 30]], "combas": [0, -0.2, 0, 0], "cerrada": True}
    eje = {"vertices": [[X0 + 10, Y0 + 75], [X0 + 140, Y0 + 80]]}
    caras = p.evaluate("""async () => { const r = await TVCad.pedir({op: 'records', collection: 'entities', type: '3DFACE', paths: ['/first_corner', '/second_corner', '/third_corner'], limit: 10000});
        return r.records.map(e => ({handle: e.handle, vertices: ['/first_corner', '/second_corner', '/third_corner'].map(k => [e.values[k].x, e.values[k].y, e.values[k].z])})); }""")
    for orden, datos in (("cuadro", {"poligono": lote}), ("perfil", {"caras": caras, "eje": eje, "intervalo": 10, "rasante": [[0, 106], [130.1, 110]]}),
                         ("secciones", {"caras": caras, "eje": eje, "intervalo": 20, "izquierda": 15, "derecha": 15, "rasante": [[0, 106], [130.1, 110]]})):
        p.evaluate("""async ([o, d]) => { if (o !== 'cuadro') d.esquina_superior = await TVCivil.lugarLibre(o === 'perfil' ? 'debajo' : 'lado');
            const r = await TVCivil.calcular(o, d); await TVCivil.insertarDxf(r.dxf, o + '.dxf'); }""", [orden, datos])
    pedir(p, {"op": "action", "name": "zoom_extents"})
    foto(p, "14-dibujado", ancho, tema)
    # v1.1: cuadro RAN, rumbo y distancia de los lados, y la cuadrícula UTM de todo el plano
    lote2 = {"vertices": [[X0 + 30, Y0 - 120], [X0 + 30, Y0 - 40], [X0 + 120, Y0 - 40], [X0 + 120, Y0 - 120]], "cerrada": True}
    p.evaluate("""async (l) => { const z = {formato: 'ran', zona: 14, hemisferio: 'N'};
        let r = await TVCivil.calcular('cuadro', {poligono: l, opciones: z, esquina: [l.vertices[2][0] + 20, l.vertices[2][1]]}); await TVCivil.insertarDxf(r.dxf, 'ran.dxf');
        r = await TVCivil.calcular('anotar_lados', {polilineas: [l], numerar_vertices: false}); await TVCivil.insertarDxf(r.dxf, 'lados.dxf'); }""", lote2)
    p.evaluate("""async () => { const v = (await TVCad.pedir({op: 'sysvar', get: ['extmin', 'extmax']})); const x = (v.result && v.result.values) || v.values;
        const r = await TVCivil.calcular('cuadricula', {caja: [x.extmin[0], x.extmin[1], x.extmax[0], x.extmax[1]], zona: 14}); await TVCivil.insertarDxf(r.dxf, 'reticula.dxf'); }""")
    pedir(p, {"op": "action", "name": "zoom_extents"})
    cerrar_aviso_gpu(p)
    foto(p, "23-v11-dibujado", ancho, tema)
    assert not n.errores, n.errores
