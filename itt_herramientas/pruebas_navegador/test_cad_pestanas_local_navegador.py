"""Donde se juntan las pestañas (varios planos en una ventana) y la carpeta del proyecto en esta computadora, con el editor real.

- Cada pestaña con su carpeta: dos proyectos cuyos planos nombran la misma ruta («C:\\Obra\\Fotos\\f001.png») con otro archivo cada uno;
  al cambiar de pestaña cada plano muestra la suya (se comprueba el píxel), una tercera pestaña sin carpeta muestra el marco, y cerrar una
  pestaña suelta sus imágenes.
- Insertar una imagen georreferenciada en una pestaña, pasar a otra y «Guardar todo»: los IMAGE quedan en cada versión. Al recargar
  vuelven las pestañas y el permiso de las dos carpetas se pide con un solo toque.
- Esc con «Referencias externas» o con la hoja de insertar abiertos: el primero cierra la hoja, el siguiente cancela la orden.
- Nada de la carpeta viaja durante las operaciones de pestañas: registro de cada petición (cuerpos y direcciones) y el contador de bytes
  en el cable (contador_bytes.py).

Rásteres sintéticos (nunca datos de clientes). Corre bajo el candado de la máquina, un navegador a la vez.
"""
from __future__ import annotations

import os
import types
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio
from itt_herramientas.models import VersionPlano

from . import cad_local_rasters as r
from .contador_bytes import ContadorBytes, agrupar
from .test_cad_local_navegador import Red, carpeta_opfs, color_en, esperar_imagenes, insertado, listo, sin_subir, ver
from .test_cad_navegador import LARGO, Navegador, _errores_propios, _persona, cad, esperar_estado  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
WINDOWS = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
X0, Y0 = 389000.0, 3169000.0
RUTA = "C:\\Obra\\Fotos\\f001.png"


@pytest.fixture
def ana(crear_usuario, roles):
    return _persona(crear_usuario, roles, "ana", "Ana López", "cad")


@pytest.fixture
def proxy(servidor):
    u = urlparse(servidor.url)
    c = ContadorBytes(u.hostname, u.port).arrancar()
    yield c
    c.detener()


@pytest.fixture
def abrir(playwright_, servidor, sesion_de):
    abiertos = []

    def _abrir(usuario, motor="chromium", por=None, **kw):
        if motor == "chromium":
            kw.setdefault("user_agent", WINDOWS)
        n = Navegador(playwright_, motor, por or servidor, sesion_de(usuario), **kw)
        abiertos.append(n)
        return n
    yield _abrir
    for n in abiertos:
        n.cerrar()


def proyecto(base: Path, nombre: str, color, extra: str) -> Path:
    """Una carpeta de proyecto: Fotos/f001.png de un color (80 × 60 px) y un archivo que ningún plano nombra."""
    from PIL import Image

    carpeta = base / nombre
    (carpeta / "Fotos").mkdir(parents=True)
    Image.new("RGB", (80, 60), color).save(carpeta / "Fotos" / "f001.png")
    Image.new("RGB", (16, 16), (128, 128, 128)).save(carpeta / extra)
    return carpeta


def plano_con_imagen(usuario, tmp: Path, nombre: str):
    """Un DXF con un IMAGE de 40 × 30 m en (X0, Y0) que nombra RUTA, como lo deja AutoCAD con la ruta absoluta de Windows."""
    import ezdxf

    d = ezdxf.new("R2018", setup=False)
    d.header["$INSUNITS"] = 6
    m = d.modelspace()
    definicion = d.add_image_def(filename=RUTA, size_in_pixel=(80, 60))
    m.add_image(definicion, insert=(X0, Y0), size_in_units=(40.0, 30.0), rotation=0)
    m.add_line((X0 - 5, Y0 - 5), (X0 + 45, Y0 + 35))
    salida = tmp / f"{nombre}.dxf"
    d.saveas(salida)
    return servicio.crear_desde_ruta(usuario, salida, nombre, "Demo")


def pestanas(p):
    return p.evaluate("TVCadPestanas.lista()")


def esperar_pestanas(p, n):
    esperar(p, f"TVCadPestanas.lista().length === {n} && TVCadPestanas.lista().every(t => t.cargado)", LARGO)


def activa(p, plano_id):
    plano_id = str(plano_id)
    p.evaluate("(id) => TVCadPestanas.activar(id)", plano_id)
    esperar(p, f"TVCadPestanas.lista().some(t => t.activa && t.id === {plano_id!r}) && TVCad.estado().plano.id === {plano_id!r}"
               f" && window.TVCadLocal && TVCadLocal.estado().plano === {plano_id!r}", LARGO)


def vincular_desde_el_panel(p, carpeta: Path, nombre: str):
    carpeta_opfs(p, carpeta, nombre)
    p.locator("#cad-boton-referencias").click()
    expect(p.locator("#cad-referencias")).to_be_visible()
    p.locator("#cad-local-vincular").click()
    esperar(p, f"TVCadLocal.estado().raices.includes({nombre!r})", LARGO)
    p.locator("#cad-referencias [data-tv-cerrar]").last.click()
    expect(p.locator("#cad-referencias")).to_be_hidden()


def de_la_imagen(p):
    """Lo que el editor tiene de RUTA para el plano a la vista (`supplied` de `references`)."""
    refs = p.evaluate("TVCad.pedir({op: 'references'})")
    return [i["supplied"] for i in refs["images"] if i["path"] == RUTA]


def rojo(c):
    return c[0] > 150 and c[1] < 90 and c[2] < 90


def azul(c):
    return c[2] > 150 and c[0] < 90


def esperar_color(p, prueba, x, y, que):
    ultimo = None
    for _ in range(40):
        ultimo = color_en(p, x, y)
        if prueba(ultimo):
            return ultimo
        p.wait_for_timeout(250)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/fallo-pestanas-local-{que}.png")
    raise AssertionError(f"{que}: el píxel es {ultimo}; {p.evaluate('TVCadLocal.estado()')}")


def test_cada_pestana_con_su_carpeta_y_nada_viaja(cad, abrir, ana, tmp_path, proxy):
    a_dir = proyecto(tmp_path, "Proyecto_A", (230, 20, 20), "solo_en_la_carpeta_A.png")
    b_dir = proyecto(tmp_path, "Proyecto_B", (20, 40, 230), "solo_en_la_carpeta_B.png")
    a = plano_con_imagen(ana, tmp_path, "Fachada A")
    b = plano_con_imagen(ana, tmp_path, "Fachada B")
    c = plano_con_imagen(ana, tmp_path, "Fachada C")
    n = abrir(ana, "chromium", por=types.SimpleNamespace(url=proxy.url), ancho=1366, alto=860)
    p = n.ir(f"/cad/{a.pk}/")
    red = Red(p)
    listo(p)
    expect(p.locator("#cad-aviso-local")).to_be_visible(timeout=LARGO)
    vincular_desde_el_panel(p, a_dir, "Proyecto_A")
    ver(p, X0 + 20, Y0 + 15, 60)
    esperar_imagenes(p, 1)
    esperar_color(p, rojo, X0 + 20, Y0 + 15, "A en rojo")

    # Desde aquí, solo operaciones de pestañas y de la carpeta: nada de la carpeta debe viajar.
    proxy.esperar_quieto()
    proxy.marcar("pestanas")
    inicio = len(red.peticiones)

    # B: la misma ruta, otro proyecto. Sin su carpeta, el editor NO muestra la de A (marco y aviso).
    assert p.evaluate("(id) => TVCadPestanas.abrir(id)", str(b.pk)) == str(b.pk)
    esperar_pestanas(p, 2)
    esperar(p, f"window.TVCadLocal && TVCadLocal.estado().plano === {str(b.pk)!r}", LARGO)
    esperar(p, "TVCad.pedir({op: 'references'}).then(r => r.images.length === 1 && r.images[0].supplied === null)", LARGO)
    expect(p.locator("#cad-aviso-local")).to_be_visible(timeout=LARGO)
    vincular_desde_el_panel(p, b_dir, "Proyecto_B")
    ver(p, X0 + 20, Y0 + 15, 60)
    esperar_imagenes(p, 1)
    esperar_color(p, azul, X0 + 20, Y0 + 15, "B en azul")
    est = p.evaluate("TVCadLocal.estado()")
    assert [x["plano"] for x in est["editor"] if x["ruta"] == RUTA] == [str(b.pk)], est["editor"]

    # De vuelta en A: su archivo otra vez, no el de B; la carpeta de A sigue vinculada a A.
    activa(p, str(a.pk))
    assert p.evaluate("TVCadLocal.estado().raices") == ["Proyecto_A"]
    esperar_imagenes(p, 1)
    esperar_color(p, rojo, X0 + 20, Y0 + 15, "A otra vez en rojo")

    # C, sin carpeta: el marco con el nombre, aunque A y B tengan esa ruta.
    assert p.evaluate("(id) => TVCadPestanas.abrir(id)", str(c.pk)) == str(c.pk)
    esperar_pestanas(p, 3)
    esperar(p, f"TVCadLocal.estado().plano === {str(c.pk)!r}", LARGO)
    esperar(p, "TVCad.pedir({op: 'references'}).then(r => r.images[0].supplied === null)", LARGO)
    p.wait_for_timeout(1500)
    assert de_la_imagen(p) == [None], "C no tiene carpeta: no debe ver la imagen de otro proyecto"
    expect(p.locator("#cad-aviso-local-vincular")).to_be_visible()
    ver(p, X0 + 20, Y0 + 15, 60)
    p.wait_for_timeout(800)
    centro = color_en(p, X0 + 20, Y0 + 15)
    assert not rojo(centro) and not azul(centro), centro
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-pestanas-local-sin-carpeta-1366.png")

    # B otra vez (azul) y cerrarla: lo suyo sale del editor y de la página; A sigue con lo suyo.
    activa(p, str(b.pk))
    esperar_imagenes(p, 1)
    esperar_color(p, azul, X0 + 20, Y0 + 15, "B otra vez en azul")
    p.locator(".cad-pestana", has_text="Fachada B").locator(".cad-pestana__cerrar").click()
    esperar_pestanas(p, 2)
    esperar(p, f"!TVCadLocal.estado().planos.some(x => x.plano === {str(b.pk)!r})", LARGO)
    est = p.evaluate("TVCadLocal.estado()")
    assert not [x for x in est["editor"] if x["plano"] == str(b.pk)], est["editor"]
    activa(p, str(a.pk))
    esperar_imagenes(p, 1)
    esperar_color(p, rojo, X0 + 20, Y0 + 15, "A tras cerrar B")
    # Las pestañas de atrás no se quedan con detalles (solo su vista reducida, con tope).
    est = p.evaluate("TVCadLocal.estado()")
    assert not [x for x in est["editor"] if x["plano"] != str(a.pk) and x["detalle"]], est["editor"]

    # Nada de las carpetas viajó: ni bytes de sus archivos, ni nombres que los planos no traen; y pocos bytes subidos.
    proxy.esperar_quieto()
    muestras = [f.read_bytes()[40:104] for d in (a_dir, b_dir) for f in sorted(d.rglob("*.png"))]
    subidos = sin_subir(red, inicio, muestras, ["Proyecto_A", "Proyecto_B", "solo_en_la_carpeta_A", "solo_en_la_carpeta_B"])
    tramo = proxy.tramo("pestanas")
    pedido = sum(x.pedido for x in tramo)
    for x in tramo:
        assert "Proyecto_" not in x.ruta and "solo_en_la_carpeta" not in x.ruta, x.ruta
    assert subidos < 64 * 1024, (subidos, agrupar(tramo)[:8])
    assert pedido < 256 * 1024, (pedido, agrupar(tramo)[:8])
    assert p.locator("#cad-estado").get_attribute("data-estado") == "guardado"
    assert not _errores_propios(n, "chromium"), n.errores


def test_insertar_en_una_pestana_guardar_todo_y_un_solo_permiso(cad, abrir, ana, tmp_path):
    from PIL import Image

    # Dos ortofotos (200 × 100 px, píxel de 0.5 m) en dos carpetas: una para cada plano.
    carpetas = {}
    for nombre, color, x in (("Orto_A", (230, 20, 20), 389100.25), ("Orto_B", (20, 40, 230), 389400.25)):
        d = tmp_path / nombre
        d.mkdir()
        archivo = f"{nombre.lower()}.png"
        Image.new("RGB", (200, 100), color).save(d / archivo)
        r.mundo(d / f"{nombre.lower()}.pgw", 0.5, 0.0, 0.0, -0.5, x, 3169049.75)
        carpetas[nombre] = (d, archivo)
    a = servicio.crear_vacio(ana, "Ortofoto Norte", "", "dxf")
    b = servicio.crear_vacio(ana, "Ortofoto Sur", "", "dwg")
    n = abrir(ana, "chromium", ancho=1366, alto=860)
    p = n.ir(f"/cad/{a.pk}/")
    listo(p)
    assert p.evaluate("(id) => TVCadPestanas.abrir(id)", str(b.pk)) == str(b.pk)
    esperar_pestanas(p, 2)

    def insertar(nombre):
        d, archivo = carpetas[nombre]
        vincular_desde_el_panel(p, d, nombre)
        p.evaluate("() => { window.showOpenFilePicker = undefined; }")
        p.locator("#cad-menu summary").click()
        p.locator("#cad-menu-insertar-imagen").click()
        p.locator("#cad-local-imagen").set_input_files([str(d / archivo), str(d / archivo.replace(".png", ".pgw"))])
        expect(p.locator("#cad-local-ins-detalle")).to_contain_text("archivo de mundo", timeout=LARGO)
        p.locator("#cad-local-ins-ir").click()
        insertado(p)
        refs = p.evaluate("TVCad.pedir({op: 'references'})")["images"]
        assert [i["path"] for i in refs] == [archivo], refs
        esperar_imagenes(p, 1)

    # En B (la que se ve), luego en A: B queda atrás con su IMAGE sin guardar.
    activa(p, str(b.pk))
    insertar("Orto_B")
    activa(p, str(a.pk))
    insertar("Orto_A")
    lista = {t["id"]: t for t in pestanas(p)}
    assert lista[str(a.pk)]["sucio"] and lista[str(b.pk)]["sucio"], lista

    # Guardar todo: cada plano su versión, con su IMAGE y la ruta relativa a su carpeta.
    r_todo = p.evaluate("TVCadPestanas.guardarTodo()")
    assert r_todo["ok"] and all(f["ok"] for f in r_todo["filas"]), r_todo
    esperar(p, "TVCadPestanas.lista().every(t => !t.sucio)", LARGO)
    for plano, archivo in ((a, "orto_a.png"), (b, "orto_b.png")):
        v = VersionPlano.objects.filter(plano=plano).order_by("-numero").first()
        assert v is not None and v.numero >= 2, plano.nombre
        # El DXF se lee aquí; el DWG (comprimido) se comprueba al reabrirlo, abajo.
        if v.formato == "dxf":
            texto = servicio.ruta_version(v).read_bytes().decode("latin-1").replace("\r\n", "\n")
            assert archivo in texto and "IMAGEDEF" in texto and "\nIMAGE\n" in texto, plano.nombre
            assert "Orto_" not in texto, f"{plano.nombre}: la carpeta de la computadora no va en el plano"

    # Tras reiniciar el navegador: los permisos vuelven a «preguntar». Al recargar vuelven las dos pestañas; un solo toque basta para las dos.
    p.context.add_init_script("""
      if (self.FileSystemHandle) {
        self.__pedidos = 0;
        FileSystemHandle.prototype.queryPermission = async function () { return self.__concedido ? "granted" : "prompt"; };
        FileSystemHandle.prototype.requestPermission = async function () { self.__pedidos++; self.__concedido = true; return "granted"; };
      }""")
    p.reload()
    listo(p)
    esperar_pestanas(p, 2)
    # Cada pestaña reabre su última versión (la de Guardar todo), con su IMAGE y la ruta tal como se insertó.
    for plano, archivo in ((a, "orto_a.png"), (b, "orto_b.png")):
        refs = p.evaluate("([id]) => TVCad.pedir({op: 'references', document_id: TVCadPestanas.lista().find(t => t.id === id).documento})", [str(plano.pk)])
        assert [i["path"] for i in refs["images"]] == [archivo], (plano.nombre, refs["images"])
    esperar(p, "TVCadLocal.estado().planos.length === 2 && TVCadLocal.estado().planos.every(x => x.permisoPendiente)", LARGO)
    expect(p.locator("#cad-aviso-local-permitir")).to_be_visible(timeout=LARGO)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-pestanas-local-permiso-1366.png")
    p.locator("#cad-aviso-local-permitir").click()
    esperar(p, "TVCadLocal.estado().planos.every(x => !x.permisoPendiente)", LARGO)
    p.evaluate("TVCad.pedir({op: 'run', cmd: 'ZOOM E'})")
    esperar_imagenes(p, 1)
    otra = b.pk if p.evaluate("TVCad.estado().plano.id") == str(a.pk) else a.pk
    activa(p, otra)
    p.evaluate("TVCad.pedir({op: 'run', cmd: 'ZOOM E'})")
    esperar_imagenes(p, 1)
    expect(p.locator("#cad-aviso-local")).to_be_hidden()
    assert p.evaluate("self.__pedidos") == 2, "un toque: las dos carpetas a la vez"
    assert p.evaluate("TVCadLocal.estado().raices.length") == 1
    assert not _errores_propios(n, "chromium"), n.errores


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_esc_cierra_la_hoja_y_luego_cancela_la_orden(cad, abrir, ana, tmp_path, motor):
    from PIL import Image

    d = tmp_path / "Orto"
    d.mkdir()
    Image.new("RGB", (40, 20), (30, 160, 30)).save(d / "orto.png")
    r.mundo(d / "orto.pgw", 0.5, 0.0, 0.0, -0.5, 389100.25, 3169049.75)
    plano = plano_con_imagen(ana, tmp_path, "Fachada Esc")
    n = abrir(ana, motor, ancho=1280, alto=800)
    p = n.ir(f"/cad/{plano.pk}/")
    listo(p)
    lienzo = p.locator("#cad-editor")

    def orden():
        return p.evaluate("TVCad.pedir({op: 'state'})").get("command")

    def empezar_linea():
        lienzo.click(position={"x": 700, "y": 220})
        p.keyboard.type("LINE"); p.keyboard.press("Enter"); p.wait_for_timeout(300)
        lienzo.click(position={"x": 760, "y": 260}); p.wait_for_timeout(300)
        assert orden(), "LINE en curso"

    # «Referencias externas» abierto sobre una orden: un Esc cierra el panel (la orden sigue), el siguiente la cancela.
    empezar_linea()
    p.locator("#cad-boton-referencias").click()
    expect(p.locator("#cad-referencias")).to_be_visible()
    p.keyboard.press("Escape")
    expect(p.locator("#cad-referencias")).to_be_hidden()
    p.wait_for_timeout(400)
    assert orden(), "Esc cerró el panel; la orden sigue"
    p.keyboard.press("Escape")
    esperar(p, "TVCad.pedir({op: 'state'}).then(s => !s.command)", LARGO)

    # La hoja «Insertar imagen georreferenciada»: igual.
    empezar_linea()
    p.locator("#cad-menu summary").click()
    if motor == "chromium":
        p.evaluate("() => { window.showOpenFilePicker = undefined; }")
        p.locator("#cad-menu-insertar-imagen").click()
        p.locator("#cad-local-imagen").set_input_files([str(d / "orto.png"), str(d / "orto.pgw")])
    else:
        with p.expect_file_chooser() as fc:
            p.locator("#cad-menu-insertar-imagen").click()
        fc.value.set_files([str(d / "orto.png"), str(d / "orto.pgw")])
    expect(p.locator("#cad-local-insertar")).to_be_visible()
    expect(p.locator("#cad-local-ins-detalle")).to_contain_text("archivo de mundo", timeout=LARGO)
    assert orden(), "elegir la imagen no termina la orden"
    p.keyboard.press("Escape")
    expect(p.locator("#cad-local-insertar")).to_be_hidden()
    p.wait_for_timeout(400)
    assert orden(), "Esc cerró la hoja; la orden sigue"
    p.keyboard.press("Escape")
    esperar(p, "TVCad.pedir({op: 'state'}).then(s => !s.command)", LARGO)
    # Nada se insertó ni cambió.
    assert [i["path"] for i in p.evaluate("TVCad.pedir({op: 'references'})")["images"]] == [RUTA]
    assert p.locator("#cad-estado").get_attribute("data-estado") == "guardado"
    assert p.url.endswith(f"/cad/{plano.pk}/")
    assert not _errores_propios(n, motor), n.errores
