"""CAD en el navegador con el editor real (Open CAD Studio web fijado en upstream.json): biblioteca, abrir, dibujar, guardar versión,
restaurar, solo lectura y toma de control, borrador de recuperación, conflicto como copia, teléfono y la barra de extensiones (TVCad).

El editor se prepara una vez con `python3 itt_herramientas/cad/editor/preparar.py editor --destino DIR` (baja la publicación fijada y
comprueba su SHA-256); DIR sale de `TV_CAD_EDITOR_PRUEBAS` (por omisión ~/.cache/tresvizo-cad/editor). Chromium dibuja con WebGL por
software (SwiftShader). Corre bajo el candado de la máquina compartida.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import editor as cad_editor
from itt_herramientas.cad import servicio
from itt_herramientas.models import Plano, VersionPlano

from .test_pwa_navegador import TIEMPO, esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")
EDITOR = Path(os.environ.get("TV_CAD_EDITOR_PRUEBAS", str(Path.home() / ".cache" / "tresvizo-cad" / "editor")))
LARGO = 90000


@pytest.fixture
def cad(settings):
    if not (EDITOR / cad_editor.version() / "index.html").is_file():
        pytest.skip(f"Prepara el editor: python3 itt_herramientas/cad/editor/preparar.py editor --destino {EDITOR}")
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(EDITOR)
    settings.HERRAMIENTAS_CAD_AUTOGUARDADO_S = 3
    settings.HERRAMIENTAS_CAD_LATIDO_S = 2
    return EDITOR


@pytest.fixture
def sin_tactil(cad, settings):
    """El camino con el modo táctil apagado (TV_CAD_TACTIL): en el teléfono el plano se abre para ver. Con el ajuste encendido, el
    teléfono abre el modo táctil (test_cad_tactil_navegador.py); estas pruebas fijan el apagado para correr igual con cualquiera."""
    settings.TV_CAD_TACTIL = False
    return cad


def _errores_propios(n, motor):
    """En WebKit la captura de Playwright inyecta un estilo que la CSP de la página (con razón) rechaza: no es un error de la página."""
    return [e for e in n.errores if not (motor == "webkit" and "Refused to apply a stylesheet" in e)]


def _persona(crear_usuario, roles, usuario, nombre, *rol):
    u = crear_usuario(usuario)
    u.first_name, u.last_name = nombre.split(" ", 1)
    u.email = f"{usuario}@grupoitt.com"
    u.save()
    roles.conceder(u, "herramientas", *rol)
    return u


class Navegador:
    """Un contexto con su sesión; junta los errores de JavaScript y de la CSP de la página (y del editor)."""

    def __init__(self, playwright_, motor, servidor, cookie, ancho=1280, alto=860, movil=False, user_agent=None, sin_service_worker=False, escala=None):
        tipo = getattr(playwright_, motor)
        args = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] if motor == "chromium" else []
        self.navegador = tipo.launch(args=args)
        extra = {"user_agent": user_agent} if user_agent else {}
        # Las pruebas que cortan la red con `route` en WebKit: Playwright no ve lo que pasa por el service worker (que controla la página
        # desde que guarda el editor en el equipo), así que en ellas no se registra.
        if sin_service_worker:
            extra["service_workers"] = "block"
        if escala:
            extra["device_scale_factor"] = escala  # escala de pantalla fraccionaria (Windows: 1.25, 1.5)
        self.contexto = self.navegador.new_context(viewport={"width": ancho, "height": alto}, is_mobile=movil and motor == "chromium", has_touch=movil,
                                                   locale="es-MX", accept_downloads=True, **extra)
        self.contexto.add_cookies([cookie])
        self.errores, self.dialogos = [], []
        self.pagina = self.contexto.new_page()
        self.pagina.set_default_timeout(LARGO)
        self.pagina.on("pageerror", lambda e: self.errores.append(f"pageerror: {e}"))
        self.pagina.on("console", lambda m: self.errores.append(f"console: {m.text}") if m.type == "error" and "Content Security Policy" in m.text else None)
        self.pagina.on("dialog", lambda d: (self.dialogos.append(d.type), d.dismiss()))
        self.servidor = servidor

    def ir(self, ruta):
        self.pagina.goto(self.servidor.url + ruta)
        return self.pagina

    def cerrar(self):
        self.navegador.close()


@pytest.fixture
def navegar(playwright_, servidor, sesion_de):
    abiertos = []

    def abrir(usuario, motor="chromium", **kw):
        n = Navegador(playwright_, motor, servidor, sesion_de(usuario), **kw)
        abiertos.append(n)
        return n
    yield abrir
    for n in abiertos:
        n.cerrar()


def estado(p):
    return p.locator("#cad-estado").get_attribute("data-estado")


def esperar_estado(p, valor, timeout=LARGO):
    esperar(p, f"(document.getElementById('cad-estado') || {{}}).dataset && document.getElementById('cad-estado').dataset.estado === '{valor}'", timeout)


def dibujar(p, cmd="CIRCLE 0,0 25"):
    """Una edición por el canal de control (la misma vía de las extensiones: window.TVCad)."""
    esperar(p, "window.TVCad && TVCad.estaListo()")
    r = p.evaluate("c => TVCad.ejecutar(c).then(r => r.ok !== false)", cmd)
    assert r


MOTORES = ["chromium", "webkit"]


@pytest.mark.parametrize("motor", MOTORES)
def test_abrir_dibujar_guardar_y_restaurar(cad, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Predio Norte", "Agrícola Norte", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    assert p.locator("#cad-guardar").is_disabled()
    if CAPTURAS:
        p.wait_for_timeout(1500)
        p.screenshot(path=f"{CAPTURAS}/cad-editor-{motor}-1280.png")
    dibujar(p)
    esperar_estado(p, "sucio")
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    expect(p.locator("#cad-subtitulo")).to_have_text("Agrícola Norte · Versión 2 · Ana López")
    v2 = VersionPlano.objects.get(plano=p0, numero=2)
    assert b"CIRCLE" in servicio.ruta_version(v2).read_bytes() and v2.autor_nombre == "Ana López"
    # Ctrl/⌘+S dentro del editor también guarda en la biblioteca (vía del propio editor)
    dibujar(p, "LINE 0,0 40,40")
    esperar_estado(p, "sucio")
    p.locator("#cad-editor").click(position={"x": 600, "y": 420})
    p.keyboard.press("Meta+s" if motor == "webkit" else "Control+s")          # ⌘S en Mac lo reenvía nuestra página de arranque
    esperar(p, "document.getElementById('cad-estado').dataset.estado === 'guardado'")
    esperar(p, "true", 1)
    assert VersionPlano.objects.filter(plano=p0).count() == 3
    # Restaurar la versión 1 desde «Versiones»
    p.locator("#cad-boton-versiones").click()
    p.locator('[data-cad-restaurar="1"]').click()
    expect(p.locator("#cad-versiones-lista li")).to_have_count(4, timeout=LARGO)
    esperar_estado(p, "guardado")
    p0.refresh_from_db()
    assert p0.revision == 4 and VersionPlano.objects.get(plano=p0, numero=4).nota == "Restaurada de la versión 1"
    assert not n.dialogos and not _errores_propios(n, motor), (n.dialogos, n.errores)


def test_solo_lectura_y_tomar_el_control(cad, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    jefe = _persona(crear_usuario, roles, "jefe", "Jorge Admin", "admin")
    p0 = servicio.crear_vacio(ana, "Bodega", "", "dwg")
    a = navegar(ana)
    pa = a.ir(f"/cad/{p0.pk}/")
    esperar_estado(pa, "guardado")
    b = navegar(jefe)
    pb = b.ir(f"/cad/{p0.pk}/")
    esperar_estado(pb, "lectura")
    expect(pb.locator("#cad-aviso-lectura")).to_contain_text("Ana López está editando este plano")
    if CAPTURAS:
        pb.screenshot(path=f"{CAPTURAS}/cad-solo-lectura-chromium-1280.png")
    dibujar(pa, "CIRCLE 5,5 5")
    esperar_estado(pa, "sucio")
    pb.locator("#cad-tomar").click()
    esperar_estado(pb, "guardado")
    # Ana pierde el control con el siguiente latido: sus cambios se ofrecen como copia, nada se pisa.
    esperar(pa, "!document.getElementById('cad-aviso-lectura').hidden")
    expect(pa.locator("#cad-aviso-lectura")).to_contain_text("Jorge Admin tomó el control")
    pa.locator("#cad-guardar").click()
    pa.wait_for_url(lambda u: f"/cad/{p0.pk}/" not in u)
    copia = Plano.objects.exclude(pk=p0.pk).get()
    assert copia.nombre == "Bodega (copia)" and copia.origen["tipo"] == "copia"
    p0.refresh_from_db()
    assert p0.revision == 1
    assert not a.dialogos and not a.errores and not b.errores, (a.errores, b.errores)


def test_borrador_de_recuperacion_sin_aviso_del_navegador(cad, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Lote 7", "", "dxf")
    n = navegar(ana)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p, "CIRCLE 1,1 9")
    esperar_estado(p, "sucio")
    t = time.monotonic()
    while not servicio.borrador_de(p0, ana.pk) and time.monotonic() - t < 30:
        time.sleep(0.5)
    assert servicio.borrador_de(p0, ana.pk), "no se guardó el borrador de recuperación"
    assert VersionPlano.objects.filter(plano=p0).count() == 1                      # el borrador no es una versión
    p.reload()                                                                      # cerrar sin guardar: el navegador no pregunta
    expect(p.locator("#cad-aviso-borrador")).to_be_visible()
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-recuperar-chromium-1280.png")
    esperar_estado(p, "guardado")
    p.locator("#cad-recuperar").click()
    esperar_estado(p, "sucio")
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado")
    assert b"CIRCLE" in servicio.ruta_version(VersionPlano.objects.get(plano=p0, numero=2)).read_bytes()
    assert not servicio.borrador_de(p0, ana.pk)
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)


def test_conflicto_se_guarda_como_copia(cad, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Casa", "", "dwg")
    n = navegar(ana)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p, "CIRCLE 0,0 3")
    esperar_estado(p, "sucio")
    # Mientras, aparece una versión nueva (p. ej. alguien restauró una versión antes de que Ana abriera): la base de Ana ya no es la vigente.
    tmp = servicio.carpeta_temporal() / "x.tmp"
    tmp.write_bytes((Path(servicio.__file__).parent / "plantillas" / "vacio.dwg").read_bytes())
    servicio._nueva_version(Plano.objects.get(pk=p0.pk), tmp, "dwg", ana, nota="otra")
    p.locator("#cad-guardar").click()
    expect(p.locator("#cad-conflicto")).to_be_visible()
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-conflicto-chromium-1280.png")
    p.locator("#cad-guardar-copia").click()
    p.wait_for_url(lambda u: f"/cad/{p0.pk}/" not in u)
    assert Plano.objects.exclude(pk=p0.pk).get().origen["tipo"] == "copia"
    assert VersionPlano.objects.filter(plano=p0).count() == 2
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_telefono_abre_para_ver_y_la_biblioteca_cabe(sin_tactil, navegar, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Plano con un nombre bastante largo para un teléfono", "Agrícola Norte del Valle", "dwg")
    n = navegar(ana, motor, ancho=360, alto=740, movil=True)
    p = n.ir("/cad/")                                                        # el proyecto, como fila de carpeta
    expect(p.locator(".cad-plano.cad-carpeta")).to_have_count(1)
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    p.locator(".cad-plano__abrir").click()
    expect(p.locator("#cad-titulo")).to_have_text("Agrícola Norte del Valle")
    expect(p.locator(".cad-plano")).to_have_count(1)
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    p.locator(".cad-plano__abrir").click()
    esperar_estado(p, "lectura")
    expect(p.locator("#cad-aviso-lectura")).to_contain_text("En el teléfono los planos se abren para ver")
    assert p.locator("#cad-guardar").is_disabled()
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-telefono-{motor}-360.png")
    assert not _errores_propios(n, motor), n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_un_plano_grande_se_anuncia_en_el_telefono_antes_de_bajar_el_editor(sin_tactil, navegar, crear_usuario, roles, motor):
    """docs/cad.md, «Planos grandes»: en un equipo táctil, un plano que no cabe en la memoria que ese navegador deja a una pestaña se
    dice antes de bajar el editor y el plano (el marco sigue en about:blank), con «Descargar el plano» e «Intentar abrirlo aquí». En la
    computadora el mismo plano abre sin aviso. El tamaño se finge en la base: el aviso solo lee el tamaño del plano."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Plano maestro", "Agrícola Norte", "dwg")
    Plano.objects.filter(pk=p0.pk).update(bytes=48 * 1048576)
    n = navegar(ana, motor, ancho=375, alto=812, movil=True)
    # Un teléfono Android de 4 GB (WebKit no tiene `deviceMemory`, como Safari en el iPhone).
    n.contexto.add_init_script("try { Object.defineProperty(navigator, 'deviceMemory', { get: () => 4 }); } catch (e) {}")
    p = n.ir(f"/cad/{p0.pk}/")
    expect(p.locator("#cad-grande")).to_be_visible()
    expect(p.locator("#cad-grande-texto")).to_contain_text("Pesa 48 MB")
    assert p.locator("#cad-editor").get_attribute("src") == "about:blank"
    necesaria, tope = p.evaluate("[TVCadMemoria.necesariaMB(48 * 1048576, 'dwg'), TVCadMemoria.topeMB()]")
    assert necesaria > tope
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-grande-telefono-{motor}-375.png")
    p.locator("#cad-grande-intentar").click()
    expect(p.locator("#cad-grande")).to_be_hidden()
    esperar_estado(p, "lectura")
    # En la computadora: sin aviso, y la carga dice cuánto lleva («Abriendo el plano… N %»).
    c = navegar(ana, motor)
    c.contexto.add_init_script("""addEventListener('DOMContentLoaded', () => { window.__textos = [];
        const t = document.getElementById('cad-carga-texto');
        if (t) new MutationObserver(() => window.__textos.push(t.textContent)).observe(t, {childList: true, characterData: true, subtree: true}); });""")
    q = c.ir(f"/cad/{p0.pk}/")
    esperar_estado(q, "guardado")
    assert q.locator("#cad-grande").is_hidden()
    textos = q.evaluate("window.__textos")
    assert any(t.startswith("Abriendo el plano… ") and t.endswith(" %") for t in textos), textos
    assert not _errores_propios(n, motor) and not _errores_propios(c, motor), (n.errores, c.errores)


def test_tvcad_da_la_barra_y_el_estado_a_las_extensiones(cad, navegar, crear_usuario, roles):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Extensiones", "", "dxf")
    n = navegar(ana)
    p = n.ir(f"/cad/{p0.pk}/")
    error = p.evaluate("TVCad.pedir({op: 'state'}).then(() => 'sin error', e => e.codigo)")
    assert error in ("no_listo", "sin error")
    esperar(p, "TVCad.estaListo()")
    datos = p.evaluate("""async () => {
        const visto = [];
        TVCad.on('cambio', d => visto.push(d.sucio));
        const barra = TVCad.barra();
        const b = document.createElement('button'); b.className = 'tv-boton tv-boton--secundario'; b.textContent = 'Civil'; barra.append(b);
        const estado = TVCad.estado();
        const r = await TVCad.pedir({op: 'state'});
        let rechazo = '';
        try { await TVCad.pedir({op: 'operacion_que_no_existe'}); } catch (e) { rechazo = e.codigo; }
        await TVCad.ejecutar('CIRCLE 0,0 2');
        await new Promise(r => setTimeout(r, 2500));
        return {estado, ok: r.ok !== false, rechazo, barra: !barra.hidden, visto};
    }""")
    assert datos["estado"]["editable"] is True and datos["estado"]["plano"]["id"] == str(p0.pk) and datos["ok"]
    assert datos["rechazo"] == "rechazada" and datos["barra"] and True in datos["visto"]
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-barra-extensiones-chromium-1280.png")
    assert not n.errores, n.errores


@pytest.mark.parametrize("ancho", [360, 1280])
def test_biblioteca_nuevo_subir_buscar_y_zip(sin_tactil, navegar, crear_usuario, roles, ancho, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    n = navegar(ana, ancho=ancho, alto=820, movil=ancho < 768)
    p = n.ir("/cad/")
    expect(p.get_by_text("Todavía no hay planos.")).to_be_visible()
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-vacia-chromium-{ancho}.png", full_page=True)
    p.locator("#cad-boton-nuevo").click()
    p.fill("#cad-nuevo-nombre", "Levantamiento predio Norte")
    p.locator("#cad-nuevo button[type=submit]").click()
    p.wait_for_url(lambda u: "/cad/" in u and u.rstrip("/").count("/") > 3)
    esperar_estado(p, "lectura" if ancho < 768 else "guardado")
    n.ir("/cad/")
    p.locator("#cad-boton-subir").click()
    archivo = tmp_path / "Plano del lote.dxf"
    archivo.write_bytes((Path(servicio.__file__).parent / "plantillas" / "vacio.dxf").read_bytes())
    p.set_input_files("#cad-subida input[type=file]", str(archivo))
    p.wait_for_url(lambda u: u.rstrip("/").count("/") > 3, timeout=LARGO)
    n.ir("/cad/")
    expect(p.locator(".cad-plano")).to_have_count(2)
    assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-biblioteca-chromium-{ancho}.png", full_page=True)
    p.fill("#cad-q", "lote")
    with p.expect_navigation():
        p.press("#cad-q", "Enter")
    expect(p.locator(".cad-plano")).to_have_count(1)
    n.ir("/cad/")
    for c in p.locator("[data-cad-sel]").all():
        c.check()
    with p.expect_download(timeout=LARGO) as descarga:
        p.locator("#cad-sel-zip").click()
    assert descarga.value.suggested_filename.startswith("Planos ")
    assert not n.errores, n.errores


@pytest.mark.skipif(not CAPTURAS, reason="solo con ITT_CAPTURAS=carpeta: capturas de todas las pantallas de CAD")
@pytest.mark.parametrize("tema", ["claro", "oscuro"])
@pytest.mark.parametrize("ancho", [360, 768, 1280])
def test_capturas_de_todas_las_pantallas(sin_tactil, navegar, crear_usuario, roles, settings, ancho, tema, tmp_path):
    """Biblioteca, filtros, subir, nuevo, selección, editor, sin guardar, versiones, imprimir, más acciones, solo lectura y papelera."""
    from tvplataforma.models import Preferencias

    motor = tmp_path / "OpenCADStudio"
    motor.write_text("#!/bin/sh\n")
    motor.chmod(0o755)
    settings.HERRAMIENTAS_CAD_NATIVO = str(motor)                         # solo para que se vea la hoja de impresión
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad", "admin")
    beto = _persona(crear_usuario, roles, "beto", "Beto Sáenz", "cad")
    Preferencias.objects.create(usuario_id=ana.pk, tema=tema)
    movil = ancho < 768
    n = navegar(ana, ancho=ancho, alto=780 if movil else 860, movil=movil)
    p = n.pagina
    previo = f"{CAPTURAS}/cad-{ancho}-{tema}"

    def foto(nombre, completa=True):
        p.wait_for_timeout(400)
        p.screenshot(path=f"{previo}-{nombre}.png", full_page=completa)
        assert p.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"), nombre

    n.ir("/cad/")
    foto("01-biblioteca-vacia")
    datos = [("Levantamiento predio Norte", "Agrícola Norte", "dwg", ana), ("Planta arquitectónica casa 3", "Familia Pérez", "dxf", beto),
             ("Curvas de nivel lote 7", "Rancho El Sauz", "dwg", beto), ("Deslinde parcela 22", "Agrícola Norte", "dxf", ana)]
    planos = [servicio.crear_vacio(u, nombre, proyecto, fmt) for nombre, proyecto, fmt, u in datos]
    servicio.bloqueo(planos[1].pk, beto, "sesion-beto-000001", "tomar")
    n.ir("/cad/")
    foto("02-biblioteca")
    p.locator("#cad-mas").click()
    foto("03-filtros")
    p.locator("#cad-boton-subir").click()
    foto("04-subir", completa=False)
    n.ir("/cad/")
    p.locator("#cad-boton-nuevo").click()
    foto("05-nuevo-plano", completa=False)
    n.ir("/cad/")
    p.locator("[data-cad-sel]").first.check()
    p.locator("[data-cad-sel]").nth(1).check()
    foto("06-seleccion-zip")
    n.ir(f"/cad/{planos[0].pk}/")
    esperar_estado(p, "lectura" if movil else "guardado")
    p.wait_for_timeout(1500)
    foto("07-editor", completa=False)
    if not movil:
        dibujar(p, "CIRCLE 0,0 20")
        esperar_estado(p, "sucio")
        foto("08-editor-sin-guardar", completa=False)
        p.locator("#cad-guardar").click()
        esperar_estado(p, "guardado")
    p.locator("#cad-boton-versiones").click()
    foto("09-versiones", completa=False)
    p.keyboard.press("Escape")
    p.locator("#cad-boton-imprimir").click()
    foto("10-imprimir", completa=False)
    p.keyboard.press("Escape")
    p.locator("#cad-menu summary").click()
    foto("11-mas-acciones", completa=False)
    p.keyboard.press("Escape")
    n.ir(f"/cad/{planos[1].pk}/")
    esperar_estado(p, "lectura")
    p.wait_for_timeout(1000)
    foto("12-editor-solo-lectura", completa=False)
    servicio.eliminar(planos[3], ana)
    n.ir("/cad/papelera/")
    foto("13-papelera")
    p.locator("[data-tv-abrir=cad-vaciar]").click()
    foto("14-vaciar-papelera", completa=False)
    assert not n.errores, n.errores


@pytest.mark.parametrize("motor", MOTORES)
def test_salir_con_cambios_sin_aviso_del_navegador(cad, navegar, crear_usuario, roles, motor):
    """El editor registraría «¿Abandonar el sitio?» al tener cambios (lo destapó el recorrido por la pila real); la página no lo permite."""
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = servicio.crear_vacio(ana, "Salir", "", "dxf")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar_estado(p, "guardado")
    dibujar(p, "CIRCLE 2,2 2")
    esperar_estado(p, "sucio")
    p.locator("#cad-editor").click(position={"x": 600, "y": 420})          # una interacción: sin ella el navegador no muestra el aviso
    p.reload(wait_until="domcontentloaded", timeout=30000)
    esperar_estado(p, "guardado")
    assert not n.dialogos, n.dialogos
