#!/usr/bin/env python3
"""Prepara el editor CAD (compilación web de Open CAD Studio) y el motor nativo de Linux, fijados en `upstream.json`.

Solo biblioteca estándar: lo usan el desarrollo, las pruebas y `despliegue/preparar-release.sh` del portal (en la Mac, sin el entorno de
la app). Nunca se guarda el paquete del editor en Git: se baja de la publicación oficial y se comprueba su SHA-256.

    python3 preparar.py editor --destino DIR          DIR/<versión>/ con el editor listo para servir (y sus .gz)
    python3 preparar.py nativo --arch x86_64 --destino DIR
    python3 preparar.py verificar --destino DIR       comprueba DIR/<versión>/MANIFIESTO.json
    python3 preparar.py editor --anterior VERSIÓN --destino DIR   una versión anterior de «anteriores» (pruebas y mediciones)

Cada pieza sale de su «fuente» en upstream.json: la publicación oficial (descarga directa) o una publicación del repositorio privado de
nuestra versión corregida del editor (`gh release download` con la sesión de gh de la Mac; ningún token va al servidor).
Actualizar: cambia versión, fuente, nombres, tamaños y SHA-256 en `upstream.json`, pasa la versión que se reemplaza al principio de
«anteriores» (se guardan las 2 o 3 últimas) y vuelve a correr las pruebas (las del navegador abren, editan y guardan un plano con el editor
real).

PARCHES (docs/cad.md, «El editor guardado en el equipo»): para cada versión de «anteriores», cada archivo del paquete oficial que cambió
(el .wasm principal, su .js, …) lleva un parche binario `_parches/<versión anterior>/<ruta>.tvp`, precomprimido en .gz y .br, que el
navegador que ya tiene esa versión guardada baja y aplica en lugar del archivo entero (static/herramientas/cad/parche.worker.js). Se hace
con `bsdiff` (Homebrew) y se pasa al formato TVP (abajo), que se aplica leyendo el parche de corrido; `brotli` (Homebrew) hace el .br.
Cada parche se comprueba aquí aplicándolo y comparando el SHA-256 del resultado con el del archivo nuevo; el navegador vuelve a comprobarlo.
"""
from __future__ import annotations

import argparse
import bz2
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
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
#: Carpeta de los parches dentro de cada versión, y cuántas versiones anteriores se atienden como mucho.
PARCHES = "_parches"
ANTERIORES_MAX = 3
#: Archivos de menos de esto se bajan enteros (un parche no ahorraría nada que valga la pena).
PARCHE_MINIMO = 16 * 1024
#: Un parche solo se publica si pesa (comprimido) menos que esta fracción del archivo entero comprimido.
PARCHE_RENTABLE = 0.5
#: Formato TVP, little-endian: «TVPARCH1», tamaño viejo, tamaño nuevo, número de registros, bytes «extra» y bytes «diferencia» (u32);
#: los registros (x u32, y u32, z i32: x bytes = viejo + diferencia, y bytes del «extra», y se avanza z en el viejo); el extra; la
#: diferencia. Es el algoritmo de bsdiff con la diferencia al final: quien lo aplica guarda en memoria solo registros y extra (unos MB)
#: y lee la diferencia de corrido mientras escribe el archivo nuevo.
MAGIA_TVP = b"TVPARCH1"


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
    js = re.search(r"from\s+'/app/(OpenCADStudio-[0-9a-f]{12,16}\.js)'", index_oficial)
    wasm = re.search(r"module_or_path:\s*'/app/(OpenCADStudio-[0-9a-f]{12,16}_bg\.wasm)'", index_oficial)
    if not js or not wasm:
        sys.exit("FALLA: la página oficial ya no tiene la forma conocida (cargador de Trunk). Revisa arranque.js con la versión nueva.")
    return js.group(1), wasm.group(1)


def anteriores() -> list[dict]:
    """Las versiones anteriores que reciben parches (`upstream.json` «anteriores»: {version, web}), la más nueva primero."""
    lista = datos().get("anteriores") or []
    if len(lista) > ANTERIORES_MAX:
        sys.exit(f"FALLA: «anteriores» en upstream.json tiene {len(lista)} versiones; se atienden hasta {ANTERIORES_MAX}.")
    for a in lista:
        if not re.fullmatch(r"[\w.+-]+", a.get("version", "")) or not isinstance(a.get("web"), dict):
            sys.exit(f"FALLA: versión anterior inválida en upstream.json: {a}")
    return lista


def _extraer(zip_ruta: Path, trabajo: Path) -> tuple[str, str]:
    """Los archivos de `app/` del ZIP oficial en `trabajo`. Devuelve los nombres del .js y el .wasm principales (con su huella)."""
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
    return _nombres_de_arranque((trabajo / "index.html").read_text(encoding="utf-8"))


def preparar_editor(destino: Path, cache: Path, local: Path | None, comprimir: bool = True, anterior: str | None = None) -> Path:
    d = datos()
    previas = anteriores()
    if anterior:
        # Una versión anterior tal cual, sin parches (las pruebas del navegador y las mediciones simulan con ella el cambio de versión).
        a = next((x for x in previas if x["version"] == anterior), None)
        if a is None:
            sys.exit(f"FALLA: {anterior} no está en «anteriores» de upstream.json.")
        d, previas = {**d, "version": a["version"], "web": a["web"]}, []
    ver = d["version"]
    final = destino / ver
    # La huella cambia con el ZIP, con las versiones anteriores, con nuestra página y con este script (un formato de manifiesto nuevo
    # vuelve a preparar).
    huella = hashlib.sha256((d["web"]["sha256"] + "".join(a["web"]["sha256"] for a in previas)
                             + "".join(_sha256(AQUI / n) for n in (*PROPIOS, "preparar.py"))).encode()).hexdigest()
    if (final / "MANIFIESTO.json").is_file() and json.loads((final / "MANIFIESTO.json").read_text()).get("huella") == huella:
        print(f"OK    editor {ver} ya preparado en {final}")
        return final
    zip_ruta = obtener(d["web"], cache, local)
    destino.mkdir(parents=True, exist_ok=True)
    trabajo = Path(tempfile.mkdtemp(prefix=f".{ver}-", dir=destino))
    try:
        js, wasm = _extraer(zip_ruta, trabajo)
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
        archivos, comprimidos = {}, {}
        for ruta in sorted(p for p in trabajo.rglob("*") if p.is_file()):
            relativo = ruta.relative_to(trabajo).as_posix()
            archivos[relativo] = _sha256(ruta)
            if comprimir and ruta.suffix in COMPRIMIR and ruta.stat().st_size > 1024:
                comprimidos[f"{relativo}.gz"] = _gzip(ruta, Path(str(ruta) + ".gz"))
        # Los parches desde cada versión anterior (fuera de «archivos»: no son del editor, solo una forma más corta de llegar a él).
        parches = {}
        for a in previas:
            for relativo, p in _parches_desde(a, cache, trabajo, archivos, js, wasm).items():
                parches.setdefault(relativo, []).append(p)
        # «comprimidos»: el hash de cada .gz, que es lo que Caddy entrega a casi todos los navegadores (auditoría B-8).
        (trabajo / "MANIFIESTO.json").write_text(json.dumps({"version": ver, "zip_sha256": d["web"]["sha256"], "huella": huella, "js": js, "wasm": wasm,
                                                             "archivos": archivos, "comprimidos": comprimidos, "parches": parches}, indent=1), encoding="utf-8")
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


def _gzip(origen: Path, gz: Path) -> str:
    with open(origen, "rb") as src, open(gz, "wb") as crudo, gzip.GzipFile(filename="", mode="wb", compresslevel=9, fileobj=crudo, mtime=0) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    return _sha256(gz)


def _herramienta(nombre: str) -> str:
    ruta = shutil.which(nombre)
    if not ruta:
        sys.exit(f"FALLA: hace falta `{nombre}` para preparar los parches del editor (brew install {nombre}).")
    return ruta


def _offtin(b: bytes) -> int:
    """Un entero de bsdiff: 8 bytes little-endian con el signo en el bit más alto (no en complemento a dos)."""
    y = int.from_bytes(b[:8], "little") & ~(1 << 63)
    return -y if b[7] & 0x80 else y


def tvp_de_bsdiff(bsdiff: bytes, tam_viejo: int) -> bytes:
    """Pasa un parche BSDIFF40 (tres bloques bzip2: registros, diferencia, extra) al formato TVP (ver MAGIA_TVP)."""
    if bsdiff[:8] != b"BSDIFF40" or len(bsdiff) < 32:
        raise ValueError("no es un parche BSDIFF40")
    largo_ctrl, largo_dif, tam_nuevo = _offtin(bsdiff[8:16]), _offtin(bsdiff[16:24]), _offtin(bsdiff[24:32])
    ctrl = bz2.decompress(bsdiff[32:32 + largo_ctrl])
    dif = bz2.decompress(bsdiff[32 + largo_ctrl:32 + largo_ctrl + largo_dif])
    extra = bz2.decompress(bsdiff[32 + largo_ctrl + largo_dif:])
    if len(ctrl) % 24:
        raise ValueError("registros de bsdiff incompletos")
    registros = bytearray()
    for i in range(0, len(ctrl), 24):
        x, y, z = _offtin(ctrl[i:i + 8]), _offtin(ctrl[i + 8:i + 16]), _offtin(ctrl[i + 16:i + 24])
        if not (0 <= x < 1 << 32 and 0 <= y < 1 << 32 and -(1 << 31) <= z < 1 << 31):
            raise ValueError("registro de bsdiff fuera de rango")
        registros += struct.pack("<IIi", x, y, z)
    for n in (tam_viejo, tam_nuevo, len(dif), len(extra)):
        if not 0 <= n < 1 << 32:
            raise ValueError("archivo demasiado grande para TVP")
    return MAGIA_TVP + struct.pack("<5I", tam_viejo, tam_nuevo, len(ctrl) // 24, len(extra), len(dif)) + bytes(registros) + extra + dif


def _sumar(a, b) -> bytes:
    """a + b byte a byte (módulo 256), de un golpe con enteros grandes: sin acarreo entre bytes porque cada byte suma sus 7 bits bajos
    (cabe en 8) y el bit alto va aparte con XOR."""
    n = len(a)
    if not n:
        return b""
    x, y = int.from_bytes(a, "little"), int.from_bytes(b, "little")
    bajos, altos = int.from_bytes(b"\x7f" * n, "little"), int.from_bytes(b"\x80" * n, "little")
    return (((x & bajos) + (y & bajos)) ^ ((x ^ y) & altos)).to_bytes(n, "little")


def aplicar_tvp(viejo: bytes, tvp: bytes) -> bytes:
    """Aplica un parche TVP, igual que el navegador (parche.worker.js). ValueError si el parche no es de este archivo o está dañado."""
    mv = memoryview(tvp)
    if bytes(mv[:8]) != MAGIA_TVP or len(tvp) < 28:
        raise ValueError("no es un parche TVP")
    tam_viejo, tam_nuevo, n, n_extra, n_dif = struct.unpack_from("<5I", mv, 8)
    if tam_viejo != len(viejo):
        raise ValueError("el parche es de otro archivo")
    i_extra = 28 + 12 * n
    i_dif = i_extra + n_extra
    if i_dif + n_dif != len(tvp):
        raise ValueError("parche TVP incompleto")
    nuevo = bytearray(tam_nuevo)
    pn = po = pe = pd = 0
    for x, y, z in struct.iter_unpack("<IIi", mv[28:i_extra]):
        if pn + x + y > tam_nuevo or pd + x > n_dif or pe + y > n_extra:
            raise ValueError("parche TVP dañado")
        lo, hi = max(po, 0), min(po + x, tam_viejo)
        base = bytearray(x)
        if hi > lo:
            base[lo - po:hi - po] = viejo[lo:hi]
        nuevo[pn:pn + x] = _sumar(base, mv[i_dif + pd:i_dif + pd + x])
        pn += x
        pd += x
        po += x
        nuevo[pn:pn + y] = mv[i_extra + pe:i_extra + pe + y]
        pn += y
        pe += y
        po += z
    if pn != tam_nuevo or pd != n_dif or pe != n_extra:
        raise ValueError("parche TVP dañado")
    return bytes(nuevo)


def _hacer_parche(viejo: Path, nuevo: Path, cache: Path) -> tuple[Path, Path, str]:
    """El parche TVP de `viejo` a `nuevo`, comprimido en .gz y .br (en la caché, por los SHA-256 de los dos: bsdiff tarda ~1 min con el
    .wasm). Siempre se comprueba aplicándolo (el .gz, que es lo que también se publica) antes de usarlo. Devuelve el .gz, el .br y el
    SHA-256 del parche sin comprimir (con él `tvctl desplegar` comprueba que el .gz descomprime a lo que se probó)."""
    sha_v, sha_n = _sha256(viejo), _sha256(nuevo)
    carpeta = cache / "parches"
    carpeta.mkdir(parents=True, exist_ok=True)
    base = carpeta / f"{sha_v[:24]}-{sha_n[:24]}.tvp"
    gz, br = Path(f"{base}.gz"), Path(f"{base}.br")
    if not (gz.is_file() and br.is_file()):
        with tempfile.TemporaryDirectory(dir=carpeta) as t:
            crudo = Path(t) / "p.bsdiff"
            r = subprocess.run([_herramienta("bsdiff"), str(viejo), str(nuevo), str(crudo)], capture_output=True, text=True)
            if r.returncode != 0 or not crudo.is_file():
                sys.exit(f"FALLA: bsdiff no pudo hacer el parche de {nuevo.name}: {(r.stderr or r.stdout).strip()[-300:]}")
            tvp = Path(t) / "p.tvp"
            tvp.write_bytes(tvp_de_bsdiff(crudo.read_bytes(), viejo.stat().st_size))
            _gzip(tvp, Path(t) / "p.tvp.gz")
            r = subprocess.run([_herramienta("brotli"), "-q", "11", "-w", "24", "-f", "-o", str(Path(t) / "p.tvp.br"), str(tvp)], capture_output=True, text=True)
            if r.returncode != 0:
                sys.exit(f"FALLA: brotli no pudo comprimir el parche de {nuevo.name}: {(r.stderr or r.stdout).strip()[-300:]}")
            r = subprocess.run([_herramienta("brotli"), "-d", "-c", str(Path(t) / "p.tvp.br")], capture_output=True)
            if r.returncode != 0 or hashlib.sha256(r.stdout).hexdigest() != _sha256(tvp):
                sys.exit(f"FALLA: el .br del parche de {nuevo.name} no descomprime a su original.")
            os.replace(Path(t) / "p.tvp.br", br)
            os.replace(Path(t) / "p.tvp.gz", gz)
    with gzip.open(gz, "rb") as f:
        tvp = f.read()
    try:
        ok = hashlib.sha256(aplicar_tvp(viejo.read_bytes(), tvp)).hexdigest() == sha_n
    except ValueError:
        ok = False
    if not ok:
        gz.unlink(missing_ok=True)
        br.unlink(missing_ok=True)
        sys.exit(f"FALLA: el parche de {nuevo.name} no reproduce el archivo nuevo. Vuelve a preparar.")
    return gz, br, hashlib.sha256(tvp).hexdigest()


def _parches_desde(anterior: dict, cache: Path, trabajo: Path, archivos: dict, js: str, wasm: str) -> dict:
    """Los parches desde una versión anterior para los archivos del paquete oficial que cambiaron. Los deja en
    `trabajo/_parches/<versión>/<ruta>.tvp.{gz,br}` y devuelve {ruta nueva: {desde, ruta, sha256, archivo, tvp, gz, br, bytes_gz, bytes_br}}
    («ruta» y «sha256»: el archivo de la versión anterior al que se aplica; «tvp», «gz», «br»: SHA-256 del parche y de sus dos formas)."""
    ver = anterior["version"]
    zip_ruta = obtener(anterior["web"], cache)
    salida = {}
    with tempfile.TemporaryDirectory(dir=trabajo.parent) as t:
        viejo_dir = Path(t)
        js_v, wasm_v = _extraer(zip_ruta, viejo_dir)
        viejos = {p.relative_to(viejo_dir).as_posix(): p for p in viejo_dir.rglob("*") if p.is_file()}
        shas_viejos = {_sha256(p) for p in viejos.values()}
        for relativo, sha in sorted(archivos.items()):
            nuevo = trabajo / relativo
            if relativo in PROPIOS or relativo in VACIOS or sha in shas_viejos or nuevo.stat().st_size < PARCHE_MINIMO:
                continue                     # nuestro, igual que antes (se copia en el navegador) o chico: no lleva parche
            de = wasm_v if relativo == wasm else js_v if relativo == js else relativo     # el .js y el .wasm cambian de nombre
            if de not in viejos:
                continue
            gz, br, sha_tvp = _hacer_parche(viejos[de], nuevo, cache)
            entero = (nuevo.stat().st_size if not Path(f"{nuevo}.gz").is_file() else Path(f"{nuevo}.gz").stat().st_size)
            if gz.stat().st_size >= PARCHE_RENTABLE * entero:
                continue
            destino = trabajo / PARCHES / ver / f"{relativo}.tvp"
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(gz, f"{destino}.gz")
            shutil.copyfile(br, f"{destino}.br")
            salida[relativo] = {"desde": ver, "ruta": de, "sha256": _sha256(viejos[de]), "archivo": f"{PARCHES}/{ver}/{relativo}.tvp",
                                "tvp": sha_tvp, "gz": _sha256(gz), "br": _sha256(br), "bytes_gz": gz.stat().st_size, "bytes_br": br.stat().st_size}
            print(f"OK    parche {ver} → {relativo}: {gz.stat().st_size:,} B (.gz), {br.stat().st_size:,} B (.br) en vez de {entero:,} B", file=sys.stderr)
    return salida


def verificar(destino: Path) -> None:
    d = datos()
    final = destino / d["version"]
    manifiesto = json.loads((final / "MANIFIESTO.json").read_text(encoding="utf-8"))
    if manifiesto.get("zip_sha256") != d["web"]["sha256"]:
        sys.exit("FALLA: el editor preparado no es el de upstream.json.")
    if "comprimidos" not in manifiesto:
        sys.exit("FALLA: el editor se preparó con una versión anterior de preparar.py (sin el hash de los .gz). Vuelve a prepararlo.")
    if "parches" not in manifiesto:
        sys.exit("FALLA: el editor se preparó con una versión anterior de preparar.py (sin la lista de parches). Vuelve a prepararlo.")
    esperados = {**manifiesto["archivos"], **manifiesto["comprimidos"], **parches_en_disco(manifiesto)}
    # El conjunto exacto: un archivo de más (también un .gz) lo serviría Caddy sin que nadie lo haya comprobado.
    en_disco = {p.relative_to(final).as_posix() for p in final.rglob("*") if p.is_file() and p.name != "MANIFIESTO.json"}
    sobran, faltan = sorted(en_disco - set(esperados)), sorted(set(esperados) - en_disco)
    if sobran:
        sys.exit(f"FALLA: archivos que no son del editor preparado: {', '.join(sobran[:10])}")
    if faltan:
        sys.exit(f"FALLA: faltan archivos del editor preparado: {', '.join(faltan[:10])}")
    for relativo, sha in esperados.items():
        if _sha256(final / relativo) != sha:
            sys.exit(f"FALLA: {relativo} cambió después de prepararse.")
    n_parches = sum(len(x) for x in manifiesto["parches"].values())
    print(f"OK    editor {d['version']} verificado ({len(manifiesto['archivos'])} archivos, {len(manifiesto['comprimidos'])} comprimidos y {n_parches} parches)")


def parches_en_disco(manifiesto: dict) -> dict:
    """{ruta en disco: SHA-256} de los parches del manifiesto (cada uno en .gz y .br)."""
    salida = {}
    for lista in manifiesto.get("parches", {}).values():
        for p in lista:
            salida[f"{p['archivo']}.gz"] = p["gz"]
            salida[f"{p['archivo']}.br"] = p["br"]
    return salida


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
    # El motor nativo es el oficial de la versión base (la compilación corregida es solo la web).
    base = d.get("base", d["version"])
    (destino / "nativo.json").write_text(json.dumps({"version": base, "arch": arch, "archivo": n["archivo"], "sha256": n["sha256"]}), encoding="utf-8")
    print(f"OK    motor nativo {base} {arch} en {final}")
    return final


def main(argv=None):
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = a.add_subparsers(dest="accion", required=True)
    e = s.add_parser("editor")
    e.add_argument("--destino", type=Path, required=True)
    e.add_argument("--zip", type=Path, help="el ZIP oficial ya bajado (se comprueba igual)")
    e.add_argument("--cache", type=Path, default=cache_por_omision())
    e.add_argument("--sin-comprimir", action="store_true")
    e.add_argument("--anterior", help="prepara esta versión de «anteriores» (sin parches), para pruebas y mediciones")
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
        preparar_editor(args.destino, args.cache, args.zip, not args.sin_comprimir, args.anterior)
    elif args.accion == "nativo":
        preparar_nativo(args.arch, args.destino, args.cache, args.appimage)
    elif args.accion == "verificar":
        verificar(args.destino)
    else:
        print(datos()["version"])


if __name__ == "__main__":
    main()
