"""CAD en una tableta Android (docs/cad.md, «Tableta Android» y «El arranque vigilado»), Chromium con perfiles de tableta.

El agente reducido de Chrome en una tableta es «Linux; Android 10; K» sin «Mobile» (con «sitio de escritorio», el de Linux de escritorio);
la pantalla es de tableta (800 × 1280 en vertical, 1280 × 800 en horizontal), solo táctil (puntero grueso, sin uno fino) y con o sin
WebGPU. Comprueba: que el plano abre en cada perfil (1, 5 y 20 MB), en modo táctil, sin guardar el editor solo (como en el teléfono); que
un editor que no arranca nunca deja una carga sin fin (el aviso con «Reintentar», el otro modo y «Descargar el plano», y el informe al
servidor); que lo guardado en el equipo que no arranca se borra y el editor se abre de la red; y que los avisos se pueden tocar en modo
táctil. No es una tableta real: la GPU es SwiftShader (WebGL 2 por software). Corre bajo el candado de la máquina, un navegador a la vez.
"""
from __future__ import annotations

import io
import json
import uuid

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import editor as cad_editor
from itt_herramientas.cad import servicio
from itt_herramientas.models import Plano

from .test_cad_navegador import LARGO, _persona, cad, esperar_estado  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]

UA_TABLETA = "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
UA_SITIO_ESCRITORIO = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
PERFILES = {
    "vertical": dict(ua=UA_TABLETA, movil=True, w=800, h=1280, dpr=1.8, mem=4),
    "horizontal": dict(ua=UA_TABLETA, movil=True, w=1280, h=800, dpr=1.8, mem=8),
    "sitio-escritorio-vertical": dict(ua=UA_SITIO_ESCRITORIO, movil=False, w=800, h=1280, dpr=2.0, mem=8),
    "sitio-escritorio-horizontal": dict(ua=UA_SITIO_ESCRITORIO, movil=False, w=1280, h=800, dpr=1.5, mem=8),
    "sin-webgpu": dict(ua=UA_TABLETA, movil=True, w=800, h=1280, dpr=2.0, mem=4, sin_webgpu=True),
}
SIN_WEBGPU = "Object.defineProperty(Navigator.prototype, 'gpu', { get: () => undefined });"
SIN_WEBGL = ("const g = HTMLCanvasElement.prototype.getContext; HTMLCanvasElement.prototype.getContext = function (t, ...r) {"
             " return /webgl/.test(t) ? null : g.call(this, t, ...r); };")


@pytest.fixture
def tactil(cad, settings):
    settings.TV_CAD_TACTIL = True
    return cad


class Tableta:
    """Un Chromium con el perfil de una tableta Android; junta errores, peticiones a /cad/ y los informes de arranque."""

    def __init__(self, playwright_, servidor, cookie, perfil, extra_js=""):
        pf = PERFILES[perfil]
        self.navegador = playwright_.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        self.contexto = self.navegador.new_context(viewport={"width": pf["w"], "height": pf["h"]}, device_scale_factor=pf["dpr"],
                                                   is_mobile=pf["movil"], has_touch=True, user_agent=pf["ua"], locale="es-MX")
        self.contexto.add_cookies([cookie])
        js = (f"try {{ Object.defineProperty(Navigator.prototype, 'deviceMemory', {{ get: () => {pf['mem']} }});"
              " Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', { get: () => 8 }); } catch (e) {}")
        if pf.get("sin_webgpu"):
            js += f" try {{ {SIN_WEBGPU} }} catch (e) {{}}"
        if extra_js:
            js += f" try {{ {extra_js} }} catch (e) {{}}"
        self.contexto.add_init_script(js)
        self.servidor = servidor
        self.errores, self.peticiones, self.informes = [], [], []
        self.pagina = p = self.contexto.new_page()
        p.set_default_timeout(LARGO)
        p.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        p.on("request", self._peticion)

    def _peticion(self, r):
        ruta = r.url.replace(self.servidor.url, "")
        if ruta.startswith("/cad/") and not ruta.startswith("/cad/editor/"):
            self.peticiones.append((r.method, ruta))
        if ruta.endswith("/arranque/") and r.method == "POST":
            self.informes.append(json.loads(r.post_data or "{}"))

    def ir(self, ruta):
        self.pagina.goto(self.servidor.url + ruta)
        return self.pagina

    def cerrar(self):
        self.navegador.close()


@pytest.fixture
def tableta(playwright_, servidor, sesion_de):
    abiertas = []

    def abrir(usuario, perfil="vertical", extra_js=""):
        t = Tableta(playwright_, servidor, sesion_de(usuario), perfil, extra_js)
        abiertas.append(t)
        return t
    yield abrir
    for t in abiertas:
        t.cerrar()


def plano_de(usuario, mb, nombre):
    """Un DXF de unos `mb` MB en UTM (líneas cortas en una retícula)."""
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    m = d.modelspace()
    x0, y0 = 395000.0, 3175000.0
    for i in range(int(mb * 1048576 / 150)):
        m.add_line((x0 + (i % 400), y0 + i // 400), (x0 + (i % 400) + 0.8, y0 + i // 400 + 0.5))
    s = io.StringIO()
    d.write(s)
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(s.getvalue().encode())
    return servicio.crear(usuario, t, nombre, "", {"tipo": "prueba"})


def esperar_tactil_listo(p):
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)


@pytest.mark.parametrize(("perfil", "mb"), [(p, 1) for p in PERFILES] + [("vertical", 5), ("horizontal", 20)])
def test_la_tableta_abre_el_plano_en_modo_tactil(tactil, tableta, crear_usuario, roles, perfil, mb):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_de(ana, mb, f"Plano de {mb} MB")
    t = tableta(ana, perfil)
    p = t.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado", 180000)
    esperar_tactil_listo(p)
    assert p.evaluate("document.documentElement.classList.contains('cad-tactil')")
    assert p.locator("#cad-atasco").is_hidden()
    # Como el teléfono: el editor se abre de la red y no se guarda solo en la tableta (eso es a pedido, «Sin conexión»).
    assert ("GET", "/cad/paquete-del-editor/") not in t.peticiones, t.peticiones
    assert ("POST", f"/cad/{p0.pk}/bloqueo/") in t.peticiones and ("GET", f"/cad/{p0.pk}/archivo/") in t.peticiones
    assert p.evaluate("TVCad.pedir({op: 'query', detail: 'summary', limit: 5}).then(r => r.entities.length)") > 0
    assert not t.informes and not t.errores, (t.informes, t.errores)


def test_un_editor_que_no_arranca_lo_dice_con_que_hacer(tactil, tableta, crear_usuario, roles):
    """Sin WebGL 2 el editor carga su WebAssembly pero nunca arranca (antes: «Abriendo…» para siempre). El aviso llega a tiempo, se
    puede tocar sobre la capa táctil, y el servidor recibe en qué etapa y en qué equipo; «Abrir en modo completo» reabre en ese modo."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_de(ana, 1, "Plano")
    t = tableta(ana, "vertical", SIN_WEBGL)
    p = t.ir(f"/cad/{p0.pk}/")
    expect(p.locator("#cad-atasco")).to_be_visible(timeout=150000)
    expect(p.locator("#cad-atasco-titulo")).to_have_text("Este navegador no deja dibujar el plano.")
    expect(p.locator("#cad-carga")).to_be_hidden()
    expect(p.locator("#cad-atasco-modo")).to_have_text("Abrir en modo completo")
    assert ("POST", f"/cad/{p0.pk}/bloqueo/") not in t.peticiones          # nada se toma mientras el editor no arranca
    esperar(p, "true", 1)
    assert len(t.informes) == 1, t.informes
    i = t.informes[0]
    assert i["etapa"] == "editor" and i["desde"] == "red" and i["modo"] == "t" and i["gl"] == "sin WebGL2"
    assert i["eq"]["w"] == 800 and i["eq"]["tactil"] == 1 and i["eq"]["fino"] == 0 and [x[0] for x in i["pasos"]][-3:] == ["marco", "compilar", "editor"]
    p.locator("#cad-atasco-modo").click()                                   # se toca: está encima de la capa táctil
    p.wait_for_load_state()
    expect(p.locator("#cad-atasco")).to_be_visible(timeout=150000)
    assert not p.evaluate("document.documentElement.classList.contains('cad-tactil')")
    expect(p.locator("#cad-atasco-modo")).to_have_text("Abrir en modo táctil")
    expect(p.locator("#cad-atasco-reintentar")).to_be_visible()
    assert len(t.informes) == 2 and t.informes[1]["modo"] == "c"
    assert not t.errores, t.errores


def test_lo_guardado_que_no_arranca_se_borra_y_abre_de_la_red(tactil, playwright_, servidor, sesion_de, crear_usuario, roles):
    """En la computadora el editor se guarda solo (Cache Storage, servido por el service worker). Si esa copia no arranca, la página la
    borra y lo abre de la red sin que la persona haga nada; el servidor sabe que pasó («recuperado»)."""
    from .test_cad_navegador import Navegador

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_de(ana, 1, "Plano")
    n = Navegador(playwright_, "chromium", servidor, sesion_de(ana))
    try:
        p = n.ir(f"/cad/{p0.pk}/")
        esperar_estado(p, "guardado")
        version = cad_editor.version()
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)", LARGO)
        # La copia guardada se daña de forma que su tamaño cuadra (el service worker la sirve) pero el editor no carga.
        # La página del editor guardada nombra un módulo que no existe: el service worker la sirve (su tamaño cuadra) y el editor no carga.
        p.evaluate("""async (v) => {
          const c = await caches.open('itt-cad-editor-' + v);
          for (const r of await c.keys()) {
            if (!new URL(r.url).pathname.endsWith('/index.html')) continue;
            const viejo = await c.match(r);
            const texto = (await viejo.text()).replace(/(name="ocs-js" content=")[^"]+/, '$1falta-' + Date.now() + '.js');
            const cuerpo = new TextEncoder().encode(texto);
            const h = new Headers(viejo.headers); h.set('X-TV-Bytes', String(cuerpo.byteLength));
            await c.put(r, new Response(cuerpo, {status: 200, headers: h}));
          }
        }""", version)
        informes = []
        p.on("request", lambda r: informes.append(json.loads(r.post_data)) if r.url.endswith("/arranque/") and r.method == "POST" else None)
        p.reload()
        esperar_estado(p, "guardado")
        assert p.locator("#cad-atasco").is_hidden()
        assert len(informes) == 1 and informes[0]["recuperado"] and informes[0]["desde"] == "equipo" and informes[0]["etapa"] == "compilar", informes
        # La versión dañada se borró (la siguiente apertura la vuelve a guardar).
        assert not p.evaluate("TVCadEditor.estado().then(e => e.alDia)")
    finally:
        n.cerrar()


def test_los_avisos_se_pueden_tocar_en_modo_tactil(tactil, tableta, crear_usuario, roles):
    """«Planos grandes» en una tableta de 4 GB en modo táctil: el aviso y «Intentar abrirlo aquí» quedan encima de la capa táctil."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Plano maestro", "", "dwg")
    Plano.objects.filter(pk=p0.pk).update(bytes=48 * 1048576)
    t = tableta(ana, "vertical")
    p = t.ir(f"/cad/{p0.pk}/")
    expect(p.locator("#cad-grande")).to_be_visible()
    assert p.evaluate("document.documentElement.classList.contains('cad-tactil')")
    p.locator("#cad-grande-intentar").click()
    expect(p.locator("#cad-grande")).to_be_hidden()
    esperar_estado(p, "guardado", 180000)
    esperar_tactil_listo(p)
    assert not t.errores, t.errores
