"""CAD en el teléfono con datos móviles: cuántos bytes viajan de verdad, medidos en el cable (docs/cad.md, «Datos móviles»).

Como la app instalada en un teléfono (perfil que se guarda, 375 × 812, toque), con el CAD descargado y un plano de más de 1 MB con
presentaciones marcado «Disponible sin conexión». Entre el navegador y el servidor de pruebas va `contador_bytes.ContadorBytes`, un
proxy que cuenta los bytes en los dos sentidos por petición y comprime como Caddy en producción; Playwright (`request.sizes()`,
`response.from_service_worker`) lo cruza. Chromium y WebKit, un navegador a la vez, bajo el candado de la máquina.

Escenarios (docs/cad.md): (a) primera vez: bajar el CAD y llevar el plano; (b) abrirlo con conexión, mirarlo 2 minutos (mover,
acercar, cambiar de presentación, medir) y cerrarlo; (c) otra vez; (d) dibujar 5 objetos en 3 minutos y guardar; (e) una versión nueva
del editor fijado; (f) otra cuenta guarda una versión nueva; (g) modo avión; (h) un plano que NO está en el teléfono, como referencia.

Falla si (b), (c) o el reabrir de (e) y (f) pasan de 30 KB por apertura o de 2 KB por minuto abierto, o si (g) manda algo.
`ITT_DATOS_CAD=<carpeta>` deja ahí la tabla medida (JSON y Markdown).
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import signal
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import editor as cad_editor
from itt_herramientas.cad import servicio

from .contador_bytes import ContadorBytes, agrupar, resumen
from .test_cad_navegador import LARGO, _persona, cad  # noqa: F401 (fixtures)
from .test_cad_tactil_navegador import GESTOS, INSTALADA, X0, Y0, _listo_sin_conexion, _llevar_al_telefono
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
DATOS = os.environ.get("ITT_DATOS_CAD", "")
USO_S = int(os.environ.get("ITT_DATOS_USO_S", "120"))            # (b) y (c): 2 minutos mirando
DIBUJO_S = int(os.environ.get("ITT_DATOS_DIBUJO_S", "180"))      # (d): 5 objetos en 3 minutos
OBJETIVO_ABRIR = 30 * 1024
OBJETIVO_MINUTO = 2 * 1024
KB = 1024


@pytest.fixture(autouse=True)
def limite_por_prueba():
    """Estas pruebas miden en tiempo real (minutos con el plano abierto): su propio tope, más largo que el de conftest."""
    def vencida(signum, frame):
        raise TimeoutError("La prueba de datos móviles pasó de 25 minutos")
    previo = signal.signal(signal.SIGALRM, vencida)
    signal.alarm(1500)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previo)


@pytest.fixture
def produccion(cad, settings):
    """Modo táctil encendido y los tiempos de producción (borrador, latido): las pruebas del editor los acortan, aquí no."""
    settings.TV_CAD_TACTIL = True
    for nombre in ("HERRAMIENTAS_CAD_AUTOGUARDADO_S", "HERRAMIENTAS_CAD_LATIDO_S"):
        if hasattr(settings, nombre):
            delattr(settings, nombre)
    return cad


@pytest.fixture
def proxy(servidor):
    u = urlparse(servidor.url)
    c = ContadorBytes(u.hostname, u.port).arrancar()
    yield c
    c.detener()


def plano_grande(usuario, nombre="Plano de datos"):
    """Más de 1 MB: una cuadrícula de lotes en UTM con textos, en dos capas, y dos presentaciones con ventanas («Planta 1», «Hoja A1»)."""
    import ezdxf

    d = ezdxf.new("R2018", setup=True)
    d.layers.add("LOTES", color=3)
    d.layers.add("TEXTOS", color=2)
    m = d.modelspace()
    for i in range(60):
        for j in range(60):
            x, y = X0 + i * 12, Y0 + j * 12
            m.add_lwpolyline([(x, y), (x + 10, y), (x + 10, y + 10), (x, y + 10)], close=True, dxfattribs={"layer": "LOTES"})
            m.add_text(f"L{i}-{j}", dxfattribs={"height": 1.2, "insert": (x + 1, y + 1), "layer": "TEXTOS"})
    for nombre_hoja in ("Planta 1", "Hoja A1"):
        hoja = d.layouts.new(nombre_hoja)
        hoja.add_viewport(center=(150, 100), size=(280, 180), view_center_point=(X0 + 360, Y0 + 360), view_height=760)
    s = io.StringIO()
    d.write(s)
    datos = s.getvalue().encode()
    assert len(datos) > 1024 * 1024, len(datos)
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(datos)
    return servicio.crear(usuario, t, nombre, "Fracc. Datos", {"tipo": "prueba"})


#: Los avisos breves (tvAviso) que salieron en la página, para comprobarlos aunque ya se hayan ido.
AVISOS = """(() => { window.__avisos = []; new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes)
  if (n.nodeType === 1 && n.closest && n.closest('#tv-avisos')) window.__avisos.push(n.textContent || ''); })
  .observe(document, {childList: true, subtree: true}); })()"""


class Telefono:
    """La app instalada (perfil que se guarda) que habla con el servidor solo por el proxy, con lo que Playwright ve de cada petición."""

    def __init__(self, playwright_, motor, proxy, cookie, carpeta):
        tipo = getattr(playwright_, motor)
        args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if motor == "chromium" else []
        self.contexto = tipo.launch_persistent_context(str(carpeta), headless=True, args=args, viewport={"width": 375, "height": 812},
                                                       is_mobile=motor == "chromium", has_touch=True, locale="es-MX")
        self.contexto.add_cookies([{**cookie, "url": proxy.url}])
        self.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
        self.contexto.add_init_script(INSTALADA)
        self.contexto.add_init_script(AVISOS)
        self.proxy = proxy
        self.errores, self.vistas, self.fallidas = [], [], []
        self.pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        self.pagina.set_default_timeout(LARGO)
        self.pagina.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        self.contexto.on("requestfinished", lambda r: self.vistas.append(r))
        self.contexto.on("requestfailed", lambda r: self.fallidas.append(r))

    def ir(self, ruta):
        self.pagina.goto(self.proxy.url + ruta)
        return self.pagina

    def cerrar_plano(self):
        """Salir del plano como al cerrar la app o volver atrás: la página se va (pagehide suelta el bloqueo)."""
        self.pagina.goto("about:blank")
        self.proxy.esperar_quieto()

    def playwright_bytes(self, desde: int) -> dict:
        """Lo que Playwright cree que salió a la red (sin lo que respondió el service worker)."""
        red = sw = 0
        for r in self.vistas[desde:]:
            try:
                resp = r.response()
                if resp is not None and resp.from_service_worker:
                    sw += 1
                    continue
                s = r.sizes()
                red += s["requestHeadersSize"] + s["requestBodySize"] + s["responseHeadersSize"] + s["responseBodySize"]
            except Exception:  # noqa: BLE001 (la página ya no está)
                continue
        return {"red": red, "del_service_worker": sw}


class Medicion:
    def __init__(self, telefono: Telefono, motor: str, archivo: str):
        self.t, self.motor, self.archivo = telefono, motor, archivo
        self.filas: dict[str, dict] = {}

    def tramo(self, nombre, accion):
        self.t.proxy.esperar_quieto()
        self.t.proxy.marcar(nombre)
        n0 = len(self.t.vistas)
        t0 = time.monotonic()
        accion()
        self.t.proxy.esperar_quieto()
        pet = self.t.proxy.tramo(nombre)
        fila = {**resumen(pet), "segundos": round(time.monotonic() - t0, 1), "playwright": self.t.playwright_bytes(n0), "detalle": agrupar(pet)}
        if len(pet) <= 40:                                   # cada petición, para ver qué fue (las de bajar el editor no)
            fila["peticiones_una_a_una"] = [[x.metodo, x.ruta.split("?")[0], x.estado, x.pedido, x.respuesta] for x in pet]
        # Las grandes (más de 256 KB en el cable), con su tamaño sin comprimir y si se comprimieron.
        fila["grandes"] = [[x.metodo, x.ruta.split("?")[0], x.estado, x.respuesta, x.cuerpo, x.gzip] for x in pet if x.respuesta > 262144]
        self.filas[nombre] = fila
        self.escribir()
        return fila

    def escribir(self):
        if not DATOS:
            return
        carpeta = Path(DATOS)
        carpeta.mkdir(parents=True, exist_ok=True)
        (carpeta / f"{self.archivo}-{self.motor}.json").write_text(json.dumps(self.filas, indent=1, ensure_ascii=False, default=list), encoding="utf-8")
        lineas = [f"| Escenario ({self.motor}) | Peticiones | Bytes en el cable | Playwright (red) | Segundos |", "| --- | ---: | ---: | ---: | ---: |"]
        for k, f in self.filas.items():
            lineas.append(f"| {k} | {f['peticiones']} | {f['total']:,} | {f['playwright']['red']:,} | {f['segundos']} |")
        lineas.append("")
        for k, f in self.filas.items():
            lineas.append(f"### {k}")
            for g in f["detalle"][:14]:
                lineas.append(f"- {g['metodo']} {g['ruta']} ×{g['n']} {g['estados']}: {g['pedido'] + g['respuesta']:,} B")
        (carpeta / f"{self.archivo}-{self.motor}.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")


def _comprobar(p, expresion, timeout=30000):
    """Como `esperar`, pero sin detener la medición: dice si se cumplió (se exige al final, con la tabla ya escrita)."""
    try:
        esperar(p, expresion, timeout)
        return True
    except AssertionError:
        return False


def _listo(p):
    esperar(p, "window.TVCadTactil && TVCadTactil.listo() && window.TVCad && TVCad.estaListo()", LARGO)
    esperar(p, "document.getElementById('tt').dataset.estado !== 'cargando'", LARGO)
    p.evaluate(GESTOS)


def _presentaciones(p):
    r = p.evaluate("TVCad.pedir({op: 'layouts'})")
    nombres = [x if isinstance(x, str) else x.get("name") for x in r.get("layouts", [])]
    modelo = next((n for n in nombres if re.match(r"^model(o)?$", str(n), re.I)), nombres[0])
    hojas = [n for n in nombres if n != modelo]
    return modelo, hojas


def _usar(p, segundos):
    """Mirar el plano: mover, acercar, cambiar de presentación y medir, un gesto cada ~10 s."""
    modelo, hojas = _presentaciones(p)
    assert "Planta 1" in hojas, hojas
    t0, i = time.monotonic(), 0
    while time.monotonic() - t0 < segundos - 2:
        paso = i % 4
        if paso == 0:
            p.evaluate("__g.arrastrar(150, 300, 70, 40)")
        elif paso == 1:
            p.evaluate("__g.pellizcar(187, 350, 80, 150)")
        elif paso == 2:
            p.evaluate("(n) => TVCad.pedir({op: 'layout_set', name: n})", "Planta 1")
            p.wait_for_timeout(800)
            p.evaluate("__g.arrastrar(160, 320, -40, 30)")
            p.evaluate("(n) => TVCad.pedir({op: 'layout_set', name: n})", modelo)
        else:
            p.locator('.tt-pestanas [data-grupo="medir"]').click()
            p.locator("#tt-hoja").get_by_role("button", name="Distancia", exact=True).click()
            for x, y in ((120, 300), (240, 330)):
                p.evaluate("([x, y]) => __g.tocar(x, y)", [x, y])
                p.wait_for_timeout(250)
                p.locator("#tt-confirmar").click()
                p.wait_for_timeout(200)
            expect(p.locator("#tt-tarjeta")).to_contain_text("Distancia")
            p.keyboard.press("Escape")
            if p.locator("#tt-tarjeta-cerrar").is_visible():
                p.locator("#tt-tarjeta-cerrar").click()
        i += 1
        siguiente = t0 + i * 10
        p.wait_for_timeout(max(0, (siguiente - time.monotonic()) * 1000))
    restante = segundos - (time.monotonic() - t0)
    if restante > 0:
        p.wait_for_timeout(restante * 1000)
    rev = p.evaluate("TVCad.pedir({op: 'state'}).then(s => s.documents.find(d => !d.start).revision)")
    return rev


def _abrir_mirar_cerrar(med, t, nombre, ruta, segundos, al_abrir=None):
    """(b)/(c): abrir, mirar `segundos`, cerrar. Devuelve {abrir, por_minuto} (abrir incluye cerrar)."""
    p = t.pagina
    med.tramo(f"{nombre} · abrir", lambda: (t.ir(ruta), _listo(p), al_abrir and al_abrir()))
    med.filas[f"{nombre} · abrir"]["marca"] = p.locator("#tt-sub-texto").text_content()
    if segundos:
        med.tramo(f"{nombre} · {segundos // 60 if segundos >= 60 else segundos} {'min' if segundos >= 60 else 's'} mirando", lambda: _usar(p, segundos))
        assert p.locator("#tt").get_attribute("data-estado") != "sucio", "mirar nunca deja el plano «Sin guardar»"
    med.tramo(f"{nombre} · cerrar", t.cerrar_plano)
    abrir = med.filas[f"{nombre} · abrir"]["total"] + med.filas[f"{nombre} · cerrar"]["total"]
    minuto = 0
    if segundos:
        uso = med.filas[[k for k in med.filas if k.startswith(f"{nombre} · ") and "mirando" in k][-1]]
        # La búsqueda de una versión nueva del service worker es una por página abierta (el navegador la hace por su cuenta unos segundos
        # o minutos después de navegar): cuenta en la apertura, no por minuto.
        sw = sum(g["pedido"] + g["respuesta"] for g in uso["detalle"] if g["ruta"] == "/sw.js")
        abrir += sw
        minuto = (uso["total"] - sw) / (uso["segundos"] / 60)
    med.filas[f"{nombre} · resumen"] = {"peticiones": 0, "total": abrir, "pedido": 0, "respuesta": 0, "segundos": 0,
                                       "playwright": {"red": 0}, "detalle": [], "abrir": abrir, "por_minuto": round(minuto)}
    med.escribir()
    return {"abrir": abrir, "por_minuto": minuto}


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_datos_moviles_abrir_mirar_y_sin_conexion(produccion, proxy, playwright_, sesion_de, crear_usuario, roles, tmp_path, motor):
    """(a), (b), (c), (g) y (h)."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_grande(ana)
    p1 = plano_grande(ana, "Plano sin llevar")
    t = Telefono(playwright_, motor, proxy, sesion_de(ana), tmp_path / f"perfil-{motor}")
    med = Medicion(t, motor, "mirar")
    try:
        p = t.pagina

        def primera_vez():
            t.ir(f"/cad/{p0.pk}/")
            _listo(p)
            _llevar_al_telefono(t, p, p0)
            t.cerrar_plano()
        med.tramo("(a) primera vez: bajar el CAD y llevar el plano", primera_vez)
        b = _abrir_mirar_cerrar(med, t, "(b)", f"/cad/{p0.pk}/", USO_S)
        c = _abrir_mirar_cerrar(med, t, "(c)", f"/cad/{p0.pk}/", USO_S)

        def avion():
            consola = []
            p.on("console", lambda m: consola.append(f"{m.type}: {m.text[:160]}"))
            t.ir(f"/cad/{p0.pk}/")
            _listo_sin_conexion(p, consola)
            expect(p.locator("#tt-nombre")).to_have_text("Plano de datos")
            p.evaluate(GESTOS)
            p.evaluate("__g.arrastrar(150, 300, 70, 40)")
            p.evaluate("__g.pellizcar(187, 350, 80, 150)")
            p.evaluate("(n) => TVCad.pedir({op: 'layout_set', name: n})", "Planta 1")
            p.wait_for_timeout(3000)
            p.goto("about:blank")
        # Modo avión: el navegador sin red (set_offline) y la red cortada en el proxy. Se mide solo lo de sin conexión.
        n_fallidas = len(t.fallidas)
        t.contexto.set_offline(True)
        proxy.cortar = True
        try:
            g = med.tramo("(g) modo avión", avion)
        finally:
            proxy.cortar = False
            t.contexto.set_offline(False)
        intentos = sorted({urlparse(r.url).path for r in t.fallidas[n_fallidas:] if urlparse(r.url).hostname == "localhost"})
        cortadas = sorted({x.ruta for x in proxy.tramo("(g) modo avión") if x.cortada})
        med.filas["(g) modo avión"].update({"intentos_sin_red": intentos, "cortadas": cortadas,
                                            "llegaron": sum(x.respuesta for x in proxy.tramo("(g) modo avión"))})
        med.escribir()
        h = _abrir_mirar_cerrar(med, t, "(h) plano NO llevado", f"/cad/{p1.pk}/", 60)
        med.escribir()
        print(json.dumps({k: {x: v[x] for x in ("peticiones", "total")} for k, v in med.filas.items()}, ensure_ascii=False, indent=1))

        for nombre, r in (("(b)", b), ("(c)", c)):
            assert "Desde el teléfono" in med.filas[f"{nombre} · abrir"]["marca"], med.filas[f"{nombre} · abrir"]["marca"]
            assert r["abrir"] <= OBJETIVO_ABRIR, f"{nombre}: abrir costó {r['abrir'] / KB:.1f} KB (objetivo 30 KB): {med.filas[f'{nombre} · abrir']['detalle'][:6]}"
            assert r["por_minuto"] <= OBJETIVO_MINUTO, f"{nombre}: {r['por_minuto'] / KB:.2f} KB por minuto abierto (objetivo 2 KB)"
        # Sin conexión no llega nada: lo único que intenta salir es la búsqueda de una versión nueva del service worker, que hace el
        # navegador por su cuenta (sin red falla y no cuesta nada); la página y el plano no piden nada que no tengan en el teléfono.
        assert med.filas["(g) modo avión"]["llegaron"] == 0 and set(cortadas) <= {"/sw.js"}, (cortadas, g["detalle"])
        assert h["abrir"] > 1024 * 1024, "referencia: un plano que no está en el teléfono se baja al abrirlo"
        assert "Desde el teléfono" not in med.filas["(h) plano NO llevado · abrir"]["marca"]
        assert not t.errores, t.errores
    finally:
        t.contexto.close()


def _editor_nuevo(base: Path, version: str, destino: Path) -> tuple[str, int]:
    """Una versión «nueva» del editor fijado en `destino`: los mismos archivos (enlaces duros) salvo uno que cambia. Devuelve su nombre."""
    nueva = f"{version}-datos"
    origen, final = base / version, destino / nueva
    for ruta in origen.rglob("*"):
        rel = ruta.relative_to(origen)
        if ruta.is_dir():
            (final / rel).mkdir(parents=True, exist_ok=True)
            continue
        (final / rel).parent.mkdir(parents=True, exist_ok=True)
        os.link(ruta, final / rel)
    cambia = "editor.css"
    (final / cambia).unlink()
    texto = (origen / cambia).read_bytes() + b"\n/* versi\xc3\xb3n nueva de prueba */\n"
    (final / cambia).write_bytes(texto)
    if (final / f"{cambia}.gz").exists():
        (final / f"{cambia}.gz").unlink()
    import hashlib

    man = json.loads((origen / "MANIFIESTO.json").read_text(encoding="utf-8"))
    man["archivos"][cambia] = hashlib.sha256(texto).hexdigest()
    (final / "MANIFIESTO.json").unlink()
    (final / "MANIFIESTO.json").write_text(json.dumps(man), encoding="utf-8")
    return nueva, len(texto)


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_datos_moviles_dibujar_versiones_del_editor_y_de_otra_cuenta(produccion, proxy, playwright_, sesion_de, crear_usuario, roles, tmp_path,
                                                                     settings, monkeypatch, motor):
    """(d), (e) y (f), después de (a)."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    p0 = plano_grande(ana)
    t = Telefono(playwright_, motor, proxy, sesion_de(ana), tmp_path / f"perfil-{motor}")
    med = Medicion(t, motor, "editar")
    comprobado = {}
    try:
        p = t.pagina

        def primera_vez():
            t.ir(f"/cad/{p0.pk}/")
            _listo(p)
            _llevar_al_telefono(t, p, p0)
            t.cerrar_plano()
        med.tramo("(a) primera vez", primera_vez)

        # (d) Dibujar 5 objetos en 3 minutos y guardar.
        med.tramo("(d) · abrir", lambda: (t.ir(f"/cad/{p0.pk}/"), _listo(p)))

        def dibujar():
            t0 = time.monotonic()
            for k in range(5):
                p.evaluate(f"TVCad.ejecutar('CIRCLE {X0 + 20 + k * 30},{Y0 + 20} 4')")
                expect(p.locator("#tt")).to_have_attribute("data-estado", "sucio")
                if k == 0:                                   # los cambios quedan en el teléfono en segundos, sin red
                    comprobado["copia de los cambios en el teléfono"] = _comprobar(p, f"TVCadCampo.cambios('{p0.pk}').then(c => !!c && c.origen === 'pagina')")
                fin = t0 + (k + 1) * DIBUJO_S / 5
                p.wait_for_timeout(max(0, (fin - time.monotonic()) * 1000))
        med.tramo("(d) · 5 objetos en 3 min", dibujar)

        def guardar():
            p.locator("#tt-guardar").click()
            expect(p.locator("#tt")).to_have_attribute("data-estado", "guardado", timeout=LARGO)
        med.tramo("(d) · guardar", guardar)
        # Guardada la versión, la copia de los cambios sobra y la del teléfono ya es la versión 2 (con los bytes subidos).
        comprobado["al guardar se borra la copia de los cambios"] = _comprobar(p, f"TVCadCampo.cambios('{p0.pk}').then(c => !c)")
        comprobado["al guardar la copia es la versión nueva"] = _comprobar(p, f"TVCadCampo.plano('{p0.pk}').then(c => !!c && c.revision === 2)")
        med.tramo("(d) · cerrar", t.cerrar_plano)
        p0.refresh_from_db()
        assert p0.revision == 2 and b"CIRCLE" in servicio.ruta_version(servicio.version_vigente(p0)).read_bytes()
        borradores = [g for g in med.filas["(d) · 5 objetos en 3 min"]["detalle"] if g["ruta"] == "/cad/<id>/borrador/"]
        guardados = [g for g in med.filas["(d) · guardar"]["detalle"] if g["ruta"] == "/cad/<id>/guardar/"]
        med.filas["(d) · borradores"] = {"peticiones": sum(g["n"] for g in borradores), "total": sum(g["pedido"] for g in borradores), "pedido": 0,
                                         "respuesta": 0, "segundos": 0, "playwright": {"red": 0}, "detalle": borradores}

        # (e) Versión nueva del editor fijado: solo baja lo que cambió, una vez.
        raiz2 = tmp_path / "editor2"
        raiz2.mkdir()
        nueva, tam_cambio = _editor_nuevo(Path(settings.HERRAMIENTAS_CAD_EDITOR_DIR), cad_editor.version(), raiz2)
        settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(raiz2)
        monkeypatch.setattr(cad_editor, "version", lambda: nueva)
        e1 = med.tramo("(e) · abrir con el editor nuevo", lambda: (t.ir(f"/cad/{p0.pk}/"), _listo(p), p.wait_for_timeout(3000)))
        comprobado["el editor nuevo reemplaza al anterior"] = _comprobar(
            p, f"caches.keys().then(k => k.includes('itt-cad-editor-{nueva}') && k.filter(n => n.startsWith('itt-cad-editor-')).length === 1)", LARGO * 2)
        med.tramo("(e) · cerrar", t.cerrar_plano)
        e2 = _abrir_mirar_cerrar(med, t, "(e) reabrir", f"/cad/{p0.pk}/", 0)

        # (f) Otra cuenta guarda una versión nueva: el teléfono la baja una vez y después vuelve a costar casi nada.
        s = uuid.uuid4().hex[:20]
        servicio.bloqueo(p0.pk, beto, s, "tomar")
        tmp = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
        tmp.write_bytes(servicio.ruta_version(servicio.version_vigente(p0)).read_bytes().replace(b"L0-0", b"B0-0"))
        servicio.guardar(p0.pk, beto, s, 2, tmp)
        servicio.bloqueo(p0.pk, beto, s, "soltar")
        tam_plano = servicio.version_vigente(p0).bytes
        f1 = _abrir_mirar_cerrar(med, t, "(f) versión de otra cuenta", f"/cad/{p0.pk}/", 0,
                                 al_abrir=lambda: comprobado.__setitem__("aviso de la versión nueva", _comprobar(p, "window.__avisos.some(t => t.includes('Había una versión nueva (la 3)'))")))
        f2 = _abrir_mirar_cerrar(med, t, "(f) reabrir", f"/cad/{p0.pk}/", 0)
        print(json.dumps({k: {x: v[x] for x in ("peticiones", "total")} for k, v in med.filas.items()}, ensure_ascii=False, indent=1))

        med.filas["comprobado"] = {"peticiones": 0, "total": 0, "pedido": 0, "respuesta": 0, "segundos": 0, "playwright": {"red": 0}, "detalle": [],
                                   "resultado": comprobado}
        med.escribir()
        assert all(comprobado.values()), comprobado
        n_borradores = med.filas["(d) · borradores"]["peticiones"]
        assert n_borradores <= math.ceil(DIBUJO_S / 120) + 1, f"(d): {n_borradores} borradores en {DIBUJO_S} s"
        assert sum(g["n"] for g in guardados) == 1, guardados
        assert e1["total"] < tam_cambio + 200 * KB, f"(e): {e1['total'] / KB:.0f} KB con un solo archivo cambiado: {e1['detalle'][:6]}"
        assert e2["abrir"] <= OBJETIVO_ABRIR, f"(e) reabrir: {e2['abrir'] / KB:.1f} KB"
        assert tam_plano <= f1["abrir"] <= tam_plano + OBJETIVO_ABRIR, f"(f): {f1['abrir'] / KB:.0f} KB (el plano pesa {tam_plano / KB:.0f} KB)"
        assert f2["abrir"] <= OBJETIVO_ABRIR, f"(f) reabrir: {f2['abrir'] / KB:.1f} KB"
        assert not t.errores, t.errores
    finally:
        t.contexto.close()
