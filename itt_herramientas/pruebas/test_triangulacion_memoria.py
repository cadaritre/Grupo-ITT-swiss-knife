"""Regresión de memoria de la triangulación EN EL LÍMITE de la web (75 000 puntos, ~150 000 triángulos).

Cada paso corre en un proceso nuevo (el pico de memoria de un proceso solo sube) y se compara con el tope de la cola `herramientas`, que sale del ajuste
`HERRAMIENTAS_COLA_MEMORIA_MB` (1024 MB en el servidor de 8 GB; 768 MB antes).
El proceso de la prueba mide solo Python + NumPy + ezdxf + dominio; a lo medido se le suman ~100 MB del trabajador real. Los números de referencia están en docs/pwa.md.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SOBRECOSTO_TRABAJADOR_MB = 100        # Django, la base y tvplataforma que el proceso de medida no carga (estimado)
PUNTOS = 75_000
RAIZ = Path(__file__).resolve().parents[2]

COMUN = f"""
import math, random, resource, sys
sys.path.insert(0, {str(RAIZ)!r})
import numpy as np
from itt_herramientas.dominio import triangulation as tri
def pico_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 1e6 if sys.platform == "darwin" else r / 1e3
"""

PASOS = {
    "tin": "m = tri.create_tin_model(RUTA, max_points=%d); np.asarray(m.triangles)" % PUNTOS,
    "dxf_tin": "T, P = cargar(); tri.write_tin_dxf(tri.TinModel('x', P, T, tri.TinStats()), SALIDA)",
    "dxf_pendientes": "T, P = cargar(); tri.write_slope_dxf(tri.analyze_slopes(T, tri.clone_default_ranges(), 'x'), SALIDA)",
    "dxf_escurrimiento": "T, P = cargar(); tri.write_flow_dxf(tri.analyze_flow(T, tri.clone_default_flow_ranges(), 'x'), SALIDA)",
}


def correr(codigo: str, **entorno) -> float:
    programa = COMUN + textwrap.dedent(f"""
        RUTA, NPZ, SALIDA = {entorno['ruta']!r}, {entorno['npz']!r}, {entorno['salida']!r}
        def cargar():
            d = np.load(NPZ)
            return [(tuple(a), tuple(b), tuple(c)) for a, b, c in d["triangulos"].tolist()], [tuple(p) for p in d["puntos"].tolist()]
        {codigo}
        print("PICO", pico_mb())
    """)
    r = subprocess.run([sys.executable, "-c", programa], capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-800:]
    return float(r.stdout.rsplit("PICO", 1)[1])


@pytest.fixture(scope="module")
def entradas(tmp_path_factory):
    carpeta = tmp_path_factory.mktemp("memoria_triangulacion")
    ruta, npz = carpeta / "limite.dxf", carpeta / "superficie.npz"
    correr(f"""
import ezdxf
rnd = random.Random(1)
doc = ezdxf.new("R2010"); msp = doc.modelspace()
for i in range({PUNTOS}):
    x = 370000 + rnd.uniform(0, 2000); y = 3170000 + rnd.uniform(0, 2000)
    msp.add_point((x, y, 1400 + 30 * math.sin(x / 300) + 20 * math.cos(y / 250)))
doc.saveas(RUTA)
m = tri.create_tin_model(RUTA)
np.savez_compressed(NPZ, triangulos=np.asarray(m.triangles), puntos=np.asarray(m.points))
""".replace("\n", "\n        "), ruta=str(ruta), npz=str(npz), salida=str(carpeta / "x.dxf"))
    return {"ruta": str(ruta), "npz": str(npz), "salida": str(carpeta / "salida.dxf")}


@pytest.mark.parametrize("paso", ["tin", "dxf_tin", "dxf_pendientes", "dxf_escurrimiento"])
def test_pico_de_memoria_en_el_limite_de_la_web(paso, entradas, settings):
    tope = settings.HERRAMIENTAS_COLA_MEMORIA_MB
    pico = correr(PASOS[paso], **entradas) + SOBRECOSTO_TRABAJADOR_MB
    print(f"{paso}: {pico:.0f} MB (tope {tope} MB)")
    assert pico < tope, f"{paso}: {pico:.0f} MB con el sobrecosto del trabajador (tope {tope} MB)"
