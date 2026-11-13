"""Archivos de muestra del convertidor, parecidos a los de trabajo: un levantamiento topográfico, una planta de construcción,
curvas de nivel y un KML de predios. Deterministas (semilla fija) y de tamaño moderado.

Sirven para medir cuántos vértices y cuántos bytes produce el convertidor (escritorio contra web) y para las pruebas de
regresión de tamaño.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

X0, Y0 = 369_800.0, 3_169_700.0          # Chihuahua, UTM 13N


def levantamiento_topografico(destino: Path) -> Path:
    import ezdxf

    r = random.Random(11)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(1800):                                           # puntos con su cota escrita
        x, y = X0 + r.uniform(0, 600), Y0 + r.uniform(0, 500)
        z = 1420 + 6 * math.sin(x / 80) + 4 * math.cos(y / 60)
        msp.add_point((round(x, 3), round(y, 3), round(z, 3)), dxfattribs={"layer": "PUNTOS"})
        if i % 3 == 0:
            msp.add_text(f"{z:.2f}", dxfattribs={"layer": "COTAS", "insert": (round(x + 0.5, 3), round(y + 0.5, 3)), "height": 0.8})
    for k in range(60):                                             # bardas y límites: polilíneas rectas con muchos vértices
        n = r.randint(40, 250)
        x, y = X0 + r.uniform(0, 500), Y0 + r.uniform(0, 400)
        pts = []
        for i in range(n):
            x += r.uniform(0.5, 4.0)
            y += r.uniform(-1.5, 1.5)
            pts.append((round(x, 3), round(y, 3)))
        msp.add_lwpolyline(pts, dxfattribs={"layer": "BARDAS" if k % 2 else "LIMITE"})
    for k in range(25):                                             # perfiles en 3D
        n = r.randint(60, 200)
        x, y = X0 + r.uniform(0, 500), Y0 + r.uniform(0, 400)
        pts = []
        for i in range(n):
            x += r.uniform(1.0, 3.0)
            pts.append((round(x, 3), round(y + math.sin(i / 7) * 2, 3), round(1420 + i * 0.03, 3)))
        msp.add_polyline3d(pts, dxfattribs={"layer": "PERFILES"})
    for i in range(300):                                            # cuadrícula
        x = X0 + (i % 20) * 30
        y = Y0 + (i // 20) * 30
        msp.add_line((x, y, 0), (x + 30, y, 0), dxfattribs={"layer": "CUADRICULA"})
    ruta = destino / "levantamiento_topografico.dxf"
    doc.saveas(ruta)
    return ruta


def planta_construccion(destino: Path) -> Path:
    import ezdxf

    r = random.Random(12)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for i in range(400):
        x, y = X0 + r.uniform(0, 80), Y0 + r.uniform(0, 60)
        if i % 2:
            msp.add_line((x, y), (x + r.choice([3, 4.5, 6, 8]), y), dxfattribs={"layer": "MUROS"})
        else:
            msp.add_line((x, y), (x, y + r.choice([3, 4.5, 6, 8])), dxfattribs={"layer": "MUROS"})
    for i in range(120):
        x, y = X0 + (i % 12) * 7, Y0 + (i // 12) * 6
        msp.add_lwpolyline([(x, y), (x + 6, y), (x + 6, y + 5), (x, y + 5)], close=True, dxfattribs={"layer": "CUARTOS"})
    for i in range(80):
        msp.add_circle((X0 + (i % 10) * 8, Y0 + (i // 10) * 7), r.choice([0.2, 0.3, 0.45]), dxfattribs={"layer": "COLUMNAS"})
    for i in range(60):
        msp.add_arc((X0 + (i % 12) * 7 + 3, Y0 + (i // 12) * 6), 0.9, 0, 90, dxfattribs={"layer": "PUERTAS"})
    for i in range(40):
        x, y = X0 + (i % 8) * 9, Y0 + 70 + (i // 8) * 8
        msp.add_lwpolyline([(x, y, 0, 0, 0.41421356), (x + 5, y, 0, 0, 0), (x + 5, y + 4, 0, 0, 0.41421356), (x, y + 4, 0, 0, 0)], format="xyseb", close=True, dxfattribs={"layer": "ESQUINAS"})
    for i in range(6):
        msp.add_ellipse((X0 + i * 12, Y0 + 100), major_axis=(4, 0, 0), ratio=0.6, dxfattribs={"layer": "JARDINERAS"})
    ruta = destino / "planta_construccion.dxf"
    doc.saveas(ruta)
    return ruta


def curvas_de_nivel(destino: Path) -> Path:
    import ezdxf

    r = random.Random(13)
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    for k in range(250):                                            # curvas de nivel como splines por puntos
        n = r.randint(25, 70)
        cx, cy, rad = X0 + r.uniform(50, 450), Y0 + r.uniform(50, 350), r.uniform(15, 90)
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            rr = rad * (1 + 0.15 * math.sin(3 * a + k))
            pts.append((round(cx + rr * math.cos(a), 3), round(cy + rr * math.sin(a), 3), 1400 + (k % 40)))
        msp.add_spline(fit_points=pts, dxfattribs={"layer": f"CURVAS_{1400 + (k % 40)}"})
    for k in range(80):                                             # curvas ya en polilínea 3D
        n = r.randint(120, 400)
        cx, cy, rad = X0 + r.uniform(50, 450), Y0 + r.uniform(50, 350), r.uniform(20, 100)
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            rr = rad * (1 + 0.1 * math.sin(4 * a + k))
            pts.append((round(cx + rr * math.cos(a), 3), round(cy + rr * math.sin(a), 3), 1400 + (k % 40)))
        msp.add_polyline3d(pts, dxfattribs={"layer": "CURVAS_POLILINEA"})
    ruta = destino / "curvas_de_nivel.dxf"
    doc.saveas(ruta)
    return ruta


def predios_kml(destino: Path) -> Path:
    r = random.Random(14)
    filas = []
    for k in range(120):
        n = r.randint(80, 600)
        lon0, lat0, rad = -106.07 + r.uniform(0, 0.05), 28.62 + r.uniform(0, 0.05), r.uniform(0.0005, 0.004)
        pts = []
        for i in range(n):
            a = 2 * math.pi * i / n
            rr = rad * (1 + 0.05 * math.sin(5 * a + k))
            pts.append(f"{lon0 + rr * math.cos(a):.9f},{lat0 + rr * math.sin(a) * 0.9:.9f},0")
        pts.append(pts[0])
        filas.append(f"<Placemark><name>Predio {k + 1}</name><Polygon><outerBoundaryIs><LinearRing><coordinates>{' '.join(pts)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    for k in range(30):
        pts = " ".join(f"{-106.07 + i * 0.0003:.9f},{28.62 + k * 0.0007 + (i % 5) * 0.00001:.9f},{1400 + i * 0.1:.3f}" for i in range(200))
        filas.append(f"<Placemark><name>Eje {k + 1}</name><LineString><coordinates>{pts}</coordinates></LineString></Placemark>")
    texto = '<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder><name>Predios</name>' + "".join(filas) + "</Folder></Document></kml>"
    ruta = destino / "predios.kml"
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def todas(destino: Path) -> dict[str, Path]:
    destino.mkdir(parents=True, exist_ok=True)
    return {p.name: p for p in (levantamiento_topografico(destino), planta_construccion(destino), curvas_de_nivel(destino), predios_kml(destino))}
