"""Croquis en el navegador móvil (Chromium/Pixel y WebKit/iPhone): dibujo del área con Terra Draw sobre MapLibre (Chromium: WebGL),
vértices a mano, importación, límite de 50 km², vectorización con Overpass grabado, PDF y DXF, y el modo sin conexión."""
from __future__ import annotations

import os
from unittest import mock

import pytest
from playwright.sync_api import expect

from itt_herramientas.dominio import teselas as dominio_teselas
from itt_herramientas.models import Croquis

from .test_pwa_navegador import TIEMPO, contexto, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


@pytest.fixture
def croquista(crear_usuario, roles):
    u = crear_usuario("cris")
    roles.conceder(u, "herramientas", "croquis")
    return u


@pytest.fixture(autouse=True)
def sin_red_de_mapas():
    """Ninguna prueba sale a internet: ni teselas ni Overpass (se graba una respuesta)."""
    with mock.patch.object(dominio_teselas, "obtener", side_effect=dominio_teselas.TeselaNoDisponible("sin red (prueba)")):
        yield


def g(*pares):
    return [{"lat": a, "lon": b} for a, b in pares]


OVERPASS = [
    {"type": "way", "id": 1, "tags": {"building": "yes", "name": "Nave 1"}, "geometry": g((28.6334, -106.0695), (28.6334, -106.0685), (28.6340, -106.0685), (28.6340, -106.0695), (28.6334, -106.0695))},
    {"type": "way", "id": 2, "tags": {"highway": "primary", "name": "Av. Universidad"}, "geometry": g((28.6325, -106.0705), (28.6340, -106.0690), (28.6355, -106.0672))},
    {"type": "way", "id": 3, "tags": {"waterway": "canal"}, "geometry": g((28.6331, -106.0699), (28.6345, -106.0669))},
]


def abrir(ctx, servidor, ruta="/croquis/"):
    pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
    if os.environ.get("ITT_DEPURAR"):
        pagina.on("console", lambda m: print("CON", m.type, m.text[:300]))
        pagina.on("pageerror", lambda e: print("ERR", str(e)[:300]))
    pagina.goto(servidor.url + ruta)
    pagina.wait_for_selector("html[data-cq-lista]", timeout=TIEMPO)
    return pagina


def con_service_worker(pagina):
    pagina.evaluate("navigator.serviceWorker.ready.then(() => true)")
    if not pagina.evaluate("!!navigator.serviceWorker.controller"):
        pagina.reload()
        pagina.wait_for_selector("html[data-cq-lista]", timeout=TIEMPO)
        esperar(pagina, "!!navigator.serviceWorker.controller", timeout=TIEMPO)


def sin_pendientes(pagina, timeout=TIEMPO * 3):
    esperar(pagina, "ITT.cola.resumen().then(r => r.total === 0)", timeout=timeout)


def sin_desborde(pagina):
    assert pagina.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), "la página se desborda horizontalmente"


def nuevo(pagina):
    pagina.locator("#cq-nuevo").click()
    pagina.wait_for_selector("#cq-vista-editor:not([hidden])")


def puntos(pagina):
    return pagina.evaluate("ITT.cola.borradores().then(b => b.find(x => x.tipo === 'croquis').datos.points)")


def poner_vertices(pagina, pares):
    pagina.locator("#cq-paso-vertices summary").click()
    for i, (lat, lon) in enumerate(pares):
        pagina.locator("#cq-agregar-vertice").click()
        pagina.fill(f"#v-la-{i}", str(lat))
        pagina.locator(f"#v-la-{i}").press("Tab")
        pagina.fill(f"#v-lo-{i}", str(lon))
        pagina.locator(f"#v-lo-{i}").press("Tab")


CUADRO = [(28.6330, -106.0700), (28.6330, -106.0670), (28.6352, -106.0670), (28.6352, -106.0700)]
GRANDE = [(28.0, -106.0), (28.0, -105.5), (28.5, -105.5), (28.5, -106.0)]


def test_dibujar_vectorizar_y_exportar_pdf_y_dxf(motor, tmp_path, servidor, sesion_de, croquista):
    if motor["nombre"] != "chromium":
        pytest.skip("El dibujo sobre el mapa necesita WebGL (Chromium); en WebKit se prueban los vértices a mano")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista), accept_downloads=True)
    try:
        pagina = abrir(ctx, servidor)
        nuevo(pagina)
        pagina.wait_for_selector("html[data-mapa-listo]", timeout=TIEMPO * 2)
        pagina.fill("#q-project", "Nave industrial")
        pagina.locator("#cq-paso-capas summary").click()
        pagina.select_option("#q-map_layer", "Base neutra")
        pagina.locator("#cq-paso-capas summary").click()
        pagina.locator(".cq-modos [data-modo=polygon]").click()
        pagina.locator("#cq-mapa").scroll_into_view_if_needed()
        pagina.wait_for_timeout(300)
        caja = pagina.locator("#cq-mapa canvas").first.bounding_box()
        cx, cy = caja["x"] + caja["width"] / 2, caja["y"] + caja["height"] / 2
        esquinas = [(-70, -60), (70, -60), (70, 60), (-70, 60)]
        for dx, dy in esquinas:
            pagina.mouse.click(cx + dx, cy + dy)
        pagina.mouse.click(cx + esquinas[-1][0], cy + esquinas[-1][1])                       # cerrar el polígono en el último vértice
        esperar(pagina, "ITT.cola.borradores().then(b => b.find(x => x.tipo === 'croquis').datos.points.length === 4)", timeout=TIEMPO)
        expect(pagina.locator("#cq-area")).to_contain_text("4 vértices")
        assert pagina.locator("#cq-area").get_attribute("data-estado") == "ok"
        assert pagina.locator(".cq-modos [data-modo=select]").get_attribute("aria-pressed") == "true"      # al terminar queda en modo editar
        # La tabla de vértices sigue al mapa: lo dibujado aparece en ella con las mismas coordenadas
        expect(pagina.locator("#cq-vertices > li")).to_have_count(4)
        puntos = pagina.evaluate("ITT.cola.borradores().then(b => b.find(x => x.tipo === 'croquis').datos.points)")
        assert [pagina.locator(f"#v-la-{i}").input_value() for i in range(4)] == [f"{q['latitude']:.7f}" for q in puntos]
        pagina.wait_for_timeout(600)
        sin_pendientes(pagina)
        # --- Vectorizar con la respuesta de Overpass grabada ---
        with mock.patch("itt_herramientas.dominio.osm_vector._fetch_query_elements", return_value=OVERPASS):
            pagina.locator("#cq-vectorizar").click()
            expect(pagina.locator("#salida-resumen")).to_contain_text("Se obtuvieron 3 elementos", timeout=TIEMPO * 3)
        pagina.locator("#dlg-salida [data-cerrar]").click()
        expect(pagina.locator("#cq-pdf")).to_be_visible(timeout=TIEMPO)
        expect(pagina.locator("#cq-vectorizar")).to_be_hidden()
        # Las capas filtran lo que se ve en el mapa.
        pagina.locator("#cq-paso-capas summary").click()
        esperar(pagina, "!!ITT.croquis.mapa.getSource('vec') && ITT.croquis.mapa.querySourceFeatures('vec').length > 0", timeout=TIEMPO)
        pagina.locator("#capa-building").uncheck()
        assert "building" not in pagina.evaluate("JSON.stringify(ITT.croquis.mapa.getFilter('vec-borde'))")
        pagina.locator("#capa-building").check()
        # --- PDF ---
        pagina.locator("#cq-pdf").click()
        esperar(pagina, "document.querySelectorAll('#salida-paginas canvas').length >= 1", timeout=TIEMPO * 6)
        assert "CROQUIS DE UBICACION - Nave industrial.pdf" in pagina.locator("#salida-estado").inner_text()
        with pagina.expect_download() as d:
            pagina.locator("#salida-descargar").click()
        ruta = tmp_path / "croquis.pdf"
        d.value.save_as(str(ruta))
        assert ruta.read_bytes().startswith(b"%PDF")
        pagina.locator("#dlg-salida [data-cerrar]").click()
        # --- DXF ---
        pagina.locator("#cq-dxf").click()
        expect(pagina.locator("#salida-descargar")).to_be_enabled(timeout=TIEMPO * 4)
        with pagina.expect_download() as d2:
            pagina.locator("#salida-descargar").click()
        dxf = tmp_path / "croquis.dxf"
        d2.value.save_as(str(dxf))
        import ezdxf
        documento = ezdxf.readfile(str(dxf))
        capas = {c.dxf.name for c in documento.layers}
        assert len(capas) > 2 and any(e.dxftype() in ("LWPOLYLINE", "POLYLINE", "HATCH") for e in documento.modelspace())
        assert Croquis.objects.get().salida_formato == "dxf"
    finally:
        ctx.close()


def test_vertices_a_mano_limite_de_area_y_proyecto_para_el_modulo_local(motor, tmp_path, servidor, sesion_de, croquista):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista), accept_downloads=True)
    try:
        pagina = abrir(ctx, servidor)
        nuevo(pagina)
        sin_desborde(pagina)
        poner_vertices(pagina, CUADRO)
        expect(pagina.locator("#cq-area")).to_contain_text("4 vértices")
        assert pagina.locator("#cq-area").get_attribute("data-estado") == "ok"
        # Cada fila: subir, bajar y quitar con icono, nombre accesible y 44 px
        for etiqueta in ("Subir el vértice 2", "Bajar el vértice 2", "Quitar el vértice 2"):
            b = pagina.locator(f"#cq-vertices button[aria-label='{etiqueta}']")
            expect(b).to_be_visible()
            assert b.locator("svg").count() == 1 and b.bounding_box()["height"] >= 43.5, etiqueta
        expect(pagina.locator("#cq-vectorizar")).to_be_visible()
        expect(pagina.locator("#cq-modulo")).to_be_hidden()
        # Cambiar un vértice a mano mueve el polígono.
        pagina.fill("#v-lo-1", "-106.06")
        pagina.locator("#v-lo-1").press("Tab")
        esperar(pagina, "ITT.cola.borradores().then(b => (b.find(x => x.tipo === 'croquis').datos.points[1] || {}).longitude === -106.06)")
        # Pasar de 50 km²: el límite se marca en rojo y el botón de vectorizar se sustituye.
        for i, (lat, lon) in enumerate(GRANDE):
            pagina.fill(f"#v-la-{i}", str(lat)); pagina.locator(f"#v-la-{i}").press("Tab")
            pagina.fill(f"#v-lo-{i}", str(lon)); pagina.locator(f"#v-lo-{i}").press("Tab")
        assert pagina.locator("#cq-area").get_attribute("data-estado") == "excedida"
        expect(pagina.locator("#cq-area")).to_contain_text("módulo local")
        expect(pagina.locator("#cq-vectorizar")).to_be_hidden()
        expect(pagina.locator("#cq-modulo")).to_be_visible()
        # Una coordenada inválida no se acepta.
        pagina.fill("#v-la-0", "95")
        pagina.locator("#v-la-0").press("Tab")
        assert pagina.locator("#v-la-0").get_attribute("aria-invalid") == "true"
        pagina.evaluate("() => { Object.defineProperty(navigator, 'canShare', { value: undefined, configurable: true }); }")       # sin Web Share: descarga (WebKit sí comparte)
        with pagina.expect_download() as d:
            pagina.locator("#cq-modulo").click()
        import json
        proyecto = json.loads(open(d.value.path()).read())
        assert d.value.suggested_filename.startswith("EDITABLE_CROQUIS DE UBICACION") and len(proyecto["points"]) == 4 and proyecto["features"] == []
        from itt_herramientas.dominio import location_sketch as ls
        assert len(ls.SketchData.from_dict(proyecto).points) == 4                           # lo abre el escritorio / el módulo local
        sin_desborde(pagina)
        # Quitar un vértice y mover otro.
        pagina.locator("#cq-vertices > li").nth(3).locator("[data-itt=quitar-vertice]").click()
        esperar(pagina, "ITT.cola.borradores().then(b => b.find(x => x.tipo === 'croquis').datos.points.length === 3)")
    finally:
        ctx.close()


def test_importar_csv_y_kml(motor, tmp_path, servidor, sesion_de, croquista):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista))
    try:
        pagina = abrir(ctx, servidor)
        nuevo(pagina)
        pagina.locator("#cq-paso-vertices summary").click()
        csv = "Punto;Latitud;Longitud;Descripción\nA;28,633;-106,07;Esquina\nB;28.633;-106.067;\nC;28.635;-106.067;\n".encode()
        pagina.set_input_files("#cq-importar", [{"name": "puntos.csv", "mimeType": "text/csv", "buffer": csv}])
        expect(pagina.locator("#cq-vertices > li")).to_have_count(3, timeout=TIEMPO)
        expect(pagina.locator("#cq-area")).to_contain_text("3 vértices")
        kml = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>'
               '-106.07,28.633,0 -106.067,28.633,0 -106.067,28.635,0 -106.07,28.635,0 -106.07,28.633,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Document></kml>').encode()
        pagina.set_input_files("#cq-importar", [{"name": "predio.kml", "mimeType": "application/vnd.google-earth.kml+xml", "buffer": kml}])
        expect(pagina.locator("#dlg-confirmar")).to_be_visible(timeout=TIEMPO)                       # reemplaza el área actual: pregunta
        pagina.locator("#confirmar-si").click()
        expect(pagina.locator("#cq-vertices > li")).to_have_count(4, timeout=TIEMPO)
        pagina.set_input_files("#cq-importar", [{"name": "malo.csv", "mimeType": "text/csv", "buffer": b"sin;columnas\n1;2\n"}])
        expect(pagina.locator("#cq-mensaje")).to_contain_text("latitud y longitud", timeout=TIEMPO)
    finally:
        ctx.close()


def test_sin_conexion_se_edita_y_el_autoguardado_llega_al_volver(motor, tmp_path, servidor, sesion_de, croquista):
    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista))
    try:
        pagina = abrir(ctx, servidor)
        con_service_worker(pagina)
        ctx.set_offline(True)
        nuevo(pagina)
        pagina.fill("#q-title", "Croquis sin señal")
        poner_vertices(pagina, CUADRO)
        pagina.wait_for_timeout(700)
        assert pagina.evaluate("ITT.cola.resumen().then(r => r.borradores)") == 1 and Croquis.objects.count() == 0
        ctx.close()
        ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista))
        ctx.set_offline(True)
        pagina = ctx.pages[0] if ctx.pages else ctx.new_page()
        pagina.goto(servidor.url + "/croquis/")
        pagina.wait_for_selector("html[data-cq-lista]", timeout=TIEMPO)
        expect(pagina.locator("#cq-lista > li")).to_have_count(1)
        ctx.set_offline(False)
        pagina.evaluate("ITT.subirAhora()")
        sin_pendientes(pagina)
        c = Croquis.objects.get()
        assert c.titulo == "Croquis sin señal" and len(c.datos["points"]) == 4
        assert c.datos["points"][2]["latitude"] == 28.6352
    finally:
        ctx.close()


def test_mapa_a_pantalla_completa_en_el_telefono(motor, tmp_path, servidor, sesion_de, croquista):
    if motor["nombre"] != "chromium":
        pytest.skip("Necesita el mapa (WebGL)")
    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista))
    try:
        pagina = abrir(ctx, servidor)
        nuevo(pagina)
        pagina.wait_for_selector("html[data-mapa-listo]", timeout=TIEMPO * 2)
        pagina.locator("#cq-completa").click()
        caja = pagina.locator("#cq-mapa").bounding_box()
        tam = pagina.viewport_size
        assert caja["x"] == 0 and caja["y"] == 0 and caja["width"] == tam["width"] and caja["height"] == tam["height"]
        for selector in (".cq-modos [data-modo=polygon]", "#cq-completa", "#cq-ubicacion"):
            herramienta = pagina.locator(selector).bounding_box()
            assert herramienta and herramienta["height"] >= 43.5 and 0 <= herramienta["x"] and herramienta["x"] + herramienta["width"] <= tam["width"] + 1, selector
        pagina.locator("#cq-completa").click()
        assert pagina.locator("#cq-mapa").bounding_box()["height"] < tam["height"]
        sin_desborde(pagina)
    finally:
        ctx.close()


def test_importar_un_csv_con_area_pregunta_reemplazar_o_agregar(motor, tmp_path, servidor, sesion_de, croquista):
    """UX-ITT-10: ya no se mezclan dos áreas en silencio."""
    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista))
    try:
        pagina = abrir(ctx, servidor)
        nuevo(pagina)
        pagina.locator("#cq-paso-vertices summary").click()
        csv = "Punto;Latitud;Longitud\nA;28,633;-106,07\nB;28.633;-106.067\nC;28.635;-106.067\n".encode()
        otro = "Punto;Latitud;Longitud\nX;28,640;-106,07\nY;28.640;-106.067\n".encode()
        entrada = lambda datos: pagina.set_input_files("#cq-importar", [{"name": "p.csv", "mimeType": "text/csv", "buffer": datos}])
        entrada(csv)                                                       # sin área: se agrega sin preguntar
        expect(pagina.locator("#cq-vertices > li")).to_have_count(3, timeout=TIEMPO)
        assert not pagina.locator("#dlg-confirmar").is_visible()
        entrada(otro)                                                      # con área: pregunta, y cancelar no cambia nada
        expect(pagina.locator("#dlg-confirmar")).to_be_visible(timeout=TIEMPO)
        expect(pagina.locator("#confirmar-texto")).to_contain_text("ya tiene 3 vértices")
        pagina.locator("#confirmar-no").click()
        expect(pagina.locator("#cq-vertices > li")).to_have_count(3)
        entrada(otro)                                                      # agregar al final
        expect(pagina.locator("#confirmar-alt")).to_be_visible(timeout=TIEMPO)
        pagina.locator("#confirmar-alt").click()
        expect(pagina.locator("#cq-vertices > li")).to_have_count(5, timeout=TIEMPO)
        entrada(otro)                                                      # reemplazar
        expect(pagina.locator("#dlg-confirmar")).to_be_visible(timeout=TIEMPO)
        pagina.locator("#confirmar-si").click()
        expect(pagina.locator("#cq-vertices > li")).to_have_count(2, timeout=TIEMPO)
    finally:
        ctx.close()


def test_un_token_csrf_que_no_es_el_de_la_cookie_se_renueva_y_la_importacion_sigue(motor, tmp_path, servidor, sesion_de, croquista):
    """Al abrir sin cookie CSRF, ui.js y la herramienta piden /sesion/ a la vez y cada respuesta trae otro secreto: se podía guardar el
    token que no es el de la cookie y la importación quedaba en «Sin permiso» (la prueba de reemplazar o agregar fallaba bajo carga).
    Ahora un 403 de CSRF pide el token vigente y repite una vez."""
    from django.middleware.csrf import _get_new_csrf_string, _mask_cipher_secret

    ctx = contexto(motor, tmp_path, servidor, sesion_de(croquista))
    try:
        pagina = abrir(ctx, servidor)
        nuevo(pagina)
        pagina.evaluate("t => ITT.cola.guardarAjuste('csrf', t)", _mask_cipher_secret(_get_new_csrf_string()))   # el de la otra respuesta
        pagina.locator("#cq-paso-vertices summary").click()
        csv = "Punto;Latitud;Longitud\nA;28,633;-106,07\nB;28.633;-106.067\nC;28.635;-106.067\n".encode()
        pagina.set_input_files("#cq-importar", [{"name": "p.csv", "mimeType": "text/csv", "buffer": csv}])
        expect(pagina.locator("#cq-vertices > li")).to_have_count(3, timeout=TIEMPO)
        sin_pendientes(pagina)                                             # y el autoguardado también sube con el token renovado
    finally:
        ctx.close()
