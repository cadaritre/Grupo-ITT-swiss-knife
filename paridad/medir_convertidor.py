"""Mide el convertidor, escritorio contra web, con archivos de muestra: vértices y bytes de cada salida.

    python -m paridad.medir_convertidor [--ref main]
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import ejecutor, muestras


def medir(ref: str = "main", trabajo: Path | None = None) -> dict:
    trabajo = trabajo or Path(tempfile.mkdtemp(prefix="medir_conv_"))
    escritorio = ejecutor.extraer_rama(ref, trabajo / "escritorio_fuente")
    archivos = trabajo / "muestras"
    muestras.todas(archivos)
    temporal = trabajo / "casa"
    temporal.mkdir()
    entorno = ejecutor._entorno()
    entorno.update({"HOME": str(temporal), "USERPROFILE": str(temporal)})
    (temporal / "Documents").mkdir()
    salida = {}
    for lado, raiz in (("escritorio", escritorio), ("web", ejecutor.RAIZ)):
        proceso = subprocess.run([sys.executable, "-s", str(ejecutor.AQUI / "medidas.py"), lado, str(raiz), str(archivos), str(trabajo / f"salida_{lado}")],
                                 capture_output=True, text=True, env=entorno, cwd=str(trabajo))
        if proceso.returncode != 0:
            raise RuntimeError(proceso.stderr[-2000:])
        salida[lado] = json.loads(proceso.stdout.strip().splitlines()[-1])
    return salida


def tabla(datos: dict) -> str:
    filas = ["| Muestra | Elementos | Vértices (escritorio → web) | Bytes de salida (escritorio → web) | Rellenos (vértices) |", "| --- | --- | --- | --- | --- |"]
    for nombre in sorted(datos["escritorio"]):
        a, b = datos["escritorio"][nombre], datos["web"][nombre]
        pct = lambda x, y: f"{(y - x) / x:+.0%}" if x else "—"
        rellenos = f"{a.get('vertices_rellenos', 0)} → {b.get('vertices_rellenos', 0)}" if "vertices_rellenos" in a else "—"
        filas.append(f"| {nombre} | {a['elementos']:,} → {b['elementos']:,} | {a['vertices']:,} → {b['vertices']:,} ({pct(a['vertices'], b['vertices'])}) | "
                     f"{a['bytes']:,} → {b['bytes']:,} ({pct(a['bytes'], b['bytes'])}) | {rellenos} |")
    return "\n".join(filas)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="main")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    trabajo = Path(tempfile.mkdtemp(prefix="medir_conv_"))
    try:
        datos = medir(args.ref, trabajo)
        print(json.dumps(datos, indent=1) if args.json else tabla(datos))
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
