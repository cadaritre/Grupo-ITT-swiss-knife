"""CAD: regresiones de la auditoría J (15-11-2026) que necesitan el navegador: copias en el navegador (A-1), no poder guardar (M-3),
sesión terminada (M-4), borrador pendiente (M-6), bloqueo retomado tras un guardado ajeno (M-7), «Guardar y salir» fallido (B-6) y espera de tareas (B-7).
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


def test_si_otro_guardo_mientras_el_bloqueo_caduco_se_avisa_y_se_guarda_como_copia(cad, navegar, crear_usuario, roles):
    import datetime as dt
    import uuid

    from django.utils import timezone

    from itt_herramientas.models import Plano

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    beto = _persona(crear_usuario, roles, "beto", "Beto Ruiz", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p)
    esperar_estado(p, "sucio")
    # El equipo de Ana «durmió»: su bloqueo caducó y Beto guardó la versión 2 y salió.
    Plano.objects.filter(pk=p0.pk).update(bloqueo_latido=timezone.now() - dt.timedelta(minutes=5))
    sesion_beto = uuid.uuid4().hex[:24]
    assert servicio.bloqueo(p0.pk, beto, sesion_beto, "tomar")["mio"]
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(servicio.ruta_version(servicio.version_vigente(p0)).read_bytes())
    servicio.guardar(p0.pk, beto, sesion_beto, 1, t)
    servicio.bloqueo(p0.pk, beto, sesion_beto, "soltar")
    # El siguiente latido de Ana (2 s en pruebas) se da cuenta: lo dice, suelta el bloqueo y guardar crea una copia.
    expect(p.locator("#cad-aviso-lectura")).to_contain_text("Beto Ruiz guardó una versión nueva de este plano mientras no estabas", timeout=LARGO)
    expect(p.locator("#cad-guardar")).to_contain_text("Guardar como copia")
    p0.refresh_from_db()
    assert not servicio.bloqueo_activo(p0)
    p.locator("#cad-guardar").click()
    p.wait_for_url(lambda u: str(p0.pk) not in u, timeout=LARGO)
    assert Plano.objects.filter(nombre="Predio Norte (copia)").exists()
    p0.refresh_from_db()
    assert p0.revision == 2                                                    # nada se pisó
    assert not n.errores, n.errores


def test_guardar_y_salir_que_falla_no_deja_la_salida_programada(cad, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p)
    esperar_estado(p, "sucio")
    n.contexto.route(f"**/cad/{p0.pk}/guardar/", lambda r: r.abort())
    p.locator("#cad-volver").click()
    p.locator("#cad-salir-guardar").click()
    expect(p.locator("#cad-aviso-error")).to_contain_text("revisa tu conexión")
    n.contexto.unroute(f"**/cad/{p0.pk}/guardar/")
    # Más tarde, un «Guardar» normal guarda y la persona se queda en el plano.
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    p.wait_for_timeout(1500)
    assert p.url.rstrip("/").endswith(str(p0.pk))
    assert not n.errores, n.errores


def test_la_espera_de_una_tarea_termina_si_el_servidor_no_responde_la_tarea(cad, navegar, crear_usuario, roles, settings):
    """Auditoría B-7: con una página de error en lugar del estado de la tarea, «Descargar como…» avisa en vez de esperar para siempre."""
    import json
    import uuid

    settings.HERRAMIENTAS_CAD_NATIVO = "/bin/sh"                              # el botón aparece; la tarea no llega a correr (ruta interceptada)
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, "chromium")
    tarea = str(uuid.uuid4())
    n.contexto.route(f"**/cad/{p0.pk}/convertir/", lambda r: r.fulfill(status=202, content_type="application/json",
                                                                          body=json.dumps({"tarea": tarea, "terminada": False})))
    n.contexto.route(f"**/tareas/{tarea}/**", lambda r: r.fulfill(status=500, content_type="text/html", body="<h1>Error del servidor</h1>"))
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    p.locator("#cad-menu summary").click()
    p.locator("#cad-convertir").click()
    expect(p.locator("#tv-avisos")).to_contain_text("No se pudo saber cómo va", timeout=15000)


def test_un_borrador_de_otra_pagina_no_se_pisa_en_silencio(cad, navegar, crear_usuario, roles):
    """M-6, con el servidor: mientras esta página edita, otra página de la misma cuenta deja un borrador; el borrador automático de esta
    no lo reemplaza (409 `borrador_pendiente`) y se pide elegir."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "", "dxf")
    n = navegar(ana, "chromium")
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    otro = _borrador(p0, ana, "DE OTRA VENTANA")                              # sin sesión de esta página
    antes = (servicio.raiz() / otro.ruta).read_bytes()
    dibujar(p)
    expect(p.locator("#cad-borrador-elegir")).to_be_visible(timeout=LARGO)
    assert (servicio.raiz() / otro.ruta).read_bytes() == antes
    p.locator("#cad-borrador-elegir-copia").click()
    expect(p.locator("#cad-borrador-elegir")).to_be_hidden()
    from itt_herramientas.models import Plano

    assert Plano.objects.filter(nombre="Predio Norte (copia)").exists()
    assert not n.errores, n.errores
