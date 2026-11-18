"""CAD · el editor guardado en la computadora (docs/cad.md, «El editor guardado en el equipo»), medido en el cable.

Una computadora (perfil que se guarda, 1280 × 860, ratón) habla con el servidor de pruebas solo a través de `contador_bytes.ContadorBytes`
(el proxy de «Datos móviles», que comprime como Caddy). Chromium y WebKit, un navegador a la vez, bajo el candado de la máquina.

- La primera vez que se abre un plano el editor queda en Cache Storage; al reabrir y en una pestaña nueva no viaja ni un byte del editor.
- El menú dice qué ocupa («CAD guardado en esta computadora · … MB») y «Quitar» lo borra y deja de guardarlo hasta «Guardar el CAD …».
- Lo guardado y dañado (un archivo que ya no mide lo que dice el manifiesto) no se usa: el editor sale de la red y la versión se rehace.
- Cambio de versión con la pareja real tv-v2026.45-11 → -12: solo viajan los parches (y lo pequeño que cambió), el resultado tiene el
  SHA-256 del manifiesto y la versión anterior se borra. Un parche dañado no rompe nada: se baja ese archivo entero.

El editor de `TV_CAD_EDITOR_PRUEBAS` debe tener las dos versiones (`preparar.py editor --destino DIR` y
`preparar.py editor --anterior tv-v2026.45-11 --destino DIR`). `ITT_DATOS_CAD=<carpeta>` deja ahí lo medido (JSON).
"""
from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import editor as cad_editor
from itt_herramientas.cad import guardado, servicio

from .contador_bytes import ContadorBytes, agrupar
from .test_cad_navegador import EDITOR, LARGO, _persona, cad  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
DATOS = os.environ.get("ITT_DATOS_CAD", "")
ANTERIOR = "tv-v2026.45-11"
KB = 1024


@pytest.fixture
def proxy(servidor):
    u = urlparse(servidor.url)
    c = ContadorBytes(u.hostname, u.port).arrancar()
    yield c
    c.detener()


@pytest.fixture
def sin_tactil_produccion(cad, settings):
    settings.TV_CAD_TACTIL = False                     # la computadora no depende del modo táctil
    guardado._paquete.cache_clear()
    yield cad
    guardado._paquete.cache_clear()


class Computadora:
    """Un navegador de escritorio con perfil que se guarda (como el de la persona), que solo habla con el servidor por el proxy."""

    def __init__(self, playwright_, motor, proxy, cookie, carpeta):
        tipo = getattr(playwright_, motor)
        args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if motor == "chromium" else []
        self.contexto = tipo.launch_persistent_context(str(carpeta), headless=True, args=args, viewport={"width": 1280, "height": 860}, locale="es-MX")
        self.contexto.add_cookies([{**cookie, "url": proxy.url}])
        self.proxy, self.errores = proxy, []
        self.pagina = self.contexto.pages[0] if self.contexto.pages else self.contexto.new_page()
        self._vigilar(self.pagina)

    def _vigilar(self, p):
        p.set_default_timeout(LARGO)
        p.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))

    def abrir(self, ruta, pagina=None):
        p = pagina or self.pagina
        p.goto(self.proxy.url + ruta)
        esperar(p, "window.TVCad && TVCad.estaListo()", LARGO * 2)
        return p

    def nueva_pestana(self):
        p = self.contexto.new_page()
        self._vigilar(p)
        return p

    def cerrar_plano(self):
        self.pagina.goto("about:blank")
        self.proxy.esperar_quieto()


def _editor_en_cable(peticiones):
    """Lo del editor que viajó (sin lo de las páginas de Herramientas): {bytes, peticiones, rutas}."""
    ed = [x for x in peticiones if x.ruta.startswith("/cad/editor/")]
    return {"bytes": sum(x.respuesta for x in ed), "peticiones": len(ed), "rutas": sorted({x.ruta.split("?")[0] for x in ed})}


def _tramo(t, nombre, accion):
    t.proxy.esperar_quieto()
    t.proxy.marcar(nombre)
    accion()
    t.proxy.esperar_quieto()
    pet = t.proxy.tramo(nombre)
    return {"editor": _editor_en_cable(pet), "total": sum(x.total for x in pet), "detalle": agrupar(pet)[:12]}


def _cachés(p):
    return p.evaluate("caches.keys().then(k => k.filter(n => n.startsWith('itt-cad-editor-')).sort())")


def _sha_guardado(p, version, ruta):
    """El SHA-256 de lo guardado en el equipo para un archivo del editor, leído de Cache Storage."""
    return p.evaluate("""async ([v, r]) => { const c = await caches.open('itt-cad-editor-' + v); const x = await c.match(`/cad/editor/${v}/${r}`);
      if (!x) return null; const h = new Uint8Array(await crypto.subtle.digest('SHA-256', await x.arrayBuffer()));
      return Array.from(h, b => b.toString(16).padStart(2, '0')).join(''); }""", [version, ruta])


def _escribir(nombre, motor, datos):
    if DATOS:
        Path(DATOS).mkdir(parents=True, exist_ok=True)
        (Path(DATOS) / f"{nombre}-{motor}.json").write_text(json.dumps(datos, indent=1, ensure_ascii=False, default=list), encoding="utf-8")


def _manifiesto(version):
    return json.loads((EDITOR / version / "MANIFIESTO.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_se_guarda_al_abrir_y_despues_no_viaja_nada_del_editor(sin_tactil_produccion, proxy, playwright_, sesion_de, crear_usuario, roles,
                                                              tmp_path, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "Agrícola Norte", "dxf")
    v = cad_editor.version()
    t = Computadora(playwright_, motor, proxy, sesion_de(ana), tmp_path / f"perfil-{motor}")
    medido = {}
    try:
        p = t.pagina
        medido["primera vez"] = _tramo(t, "primera", lambda: t.abrir(f"/cad/{p0.pk}/"))
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)")
        assert _cachés(p) == [f"itt-cad-editor-{v}"]
        man = _manifiesto(v)
        assert _sha_guardado(p, v, man["wasm"]) == man["archivos"][man["wasm"]]
        # El editor llega una sola vez: lo que se guarda es lo que el marco abre después (del equipo).
        assert medido["primera vez"]["editor"]["bytes"] < 1.15 * guardado.paquete_del_editor()["descarga"] + 512 * KB, medido["primera vez"]
        p.locator("#cad-menu > summary").click()
        expect(p.locator("#cad-guardado-texto")).to_have_text(f"CAD guardado en esta computadora · {round(guardado.paquete_del_editor()['bytes'] / 1048576)} MB")
        expect(p.locator("#cad-guardado-quitar")).to_be_visible()
        t.cerrar_plano()

        medido["reabrir"] = _tramo(t, "reabrir", lambda: t.abrir(f"/cad/{p0.pk}/"))
        assert medido["reabrir"]["editor"]["bytes"] == 0, medido["reabrir"]
        otra = t.nueva_pestana()
        medido["pestaña nueva"] = _tramo(t, "pestana", lambda: t.abrir(f"/cad/{p0.pk}/", otra))
        assert medido["pestaña nueva"]["editor"]["bytes"] == 0, medido["pestaña nueva"]
        otra.close()
        t.cerrar_plano()

        # Dañado: el .wasm guardado ya no mide lo que dice el manifiesto → el editor sale de la red, esa versión se borra y se rehace.
        p.goto(proxy.url + "/cad/")
        p.evaluate("""async ([v, r]) => { const c = await caches.open('itt-cad-editor-' + v); const u = `/cad/editor/${v}/${r}`;
          const x = await c.match(u); const b = await x.arrayBuffer();
          await c.put(u, new Response(b.slice(0, b.byteLength - 4096), { headers: x.headers })); }""", [v, man["wasm"]])
        medido["dañado"] = _tramo(t, "danado", lambda: t.abrir(f"/cad/{p0.pk}/"))
        # El editor abrió igual (de la red: en Chromium, de su caché HTTP) y esa versión ya no está guardada.
        esperar(p, f"caches.has('itt-cad-editor-{v}').then(x => !x)")
        t.cerrar_plano()
        medido["después de dañado"] = _tramo(t, "rehecho", lambda: t.abrir(f"/cad/{p0.pk}/"))
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)")
        assert _sha_guardado(p, v, man["wasm"]) == man["archivos"][man["wasm"]]
        t.cerrar_plano()
        medido["estable"] = _tramo(t, "estable", lambda: t.abrir(f"/cad/{p0.pk}/"))
        assert medido["estable"]["editor"]["bytes"] == 0, medido["estable"]

        # «Quitar»: se borra y deja de guardarse; abrir otra vez lo baja de la red sin guardarlo; «Guardar…» lo vuelve a guardar.
        p.locator("#cad-menu > summary").click()
        p.locator("#cad-guardado-quitar").click()
        expect(p.locator("#cad-guardado-texto")).to_have_text("El CAD se descarga cada vez que abres un plano.")
        assert _cachés(p) == []
        t.cerrar_plano()
        t.abrir(f"/cad/{p0.pk}/")
        assert _cachés(p) == []
        p.locator("#cad-menu > summary").click()
        p.locator("#cad-guardado-guardar").click()
        expect(p.locator("#cad-guardado-texto")).to_contain_text("CAD guardado en esta computadora", timeout=LARGO * 2)
        assert _cachés(p) == [f"itt-cad-editor-{v}"]
        assert not t.errores, t.errores
    finally:
        _escribir("guardado", motor, medido)
        t.contexto.close()


@pytest.mark.parametrize("motor", ["chromium", "webkit"])
def test_version_nueva_baja_solo_los_parches(sin_tactil_produccion, proxy, playwright_, sesion_de, crear_usuario, roles, tmp_path, monkeypatch, motor):
    nueva = cad_editor.version()
    if not (EDITOR / ANTERIOR / "index.html").is_file():
        pytest.skip(f"Prepara la versión anterior: preparar.py editor --anterior {ANTERIOR} --destino {EDITOR}")
    man = _manifiesto(nueva)
    parches = {r: next(x for x in lista if x["desde"] == ANTERIOR) for r, lista in man["parches"].items() if any(x["desde"] == ANTERIOR for x in lista)}
    assert man["wasm"] in parches, "la versión fijada no trae el parche del .wasm desde la anterior"
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "Agrícola Norte", "dxf")
    t = Computadora(playwright_, motor, proxy, sesion_de(ana), tmp_path / f"perfil-{motor}")
    medido = {}
    try:
        p = t.pagina
        monkeypatch.setattr(cad_editor, "version", lambda: ANTERIOR)
        guardado._paquete.cache_clear()
        medido[f"primera vez ({ANTERIOR})"] = _tramo(t, "anterior", lambda: t.abrir(f"/cad/{p0.pk}/"))
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)")
        assert _cachés(p) == [f"itt-cad-editor-{ANTERIOR}"]
        t.cerrar_plano()

        monkeypatch.setattr(cad_editor, "version", lambda: nueva)
        guardado._paquete.cache_clear()
        medido[f"actualizar a {nueva}"] = m = _tramo(t, "nueva", lambda: t.abrir(f"/cad/{p0.pk}/"))
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)")
        # Solo los parches y lo pequeño que cambió (nuestra página): nunca el .wasm ni el .js enteros.
        base = f"/cad/editor/{nueva}/"
        assert base + man["wasm"] not in m["editor"]["rutas"] and base + man["js"] not in m["editor"]["rutas"], m["editor"]["rutas"]
        assert any("/_parches/" in r for r in m["editor"]["rutas"]), m["editor"]["rutas"]
        tope = sum(x["bytes_gz"] for x in parches.values()) + 400 * KB
        assert m["editor"]["bytes"] < tope, (m["editor"]["bytes"], tope, m["detalle"])
        # El resultado es el del manifiesto, la versión anterior ya no está y el editor abre de lo guardado.
        for ruta in (man["wasm"], man["js"]):
            assert _sha_guardado(p, nueva, ruta) == man["archivos"][ruta], ruta
        assert _cachés(p) == [f"itt-cad-editor-{nueva}"]
        t.cerrar_plano()
        medido["reabrir"] = _tramo(t, "reabrir", lambda: t.abrir(f"/cad/{p0.pk}/"))
        assert medido["reabrir"]["editor"]["bytes"] == 0, medido["reabrir"]
        t.cerrar_plano()

        assert not t.errores, t.errores
        t.contexto.close()

        # Un parche dañado no rompe nada: ese archivo se baja entero y queda con su SHA-256. Otro equipo (perfil nuevo): el parche bueno
        # quedó en la caché HTTP del primero.
        t = Computadora(playwright_, motor, proxy, sesion_de(ana), tmp_path / f"perfil-{motor}-2")
        p = t.pagina
        monkeypatch.setattr(cad_editor, "version", lambda: ANTERIOR)
        guardado._paquete.cache_clear()
        t.abrir(f"/cad/{p0.pk}/")
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)")
        t.cerrar_plano()
        monkeypatch.setattr(cad_editor, "version", lambda: nueva)
        guardado._paquete.cache_clear()
        dañado = tmp_path / "danado.gz"
        dañado.write_bytes(gzip.compress(b"TVPARCH1" + b"\xff" * 4000))
        original = cad_editor.parche
        monkeypatch.setattr(cad_editor, "parche", lambda ver, ruta, acepta: (dañado, "gzip") if original(ver, ruta, acepta) else None)
        medido["parche dañado"] = d = _tramo(t, "danado", lambda: t.abrir(f"/cad/{p0.pk}/"))
        esperar(p, "TVCadEditor.estado().then(e => e.alDia)")
        assert base + man["wasm"] in d["editor"]["rutas"], d["editor"]["rutas"]
        assert _sha_guardado(p, nueva, man["wasm"]) == man["archivos"][man["wasm"]]
        assert _cachés(p) == [f"itt-cad-editor-{nueva}"]
        assert not t.errores, t.errores
    finally:
        _escribir("version-nueva", motor, medido)
        t.contexto.close()
