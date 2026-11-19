"""Ortofotos por mosaicos y niveles (como un SIG) en el navegador con el editor real: lo ya visto queda nítido al volver, al acercarse
se ve el nivel anterior ampliado (nunca la vista reducida entera) y luego el fino, sin costuras entre mosaicos a escala 1, 1.25 y 1.5
con la vista girada en coordenadas UTM, y nada de la carpeta se sube. Con TV_CAD_MEDIR=<TIFF> mide abrir, mover y acercar (lo usa
docs/cad.md, «Ortofotos enormes»).

La nitidez se mide en la pantalla, sobre el patrón de cad_local_rasters.patron_nitidez (gris = 28 + 100 · ajedrez de 1 px + 100 ·
ajedrez de 8 px): a 2 px de pantalla por píxel del archivo, `fino` (diferencia media entre píxeles a 2 px) es ~100 con el nivel completo
y ~0 con cualquier nivel reducido ampliado; `grueso` (desviación) es ~50 con el nivel ¼ ampliado y ~0 con la vista reducida entera.
Un navegador a la vez, bajo el candado de la máquina.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio

from . import cad_local_rasters as r
from .test_cad_local_navegador import Red, insertado, listo, sin_subir, ver
from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, navegar  # noqa: F401
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
PIXEL = 0.05                      # m por píxel de las ortofotos de prueba
X0, Y0 = 389000.0, 3169000.0      # esquina superior izquierda (UTM 14N)
NITIDO = 40.0                     # umbral de `fino`: ~100 con el nivel completo (WebKit suaviza algo los bordes: ~55), ~0 con uno reducido
REFINA_S = 3.0                    # lo que puede tardar el nivel fino tras acercarse (con el nivel anterior ya a la vista)


@pytest.fixture
def ana(crear_usuario, roles):
    return _persona(crear_usuario, roles, "ana", "Ana López", "cad")


def gris(p, x, y, lado=64):
    """Los píxeles de pantalla (gris) de un cuadro de `lado` px CSS centrado en el punto del mundo (x, y)."""
    from PIL import Image
    px = p.evaluate("async ([x, y]) => (await TVCad.vista()).aPagina(x, y)", [x, y])
    img = p.screenshot(clip={"x": px[0] - lado / 2, "y": px[1] - lado / 2, "width": lado, "height": lado})
    return np.asarray(Image.open(io.BytesIO(img)).convert("L"), dtype=np.float64)


def fino(a):
    return float(np.abs(a[:, :-2] - a[:, 2:]).mean())


def grueso(a):
    return float(a.std())


def altura_para(p, css_por_pixel):
    """La altura de vista (m) con la que un píxel del archivo mide `css_por_pixel` px CSS."""
    h = p.evaluate("TVCad.vista().then(v => v.h)")
    return h * PIXEL / css_por_pixel


def insertar_archivo(p, tif: Path):
    p.evaluate("() => { window.showOpenFilePicker = undefined; }")
    p.locator("#cad-menu summary").click()
    p.locator("#cad-menu-insertar-imagen").click()
    p.locator("#cad-local-imagen").set_input_files([str(tif)])
    expect(p.locator("#cad-local-ins-detalle")).to_contain_text("GeoTIFF", timeout=LARGO)
    p.locator("#cad-local-ins-ir").click()
    insertado(p)


def esperar_nitido(p, x, y, tope=LARGO / 1000):
    """Segundos hasta que el punto se ve nítido (o AssertionError con el diagnóstico)."""
    t0 = time.monotonic()
    ultimo = 0.0
    while time.monotonic() - t0 < tope:
        ultimo = fino(gris(p, x, y))
        if ultimo >= NITIDO:
            return time.monotonic() - t0
        p.wait_for_timeout(50)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/fallo-mosaicos.png")
    raise AssertionError(f"no se vio nítido en {tope} s (fino {ultimo:.1f}): {json.dumps(p.evaluate('TVCadLocal.estado()'))[:2500]}")


def tiempos(p, x, y, tope):
    """Segundos hasta que el punto muestra el ajedrez de 8 px (`grueso` ≥ 30: un nivel ¼ o más fino) y hasta que está nítido (`fino` ≥
    NITIDO: el nivel completo); None si no llega en `tope` s. Para medir, sin fallar."""
    t0 = time.monotonic()
    t_grueso = t_fino = None
    while time.monotonic() - t0 < tope:
        a = gris(p, x, y)
        t = round(time.monotonic() - t0, 2)
        if t_grueso is None and grueso(a) >= 30:
            t_grueso = t
        if fino(a) >= NITIDO:
            t_fino = t
            t_grueso = t_grueso if t_grueso is not None else t
            break
        p.wait_for_timeout(50)
    return t_grueso, t_fino


def lugar(col, fila, arriba=Y0):
    """El centro (mundo) del píxel (col, fila) del archivo, con su borde superior en `arriba`."""
    return X0 + (col + 0.5) * PIXEL, arriba - (fila + 0.5) * PIXEL


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_lo_visto_queda_nitido_y_al_acercar_se_ve_el_nivel_anterior(cad, navegar, ana, tmp_path, motor):
    carpeta = tmp_path / "Orto"
    carpeta.mkdir()
    lado = 8192
    tif = r.tiff(carpeta / "orto_nitidez.tif", lado, lado, mosaico=256, compresion="deflate", vistas=4, nitidez=True,
                 geo=r.geo_utm(X0, Y0, PIXEL))
    plano = servicio.crear_vacio(ana, "Mosaicos", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    red = Red(p)
    listo(p)
    inicio = len(red.peticiones)
    insertar_archivo(p, tif)
    esperar(p, "TVCadLocal.estado().suministradas.some(s => s.raiz)", LARGO)
    cerca = altura_para(p, 2.0)

    # A, luego B (otro terreno), y de vuelta en A: nítido al instante, sin volver a leer.
    a, b = lugar(2000, 2000), lugar(2900, 2000)
    ver(p, *a, cerca)
    esperar_nitido(p, *a)
    ver(p, *b, cerca)
    esperar_nitido(p, *b)
    ver(p, *a, cerca)
    p.wait_for_timeout(120)                     # un poco más que un cuadro
    nitidez_al_volver = fino(gris(p, *a))
    assert nitidez_al_volver >= NITIDO, nitidez_al_volver

    # Acercarse: desde el nivel ¼ completo en D, ocho veces más cerca. Al instante se ve ese nivel ampliado (el ajedrez de 8 px; la
    # vista reducida entera lo tendría gris parejo), nunca el fino a medias; luego el fino, dentro de REFINA_S.
    d = lugar(6000, 5000)
    ver(p, *d, altura_para(p, 0.25))
    esperar(p, "TVCadLocal.estado().suministradas.some(s => s.completa && s.nivel === 2)", LARGO)
    p.wait_for_timeout(200)
    ver(p, *d, cerca)
    p.wait_for_timeout(60)
    antes = gris(p, *d)
    assert grueso(antes) >= 30, f"al acercar se vio la vista reducida entera (grueso {grueso(antes):.1f})"
    refina = esperar_nitido(p, *d)
    assert refina <= REFINA_S, refina
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-mosaicos-nitido-{motor}-1280.png")

    # Nada se subió: ni bytes del archivo ni su nombre.
    muestras = [tif.read_bytes()[4096 * k + 1024: 4096 * k + 1088] for k in (1, 50, 400)]
    assert sin_subir(red, inicio, muestras, [tif.name]) < 64 * 1024
    assert not _errores_propios(n, motor), n.errores


@pytest.mark.parametrize("motor,escala", [("chromium", 1), ("chromium", 1.25), ("chromium", 1.5), ("webkit", 1.5)])
def test_sin_costuras_entre_mosaicos(cad, navegar, ana, tmp_path, motor, escala):
    """Una ortofoto de un solo color en mosaicos, en UTM, vista girada 30° a escala de pantalla fraccionaria: ningún píxel del área de
    dibujo sobre la imagen sale de ese color (una costura dejaría ver el fondo o una línea más clara u oscura)."""
    carpeta = tmp_path / "Orto"
    carpeta.mkdir()
    tif = carpeta / "orto_lisa.tif"
    e = r.EscritorTiff(tif)
    color = np.array([200, 120, 40], np.uint8)
    lisa = lambda x0, y0, w, h: np.broadcast_to(color, (h, w, 3)).copy()  # noqa: E731
    e.nivel(4096, 4096, lisa, mosaico=256, compresion="deflate", geo=r.geo_utm(X0, Y0, PIXEL))
    for k in (1, 2, 3):
        e.nivel(4096 >> k, 4096 >> k, lisa, mosaico=256, compresion="deflate", reducida=True)
    e.cerrar()
    plano = servicio.crear_vacio(ana, "Costuras", "", "dxf")
    # El editor agranda su interfaz con la escala: una ventana mayor deja el área de dibujo de un tamaño útil.
    n = navegar(ana, motor, escala=escala, ancho=int(1280 * escala), alto=int(860 * escala))
    p = n.ir(f"/cad/{plano.pk}/")
    listo(p)
    for orden in ("GRIDMODE 0", "UCSICON OFF", "NAVVCUBE OFF"):
        p.evaluate("(c) => TVCad.ejecutar(c).catch(() => null)", orden)
    insertar_archivo(p, tif)
    esperar(p, "TVCadLocal.estado().suministradas.some(s => s.raiz)", LARGO)
    centro = lugar(2048, 2048)
    p.evaluate("TVCad.ejecutar('DVIEW TW 30').catch(() => null)")
    # Varios bordes de mosaico a la vista (uno cada 512 px del archivo), en el nivel completo y en el ½.
    for css in (0.8, 0.4):
        ver(p, *centro, altura_para(p, css))
        esperar(p, "TVCadLocal.estado().suministradas.some(s => s.completa)", LARGO)
        p.wait_for_timeout(400)
        from PIL import Image
        # El área de dibujo sobre la imagen: las filas y columnas del marco del editor que son casi todas del color (la vista está
        # girada 30°, así que una costura nunca ocupa una fila ni una columna entera), menos 12 px de margen por los controles.
        img = p.locator("#cad-editor").screenshot()
        a = np.asarray(Image.open(io.BytesIO(img)).convert("RGB"), dtype=np.int16)
        cerca = np.abs(a - color.astype(np.int16)).max(axis=2) <= 8

        def tramo(fraccion):
            idx = np.flatnonzero(fraccion > 0.6)
            if not idx.size:
                return 0, 0
            cortes = np.flatnonzero(np.diff(idx) > 1)
            inicios = np.r_[idx[0], idx[cortes + 1]]
            finales = np.r_[idx[cortes], idx[-1]]
            k = int(np.argmax(finales - inicios))
            return int(inicios[k]) + 12, int(finales[k]) - 12
        y0, y1 = tramo(cerca.mean(axis=1))
        x0, x1 = tramo(cerca[y0:y1].mean(axis=0) if y1 > y0 else cerca.mean(axis=0))
        if not (y1 - y0 > 100 and x1 - x0 > 200) and CAPTURAS:
            Image.open(io.BytesIO(img)).save(f"{CAPTURAS}/fallo-costuras-area-{motor}-{escala}-{css}.png")
        assert y1 - y0 > 100 and x1 - x0 > 200, (y0, y1, x0, x1)
        a = a[y0:y1, x0:x1]
        malos = np.abs(a - color.astype(np.int16)).max(axis=2) > 8
        if malos.any() and CAPTURAS:
            Image.fromarray(a.astype(np.uint8)).save(f"{CAPTURAS}/fallo-costuras-{motor}-{escala}-{css}.png")
        assert not malos.any(), f"{int(malos.sum())} píxeles fuera del color a escala {escala}, {css} px por píxel"
    assert not _errores_propios(n, motor), n.errores


@pytest.mark.skipif(not os.environ.get("TV_CAD_MEDIR"), reason="TV_CAD_MEDIR=<GeoTIFF con el patrón de nitidez> para medir")
def test_medir_ortofoto(cad, navegar, ana, tmp_path):
    """Medición (no en la corrida de siempre), igual para cualquier versión de la página: solo mira la pantalla. Tiempo hasta nítido
    al abrir, tras mover a terreno nuevo, al volver y tras un paso de acercamiento; cuadros al mover y acercar sin parar; memoria pico
    del navegador; bytes subidos. Escribe ITT_CAPTURAS/cad-mosaicos-medidas-<archivo>-<TV_CAD_MEDIR_ETIQUETA>.json."""
    ruta = Path(os.environ["TV_CAD_MEDIR"])
    etiqueta = os.environ.get("TV_CAD_MEDIR_ETIQUETA", "")
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
    lado = int(ruta.stem.rsplit("_", 1)[1].split("x")[0])
    arriba = Y0 + lado * PIXEL               # cad_local_rasters.grande: el borde superior
    sitio = lambda c, f: lugar(c, f, arriba)  # noqa: E731
    plano = servicio.crear_vacio(ana, "Medir", "", "dxf")
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{plano.pk}/")
    red = Red(p)
    listo(p)
    inicio = len(red.peticiones)
    t0 = time.monotonic()
    insertar_archivo(p, ruta)
    print("insertada", round(time.monotonic() - t0, 1), flush=True)
    # La página muestra entera la imagen recién insertada (si cae fuera de la vista): se espera a que el editor tenga algo de ella y a
    # que ese encuadre termine, para que no pise la vista que se mide.
    esperar(p, "TVCad.pedir({op: 'references'}).then(r => r.images.some(i => i.supplied))", 900000)
    p.wait_for_timeout(1500)
    cerca = altura_para(p, 2.0)
    m = {"archivo": ruta.name, "gb": round(ruta.stat().st_size / 1e9, 2), "version": etiqueta}
    a = sitio(lado // 3, lado // 3)
    ver(p, *a, cerca)
    base = round(time.monotonic() - t0, 2)
    g, f = tiempos(p, *a, 600)
    m["abrir_s"] = {"nivel_cuarto": g and round(base + g, 2), "nitido": f and round(base + f, 2)}
    b = sitio(lado // 3 + 900, lado // 3)
    ver(p, *b, cerca)
    m["mover_s"] = dict(zip(("nivel_cuarto", "nitido"), tiempos(p, *b, 120)))
    ver(p, *a, cerca)
    m["volver_s"] = dict(zip(("nivel_cuarto", "nitido"), tiempos(p, *a, 120)))
    d = sitio(2 * lado // 3, lado // 2)
    ver(p, *d, altura_para(p, 0.5))
    p.wait_for_timeout(4000)
    ver(p, *d, cerca)
    m["acercar_s"] = dict(zip(("nivel_cuarto", "nitido"), tiempos(p, *d, 120)))
    print("tiempos", m, flush=True)
    # Cuadros: 80 pasos de mover y acercar sin esperar a nada, medidos con requestAnimationFrame dentro del editor.
    p.evaluate("""() => { const w = document.getElementById('cad-editor').contentWindow; window.__cuadros = []; let t = w.performance.now();
      const f = (ahora) => { window.__cuadros.push(ahora - t); t = ahora; if (!window.__fin) w.requestAnimationFrame(f); }; w.requestAnimationFrame(f); }""")
    alturas = [cerca, cerca * 2, cerca * 4, cerca * 8, cerca * 4, cerca * 2]
    for k in range(80):
        h = alturas[k % len(alturas)]
        cx, cy = sitio(lado // 2 + (k % 9 - 4) * 300, lado // 2 + (k % 7 - 3) * 300)
        ver(p, cx, cy, h)
        p.wait_for_timeout(60)
    p.evaluate("window.__fin = true")
    cuadros = sorted(p.evaluate("window.__cuadros"))
    seguir.set()
    m.update({"cuadros": len(cuadros), "cuadro_mediana_ms": round(cuadros[len(cuadros) // 2], 1),
              "cuadro_p95_ms": round(cuadros[int(len(cuadros) * 0.95)], 1), "cuadro_max_ms": round(cuadros[-1], 1),
              "memoria_pico_navegador_mb": round(pico["mb"]),
              "piramide_local_s": p.evaluate("TVCadLocal.estado().piramides.map(x => x.segundos)"),
              "editor_bytes": p.evaluate("TVCad.pedir({op: 'references'}).then(r => r.supplied_bytes)"),
              "subidos_bytes": sin_subir(red, inicio, [ruta.open("rb").read(1 << 20)[524288:524352]], [ruta.name])})
    print(json.dumps(m))
    if CAPTURAS:
        (Path(CAPTURAS) / f"cad-mosaicos-medidas-{ruta.stem}-{etiqueta or 'actual'}.json").write_text(json.dumps(m, indent=1))
