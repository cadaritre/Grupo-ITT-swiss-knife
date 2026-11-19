"""CAD · el editor guardado en el equipo: la lista de lo que se baja, los parches entre versiones (preparar.py, formato TVP) y cómo se
sirven (docs/cad.md, «El editor guardado en el equipo»). Las pruebas del navegador están en pruebas_navegador/test_cad_guardado_navegador.py."""
from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import random
import shutil
import zipfile
from pathlib import Path

import pytest

from itt_herramientas.cad import editor, guardado

from .conftest import url
from .test_cad import dibujante, editor_falso, plano, supervisora  # noqa: F401 (fixtures)

HERRAMIENTAS = shutil.which("bsdiff") and shutil.which("brotli")


def _preparar():
    spec = importlib.util.spec_from_file_location("preparar_prueba", Path(editor.__file__).parent / "preparar.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    return prep


def _wasm(semilla: int, n: int = 96 * 1024) -> bytes:
    r = random.Random(semilla)
    return b"\0asm" + bytes(r.getrandbits(8) for _ in range(n))


def _zip(ruta: Path, js_hash: str, wasm_hash: str, wasm: bytes, js: bytes) -> Path:
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("app/index.html", f"<script type=module>import init, * as b from '/app/OpenCADStudio-{js_hash}.js';"
                                     f"await init({{ module_or_path: '/app/OpenCADStudio-{wasm_hash}_bg.wasm' }});</script>")
        z.writestr(f"app/OpenCADStudio-{js_hash}.js", js)
        z.writestr(f"app/OpenCADStudio-{wasm_hash}_bg.wasm", wasm)
        z.writestr("app/ocs-parse-worker.js", "x")
        z.writestr("app/worker_pkg/ocs_web_worker.js", "export default 1;")
        z.writestr("app/worker_pkg/ocs_web_worker_bg.wasm", _wasm(7, 20 * 1024))         # igual en las dos: se copia, sin parche
    return ruta


def _pieza(ruta: Path) -> dict:
    return {"archivo": ruta.name, "sha256": hashlib.sha256(ruta.read_bytes()).hexdigest(), "bytes": ruta.stat().st_size}


@pytest.fixture
def dos_versiones(tmp_path):
    """Un ZIP «anterior» y uno «nuevo» de mentira: el .wasm cambia unos bytes y crece, el .js cambia una línea, el resto es igual."""
    viejo_wasm = _wasm(1)
    nuevo_wasm = bytearray(viejo_wasm)
    for i in range(500, len(nuevo_wasm), 4099):
        nuevo_wasm[i] ^= 0x5A
    nuevo_wasm = bytes(nuevo_wasm[:40000]) + b"funcion nueva" * 300 + bytes(nuevo_wasm[40000:])
    r = random.Random(3)
    js_viejo = b"export default async () => ({});\n" + b"".join(b"const f%d = %d;\n" % (i, r.getrandbits(48)) for i in range(1500))
    js_nuevo = js_viejo.replace(b"({})", b"({nuevo: 1})")
    cache = tmp_path / "cache"
    cache.mkdir()
    z_viejo = _zip(cache / "viejo.zip", "0123456789abcdef", "fedcba987654321", viejo_wasm, js_viejo)          # huella de 15 cifras
    z_nuevo = _zip(cache / "nuevo.zip", "1111111111111111", "2222222222222222", nuevo_wasm, js_nuevo)
    prep = _preparar()
    datos = {**prep.datos(), "version": "tv-prueba-2", "web": _pieza(z_nuevo), "anteriores": [{"version": "tv-prueba-1", "web": _pieza(z_viejo)}]}
    prep.datos = lambda: datos
    return prep, cache, {"wasm": (viejo_wasm, nuevo_wasm), "js": (js_viejo, js_nuevo)}


@pytest.mark.skipif(not HERRAMIENTAS, reason="hace falta bsdiff y brotli (brew install bsdiff brotli)")
def test_preparar_hace_parches_comprobados_de_lo_que_cambio(tmp_path, dos_versiones):
    prep, cache, contenido = dos_versiones
    final = prep.preparar_editor(tmp_path / "salida", cache, None)
    man = json.loads((final / "MANIFIESTO.json").read_text())
    wasm, js = man["wasm"], man["js"]
    assert set(man["parches"]) == {wasm, js}, man["parches"].keys()                # el trabajador .wasm es igual: sin parche
    for ruta, (viejo, nuevo) in ((wasm, contenido["wasm"]), (js, contenido["js"])):
        (p,) = man["parches"][ruta]
        assert p["desde"] == "tv-prueba-1" and p["sha256"] == hashlib.sha256(viejo).hexdigest()
        assert p["ruta"] == ("OpenCADStudio-fedcba987654321_bg.wasm" if ruta == wasm else "OpenCADStudio-0123456789abcdef.js")
        assert p["archivo"] == f"_parches/tv-prueba-1/{ruta}.tvp"
        gz, br = final / f"{p['archivo']}.gz", final / f"{p['archivo']}.br"
        assert not (final / p["archivo"]).exists()                                    # solo comprimido: Caddy elige .br o .gz
        tvp = gzip.decompress(gz.read_bytes())
        assert hashlib.sha256(tvp).hexdigest() == p["tvp"] and hashlib.sha256(gz.read_bytes()).hexdigest() == p["gz"]
        assert hashlib.sha256(br.read_bytes()).hexdigest() == p["br"] and p["bytes_br"] == br.stat().st_size
        assert prep.aplicar_tvp(viejo, tvp) == nuevo
        assert p["bytes_gz"] < 0.5 * len(gzip.compress(nuevo))                        # vale la pena
    prep.verificar(tmp_path / "salida")
    # Un parche cambiado (o uno de más) no pasa la verificación: Caddy lo serviría sin que nadie lo haya comprobado.
    (p,) = man["parches"][wasm]
    malo = final / f"{p['archivo']}.br"
    original = malo.read_bytes()
    malo.write_bytes(original + b"x")
    with pytest.raises(SystemExit, match="cambió después de prepararse"):
        prep.verificar(tmp_path / "salida")
    malo.write_bytes(original)
    (final / "_parches" / "tv-prueba-1" / "otro.tvp.gz").write_bytes(b"x")
    with pytest.raises(SystemExit, match="no son del editor"):
        prep.verificar(tmp_path / "salida")
    # Otra vez: los parches salen de la caché (bsdiff no corre de nuevo) y dan lo mismo.
    (final / "_parches" / "tv-prueba-1" / "otro.tvp.gz").unlink()
    shutil.rmtree(final)
    otra = prep.preparar_editor(tmp_path / "salida", cache, None)
    assert json.loads((otra / "MANIFIESTO.json").read_text())["parches"] == man["parches"]
    # La versión anterior, tal cual y sin parches (para las pruebas del navegador y las mediciones).
    ant = prep.preparar_editor(tmp_path / "salida", cache, None, anterior="tv-prueba-1")
    assert ant.name == "tv-prueba-1" and json.loads((ant / "MANIFIESTO.json").read_text())["parches"] == {}


@pytest.mark.skipif(not HERRAMIENTAS, reason="hace falta bsdiff y brotli (brew install bsdiff brotli)")
def test_un_parche_de_otro_archivo_o_cortado_se_rechaza(tmp_path, dos_versiones):
    prep, cache, contenido = dos_versiones
    viejo, nuevo = contenido["wasm"]
    (tmp_path / "v").write_bytes(viejo)
    (tmp_path / "n").write_bytes(nuevo)
    gz, _, _ = prep._hacer_parche(tmp_path / "v", tmp_path / "n", cache)
    tvp = gzip.decompress(gz.read_bytes())
    with pytest.raises(ValueError, match="otro archivo"):
        prep.aplicar_tvp(viejo + b"x", tvp)
    with pytest.raises(ValueError, match="incompleto"):
        prep.aplicar_tvp(viejo, tvp[:-10])
    with pytest.raises(ValueError, match="no es un parche"):
        prep.aplicar_tvp(viejo, b"BSDIFF40" + tvp[8:])
    # Un parche en la caché que no reproduce el archivo nuevo no se usa: preparar falla y lo borra.
    with gzip.open(gz, "wb") as f:
        f.write(tvp[:28] + bytes(len(tvp) - 28))
    with pytest.raises(SystemExit, match="no reproduce"):
        prep._hacer_parche(tmp_path / "v", tmp_path / "n", cache)
    assert not gz.exists()


def test_sumar_byte_a_byte_sin_acarreo():
    prep = _preparar()
    a, b = bytes(range(256)) * 3, bytes(reversed(range(256))) * 3
    assert prep._sumar(a, b) == bytes((x + y) & 255 for x, y in zip(a, b))
    assert prep._sumar(b"", b"") == b""


@pytest.fixture
def editor_con_parche(editor_falso):
    """El editor de prueba con un parche desde una versión anterior para su .wasm (en .br y .gz, como lo deja preparar.py)."""
    wasm = "OpenCADStudio-0123456789abcdef_bg.wasm"
    man = json.loads((editor_falso / "MANIFIESTO.json").read_text())
    carpeta = editor_falso / "_parches" / "tv-anterior"
    carpeta.mkdir(parents=True)
    (carpeta / f"{wasm}.tvp.gz").write_bytes(b"parche-gz")
    (carpeta / f"{wasm}.tvp.br").write_bytes(b"parche-br")
    man["parches"] = {wasm: [{"desde": "tv-anterior", "ruta": "OpenCADStudio-aaaaaaaaaaaaaaaa_bg.wasm", "sha256": "f" * 64,
                              "archivo": f"_parches/tv-anterior/{wasm}.tvp", "tvp": "1" * 64, "gz": "2" * 64, "br": "3" * 64, "bytes_gz": 9, "bytes_br": 9}]}
    (editor_falso / "MANIFIESTO.json").write_text(json.dumps(man))
    guardado._paquete.cache_clear()
    return editor_falso


def test_el_paquete_sale_con_o_sin_modo_tactil_y_lleva_los_parches(cliente_de, dibujante, editor_con_parche, settings, client):
    settings.TV_CAD_TACTIL = False                                                  # la computadora: siempre
    r = cliente_de(dibujante).get(url("cad_editor_paquete"))
    assert r.status_code == 200 and r["Cache-Control"] == "no-cache"
    p = r.json()
    wasm = next(a for a in p["archivos"] if a["ruta"].endswith(".wasm"))
    v = editor.version()
    assert wasm["parches"] == [{"desde": "tv-anterior", "ruta": "OpenCADStudio-aaaaaaaaaaaaaaaa_bg.wasm", "sha256": "f" * 64,
                                "url": f"/cad/editor/{v}/_parches/tv-anterior/OpenCADStudio-0123456789abcdef_bg.wasm.tvp", "descarga": 9, "descarga_gz": 9}]
    assert not any(a["ruta"].startswith("_parches/") for a in p["archivos"])           # los parches no son archivos del editor
    assert all("parches" not in a for a in p["archivos"] if not a["ruta"].endswith(".wasm"))
    assert client.get(url("cad_editor_paquete")).status_code in (302, 401, 404)           # sin sesión, nada


def test_el_parche_se_entrega_comprimido_como_en_caddy(cliente_de, dibujante, editor_con_parche):
    cl = cliente_de(dibujante)
    v = editor.version()
    ruta = f"/cad/editor/{v}/_parches/tv-anterior/OpenCADStudio-0123456789abcdef_bg.wasm.tvp"
    r = cl.get(ruta, HTTP_ACCEPT_ENCODING="gzip, deflate, br, zstd")
    assert r.status_code == 200 and r["Content-Encoding"] == "br" and b"".join(r.streaming_content) == b"parche-br"
    assert "Accept-Encoding" in r["Vary"] and r["Content-Type"] == "application/octet-stream" and "'wasm-unsafe-eval'" in r["Content-Security-Policy"]
    r = cl.get(ruta, HTTP_ACCEPT_ENCODING="gzip, deflate")
    assert r.status_code == 200 and r["Content-Encoding"] == "gzip" and b"".join(r.streaming_content) == b"parche-gz"
    assert cl.get(ruta, HTTP_ACCEPT_ENCODING="identity").status_code == 404
    for malo in (f"{ruta}.gz", f"{ruta}.br", f"/cad/editor/{v}/_parches/../index.html.tvp", f"/cad/editor/{v}/_parches/tv-anterior/no.tvp",
                 f"/cad/editor/tv-otra/_parches/tv-anterior/OpenCADStudio-0123456789abcdef_bg.wasm.tvp"):
        r = cl.get(malo, HTTP_ACCEPT_ENCODING="gzip, br")
        assert r.status_code == 404 and r["Cache-Control"] == "no-store", malo


def test_cerrar_sesion_no_borra_el_editor_guardado():
    """El editor es el programa, igual para todas las cuentas: cerrar sesión (sesion.js) borra lo de la cuenta, nunca las cachés del editor."""
    estaticos = Path(editor.__file__).parents[2] / "static" / "herramientas" / "cad"
    sesion = (estaticos / "sesion.js").read_text()
    assert "caches" not in sesion and "itt-cad-editor-" not in sesion
    instalador = (estaticos / "instalador.js").read_text()
    assert "persist()" in instalador and "X-TV-SHA256" in instalador and "navigator.locks" in instalador
