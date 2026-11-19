"""«Carpeta del proyecto en esta computadora» en el navegador con el editor real: vincular una carpeta, que aparezcan sus imágenes y
referencias, que NADA de la carpeta se suba, el permiso tras recargar, insertar una imagen georreferenciada (archivo de mundo y GeoTIFF)
en su lugar exacto (se comprueba el píxel), el juego de fachadas «tipo PointCab» y, con TV_CAD_ORTO_GRANDE=<archivo>, la ortofoto de
varios GB (fluidez y memoria; ver docs/cad.md, «Carpeta del proyecto»).

Chromium: la carpeta se da como la daría `showDirectoryPicker` (un `FileSystemDirectoryHandle` real del almacenamiento privado del
navegador, con los archivos generados) y también por el camino de Safari y Firefox (<input webkitdirectory>). WebKit: el camino de
Safari. Todo con rásteres sintéticos de cad_local_rasters.py. Corre bajo el candado de la máquina, un navegador a la vez.
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio

from . import cad_local_rasters as r
from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, esperar_estado, navegar  # noqa: F401
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")


class Red:
    """Cada petición de la página y de sus marcos y trabajadores: método, dirección y bytes del cuerpo."""

    def __init__(self, pagina):
        self.peticiones = []
        pagina.on("console", lambda m: print("consola:", m.type, m.text[:300]) if m.type in ("error", "warning") else None)
        pagina.on("request", self._anotar)
        pagina.context.on("request", self._anotar)

    def _anotar(self, req):
        try:
            cuerpo = req.post_data_buffer or b""
        except Exception:
            cuerpo = b""
        self.peticiones.append((req.method, req.url, cuerpo))

    def desde(self, n):
        return self.peticiones[n:]


def sin_subir(red, desde, muestras: list[bytes], nombres: list[str]):
    """Ninguna petición lleva bytes de los archivos de la carpeta ni nombres que el plano no traía."""
    for metodo, url, cuerpo in red.desde(desde):
        for m in muestras:
            assert m not in cuerpo, f"{metodo} {url} llevó bytes de un archivo de la carpeta"
        for n in nombres:
            assert n.lower() not in url.lower() and n.encode() not in cuerpo, f"{metodo} {url} llevó el nombre {n}"
    subidos = sum(len(c) for _, _, c in red.desde(desde))
    return subidos


def carpeta_opfs(p, carpeta: Path, nombre="Proyecto_Demo"):
    """Copia `carpeta` al almacenamiento privado del navegador y deja `showDirectoryPicker` devolviendo ese directorio (un
    FileSystemDirectoryHandle real: se guarda en IndexedDB y tiene permisos como uno elegido por la persona)."""
    archivos = [f for f in sorted(carpeta.rglob("*")) if f.is_file()]
    p.evaluate("""async (n) => {
      const raiz = await navigator.storage.getDirectory();
      try { await raiz.removeEntry(n, { recursive: true }); } catch (_) {}
      await raiz.getDirectoryHandle(n, { create: true });
    }""", nombre)
    lote = []
    def mandar():
        p.evaluate("""async ([n, lote]) => {
          const raiz = await (await navigator.storage.getDirectory()).getDirectoryHandle(n);
          for (const [ruta, b64] of lote) {
            const partes = ruta.split("/"); let d = raiz;
            for (const x of partes.slice(0, -1)) d = await d.getDirectoryHandle(x, { create: true });
            const w = await (await d.getFileHandle(partes.at(-1), { create: true })).createWritable();
            await w.write(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))); await w.close();
          }
        }""", [nombre, lote])
    for f in archivos:
        lote.append([str(f.relative_to(carpeta)), base64.b64encode(f.read_bytes()).decode()])
        if sum(len(x[1]) for x in lote) > 8_000_000:
            mandar()
            lote = []
    if lote:
        mandar()
    p.evaluate("""(n) => { window.showDirectoryPicker = async () => (await navigator.storage.getDirectory()).getDirectoryHandle(n); }""", nombre)


def plano_de(usuario, ruta: Path, nombre="Fachadas"):
    return servicio.crear_desde_ruta(usuario, ruta, nombre, "Demo")


def color_en(p, x, y):
    """Color de la pantalla en el punto del mundo (x, y) del editor."""
    px = p.evaluate("async ([x, y]) => (await TVCad.vista()).aPagina(x, y)", [x, y])
    img = p.screenshot(clip={"x": px[0] - 1, "y": px[1] - 1, "width": 3, "height": 3})
    from PIL import Image
    import io
    return Image.open(io.BytesIO(img)).convert("RGB").getpixel((1, 1))


def insertado(p):
    """Tras «Insertar»: el plano queda con cambios (o se dice por qué no)."""
    try:
        esperar_estado(p, "sucio")
    except AssertionError:
        diag = p.evaluate("""() => ({ mensaje: document.getElementById('cad-local-mensaje').textContent,
          hoja: document.getElementById('cad-local-insertar').open, detalle: document.getElementById('cad-local-ins-detalle').textContent,
          registro: TVCad.registro().slice(-8), estado: document.getElementById('cad-estado').dataset.estado })""")
        raise AssertionError(f"no se insertó: {json.dumps(diag)[:3000]}")


def listo(p):
    """El plano abierto y sin el aviso de gráficos del editor (Chromium de pruebas dibuja por software y el editor lo avisa)."""
    esperar_estado(p, "guardado")
    p.evaluate("TVCad.pedir({op: 'action', name: 'close_modal'}).catch(() => null)")


def ver(p, cx, cy, alto):
    p.evaluate("async ([cx, cy, h]) => { await TVCad.pedir({op: 'view_set', center: [cx, cy], height: h}); }", [cx, cy, alto])


def zoom_extension(p):
    """ZOOM E cuando el área de dibujo ya no cambia de tamaño. Un ZOOM E encuadra el área de ese momento (como AutoCAD, que al cambiar el
    tamaño conserva centro y altura): si después se abre un panel o se acomoda la página, lo de los lados queda fuera de la vista. Deja en
    la página el área con la que se encuadró, para el diagnóstico."""
    anterior, quieta = None, 0
    for _ in range(40):
        rect = p.evaluate("TVCad.pedir({op: 'state'}).then(r => r.viewport_rect)")
        quieta = quieta + 1 if rect == anterior else 0
        if quieta >= 3:
            break
        anterior = rect
        p.wait_for_timeout(250)
    p.evaluate("TVCad.pedir({op: 'run', cmd: 'ZOOM E'})")
    p.evaluate("r => { window.__rect_zoom = r; }", anterior)


def esperar_imagenes(p, n, timeout=LARGO):
    try:
        esperar(p, f"window.TVCadLocal && TVCadLocal.estado().suministradas.filter(s => s.raiz).length >= {n}", timeout)
    except AssertionError:
        if CAPTURAS:
            p.screenshot(path=f"{CAPTURAS}/fallo-imagenes.png")
        diag = p.evaluate("""async () => ({ local: window.TVCadLocal && (({resueltas, ...x}) => ({...x, resueltas: resueltas.length, malas: resueltas.filter(r => r.estado !== 'ruta').slice(0, 3)}))(TVCadLocal.estado()),
          vistas: await TVCad.pedir({op: 'image_tiles'}).catch(e => String(e)),
          refs: await TVCad.pedir({op: 'references'}).then(r => ({images: r.images.slice(0, 3), supplied_bytes: r.supplied_bytes, xrefs: r.xrefs})).catch(e => String(e)),
          estado: await TVCad.pedir({op: 'state'}).then(r => ({view: r.view, viewport_rect: r.viewport_rect, command: r.command, modal: r.modal})).catch(e => String(e)),
          extension: await TVCad.pedir({op: 'extents'}).catch(e => String(e)),
          rect_al_zoom: window.__rect_zoom || null,
          vistas_imagen: await TVCad.pedir({op: 'image_views'}).then(r => ({n: r.views.length, tam: r.viewport_size})).catch(e => String(e)) })""")
        loc = diag.get("local") or {}
        sum_ = loc.get("suministradas") or []
        vistas = diag.get("vistas") if isinstance(diag.get("vistas"), dict) else {}
        resumen = {"suministradas": len(sum_), "con_raiz": sum(1 for s in sum_ if s.get("raiz")), "faltan": sum(s.get("faltan") or 0 for s in sum_),
                   "documento": loc.get("documento"), "leyendo": loc.get("leyendo"), "porEntregar": loc.get("porEntregar"), "editorBytes": loc.get("editorBytes"),
                   "piramides": loc.get("piramides"), "planos": loc.get("planos"), "resueltas": loc.get("resueltas"), "malas": loc.get("malas"),
                   "vistas": {k: (v if not isinstance(v, list) else f"{len(v)} elementos") for k, v in vistas.items()} if vistas else diag.get("vistas"),
                   "refs": diag.get("refs") if not isinstance(diag.get("refs"), dict) else {"supplied_bytes": diag["refs"].get("supplied_bytes"), "xrefs": diag["refs"].get("xrefs")},
                   "estado": diag.get("estado"), "rect_al_zoom": diag.get("rect_al_zoom"), "extension": diag.get("extension"), "vistas_imagen": diag.get("vistas_imagen")}
        raise AssertionError(f"imágenes sin dar ({n} esperadas): {json.dumps(resumen, ensure_ascii=False)[:4000]}")


@pytest.fixture
def ana(crear_usuario, roles):
    return _persona(crear_usuario, roles, "ana", "Ana López", "cad")


@pytest.fixture
def fachadas(tmp_path):
    carpeta = tmp_path / "Proyecto_Demo"
    dxf = r.pointcab(carpeta, imagenes=int(os.environ.get("TV_CAD_POINTCAB_IMAGENES", "240")), lado=512)
    return carpeta, dxf


def test_carpeta_vinculada_imagenes_y_xref_sin_subir_nada(cad, navegar, ana, fachadas):
    carpeta, dxf = fachadas
    plano = plano_de(ana, dxf)
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{plano.pk}/")
    red = Red(p)
    listo(p)
    # Sin carpeta: el marco con el nombre, un aviso y una sola acción.
    expect(p.locator("#cad-aviso-local")).to_be_visible(timeout=LARGO)
    expect(p.locator("#cad-aviso-local-vincular")).to_have_text("Vincular la carpeta del proyecto")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-local-sin-carpeta-chromium-1280.png")
    carpeta_opfs(p, carpeta)
    inicio = len(red.peticiones)
    t0 = time.monotonic()
    p.locator("#cad-aviso-local-vincular").click()
    esperar(p, "TVCadLocal.estado().resueltas.length >= 240", LARGO)
    estado = p.evaluate("TVCadLocal.estado()")
    malas = [x for x in estado["resueltas"] if x["estado"] not in ("ruta",)]
    assert not malas, malas[:5]
    # Todo el juego a la vista: cada imagen con su vista reducida, memoria acotada.
    zoom_extension(p)
    esperar_imagenes(p, 240)
    t_todas = time.monotonic() - t0
    refs = p.evaluate("TVCad.pedir({op: 'references'})")
    en_editor = refs["supplied_bytes"]
    assert en_editor < 240 * 512 * 512 * 4 / 2, en_editor
    # La referencia externa (marco y ejes) se abrió desde la carpeta.
    assert [x["status"] for x in refs["xrefs"]] == ["loaded"], refs["xrefs"]
    expect(p.locator("#cad-aviso-local")).to_be_hidden()
    # Nada se subió: ni bytes de las imágenes ni sus nombres; el plano sigue sin cambios.
    muestras = [f.read_bytes()[200:264] for f in sorted(carpeta.rglob("*.jpg"))[:20]] + [(carpeta / "Referencias" / "Marco_y_ejes.dxf").read_bytes()[100:180]]
    subidos = sin_subir(red, inicio, muestras, ["Proyecto_Demo", "Marco_y_ejes.dxf"])
    assert subidos < 64 * 1024, subidos
    assert p.locator("#cad-estado").get_attribute("data-estado") == "guardado"
    if CAPTURAS:
        p.wait_for_timeout(800)
        p.screenshot(path=f"{CAPTURAS}/cad-local-fachadas-chromium-1280.png")
        p.locator("#cad-boton-referencias").click()
        p.wait_for_timeout(700)
        p.screenshot(path=f"{CAPTURAS}/cad-local-panel-chromium-1280.png")
        p.locator("#cad-referencias [data-tv-cerrar]").first.click()
    # Acercarse a una fachada: el nivel de la pantalla, solo de lo visible.
    ver(p, 389000.0 + 2.0, 3169000.0 + 1.5, 2.0)
    esperar(p, "TVCadLocal.estado().suministradas.some(s => s.completa && s.nivel === 0 && s.teselas > 1)", LARGO)
    # Recargar: el navegador recuerda la carpeta; con el permiso vigente se vuelve a usar sola (Chromium de pruebas lo mantiene).
    p.reload()
    listo(p)
    esperar(p, "window.TVCadLocal && TVCadLocal.estado().raices.length === 1", LARGO)
    zoom_extension(p)
    esperar_imagenes(p, 240)
    # Desvincular: vuelve el marco con el nombre y el aviso.
    p.locator("#cad-boton-referencias").click()
    p.locator("#cad-local-desvincular").click()
    esperar(p, "TVCadLocal.estado().raices.length === 0")
    expect(p.locator("#cad-aviso-local")).to_be_visible()
    refs = p.evaluate("TVCad.pedir({op: 'references'})")
    assert all(i["supplied"] is None for i in refs["images"])
    p.reload()
    listo(p)
    expect(p.locator("#cad-aviso-local-vincular")).to_be_visible(timeout=LARGO)
    (Path(CAPTURAS) / "cad-local-medidas.json").write_text(json.dumps({"fachadas_imagenes": 240, "todas_visibles_s": round(t_todas, 1),
        "bytes_en_el_editor": en_editor})) if CAPTURAS else None
    assert not _errores_propios(n, "chromium"), n.errores


def test_permiso_tras_reiniciar_se_pide_con_un_toque(cad, navegar, ana, fachadas):
    """Tras reiniciar el navegador el permiso de la carpeta vuelve a «preguntar»: la página lo dice y un solo toque lo pide."""
    carpeta, dxf = fachadas
    plano = plano_de(ana, dxf)
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{plano.pk}/")
    listo(p)
    carpeta_opfs(p, carpeta)
    p.locator("#cad-aviso-local-vincular").click()
    zoom_extension(p)
    esperar_imagenes(p, 1)
    # Lo que hace Chrome tras reiniciar: el handle guardado sigue, el permiso pasa a «prompt» hasta que la persona lo concede.
    p.context.add_init_script("""
      if (self.FileSystemHandle) {
        FileSystemHandle.prototype.queryPermission = async function () { return self.__concedido ? "granted" : "prompt"; };
        FileSystemHandle.prototype.requestPermission = async function () { self.__concedido = true; return "granted"; };
      }""")
    p.reload()
    listo(p)
    expect(p.locator("#cad-aviso-local-permitir")).to_be_visible(timeout=LARGO)
    expect(p.locator("#cad-aviso-local-texto")).to_contain_text("pide permiso")
    assert p.evaluate("TVCadLocal.estado().raices.length") == 0
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-local-permiso-chromium-1280.png")
    p.locator("#cad-aviso-local-permitir").click()
    zoom_extension(p)
    esperar_imagenes(p, 1)
    expect(p.locator("#cad-aviso-local")).to_be_hidden()
    assert not _errores_propios(n, "chromium"), n.errores


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_insertar_imagen_georreferenciada_en_su_lugar(cad, navegar, ana, tmp_path, motor):
    from PIL import Image

    # 200 × 100 px, píxel de 0.5 m: mitad izquierda roja, derecha azul; esquina superior izquierda en (389 100, 3 169 050).
    carpeta = tmp_path / "Orto"
    carpeta.mkdir()
    im = Image.new("RGB", (200, 100), (230, 20, 20))
    im.paste((20, 40, 230), (100, 0, 200, 100))
    im.save(carpeta / "orto.png")
    r.mundo(carpeta / "orto.pgw", 0.5, 0.0, 0.0, -0.5, 389100.25, 3169049.75)
    plano = servicio.crear_vacio(ana, "Georreferencia", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    Red(p)
    listo(p)
    p.locator("#cad-menu summary").click()
    with p.expect_file_chooser() if motor == "webkit" else _sin_selector() as fc:
        if motor == "chromium":
            # Chromium: el selector de archivos del sistema se sustituye por los mismos archivos.
            p.evaluate("""() => { window.showOpenFilePicker = undefined; }""")
        p.locator("#cad-menu-insertar-imagen").click()
    if motor == "chromium":
        p.locator("#cad-local-imagen").set_input_files([str(carpeta / "orto.png"), str(carpeta / "orto.pgw")])
    else:
        fc.value.set_files([str(carpeta / "orto.png"), str(carpeta / "orto.pgw")])
    expect(p.locator("#cad-local-ins-detalle")).to_contain_text("archivo de mundo", timeout=LARGO)
    expect(p.locator("#cad-local-ins-detalle")).to_contain_text("X 389,100.00")
    p.locator("#cad-local-ins-ir").click()
    insertado(p)
    ent = p.evaluate("TVCad.pedir({op: 'references'})")["images"]
    assert len(ent) == 1 and ent[0]["path"] == "orto.png" and ent[0]["size"] == [200, 100], ent
    esperar_imagenes(p, 1)
    ver(p, 389150.0, 3169025.0, 70.0)
    p.wait_for_timeout(1200)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-local-georref-{motor}-antes.png")
    rojo, azul = color_en(p, 389110.0, 3169025.0), color_en(p, 389190.0, 3169025.0)
    assert rojo[0] > 150 and rojo[2] < 90, (rojo, ent)
    assert azul[2] > 150 and azul[0] < 90, (azul, ent)
    # Fuera de la imagen (a 2 m del borde izquierdo): fondo del editor, no rojo.
    afuera = color_en(p, 389098.0, 3169025.0)
    assert not (afuera[0] > 150 and afuera[2] < 90), afuera
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-local-georref-{motor}-1280.png")
    assert not _errores_propios(n, motor), n.errores


class _sin_selector:
    value = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_geotiff_en_mosaicos_por_partes(cad, navegar, ana, tmp_path):
    """Un GeoTIFF en mosaicos (Deflate) con vistas internas: se inserta con sus etiquetas y se lee por partes."""
    carpeta = tmp_path / "Orto"
    carpeta.mkdir()
    tif = r.tiff(carpeta / "orto_mosaico.tif", 4096, 3072, mosaico=256, compresion="deflate", vistas=3,
                 geo=r.geo_utm(389000.0, 3169000.0, 0.1))
    plano = servicio.crear_vacio(ana, "GeoTIFF", "", "dxf")
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{plano.pk}/")
    listo(p)
    p.evaluate("() => { window.showOpenFilePicker = undefined; }")
    p.locator("#cad-menu summary").click()
    p.locator("#cad-menu-insertar-imagen").click()
    p.locator("#cad-local-imagen").set_input_files([str(tif)])
    expect(p.locator("#cad-local-ins-detalle")).to_contain_text("GeoTIFF", timeout=LARGO)
    p.locator("#cad-local-ins-ir").click()
    insertado(p)
    assert len(p.evaluate("TVCad.pedir({op: 'references'})")["images"]) == 1
    esperar_imagenes(p, 1)
    # El patrón: rojo = columna mod 256. Columna 128 (x = 389 012.85) es rojo 128 ± JPEG del lector, no aplica: Deflate es exacto.
    ver(p, 389012.85, 3169000.0 - 10.05, 4.0)
    esperar(p, "TVCadLocal.estado().suministradas.some(s => s.completa && s.nivel === 0)", LARGO)
    p.wait_for_timeout(800)
    c = color_en(p, 389012.85, 3169000.0 - 10.05)
    assert abs(c[0] - 128) <= 12 and abs(c[1] - 100) <= 12, c
    assert not _errores_propios(n, "chromium"), n.errores


@pytest.mark.skipif(not os.environ.get("TV_CAD_ORTO_GRANDE"), reason="TV_CAD_ORTO_GRANDE=<BigTIFF de varios GB> para medir")
def test_ortofoto_enorme_fluida(cad, navegar, ana, tmp_path):
    """Medición (no en la corrida de siempre): un BigTIFF de varios GB desde la computadora; la vista general se arma una vez, el
    recorrido y el acercamiento piden solo lo visible. Escribe las medidas en ITT_CAPTURAS/cad-local-orto.json."""
    import subprocess
    import threading

    ruta = Path(os.environ["TV_CAD_ORTO_GRANDE"])
    # Memoria residente de los procesos del navegador de la prueba (renderizador, GPU, trabajadores), muestreada cada 0.5 s.
    pico = {"mb": 0.0}
    seguir = threading.Event()

    def muestrear():
        while not seguir.wait(0.5):
            salida = subprocess.run(["ps", "-axo", "rss=,command="], capture_output=True, text=True).stdout
            total = sum(int(l.split(None, 1)[0]) for l in salida.splitlines()
                        if "ms-playwright" in l and ("Chromium" in l or "chrome" in l.lower()))
            pico["mb"] = max(pico["mb"], total / 1024)
    hilo = threading.Thread(target=muestrear, daemon=True)
    hilo.start()
    plano = servicio.crear_vacio(ana, "Ortofoto enorme", "", "dxf")
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{plano.pk}/")
    red = Red(p)
    listo(p)
    p.evaluate("() => { window.showOpenFilePicker = undefined; }")
    p.locator("#cad-menu summary").click()
    p.locator("#cad-menu-insertar-imagen").click()
    t0 = time.monotonic()
    p.locator("#cad-local-imagen").set_input_files([str(ruta)])
    expect(p.locator("#cad-local-ins-detalle")).to_contain_text("GeoTIFF", timeout=LARGO)
    t_info = time.monotonic() - t0
    inicio = len(red.peticiones)
    p.locator("#cad-local-ins-ir").click()
    insertado(p)
    t1 = time.monotonic()
    esperar_imagenes(p, 1, 900000)
    t_primera = time.monotonic() - t1
    esperar(p, "TVCadLocal.estado().piramides.every(x => !x.corriendo)", 1800000)
    t_piramide = time.monotonic() - t1
    medidas = {"archivo_gb": round(ruta.stat().st_size / 1e9, 2), "info_s": round(t_info, 2), "primera_vista_s": round(t_primera, 1),
               "piramide_s": round(t_piramide, 1), "piramide": p.evaluate("TVCadLocal.estado().piramides")}
    # Recorrido: 60 pasos de vista (acercar, alejar, mover) midiendo cuadros con requestAnimationFrame dentro del editor.
    ext = p.evaluate("TVCad.pedir({op: 'extents'})")
    (x0, y0), (x1, y1) = ext["min"], ext["max"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    p.evaluate("""() => { const w = document.getElementById('cad-editor').contentWindow; window.__cuadros = []; let t = w.performance.now();
      const f = (ahora) => { window.__cuadros.push(ahora - t); t = ahora; w.requestAnimationFrame(f); }; w.requestAnimationFrame(f); }""")
    alturas = [y1 - y0, 400.0, 60.0, 8.0, 60.0, 400.0]
    for k in range(60):
        h = alturas[k % len(alturas)]
        ver(p, cx + (k % 7 - 3) * h * 0.2, cy + (k % 5 - 2) * h * 0.2, h)
        p.wait_for_timeout(120)
    p.wait_for_timeout(3000)
    cuadros = sorted(p.evaluate("window.__cuadros"))
    seguir.set()
    refs = p.evaluate("TVCad.pedir({op: 'references'})")
    medidas.update({"cuadros": len(cuadros), "cuadro_mediana_ms": round(cuadros[len(cuadros) // 2], 1),
                    "cuadro_p95_ms": round(cuadros[int(len(cuadros) * 0.95)], 1), "cuadro_max_ms": round(cuadros[-1], 1),
                    "bytes_en_el_editor": refs["supplied_bytes"], "memoria_pico_navegador_mb": round(pico["mb"]),
                    "subidos_bytes": sin_subir(red, inicio, [ruta.open("rb").read(4096)[1024:1088]], [ruta.name])})
    if CAPTURAS:
        (Path(CAPTURAS) / f"cad-local-orto-{ruta.stem}.json").write_text(json.dumps(medidas, indent=1))
        p.screenshot(path=f"{CAPTURAS}/cad-local-orto-{ruta.stem}-chromium-1280.png")
    print(json.dumps(medidas))
    assert refs["supplied_bytes"] < 96 * 1024 * 1024


def test_safari_y_firefox_eligen_la_carpeta_para_la_sesion(cad, navegar, ana, fachadas):
    """WebKit no tiene showDirectoryPicker: la carpeta se elige con <input webkitdirectory> y vale mientras la página esté abierta."""
    carpeta, dxf = fachadas
    plano = plano_de(ana, dxf)
    n = navegar(ana, "webkit")
    p = n.ir(f"/cad/{plano.pk}/")
    red = Red(p)
    listo(p)
    expect(p.locator("#cad-aviso-local")).to_be_visible(timeout=LARGO)
    inicio = len(red.peticiones)
    with p.expect_file_chooser() as fc:
        p.locator("#cad-aviso-local-vincular").click()
    fc.value.set_files(str(carpeta))
    esperar(p, "TVCadLocal.estado().resueltas.length >= 240", LARGO)
    zoom_extension(p)
    esperar_imagenes(p, 20)
    p.locator("#cad-boton-referencias").click()
    expect(p.locator("#cad-local-sesion")).to_be_visible()
    expect(p.locator("#cad-local-carpeta-texto")).to_contain_text("solo mientras esta página esté abierta")
    if CAPTURAS:
        p.wait_for_timeout(700)
        p.screenshot(path=f"{CAPTURAS}/cad-local-panel-webkit-1280.png")
    muestras = [f.read_bytes()[200:264] for f in sorted(carpeta.rglob("*.jpg"))[:20]]
    assert sin_subir(red, inicio, muestras, ["Proyecto_Demo"]) < 64 * 1024
    assert not _errores_propios(n, "webkit"), n.errores


# -- El aviso de la carpeta se puede ocultar (dueño, 19-11: «que el aviso de la referencia se pueda quitar en el teléfono») ----------

def _dxf_con_imagenes(destino: Path, rutas: list[str]) -> Path:
    """Un DXF con un IMAGE por ruta (40 × 30 m, uno junto al otro), como lo deja AutoCAD con rutas absolutas de Windows."""
    import ezdxf

    d = ezdxf.new("R2018", setup=False)
    d.header["$INSUNITS"] = 6
    m = d.modelspace()
    for i, ruta in enumerate(rutas):
        definicion = d.add_image_def(filename=ruta, size_in_pixel=(80, 60))
        m.add_image(definicion, insert=(389000.0 + 50 * i, 3169000.0), size_in_units=(40.0, 30.0), rotation=0)
    d.saveas(destino)
    return destino


def _faltan(p, n):
    esperar(p, f"window.TVCadLocal && TVCadLocal.estado().faltan === {n}", LARGO)


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_el_aviso_de_la_carpeta_se_oculta_y_vuelve_si_falta_otra(cad, navegar, ana, tmp_path, motor):
    p0 = plano_de(ana, _dxf_con_imagenes(tmp_path / "uno.dxf", ["C:\\Obra\\Fotos\\f001.png"]), "Con aviso")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{p0.pk}/")
    listo(p)
    aviso = p.locator("#cad-aviso-local")
    expect(aviso).to_be_visible(timeout=LARGO)
    # En la computadora: el aviso completo, con «Vincular la carpeta del proyecto» y la × para ocultarlo.
    expect(p.locator("#cad-aviso-local-texto")).to_have_text("Este plano usa un archivo (imágenes o dibujos) que están en una carpeta de tu computadora.")
    expect(p.locator("#cad-aviso-local-vincular")).to_be_visible()
    assert "cad-aviso--compacto" not in (aviso.get_attribute("class") or "")
    p.locator("#cad-aviso-local-cerrar").click()
    expect(aviso).to_be_hidden()
    # «Referencias» sigue ahí, con lo que falta: la carpeta se puede vincular después.
    expect(p.locator("#cad-local-boton-cuenta")).to_have_text("1")

    # Recargar: sigue oculto (este dispositivo, esta cuenta y este plano).
    p.reload()
    listo(p)
    _faltan(p, 1)
    p.wait_for_timeout(500)
    expect(aviso).to_be_hidden()
    expect(p.locator("#cad-boton-referencias")).to_be_visible()

    # Una versión nueva con otra imagen que falta: el aviso vuelve, con las dos.
    temporal = servicio.carpeta_temporal() / f"aviso-{p0.pk}.dxf"
    _dxf_con_imagenes(temporal, ["C:\\Obra\\Fotos\\f001.png", "C:\\Obra\\Fotos\\f002.png"])
    p0.refresh_from_db()
    servicio._nueva_version(p0, temporal, "dxf", ana, "otra imagen")
    p.reload()
    listo(p)
    _faltan(p, 2)
    expect(aviso).to_be_visible(timeout=LARGO)
    expect(p.locator("#cad-aviso-local-texto")).to_contain_text("2 archivos")
    assert not _errores_propios(n, motor), n.errores


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_en_el_telefono_el_aviso_es_una_linea_sin_vincular_y_se_cierra(cad, navegar, ana, tmp_path, settings, motor):
    from .test_cad_tactil_navegador import abrir as abrir_tactil

    settings.TV_CAD_TACTIL = True
    p0 = plano_de(ana, _dxf_con_imagenes(tmp_path / "uno.dxf", ["C:\\Obra\\Fotos\\f001.png"]), "En el teléfono")
    n, p = abrir_tactil(navegar, ana, motor, p0=p0)
    aviso = p.locator("#cad-aviso-local")
    expect(aviso).to_be_visible(timeout=LARGO)
    # Aquí no se pueden vincular carpetas: una línea que lo dice, sin «Vincular» ni «Permitir», con la ×.
    expect(p.locator("#cad-aviso-local-texto")).to_have_text("La imagen de este plano está en una computadora; aquí ves su marco.")
    expect(p.locator("#cad-aviso-local-vincular")).to_be_hidden()
    expect(p.locator("#cad-aviso-local-permitir")).to_be_hidden()
    assert "cad-aviso--compacto" in (aviso.get_attribute("class") or "")
    caja = aviso.bounding_box()
    assert caja["height"] <= 60, caja                                     # una franja corta, no la banda grande de antes
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-aviso-local-telefono-{motor}.png")
    p.locator("#cad-aviso-local-cerrar").click()
    expect(aviso).to_be_hidden()

    p.reload()
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    _faltan(p, 1)
    p.wait_for_timeout(500)
    expect(aviso).to_be_hidden()
    assert not _errores_propios(n, motor), n.errores
