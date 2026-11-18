"""El lector TIFF de «Carpeta del proyecto» (static/herramientas/cad/local/tiff.js) con TIFF sintéticos de cada disposición y compresión.

Corre el lector en Node (sin navegador): TIFF y BigTIFF, II y MM, mosaicos y tiras, un plano y planos separados, ninguna compresión,
LZW, Deflate y PackBits, predictor horizontal, gris, RGB, RGBA, 16 bits, vistas generales internas y etiquetas GeoTIFF. JPEG en TIFF
necesita el decodificador del navegador: lo prueba test_cad_local_navegador.py.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from itt_herramientas.pruebas_navegador import cad_local_rasters as r

AQUI = Path(__file__).resolve().parent
TIFF_JS = AQUI.parent / "static" / "herramientas" / "cad" / "local" / "tiff.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")

CASOS = [
    ("tiras_sin", dict(filas_tira=7)),
    ("tiras_lzw", dict(filas_tira=16, compresion="lzw")),
    ("tiras_deflate_predictor", dict(filas_tira=32, compresion="deflate", predictor=2)),
    ("tiras_packbits", dict(filas_tira=5, compresion="packbits")),
    ("mosaico_sin", dict(mosaico=64)),
    ("mosaico_lzw_predictor", dict(mosaico=128, compresion="lzw", predictor=2)),
    ("mosaico_deflate", dict(mosaico=256, compresion="deflate")),
    ("mosaico_planos", dict(mosaico=64, planar=2, compresion="deflate")),
    ("tiras_planos_lzw", dict(filas_tira=9, planar=2, compresion="lzw")),
    ("rgba", dict(mosaico=64, alfa=True, compresion="lzw")),
    ("gris", dict(filas_tira=11, spp=1, compresion="deflate")),
    ("mm", dict(mosaico=64, mm=True, compresion="lzw")),
    ("bigtiff_mosaico", dict(mosaico=128, grande=True, compresion="deflate")),
    ("bigtiff_mm_tiras", dict(filas_tira=13, grande=True, mm=True)),
    ("bits16", dict(mosaico=64, bits=16)),
    ("bits16_predictor", dict(filas_tira=8, bits=16, compresion="lzw", predictor=2)),
    ("vistas_internas", dict(mosaico=64, compresion="deflate", vistas=2)),
    ("geotiff_area", dict(mosaico=64, geo=r.geo_utm(389000.0, 3169000.0, 0.1))),
    ("geotiff_punto", dict(filas_tira=16, geo=r.geo_utm(389000.05, 3168999.95, 0.1, epsg=6372, puntual=True))),
]


def test_lector_tiff_en_cada_disposicion(tmp_path):
    casos = []
    for nombre, kw in CASOS:
        ruta = r.tiff(tmp_path / f"{nombre}.tif", 300, 211, **kw)
        geo = kw.get("geo")
        casos.append({
            "nombre": nombre, "ruta": str(ruta), "ancho": 300, "alto": 211, "spp": kw.get("spp", 3), "alfa": kw.get("alfa", False),
            "vistas": kw.get("vistas", 0),
            "geo": {"epsg": geo[34735][-1], "puntual": geo[34735][11] == 2, "x": geo[33922][3]} if geo else None,
        })
    (tmp_path / "casos.json").write_text(json.dumps(casos))
    salida = subprocess.run(["node", str(AQUI / "js" / "cad_local_tiff.mjs"), str(TIFF_JS), str(tmp_path / "casos.json")],
                            capture_output=True, text=True, timeout=120)
    assert salida.returncode == 0, salida.stderr
    resultados = [json.loads(x) for x in salida.stdout.splitlines() if x.startswith("{")]
    malos = [x for x in resultados if not x["ok"]]
    assert len(resultados) == len(CASOS) and not malos, malos or salida.stderr


def test_reglas_para_resolver_referencias():
    """Ruta relativa más larga primero, luego nombre; mayúsculas, rutas de Windows, Mac y de red; ambigüedades (carpeta.js)."""
    js = AQUI.parent / "static" / "herramientas" / "cad" / "local" / "carpeta.js"
    salida = subprocess.run(["node", str(AQUI / "js" / "cad_local_carpeta.mjs"), str(js)], capture_output=True, text=True, timeout=60)
    assert salida.returncode == 0 and salida.stdout.strip() == "ok", salida.stderr
