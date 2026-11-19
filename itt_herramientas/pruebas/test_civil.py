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


def test_hojas_de_perfil_y_secciones_con_estaciones_libres(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    xs, ys = np.meshgrid(np.arange(0, 201, 20.0), np.arange(-40, 41, 20.0))
    pts = [[X0 + x, Y0 + y, 100 + 0.05 * x, str(i), ""] for i, (x, y) in enumerate(zip(xs.ravel(), ys.ravel()))]
    tri = cl.get(mandar(cl, "triangular", {"puntos": pts}).json()["resultado"]["url"]).json()
    eje = {"vertices": [[X0, Y0], [X0 + 200, Y0]]}
    base = {"caras": caras_de(tri["dxf"]), "eje": eje, "rasante": [[0, 105], [200, 105]], "bloque": "1F3", "intervalo": 25, "hasta": 100,
            "adicionales": ["0+033.30"], "adicionales_puntos": [[X0 + 60, Y0 + 7]]}
    # cuántas estaciones salen: lo dice la hoja antes de dibujar
    d = mandar(cl, "estaciones", {**base, "columnas": 2, "renglones": 2}).json()
    assert d["estado"] == "completada", d
    res = d["resultado"]["resumen"]
    assert res["estaciones"] == 7 and res["hojas"] == 2               # 0, 25, 33.3, 50, 60, 75, 100
    d = mandar(cl, "secciones", {**base, "izquierda": 15, "derecha": 15, "columnas": 2, "renglones": 2, "escala_h": 200, "escala_v": 100, "paso_h": 5, "paso_v": 1,
                                 "orden": "renglones"}, comprimir=True).json()
    assert d["estado"] == "completada", d
    r = cl.get(d["resultado"]["url"]).json()
    assert r["resumen"]["secciones"] == 7 and r["resumen"]["hojas"] == 2 and r["resumen"]["escala_v"] == 100
    assert r["csv"].startswith("\ufeff") and "TOTALES" in r["csv"]
    d = mandar(cl, "perfil", {**base, "escala_h": 1000, "escala_v": 100, "guitarra": ["terreno", "rasante"], "paso_cota": 1}, comprimir=True).json()
    assert d["estado"] == "completada", d
    r = cl.get(d["resultado"]["url"]).json()
    assert r["resumen"]["guitarra"] == 7 and r["resumen"]["escala_v"] == 100


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


@pytest.mark.parametrize("tactil", [False, True], ids=["tactil_apagado", "tactil_encendido"])
def test_la_pagina_del_plano_trae_civil_solo_para_editar(cliente_de, dibujante, supervisora, editor_civil, settings, tactil):
    settings.TV_CAD_TACTIL = tactil   # Civil está igual con el modo táctil encendido (producción) o apagado
    p = cad_servicio.crear_vacio(dibujante, "Predio", "", "dxf")
    pagina = cliente_de(dibujante).get(url("cad_plano", pk=p.pk)).content.decode()
    # La barra y las hojas llegan aparte (iguales para todos los planos, con su huella: docs/cad.md, «Datos móviles»); la página trae su lugar,
    # la configuración de este plano y el script que las pone antes de civil.js.
    assert 'id="civil-panel"' in pagina and "herramientas/cad/civil-panel.js" in pagina and "herramientas/cad/civil.js" in pagina
    panel = pagina.split('id="civil-panel" hidden data-url="')[1].split('"')[0].replace("&amp;", "&")
    html = cliente_de(dibujante).get(panel).content.decode()
    assert 'id="civil-barra"' in html and "Cuadro de construcción" in html
    assert "Invertir triángulos" in html and "Importar puntos" in html
    for nuevo in ("Renumerar puntos", "Revisar triangulación", "Cuadro de curvas", "Los que elija", "Localizar punto", "Reporte de puntos",
                  "Insertar puntos", "Rumbo y distancia de lados", "Numerar vértices", "Memoria descriptiva", "Cuadrícula UTM", "UTM-GPS", "Zona UTM",
                  "Dibujar punto", "Editar punto", "Etiquetas de puntos", "Convertir puntos", "Etiquetar curvas", "Suavizar curvas",
                  "Curva masa", "Lotificar manzana", "Carretera", "Despalme", "Sobreelevación y ampliación en las curvas (SCT)", "C3R3", "T3S2R4", "T3S3", "Eje con espirales",
                  "Seleccionar puntos en el plano", "Dentro de un polígono", "Unir curvas cortadas", "Unir y etiquetar", "Poner aquí",
                  "Importar KML o KMZ", "Exportar KML o KMZ", "Formato guardado", "Guardar formato", "Cómo está escrito el archivo",
                  "Con la superficie de proyecto (triangulación de proyecto)", "Delimitador", "Tratar delimitadores seguidos como uno"):
        assert nuevo in html, nuevo
    # la curva nunca se corta bajo su etiqueta; y lo que agrega un bloque siempre pregunta dónde va (sin «Debajo del plano» automático)
    for quitado in ("Cortar la curva bajo la etiqueta", "Debajo del plano", "A un lado del plano", "Junto al polígono"):
        assert quitado not in html, quitado
    cfg = json.loads(pagina.split('id="civil-config" type="application/json">')[1].split("</script>")[0])
    assert cfg["capas"]["tin"]["terreno"] == "TRIANGULACION" and cfg["bloques"]["terreno"] == "PT_TERRENO"
    assert cfg["urls"]["formatos"].endswith("/cad/civil/formatos/") and ["LAT", "Latitud"] in cfg["columnas"] and cfg["ordenes"]["PNEZD"]
    assert "__ORDEN__" in cfg["urls"]["orden"]
    html = cliente_de(supervisora).get(url("cad_plano", pk=p.pk)).content.decode()
    assert "civil-barra" not in html and "civil-panel" not in html


def test_la_pestana_civil_de_la_cinta_sale_de_los_menus(cliente_de, dibujante, editor_civil):
    """La pestaña «Civil» del editor se arma de `MENUS` (civil/pagina.py, `cinta()`): cada acción con un comando válido y único, su icono
    SVG y su hoja en civil.js; llega en el panel de Civil y civil-cinta.js carga antes que civil.js."""
    import re

    from itt_herramientas.civil import pagina as civil_pagina

    c = civil_pagina.cinta()
    assert c["prefijo"] == "CV"
    assert [g["clave"] for g in c["grupos"]] == [m["clave"] for m in civil_pagina.MENUS]
    acciones = [c["escala"]] + [a for g in c["grupos"] for a in g["acciones"]]
    por_comando = {}
    for a in acciones:
        assert re.fullmatch(r"CV[A-Z0-9_]{1,30}", a["comando"]), a["comando"]
        assert a["icono"].startswith("<svg") and 'stroke="currentColor"' in a["icono"], a["clave"]
        assert a["nombre"] and not a["nombre"].endswith("…"), a["nombre"]
        por_comando.setdefault(a["comando"], set()).add(a["clave"])
    assert all(len(v) == 1 for v in por_comando.values()), {k: v for k, v in por_comando.items() if len(v) > 1}
    for comando in ("CVTRIANGULAR", "CVCURVAS", "CVSECCIONES", "CVIMPORTARPUNTOS", "CVCUADRO", "CVPERFIL", "CVVOLUMEN", "CVIMAGEN", "CVREFERENCIAS",
                    "CVSATELITE", "CVIMPORTARKML", "CVEXPORTARKML"):
        assert comando in por_comando, comando
    grandes = {a["clave"] for a in acciones if a["grande"]}
    assert {"importar", "triangular", "curvas", "cuadro", "perfil", "secciones", "volumen", "imagen", "satelite"} <= grandes, grandes
    assert {a["clave"] for a in acciones if a["solo_lee"]} >= {"exportar", "reporte", "localizar", "memoria", "referencias", "escala", "exportar_kml"}
    # por omisión, «CV» + la clave sin guiones bajos: lo que se agregue a MENUS tiene su comando sin tocar nada más
    assert civil_pagina.comando({"clave": "imagen_satelital"}) == "CVIMAGENSATELITAL"
    # cada acción tiene su hoja o su orden en civil.js, o la registra otro módulo de la página (TVCivil.registrarAccion)
    cad = Path(__file__).resolve().parents[1] / "static" / "herramientas" / "cad"
    js = (cad / "civil.js").read_text(encoding="utf-8")
    bloque = js.split("const ACCIONES = {")[1].split("\n  };")[0]
    registradas = {m for f in cad.rglob("*.js") for m in re.findall(r'registrarAccion\("(\w+)"', f.read_text(encoding="utf-8"))}
    assert "satelite" in registradas
    for a in acciones:
        assert re.search(rf"\b{a['clave']}:", bloque) or a["clave"] in registradas, f"civil.js no abre «{a['clave']}»"
    p = cad_servicio.crear_vacio(dibujante, "Predio", "", "dxf")
    pagina = cliente_de(dibujante).get(url("cad_plano", pk=p.pk)).content.decode()
    assert pagina.index("herramientas/cad/civil-cinta.js") < pagina.index("herramientas/cad/civil-panel.js")
    panel = pagina.split('id="civil-panel" hidden data-url="')[1].split('"')[0].replace("&amp;", "&")
    html = cliente_de(dibujante).get(panel).content.decode()
    datos = json.loads(html.split('id="civil-cinta" type="application/json">')[1].split("</script>")[0])
    assert datos == json.loads(json.dumps(c))
    # la barra de respaldo (editor sin pestañas propias) también trae el grupo de imágenes
    assert 'data-civil-menu="imagenes"' in html and 'data-civil="referencias"' in html


@pytest.fixture
def editor_civil(tmp_path, settings):
    from itt_herramientas.cad import editor

    base = tmp_path / "editor" / editor.version()
    base.mkdir(parents=True)
    (base / "index.html").write_text("<!doctype html><title>x</title>")
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(tmp_path / "editor")
    return base


# -- Archivos de puntos con cualquier formato, KML y superficie de proyecto (motor 0.9) ----------------------------------------------

def _b64(texto: str) -> str:
    return base64.b64encode(texto.encode()).decode()


def test_importar_puntos_con_delimitador_propio_y_columnas_en_el_orden_que_se_quiera(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    texto = "Punto::Cota::Este::Norte::Nota\r\n1::2240,5::483250,125::2148870,5::BN\r\n2::2241,25::483260,5::2148880,25::TN\r\n"
    fm = {"delimitador": "::", "decimal": ",", "saltar": 1, "columnas": ["P", "Z", "E", "N", "D"]}
    d = mandar(cl, "importar_puntos", {"archivo": _b64(texto), "solo_vista": True, "formato": fm, "escala": {"zona": 14}}).json()["resultado"]
    assert d["resumen"]["puntos"] == 2 and d["formato"]["delimitador"] == "::" and d["formato"]["columnas"] == ["P", "Z", "E", "N", "D"]
    assert d["muestra"][0] == ["1", 483250.125, 2148870.5, 2240.5, "BN"] and d["tabla"][0]["campos"][1] == "2240,5"
    # el mismo archivo sin decir nada de columnas: la hoja recibe los renglones para elegirlas
    sin = mandar(cl, "importar_puntos", {"archivo": _b64("a;b;c\nx;y;z\n"), "solo_vista": True, "escala": {"zona": 14}}).json()["resultado"]
    assert "orden de las columnas" in sin["error"] and sin["campos"] == 3 and sin["tabla"][1]["campos"] == ["x", "y", "z"]
    # y la orden completa dibuja
    r = mandar(cl, "importar_puntos", {"archivo": _b64(texto), "formato": fm, "escala": {"denominador": 500, "zona": 14}}).json()["resultado"]
    doc = ezdxf.read(io.StringIO(base64.b64decode(r["dxf"]).decode()))
    assert sorted(i.get_attrib_text("NUM") for i in doc.modelspace().query("INSERT")) == ["1", "2"]
    # un delimitador que choca con el calificador o un decimal imposible se dicen con claridad
    mal = mandar(cl, "importar_puntos", {"archivo": _b64(texto), "formato": {"delimitador": '"', "calificador": '"'}})
    assert mal.status_code == 422 and "calificador" in mal.json()["error"]


def test_exportar_puntos_con_formato_propio(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    pts = [[483250.126, 2148870.5, 2240.5, "1", "BN, NORTE"], [483260.5, 2148880.25, 2241.0, "2", ""]]
    fm = {"delimitador": ";", "decimal": ",", "columnas": ["P", "N", "E", "Z", "D"], "decimales": 2, "decimales_z": 1, "encabezado": True}
    r = mandar(cl, "exportar_puntos", {"puntos": pts, "formato": fm}).json()["resultado"]
    lineas = base64.b64decode(r["archivo"]).decode().splitlines()
    assert lineas[0].split(";") == ["Punto", "Norte", "Este", "Elevacion", "Descripcion"]
    assert lineas[1] == "1;2148870,50;483250,13;2240,5;BN, NORTE"


def test_kml_sale_del_plano_y_vuelve_igual(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    lote = {"vertices": [[X0, Y0], [X0 + 40, Y0], [X0 + 40, Y0 + 25], [X0, Y0 + 25]], "cerrada": True, "capa": "LOTES"}
    r = mandar(cl, "exportar_kml", {"polilineas": [lote], "puntos": [[X0 + 5, Y0 + 5, 12.5, "7", "BN"]], "textos": [{"valor": "LOTE 9", "posicion": [X0 + 20, Y0 + 12], "altura": 3}],
                                    "nombres": {"modo": "etiqueta"}, "formato": "kmz", "nombre": "Predio", "escala": {"zona": 14}}).json()["resultado"]
    assert r["extension"] == "kmz" and r["resumen"]["poligonos"] == 1 and r["resumen"]["superficie"] == pytest.approx(1000)
    vista = mandar(cl, "importar_kml", {"archivo": r["archivo"], "solo_vista": True, "escala": {"zona": 14}}).json()["resultado"]
    assert (vista["resumen"]["poligonos"], vista["resumen"]["puntos"], vista["resumen"]["kmz"]) == (1, 1, True) and "dxf" not in vista
    dentro = mandar(cl, "importar_kml", {"archivo": r["archivo"], "escala": {"zona": 14}, "puntos_como": "proyecto"}).json()["resultado"]
    doc = ezdxf.read(io.StringIO(base64.b64decode(dentro["dxf"]).decode()))
    poli, = [p for p in doc.modelspace().query("LWPOLYLINE") if p.closed]
    assert sorted((round(x, 3), round(y, 3)) for x, y in poli.get_points("xy")) == sorted([(X0, Y0), (X0 + 40, Y0), (X0 + 40, Y0 + 25), (X0, Y0 + 25)])
    ins, = doc.modelspace().query("INSERT")
    assert ins.dxf.name == "PT_PROYECTO" and ins.get_attrib_text("DESC") == "7"
    # sin zona del plano no se puede convertir, y un archivo que no es KML se dice
    assert "zona" in mandar(cl, "importar_kml", {"archivo": r["archivo"]}).json()["error"].lower()
    assert mandar(cl, "importar_kml", {"archivo": _b64("hola"), "escala": {"zona": 14}}).status_code == 422


def test_perfil_secciones_y_curva_masa_leen_la_superficie_de_proyecto(cliente_de, dibujante):
    cl = cliente_de(dibujante)

    def plano(z):
        return [{"handle": "A", "vertices": [[X0 - 50, Y0 - 50, z], [X0 + 150, Y0 - 50, z], [X0 + 150, Y0 + 50, z]]},
                {"handle": "B", "vertices": [[X0 - 50, Y0 - 50, z], [X0 + 150, Y0 + 50, z], [X0 - 50, Y0 + 50, z]]}]

    eje = {"vertices": [[X0, Y0], [X0 + 80, Y0]]}
    base = {"caras": plano(100.0), "caras_proyecto": plano(99.0), "eje": eje, "intervalo": 20}
    def resultado(d):
        return cl.get(d["resultado"]["url"]).json() if "url" in (d.get("resultado") or {}) else d["resultado"]

    p = resultado(mandar(cl, "perfil", base).json())
    assert all(e[1] == pytest.approx(100) and e[2] == pytest.approx(99) for e in p["estaciones"])
    s = resultado(mandar(cl, "secciones", {**base, "izquierda": 20, "derecha": 20}).json())
    assert s["resumen"]["corte"] == pytest.approx(40 * 80) and s["resumen"]["terraplen"] == pytest.approx(0)
    m = resultado(mandar(cl, "curva_masa", {**base, "izquierda": 20, "derecha": 20}).json())
    assert m["resumen"]["corte"] == pytest.approx(40 * 80)
    # rasante y superficie de proyecto no se mezclan
    r = mandar(cl, "secciones", {**base, "rasante": [[0, 99], [80, 99]]})
    assert r.status_code == 422 and "elige una" in r.json()["error"]


# -- Formatos de puntos guardados por cuenta ---------------------------------------------------------------------------------------

def formatos(cliente, uso, **extra):
    return cliente.get(url("civil_formatos"), {"uso": uso, **extra})


def guardar(cliente, uso, nombre, datos):
    return cliente.post(url("civil_formatos") + f"?uso={uso}", json.dumps({"nombre": nombre, "datos": datos}), content_type="application/json")


MI_ESTACION = {"delimitador": ";", "juntar": False, "calificador": '"', "decimal": ",", "columnas": ["P", "N", "E", "Z", "D"], "saltar": 1}


def test_formatos_guardados_se_guardan_se_reemplazan_y_se_borran(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    assert formatos(cl, "importar").json()["formatos"] == []
    r = guardar(cl, "importar", "  Mi   estación ", MI_ESTACION)
    assert r.status_code == 200 and r["Cache-Control"] == "private, no-store"
    d = r.json()
    assert d["guardado"]["nombre"] == "Mi estación" and d["guardado"]["datos"]["columnas"] == ["P", "N", "E", "Z", "D"] and d["guardado"]["datos"]["decimal"] == ","
    # el mismo nombre (sin importar mayúsculas) lo reemplaza; no se duplica
    guardar(cl, "importar", "MI ESTACIÓN", {**MI_ESTACION, "delimitador": "\t"})
    lista = formatos(cl, "importar").json()["formatos"]
    assert [f["nombre"] for f in lista] == ["MI ESTACIÓN"] and lista[0]["datos"]["delimitador"] == "\t"
    # importar y exportar son listas distintas
    assert formatos(cl, "exportar").json()["formatos"] == []
    guardar(cl, "exportar", "Colector X", {"delimitador": ",", "columnas": ["P", "E", "N", "Z", "D"], "decimales": 2, "encabezado": True})
    assert [f["nombre"] for f in formatos(cl, "exportar").json()["formatos"]] == ["Colector X"]
    r = cl.delete(url("civil_formatos") + "?uso=importar&nombre=mi%20estaci%C3%B3n")
    assert r.status_code == 200 and r.json()["formatos"] == []
    assert cl.delete(url("civil_formatos") + "?uso=importar&nombre=ya%20no%20est%C3%A1").status_code == 404


def test_formatos_guardados_se_validan(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    for nombre, datos, texto in [("", MI_ESTACION, "nombre"), ("x" * 61, MI_ESTACION, "60"), ("A", {**MI_ESTACION, "columnas": ["P", "Z", "Z"]}, "dos veces"),
                                 ("A", {**MI_ESTACION, "delimitador": "123456789"}, "8 caracteres"), ("A", {**MI_ESTACION, "decimal": ";"}, "decimal"),
                                 ("A", {**MI_ESTACION, "saltar": 5000}, "saltan"), ("A", "no es un formato", "formato")]:
        r = guardar(cl, "importar", nombre, datos)
        assert r.status_code == 422 and texto in r.json()["error"], (nombre, r.json())
    assert guardar(cl, "otro", "A", MI_ESTACION).status_code == 422
    assert formatos(cl, "otro").status_code == 422
    assert cl.post(url("civil_formatos") + "?uso=importar", b"{no es json", content_type="application/json").status_code == 422
    assert cl.post(url("civil_formatos") + "?uso=importar", b"[1]", content_type="application/json").status_code == 422
    assert cl.put(url("civil_formatos") + "?uso=importar", b"{}", content_type="application/json").status_code == 405
    # solo se guarda lo que el motor entiende: nada de claves de más
    d = guardar(cl, "importar", "Limpio", {**MI_ESTACION, "x": {"a": 1}, "__proto__": 1}).json()["guardado"]["datos"]
    assert set(d) == {"delimitador", "juntar", "calificador", "decimal", "columnas", "saltar"}


def test_formatos_guardados_tienen_tope_por_cuenta_y_uso(cliente_de, dibujante, otra, monkeypatch):
    monkeypatch.setattr(servicio, "MAX_FORMATOS", 3)
    cl = cliente_de(dibujante)
    for k in range(3):
        assert guardar(cl, "importar", f"F{k}", MI_ESTACION).status_code == 200
    r = guardar(cl, "importar", "F3", MI_ESTACION)
    assert r.status_code == 422 and "3 formatos" in r.json()["error"]
    assert guardar(cl, "importar", "f0", {**MI_ESTACION, "saltar": 2}).status_code == 200        # reemplazar un existente no cuenta como uno nuevo
    assert guardar(cl, "exportar", "F0", {"delimitador": ",", "columnas": ["P", "E", "N"]}).status_code == 200  # el tope es por uso
    assert guardar(cliente_de(otra), "importar", "F3", MI_ESTACION).status_code == 200            # y por cuenta


def test_formatos_guardados_son_de_la_cuenta_y_solo_de_quien_edita(cliente_de, dibujante, otra, supervisora):
    guardar(cliente_de(dibujante), "importar", "Mi estación", MI_ESTACION)
    assert formatos(cliente_de(otra), "importar").json()["formatos"] == []
    assert cliente_de(otra).delete(url("civil_formatos") + "?uso=importar&nombre=Mi%20estaci%C3%B3n").status_code == 404
    assert len(formatos(cliente_de(dibujante), "importar").json()["formatos"]) == 1
    cs = cliente_de(supervisora)
    assert formatos(cs, "importar").status_code in (302, 403)
    assert guardar(cs, "importar", "X", MI_ESTACION).status_code in (302, 403)
    from itt_herramientas.models import FormatoPuntos

    assert not FormatoPuntos.objects.filter(nombre="X").exists()


def test_los_formatos_se_borran_con_la_cuenta(dibujante, otra):
    from itt_herramientas.models import FormatoPuntos

    servicio.guardar_formato(dibujante.pk, "importar", "A", MI_ESTACION)
    servicio.guardar_formato(dibujante.pk, "exportar", "B", {"delimitador": ",", "columnas": ["P", "E", "N"]})
    servicio.guardar_formato(otra.pk, "importar", "A", MI_ESTACION)
    assert servicio.al_eliminar_cuenta(dibujante.pk) == "Civil: se borraron 2 formatos de puntos guardados."
    assert servicio.al_eliminar_cuenta(dibujante.pk) is None                                    # idempotente
    assert list(FormatoPuntos.objects.values_list("cuenta_id", flat=True)) == [otra.pk]
    from itt_herramientas.modulo import MODULO

    assert "itt_herramientas.civil.servicio.al_eliminar_cuenta" in MODULO.al_eliminar_cuenta
