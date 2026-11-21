"""CAD con el compilador de shaders de una GPU Mali (docs/cad.md, «Tableta Mali: los shaders del editor y el arranque que se corta»).

La Galaxy Tab S9 FE del dueño (Mali-G68) no abría: su controlador rechaza en el GLSL de WebGL 2 los valores de tipo arreglo
(«S0032: no default precision defined for variable 'vec4[8]'»), wgpu lo trata como fatal y la página esperaba 47 s para decirlo.
Aquí no hay GPU Mali: el compilador de WebGL 2 de Chromium (SwiftShader) se envuelve para que rechace lo mismo que Mali, con su mensaje,
en todos los marcos. Comprueba que el editor fijado ya no lleva ningún arreglo en sus shaders (abre en cada perfil de tableta, sin un
solo shader rechazado), y que un fallo de verdad de la tarjeta de video corta el arranque en cuanto el editor lo muestra, con el aviso de
siempre y el motivo `gpu` en el informe, sin borrar el editor guardado. Corre bajo el candado de la máquina, un navegador a la vez.
"""
from __future__ import annotations

import json

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import editor as cad_editor

from .test_cad_navegador import LARGO, _persona, cad, esperar_estado  # noqa: F401 (fixtures)
from .test_cad_tableta_navegador import PERFILES, SIN_WEBGPU, UA_TABLETA, plano_de
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]

# La tableta del dueño según su informe del 20-11-2026: pantalla de 823 × 1238 a 1.75, 4 GB, 6 núcleos, puntero fino (no solo táctil):
# el editor completo, guardado en el equipo.
DUENO = dict(ua=UA_TABLETA, movil=True, toque=False, w=823, h=1238, dpr=1.75, mem=4, cpu=6)
TODOS = {**{k: {**v, "toque": True, "cpu": 8} for k, v in PERFILES.items()}, "dueno": DUENO, "dueno-sin-webgpu": {**DUENO, "sin_webgpu": True}}

# El compilador de Mali, como lo vio la tableta: un valor de tipo arreglo fuera de un bloque uniforme o de un struct (un constructor
# `vec4[8](…)`, una variable, un parámetro o un retorno de tipo arreglo) no compila. `MODO = "todo"` rechaza todo shader de fragmentos:
# una tarjeta de video que no deja dibujar nada.
MALI = r"""
(() => {
  const P = WebGL2RenderingContext.prototype, fuente = new WeakMap(), malos = new WeakMap();
  const TIPO = "(?:float|int|uint|bool|[biu]?vec[234]|mat[234](?:x[234])?)";
  const CONSTRUCTOR = new RegExp("\\b(" + TIPO + ")\\s*\\[\\s*(\\d*)\\s*\\]\\s*\\(");
  const DECLARADO = new RegExp("\\b(" + TIPO + ")\\s+\\w+\\s*\\[\\s*(\\d+)\\s*\\]");
  window.__mali = { compilados: 0, rechazados: [] };
  const fuera = (src) => src.replace(/\b(?:struct|uniform|buffer)\b[^{;]*\{[^}]*\}/g, "");
  const shaderSource = P.shaderSource, compileShader = P.compileShader, getShaderParameter = P.getShaderParameter, getShaderInfoLog = P.getShaderInfoLog;
  P.shaderSource = function (s, src) { fuente.set(s, String(src)); return shaderSource.call(this, s, src); };
  P.compileShader = function (s) {
    window.__mali.compilados += 1;
    const src = fuente.get(s) || "", limpio = fuera(src);
    const fragmentos = this.getShaderParameter(s, this.SHADER_TYPE) === this.FRAGMENT_SHADER;
    let m = CONSTRUCTOR.exec(limpio) || DECLARADO.exec(limpio);
    if (!m && window.__maliModo === "todo" && fragmentos) m = ["", "vec4", "8"];
    if (m) {
      const linea = src.slice(0, src.indexOf(m[0]) >= 0 ? src.indexOf(m[0]) : 0).split("\n").length;
      const log = `0:${linea}: S0032: no default precision defined for variable '${m[1]}[${m[2] || "8"}]'\n`;
      malos.set(s, log);
      window.__mali.rechazados.push({ linea, tipo: `${m[1]}[${m[2] || ""}]`, fragmentos, inicio: src.slice(0, 80) });
    }
    return compileShader.call(this, s);
  };
  P.getShaderParameter = function (s, p) {
    if (p === this.COMPILE_STATUS && malos.has(s)) return false;
    return getShaderParameter.call(this, s, p);
  };
  P.getShaderInfoLog = function (s) { return malos.has(s) ? malos.get(s) : getShaderInfoLog.call(this, s); };
})();
"""


class Tableta:
    """Chromium con el perfil de una tableta y el compilador de Mali en todos los marcos; junta errores e informes de arranque."""

    def __init__(self, playwright_, servidor, cookie, perfil, modo=""):
        pf = TODOS[perfil]
        self.navegador = playwright_.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        self.contexto = self.navegador.new_context(viewport={"width": pf["w"], "height": pf["h"]}, device_scale_factor=pf["dpr"],
                                                   is_mobile=pf["movil"], has_touch=pf["toque"], user_agent=pf["ua"], locale="es-MX")
        self.contexto.add_cookies([cookie])
        js = (f"try {{ Object.defineProperty(Navigator.prototype, 'deviceMemory', {{ get: () => {pf['mem']} }});"
              f" Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {{ get: () => {pf['cpu']} }}); }} catch (e) {{}}")
        if pf.get("sin_webgpu"):
            js += f" try {{ {SIN_WEBGPU} }} catch (e) {{}}"
        js += f" window.__maliModo = {json.dumps(modo)}; {MALI}"
        self.contexto.add_init_script(js)
        self.servidor = servidor
        self.errores, self.informes = [], []
        self.pagina = p = self.contexto.new_page()
        p.set_default_timeout(LARGO)
        p.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        p.on("request", self._peticion)

    def _peticion(self, r):
        if r.url.endswith("/arranque/") and r.method == "POST":
            self.informes.append(json.loads(r.post_data or "{}"))

    def ir(self, ruta):
        self.pagina.goto(self.servidor.url + ruta)
        return self.pagina

    def mali(self):
        """Lo que vio el compilador en el marco del editor."""
        for f in self.pagina.frames:
            try:
                d = f.evaluate("window.__mali || null")
            except Exception:  # noqa: BLE001 (un marco que se va)
                continue
            if d and f != self.pagina.main_frame:
                return d
        return None

    def cerrar(self):
        self.navegador.close()


@pytest.fixture
def tactil(cad, settings):
    settings.TV_CAD_TACTIL = True
    return cad


@pytest.fixture
def tableta(playwright_, servidor, sesion_de):
    abiertas = []

    def abrir(usuario, perfil="dueno", modo=""):
        t = Tableta(playwright_, servidor, sesion_de(usuario), perfil, modo)
        abiertas.append(t)
        return t
    yield abrir
    for t in abiertas:
        t.cerrar()


@pytest.mark.parametrize("perfil", list(TODOS))
def test_con_el_compilador_de_mali_el_plano_abre(tactil, tableta, crear_usuario, roles, perfil):
    """Ningún shader del editor fijado lleva un valor de tipo arreglo: con el compilador de Mali abre igual, sin uno solo rechazado."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_de(ana, 1, "Plano")
    t = tableta(ana, perfil)
    p = t.ir(f"/cad/{p0.pk}/")
    # El plano abierto o el aviso de que no arrancó (con un shader rechazado, al momento): lo que llegue primero.
    esperar(p, "(document.getElementById('cad-estado') || {dataset: {}}).dataset.estado === 'guardado'"
               " || !document.getElementById('cad-atasco').hidden", 180000)
    assert p.locator("#cad-atasco").is_hidden(), (t.mali(), t.informes)
    assert p.evaluate("document.documentElement.classList.contains('cad-tactil')") == TODOS[perfil]["toque"]
    # Dibuja: el plano llega al lienzo (los pipelines de la escena también compilaron).
    assert p.evaluate("TVCad.pedir({op: 'query', detail: 'summary', limit: 5}).then(r => r.entities.length)") > 0
    p.wait_for_timeout(1500)
    m = t.mali()
    assert m and m["compilados"] >= 20 and not m["rechazados"], m
    assert not t.informes and not t.errores, (t.informes, t.errores)


@pytest.mark.parametrize("perfil", ["vertical", "dueno"])
def test_un_fallo_de_la_tarjeta_de_video_corta_el_arranque_al_momento(tactil, tableta, crear_usuario, roles, perfil):
    """Una tarjeta de video que no compila ningún shader de fragmentos: el editor lo muestra en su aviso de errores y la página termina
    el arranque ahí mismo (antes, el plazo de la etapa: 45 s), con «El CAD no pudo arrancar en este equipo», «Reintentar», el otro modo
    y «Descargar el plano»; el informe lleva el motivo `gpu` y el error entero. Lo guardado en el equipo no se borra: no era la causa."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_de(ana, 1, "Plano")
    t = tableta(ana, perfil, "todo")
    p = t.ir(f"/cad/{p0.pk}/")
    expect(p.locator("#cad-atasco")).to_be_visible(timeout=180000)
    expect(p.locator("#cad-atasco-titulo")).to_have_text("El CAD no pudo arrancar en este equipo.")
    expect(p.locator("#cad-atasco-texto")).to_contain_text("tarjeta de video")
    expect(p.locator("#cad-atasco-reintentar")).to_be_visible()
    expect(p.locator("#cad-atasco-modo")).to_be_visible()
    expect(p.locator("#cad-carga")).to_be_hidden()
    esperar(p, "true", 1)
    assert len(t.informes) == 1, t.informes
    i = t.informes[0]
    assert i["motivo"] == "gpu" and i["etapa"] in ("compilar", "editor") and not i["recuperado"], i
    assert "Shader compilation failed" in i["aviso"] and "S0032" in i["aviso"], i["aviso"]
    # Cortado al aparecer el error, no al vencer el plazo de la etapa (45 s).
    assert i["ms"] - i["pasos"][-1][1] < 20000, i
    m = t.mali()
    assert m and m["rechazados"] and all(x["fragmentos"] for x in m["rechazados"]), m
    if not TODOS[perfil]["toque"]:
        assert i["desde"] == "equipo", i
        assert p.evaluate(f"TVCadEditor.estado().then(e => e.alDia && e.fijada === {json.dumps(cad_editor.version())})"), "lo guardado se borró"
    # El único error de JavaScript es el pánico del propio editor (wasm `unreachable`), que es justo lo que se espera aquí.
    assert all("unreachable" in e for e in t.errores), t.errores
