"""Ejecutor de paridad: pasa las mismas entradas por el escritorio (rama `main`) y por la web y compara.

El escritorio se extrae con `git archive` a un directorio temporal (sin *worktrees* y sin tocar `main`). Cada lado
corre en su propio proceso (`trabajador.py`), con la carpeta de datos del escritorio aislada.

Uso desde la raíz del repositorio web:

    python -m paridad.ejecutor                  # compara contra la rama main
    python -m paridad.ejecutor --ref main --salida /tmp/paridad --mantener

o, con pytest: `pytest paridad`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
AQUI = Path(__file__).resolve().parent
FUENTES = RAIZ / "itt_herramientas" / "dominio" / "fuentes"


class ParidadError(RuntimeError):
    pass


def extraer_rama(ref: str, destino: Path, repo: Path = RAIZ) -> Path:
    """`git archive <ref> | tar -x` en `destino`. No cambia de rama ni crea *worktrees*."""
    destino.mkdir(parents=True, exist_ok=True)
    existe = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], capture_output=True, text=True)
    if existe.returncode != 0:
        raise ParidadError(f"No existe la referencia «{ref}» en {repo}")
    archivo = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", ref], capture_output=True, check=True)
    with tarfile.open(fileobj=__import__("io").BytesIO(archivo.stdout)) as tar:
        tar.extractall(destino, filter="data")
    if not (destino / "photo_report_app").is_dir():
        raise ParidadError(f"«{ref}» no contiene photo_report_app: no es la rama del escritorio")
    return destino


def preparar_entradas(raiz_escritorio: Path, destino: Path) -> Path:
    """Genera las entradas comunes y copia los logos del escritorio (lo que debe coincidir con los de la web)."""
    sys.path.insert(0, str(AQUI))
    try:
        import casos
        casos.generar_entradas(destino)
    finally:
        sys.path.remove(str(AQUI))
    volcado = subprocess.run([sys.executable, "-s", str(AQUI / "trabajador.py"), "--volcar-personales", str(raiz_escritorio), str(destino / "personales.json")],
                             capture_output=True, text=True, env=_entorno())
    if volcado.returncode != 0:
        raise ParidadError(f"No pude leer los datos de fábrica del escritorio:\n{volcado.stderr[-2000:]}")
    shutil.copyfile(raiz_escritorio / "assets" / "logo.png", destino / "logo_grupo_itt.png")
    shutil.copyfile(raiz_escritorio / "assets" / "logo_tresvizo.png", destino / "logo_tresvizo.png")
    return destino


def _entorno() -> dict[str, str]:
    entorno = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    entorno.update({"PYTHONHASHSEED": "0", "TZ": "UTC", "RL_invariant": "1", "LC_ALL": "C.UTF-8", "PYTHONUTF8": "1"})
    return entorno


def correr_lado(lado: str, raiz: Path, entradas: Path, salida: Path, fuentes: Path | None = None, timeout: int = 1200) -> Path:
    comando = [sys.executable, "-s", str(AQUI / "trabajador.py"), "--lado", lado, "--raiz", str(raiz), "--entradas", str(entradas), "--salida", str(salida)]
    if fuentes is not None:
        comando += ["--fuentes", str(fuentes)]
    proceso = subprocess.run(comando, capture_output=True, text=True, env=_entorno(), timeout=timeout)
    if proceso.returncode != 0:
        raise ParidadError(f"El trabajador «{lado}» falló ({proceso.returncode}):\n{proceso.stderr[-3000:]}")
    return salida


@dataclass
class Corrida:
    web: Path
    escritorio: Path
    entradas: Path
    raiz_escritorio: Path


def correr_ambos(ref: str, trabajo: Path, repo: Path = RAIZ, fuentes_liberation: bool = True) -> Corrida:
    trabajo.mkdir(parents=True, exist_ok=True)
    escritorio = extraer_rama(ref, trabajo / "escritorio_fuente", repo)
    entradas = preparar_entradas(escritorio, trabajo / "entradas")
    web = correr_lado("web", repo, entradas, trabajo / "salida_web")
    esc = correr_lado("escritorio", escritorio, entradas, trabajo / "salida_escritorio", FUENTES if fuentes_liberation else None)
    return Corrida(web, esc, entradas, escritorio)


def comparar_caso(caso: str, web: Path, escritorio: Path, tolerante: bool = False) -> list[tuple[str, object]]:
    """Devuelve [(artefacto, Resultado)] de un caso; los errores del caso cuentan como un artefacto más."""
    sys.path.insert(0, str(AQUI))
    try:
        import comparadores as C
    finally:
        sys.path.remove(str(AQUI))
    rw = json.loads((web / "resultados.json").read_text(encoding="utf-8"))[caso]
    re_ = json.loads((escritorio / "resultados.json").read_text(encoding="utf-8"))[caso]
    salida: list[tuple[str, object]] = []
    if rw["error"] != re_["error"]:
        salida.append(("<error>", C.Resultado(C.DISTINTO, f"web={rw['error']} escritorio={re_['error']}")))
        return salida
    if rw["artefactos"] != re_["artefactos"]:
        salida.append(("<artefactos>", C.Resultado(C.DISTINTO, f"web={rw['artefactos']} escritorio={re_['artefactos']}")))
        return salida
    geometrica = caso.split("/", 1)[-1].startswith("convertidor_")        # el convertidor mejoró a propósito: se compara la geometría, no los bytes
    for nombre in rw["artefactos"]:
        a, b = web / caso / nombre, escritorio / caso / nombre
        if geometrica and a.suffix.lower() in {".kml", ".kmz", ".dxf"}:
            salida.append((nombre, C.comparar_geometria(a, b)))
        else:
            salida.append((nombre, C.comparar(a, b, tolerante)))
    return salida


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", default="main", help="rama o commit del escritorio (por omisión main)")
    ap.add_argument("--salida", type=Path, default=None, help="carpeta de trabajo (por omisión una temporal)")
    ap.add_argument("--mantener", action="store_true", help="no borrar la carpeta de trabajo")
    ap.add_argument("--tolerante", action="store_true", help="aceptar diferencias de tipografía en PDF e imágenes")
    args = ap.parse_args(argv)
    trabajo = args.salida or Path(tempfile.mkdtemp(prefix="paridad_"))
    try:
        corrida = correr_ambos(args.ref, trabajo)
        sys.path.insert(0, str(AQUI))
        import casos
        malos = 0
        for caso in casos.claves():
            for artefacto, resultado in comparar_caso(caso, corrida.web, corrida.escritorio, args.tolerante):
                marca = "ok " if resultado.ok else "MAL"
                malos += 0 if resultado.ok else 1
                print(f"{marca} {caso}/{artefacto}: {resultado.nivel}" + (f" ({resultado.motivo})" if resultado.motivo else ""))
        print(f"\n{'PARIDAD COMPLETA' if not malos else f'{malos} diferencias'}")
        return 1 if malos else 0
    finally:
        if not args.mantener and not args.salida:
            shutil.rmtree(trabajo, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
