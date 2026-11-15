#!/usr/bin/env python3
"""Prepara el editor CAD (compilación web de Open CAD Studio) y el motor nativo de Linux, fijados en `upstream.json`.

Solo biblioteca estándar: lo usan el desarrollo, las pruebas y `despliegue/preparar-release.sh` del portal (en la Mac, sin el entorno de
la app). Nunca se guarda el paquete del editor en Git: se baja de la publicación oficial y se comprueba su SHA-256.

    python3 preparar.py editor --destino DIR          DIR/<versión>/ con el editor listo para servir (y sus .gz)
    python3 preparar.py nativo --arch x86_64 --destino DIR
    python3 preparar.py verificar --destino DIR       comprueba DIR/<versión>/MANIFIESTO.json

Cada pieza sale de su «fuente» en upstream.json: la publicación oficial (descarga directa) o una publicación del repositorio privado de
nuestra versión corregida del editor (`gh release download` con la sesión de gh de la Mac; ningún token va al servidor).
Actualizar: cambia versión, fuente, nombres, tamaños y SHA-256 en `upstream.json` y vuelve a correr las pruebas (las del navegador abren,
editan y guardan un plano con el editor real).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PROPIOS = ("index.html", "arranque.js", "editor.css")
#: Del paquete oficial no se sirven: su página (scripts en línea y analítica de terceros), su service worker y su manifiesto.
QUITAR = ("index.html", "sw.js", "app.webmanifest", ".trunk-lock", "fonts/generate.sh")
#: La página de inicio del editor lista videos y conversaciones de internet (miniaturas de YouTube): se sirven vacías.
VACIOS = ("videos.json", "discussions.json", "supporters.json")
COMPRIMIR = (".js", ".wasm", ".json", ".svg", ".ttf", ".html", ".css")


def datos() -> dict:
    return json.loads((AQUI / "upstream.json").read_text(encoding="utf-8"))


def _sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            h.update(trozo)
    return h.hexdigest()


def cache_por_omision() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "tresvizo-cad"


def _fuente(pieza: dict | None = None) -> dict:
    """De dónde se baja una pieza: la suya, si la trae, o la general de `upstream.json`."""
    f = dict((pieza or {}).get("fuente") or datos()["fuente"])
    if f.get("tipo") not in ("publica", "gh") or not re.fullmatch(r"[\w.-]+/[\w.-]+", f.get("repo", "")) or not re.fullmatch(r"[\w.+-]+", f.get("tag", "")):
        sys.exit(f"FALLA: fuente inválida en upstream.json: {f}")
    return f


def _bajar(fuente: dict, archivo: str, temporal: Path) -> None:
    if fuente["tipo"] == "publica":
        url = f"https://github.com/{fuente['repo']}/releases/download/{fuente['tag']}/{archivo}"
        print(f"Bajando {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=60) as r, open(temporal, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        return
    # Repositorio privado: con la sesión de gh de la Mac. Ningún token sale de ella (el servidor recibe el archivo en el paquete).
    if not shutil.which("gh"):
        sys.exit(f"FALLA: {archivo} está en el repositorio privado {fuente['repo']}: hace falta gh con sesión iniciada.")
    print(f"Bajando {archivo} de {fuente['repo']} {fuente['tag']} (gh)", file=sys.stderr)
    with tempfile.TemporaryDirectory(dir=temporal.parent) as d:
        r = subprocess.run(["gh", "release", "download", fuente["tag"], "-R", fuente["repo"], "-p", archivo, "-D", d], capture_output=True, text=True)
        bajado = Path(d) / archivo
        if r.returncode != 0 or not bajado.is_file():
            sys.exit(f"FALLA: gh no pudo bajar {archivo} de {fuente['repo']} {fuente['tag']}: {(r.stderr or r.stdout).strip()[-300:]}")
        os.replace(bajado, temporal)


def obtener(pieza: dict, cache: Path, local: Path | None = None) -> Path:
    """El archivo de la pieza (`{archivo, sha256, bytes[, fuente]}`), comprobado. Usa `local` o la caché si ya está; si no, lo baja de su fuente."""
    archivo, sha, tam = pieza["archivo"], pieza["sha256"], pieza["bytes"]
    if not re.fullmatch(r"[\w.+-]+", archivo):
        sys.exit(f"FALLA: nombre de archivo inválido en upstream.json: {archivo}")
    cache.mkdir(parents=True, exist_ok=True)
    destino = cache / archivo
    if local is not None:
        if local.is_file() and local.stat().st_size == tam and _sha256(local) == sha:
            return local
        sys.exit(f"FALLA: {local} no coincide con upstream.json (tamaño o sha256). No se usa.")
    if destino.is_file() and destino.stat().st_size == tam and _sha256(destino) == sha:
        return destino
    temporal = destino.with_suffix(destino.suffix + ".parcial")
    temporal.unlink(missing_ok=True)
    _bajar(_fuente(pieza), archivo, temporal)
    real = _sha256(temporal)
    if real != sha or temporal.stat().st_size != tam:
        temporal.unlink(missing_ok=True)
        sys.exit(f"FALLA: {archivo} no coincide con upstream.json (sha256 {real}). No se usa.")
    os.replace(temporal, destino)
    return destino


def _nombres_de_arranque(index_oficial: str) -> tuple[str, str]:
    js = re.search(r"from\s+'/app/(OpenCADStudio-[0-9a-f]{16}\.js)'", index_oficial)
    wasm = re.search(r"module_or_path:\s*'/app/(OpenCADStudio-[0-9a-f]{16}_bg\.wasm)'", index_oficial)
    if not js or not wasm:
        sys.exit("FALLA: la página oficial ya no tiene la forma conocida (cargador de Trunk). Revisa arranque.js con la versión nueva.")
    return js.group(1), wasm.group(1)


def preparar_editor(destino: Path, cache: Path, local: Path | None, comprimir: bool = True) -> Path:
    d = datos()
    ver = d["version"]
    final = destino / ver
    huella = hashlib.sha256((d["web"]["sha256"] + "".join(_sha256(AQUI / n) for n in PROPIOS)).encode()).hexdigest()
    if (final / "MANIFIESTO.json").is_file() and json.loads((final / "MANIFIESTO.json").read_text()).get("huella") == huella:
        print(f"OK    editor {ver} ya preparado en {final}")
        return final
    zip_ruta = obtener(d["web"], cache, local)
    destino.mkdir(parents=True, exist_ok=True)
    trabajo = Path(tempfile.mkdtemp(prefix=f".{ver}-", dir=destino))
    try:
        with zipfile.ZipFile(zip_ruta) as z:
            for info in z.infolist():
                nombre = info.filename
                if not nombre.startswith("app/") or info.is_dir():
                    continue
                relativo = Path(nombre[len("app/"):])
                if relativo.is_absolute() or ".." in relativo.parts:
                    sys.exit(f"FALLA: ruta inválida en el paquete: {nombre}")
                salida = trabajo / relativo
                salida.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(salida, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        js, wasm = _nombres_de_arranque((trabajo / "index.html").read_text(encoding="utf-8"))
        for nombre in (js, wasm, "ocs-parse-worker.js", "worker_pkg/ocs_web_worker.js", "worker_pkg/ocs_web_worker_bg.wasm"):
            if not (trabajo / nombre).is_file():
                sys.exit(f"FALLA: falta {nombre} en el paquete oficial.")
        for nombre in QUITAR:
            (trabajo / nombre).unlink(missing_ok=True)
        for nombre in VACIOS:
            (trabajo / nombre).write_text("[]\n", encoding="utf-8")
        plantilla = (AQUI / "index.html").read_text(encoding="utf-8")
        (trabajo / "index.html").write_text(plantilla.replace("__JS__", js).replace("__WASM__", wasm).replace("__VERSION__", ver), encoding="utf-8")
        for nombre in ("arranque.js", "editor.css"):
            shutil.copyfile(AQUI / nombre, trabajo / nombre)
        archivos = {}
        for ruta in sorted(p for p in trabajo.rglob("*") if p.is_file()):
            relativo = ruta.relative_to(trabajo).as_posix()
            archivos[relativo] = _sha256(ruta)
            if comprimir and ruta.suffix in COMPRIMIR and ruta.stat().st_size > 1024:
                with open(ruta, "rb") as src, open(str(ruta) + ".gz", "wb") as crudo, \
                        gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=crudo, mtime=0) as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
        (trabajo / "MANIFIESTO.json").write_text(json.dumps({"version": ver, "zip_sha256": d["web"]["sha256"], "huella": huella, "js": js, "wasm": wasm,
                                                             "archivos": archivos}, indent=1), encoding="utf-8")
        for p in [trabajo, *trabajo.rglob("*")]:
            os.chmod(p, 0o755 if p.is_dir() else 0o644)
        if final.exists():
            shutil.rmtree(final)
        os.replace(trabajo, final)
    finally:
        if trabajo.exists():
            shutil.rmtree(trabajo, ignore_errors=True)
    print(f"OK    editor {ver} en {final} ({sum(1 for _ in final.rglob('*'))} archivos)")
    return final


def verificar(destino: Path) -> None:
    d = datos()
    final = destino / d["version"]
    manifiesto = json.loads((final / "MANIFIESTO.json").read_text(encoding="utf-8"))
    if manifiesto.get("zip_sha256") != d["web"]["sha256"]:
        sys.exit("FALLA: el editor preparado no es el de upstream.json.")
    for relativo, sha in manifiesto["archivos"].items():
        if _sha256(final / relativo) != sha:
            sys.exit(f"FALLA: {relativo} cambió después de prepararse.")
    print(f"OK    editor {d['version']} verificado ({len(manifiesto['archivos'])} archivos)")


def preparar_nativo(arch: str, destino: Path, cache: Path, local: Path | None) -> Path:
    d = datos()
    if arch not in d["nativo"]:
        sys.exit(f"FALLA: no hay motor nativo para {arch} en upstream.json.")
    n = d["nativo"][arch]
    origen = obtener(n, cache, local)
    destino.mkdir(parents=True, exist_ok=True)
    final = destino / n["archivo"]
    if final.resolve() != origen.resolve():
        shutil.copyfile(origen, final)
    os.chmod(final, 0o755)
    (destino / "nativo.json").write_text(json.dumps({"version": d["version"], "arch": arch, "archivo": n["archivo"], "sha256": n["sha256"]}), encoding="utf-8")
    print(f"OK    motor nativo {d['version']} {arch} en {final}")
    return final


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = a.add_subparsers(dest="accion", required=True)
    e = s.add_parser("editor")
    e.add_argument("--destino", type=Path, required=True)
    e.add_argument("--zip", type=Path, help="el ZIP oficial ya bajado (se comprueba igual)")
    e.add_argument("--cache", type=Path, default=cache_por_omision())
    e.add_argument("--sin-comprimir", action="store_true")
    n = s.add_parser("nativo")
    n.add_argument("--arch", choices=("x86_64", "aarch64"), required=True)
    n.add_argument("--destino", type=Path, required=True)
    n.add_argument("--appimage", type=Path)
    n.add_argument("--cache", type=Path, default=cache_por_omision())
    v = s.add_parser("verificar")
    v.add_argument("--destino", type=Path, required=True)
    s.add_parser("version")
    args = a.parse_args(argv)
    if args.accion == "editor":
        preparar_editor(args.destino, args.cache, args.zip, not args.sin_comprimir)
    elif args.accion == "nativo":
        preparar_nativo(args.arch, args.destino, args.cache, args.appimage)
    elif args.accion == "verificar":
        verificar(args.destino)
    else:
        print(datos()["version"])


if __name__ == "__main__":
    main()
