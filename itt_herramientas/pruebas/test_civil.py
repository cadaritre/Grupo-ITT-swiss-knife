"""Civil (topografía dentro de CAD): motor fijado, órdenes del servidor, permisos y la página."""
from __future__ import annotations

import base64
import gzip
import io
import json
from pathlib import Path

import ezdxf
import numpy as np
import pytest

from itt_herramientas.cad import servicio as cad_servicio
from itt_herramientas.civil import actualizar_motor, servicio
from itt_herramientas.pruebas.conftest import url

pytestmark = pytest.mark.django_db
X0, Y0 = 390000.0, 3168000.0


@pytest.fixture
def dibujante(crear_usuario, roles):
    u = crear_usuario("dana")
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def otra(crear_usuario, roles):
    u = crear_usuario("olga")
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def supervisora(crear_usuario, roles):
    u = crear_usuario("sara")
    roles.conceder(u, "herramientas", "supervision")
    return u


def mandar(cliente, orden, datos, comprimir=False):
    cuerpo = json.dumps(datos).encode()
    extra = {}
    if comprimir:
        cuerpo = gzip.compress(cuerpo)
        extra["HTTP_CONTENT_ENCODING"] = "gzip"
    return cliente.post(url("civil_orden", orden=orden), cuerpo, content_type="application/json", **extra)


def puntos(n=150, semilla=1):
    rng = np.random.default_rng(semilla)
    xy = np.vstack([rng.uniform(0, 100, (n, 2)), [[0, 0], [100, 0], [100, 100], [0, 100]]])
    return [[X0 + x, Y0 + y, 100 + 0.05 * x + 0.02 * y, str(i + 1), "TN"] for i, (x, y) in enumerate(xy)]


def caras_de(dxf_b64):
    doc = ezdxf.read(io.StringIO(base64.b64decode(dxf_b64).decode()))
    return [{"handle": f.dxf.handle, "vertices": [list(f.dxf.vtx0), list(f.dxf.vtx1), list(f.dxf.vtx2)]} for f in doc.modelspace().query("3DFACE")]


def test_el_motor_copiado_coincide_con_su_huella():
    carpeta = Path(actualizar_motor.__file__).parent / "motor"
    d = json.loads((carpeta / "MOTOR.json").read_text())
    assert d["repo"] == "cadaritre/TresVizo-Civil" and len(d["commit"]) == 40 and d["version"]
    assert actualizar_motor.huellas(carpeta) == d["archivos"], "civil/motor/ se cambió a mano: cópialo con actualizar_motor.py"
    assert servicio.motor()["commit"] == d["commit"][:9]


def test_orden_ligera_responde_en_la_misma_peticion(cliente_de, dibujante):
    r = mandar(cliente_de(dibujante), "cuadro", {"poligono": {"vertices": [[X0, Y0], [X0, Y0 + 100], [X0 + 100, Y0 + 100], [X0 + 100, Y0]]}})
    assert r.status_code == 200 and r["Cache-Control"] == "private, no-store"
    d = r.json()
    assert d["terminada"] and d["resultado"]["resumen"]["superficie_ha"] == "1-00-00.00 ha"
    doc = ezdxf.read(io.StringIO(base64.b64decode(d["resultado"]["dxf"]).decode()))
    assert any(t.dxf.text == "CUADRO DE CONSTRUCCIÓN" for t in doc.modelspace().query("TEXT"))


def test_orden_pesada_va_a_la_cola_y_su_resultado_es_de_la_cuenta(cliente_de, dibujante, otra):
    cl = cliente_de(dibujante)
    r = mandar(cl, "triangular", {"puntos": puntos()}, comprimir=True)
    assert r.status_code == 202
    d = r.json()
    assert d["terminada"] and d["estado"] == "completada", d  # en pruebas las tareas corren al momento
    res = cl.get(d["resultado"]["url"])
    assert res.status_code == 200
    tri = res.json()
    assert tri["resumen"]["triangulos"] > 200 and tri["resumen"]["area_planta"] == pytest.approx(10000)
    # el archivo de entrada ya no está; el resultado solo lo ve quien lo pidió
    assert not list(cad_servicio.carpeta_temporal().glob("civil-*.json"))
    assert cliente_de(otra).get(d["resultado"]["url"]).status_code == 404
    # y con esas caras: curvas de nivel
    c = mandar(cl, "curvas", {"caras": caras_de(tri["dxf"]), "intervalo": 1}, comprimir=True).json()
    curvas = cl.get(c["resultado"]["url"]).json()
    assert curvas["resumen"]["curvas"] >= 5


def test_cuadro_de_curvas_y_renumerar_en_la_misma_peticion(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    b90 = float(np.tan(np.radians(22.5)))
    r = mandar(cl, "cuadro_curvas", {"polilineas": [{"vertices": [[X0, Y0], [X0 + 100, Y0 + 100]], "combas": [b90, 0]}]})
    assert r.status_code == 200 and r.json()["resultado"]["resumen"]["curvas"] == 1
    pts = [{"x": X0 + i, "y": Y0, "z": 100, "tipo": "terreno", "atributos": {"NUM": f"P{9 - i}", "ELEV": "100.000", "DESC": "TN"}} for i in range(3)]
    r = mandar(cl, "redibujar_puntos", {"puntos": pts, "renumerar": {"inicio": 1, "incremento": 1, "orden": "oeste_este"}, "escala": {"denominador": 500}})
    doc = ezdxf.read(io.StringIO(base64.b64decode(r.json()["resultado"]["dxf"]).decode()))
    assert [(i.get_attrib_text("NUM"), i.dxf.xscale) for i in doc.modelspace().query("INSERT")] == [("1", 0.5), ("2", 0.5), ("3", 0.5)]


def test_revisar_triangulacion_va_a_la_cola(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    sana = [{"handle": "1A", "vertices": [[X0, Y0, 100], [X0 + 10, Y0, 100], [X0 + 10, Y0 + 10, 100]]},
            {"handle": "1B", "vertices": [[X0, Y0, 100], [X0 + 10, Y0 + 10, 100], [X0, Y0 + 10, 100]]}]
    d = mandar(cl, "revisar_tin", {"caras": sana + [{"handle": "1C", "vertices": sana[0]["vertices"]}]}).json()
    assert d["terminada"] and d["estado"] == "completada", d
    r = cl.get(d["resultado"]["url"]).json()
    assert r["resumen"]["repetidas"] == 1 and r["handles"] == ["1C"] and r["graves"] == ["1C"]


CUADRADO = [[X0, Y0], [X0, Y0 + 100], [X0 + 100, Y0 + 100], [X0 + 100, Y0]]


def test_memoria_descriptiva_en_pdf_y_docx(cliente_de, dibujante):
    import zipfile

    from pypdf import PdfReader

    cl = cliente_de(dibujante)
    base = {"poligono": {"vertices": CUADRADO}, "opciones": {"formato": "ran", "zona": 14}, "colindancias": ["Calle Juárez"],
            "datos": [["Predio", "El Mezquite"], ["Propietario", ""]]}
    r = mandar(cl, "memoria", {**base, "formato": "pdf"}).json()["resultado"]
    assert r["extension"] == "pdf" and r["tipo"] == "application/pdf" and "documento" not in r
    texto = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(base64.b64decode(r["archivo"]))).pages)
    assert "MEMORIA DESCRIPTIVA" in texto and "El Mezquite" in texto and "Calle Juárez" in texto and "LATITUD" in texto
    r = mandar(cl, "memoria", {**base, "formato": "docx"}).json()["resultado"]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(r["archivo"]))) as z:
        xml = z.read("word/document.xml").decode()
        assert "[Content_Types].xml" in z.namelist()
    assert "Del vértice 1 al vértice 2" in xml and "colindando con Calle Juárez" in xml and "El Mezquite" in xml
    # sin formato de archivo, el contenido (para que la página arme las colindancias lado por lado)
    d = mandar(cl, "memoria", {"poligono": {"vertices": CUADRADO}}).json()["resultado"]
    assert [f[:2] for f in d["documento"]["filas"][1:]] == [["1", "2"], ["2", "3"], ["3", "4"], ["4", "1"]]
    assert mandar(cl, "memoria", {**base, "formato": "odt"}).status_code == 422


def test_reporte_de_puntos_y_cuadricula_y_anotar(cliente_de, dibujante):
    from pypdf import PdfReader

    cl = cliente_de(dibujante)
    r = mandar(cl, "reporte_puntos", {"puntos": puntos(30), "zona": 14, "formato": "pdf", "titulo": "LEVANTAMIENTO"}).json()["resultado"]
    texto = PdfReader(io.BytesIO(base64.b64decode(r["archivo"]))).pages[0].extract_text()
    assert "LEVANTAMIENTO" in texto and "LONGITUD" in texto and r["resumen"]["puntos"] == 34
    r = mandar(cl, "cuadricula", {"caja": [X0, Y0, X0 + 300, Y0 + 200], "intervalo": 100, "zona": 14}).json()["resultado"]
    capas = {e.dxf.layer for e in ezdxf.read(io.StringIO(base64.b64decode(r["dxf"]).decode())).modelspace()}
    assert capas == {"RETICULA_UTM", "RETICULA_MARCO", "RETICULA_TEXTOS"}
    r = mandar(cl, "anotar_lados", {"polilineas": [{"vertices": CUADRADO, "cerrada": True}]}).json()["resultado"]
    assert r["resumen"]["lados"] == 4


def test_insertar_puntos_va_a_la_cola(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    caras = [{"handle": "F1", "vertices": [[X0, Y0, 1], [X0 + 10, Y0, 1], [X0 + 10, Y0 + 10, 1]]},
             {"handle": "F2", "vertices": [[X0, Y0, 1], [X0 + 10, Y0 + 10, 1], [X0, Y0 + 10, 1]]}]
    d = mandar(cl, "insertar_puntos", {"caras": caras, "puntos": [[X0 + 3, Y0 + 1, 5, "1", ""]]}).json()
    assert d["estado"] == "completada", d
    r = cl.get(d["resultado"]["url"]).json()
    assert "F1" in r["borrar"] and r["resumen"]["insertados"] == 1
    assert mandar(cl, "insertar_puntos", {"caras": caras, "puntos": []}).status_code == 422


def test_curva_masa_por_la_cola_y_lotificar(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    xs, ys = np.meshgrid(np.arange(0, 201, 20.0), np.arange(-40, 41, 20.0))
    pts = [[X0 + x, Y0 + y, 100 + 0.05 * x, str(i), ""] for i, (x, y) in enumerate(zip(xs.ravel(), ys.ravel()))]
    tri = cl.get(mandar(cl, "triangular", {"puntos": pts}).json()["resultado"]["url"]).json()
    d = mandar(cl, "curva_masa", {"caras": caras_de(tri["dxf"]), "eje": {"vertices": [[X0, Y0], [X0 + 200, Y0]]}, "intervalo": 20,
                                  "rasante": [[0, 105], [200, 105]], "plantilla": {"tipo": "carretera"}}, comprimir=True).json()
    assert d["estado"] == "completada", d
    r = cl.get(d["resultado"]["url"]).json()
    assert r["resumen"]["estaciones"] == 11 and r["resumen"]["despalme"] > 0 and r["lazos"] is not None
    import math

    b = math.tan(math.radians(90) / 4)
    d = mandar(cl, "secciones", {"caras": caras_de(tri["dxf"]), "eje": {"vertices": [[X0, Y0 - 20], [X0 + 100, Y0 - 20], [X0 + 200, Y0 + 80]], "combas": [0, 0.2, 0]},
                                 "intervalo": 20, "izquierda": 20, "derecha": 20, "rasante": [[0, 105], [300, 105]],
                                 "plantilla": {"tipo": "carretera", "curvas": {"velocidad": 60, "vehiculo": "C3R3"}}}, comprimir=True).json()
    r = cl.get(d["resultado"]["url"]).json()
    c, = r["curvas"]
    assert c["lado"] == "izquierda" and 0.03 < c["sobreelevacion"] <= 0.10 and c["ampliacion"] >= 0.5
    r = mandar(cl, "eje_espirales", {"pi": [[X0, Y0], [X0 + 500, Y0], [X0 + 900, Y0 + 400]], "curvas": [{"radio": 300, "espiral": "auto"}], "velocidad": 60}).json()
    e, = r["resultado"]["elementos"]
    assert e["le"] > 0 and e["te"] < e["ec"] < e["ce"] < e["et"] and r["resultado"]["definicion"]["curvas"][0][0] == 300
    r = mandar(cl, "lotificar", {"poligono": {"vertices": CUADRADO}, "lado_frente": 0, "metodo": "por_cantidad", "valor": 5}).json()["resultado"]
    assert r["resumen"]["lotes"] == 5 and r["resumen"]["superficie"] == pytest.approx(10000)


def test_errores_de_la_persona_salen_con_su_mensaje(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    r = mandar(cl, "curvas", {"caras": []})
    assert r.status_code == 422 and "triangula" in r.json()["error"]
    r = mandar(cl, "cuadro", {"poligono": {"vertices": [[0, 0], [1, 1]]}})
    assert r.status_code == 422 and "polígono" in r.json()["error"].lower()
    r = cl.post(url("civil_orden", orden="cuadro"), b"{no es json", content_type="application/json")
    assert r.status_code == 422 and "dañados" in r.json()["error"]
    assert cl.post(url("civil_orden", orden="borrar_todo"), b"{}", content_type="application/json").status_code == 404


def test_solo_quien_edita_planos(cliente_de, supervisora):
    r = mandar(cliente_de(supervisora), "cuadro", {"poligono": {"vertices": [[0, 0], [0, 1], [1, 0]]}})
    assert r.status_code in (302, 403)


def test_tope_de_tamano(cliente_de, dibujante, monkeypatch):
    monkeypatch.setattr(servicio, "MAX_PUNTOS", 10)
    r = mandar(cliente_de(dibujante), "triangular", {"puntos": puntos(20)})
    assert r.status_code == 422 and "puntos" in r.json()["error"]


def test_la_pagina_del_plano_trae_civil_solo_para_editar(cliente_de, dibujante, supervisora, editor_civil):
    p = cad_servicio.crear_vacio(dibujante, "Predio", "", "dxf")
    html = cliente_de(dibujante).get(url("cad_plano", pk=p.pk)).content.decode()
    assert 'id="civil-barra"' in html and "herramientas/cad/civil.js" in html and "Cuadro de construcción" in html
    assert "Invertir triángulos" in html and "Importar puntos" in html
    for nuevo in ("Renumerar puntos", "Revisar triangulación", "Cuadro de curvas", "Los que elija", "Localizar punto", "Reporte de puntos",
                  "Insertar puntos", "Rumbo y distancia de lados", "Numerar vértices", "Memoria descriptiva", "Cuadrícula UTM", "UTM-GPS", "Zona UTM",
                  "Dibujar punto", "Editar punto", "Etiquetas de puntos", "Convertir puntos", "Etiquetar curvas", "Suavizar curvas",
                  "Curva masa", "Lotificar manzana", "Carretera", "Despalme", "Sobreelevación y ampliación en las curvas (SCT)", "C3R3", "T3S2R4", "T3S3", "Eje con espirales",
                  "Seleccionar puntos en el plano", "Dentro de un polígono", "Unir curvas cortadas", "Unir y etiquetar", "Poner aquí"):
        assert nuevo in html, nuevo
    # la curva nunca se corta bajo su etiqueta; y lo que agrega un bloque siempre pregunta dónde va (sin «Debajo del plano» automático)
    for quitado in ("Cortar la curva bajo la etiqueta", "Debajo del plano", "A un lado del plano", "Junto al polígono"):
        assert quitado not in html, quitado
    cfg = json.loads(html.split('id="civil-config" type="application/json">')[1].split("</script>")[0])
    assert cfg["capas"]["tin"]["terreno"] == "TRIANGULACION" and cfg["bloques"]["terreno"] == "PT_TERRENO"
    assert "__ORDEN__" in cfg["urls"]["orden"]
    html = cliente_de(supervisora).get(url("cad_plano", pk=p.pk)).content.decode()
    assert "civil-barra" not in html


@pytest.fixture
def editor_civil(tmp_path, settings):
    from itt_herramientas.cad import editor

    base = tmp_path / "editor" / editor.version()
    base.mkdir(parents=True)
    (base / "index.html").write_text("<!doctype html><title>x</title>")
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(tmp_path / "editor")
    return base
