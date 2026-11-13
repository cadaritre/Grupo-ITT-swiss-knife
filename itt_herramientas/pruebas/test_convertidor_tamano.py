"""Regresión de tamaño del convertidor: vértices y bytes de salida sobre archivos de muestra, con las cifras del escritorio como
referencia («antes», medidas con `python -m paridad.medir_convertidor` sobre la rama main) y las de la web como tope («después»)."""
from __future__ import annotations

import zipfile

import ezdxf
import pytest
from pyproj import CRS, Transformer

from itt_herramientas.dominio import geospatial_converter as gc
from paridad import medidas, muestras

# (vértices, bytes) del escritorio y de la web con la tolerancia por omisión (1 cm), KMZ para DXF y DXF para KML.
ANTES = {"curvas_de_nivel.dxf": (160_802, 1_385_419), "levantamiento_topografico.dxf": (15_030, 145_165),
         "planta_construccion.dxf": (7_154, 58_647), "predios.kml": (49_172, 4_909_979)}
DESPUES = {"curvas_de_nivel.dxf": (33_293, 247_090), "levantamiento_topografico.dxf": (15_030, 120_073),
           "planta_construccion.dxf": (2_634, 20_789), "predios.kml": (49_172, 3_796_412)}


@pytest.fixture(scope="module")
def convertidos(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("muestras_tamano")
    archivos = muestras.todas(carpeta)
    salidas = {}
    for nombre, ruta in archivos.items():
        ds = gc.read_geospatial(ruta, zone=13, hemisphere="N")
        destino = carpeta / (ruta.stem + (".kmz" if ruta.suffix == ".dxf" else ".dxf"))
        gc.convert_file(ds, destino, zone=13, hemisphere="N", add_labels=True, add_hatches=True, clamp_to_ground=True)
        salidas[nombre] = (medidas.medir_salida(destino), destino)
    return archivos, salidas


@pytest.mark.parametrize("nombre", sorted(DESPUES))
def test_la_salida_no_crece_sobre_lo_medido(convertidos, nombre):
    medida, _ = convertidos[1][nombre]
    vertices, tamano = DESPUES[nombre]
    assert medida["vertices"] <= vertices * 1.02, (nombre, medida["vertices"], vertices)
    assert medida["bytes"] <= tamano * 1.02, (nombre, medida["bytes"], tamano)


@pytest.mark.parametrize("nombre", sorted(ANTES))
def test_nunca_peor_que_el_escritorio(convertidos, nombre):
    medida, _ = convertidos[1][nombre]
    assert medida["vertices"] <= ANTES[nombre][0] and medida["bytes"] < ANTES[nombre][1]


def test_curvas_y_planta_bajan_mucho_y_las_rectas_no_ganan_vertices(convertidos):
    salidas = convertidos[1]
    assert salidas["curvas_de_nivel.dxf"][0]["vertices"] < ANTES["curvas_de_nivel.dxf"][0] * 0.30
    assert salidas["curvas_de_nivel.dxf"][0]["bytes"] < ANTES["curvas_de_nivel.dxf"][1] * 0.25
    assert salidas["planta_construccion.dxf"][0]["vertices"] < ANTES["planta_construccion.dxf"][0] * 0.45
    assert salidas["levantamiento_topografico.dxf"][0]["vertices"] == ANTES["levantamiento_topografico.dxf"][0]
    assert salidas["levantamiento_topografico.dxf"][0]["bytes"] < ANTES["levantamiento_topografico.dxf"][1] * 0.90


def test_los_vertices_originales_del_levantamiento_quedan_exactos_en_el_kml(convertidos):
    archivos, salidas = convertidos
    t = Transformer.from_crs(CRS.from_epsg(32613), CRS.from_epsg(4326), always_xy=True)
    esperados = []
    for e in ezdxf.readfile(archivos["levantamiento_topografico.dxf"]).modelspace():
        if e.dxftype() == "LWPOLYLINE":
            esperados.append([t.transform(x, y) for x, y in e.get_points("xy")])
        elif e.dxftype() == "POLYLINE":
            esperados.append([t.transform(v.dxf.location.x, v.dxf.location.y) for v in e.vertices])
        elif e.dxftype() == "LINE":
            esperados.append([t.transform(e.dxf.start.x, e.dxf.start.y), t.transform(e.dxf.end.x, e.dxf.end.y)])
    import re
    with zipfile.ZipFile(salidas["levantamiento_topografico.dxf"][1]) as zf:
        kml = zf.read("doc.kml").decode()
    lineas = [[tuple(float(v) for v in trio.split(",")[:2]) for trio in c.split()] for c in re.findall(r"<LineString>.*?<coordinates>([^<]*)</coordinates>", kml)]
    por_cuenta = lambda xs: sorted(xs, key=lambda l: (len(l), l[0]))
    assert len(lineas) == len(esperados)
    for hallada, esperada in zip(por_cuenta(lineas), por_cuenta(esperados)):
        assert len(hallada) == len(esperada)                                           # ni un vértice más ni uno menos
        assert all(abs(a[0] - b[0]) <= 5.1e-9 and abs(a[1] - b[1]) <= 5.1e-9 for a, b in zip(hallada, esperada))     # 8 decimales de grado
