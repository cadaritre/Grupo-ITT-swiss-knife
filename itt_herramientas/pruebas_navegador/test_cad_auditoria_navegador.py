"""CAD: regresiones de la auditoría J (15-11-2026) que necesitan el navegador: copias en el navegador (A-1), no poder guardar (M-3),
sesión terminada (M-4), borrador pendiente (M-6), bloqueo retomado tras un guardado ajeno (M-7) y «Guardar y salir» fallido (B-6).
Con el editor real, como test_cad_navegador.py (mismo editor preparado). Corre bajo el candado de la máquina compartida.
"""
from __future__ import annotations

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import servicio

from .test_cad_navegador import LARGO, MOTORES, _persona, cad, dibujar, esperar_estado, navegar  # noqa: F401

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]

SEMBRAR = """async () => {
  localStorage.setItem("opencadstudio.session", JSON.stringify({tabs: ["Plano de un cliente.dwg"], active: 0}));
  let raiz;
  try { raiz = await navigator.storage.getDirectory(); } catch (_) { return false; }   // WebKit de Playwright: sin almacenamiento de archivos
  const dir = await raiz.getDirectoryHandle("opencadstudio-recent", {create: true});
  await dir.getFileHandle("Plano de un cliente.dwg", {create: true});        // basta el archivo (WebKit no escribe desde la página)
  const a = await raiz.getDirectoryHandle("opencadstudio-autosave", {create: true});
  await a.getFileHandle("Plano de un cliente.dwg", {create: true});
  return true;
}"""
COPIAS = """async () => {
  const fuera = []; let raiz = null;
  try { raiz = await navigator.storage.getDirectory(); } catch (_) {}
  for (const n of raiz ? ["opencadstudio-recent", "opencadstudio-autosave"] : []) {
    try { const d = await raiz.getDirectoryHandle(n); for await (const [k] of d.entries()) fuera.push(n + "/" + k); } catch (_) {}
  }
  return {archivos: fuera, sesion: localStorage.getItem("opencadstudio.session")};
}"""


@pytest.mark.parametrize("motor", MOTORES)
def test_las_copias_del_editor_se_borran_al_arrancar_y_al_salir(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir("/cad/")
    con_archivos = p.evaluate(SEMBRAR)
    assert p.evaluate(COPIAS)["archivos"] or not con_archivos
    # Al arrancar el editor, las copias que ya había en el navegador se borran antes de que el editor las vea.
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    r = p.evaluate(COPIAS)
    assert "opencadstudio-recent/Plano de un cliente.dwg" not in r["archivos"] and "opencadstudio-autosave/Plano de un cliente.dwg" not in r["archivos"]
    assert r["sesion"] is None or "Plano de un cliente" not in r["sesion"]
    dibujar(p)
    esperar_estado(p, "sucio")
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    # Al salir de la página del plano no queda ninguna copia del plano en el navegador.
    p.goto(n.servidor.url + "/cad/")
    p.wait_for_function(f"async () => {{ const r = await ({COPIAS})(); return r.archivos.length === 0 && !r.sesion; }}", timeout=LARGO)
    assert not n.errores, n.errores


def test_cerrar_sesion_borra_las_copias_y_avisa_de_los_cambios_sin_subir(cad, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    n = navegar(ana, "chromium")
    p = n.ir("/cad/")
    n.contexto.route("**/sso/salir", lambda r: r.fulfill(status=200, content_type="text/html", body="<!doctype html><title>Fuera</title><p>Sesión cerrada</p>"))
    p.evaluate(SEMBRAR)
    # Un plano llevado al teléfono y otro con cambios hechos sin conexión (modo táctil): la copia se borra, los cambios no sin avisar.
    p.evaluate("""async () => {
      const db = await TVCadSesion.abrir();
      await new Promise((ok) => { const t = db.transaction(["planos", "cambios"], "readwrite");
        t.objectStore("planos").put({id: "p1", cuenta: 1, nombre: "Llevado", formato: "dxf", bytes: new Uint8Array([1, 2, 3])});
        t.objectStore("cambios").put({id: "p2", cuenta: 1, nombre: "Lote 7", formato: "dxf", base: 1, bytes: new Uint8Array([4, 5, 6])});
        t.oncomplete = ok; });
      db.close();
    }""")
    # Desde la paleta de órdenes (llama a form.submit(), sin evento): también pregunta; «Seguir en la app» no sale.
    p.evaluate("() => { document.getElementById('tv-salir').submit(); }")
    hoja = p.get_by_role("dialog", name="Tienes cambios de planos sin subir")
    expect(hoja).to_be_visible()
    hoja.get_by_role("button", name="Seguir en la app").click()
    expect(hoja).to_be_hidden()
    assert "/cad/" in p.url
    p.locator(".tv-menu--cuenta summary").first.click()
    p.locator("#tv-salir button").click()
    expect(hoja).to_be_visible()
    expect(hoja.get_by_role("button", name="Descargar «Lote 7»")).to_be_visible()
    with p.expect_download() as bajada:
        hoja.get_by_role("button", name="Descargar «Lote 7»").click()
    assert bajada.value.suggested_filename == "Lote 7 (cambios sin conexión).dxf"
    hoja.get_by_role("button", name="Cerrar sesión").click()
    p.wait_for_url("**/sso/salir")
    r = p.evaluate(COPIAS)
    assert r == {"archivos": [], "sesion": None}
    quedan = p.evaluate("""async () => { const db = await new Promise((ok) => { const r = indexedDB.open("tv-cad-campo"); r.onsuccess = () => ok(r.result); });
      const leer = (s) => new Promise((ok) => { const r = db.transaction(s).objectStore(s).getAll(); r.onsuccess = () => ok(r.result.map((x) => x.id || x.clave)); });
      const x = {planos: await leer("planos"), cambios: await leer("cambios"), estado: await new Promise((ok) => { const r = db.transaction("estado").objectStore("estado").get("sesion"); r.onsuccess = () => ok(r.result); })};
      db.close(); return x; }""")
    assert quedan["planos"] == [] and quedan["cambios"] == ["p2"] and quedan["estado"]["cerrada"] is True
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_si_no_se_puede_guardar_se_pueden_descargar_los_cambios(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p, "CIRCLE 390000,3168000 25")
    esperar_estado(p, "sucio")
    # Biblioteca llena: el servidor no recibe el plano (422) y la persona puede bajar su trabajo.
    n.contexto.route(f"**/cad/{p0.pk}/guardar/", lambda r: r.fulfill(status=422, content_type="application/json",
                                                                         body='{"error": "La biblioteca de planos está llena."}'))
    p.locator("#cad-guardar").click()
    caja = p.locator("#cad-aviso-error")
    expect(caja).to_contain_text("La biblioteca de planos está llena.")
    with p.expect_download() as bajada:
        caja.get_by_role("button", name="Descargar mis cambios").click()
    assert bajada.value.suggested_filename == "Predio Norte (cambios sin guardar).dxf"
    assert b"CIRCLE" in open(bajada.value.path(), "rb").read()
    esperar_estado(p, "sucio")                                               # sigue sin guardar: nada se dio por guardado
    # Sin red: igual.
    n.contexto.unroute(f"**/cad/{p0.pk}/guardar/")
    n.contexto.route(f"**/cad/{p0.pk}/guardar/", lambda r: r.abort())
    p.locator("#cad-guardar").click()
    expect(caja).to_contain_text("revisa tu conexión")
    expect(caja.get_by_role("button", name="Descargar mis cambios")).to_be_visible()
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_si_la_sesion_termina_no_se_recarga_y_se_puede_seguir(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    p.evaluate("window.__sinRecargar = true")
    dibujar(p)
    esperar_estado(p, "sucio")
    sin_sesion = lambda r: r.fulfill(status=401, body="")                     # noqa: E731
    n.contexto.route(f"**/cad/{p0.pk}/**", sin_sesion)
    p.locator("#cad-guardar").click()
    expect(p.locator("#cad-aviso-sesion")).to_be_visible()
    expect(p.locator("#cad-aviso-sesion").get_by_role("button", name="Descargar mis cambios")).to_be_visible()
    expect(p.locator("#cad-entrar")).to_have_attribute("target", "_blank")
    p.wait_for_timeout(3000)                                                   # el latido (2 s en pruebas) tampoco recarga
    assert p.evaluate("window.__sinRecargar === true")
    esperar_estado(p, "sucio")
    # La persona entra en otra pestaña y vuelve: la franja se va y guardar funciona.
    n.contexto.unroute(f"**/cad/{p0.pk}/**", sin_sesion)
    p.evaluate("window.dispatchEvent(new Event('focus'))")
    expect(p.locator("#cad-aviso-sesion")).to_be_hidden()
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    assert p.evaluate("window.__sinRecargar === true")
    assert not n.errores, n.errores


def _borrador(plano, usuario, texto="BORRADOR VIEJO"):
    """Un borrador de recuperación propio en el servidor (DXF con un texto que se reconoce)."""
    import io
    import uuid

    import ezdxf

    d = ezdxf.new("R2018")
    d.modelspace().add_text(texto, dxfattribs={"height": 2.5, "insert": (390000, 3168000)})
    s = io.StringIO(); d.write(s)
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(s.getvalue().encode())
    return servicio.guardar_borrador(plano, usuario, plano.revision, t)


def test_el_borrador_pendiente_no_se_pisa_y_se_pide_elegir(cad, navegar, crear_usuario, roles):
    from itt_herramientas.models import Plano

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    viejo = _borrador(p0, ana)
    ruta = servicio.raiz() / viejo.ruta
    antes = ruta.read_bytes()
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    expect(p.locator("#cad-aviso-borrador")).to_be_visible()
    # Sin tocar el aviso, la persona dibuja: se le pide elegir, y el borrador automático (3 s en pruebas) no pisa el anterior.
    dibujar(p)
    hoja = p.locator("#cad-borrador-elegir")
    expect(hoja).to_be_visible()
    p.keyboard.press("Escape")
    expect(hoja).to_be_visible()                                               # no se cierra sin una respuesta
    p.wait_for_timeout(5000)
    assert ruta.read_bytes() == antes
    hoja.locator("#cad-borrador-elegir-copia").click()
    expect(hoja).to_be_hidden()
    expect(p.locator("#cad-aviso-borrador")).to_be_hidden()
    copia = Plano.objects.get(nombre="Predio Norte (copia)")
    assert b"BORRADOR VIEJO" in servicio.ruta_version(servicio.version_vigente(copia)).read_bytes()
    # Ya decidido: el borrador automático vuelve a proteger lo nuevo.
    for _ in range(60):
        b = servicio.borrador_de(p0, ana.pk)
        if b is not None:
            break
        p.wait_for_timeout(250)
    assert b is not None and b"BORRADOR VIEJO" not in (servicio.raiz() / b.ruta).read_bytes()
    assert not n.errores, n.errores
