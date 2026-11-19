"""Civil en el navegador con el editor real: archivos de puntos con delimitador propio y columnas en cualquier orden (con formato guardado),
KML y KMZ (importar, exportar y volver a importar) y la triangulación de proyecto de punta a punta (puntos de proyecto, triangulación, curvas,
perfil, secciones y volumen leyendo la superficie de proyecto). Chromium y WebKit, UTM zona 14.

Corre bajo el candado pesado de la máquina, un navegador a la vez:
    TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor pytest itt_herramientas/pruebas_navegador/test_civil_puntos_kml_navegador.py -k chromium
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from pyproj import Transformer

from itt_herramientas.cad import servicio
from itt_herramientas.models import FormatoPuntos

from .test_cad_navegador import LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_civil_navegador import (X0, Y0, MOTORES, archivo_pnezd, cuenta, elegir_en_dibujo, enfocar, esperar_aviso, esperar_libre, menu, pedir,  # noqa: F401
                                   poner_aqui)
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]
KML = "{http://www.opengis.net/kml/2.2}"


def abrir_plano(navegar, crear_usuario, roles, motor, nombre):
    ana = _persona(crear_usuario, roles, "ana", "Ana López", "cad")
    plano = servicio.crear_vacio(ana, nombre, "", "dwg")
    n = navegar(ana, motor)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); }; }")
    return ana, plano, n, p


def preparar_pagina(n, plano):
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    esperar(p, "!!document.querySelector('.civil-barra [data-civil-menu]')", LARGO)
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); }; }")
    return p


def leida(p):
    """Espera a que la vista previa de importar puntos termine de leer (cada cambio la vuelve a pedir al servidor)."""
    p.wait_for_timeout(200)
    esperar(p, "!document.getElementById('civil-imp-vista').dataset.leyendo", LARGO)


def entidades(p, **filtro):
    return pedir(p, {"op": "query", "detail": "full", "limit": 10000, **filtro}).get("entities", [])


# -- Archivos de puntos: delimitador propio, columnas en cualquier orden y formato guardado -----------------------------------------------

def archivo_raro(n=40, desfase=0.0):
    """Delimitado por «::», con coma decimal, dos renglones de título y las columnas Cota, Este, Norte, Punto, Nota."""
    filas = ["LEVANTAMIENTO DEL COLECTOR X", "Cota::Este::Norte::Pt::Obs"]
    for i in range(n):
        x, y, z = 10 + 2.5 * (i % 8) + desfase, 20 + 3.5 * (i // 8), 100.25 + 0.125 * i
        filas.append(f"{z:.3f}::{X0 + x:.3f}::{Y0 + y:.3f}::{i + 1}::{'BN' if i == 0 else 'TN'}".replace(".", ","))
    return ("\r\n".join(filas) + "\r\n").encode()


@pytest.mark.parametrize("motor", MOTORES)
def test_puntos_delimitador_propio_columnas_y_formato_guardado(cad, navegar, crear_usuario, roles, motor, tmp_path):
    ana, plano, n, p = abrir_plano(navegar, crear_usuario, roles, motor, "Colector X")
    archivo = tmp_path / "colector.txt"
    archivo.write_bytes(archivo_raro())

    # 1. el archivo no se reconoce solo: la hoja muestra los renglones para decir cómo está escrito
    menu(p, "puntos", "importar")
    p.locator("#civil-imp-archivo").set_input_files(str(archivo))
    esperar(p, "document.querySelectorAll('#civil-imp-vista [data-civil-col]').length > 0", LARGO)
    # delimitador propio, coma decimal, dos renglones que no se leen
    p.locator("#civil-imp-delim").select_option("otro")
    p.locator("#civil-imp-delim-otro").fill("::")
    p.locator("#civil-imp-dec").select_option(",")
    p.locator("#civil-imp-saltar").fill("2")
    p.wait_for_timeout(700)                                                # la última lectura sale después de una pausa al escribir
    leida(p)
    esperar(p, "document.querySelectorAll('#civil-imp-vista [data-civil-col]').length === 5", LARGO)
    # las columnas, en el orden que se quiera: Cota, Este, Norte, Punto, Nota
    for i, c in enumerate(["Z", "E", "N", "P", "D"]):
        p.locator(f'#civil-imp-vista [data-civil-col="{i}"]').select_option(c)
        leida(p)
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    assert "40 puntos" in p.locator("#civil-imp-vista").inner_text()
    assert "Importar 40 puntos" in p.locator('[data-civil-form="importar"] [data-civil-enviar]').inner_text()

    # 2. guardar el formato con nombre
    p.locator("[data-civil-form=importar] [data-civil-formato-nuevo]").click()
    p.locator("#civil-imp-nombre-formato").fill("Colector X")
    p.locator("[data-civil-form=importar] [data-civil-formato-confirmar]").click()
    esperar_aviso(p, "Formato «Colector X» guardado")
    guardado = FormatoPuntos.objects.get(cuenta_id=ana.pk, uso="importar", nombre="Colector X").datos
    assert guardado == {"delimitador": "::", "juntar": False, "calificador": '"', "decimal": ",", "columnas": ["Z", "E", "N", "P", "D"], "saltar": 2}
    assert p.locator("#civil-imp-guardado").input_value() == "Colector X"

    # 3. importar: los puntos quedan donde van y un solo Deshacer los quita
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO * 2)
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 40
    puntos = {e["attributes"]["NUM"]: e for e in entidades(p, type="INSERT", layer="PUNTOS_TERRENO", fields=["position", "attributes"])}
    e1, e40 = puntos["1"], puntos["40"]
    assert e1["attributes"]["DESC"] == "BN" and e1["position"] == pytest.approx([X0 + 10, Y0 + 20, 100.25], abs=1e-6)
    assert e40["position"] == pytest.approx([X0 + 10 + 2.5 * 7, Y0 + 20 + 3.5 * 4, 100.25 + 0.125 * 39], abs=1e-6)
    pedir(p, {"op": "undo"})
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 0
    pedir(p, {"op": "redo"})
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 40

    # 4. otra sesión (el plano sin guardar se pierde al recargar): el formato sigue en la cuenta y con él el mismo archivo se lee sin tocar nada
    p = preparar_pagina(n, plano)
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 0
    menu(p, "puntos", "importar")
    esperar(p, "[...document.querySelectorAll('#civil-imp-guardado option')].some(o => o.value === 'Colector X')", LARGO)
    p.locator("#civil-imp-guardado").select_option("Colector X")
    p.locator("#civil-imp-archivo").set_input_files(str(archivo))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    assert "40 puntos" in p.locator("#civil-imp-vista").inner_text()
    assert p.locator("#civil-imp-delim").input_value() == "otro" and p.locator("#civil-imp-delim-otro").input_value() == "::"
    assert [p.locator(f'#civil-imp-vista [data-civil-col="{i}"]').input_value() for i in range(5)] == ["Z", "E", "N", "P", "D"]
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO * 2)
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 40
    menu(p, "puntos", "importar")
    # borrar el formato pide un segundo toque
    p.locator("[data-civil-form=importar] [data-civil-formato-borrar]").click()
    assert FormatoPuntos.objects.filter(cuenta_id=ana.pk, uso="importar").count() == 1
    p.locator("[data-civil-form=importar] [data-civil-formato-borrar]").click()
    esperar_aviso(p, "Formato «Colector X» borrado")
    assert FormatoPuntos.objects.filter(cuenta_id=ana.pk).count() == 0
    p.locator("#civil-hoja-importar [data-tv-cerrar]").first.click()

    # 5. exportar con otro formato: punto y coma, coma decimal, Punto-Elevación-Este-Norte, con nombres de columna
    menu(p, "puntos", "exportar")
    esperar(p, "document.querySelector('[data-civil-ejemplo]').textContent.includes(',')", LARGO)
    p.locator("#civil-exp-delim").select_option(";")
    p.locator("#civil-exp-decimal").select_option(",")
    p.locator("#civil-exp-decz").select_option("2")
    p.locator("#civil-exp-enc").check()
    for i, c in enumerate(["P", "Z", "E", "N"]):
        if i >= p.locator("[data-civil-form=exportar] [data-civil-col]").count():
            p.locator("[data-civil-form=exportar] [data-civil-columna-agregar]").click()
        p.locator("[data-civil-form=exportar] [data-civil-col]").nth(i).select_option(c)
    while p.locator("[data-civil-form=exportar] [data-civil-col]").count() > 4:
        p.locator("[data-civil-form=exportar] [data-civil-columna-quitar]").last.click()
    esperar(p, "document.querySelector('[data-civil-ejemplo]').textContent.startsWith('Punto;Elevacion;Este;Norte')", LARGO)
    ejemplo = p.locator("[data-civil-ejemplo]").inner_text().splitlines()
    assert ejemplo[1].count(";") == 3 and "," in ejemplo[1] and "." not in ejemplo[1]
    p.locator("[data-civil-form=exportar] [data-civil-formato-nuevo]").click()
    p.locator("#civil-exp-nombre-formato").fill("Para Excel")
    p.locator("#civil-exp-nombre-formato").press("Enter")                      # Enter guarda el formato, no manda la hoja
    esperar_aviso(p, "Formato «Para Excel» guardado")
    assert FormatoPuntos.objects.get(cuenta_id=ana.pk, uso="exportar").datos["columnas"] == ["P", "Z", "E", "N"]
    assert not p.locator('[data-civil-form="exportar"] button[type=submit]').is_disabled(), p.locator("[data-civil-ejemplo]").inner_text()
    try:
        with p.expect_download(timeout=15000) as d:
            p.locator('[data-civil-form="exportar"] button[type=submit]').click()
    except Exception:
        raise AssertionError(f"no hubo descarga; avisos: {p.evaluate('window.__avisos')}; estado: {p.locator('[data-civil-form=exportar] .civil-estado').inner_text()}") from None
    lineas = open(d.value.path(), encoding="utf-8").read().splitlines()
    assert lineas[0] == "Punto;Elevacion;Este;Norte" and len(lineas) == 41
    primera = {l.split(";")[0]: l.split(";") for l in lineas[1:]}["1"]
    assert primera[1] == "100,25" and float(primera[2].replace(",", ".")) == pytest.approx(X0 + 10) and d.value.suggested_filename.endswith(".txt")

    # 6. el archivo exportado vuelve a entrar solo (el encabezado dice qué es cada columna)
    p.locator("#civil-hoja-exportar [data-tv-cerrar]").first.click()
    menu(p, "puntos", "importar")
    p.locator("#civil-imp-delim").select_option("")
    p.locator("#civil-imp-dec").select_option("")
    p.locator("#civil-imp-saltar").fill("0")
    p.locator("#civil-imp-archivo").set_input_files(d.value.path())
    leida(p)
    p.wait_for_timeout(500)
    leida(p)
    assert not p.locator('[data-civil-form="importar"] [data-civil-enviar]').is_disabled(), p.locator("#civil-imp-vista").inner_text()
    texto = p.locator("#civil-imp-vista").inner_text()
    assert "40 puntos" in texto and "encabezado" in texto and "punto y coma" in texto and "coma decimal" in texto
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)


# -- KML y KMZ -----------------------------------------------------------------------------------------------------------------------

def geo(x, y):
    lon, lat = Transformer.from_crs(32614, 4326, always_xy=True).transform(X0 + x, Y0 + y)
    return lon, lat


def coords(puntos, alt=0):
    return " ".join(f"{geo(x, y)[0]:.9f},{geo(x, y)[1]:.9f},{alt}" for x, y in puntos)


def kml_de_prueba() -> str:
    manzana = [(0, 0), (80, 0), (80, 60), (0, 60), (0, 0)]
    hueco = [(30, 20), (50, 20), (50, 40), (30, 40), (30, 20)]
    lote = [(100, 0), (140, 0), (140, 30), (100, 30), (100, 0)]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Fraccionamiento</name>
<Style id="rojo"><LineStyle><color>ff0000ff</color></LineStyle></Style>
<Folder><name>Manzanas</name>
  <Placemark><name>Manzana A</name><styleUrl>#rojo</styleUrl><Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(manzana)}</coordinates></LinearRing></outerBoundaryIs>
    <innerBoundaryIs><LinearRing><coordinates>{coords(hueco)}</coordinates></LinearRing></innerBoundaryIs></Polygon></Placemark>
  <Folder><name>Lotes</name><Placemark><name>Lote 1</name><Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(lote)}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark></Folder>
</Folder>
<Folder><name>Calles</name><Placemark><name>Calle Juárez</name><LineString><coordinates>{coords([(-20, 30), (160, 35)])}</coordinates></LineString></Placemark></Folder>
<Folder><name>Bancos</name>
  <Placemark><name>BN-1</name><Point><coordinates>{coords([(10, 10)], 2240.5)}</coordinates></Point></Placemark>
  <Placemark><name>BN-2</name><Point><coordinates>{coords([(120, 10)], 2243.25)}</coordinates></Point></Placemark>
</Folder></Document></kml>"""


def kmz_de_prueba(ruta):
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_de_prueba())


def cerradas(p):
    """Las polilíneas cerradas del plano como listas de vértices (x, y) redondeadas al milímetro."""
    salida = []
    for e in entidades(p, type="LWPOLYLINE"):
        pr = e.get("properties") or {}
        if pr.get("is_closed") and pr["vertices"][0]["location"]["x"] > 1e5:    # sin el triángulo del símbolo de punto
            salida.append(sorted((round(v["location"]["x"], 3), round(v["location"]["y"], 3)) for v in pr["vertices"]))
    return sorted(salida)


@pytest.mark.parametrize("motor", MOTORES)
def test_kml_kmz_importar_exportar_y_volver_a_importar(cad, navegar, crear_usuario, roles, motor, tmp_path):
    ana, plano, n, p = abrir_plano(navegar, crear_usuario, roles, motor, "Fraccionamiento")
    kmz = tmp_path / "frac.kmz"
    kmz_de_prueba(kmz)
    antes = len(entidades(p))

    # 1. importar el KMZ: la vista previa cuenta lo que trae y avisa de los huecos
    menu(p, "plano", "importar_kml")
    p.locator("#civil-kml-archivo").set_input_files(str(kmz))
    esperar(p, "document.querySelector('[data-civil-form=importar_kml] [data-civil-enviar]').disabled === false", LARGO)
    vista = " ".join(p.locator("#civil-kml-vista").inner_text().split())
    assert "2 polígonos" in vista and "1 línea" in vista and "2 puntos" in vista and "KMZ" in vista and "1 hueco" in vista
    for capa in ("Manzanas", "Calles", "Bancos"):
        assert capa in vista, capa
    p.locator('[data-civil-form="importar_kml"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar-kml').open", LARGO * 2)
    esperar_aviso(p, "elementos importados")
    original = cerradas(p)
    assert len(original) == 3                                                  # la manzana, su hueco y el lote
    esperadas = [[(0, 0), (80, 0), (80, 60), (0, 60)], [(30, 20), (50, 20), (50, 40), (30, 40)], [(100, 0), (140, 0), (140, 30), (100, 30)]]
    for c, e in zip(sorted(original), sorted(sorted((X0 + x, Y0 + y) for x, y in v) for v in esperadas)):
        assert len(c) == len(e) and all(abs(a - b) <= 2e-3 for u, v in zip(c, e) for a, b in zip(u, v)), (c, e)   # de geográficas a UTM, al milímetro
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 2
    insercion = {e["attributes"]["DESC"]: e for e in entidades(p, type="INSERT", layer="PUNTOS_TERRENO", fields=["position", "attributes"])}
    assert insercion["BN-1"]["position"] == pytest.approx([X0 + 10, Y0 + 10, 2240.5], abs=2e-3)
    capas = {e["layer"] for e in entidades(p) if e["type"] in ("LwPolyline", "Polyline")}
    assert capas - {"0"} == {"Manzanas", "Manzanas - Lotes", "Calles"}, capas           # una capa por carpeta
    nombres = sorted((e.get("value") or (e.get("properties") or {}).get("value") or "") for e in entidades(p) if e["type"] in ("Text", "MText"))
    assert "Manzana A" in nombres and "Lote 1" in nombres and "Calle Juárez" in nombres, nombres
    # un solo Deshacer devuelve el plano a como estaba
    pedir(p, {"op": "undo"})
    assert len(entidades(p)) == antes
    pedir(p, {"op": "redo"})
    assert cerradas(p) == original

    # 2. exportar todas las cerradas como KML: tres polígonos, con nombre, superficie y colores
    menu(p, "plano", "exportar_kml")
    p.locator('[data-civil-form="exportar_kml"] input[name=origen][value=cerradas]').check(force=True)
    p.locator('[data-civil-form="exportar_kml"] input[name=formato][value=kml]').check(force=True)
    p.locator('[data-civil-form="exportar_kml"] details.civil-mas').evaluate("d => d.open = true")
    p.locator("#civil-kx-ro").fill("40")
    with p.expect_download(timeout=LARGO) as d:
        p.locator('[data-civil-form="exportar_kml"] button[type=submit]').click()
    assert d.value.suggested_filename == "Fraccionamiento.kml"
    raiz = ET.parse(d.value.path()).getroot()
    marcas = {m.find(f"{KML}name").text: m for m in raiz.iter(f"{KML}Placemark")}
    assert {"Manzana A", "Lote 1"} <= set(marcas), set(marcas)
    desc = marcas["Lote 1"].find(f"{KML}description").text
    assert ("1,200.000 m²" in desc or "1,199.999 m²" in desc) and "0-12-00.00 ha" in desc, desc          # ida y vuelta por latitud y longitud: al milímetro
    estilo = raiz.find(f"{KML}Document/{KML}Style[@id='poligono']")
    assert estilo.find(f"{KML}PolyStyle/{KML}color").text.startswith("66")
    esperar(p, "!document.querySelector('[data-civil-form=exportar_kml] [data-civil-resultado]').hidden", LARGO)
    assert "superficie" in p.locator('[data-civil-form="exportar_kml"] [data-civil-resultado]').inner_text().lower()
    p.locator("#civil-hoja-exportar-kml [data-tv-cerrar]").first.click()

    # 3. exportar solo el lote que se elige en el dibujo, con los puntos de terreno, como KMZ
    mapa = enfocar(p, (X0 + 100, Y0, X0 + 140, Y0 + 30))
    menu(p, "plano", "exportar_kml")
    p.locator('[data-civil-form="exportar_kml"] input[name=origen][value=elegir]').check(force=True)
    p.locator('[data-civil-form="exportar_kml"] [data-civil-elegir="lineas"]').click()
    elegir_en_dibujo(p, [(X0 + 120, Y0)], mapa)                                # sobre su lado de abajo
    esperar(p, "document.querySelector('[data-civil-form=exportar_kml] [data-civil-cuenta=lineas]').classList.contains('civil-elegido')", LARGO)
    assert "1 cerrada" in p.locator('[data-civil-form="exportar_kml"] [data-civil-cuenta="lineas"]').inner_text()
    p.locator("#civil-kx-puntos").select_option("terreno")
    p.locator('[data-civil-form="exportar_kml"] input[name=formato][value=kmz]').check(force=True)
    with p.expect_download(timeout=LARGO) as d2:
        p.locator('[data-civil-form="exportar_kml"] button[type=submit]').click()
    assert d2.value.suggested_filename == "Fraccionamiento.kmz"
    with zipfile.ZipFile(d2.value.path()) as z:
        assert z.namelist() == ["doc.kml"]
        raiz2 = ET.fromstring(z.read("doc.kml"))
    nombres_kmz = sorted(m.find(f"{KML}name").text for m in raiz2.iter(f"{KML}Placemark"))
    assert nombres_kmz == ["1", "2", "Lote 1"], nombres_kmz                      # los puntos llevan su número de Civil
    p.locator("#civil-hoja-exportar-kml [data-tv-cerrar]").first.click()

    # 4. ida y vuelta: el KML exportado vuelve a entrar con los mismos vértices (al milímetro)
    menu(p, "plano", "importar_kml")
    p.locator("#civil-kml-archivo").set_input_files(d.value.path())
    esperar(p, "document.getElementById('civil-kml-vista').textContent.replace(/\\s+/g, ' ').includes('3 polígonos')", LARGO)
    p.locator('[data-civil-form="importar_kml"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar-kml').open", LARGO * 2)
    nuevas = cerradas(p)
    assert len(nuevas) == 6
    for k, c in enumerate(original):
        assert nuevas.count(c) == 2, (k, c)
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)


@pytest.mark.parametrize("motor", ["chromium"])
def test_kml_con_zona_distinta_avisa_y_un_archivo_que_no_es_kml_se_dice(cad, navegar, crear_usuario, roles, motor, tmp_path):
    ana, plano, n, p = abrir_plano(navegar, crear_usuario, roles, motor, "Zona")
    malo = tmp_path / "malo.kml"
    malo.write_text("<kml><Document><Placemark/></Document></kml>")
    menu(p, "plano", "importar_kml")
    p.locator("#civil-kml-archivo").set_input_files(str(malo))
    esperar(p, "!!document.querySelector('#civil-kml-vista .civil-aviso-linea--error')", LARGO)
    assert p.locator('[data-civil-form="importar_kml"] [data-civil-enviar]').is_disabled()
    # los mismos datos en la zona 13 del plano: se avisa que caen en otra zona
    lon, lat = Transformer.from_crs(32613, 4326, always_xy=True).transform(X0, Y0)
    lejos = tmp_path / "lejos.kml"
    lejos.write_text(f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>P</name><Point><coordinates>{lon:.8f},{lat:.8f},0</coordinates></Point></Placemark></Document></kml>')
    p.locator("#civil-kml-archivo").set_input_files(str(lejos))
    esperar(p, "document.querySelector('[data-civil-form=importar_kml] [data-civil-enviar]').disabled === false", LARGO)
    assert "zona" in p.locator("#civil-kml-vista").inner_text().lower()


# -- Triangulación de proyecto de punta a punta -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("motor", MOTORES)
def test_triangulacion_de_proyecto_de_punta_a_punta(cad, navegar, crear_usuario, roles, motor, tmp_path):
    """Terreno plano a 100 m y proyecto inclinado de 99.00 a 99.60: puntos, triangulación, curvas, perfil, secciones y volumen."""
    ana, plano, n, p = abrir_plano(navegar, crear_usuario, roles, motor, "Proyecto")
    terreno_txt, proyecto_txt = tmp_path / "terreno.txt", tmp_path / "proyecto.txt"
    terreno_txt.write_bytes(archivo_pnezd(300, lado=120.0, f=lambda x, y: 100.0))
    proyecto_txt.write_bytes(archivo_pnezd(300, semilla=9, lado=120.0, f=lambda x, y: 99.0 + 0.005 * x, prefijo="PR-"))

    for archivo, tipo in ((terreno_txt, "terreno"), (proyecto_txt, "proyecto")):
        menu(p, "puntos", "importar")
        p.locator(f'[data-civil-form="importar"] input[name=tipo][value={tipo}]').check()
        p.locator("#civil-imp-archivo").set_input_files(str(archivo))
        esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
        p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
        esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO * 2)
        menu(p, "terreno", "triangular")
        p.locator(f'[data-civil-form="triangular"] input[name=tipo][value={tipo}]').check()
        p.locator('[data-civil-form="triangular"] button[type=submit]').click()
        esperar(p, "!document.getElementById('civil-hoja-triangular').open", LARGO * 3)
    assert cuenta(p, "INSERT", "PUNTOS_TERRENO") == 300 and cuenta(p, "INSERT", "PUNTOS_PROYECTO") == 300
    caras_t, caras_p = cuenta(p, "3DFACE", "TRIANGULACION"), cuenta(p, "3DFACE", "TRIANGULACION_PROYECTO")
    assert caras_t > 400 and caras_p > 400

    # curvas de nivel del proyecto: la superficie inclinada da sus curvas cada 0.1 m
    menu(p, "curvas", "curvas")
    p.locator('[data-civil-form="curvas"] input[name=tipo][value=proyecto]').check()
    p.locator("#civil-cur-int").select_option("0.1")
    p.locator('[data-civil-form="curvas"] button[type=submit]').click()
    esperar(p, "!document.getElementById('civil-hoja-curvas').open", LARGO * 3)
    esperar_libre(p)
    assert cuenta(p, None, "CURVAS_MAESTRAS_PROYECTO") + cuenta(p, None, "CURVAS_INTERMEDIAS_PROYECTO") >= 4
    assert cuenta(p, None, "CURVAS_MAESTRAS") + cuenta(p, None, "CURVAS_INTERMEDIAS") == 0           # el terreno es plano: no tiene curvas

    # el eje (con las capas de puntos, triángulos y curvas apagadas, para que al elegirlo en el dibujo no se pegue a otra cosa)
    for capa in ("PUNTOS_TERRENO", "PUNTOS_PROYECTO", "PUNTOS_NUMERO", "PUNTOS_ELEVACION", "PUNTOS_DESCRIPCION", "TRIANGULACION", "TRIANGULACION_PROYECTO",
                 "CURVAS_MAESTRAS_PROYECTO", "CURVAS_INTERMEDIAS_PROYECTO"):
        try:
            pedir(p, {"op": "set_properties", "collection": "layers", "name": capa, "updates": [{"path": "/flags/off", "value": True}]})
        except Exception:                                                   # una capa que este plano no tiene
            pass
    pedir(p, {"op": "entities_create", "entities": [{"type": "LwPolyline", "vertices": [[X0 + 10, Y0 + 60], [X0 + 110, Y0 + 60]], "closed": False, "layer": "EJE"}]})
    mapa = enfocar(p, (X0 + 10, Y0 + 55, X0 + 110, Y0 + 65))

    # perfil: la hoja dice qué superficies hay, y con el interruptor lee la de proyecto
    menu(p, "ejes", "perfil")
    esperar(p, f"document.querySelector('[data-civil-form=perfil] [data-civil-sup=proyecto] i').textContent.startsWith('{caras_p:,}')", LARGO)
    assert p.locator('[data-civil-form="perfil"] [data-civil-sup="terreno"]').inner_text().replace("\n", " ").startswith("Terreno")
    assert "sigue el terreno" in p.locator('[data-civil-form="perfil"] [data-civil-sup-uso]').inner_text()
    p.locator('[data-civil-form="perfil"] [data-civil-elegir="eje"]').click()
    elegir_en_dibujo(p, [(X0 + 70, Y0 + 60)], mapa)
    esperar(p, "document.querySelector('[data-civil-form=perfil] [data-civil-cuenta=eje]').classList.contains('civil-elegido')", LARGO)
    p.locator("#civil-per-consup").check()
    assert "superficie de proyecto" in p.locator('[data-civil-form="perfil"] [data-civil-sup-uso]').inner_text()
    p.locator('[data-civil-form="perfil"] button[type=submit]').click()
    poner_aqui(p)
    esperar(p, "!document.getElementById('civil-hoja-perfil').open", LARGO * 3)
    assert cuenta(p, None, "PERFIL_PROYECTO") >= 1 and cuenta(p, None, "PERFIL_TERRENO") >= 1
    textos = [(e.get("value") or (e.get("properties") or {}).get("value") or "") for e in entidades(p, layer="PERFIL_TEXTOS")]
    assert "ELEV. PROYECTO" in textos and "ESPESOR" in textos and "+0.95" in textos and "+0.45" in textos, textos[:12]   # 0+000 está en x = 10 m (0.95) y 0+100 en x = 110 m (0.45)

    # secciones: el interruptor de proyecto apaga la rasante (son una cosa o la otra) y el corte sale de las dos superficies
    pedir(p, {"op": "set_properties", "collection": "layers", "name": "EJE_ESTACIONES", "updates": [{"path": "/flags/off", "value": True}]})
    mapa = enfocar(p, (X0 + 10, Y0 + 55, X0 + 110, Y0 + 65))
    menu(p, "ejes", "secciones")
    p.locator('[data-civil-form="secciones"] [data-civil-elegir="eje"]').click()
    elegir_en_dibujo(p, [(X0 + 70, Y0 + 60)], mapa)
    esperar(p, "document.querySelector('[data-civil-form=secciones] [data-civil-cuenta=eje]').classList.contains('civil-elegido')", LARGO)
    p.locator("#civil-sec-conras").check()
    p.locator("#civil-sec-consup").check()
    assert not p.locator("#civil-sec-conras").is_checked()
    assert "superficie de proyecto" in p.locator('[data-civil-form="secciones"] [data-civil-sup-uso]').inner_text()
    p.locator('[data-civil-form="secciones"] button[type=submit]').click()
    poner_aqui(p)
    esperar(p, "!document.querySelector('[data-civil-form=secciones] [data-civil-resultado]').hidden", LARGO * 3)
    resultado = p.locator('[data-civil-form="secciones"] [data-civil-resultado]').inner_text()
    assert "Corte" in resultado and ("2,799.9" in resultado or "2,800.0" in resultado), resultado                       # 40 m de ancho × (100 m − 0.005 · 6 000 m)
    assert cuenta(p, None, "SECCION_PROYECTO") >= 6
    p.locator("#civil-hoja-secciones [data-tv-cerrar]").first.click()

    # volumen entre superficies: 120 × 120 m, de 1.00 m a 0.40 m de corte (media 0.70 m)
    menu(p, "volumenes", "volumen")
    esperar(p, "!document.querySelector('[data-civil-form=volumen] [data-civil-sup=proyecto]').classList.contains('civil-superficie--vacia')", LARGO)
    p.locator('[data-civil-form="volumen"] button[type=submit]').click()
    esperar(p, "!document.querySelector('[data-civil-form=volumen] [data-civil-resultado]').hidden", LARGO * 3)
    texto = p.locator('[data-civil-form="volumen"] [data-civil-resultado]').inner_text()
    assert "10,080." in texto and "14,400.00 m²" in texto, texto
    p.locator("#civil-hoja-volumen [data-tv-cerrar]").first.click()

    # sin triangulación de proyecto, el interruptor lo dice con claridad
    pedir(p, {"op": "entities_delete", "handles": [e["handle"] for e in entidades(p, type="3DFACE", layer="TRIANGULACION_PROYECTO", fields=["handle"])]})
    menu(p, "ejes", "perfil")
    esperar(p, "document.querySelector('[data-civil-form=perfil] [data-civil-sup=proyecto]').classList.contains('civil-superficie--vacia')", LARGO)
    assert "Sin triangular" in p.locator('[data-civil-form="perfil"] [data-civil-sup="proyecto"]').inner_text()
    p.locator('[data-civil-form="perfil"] button[type=submit]').click()
    esperar_aviso(p, "No hay triangulación de proyecto")
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)
