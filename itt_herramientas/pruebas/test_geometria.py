"""Geometría adaptativa del convertidor: error de cuerda medido, rectas sin vértices nuevos y vértices originales intactos."""
from __future__ import annotations

import math
import random

import ezdxf
import pytest
from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point

from itt_herramientas.dominio import geometria as g
from itt_herramientas.dominio import geospatial_converter as gc

UTM13 = Transformer.from_crs(CRS.from_epsg(32613), CRS.from_epsg(4326), always_xy=True)
X0, Y0 = 370_000.0, 3_170_000.0


def desviacion_a_arco(puntos, cx, cy, radio, a0, barrido, muestras=4000) -> float:
    """Mayor distancia entre el arco VERDADERO y la polilínea aproximada (medida, no supuesta)."""
    linea = LineString([(p[0], p[1]) for p in puntos])
    peor = 0.0
    for i in range(muestras + 1):
        a = a0 + barrido * i / muestras
        peor = max(peor, linea.distance(Point(cx + radio * math.cos(a), cy + radio * math.sin(a))))
    return peor


@pytest.mark.parametrize("radio", [0.05, 0.3, 2.0, 15.0, 120.0, 800.0])
@pytest.mark.parametrize("tolerancia", [0.001, 0.01, 0.05, 0.25])
def test_el_arco_nunca_se_separa_mas_que_la_tolerancia(radio, tolerancia):
    barrido = math.radians(137)
    inicio = (X0 + radio, Y0, 0.0, True)
    fin = (X0 + radio * math.cos(barrido), Y0 + radio * math.sin(barrido), 0.0, True)
    puntos = [inicio, *g.puntos_de_arco(X0, Y0, radio, 0.0, barrido, 0.0, 0.0, tolerancia), fin]
    assert desviacion_a_arco(puntos, X0, Y0, radio, 0.0, barrido) <= tolerancia * (1 + 1e-9)
    for p in puntos[1:-1]:
        assert math.hypot(p[0] - X0, p[1] - Y0) == pytest.approx(radio, abs=1e-9)          # los puntos nuevos están SOBRE el arco


def test_a_mayor_tolerancia_menos_puntos_y_a_menor_radio_menos_puntos():
    cuentas = [g.segmentos_de_arco(10.0, 2 * math.pi, t) for t in (0.001, 0.01, 0.05, 0.25)]
    assert cuentas == sorted(cuentas, reverse=True) and cuentas[0] > cuentas[-1]
    assert g.segmentos_de_arco(0.3, 2 * math.pi, 0.01) < g.segmentos_de_arco(50, 2 * math.pi, 0.01)


def test_un_arco_siempre_conserva_un_segmento_por_cada_45_grados():
    assert g.segmentos_de_arco(10.0, math.radians(90), 5.0) >= 2
    assert len(g.puntos_de_circulo(X0, Y0, 0, 0.3, 5.0)) - 1 >= 8


@pytest.mark.parametrize("bulge", [0.05, 0.4142135623730951, 1.0, -0.7, -0.2])
def test_bulge_da_un_arco_exacto_dentro_de_la_tolerancia(bulge):
    p0, p1 = (X0, Y0, 0.0), (X0 + 12.0, Y0 + 5.0, 0.0)
    interior = g.interiores_de_bulge(p0, p1, bulge, 0.01)
    # El arco verdadero pasa por p0, p1 y todos los puntos interiores equidistan del mismo centro.
    puntos = [(*p0, True), *interior, (*p1, True)]
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    cuerda = math.hypot(dx, dy)
    desplazamiento = (cuerda / 2) * (1 - bulge * bulge) / (2 * bulge)
    cx, cy = (p0[0] + p1[0]) / 2 - dy / cuerda * desplazamiento, (p0[1] + p1[1]) / 2 + dx / cuerda * desplazamiento
    radio = math.hypot(p0[0] - cx, p0[1] - cy)
    for p in puntos:
        assert math.hypot(p[0] - cx, p[1] - cy) == pytest.approx(radio, abs=1e-6)
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    assert desviacion_a_arco(puntos, cx, cy, radio, a0, 4 * math.atan(bulge)) <= 0.01 * (1 + 1e-9)
    assert g.interiores_de_bulge(p0, p1, 0.0, 0.01) == [] and g.interiores_de_bulge(p0, p0, 0.5, 0.01) == []


def test_limpiar_no_toca_vertices_originales():
    # Tres vértices originales exactamente alineados: son esquinas levantadas, se conservan todos.
    originales = [(0.0, 0.0, 0.0, True), (1.0, 1.0, 0.0, True), (2.0, 2.0, 0.0, True), (3.0, 1.0, 0.0, True)]
    assert g.limpiar(originales) == originales


def test_limpiar_quita_generados_alineados_y_duplicados_exactos():
    pts = [(0.0, 0.0, 0.0, True), (1.0, 1.0, 0.0, False), (2.0, 2.0, 0.0, False), (3.0, 3.0, 0.0, True), (3.0, 3.0, 0.0, True), (4.0, 0.0, 0.0, True)]
    assert g.limpiar(pts) == [(0.0, 0.0, 0.0, True), (3.0, 3.0, 0.0, True), (4.0, 0.0, 0.0, True)]
    curvo = [(0.0, 0.0, 0.0, True), (1.0, 0.2, 0.0, False), (2.0, 0.0, 0.0, True)]
    assert g.limpiar(curvo) == curvo                                           # un generado fuera de la recta se queda
    elevado = [(0.0, 0.0, 0.0, True), (1.0, 1.0, 5.0, False), (2.0, 2.0, 0.0, True)]
    assert g.limpiar(elevado) == elevado                                       # alineado en planta pero no en elevación
    assert g.limpiar([(1.0, 1.0, 0.0, False), (1.0, 1.0, 0.0, True)]) == [(1.0, 1.0, 0.0, True)]   # entre dos duplicados se conserva el original


def utm_a_geo(x, y):
    return UTM13.transform(x, y)


def test_una_recta_de_levantamiento_no_gana_vertices_al_reproyectar():
    for largo in (10, 100, 800, 2500):
        a, b = (X0, Y0, 0.0, True), (X0 + largo, Y0 + largo * 0.7, 0.0, True)
        assert g.subdividir_si_se_dobla(a, b, utm_a_geo, 0.01) == [], largo


def test_una_recta_enorme_solo_se_parte_si_la_reproyeccion_la_dobla():
    a, b = (X0, Y0, 0.0, True), (X0 + 250_000, Y0 + 400_000, 0.0, True)           # cientos de kilómetros: ahí sí se dobla
    intermedios = g.subdividir_si_se_dobla(a, b, utm_a_geo, 0.01)
    assert len(intermedios) > 0 and all(not p[3] for p in intermedios)
    # medido: tras partir, el punto medio de cada tramo se separa de su cuerda geográfica menos que la tolerancia
    todos = [a, *intermedios, b]
    for p, q in zip(todos, todos[1:]):
        m = utm_a_geo((p[0] + q[0]) / 2, (p[1] + q[1]) / 2)
        pg, qg = utm_a_geo(p[0], p[1]), utm_a_geo(q[0], q[1])
        lon_g, lat_g = g._medio_geodesico(pg[0], pg[1], qg[0], qg[1])
        assert g._metros(m[0] - lon_g, m[1] - lat_g, m[1]) <= 0.01 * 1.0001


def test_tolerancia_acotada():
    assert g.tolerancia_valida(None) == 0.01 and g.tolerancia_valida(0) == g.TOLERANCIA_MINIMA and g.tolerancia_valida(99) == 5.0
    assert set(g.TOLERANCIAS) == {"fina", "topografica", "dibujo", "ligera"} and g.TOLERANCIAS["topografica"] == g.TOLERANCIA_POR_OMISION


# -- Lectura de DXF completa: vértices originales y curvas medidas ---------------------------------------------------

def leer(tmp_path, llenar, tolerancia=0.01):
    doc = ezdxf.new("R2010")
    llenar(doc.modelspace())
    ruta = tmp_path / "t.dxf"
    doc.saveas(ruta)
    return gc.read_dxf(ruta, 13, "N", curve_tolerance=tolerancia)


def esta(dataset_parte, x, y, tol=1e-9) -> bool:
    lon, lat = utm_a_geo(x, y)
    return any(abs(p[0] - lon) <= tol and abs(p[1] - lat) <= tol for p in dataset_parte)


def test_todo_vertice_original_se_conserva_en_rectas_y_polilineas(tmp_path):
    rng = random.Random(3)
    puntos2d = [(round(X0 + i * 2.7, 3), round(Y0 + rng.uniform(-3, 3), 3)) for i in range(80)]
    alineados = [(X0 + 500, Y0), (X0 + 510, Y0 + 10), (X0 + 520, Y0 + 20), (X0 + 530, Y0 + 30)]           # esquinas exactamente alineadas
    puntos3d = [(X0 + i, Y0 + 300 + i * 0.5, 1400 + i * 0.01) for i in range(50)]

    def llenar(m):
        m.add_lwpolyline(puntos2d)
        m.add_lwpolyline(alineados)
        m.add_polyline3d(puntos3d)
        m.add_line((X0, Y0 + 900), (X0 + 100, Y0 + 905))

    ds = leer(tmp_path, llenar)
    partes = [f.parts[0] for f in ds.features]
    assert [len(p) for p in partes] == [80, 4, 50, 2]                              # ni uno más ni uno menos
    for x, y in puntos2d + alineados + [(p[0], p[1]) for p in puntos3d] + [(X0, Y0 + 900), (X0 + 100, Y0 + 905)]:
        assert any(esta(p, x, y) for p in partes), (x, y)
    assert [round(v[2], 6) for v in partes[2]][:3] == [1400.0, 1400.01, 1400.02]


def test_vertices_duplicados_exactos_se_quitan(tmp_path):
    ds = leer(tmp_path, lambda m: m.add_lwpolyline([(X0, Y0), (X0, Y0), (X0 + 5, Y0), (X0 + 5, Y0), (X0 + 5, Y0 + 5)]))
    assert len(ds.features[0].parts[0]) == 3


@pytest.mark.parametrize("tolerancia", [0.001, 0.01, 0.05, 0.25])
def test_circulo_arco_y_bulge_medidos_contra_la_curva_verdadera(tmp_path, tolerancia):
    def llenar(m):
        m.add_circle((X0, Y0), 7.5)
        m.add_arc((X0 + 40, Y0), 12.0, 20, 140)
        m.add_lwpolyline([(X0 + 80, Y0, 0, 0, 0.5), (X0 + 95, Y0, 0, 0, 0)], format="xyseb")

    ds = leer(tmp_path, llenar, tolerancia)
    def a_utm(parte):
        t = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(32613), always_xy=True)
        return [(*t.transform(lon, lat), 0.0, False) for lon, lat, _ in parte]
    circulo, arco, bulge = (a_utm(f.parts[0]) for f in ds.features)
    assert desviacion_a_arco(circulo, X0, Y0, 7.5, 0, 2 * math.pi) <= tolerancia + 2e-4         # + redondeo de la reproyección
    assert desviacion_a_arco(arco, X0 + 40, Y0, 12.0, math.radians(20), math.radians(120)) <= tolerancia + 2e-4
    assert (round(arco[0][0] - (X0 + 40 + 12 * math.cos(math.radians(20))), 6), round(arco[-1][1] - (Y0 + 12 * math.sin(math.radians(140))), 6)) == (0.0, 0.0)   # extremos exactos
    assert (round(bulge[0][0] - (X0 + 80), 6), round(bulge[-1][0] - (X0 + 95), 6)) == (0.0, 0.0)


def test_elipse_y_spline_dentro_de_la_tolerancia_medida(tmp_path):
    from ezdxf.path import make_path

    doc = ezdxf.new("R2010")
    m = doc.modelspace()
    m.add_ellipse((X0, Y0), major_axis=(20, 0, 0), ratio=0.4)
    m.add_spline(fit_points=[(X0 + i * 6, Y0 + 30 + math.sin(i / 1.5) * 5) for i in range(12)])
    ruta = tmp_path / "e.dxf"
    doc.saveas(ruta)
    ds = gc.read_dxf(ruta, 13, "N", curve_tolerance=0.01)
    t = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(32613), always_xy=True)
    for entidad, feat in zip(ezdxf.readfile(ruta).modelspace(), ds.features):
        verdadera = LineString([(v.x, v.y) for v in make_path(entidad).approximate(segments=3000)])
        aprox = LineString([t.transform(lon, lat) for lon, lat, _ in feat.parts[0]])
        peor = max(aprox.distance(Point(*verdadera.coords[i])) for i in range(0, len(verdadera.coords), 3))
        assert peor <= 0.01 + 5e-4, (entidad.dxftype(), peor)


def test_la_tolerancia_cambia_la_cantidad_de_puntos(tmp_path):
    cuentas = []
    for t in (0.001, 0.01, 0.05, 0.25):
        ds = leer(tmp_path, lambda m: (m.add_circle((X0, Y0), 20), m.add_spline(fit_points=[(X0 + i * 6, Y0 + math.sin(i) * 5) for i in range(15)])), t)
        cuentas.append(sum(len(p) for f in ds.features for p in f.parts))
    assert cuentas == sorted(cuentas, reverse=True) and cuentas[0] > 2 * cuentas[-1]


# -- Escritura: precisión y tamaño ------------------------------------------------------------------------------

def dataset_con(parte, tipo="LineString", capa="L", nombre=""):
    return gc.GeoDataset(features=[gc.GeoFeature(tipo, [parte], capa, nombre)], source_name="t.kml")


def test_kml_escribe_ocho_decimales_sin_ceros_ni_altura_pegado_al_relieve(tmp_path):
    ds = dataset_con([(-106.123456789012, 28.5, 1420.0), (-106.1, 28.6123456789, 1420.5)])
    ruta = gc.write_kml(ds, tmp_path / "a.kml", clamp_to_ground=True)
    texto = ruta.read_text()
    assert "<coordinates>-106.12345679,28.5 -106.1,28.61234568</coordinates>" in texto
    ruta = gc.write_kml(ds, tmp_path / "b.kml", clamp_to_ground=False)
    assert "<coordinates>-106.12345679,28.5,1420 -106.1,28.61234568,1420.5</coordinates>" in ruta.read_text()
    assert gc._decimales(-0.000000001, 8) == "0" and gc._decimales(12.0, 3) == "12" and gc._decimales(0.0, 8) == "0"


def test_kml_ida_y_vuelta_dentro_de_un_milimetro(tmp_path):
    rng = random.Random(5)
    parte = [(-106.0 + rng.random() * 0.1, 28.0 + rng.random() * 0.1, 0.0) for _ in range(200)]
    ruta = gc.write_kml(dataset_con(parte), tmp_path / "a.kml")
    leido = gc.read_kml(ruta).features[0].parts[0]
    assert len(leido) == 200
    for (a, b, _), (c, d, _) in zip(parte, leido):
        assert abs(a - c) <= 5.1e-9 and abs(b - d) <= 5.1e-9                      # 8 decimales de grado ≈ 1 mm


def test_dxf_escribe_con_la_precision_de_la_fuente_no_con_diecisiete_digitos(tmp_path):
    parte = [(-106.0 + i * 1.23456789e-4, 28.0 + i * 7.6543219e-5, 0.0) for i in range(60)]
    ruta = gc.write_dxf(dataset_con(parte), tmp_path / "a.dxf", 13, "N", add_labels=False)
    poli = next(e for e in ezdxf.readfile(ruta).modelspace() if e.dxftype() == "LWPOLYLINE")
    valores = [v for p in poli.get_points("xy") for v in p]
    assert len(valores) == 120 and all(v == round(v, 4) for v in valores)         # 0.1 mm, como el KML de origen


def test_dxf_no_repite_vertices_que_el_redondeo_vuelve_iguales(tmp_path):
    parte = [(-106.0, 28.0, 0.0), (-106.0 + 1e-10, 28.0, 0.0), (-106.0 + 3e-4, 28.0, 0.0)]
    ruta = gc.write_dxf(dataset_con(parte), tmp_path / "a.dxf", 13, "N", add_labels=False)
    poli = [e for e in ezdxf.readfile(ruta).modelspace() if e.dxftype() == "LWPOLYLINE"]
    assert len(poli[0]) == 2
