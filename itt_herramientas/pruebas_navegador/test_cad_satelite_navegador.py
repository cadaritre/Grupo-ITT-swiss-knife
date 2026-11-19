"""«Imagen satelital desde una polilínea» con el editor real y un proveedor de teselas FALSO en otro origen (nunca el de verdad).

El proveedor falso habla como el MapServer de ArcGIS (`?f=json`, `/tile/z/y/x`, `/tilemap/z/y/x/w/h`) y pinta en sus teselas marcas rojas
en la latitud y longitud exactas de puntos UTM conocidos (pyproj, GRS80). Se comprueba:
- la estimación (píxeles, teselas, nivel) contra lo que sale;
- el lugar: la marca cae en su píxel del JPEG según el archivo de mundo (centroide a menos de medio píxel) y en su lugar del plano en la
  pantalla del editor; el IMAGE con su esquina y sus vectores; el recorte a la polilínea (fuera no se ve) y un solo paso de deshacer;
- dónde queda: en la carpeta del proyecto (Chromium: JPEG, .jgw, .prj y .txt en «Satelital») o descargado (WebKit), y la ruta del IMAGE;
- que nada pasa por nuestro servidor (registro de peticiones: ningún cuerpo lleva bytes de teselas ni de la imagen) y que la CSP deja ir
  solo al proveedor (otro origen se bloquea);
- los topes y «Detener» (no queda nada: ni archivo ni IMAGE).
Corre bajo el candado de la máquina, un navegador a la vez.
"""
from __future__ import annotations

import io
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from PIL import Image
from pyproj import Transformer

from itt_herramientas.cad import servicio

from .test_cad_local_navegador import Red, carpeta_opfs, color_en, listo, ver
from . import cinta
from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, navegar  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
ZONA = 14
CX, CY = 484_300.0, 2_148_350.0                          # zona 14 norte, ≈ 19.4° N (Valle de México)
MARCAS = {"p1": (CX - 60.0, CY - 40.0), "p2": (CX + 80.0, CY + 50.0), "fuera": (CX + 130.0, CY - 110.0)}   # «fuera»: en la caja, fuera del polígono
BASE = "/arcgis/rest/services/World_Imagery/MapServer"
FONDO = (50, 140, 60)
ROJO = (255, 0, 0)
A_GEO = Transformer.from_crs(f"+proj=utm +zone={ZONA} +ellps=GRS80", "+proj=longlat +ellps=GRS80", always_xy=True)


def merc(lat, lon, z):
    m = 256 * 2 ** z
    return (lon + 180) / 360 * m, (0.5 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)) * m


class Proveedor:
    """Teselas PNG de 256 px: fondo de un color por nivel y una marca roja de 9 × 9 px centrada en cada punto (en su lugar de Mercator)."""

    def __init__(self, nivel_max=19, demora=0.0):
        self.nivel_max, self.demora = nivel_max, demora
        self.peticiones, self.cache = [], {}
        geo = {k: A_GEO.transform(x, y) for k, (x, y) in MARCAS.items()}
        self.marcas = {k: (lat, lon) for k, (lon, lat) in geo.items()}
        prov = self

        class Manejador(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlsplit(self.path)
                prov.peticiones.append({"ruta": u.path, "q": parse_qs(u.query), "referer": self.headers.get("Referer"), "origen": self.headers.get("Origin")})
                if prov.demora:
                    time.sleep(prov.demora)
                ruta = u.path[len(BASE):] if u.path.startswith(BASE) else None
                if ruta is None:
                    return self.responder(404, b"{}", "application/json")
                if "token" not in parse_qs(u.query):
                    return self.responder(200, b'{"error":{"code":499,"message":"Token Required."}}', "text/plain")
                if ruta in ("", "/"):
                    return self.responder(200, json.dumps({"copyrightText": "Proveedor de prueba, Imágenes Falsas S.A."}).encode(), "application/json")
                partes = ruta.strip("/").split("/")
                if partes[0] == "tilemap":
                    z, y, x, w, h = map(int, partes[1:6])
                    datos = [1 if z <= prov.nivel_max else 0] * (w * h)
                    return self.responder(200, json.dumps({"adjusted": False, "location": {"left": x, "top": y, "width": w, "height": h}, "data": datos}).encode(), "application/json")
                if partes[0] == "tile":
                    z, y, x = map(int, partes[1:4])
                    return self.responder(200, prov.tesela(z, x, y), "image/png")
                return self.responder(404, b"{}", "application/json")

            def responder(self, estado, cuerpo, tipo):
                self.send_response(estado)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    self.wfile.write(cuerpo)
                except (BrokenPipeError, ConnectionResetError):
                    pass                                          # «Detener» corta las descargas en curso

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
        self.hilo = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.hilo.start()
        self.origen = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.servicio = self.origen + BASE

    def centro_utm(self, clave):
        """Donde quedó de verdad la marca en el nivel del proveedor: el centro de su cuadro de 9 px (píxel entero de Mercator), en UTM."""
        lat, lon = self.marcas[clave]
        z = self.nivel_max
        gx, gy = merc(lat, lon, z)
        cx, cy = math.floor(gx) + 0.5, math.floor(gy) + 0.5
        m = 256 * 2 ** z
        la = math.degrees(2 * math.atan(math.exp((0.5 - cy / m) * 2 * math.pi)) - math.pi / 2)
        lo = cx / m * 360 - 180
        return A_GEO.transform(lo, la, direction="INVERSE")

    def tesela(self, z, x, y):
        k = (z, x, y)
        if k not in self.cache:
            im = Image.new("RGB", (256, 256), FONDO if z == self.nivel_max else (50, 60, 140))
            px = im.load()
            for lat, lon in self.marcas.values():
                gx, gy = merc(lat, lon, z)
                lx, ly = gx - x * 256, gy - y * 256
                for j in range(math.floor(ly) - 4, math.floor(ly) + 5):
                    for i in range(math.floor(lx) - 4, math.floor(lx) + 5):
                        if 0 <= i < 256 and 0 <= j < 256:
                            px[i, j] = ROJO
            b = io.BytesIO()
            im.save(b, "PNG")
            self.cache[k] = b.getvalue()
        return self.cache[k]

    def teselas(self):
        return [r for r in self.peticiones if r["ruta"].startswith(BASE + "/tile/")]

    def detener(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def proveedor(settings):
    prov = Proveedor()
    settings.HERRAMIENTAS_CAD_SATELITE_CLAVE = "clave-de-prueba"
    settings.HERRAMIENTAS_CAD_SATELITE_SERVICIO = prov.servicio
    yield prov
    prov.detener()


@pytest.fixture
def otro_origen():
    """Un servidor que NO es el proveedor: la CSP de la página debe impedir hablarle."""
    prov = Proveedor()
    yield prov
    prov.detener()


def pentagono():
    return [(CX + 150 * math.cos(math.radians(90 + 72 * k)), CY + 150 * math.sin(math.radians(90 + 72 * k))) for k in range(5)]


def plano_con_poligono(usuario, tmp):
    import ezdxf

    d = ezdxf.new("R2018", setup=False)
    d.header["$INSUNITS"] = 6
    d.modelspace().add_lwpolyline(pentagono(), close=True, dxfattribs={"layer": "PREDIO"})
    salida = tmp / "predio.dxf"
    d.saveas(salida)
    return servicio.crear_desde_ruta(usuario, salida, "Predio satelital", "Demo")


def pedir(p, q, ms=120000):
    return p.evaluate("([q, t]) => TVCad.pedir(q, {tiempoMs: t})", [q, ms])


def preparar(p):
    """Avisos a la vista de la prueba; la elección y los puntos sin el ratón (user_select/getpoint como en la compilación oficial: lo
    seleccionado y «Listo»; los puntos, de una lista)."""
    p.evaluate("""() => {
      window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); };
      window.__puntos = [];
      const m = document.getElementById('cad-editor').contentWindow.ocsControl; const req = m.request.bind(m);
      m.request = (q, o) => q.op === 'user_select' ? Promise.resolve({ok: false, code: 'gui_required', error: 'gui'})
        : q.op === 'getpoint' ? Promise.resolve({ok: true, status: 'completed', result: {point: window.__puntos.shift()}}) : req(q, o);
    }""")


def abrir_hoja(p):
    esperar(p, cinta.CIVIL_LISTO + " && !!window.TVCivil", LARGO)           # Civil listo: en la cinta del editor o en su barra
    p.locator("#cad-menu > summary").click()
    p.locator("#cad-menu-imagen-satelite").click()
    esperar(p, "document.getElementById('civil-hoja-satelite').open", LARGO)


def elegir_poligono(p):
    h = pedir(p, {"op": "query", "layer": "PREDIO", "detail": "summary", "limit": 5})["entities"][0]["handle"]
    p.locator("#civil-sat-polilinea").click()
    esperar(p, "!document.getElementById('civil-pick').hidden && !document.getElementById('civil-pick-listo').hidden", LARGO)
    pedir(p, {"op": "select", "handles": [h]})
    p.locator("#civil-pick-listo").click()
    esperar(p, "document.getElementById('civil-sat-area').classList.contains('civil-elegido')", LARGO)


def esperar_plan(p):
    esperar(p, "!!(window.TVCadSatelite && TVCadSatelite.estado().plan) && !document.getElementById('civil-sat-ir').disabled", LARGO)
    return p.evaluate("TVCadSatelite.estado().plan")


def esperar_aviso(p, texto, timeout=LARGO * 2):
    try:
        esperar(p, f"window.__avisos.some(a => a[0].includes({texto!r}))", timeout)
    except AssertionError:
        raise AssertionError(f"No llegó «{texto}». Avisos: {p.evaluate('window.__avisos')} · estado: {p.locator('#civil-sat-estado').inner_text()}") from None


def leer_opfs(p, carpeta, nombre):
    import base64

    b64 = p.evaluate("""async ([c, n]) => {
      let d = await navigator.storage.getDirectory();
      for (const x of c.split('/')) d = await d.getDirectoryHandle(x);
      const f = await (await d.getFileHandle(n)).getFile(); const b = new Uint8Array(await f.arrayBuffer());
      let s = ''; for (let i = 0; i < b.length; i += 32768) s += String.fromCharCode(...b.subarray(i, i + 32768)); return btoa(s);
    }""", [carpeta, nombre])
    return base64.b64decode(b64)


def lista_opfs(p, carpeta):
    return p.evaluate("""async (c) => { let d = await navigator.storage.getDirectory();
      try { for (const x of c.split('/')) d = await d.getDirectoryHandle(x); } catch (_) { return []; }
      const n = []; for await (const [k] of d.entries()) n.push(k); return n.sort(); }""", carpeta)


def centroide_rojo(im, col, fila, radio=12):
    """Centroide de la marca por la luminancia (el JPEG guarda la luminancia a resolución completa y el color a la mitad: un centroide
    por el rojo se corre hasta medio píxel según caiga en los bloques de 2 × 2). Pesa cuánto más oscura que el fondo es cada píxel."""
    px = im.load()
    fondo = 0.299 * FONDO[0] + 0.587 * FONDO[1] + 0.114 * FONDO[2]
    s = sx = sy = 0.0
    n = 0
    for j in range(max(0, fila - radio), min(im.height, fila + radio + 1)):
        for i in range(max(0, col - radio), min(im.width, col + radio + 1)):
            r, g, b = px[i, j][:3]
            w = max(0.0, fondo - 6 - (0.299 * r + 0.587 * g + 0.114 * b))
            s += w; sx += w * (i + 0.5); sy += w * (j + 0.5)
            n += r > 180 and g < 90 and b < 90
    return (sx / s, sy / s, n) if s and n else None


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_imagen_satelital_desde_la_polilinea(cad, navegar, crear_usuario, roles, proveedor, otro_origen, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = plano_con_poligono(ana, tmp_path)
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    red = Red(p)
    listo(p)
    if motor == "chromium":                       # la carpeta del proyecto, como la daría showDirectoryPicker
        (tmp_path / "Proyecto").mkdir()
        (tmp_path / "Proyecto" / "leeme.txt").write_text("carpeta del proyecto")
        carpeta_opfs(p, tmp_path / "Proyecto", "Proyecto")
        p.evaluate("TVCadLocal.vincular()")
        esperar(p, "TVCadLocal.estado().raices.includes('Proyecto')", LARGO)
    preparar(p)
    abrir_hoja(p)
    assert "Imagen de Esri World Imagery" in p.locator("#civil-sat-fuente").inner_text()
    donde = p.locator("#civil-sat-donde").inner_text()
    assert ("carpeta del proyecto: Satelital" in donde) == (motor == "chromium"), donde
    elegir_poligono(p)
    assert "Polilínea de 5 vértices" in p.locator("#civil-sat-area").inner_text()
    # Máxima disponible: el proveedor tiene hasta el nivel 19 (≈ 28 cm a 19.4° N).
    plan = esperar_plan(p)
    assert plan["z"] == 19 and plan["metros"] == 0.28 and plan["motivo"] is None, plan
    texto = p.locator("#civil-sat-plan").inner_text()
    assert f"{plan['ancho']:,} × {plan['alto']:,} px".replace(",", ",") in texto.replace(" ", ",").replace(",", ",") or "px" in texto
    assert "teselas (nivel 19)" in texto and "descarga ≈" in texto and "archivo ≈" in texto, texto
    # Otra calidad cambia la estimación (60 cm: nivel 18) y se vuelve a la máxima.
    p.select_option("#civil-sat-calidad", "0.6")
    esperar(p, "TVCadSatelite.estado().plan && TVCadSatelite.estado().plan.z === 18", LARGO)
    p.select_option("#civil-sat-calidad", "max")
    plan = esperar_plan(p)
    esperar(p, "TVCadSatelite.estado().plan.z === 19", LARGO)
    p.fill("#civil-sat-nombre", "Predio norte")
    inicio, inicio_teselas = len(red.peticiones), len(proveedor.teselas())
    descargas = []
    p.on("download", lambda d: descargas.append(d))
    t0 = time.monotonic()
    p.locator("#civil-sat-ir").click()
    esperar_aviso(p, "Imagen satelital insertada")
    segundos = time.monotonic() - t0
    u = p.evaluate("TVCadSatelite.estado().ultimo")
    assert u["ancho"] == plan["ancho"] and u["alto"] == plan["alto"] and u["faltantes"] == 0, u
    assert u["recorte"] == "poligono", u
    # Las teselas que dijo la estimación, todas del proveedor, con la clave y el Referer de nuestro origen (la clave se restringe así).
    pedidas = proveedor.teselas()[inicio_teselas:]
    assert len(pedidas) == plan["teselas"]["n"], (len(pedidas), plan["teselas"])
    assert all(r["q"].get("token") == ["clave-de-prueba"] for r in pedidas)
    origen_pagina = n.servidor.url.rstrip("/")
    assert all((r["referer"] or "").rstrip("/") == origen_pagina for r in pedidas), {r["referer"] for r in pedidas}

    # Dónde quedó.
    if motor == "chromium":
        assert u["ruta"] == "Satelital\\Predio norte.jpg" and u["enCarpeta"]
        assert lista_opfs(p, "Proyecto/Satelital") == ["Predio norte.jgw", "Predio norte.jpg", "Predio norte.prj", "Predio norte.txt"]
        jpeg, mundo = leer_opfs(p, "Proyecto/Satelital", "Predio norte.jpg"), leer_opfs(p, "Proyecto/Satelital", "Predio norte.jgw").decode()
        txt = leer_opfs(p, "Proyecto/Satelital", "Predio norte.txt").decode()
        assert "Proveedor de prueba, Imágenes Falsas S.A." in txt and "UTM zona 14 norte" in txt
    else:
        esperar(p, "true", 1000)
        limite = time.monotonic() + 20
        while len(descargas) < 3 and time.monotonic() < limite:
            p.wait_for_timeout(200)
        nombres = sorted(d.suggested_filename for d in descargas)
        assert nombres == ["Predio norte.jgw", "Predio norte.jpg", "Predio norte.txt"], nombres
        por = {d.suggested_filename: d for d in descargas}
        jpeg, mundo = open(por["Predio norte.jpg"].path(), "rb").read(), open(por["Predio norte.jgw"].path()).read()
        assert u["ruta"] == "Predio norte.jpg" and not u["enCarpeta"]
    a, d_, b, e, c, f = (float(v) for v in mundo.split())
    assert (a, d_, b, e) == (0.28, 0, 0, -0.28)
    x0, y1 = c - 0.14, f + 0.14
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    assert im.size == (plan["ancho"], plan["alto"])
    # Cada marca, en su píxel: su centroide a menos de 0.3 px (8 cm) de donde la puso el proveedor, en UTM.
    for k in ("p1", "p2", "fuera"):
        x, y = proveedor.centro_utm(k)
        assert math.hypot(x - MARCAS[k][0], y - MARCAS[k][1]) < 0.3          # el cuadro de la marca, a menos de un píxel de la fuente
        ci, fj = (x - x0) / 0.28, (y1 - y) / 0.28
        cen = centroide_rojo(im, int(ci), int(fj))
        assert cen, (k, ci, fj)
        assert math.hypot(cen[0] - ci, cen[1] - fj) < 0.3, (k, cen, (ci, fj))
    # La atribución va en la esquina inferior derecha de la imagen (texto blanco sobre fondo oscuro).
    esquina = im.crop((im.width - 60, im.height - 14, im.width - 4, im.height - 4))
    rgb = esquina.tobytes()
    assert max(rgb[0::3]) > 200 and min(rgb[1::3]) < 70

    # El IMAGE: capa, ruta, esquina inferior izquierda y vectores de un píxel, recorte poligonal; y la atribución en el plano.
    refs = pedir(p, {"op": "references"})
    imagen = next(i for i in refs["images"] if i["path"] == u["ruta"])
    assert imagen["clipped"], imagen
    ent = pedir(p, {"op": "query", "handles": [u["handle"]], "detail": "full"})["entities"][0]
    assert ent["layer"] == "Imagen satelital", ent["layer"]
    pr = ent.get("properties") or {}
    ins, uu, vv = pr.get("insertion_point"), pr.get("u_vector"), pr.get("v_vector")
    xy = lambda q: (q["x"], q["y"]) if isinstance(q, dict) else (q[0], q[1])  # noqa: E731
    assert abs(xy(ins)[0] - x0) < 1e-6 and abs(xy(ins)[1] - (y1 - 0.28 * plan["alto"])) < 1e-6, ins
    assert abs(xy(uu)[0] - 0.28) < 1e-9 and abs(xy(uu)[1]) < 1e-9 and abs(xy(vv)[0]) < 1e-9 and abs(xy(vv)[1] - 0.28) < 1e-9, (uu, vv)
    cb = pr.get("clip_boundary") or {}
    assert cb.get("clip_type") == "Polygonal" and len(cb.get("vertices") or []) == 5 and pr.get("clipping_enabled") is True, cb
    textos = pedir(p, {"op": "query", "layer": "Imagen satelital", "type": "MText", "detail": "full", "limit": 5})["entities"]
    assert len(textos) == 1 and "Proveedor de prueba" in json.dumps(textos[0], ensure_ascii=False)
    # En la pantalla del editor: cada marca en su lugar del plano; la de fuera del polígono, recortada.
    for k, rojo in (("p1", True), ("p2", True), ("fuera", False)):
        ver(p, *MARCAS[k], 12.0)
        esperar(p, "TVCadLocal.estado().suministradas.some(s => s.raiz && s.completa)   /* lo que la vista necesita ya está en el editor (mosaicos, parche 60) */", LARGO)
        p.wait_for_timeout(1200)
        r_, g_, b_ = color_en(p, *MARCAS[k])
        assert (r_ > 170 and g_ < 100 and b_ < 100) == rojo, (k, (r_, g_, b_))

    # Nada pasó por nuestro servidor: ni bytes de teselas ni de la imagen en ningún cuerpo, y casi nada subido.
    muestras = [proveedor.cache[next(iter(proveedor.cache))][100:164], jpeg[2000:2064], jpeg[-200:-136]]
    nuestras = [(m, url, c) for m, url, c in red.desde(inicio) if url.startswith(n.servidor.url)]
    for m, url, cuerpo in nuestras:
        assert "/tile/" not in url and "tilemap" not in url, url
        for s in muestras:
            assert s not in cuerpo, f"{m} {url} llevó bytes de la imagen"
    assert sum(len(c) for _, _, c in nuestras) < 64 * 1024
    assert all(m == "GET" for m, url, _ in red.desde(inicio) if url.startswith(proveedor.origen))

    # La CSP deja hablar solo con el proveedor: otro origen se bloquea.
    bloqueado = p.evaluate("async (u) => { try { await fetch(u); return false; } catch (_) { return true; } }", otro_origen.servicio + "?f=json&token=x")
    assert bloqueado and not otro_origen.peticiones
    csp = [x for x in n.errores if "connect-src" in x or "Refused to connect" in x]
    assert csp and all(otro_origen.origen in x for x in csp), csp
    n.errores[:] = [x for x in n.errores if x not in csp]

    # Un solo paso de deshacer: se va la imagen con su recorte y su atribución.
    pedir(p, {"op": "undo"})
    esperar(p, "TVCad.pedir({op: 'references'}).then(r => r.images.length === 0)", LARGO)
    assert pedir(p, {"op": "query", "layer": "Imagen satelital", "type": "MText", "detail": "summary", "limit": 1}).get("count", 0) == 0
    print(f"\n{motor}: {plan['ancho']}×{plan['alto']} px, {len(pedidas)} teselas, {segundos:.1f} s (estimado {plan['segundos']} s), JPEG {len(jpeg) / 1e3:.0f} KB (estimado {plan['bytes_archivo'] / 1e3:.0f})")
    assert not _errores_propios(n, motor), n.errores


def test_topes_y_detener(cad, navegar, crear_usuario, roles, proveedor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = plano_con_poligono(ana, tmp_path)
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{plano.pk}/")
    listo(p)
    (tmp_path / "Proyecto").mkdir()
    (tmp_path / "Proyecto" / "leeme.txt").write_text("x")
    carpeta_opfs(p, tmp_path / "Proyecto", "Proyecto")
    p.evaluate("TVCadLocal.vincular()")
    esperar(p, "TVCadLocal.estado().raices.includes('Proyecto')", LARGO)
    preparar(p)
    abrir_hoja(p)
    # Dos esquinas de 4 × 4 km a 15 cm: demasiado grande, se dice por qué y qué calidad sí cabe; no deja bajar.
    p.evaluate(f"window.__puntos = [[{CX - 2000}, {CY - 2000}], [{CX + 2000}, {CY + 2000}]]")
    p.locator("#civil-sat-esquinas").click()
    esperar(p, "document.getElementById('civil-sat-area').classList.contains('civil-elegido')", LARGO)
    assert "Rectángulo de 4,000 × 4,000 m" in p.locator("#civil-sat-area").inner_text().replace(" ", ",") or "Rectángulo de 4" in p.locator("#civil-sat-area").inner_text()
    assert p.locator("#civil-sat-recortar").is_disabled()                 # sin polígono no hay recorte
    p.select_option("#civil-sat-calidad", "0.15")
    esperar(p, "document.querySelector('#civil-sat-plan [data-tipo=error]') !== null", LARGO)
    error = p.locator("#civil-sat-plan [data-tipo=error]").inner_text()
    assert "Demasiado grande" in error and "millones de píxeles" in error and "Baja la calidad a" in error, error
    assert p.locator("#civil-sat-ir").is_disabled()
    # Un área razonable con el proveedor lento: «Detener» a media descarga no deja nada.
    proveedor.demora = 0.15
    p.evaluate(f"window.__puntos = [[{CX - 400}, {CY - 300}], [{CX + 400}, {CY + 300}]]")
    p.locator("#civil-sat-esquinas").click()
    p.select_option("#civil-sat-calidad", "0.3")
    esperar_plan(p)
    p.locator("#civil-sat-ir").click()
    esperar(p, "document.getElementById('civil-sat-estado').textContent.includes('teselas')", LARGO)
    p.locator("#civil-sat-detener").click()
    esperar_aviso(p, "Se detuvo")
    assert not p.evaluate("TVCadSatelite.estado().corriendo")
    assert pedir(p, {"op": "references"})["images"] == []
    assert lista_opfs(p, "Proyecto/Satelital") == []
    assert p.locator("#cad-estado").get_attribute("data-estado") == "guardado"
    assert not _errores_propios(n, "chromium"), n.errores
