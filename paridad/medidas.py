"""Medidas de salida del convertidor: vértices y bytes, y el resumen de un lado (escritorio o web) sobre las muestras.

`python -m paridad.medir_convertidor` imprime la tabla escritorio contra web.
"""
from __future__ import annotations

import json
import re
import sys
import zipfile
from pathlib import Path

_COORDENADAS = re.compile(r"<coordinates>([^<]*)</coordinates>")


def vertices_kml(datos: bytes) -> int:
    texto = datos.decode("utf-8")
    return sum(len(c.split()) for c in _COORDENADAS.findall(texto))


def vertices_dxf(ruta: Path) -> dict[str, int]:
    """Vértices por clase en un DXF: líneas (polilíneas), puntos y rellenos (hatch)."""
    import ezdxf

    doc = ezdxf.readfile(ruta)
    cuenta = {"polilineas": 0, "puntos": 0, "rellenos": 0, "entidades": 0}
    for e in doc.modelspace():
        cuenta["entidades"] += 1
        t = e.dxftype()
        if t == "LWPOLYLINE":
            cuenta["polilineas"] += len(e)
        elif t == "POLYLINE":
            cuenta["polilineas"] += len(list(e.vertices))
        elif t == "POINT":
            cuenta["puntos"] += 1
        elif t == "HATCH":
            for camino in e.paths:
                cuenta["rellenos"] += len(getattr(camino, "vertices", []) or [])
    return cuenta


def medir_salida(ruta: Path) -> dict:
    """Vértices y bytes del archivo de salida (kml, kmz o dxf)."""
    sufijo = ruta.suffix.lower()
    if sufijo == ".kmz":
        with zipfile.ZipFile(ruta) as zf:
            kml = zf.read("doc.kml")
        return {"vertices": vertices_kml(kml), "bytes": ruta.stat().st_size, "bytes_kml": len(kml)}
    if sufijo == ".kml":
        return {"vertices": vertices_kml(ruta.read_bytes()), "bytes": ruta.stat().st_size}
    d = vertices_dxf(ruta)
    return {"vertices": d["polilineas"] + d["puntos"], "vertices_rellenos": d["rellenos"], "entidades": d["entidades"], "bytes": ruta.stat().st_size}


def correr_lado(lado: str, raiz: Path, muestras: Path, salida: Path) -> dict:
    """Convierte las muestras con el dominio de `raiz` (escritorio: photo_report_app; web: itt_herramientas.dominio)."""
    import importlib

    sys.path.insert(0, str(raiz))
    paquete = "photo_report_app" if lado == "escritorio" else "itt_herramientas.dominio"
    gc = importlib.import_module(f"{paquete}.geospatial_converter")
    salida.mkdir(parents=True, exist_ok=True)
    resultado = {}
    for nombre in sorted(p.name for p in muestras.iterdir()):
        ruta = muestras / nombre
        if ruta.suffix == ".dxf":
            if lado == "escritorio":
                ds = gc.read_geospatial(ruta, zone=13, hemisphere="N", curve_tolerance=0.5)       # el valor por omisión del escritorio
            else:
                ds = gc.read_geospatial(ruta, zone=13, hemisphere="N")
            destino = salida / f"{ruta.stem}.kmz"
            gc.convert_file(ds, destino, zone=13, hemisphere="N", add_labels=True, add_hatches=True, clamp_to_ground=True)
        else:
            ds = gc.read_geospatial(ruta)
            destino = salida / f"{ruta.stem}.dxf"
            gc.convert_file(ds, destino, zone=13, hemisphere="N", add_labels=True, add_hatches=True)
        resultado[nombre] = {"entrada_bytes": ruta.stat().st_size, "elementos": len(ds.features), **medir_salida(destino)}
    return resultado


if __name__ == "__main__":                      # uso interno: python medidas.py <lado> <raiz> <muestras> <salida>
    lado, raiz, muestras, salida = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    print(json.dumps(correr_lado(lado, raiz, muestras, salida)))
