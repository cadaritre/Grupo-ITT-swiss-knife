"""CAD: proyectos y carpetas en el navegador (docs/cad.md, «Proyectos y carpetas»). Chromium (con el agente de Windows del dueño) y
WebKit, un motor a la vez, bajo el candado de la máquina.

- «Planos»: crear, renombrar y anidar carpetas; mover arrastrando al árbol y con «Mover a…» solo con el teclado; buscar dentro de una
  carpeta; subir una carpeta soltada (con sus subcarpetas); la papelera de una carpeta y restaurarla entera.
- Las hojas del editor: «Abrir otro plano» y «Desde plantilla › De la biblioteca» recorren el mismo árbol, con sus migas.
- Una referencia externa (xref) de la misma carpeta de la biblioteca: se abre en solo lectura y «Referencias externas» dice de dónde vino.
- El teléfono (toque): la lista con carpetas y una carpeta «Disponible sin conexión».

Los planos son sintéticos (ezdxf).
"""
from __future__ import annotations

import os
from pathlib import Path

import ezdxf
import pytest
from playwright.sync_api import expect

from itt_herramientas.cad import carpetas, servicio
from itt_herramientas.models import CarpetaCad, Plano

from .test_cad_navegador import LARGO, _errores_propios, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_cad_pestanas_navegador import WINDOWS, abrir, esperar_pestanas  # noqa: F401 (fixtures)
from .test_cad_tactil_navegador import INSTALADA, _llevar_al_telefono, plano_utm, tactil, telefono  # noqa: F401 (fixtures)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
MOTORES = ["chromium", "webkit"]
CAPTURAS = os.environ.get("ITT_CAPTURAS", "")


def dxf(ruta: Path, lineas=((0, 0, 100, 0), (0, 0, 0, 100)), xref: str | None = None) -> Path:
    d = ezdxf.new("R2018", setup=True)
    msp = d.modelspace()
    for x0, y0, x1, y1 in lineas:
        msp.add_line((x0, y0), (x1, y1))
    if xref:
        nombre = Path(xref.replace("\\", "/")).stem
        d.add_xref_def(xref, nombre)
        msp.add_blockref(nombre, (0, 0))
    d.saveas(ruta)
    return ruta


def plano_de(usuario, ruta: Path, nombre: str, carpeta=None) -> Plano:
    temporal = servicio.carpeta_temporal() / f"{nombre}-{os.getpid()}.tmp"
    temporal.write_bytes(ruta.read_bytes())
    return servicio.crear(usuario, temporal, nombre, "", carpeta=carpeta)


def vacio(usuario, nombre, carpeta=None):
    return servicio.crear_vacio(usuario, nombre, "", "dxf", carpeta=carpeta)


def menu_de(p, fila, accion):
    fila.locator("[data-cad-menu]").click()
    item = p.locator(f'#cad-menu-fila [data-accion="{accion}"]')
    expect(item).to_be_visible()
    item.click()


# -- «Planos» -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_crear_renombrar_anidar_mover_y_buscar(cad, abrir, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    planta, ejes = vacio(ana, "Planta"), vacio(ana, "Ejes")
    n = abrir(ana, motor, ancho=1280, alto=860)
    p = n.ir("/cad/")
    # Proyecto nuevo, con su cliente
    p.locator("#cad-boton-carpeta").click()
    p.locator("#cad-carpeta-nombre").fill("Obra Norte")
    p.locator("#cad-carpeta-cliente").fill("Agrícola Norte")
    p.locator("#cad-carpeta-nueva-form [type=submit]").click()
    expect(p.locator("#cad-titulo")).to_have_text("Obra Norte")
    expect(p.locator("#cad-subtitulo-lista")).to_contain_text("Proyecto · Agrícola Norte")
    obra = CarpetaCad.objects.get(nombre="Obra Norte")
    # Una carpeta dentro
    p.locator("#cad-boton-carpeta").click()
    p.locator("#cad-carpeta-nombre").fill("Base")
    p.locator("#cad-carpeta-nueva-form [type=submit]").click()
    expect(p.locator("#cad-titulo")).to_have_text("Base")
    expect(p.locator("#cad-migas")).to_have_text("PlanosObra NorteBase")
    # Cambiarle el nombre desde el menú de la carpeta abierta
    p.locator("#cad-carpeta-acciones").click()
    p.locator('#cad-menu-fila [data-accion="renombrar"]').click()
    p.locator("#cad-renombrar-nombre").fill("Base topográfica")
    p.locator("#cad-renombrar-form [type=submit]").click()
    expect(p.locator("#cad-titulo")).to_have_text("Base topográfica")
    base = CarpetaCad.objects.get(nombre="Base topográfica")
    assert base.padre_id == obra.pk
    # El árbol: Obra Norte abierta (camino de la carpeta), con la actual marcada
    expect(p.locator('#cad-arbol [role="treeitem"][aria-selected="true"]')).to_have_attribute("data-carpeta", str(base.pk))
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-carpetas-{motor}-1280.png")

    # Arrastrar «Planta» de la raíz al proyecto en el árbol
    p = n.ir("/cad/")
    fila = p.locator(f'#cad-planos li[data-id="{planta.pk}"]')
    destino = p.locator(f'#cad-arbol [data-carpeta="{obra.pk}"] > .cad-arbol__fila')
    fila.drag_to(destino)
    esperar(p, "document.readyState === 'complete'", LARGO)
    planta.refresh_from_db()
    for _ in range(40):
        if planta.carpeta_id:
            break
        p.wait_for_timeout(250)
        planta.refresh_from_db()
    assert (planta.carpeta_id, planta.proyecto) == (obra.pk, "Obra Norte")

    # «Mover a…» solo con el teclado: menú de la fila, el árbol de destino y «Mover aquí»
    p = n.ir("/cad/")
    boton = p.locator(f'#cad-planos li[data-id="{ejes.pk}"] [data-cad-menu]')
    boton.focus()
    p.keyboard.press("Enter")
    expect(p.locator("#cad-menu-fila")).to_be_visible()
    for _ in range(4):
        if p.evaluate("document.activeElement && document.activeElement.dataset.accion") == "mover":
            break
        p.keyboard.press("ArrowDown")
    p.keyboard.press("Enter")
    hoja = p.locator("dialog.cad-mover")
    expect(hoja).to_be_visible()
    # En el árbol: ↓ al proyecto, → lo abre, ↓ a su carpeta, Intro la elige.
    p.locator(".cad-mover__arbol .cad-arbol__fila[tabindex='0']").focus()
    p.keyboard.press("ArrowDown"); p.keyboard.press("ArrowRight"); p.keyboard.press("ArrowDown"); p.keyboard.press("Enter")
    expect(p.locator(".cad-mover__destino")).to_have_text("Destino: Planos › Obra Norte › Base topográfica")
    p.locator("#cad-mover-ir").focus()
    p.keyboard.press("Enter")
    esperar(p, "!document.querySelector('dialog.cad-mover')", LARGO)
    for _ in range(40):
        ejes.refresh_from_db()
        if ejes.carpeta_id:
            break
        p.wait_for_timeout(250)
    assert ejes.carpeta_id == base.pk

    # Buscar dentro del proyecto (y sus carpetas), con dónde está cada plano
    p = n.ir(f"/cad/carpetas/{obra.pk}/")
    p.locator("#cad-q").fill("ejes")
    p.locator("#cad-q").press("Enter")
    expect(p.locator("#cad-planos")).to_contain_text("Ejes")
    expect(p.locator("#cad-planos")).to_contain_text("En Obra Norte › Base topográfica")
    expect(p.locator("#cad-planos")).not_to_contain_text("Planta")
    assert not _errores_propios(n, motor), n.errores


SOLTAR_CARPETA = """async ([archivos]) => {
  // Una carpeta soltada desde la computadora, como la da el navegador (FileSystemDirectoryEntry), con archivos de verdad.
  const hacer = (ruta, texto) => new File([texto], ruta.split('/').pop(), { type: '' });
  const arbol = {};
  for (const [ruta, texto] of archivos) { let n = arbol; const partes = ruta.split('/'); for (const d of partes.slice(0, -1)) n = (n[d] = n[d] || {}); n[partes.at(-1)] = hacer(ruta, texto); }
  const entrada = (nombre, x) => x instanceof File
    ? { name: nombre, isFile: true, isDirectory: false, file: (ok) => ok(x) }
    : { name: nombre, isFile: false, isDirectory: true, createReader: () => { let dado = false; return { readEntries: (ok) => { ok(dado ? [] : Object.entries(x).map(([k, v]) => entrada(k, v))); dado = true; } }; } };
  const raices = Object.entries(arbol).map(([k, v]) => entrada(k, v));
  const dt = { types: ['Files'], files: [], items: raices.map((e) => ({ webkitGetAsEntry: () => e })) };
  const ev = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'dataTransfer', { value: dt });
  document.querySelector('#cad-planos, .cad-contenido').dispatchEvent(ev);
}"""


@pytest.mark.parametrize("motor", MOTORES)
def test_soltar_una_carpeta_recrea_sus_subcarpetas(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    obra = carpetas.crear(ana, "Obra")
    base_dxf = dxf(tmp_path / "base.dxf").read_text()
    hoja_dxf = dxf(tmp_path / "hoja.dxf", ((0, 0, 10, 10),)).read_text()
    n = abrir(ana, motor)
    p = n.ir(f"/cad/carpetas/{obra.pk}/")
    p.evaluate(SOLTAR_CARPETA, [[["Levantamiento/base.dxf", base_dxf], ["Levantamiento/Planos/Hojas/hoja 1.dxf", hoja_dxf],
                                 ["Levantamiento/notas.txt", "no es un plano"], ["Levantamiento/.DS_Store", "x"]]])
    esperar(p, "document.readyState === 'complete' && !!document.querySelector('#cad-titulo')", LARGO)
    for _ in range(120):
        if Plano.objects.count() == 2:
            break
        p.wait_for_timeout(250)
    arbol = carpetas.Arbol()
    lugares = {x.nombre: arbol.ubicacion(x.carpeta_id) for x in Plano.objects.all()}
    assert lugares == {"base": "Obra › Levantamiento", "hoja 1": "Obra › Levantamiento › Planos › Hojas"}
    assert not Plano.objects.filter(nombre="notas").exists()
    # Al terminar se recarga la carpeta con lo nuevo.
    p.wait_for_load_state()
    expect(p.locator("#cad-planos")).to_contain_text("Levantamiento", timeout=LARGO)
    assert not _errores_propios(n, motor), n.errores


def test_subir_una_carpeta_con_el_boton(cad, abrir, crear_usuario, roles, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    obra = carpetas.crear(ana, "Obra")
    raiz = tmp_path / "Entrega"
    (raiz / "Cortes").mkdir(parents=True)
    dxf(raiz / "planta.dxf")
    dxf(raiz / "Cortes" / "corte A.dxf")
    n = abrir(ana, "chromium")
    p = n.ir(f"/cad/carpetas/{obra.pk}/")
    p.locator("#cad-boton-subir").click()
    p.locator("#cad-subir-carpeta").set_input_files(str(raiz))
    for _ in range(120):
        if Plano.objects.count() == 2:
            break
        p.wait_for_timeout(250)
    arbol = carpetas.Arbol()
    assert {x.nombre: arbol.ubicacion(x.carpeta_id) for x in Plano.objects.all()} == {"planta": "Obra › Entrega", "corte A": "Obra › Entrega › Cortes"}


@pytest.mark.parametrize("motor", MOTORES)
def test_papelera_de_una_carpeta_y_restaurarla_entera(cad, abrir, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    obra = carpetas.crear(ana, "Obra")
    base = carpetas.crear(ana, "Base", obra)
    vacio(ana, "A", obra), vacio(ana, "B", base)
    n = abrir(ana, motor)
    p = n.ir("/cad/")
    menu_de(p, p.locator(f'#cad-planos li[data-carpeta="{obra.pk}"]'), "eliminar")
    expect(p.locator("#cad-eliminar-carpeta-texto")).to_have_text("Se mueve a la papelera con 2 planos y 1 carpeta. Se puede restaurar entera durante 30 días.")
    p.locator("#cad-eliminar-carpeta-ir").click()
    expect(p.locator("#tv-avisos")).to_contain_text("«Obra» se movió a la papelera con 2 planos y 1 carpeta.", timeout=LARGO)
    expect(p.locator(f'#cad-planos li[data-carpeta="{obra.pk}"]')).to_have_count(0)
    p = n.ir("/cad/papelera/")
    fila = p.locator(f'#cad-papelera li[data-carpeta="{obra.pk}"]')
    expect(fila).to_contain_text("Proyecto con 2 planos y 1 carpeta")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-papelera-carpeta-{motor}-1280.png")
    fila.get_by_role("button", name="Restaurar").click()
    expect(p.locator("body")).to_contain_text("«Obra» volvió a la biblioteca con 2 planos.")
    assert Plano.objects.filter(eliminado__isnull=True).count() == 2 and not CarpetaCad.objects.filter(eliminada__isnull=False).exists()
    assert not _errores_propios(n, motor), n.errores


# -- Las hojas del editor --------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_abrir_otro_plano_y_desde_plantilla_recorren_el_arbol(cad, abrir, crear_usuario, roles, motor, tmp_path):
    from .test_cad_plantillas_navegador import menu_de_pestana, plantilla, presentaciones

    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    obra = carpetas.crear(ana, "Obra")
    hojas, base = carpetas.crear(ana, "Hojas", obra), carpetas.crear(ana, "Base", obra)
    a = vacio(ana, "Hoja 1", hojas)
    vacio(ana, "Base topo", base)
    marco = plano_de(ana, plantilla(tmp_path / "marco.dxf"), "Marco A1", servicio_proyecto(ana, "Plantillas"))
    n = abrir(ana, motor, ancho=1366, alto=900)
    p = n.ir(f"/cad/{a.pk}/")
    esperar_estado(p, "guardado")
    # «Abrir otro plano» empieza en la carpeta del plano a la vista, con sus migas y el árbol al lado.
    p.locator("#cad-abrir-otro").click()
    hoja = p.locator("#cad-elegir")
    expect(p.locator("#cad-elegir-migas")).to_have_text("PlanosObraHojas", timeout=LARGO)
    expect(hoja.locator(".cad-elegir__plano", has_text="Hoja 1")).to_contain_text("Abierto")
    expect(p.locator(f'#cad-elegir-arbol [data-carpeta="{hojas.pk}"]')).to_have_attribute("aria-selected", "true")
    hoja.locator("#cad-elegir-migas").get_by_role("button", name="Obra").click()
    expect(hoja.locator(".cad-elegir__carpeta-fila")).to_have_count(2, timeout=LARGO)
    hoja.locator(".cad-elegir__carpeta-fila", has_text="Base").click()
    expect(p.locator("#cad-elegir-migas")).to_have_text("PlanosObraBase", timeout=LARGO)
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-abrir-otro-carpetas-{motor}.png")
    hoja.locator(".cad-elegir__plano", has_text="Base topo").click()
    esperar_pestanas(p, 2)
    # El árbol de la hoja también lleva: clic en «Hojas» del árbol.
    p.locator("#cad-abrir-otro").click()
    p.locator(f'#cad-elegir-arbol [data-carpeta="{hojas.pk}"] > .cad-arbol__fila').click()
    expect(p.locator("#cad-elegir-migas")).to_have_text("PlanosObraHojas", timeout=LARGO)
    p.keyboard.press("Escape")
    # «Desde plantilla › De la biblioteca»: el mismo árbol; en la raíz, «Plantillas» primero.
    esperar(p, "TVCadPlantillas.estado().menu === true", LARGO)
    assert menu_de_pestana(p, presentaciones(p)[-1], "from_template")
    p.locator("label", has_text="De la biblioteca").click()
    lista = p.locator("#cad-plantilla-lista")
    expect(lista.locator(".cad-elegir__carpeta-fila").first).to_contain_text("Plantillas", timeout=LARGO)
    lista.locator(".cad-elegir__carpeta-fila", has_text="Plantillas").click()
    expect(p.locator("#cad-plantilla-migas")).to_have_text("PlanosPlantillas", timeout=LARGO)
    expect(lista.locator(".cad-plantilla__fila", has_text="Marco A1")).to_be_visible()
    assert marco.carpeta.nombre == "Plantillas"
    assert not _errores_propios(n, motor), n.errores


def servicio_proyecto(usuario, nombre):
    return carpetas.proyecto_llamado(nombre, usuario)


# -- Referencias externas desde la biblioteca ----------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_una_referencia_de_la_misma_carpeta_se_abre_en_solo_lectura(cad, abrir, crear_usuario, roles, motor, tmp_path):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    obra = carpetas.crear(ana, "Obra")
    planos, base_dir = carpetas.crear(ana, "Planos", obra), carpetas.crear(ana, "Base", obra)
    plano_de(ana, dxf(tmp_path / "ejes.dxf", ((0, 0, 500, 0), (0, 0, 0, 500))), "ejes", planos)
    plano_de(ana, dxf(tmp_path / "base.dxf", ((10, 10, 300, 300),)), "base", base_dir)
    host = plano_de(ana, dxf(tmp_path / "host.dxf", ((0, 0, 1, 1),), xref="ejes.dxf"), "Planta", planos)
    otro = plano_de(ana, dxf(tmp_path / "otro.dxf", ((0, 0, 1, 1),), xref="..\\Base\\base.dxf"), "Corte", planos)
    n = abrir(ana, motor)
    p = n.ir(f"/cad/{host.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "TVCad.pedir({op: 'references'}).then(r => r.xrefs.length === 1 && r.xrefs[0].status === 'loaded')", LARGO)
    est = p.evaluate("TVCadLocal.estado()")
    assert est["biblioteca"] == [{"ruta": "ejes.dxf", "id": str(Plano.objects.get(nombre="ejes").pk), "ubicacion": "Obra › Planos", "como": "carpeta"}]
    expect(p.locator("#cad-aviso-local")).to_be_hidden()                     # no pide la carpeta de la computadora
    p.locator("#cad-boton-referencias").click()
    fila = p.locator('#cad-local-lista [data-origen="biblioteca"]')
    expect(fila).to_contain_text("En la biblioteca")
    expect(fila).to_contain_text("Biblioteca: Obra › Planos · ejes.dxf · versión 1")
    expect(fila).to_contain_text("abierta (solo lectura)")
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-referencia-biblioteca-{motor}.png")
    # Solo lectura: la referencia no es parte del plano (no se puede borrar como sus objetos) y el plano sigue sin cambios.
    assert p.locator("#cad-estado").get_attribute("data-estado") == "guardado"
    # Por ruta relativa, desde otra carpeta del proyecto.
    p = n.ir(f"/cad/{otro.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "TVCad.pedir({op: 'references'}).then(r => r.xrefs.length === 1 && r.xrefs[0].status === 'loaded')", LARGO)
    assert p.evaluate("TVCadLocal.estado().biblioteca")[0]["ubicacion"] == "Obra › Base"
    assert not _errores_propios(n, motor), n.errores


# -- El teléfono ------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_telefono_con_carpetas_y_una_carpeta_sin_conexion(tactil, telefono, crear_usuario, roles, motor):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    p0 = plano_utm(ana)
    obra = carpetas.crear(ana, "Obra")
    base = carpetas.crear(ana, "Base", obra)
    a, b = vacio(ana, "Hoja A", obra), vacio(ana, "Hoja B", base)
    n = telefono(ana, motor)
    n.contexto.add_init_script("try { localStorage.setItem('tv.cad.tactil', 'true') } catch (e) {}")
    n.contexto.add_init_script(INSTALADA)
    p = n.ir(f"/cad/{p0.pk}/")
    esperar(p, "window.TVCadTactil && TVCadTactil.listo()", LARGO)
    _llevar_al_telefono(n, p, p0)                                            # el CAD en el teléfono (y un plano suelto)
    # La lista con carpetas en el teléfono: sin el árbol de al lado; las carpetas son filas; se entra con un toque.
    p.goto(n.servidor.url + "/cad/")
    expect(p.locator("#cad-arbol-caja")).to_be_hidden()
    fila = p.locator(f'#cad-planos li[data-carpeta="{obra.pk}"]')
    expect(fila).to_be_visible()
    if CAPTURAS:
        p.screenshot(path=f"{CAPTURAS}/cad-carpetas-telefono-{motor}-375.png")
    # «Disponible sin conexión» para la carpeta entera (con su subcarpeta)
    fila.locator("[data-cad-menu]").tap()
    p.locator('#cad-menu-fila [data-accion="sin-conexion"]').tap()
    confirmar = p.locator("dialog.tv-hoja--confirmar[open]")
    expect(confirmar).to_contain_text("2 planos")
    confirmar.get_by_role("button", name="Llevar al teléfono").tap()
    expect(p.locator("#tv-avisos")).to_contain_text("«Obra» quedó en este teléfono: 2 planos.", timeout=LARGO)
    ids = set(p.evaluate("TVCadCampo.planos().then(l => l.map(x => x.id))"))
    assert {str(a.pk), str(b.pk), str(p0.pk)} <= ids
    assert p.evaluate("TVCadCampo.carpetas().then(l => l.map(x => x.nombre))") == ["Obra"]
    expect(fila.locator("[data-cad-campo-marca]")).to_be_visible()
    # Una versión nueva de un plano de la carpeta: al abrir la carpeta se ofrece ponerla al día.
    temporal = servicio.carpeta_temporal() / "nueva.tmp"
    temporal.write_bytes(servicio.ruta_version(servicio.version_vigente(b)).read_bytes() + b"\n999\nnueva\n")
    servicio.bloqueo(b.pk, ana, "sesion-de-prueba-01", "tomar")
    servicio.guardar(b.pk, ana, "sesion-de-prueba-01", 1, temporal)
    servicio.bloqueo(b.pk, ana, "sesion-de-prueba-01", "soltar")
    p.goto(n.servidor.url + f"/cad/carpetas/{obra.pk}/")
    expect(p.locator("#cad-campo-carpeta")).to_contain_text("1 plano nuevo o con versión nueva", timeout=LARGO)
    p.locator("#cad-campo-carpeta-ir").tap()
    esperar(p, f"TVCadCampo.plano('{b.pk}').then(x => x && x.revision === 2)", LARGO)
    # Sin conexión, la página de CAD sin conexión los tiene.
    n.contexto.set_offline(True)
    p.goto(n.servidor.url + "/cad/sin-conexion/")
    expect(p.locator("body")).to_contain_text("Hoja B", timeout=LARGO)
    n.contexto.set_offline(False)
    assert not n.errores, n.errores
