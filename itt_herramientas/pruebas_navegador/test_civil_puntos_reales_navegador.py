"""Los puntos de Civil como entidades POINT con rótulos TEXT, con el editor real (Chromium y WebKit): importar, editar (el punto y sus rótulos
cambian juntos, en un solo paso de deshacer), etiquetas, mover solo el punto, explotar, planos con puntos de bloque (se leen, se ofrece
convertirlos y la conversión es un solo paso de deshacer) y la vuelta por DWG con LibreDWG.

Corre bajo el candado pesado de la máquina, un navegador a la vez:
    TV_CAD_EDITOR_PRUEBAS=~/.cache/tresvizo-cad/editor pytest itt_herramientas/pruebas_navegador/test_civil_puntos_reales_navegador.py -k chromium
"""
from __future__ import annotations

import base64
import shutil
import subprocess

import ezdxf
import pytest

from itt_herramientas.cad import servicio
from itt_herramientas.civil.motor.dibujos import BLOQUES, bloque_punto
from itt_herramientas.civil.motor.dxf import a_bytes
from itt_herramientas.civil.motor.entidades import Dibujo, EstiloTexto, Insercion
from itt_herramientas.models import VersionPlano

from . import cinta
from .test_cad_navegador import LARGO, _persona, cad, esperar_estado, navegar  # noqa: F401 (fixtures)
from .test_civil_navegador import X0, Y0, MOTORES, archivo_pnezd, cuenta, elegir_en_dibujo, enfocar, esperar_aviso, esperar_libre, menu, pedir, puntos_civil  # noqa: F401
from .test_civil_puntos_kml_navegador import abrir_plano
from .test_pwa_navegador import esperar

pytestmark = [pytest.mark.navegador, pytest.mark.django_db(transaction=True)]


def en_modelo(p, tipo=None, capa=None):
    """Las entidades del espacio modelo (sin las de dentro de las definiciones de bloque)."""
    q = {"op": "query", "detail": "summary", "limit": 10000}
    if tipo:
        q["type"] = tipo
    if capa:
        q["layer"] = capa
    return [e for e in pedir(p, q).get("entities", []) if e.get("space") == "*Model_Space"]


def dxf_con_bloques(escala=0.5):
    d = Dibujo()
    d.estilo(EstiloTexto("CIVIL", "romans.shx", 0.85))
    for t, nombre in BLOQUES.items():
        d.bloques[nombre] = bloque_punto(t)
    legados = [("terreno", X0 + 10, Y0 + 10, 100.5, {"NUM": "A1", "ELEV": "100.500", "DESC": "ESQ"}),
               ("terreno", X0 + 30, Y0 + 12, 101.25, {"NUM": "A2", "ELEV": "", "DESC": "TN"}),
               ("proyecto", X0 + 50, Y0 + 14, 99.0, {"NUM": "A3", "ELEV": "99.0", "DESC": ""})]
    for tipo, x, y, z, a in legados:
        d.agregar(Insercion("PUNTOS_TERRENO" if tipo == "terreno" else "PUNTOS_PROYECTO", bloque=BLOQUES[tipo], posicion=(x, y, z), escala=escala, atributos=a,
                            datos={"TVCIVIL": [(1000, "punto"), (1000, a["NUM"]), (1000, tipo)]}))
    return base64.b64encode(a_bytes(d)).decode()


@pytest.mark.parametrize("motor", MOTORES)
def test_puntos_entidad_editar_etiquetas_mover_explotar_y_dwg(cad, navegar, crear_usuario, roles, motor, tmp_path):
    ana, plano, n, p = abrir_plano(navegar, crear_usuario, roles, motor, "Predio puntos reales")
    archivo = tmp_path / "puntos.txt"
    archivo.write_bytes(archivo_pnezd(40, lado=60.0))
    menu(p, "puntos", "importar")
    p.locator("#civil-imp-archivo").set_input_files(str(archivo))
    esperar(p, "document.querySelector('[data-civil-form=importar] [data-civil-enviar]').disabled === false", LARGO)
    p.locator('[data-civil-form="importar"] [data-civil-enviar]').click()
    esperar(p, "!document.getElementById('civil-hoja-importar').open", LARGO * 2)

    # 1. entidades POINT con Z, TEXT en su capa y el marcador del dibujo; cada rótulo apunta a su punto; ninguna oferta (no hay bloques)
    assert cuenta(p, "Point", "PUNTOS_TERRENO") == 40 and cuenta(p, "INSERT") == 0
    assert [cuenta(p, "Text", c) for c in ("PUNTOS_NUMERO", "PUNTOS_ELEVACION", "PUNTOS_DESCRIPCION")] == [40, 40, 40]
    vals = pedir(p, {"op": "sysvar", "get": ["pdmode", "pdsize"]})["result"]["values"]
    assert vals["pdmode"] == 34 and vals["pdsize"] == pytest.approx(1.2)
    pts = puntos_civil(p, "terreno")
    assert all(set(q["rotulos"]) == {"num", "elev", "desc"} and q["z"] > 0 and not q["legado"] for q in pts)
    xd = pedir(p, {"op": "xdata_get", "app": "TVCIVIL", "handles": [pts[0]["rotulos"]["num"]["handle"]]})["items"][0]["xdata"]["TVCIVIL"]
    assert xd[:4] == ["rotulo", pts[0]["numero"], "terreno", "num"] and xd[4].upper() == pts[0]["handle"].upper()    # el enlace es el handle del punto
    assert p.evaluate("document.getElementById('civil-oferta').hidden")
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); }; }")

    # 2. mover solo el punto deja sus rótulos (como en CivilCAD); explotar no daña nada
    q = pts[3]
    pedir(p, {"op": "entities_transform", "handles": [q["handle"]], "action": "move", "vector": [2, 0]})
    despues = {e["handle"]: e for e in puntos_civil(p, "terreno")}[q["handle"]]
    assert despues["x"] == pytest.approx(q["x"] + 2) and despues["rotulos"]["num"]["x"] == pytest.approx(q["rotulos"]["num"]["x"])
    pedir(p, {"op": "undo"})
    todos = [e["handle"] for e in en_modelo(p)]
    pedir(p, {"op": "select", "clear": True, "handles": todos})
    pedir(p, {"op": "run", "cmd": "EXPLODE"})
    if pedir(p, {"op": "state"}).get("command"):
        pedir(p, {"op": "cancel"})
    assert cuenta(p, "Point", "PUNTOS_TERRENO") == 40 and cuenta(p, "Text", "PUNTOS_DESCRIPCION") == 40 and len(puntos_civil(p, "terreno")[0]["rotulos"]) == 3

    # 3. editar el punto con la hoja: posición, elevación y descripción; el punto y sus rótulos cambian juntos, en un solo paso de deshacer
    objetivo = puntos_civil(p, "terreno")[5]
    num = objetivo["numero"]
    for capa in ("PUNTOS_NUMERO", "PUNTOS_ELEVACION", "PUNTOS_DESCRIPCION"):
        pedir(p, {"op": "set_properties", "collection": "layers", "name": capa, "updates": [{"path": "/flags/off", "value": True}]})
    mapa = enfocar(p, (objetivo["x"] - 5, objetivo["y"] - 5, objetivo["x"] + 5, objetivo["y"] + 5))
    menu(p, "puntos", "punto_editar")
    p.locator('[data-civil-form="punto_editar"] [data-civil-elegir="punto"]').click()
    elegir_en_dibujo(p, [(objetivo["x"], objetivo["y"])], mapa)
    esperar(p, f"document.getElementById('civil-pe-num').value === {num!r}", LARGO)
    p.locator("#civil-pe-desc").fill("EDITADO")
    p.locator("#civil-pe-x").fill(f"{X0 + 70:.4f}")
    p.locator("#civil-pe-y").fill(f"{Y0 + 70:.4f}")
    p.locator('[data-civil-form="punto_editar"] button[type=submit]').click()
    esperar_aviso(p, f"Punto {num} actualizado")
    nuevo = next(e for e in puntos_civil(p, "terreno") if e["numero"] == num)
    assert (round(nuevo["x"], 3), round(nuevo["y"], 3)) == (X0 + 70, Y0 + 70) and nuevo["atributos"]["DESC"] == "EDITADO"
    assert all(abs(r["x"] - nuevo["x"]) < 5 and abs(r["y"] - nuevo["y"]) < 5 for r in nuevo["rotulos"].values()) and len(nuevo["rotulos"]) == 3
    assert cuenta(p, "Point", "PUNTOS_TERRENO") == 40 and cuenta(p, "Text", "PUNTOS_NUMERO") == 40      # nada quedó de más
    pedir(p, {"op": "undo"})                                                                             # UN deshacer devuelve el punto y sus rótulos
    antes = next(e for e in puntos_civil(p, "terreno") if e["numero"] == num)
    assert (round(antes["x"], 3), antes["atributos"]["DESC"]) == (round(objetivo["x"], 3), objetivo["atributos"]["DESC"]) and len(antes["rotulos"]) == 3
    assert cuenta(p, "Point", "PUNTOS_TERRENO") == 40 and cuenta(p, "Text", "PUNTOS_DESCRIPCION") == 40

    # 4. etiquetas: ocultar la descripción apaga su capa; los decimales reescriben la elevación
    menu(p, "puntos", "etiquetas_puntos")
    p.locator('[data-civil-form="etiquetas_puntos"] input[name=ver_descripcion]').uncheck()
    p.locator("#civil-etp-dz").select_option("1")
    p.locator('[data-civil-form="etiquetas_puntos"] button[type=submit]').click()
    esperar_aviso(p, "Etiquetas aplicadas")
    capas = {c["name"]: c for c in pedir(p, {"op": "layers"})["layers"]}
    assert capas["PUNTOS_DESCRIPCION"]["off"] is True
    assert all(len(e["atributos"]["ELEV"].split(".")[1]) == 1 for e in puntos_civil(p, "terreno")) and cuenta(p, "Text", "PUNTOS_ELEVACION") == 40

    # 5. guardar en DWG: LibreDWG y ezdxf ven POINT con su Z y TEXT, y el marcador del dibujo
    esperar_libre(p)
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado", LARGO * 3)
    v = VersionPlano.objects.filter(plano=plano).order_by("-numero").first()
    assert v.formato == "dwg"
    if shutil.which("dwg2dxf"):
        dxf = tmp_path / "vuelta.dxf"
        subprocess.run(["dwg2dxf", "-y", "-o", str(dxf), str(servicio.ruta_version(v))], check=True, capture_output=True, timeout=300)
        doc = ezdxf.readfile(dxf)
        msp = doc.modelspace()
        assert len(msp.query("POINT")) == 40 and len(msp.query("TEXT")) == 120 and not msp.query("INSERT")
        assert doc.header["$PDMODE"] == 34 and doc.header["$PDSIZE"] == pytest.approx(1.2)
        mapa_z = {e.get_xdata("TVCIVIL")[1][1]: e.dxf.location[2] for e in msp.query("POINT")}
        assert mapa_z[num] == pytest.approx(objetivo["z"], abs=1e-6)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    assert cuenta(p, "Point", "PUNTOS_TERRENO") == 40 and pedir(p, {"op": "sysvar", "get": ["pdmode"]})["result"]["values"]["pdmode"] == 34
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)


@pytest.mark.parametrize("motor", MOTORES)
def test_planos_con_puntos_de_bloque_se_leen_se_ofrece_convertirlos_en_un_paso(cad, navegar, crear_usuario, roles, motor, tmp_path):
    ana, plano, n, p = abrir_plano(navegar, crear_usuario, roles, motor, "Predio con bloques")
    p.evaluate("([b]) => TVCivil.insertarDxf(b, 'viejo.dxf')", [dxf_con_bloques(0.5)])
    assert len(en_modelo(p, "INSERT")) == 3 and len(en_modelo(p, "Point")) == 0
    # se leen: las acciones de Civil ven los tres, de los dos tipos
    leidos = {e["numero"]: e for e in puntos_civil(p)}
    assert set(leidos) == {"A1", "A2", "A3"} and all(e["legado"] for e in leidos.values()) and leidos["A1"]["atributos"]["ELEV"] == "100.500"
    # la oferta: un renglón que no bloquea (al volver a cargar la página del plano)
    esperar_libre(p)
    p.locator("#cad-guardar").click()
    esperar_estado(p, "guardado", LARGO * 3)
    p = n.ir(f"/cad/{plano.pk}/")
    esperar_estado(p, "guardado")
    esperar(p, "window.TVCad && TVCad.estaListo()", LARGO)
    cinta.esperar_civil(p)
    p.evaluate("() => { window.__avisos = []; const a = window.tvAviso; window.tvAviso = (t, k) => { window.__avisos.push([t, k || '']); return a(t, k); }; }")
    esperar(p, "!document.getElementById('civil-oferta').hidden", LARGO)
    assert "versión anterior" in p.locator("#civil-oferta-texto").inner_text()
    # «Ahora no» la quita y no vuelve en ese plano
    p.locator("#civil-oferta-cerrar").click()
    assert p.evaluate("document.getElementById('civil-oferta').hidden")
    p.evaluate("() => localStorage.removeItem(`tv-civil-${JSON.parse(document.getElementById('civil-config').textContent).plano}-oferta`)")
    # convertir con el botón de la cinta, un solo paso de deshacer
    menu(p, "puntos", "puntos_a_entidad")
    esperar_aviso(p, "3 puntos pasaron a entidad punto")
    esperar_libre(p)
    assert len(en_modelo(p, "INSERT")) == 0 and len(en_modelo(p, "Point")) == 3 and len(en_modelo(p, "Point", "PUNTOS_PROYECTO")) == 1
    nuevos = {e["numero"]: e for e in puntos_civil(p)}
    assert nuevos["A1"]["atributos"] == {"NUM": "A1", "ELEV": "100.500", "DESC": "ESQ"} and nuevos["A2"]["atributos"] == {"NUM": "A2", "ELEV": "", "DESC": "TN"}
    assert nuevos["A3"]["atributos"] == {"NUM": "A3", "ELEV": "99.0", "DESC": ""} and nuevos["A3"]["tipo"] == "proyecto" and nuevos["A3"]["z"] == 99.0
    assert not any(e["legado"] for e in nuevos.values()) and pedir(p, {"op": "sysvar", "get": ["pdsize"]})["result"]["values"]["pdsize"] == pytest.approx(0.6)
    # las definiciones de bloque ya no existen
    for nombre in BLOQUES.values():
        with pytest.raises(Exception, match="block_missing|does not exist|not found|missing"):
            pedir(p, {"op": "block_delete", "name": nombre})
    # un solo deshacer devuelve los tres bloques (y ningún punto nuevo)
    pedir(p, {"op": "undo"})
    assert len(en_modelo(p, "INSERT")) == 3 and len(en_modelo(p, "Point")) == 0 and len(en_modelo(p, "Text")) == 0
    pedir(p, {"op": "redo"})
    assert len(en_modelo(p, "Point")) == 3 and len(en_modelo(p, "INSERT")) == 0
    esperar(p, "document.getElementById('civil-oferta').hidden", LARGO)
    assert not n.dialogos and not n.errores, (n.dialogos, n.errores)
