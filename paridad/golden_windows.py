"""Archivos dorados del escritorio en Windows (control final de paridad).

Lo corre el propietario UNA VEZ en una PC con Windows. Genera, con el escritorio real (fuentes de Windows, su propia
versión de ReportLab), las salidas de los casos de paridad, para compararlas después con las de la web.

Qué hacer:
  1. Abre la carpeta del escritorio (la rama `main` de Grupo-ITT-swiss-knife, la que ya usas) y copia dentro la
     carpeta `paridad` de la rama `web` (basta con `casos.py`, `trabajador.py` y este archivo).
  2. En esa carpeta, con el Python del escritorio (el que tiene Pillow, ReportLab, ezdxf, numpy, Shapely, pyproj,
     pyspellchecker y wordfreq), ejecuta:

         python paridad\\golden_windows.py

  3. Entrega el archivo `golden_windows_AAAAMMDD.zip` que aparece en la carpeta `paridad` (no contiene datos tuyos:
     solo las salidas de las entradas de prueba).

No abre ventanas, no usa internet y no toca tu carpeta `Documentos`: la carpeta de datos del escritorio se redirige a
un directorio temporal que se borra al terminar.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

AQUI = Path(__file__).resolve().parent


def _sha256(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _commit(escritorio: Path) -> str:
    try:
        return subprocess.run(["git", "-C", str(escritorio), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "desconocido"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Genera los archivos dorados del escritorio en esta PC.")
    ap.add_argument("--escritorio", type=Path, default=AQUI.parent, help="carpeta del escritorio (la que contiene photo_report_app)")
    ap.add_argument("--salida", type=Path, default=AQUI, help="dónde dejar el .zip")
    args = ap.parse_args(argv)
    escritorio = args.escritorio.resolve()
    if not (escritorio / "photo_report_app").is_dir():
        print(f"No encuentro photo_report_app en {escritorio}.\nCopia la carpeta paridad dentro de la carpeta del escritorio o usa --escritorio.", file=sys.stderr)
        return 2
    faltan = []
    for modulo in ("PIL", "reportlab", "ezdxf", "numpy", "shapely", "pyproj", "spellchecker", "wordfreq"):
        try:
            __import__(modulo)
        except ImportError:
            faltan.append(modulo)
    if faltan:
        print("Faltan paquetes de Python: " + ", ".join(faltan) + "\nInstálalos con: python -m pip install -r requirements.txt", file=sys.stderr)
        return 2

    sys.path.insert(0, str(AQUI))
    import casos

    trabajo = args.salida / "_trabajo_dorados"
    if trabajo.exists():
        shutil.rmtree(trabajo)
    entradas = trabajo / "entradas"
    casos.generar_entradas(entradas)
    shutil.copyfile(escritorio / "assets" / "logo.png", entradas / "logo_grupo_itt.png")
    shutil.copyfile(escritorio / "assets" / "logo_tresvizo.png", entradas / "logo_tresvizo.png")

    salida = trabajo / "escritorio"
    entorno = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    entorno.update({"PYTHONHASHSEED": "0", "TZ": "UTC", "RL_invariant": "1", "PYTHONUTF8": "1"})
    print("Corriendo los casos con el escritorio real (tarda uno o dos minutos)...")
    proceso = subprocess.run(
        [sys.executable, "-s", str(AQUI / "trabajador.py"), "--lado", "escritorio", "--raiz", str(escritorio),
         "--entradas", str(entradas), "--salida", str(salida)],
        env=entorno, capture_output=True, text=True,
    )
    sys.stdout.write(proceso.stdout)
    if proceso.returncode != 0:
        sys.stderr.write(proceso.stderr)
        print("\nEl escritorio falló. Envía el texto de arriba.", file=sys.stderr)
        return 1
    if proceso.stderr.strip():
        print("Avisos de casos (también cuentan como resultado y se comparan con la web):")
        sys.stderr.write(proceso.stderr)

    resultados = json.loads((salida / "resultados.json").read_text(encoding="utf-8"))
    archivos = {str(p.relative_to(trabajo)).replace("\\", "/"): _sha256(p) for p in sorted(trabajo.rglob("*")) if p.is_file()}
    manifiesto = {
        "creado": date.today().isoformat(),
        "sistema": platform.platform(), "python": platform.python_version(), "maquina": platform.machine(),
        "commit_escritorio": _commit(escritorio),
        "arial_instalada": Path("C:/Windows/Fonts/arial.ttf").exists(),
        "casos": {nombre: {"artefactos": r["artefactos"], "error": r["error"]} for nombre, r in resultados.items()},
        "sha256": archivos,
    }
    (trabajo / "manifiesto.json").write_text(json.dumps(manifiesto, indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    zip_ruta = args.salida / f"golden_windows_{date.today():%Y%m%d}.zip"
    with zipfile.ZipFile(zip_ruta, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(trabajo.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(trabajo).as_posix())
    shutil.rmtree(trabajo, ignore_errors=True)
    print(f"\nListo: {zip_ruta}")
    print(f"Casos: {len(resultados)}; con error: {sum(1 for r in resultados.values() if r['error'])}; Arial instalada: {manifiesto['arial_instalada']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
