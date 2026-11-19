"""«Imagen satelital desde una polilínea»: la geometría (UTM ↔ geográficas ↔ Web Mercator contra pyproj, el plan, la malla de la
reproyección, el archivo de mundo y el .prj), la configuración de la página, la clave y la CSP. El navegador lo prueba
pruebas_navegador/test_cad_satelite_navegador.py (con un proveedor de teselas falso en otro origen).
"""
from __future__ import annotations

import json
import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest
from pyproj import CRS, Transformer

from itt_herramientas.cad import satelite
from itt_herramientas.cad import servicio as cad_servicio
from itt_herramientas.civil import pagina as civil_pagina
from itt_herramientas.pruebas.conftest import url

AQUI = Path(__file__).resolve().parent
GEO_JS = AQUI.parent / "static" / "herramientas" / "cad" / "local" / "satelite-geo.js"
sin_node = pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")


def js(*llamadas):
    r = subprocess.run(["node", str(AQUI / "js" / "cad_satelite_geo.mjs"), str(GEO_JS)], input=json.dumps({"llamadas": llamadas}),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ── Geometría ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@sin_node
def test_utm_de_ida_y_vuelta_contra_pyproj_en_las_zonas_de_mexico_y_al_sur():
    rnd = random.Random(7)
    casos = []
    for _ in range(400):
        zona = rnd.randint(11, 16)
        sur = rnd.random() < 0.15
        lat = rnd.uniform(-40, -1) if sur else rnd.uniform(14, 33)
        lon = zona * 6 - 183 + rnd.uniform(-3.5, 3.5)            # hasta medio grado fuera de la zona, como un plano en la orilla
        crs = f"+proj=utm +zone={zona} +ellps=GRS80{' +south' if sur else ''} +units=m"
        e, n = Transformer.from_crs("+proj=longlat +ellps=GRS80", crs, always_xy=True).transform(lon, lat)
        casos.append((lat, lon, zona, sur, e, n))
    salida = js(*[["geoAUtm", [la, lo, z, s]] for la, lo, z, s, _, _ in casos], *[["utmAGeo", [e, n, z, s]] for _, _, z, s, e, n in casos])["salida"]
    ida, vuelta = salida[: len(casos)], salida[len(casos):]
    for (la, lo, z, s, e, n), (E, N), (LA, LO) in zip(casos, ida, vuelta):
        assert math.hypot(E - e, N - n) < 1e-4, (la, lo, z, E - e, N - n)                      # < 0.1 mm
        assert abs(LA - la) * 111_000 < 1e-4 and abs(LO - lo) * 111_000 * math.cos(math.radians(la)) < 1e-4


@sin_node
def test_web_mercator_contra_pyproj_y_la_resolucion_de_cada_nivel():
    t = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    casos = [(19.4326, -99.1332, 19), (20.6597, -103.3496, 17), (32.5149, -117.0382, 21), (-33.45, -70.66, 12), (0.0, 0.0, 3)]
    salida = js(*[["geoAMercator", [la, lo, z]] for la, lo, z in casos], *[["resolucion", [la, z]] for la, _, z in casos],
                *[["mercatorAGeo", [*[0, 0], z]] for _, _, z in casos])["salida"]
    for k, (la, lo, z) in enumerate(casos):
        x, y = t.transform(lo, la)
        m = 256 * 2 ** z / (2 * math.pi * 6378137)
        X, Y = salida[k]
        assert abs(X - (x * m + 128 * 2 ** z)) < 1e-6 and abs(Y - (128 * 2 ** z - y * m)) < 1e-6
        assert abs(salida[len(casos) + k] - 156543.03392804097 * math.cos(math.radians(la)) / 2 ** z) < 1e-9
    assert abs(salida[2 * len(casos)][0] - 85.0511287798066) < 1e-9 and salida[2 * len(casos)][1] == -180


@sin_node
def test_el_nivel_nunca_es_mas_grueso_que_la_calidad_pedida():
    llamadas = [["nivelPara", [lat, m]] for lat in (15.0, 19.4, 25.0, 32.0) for m in (0.15, 0.3, 0.6, 1.2, 2.4, 0.5)]
    niveles = js(*llamadas)["salida"]
    for (_, (lat, m)), z in zip(llamadas, niveles):
        res = 156543.03392804097 * math.cos(math.radians(lat)) / 2 ** z
        assert res <= m * (1 + 1e-9) and 156543.03392804097 * math.cos(math.radians(lat)) / 2 ** (z - 1) > m, (lat, m, z)


@sin_node
def test_plan_extension_ajustada_al_pixel_teselas_y_topes():
    # Un predio en la zona 14: 512.3 × 301.7 m con 10 m de margen, a 30 cm por píxel.
    caja = [484_100.15, 2_148_200.40, 484_612.45, 2_148_502.10]
    p, grande, muchas = js(["planear", [{"caja": caja, "margen": 10, "metros": 0.3, "zona": 14}]],
                           ["planear", [{"caja": [400_000, 2_100_000, 403_000, 2_103_000], "metros": 0.3, "zona": 14}]],
                           ["planear", [{"caja": [400_000, 2_100_000, 401_500, 2_101_500], "metros": 0.3, "zona": 14, "nivel": 21}]])["salida"]
    for k in ("x0", "y0", "x1", "y1"):
        assert abs(p[k] / 0.3 - round(p[k] / 0.3)) < 1e-6, k                               # las esquinas en múltiplos del píxel
    assert p["x0"] <= caja[0] - 10 and p["x1"] >= caja[2] + 10 and p["y0"] <= caja[1] - 10 and p["y1"] >= caja[3] + 10
    assert p["ancho"] == round((p["x1"] - p["x0"]) / 0.3) and p["alto"] == round((p["y1"] - p["y0"]) / 0.3)
    assert p["z"] == 19 and p["fuente_m"] <= 0.3 and p["motivo"] is None                       # 0.29 m a 19.4° de latitud
    t = p["teselas"]
    assert t["n"] == (t["tx1"] - t["tx0"] + 1) * (t["ty1"] - t["ty0"] + 1) and 30 <= t["n"] < 200
    # las cuatro esquinas caen dentro de las teselas pedidas
    tr = Transformer.from_crs("+proj=utm +zone=14 +ellps=GRS80", "EPSG:3857", always_xy=True)
    m = 256 * 2 ** 19 / (2 * math.pi * 6378137)
    for e, n in ((p["x0"], p["y0"]), (p["x1"], p["y1"]), (p["x0"], p["y1"]), (p["x1"], p["y0"])):
        x, y = tr.transform(e, n)
        tx, ty = math.floor((x * m + 128 * 2 ** 19) / 256), math.floor((128 * 2 ** 19 - y * m) / 256)
        assert t["tx0"] <= tx <= t["tx1"] and t["ty0"] <= ty <= t["ty1"]
    assert grande["motivo"] == "pixeles" and grande["ancho"] * grande["alto"] >= 10_000 * 10_000
    assert muchas["motivo"] in ("pixeles", "teselas")


@sin_node
def test_la_malla_de_la_reproyeccion_es_exacta_a_milesimas_de_pixel():
    for zona, caja, metros in ((14, [484_100, 2_148_200, 486_100, 2_149_700], 0.3),     # en el meridiano central
                               (13, [720_000, 3_500_000, 723_000, 3_502_000], 0.5),     # a 2° del meridiano central (convergencia ≈ 1.2°)
                               (16, [180_000, 1_650_000, 181_200, 1_651_000], 0.15)):   # a 3° y al sur de la zona (Chiapas)
        plan = js(["planear", [{"caja": caja, "metros": metros, "zona": zona}]])["salida"][0]
        assert js(["errorMalla", [plan]])["salida"][0] < 0.005, (zona, plan["z"])


@sin_node
def test_archivo_de_mundo_y_prj():
    plan = {"metros": 0.3, "x0": 484_100.1, "y1": 2_148_502.2}
    mundo, prj13, prj_sur = js(["mundo", [plan]], ["prj", [13]], ["prj", [19, True]])["salida"]
    a, d, b, e, c, f = (float(v) for v in mundo.split())
    assert (a, d, b, e) == (0.3, 0, 0, -0.3) and mundo.endswith("\r\n")
    assert abs(c - (484_100.1 + 0.15)) < 1e-6 and abs(f - (2_148_502.2 - 0.15)) < 1e-6       # el CENTRO del píxel superior izquierdo
    assert CRS.from_wkt(prj13).to_epsg() == 32613 and CRS.from_wkt(prj_sur).to_epsg() == 32719


# ── La página ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_la_accion_esta_en_los_menus_de_civil_y_su_hoja_en_el_panel(settings):
    acciones = {a["clave"]: a for m in civil_pagina.MENUS for a in m["acciones"] if not a.get("separador")}
    assert acciones["satelite"]["nombre"] == "Imagen satelital…" and acciones["satelite"]["icono"] == "satellite"
    html, _ = civil_pagina._panel()
    assert 'id="civil-hoja-satelite"' in html
    # el grupo «Imágenes» de la cinta del editor, también en la barra de respaldo (editor sin pestañas propias) y en «Más acciones»
    assert 'data-civil-menu="imagenes"' in html and 'data-civil="satelite"' in html and 'data-civil-menu="plano"' in html
    for texto in ("Polilínea", "Dos esquinas", "Máxima disponible", "30 cm por píxel", "Recortar a la polilínea", "Descargar e insertar", "Detener"):
        assert texto in html, texto


@pytest.fixture
def editor_civil(tmp_path, settings):
    from itt_herramientas.cad import editor

    base = tmp_path / "editor" / editor.version()
    base.mkdir(parents=True)
    (base / "index.html").write_text("<!doctype html><title>x</title>")
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(tmp_path / "editor")
    return base


def _config(html: str) -> dict:
    return json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])


def _connect(csp: str) -> list[str]:
    return next(d.split()[1:] for d in csp.split(";") if d.strip().startswith("connect-src"))


@pytest.mark.django_db
def test_la_clave_viaja_solo_a_quien_dibuja_y_la_csp_abre_solo_el_proveedor(cliente_de, crear_usuario, roles, editor_civil, settings):
    dibujante = crear_usuario("dana")
    roles.conceder(dibujante, "herramientas", "cad")
    supervisora = crear_usuario("sara")
    roles.conceder(supervisora, "herramientas", "supervision")
    p = cad_servicio.crear_vacio(dibujante, "Predio", "", "dxf")
    # Sin clave: la hoja dice que falta y la CSP es la de siempre.
    settings.HERRAMIENTAS_CAD_SATELITE_CLAVE = ""
    r = cliente_de(dibujante).get(url("cad_plano", pk=p.pk))
    cfg = _config(r.content.decode())
    assert cfg["satelite"]["clave"] == "" and _connect(r["Content-Security-Policy"]) == ["'self'"]
    # Con clave: el origen del proveedor en connect-src, y nada más; la página trae sus scripts.
    settings.HERRAMIENTAS_CAD_SATELITE_CLAVE = "clave-de-prueba"
    r = cliente_de(dibujante).get(url("cad_plano", pk=p.pk))
    html = r.content.decode()
    cfg = _config(html)["satelite"]
    assert cfg["clave"] == "clave-de-prueba" and cfg["servicio"] == satelite.SERVICIO and cfg["zmax"] == 21
    assert cfg["trabajador"].endswith("satelite-trabajador.js") and cfg["geo"].endswith("satelite-geo.js")
    assert "herramientas/cad/local/satelite.js" in html and "herramientas/cad/local/satelite-geo.js" in html
    assert 'id="cad-menu-imagen-satelite"' in html and "Imagen satelital…" in html
    csp = r["Content-Security-Policy"]
    assert _connect(csp) == ["'self'", "https://ibasemaps-api.arcgis.com"]
    assert "wasm-unsafe-eval" not in csp and "frame-ancestors 'none'" in csp and "img-src 'self' data: blob:" in csp
    nonce = html.split('nonce="')[1].split('"')[0] if 'nonce="' in html else None
    if nonce:
        assert f"'nonce-{nonce}'" in csp                                                     # la misma del resto de la página
    # Otro servicio (las pruebas de navegador): su origen exacto, con puerto.
    settings.HERRAMIENTAS_CAD_SATELITE_SERVICIO = "http://127.0.0.1:8123/arcgis/rest/services/World_Imagery/MapServer/"
    assert _connect(cliente_de(dibujante).get(url("cad_plano", pk=p.pk))["Content-Security-Policy"]) == ["'self'", "http://127.0.0.1:8123"]
    # Supervisión no dibuja: ni la clave ni la CSP abierta.
    r = cliente_de(supervisora).get(url("cad_plano", pk=p.pk))
    assert "clave-de-prueba" not in r.content.decode() and _config(r.content.decode())["satelite"] is None
    assert 'id="cad-menu-imagen-satelite"' not in r.content.decode()
    assert _connect(r["Content-Security-Policy"]) == ["'self'"]
    # Una versión anterior (solo lectura) tampoco.
    r = cliente_de(dibujante).get(url("cad_plano", pk=p.pk) + "?version=1")
    assert _config(r.content.decode())["satelite"] is None


@pytest.mark.django_db
def test_las_demas_paginas_no_abren_el_proveedor(cliente_de, crear_usuario, roles, settings):
    settings.HERRAMIENTAS_CAD_SATELITE_CLAVE = "clave-de-prueba"
    u = crear_usuario("dana")
    roles.conceder(u, "herramientas", "cad")
    for nombre in ("cad_lista", "inicio"):
        try:
            r = cliente_de(u).get(url(nombre))
        except Exception:
            continue
        assert "arcgis" not in r.get("Content-Security-Policy", ""), nombre
