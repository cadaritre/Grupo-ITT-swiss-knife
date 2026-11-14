from __future__ import annotations

import json
import uuid
from unittest import mock

import pytest

from itt_herramientas.comun import perfiles
from itt_herramientas.croquis import servicio
from itt_herramientas.dominio import location_sketch as ls
from itt_herramientas.models import Croquis, Perfil
from paridad import comparadores

from .conftest import url

pytestmark = pytest.mark.django_db
CUADRO = [(28.6330, -106.0700), (28.6330, -106.0670), (28.6352, -106.0670), (28.6352, -106.0700)]


@pytest.fixture
def croquista(crear_usuario, roles):
    u = crear_usuario("cris")
    roles.conceder(u, "herramientas", "croquis")
    return u


def datos(puntos=CUADRO, **campos):
    base = {"title": "Croquis de prueba", "client": "Cliente", "project": "Nave", "location": "Chihuahua", "sketch_date": "13/11/2026", "geometry": "Polígono",
            "map_layer": "Base neutra", "contour_interval": "5 m", "notes": "Notas", "points": [{"name": f"V{i}", "latitude": a, "longitude": b, "description": ""} for i, (a, b) in enumerate(puntos, 1)]}
    base.update(campos)
    return base


def post(c, nombre, cuerpo, **kw):
    return c.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


def sinc(c, idc, d, servidor=None, base=None):
    r = post(c, "croquis_sincronizar", {"id_cliente": str(idc), "servidor_id": servidor, "revision_base": base, "datos": d})
    assert r.status_code == 200, r.content
    return r.json()


# Respuesta de Overpass «grabada»: así llega con `out geom`. La prueba no usa la red.
def g(*pares):
    return [{"lat": a, "lon": b} for a, b in pares]


OVERPASS = [
    {"type": "way", "id": 1, "tags": {"building": "yes", "name": "Nave 1"}, "geometry": g((28.6334, -106.0695), (28.6334, -106.0685), (28.6340, -106.0685), (28.6340, -106.0695), (28.6334, -106.0695))},
    {"type": "way", "id": 2, "tags": {"highway": "primary", "name": "Av. Universidad"}, "geometry": g((28.6325, -106.0705), (28.6340, -106.0690), (28.6355, -106.0672))},
    {"type": "way", "id": 3, "tags": {"waterway": "canal"}, "geometry": g((28.6331, -106.0699), (28.6345, -106.0669))},
    {"type": "way", "id": 4, "tags": {"building": "yes"}, "geometry": g((28.7000, -106.2000), (28.7000, -106.1990), (28.7010, -106.1990), (28.7000, -106.2000))},       # fuera del polígono
]


def con_overpass():
    return mock.patch("itt_herramientas.dominio.osm_vector._fetch_query_elements", return_value=OVERPASS)


def test_solo_con_el_rol(cliente_de, usuaria, croquista):
    assert cliente_de(usuaria).get(url("croquis_lista")).status_code == 403
    assert cliente_de(croquista).get(url("croquis_lista")).status_code == 200


def test_contexto(cliente_de, croquista):
    r = cliente_de(croquista).get(url("croquis_contexto")).json()
    assert r["limite_km2"] == 50 and "building" in r["categorias"] and "5 m" in r["contornos"]
    assert {c["nombre"] for c in r["capas_mapa"]} >= {"Calles - OpenStreetMap", "Base neutra"}


def test_sincronizacion_idempotente_conflicto_y_aislamiento(cliente_de, croquista, crear_usuario, roles):
    c = cliente_de(croquista)
    idc = uuid.uuid4()
    a = sinc(c, idc, datos())
    assert sinc(c, idc, datos())["resultado"] == "sin_cambios" and Croquis.objects.count() == 1
    b = sinc(c, idc, datos(client="Otro"), a["servidor_id"], a["revision"])
    assert b["resultado"] == "actualizada" and b["revision"] == 2
    r = sinc(c, idc, datos(client="Del teléfono"), a["servidor_id"], 1)
    assert r["resultado"] == "copia" and Croquis.objects.get(pk=a["servidor_id"]).datos["client"] == "Otro"
    assert Croquis.objects.get(pk=r["servidor_id"]).titulo.endswith("(desde el teléfono)")
    otra = crear_usuario("otra")
    roles.conceder(otra, "herramientas", "croquis")
    co = cliente_de(otra)
    assert co.get(url("croquis_detalle", pk=a["servidor_id"])).status_code == 404
    assert post(co, "croquis_vectorizar", {}, pk=a["servidor_id"]).status_code == 404
    assert co.get(url("croquis_editable", pk=a["servidor_id"])).status_code == 404


def test_validacion(cliente_de, croquista):
    c = cliente_de(croquista)
    malos = [datos(map_layer="Satélite"), datos(contour_interval="3 m"), datos(geometry="Círculo"), datos(sketch_date="2026-11-13"), datos(title="x" * 201),
             datos(puntos=[(95, 0)] * 3), datos(puntos=[(float("nan"), 0)] * 3), datos(points=[{"name": "V1", "latitude": "28", "longitude": 1}]), datos(notes="x" * 3001),
             datos(points=[{"name": "V", "latitude": 28.0, "longitude": -106.0, "description": ""}] * 501), "no objeto"]
    for malo in malos:
        assert post(c, "croquis_sincronizar", {"id_cliente": str(uuid.uuid4()), "datos": malo}).status_code == 422, str(malo)[:80]
    assert Croquis.objects.count() == 0


def test_limite_de_area_de_la_web(cliente_de, croquista):
    c = cliente_de(croquista)
    grande = [(28.0, -106.0), (28.0, -105.5), (28.5, -105.5), (28.5, -106.0)]               # ~2 700 km²
    a = sinc(c, uuid.uuid4(), datos(puntos=grande))
    r = post(c, "croquis_vectorizar", {}, pk=a["servidor_id"])
    assert r.status_code == 422 and "módulo local" in r.json()["error"]
    assert c.get(url("croquis_lista")).json()["croquis"][0]["area_km2"] > 50
    pocos = sinc(c, uuid.uuid4(), datos(puntos=CUADRO[:2]))
    assert post(c, "croquis_vectorizar", {}, pk=pocos["servidor_id"]).status_code == 422


def test_vectorizar_recorta_clasifica_y_exporta_lo_mismo_que_el_escritorio(cliente_de, croquista, settings, monkeypatch, tmp_path):
    from reportlab import rl_config

    monkeypatch.setattr(rl_config, "invariant", 1)
    c = cliente_de(croquista)
    a = sinc(c, uuid.uuid4(), datos())
    with con_overpass():
        r = post(c, "croquis_vectorizar", {}, pk=a["servidor_id"])
    assert r.status_code == 202, r.content
    fila = c.get(url("croquis_lista")).json()["croquis"][0]
    assert fila["vectores"] == "vigentes" and fila["resumen"]["conteos"] == {"building": 1, "road": 1, "water": 1}            # el edificio de fuera del polígono no entra
    geo = c.get(url("croquis_vectores", pk=a["servidor_id"])).json()
    assert {f["properties"]["categoria"] for f in geo["features"]} == {"building", "road", "water"}
    def posiciones(geometria):
        c = geometria["coordinates"]
        return [p for linea in (c if geometria["type"] == "MultiLineString" else [anillo for poligono in c for anillo in poligono]) for p in linea]
    assert all(-106.0700 <= x <= -106.0670 and 28.6330 <= y <= 28.6352 for f in geo["features"] for x, y in posiciones(f["geometry"]))     # recortado al polígono
    # PDF y DXF por la web = los del dominio con los mismos datos (el escritorio, verificado en paridad/).
    modelo = Croquis.objects.get()
    for formato in ("pdf", "dxf"):
        assert post(c, "croquis_exportar", {"formato": formato}, pk=a["servidor_id"]).status_code == 202
        web = c.get(url("croquis_descargar", pk=a["servidor_id"]))
        assert web.status_code == 200 and f".{formato}" in web["Content-Disposition"] and "CROQUIS%20DE%20UBICACION" in web["Content-Disposition"].replace(" ", "%20")
        cuerpo = b"".join(web.streaming_content)
        directo = tmp_path / f"directo.{formato}"
        datos_dom = servicio.datos_de_dominio(modelo)
        with perfiles.activar(Perfil.objects.get(clave="grupo_itt")):
            from itt_herramientas.dominio import branding

            if formato == "pdf":
                ls.generate_sketch_pdf(datos_dom, directo, logo_path=branding.active_profile().logo_path)
            else:
                ls.generate_sketch_dxf(datos_dom, directo)
        if formato == "pdf":
            assert cuerpo == directo.read_bytes()
        else:
            web_dxf = tmp_path / "web.dxf"
            web_dxf.write_bytes(cuerpo)
            resultado = comparadores.comparar_dxf(web_dxf, directo)
            assert resultado.ok, resultado.motivo
    assert Croquis.objects.get().salida_formato == "dxf"


def test_cambiar_el_area_invalida_los_vectores_y_no_se_exporta(cliente_de, croquista):
    c = cliente_de(croquista)
    a = sinc(c, uuid.uuid4(), datos())
    with con_overpass():
        post(c, "croquis_vectorizar", {}, pk=a["servidor_id"])
    nuevos = datos(puntos=[(28.6330, -106.0700), (28.6330, -106.0660), (28.6352, -106.0660), (28.6352, -106.0700)])
    sinc(c, uuid.uuid4(), nuevos, a["servidor_id"], 1)
    assert Croquis.objects.get().datos["points"][1]["longitude"] == -106.066
    assert c.get(url("croquis_lista")).json()["croquis"][0]["vectores"] == "obsoletos"
    r = post(c, "croquis_exportar", {"formato": "pdf"}, pk=a["servidor_id"])
    assert r.status_code == 409 and "vectoriza de nuevo" in r.json()["error"].lower()
    assert c.get(url("croquis_vectores", pk=a["servidor_id"])).status_code == 409
    # Cambiar solo el título o las notas NO invalida (no afecta a los vectores).
    sinc(c, uuid.uuid4(), dict(nuevos, title="Otro título"), a["servidor_id"], 2)


def test_sin_vectorizar_no_exporta_y_la_topografica_pide_curvas(cliente_de, croquista):
    c = cliente_de(croquista)
    a = sinc(c, uuid.uuid4(), datos())
    r = post(c, "croquis_exportar", {"formato": "pdf"}, pk=a["servidor_id"])
    assert r.status_code == 409 and "Vectorizar mapa" in r.json()["error"]
    assert post(c, "croquis_exportar", {"formato": "docx"}, pk=a["servidor_id"]).status_code == 422
    topo = sinc(c, uuid.uuid4(), datos(map_layer="Topográfico - OpenTopoMap"))
    with con_overpass(), mock.patch("itt_herramientas.dominio.terrain_contours.fetch_elevation_contours", return_value=[]):
        post(c, "croquis_vectorizar", {}, pk=topo["servidor_id"])
    r = post(c, "croquis_exportar", {"formato": "pdf"}, pk=topo["servidor_id"])
    assert r.status_code == 409 and "curvas de nivel" in r.json()["error"]


def test_editable_para_el_modulo_local(cliente_de, croquista):
    c = cliente_de(croquista)
    a = sinc(c, uuid.uuid4(), datos())
    sin = json.loads(c.get(url("croquis_editable", pk=a["servidor_id"])).content)
    assert sin["features"] == [] and len(sin["points"]) == 4
    with con_overpass():
        post(c, "croquis_vectorizar", {}, pk=a["servidor_id"])
    e = c.get(url("croquis_editable", pk=a["servidor_id"]))
    assert "EDITABLE_CROQUIS" in e["Content-Disposition"]
    completo = ls.SketchData.from_dict(json.loads(e.content))                       # lo que abre el escritorio / el módulo local
    assert {f.category for f in completo.features} == {"building", "road", "water"} and completo.vectorized_at


def test_importar_csv_y_kml(cliente_de, croquista):
    from django.core.files.uploadedfile import SimpleUploadedFile

    c = cliente_de(croquista)
    csv = "Punto;Latitud;Longitud;Descripción\nA;28,633;-106,07;Esquina\nB;28.633;-106.067;\n".encode()
    r = c.post(url("croquis_importar"), {"archivo": SimpleUploadedFile("p.csv", csv)}).json()
    assert [p["name"] for p in r["puntos"]] == ["A", "B"] and r["puntos"][0]["latitude"] == 28.633 and r["puntos"][0]["description"] == "Esquina" and r["reemplaza"] is False
    kml = ('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           '<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>-106.07,28.633,0 -106.067,28.633,0 -106.067,28.635,0 -106.07,28.635,0 -106.07,28.633,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>'
           '<Placemark><Polygon><outerBoundaryIs><LinearRing><coordinates>-106.0,28.0,0 -106.0,28.001,0 -105.999,28.0005,0 -106.0,28.0,0</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>'
           '</Document></kml>').encode()
    r = c.post(url("croquis_importar"), {"archivo": SimpleUploadedFile("predio.kml", kml)}).json()
    assert len(r["puntos"]) == 4 and r["poligonos"] == 2 and r["reemplaza"] is True            # se toma el polígono mayor
    for malo, nombre in ((b"sin;columnas\n1;2\n", "x.csv"), (b"no es xml", "x.kml"), (b"x", "x.pdf"), (b"PK\x03\x04roto", "x.kmz")):
        assert c.post(url("croquis_importar"), {"archivo": SimpleUploadedFile(nombre, malo)}).status_code == 422, nombre


def test_descargar_sin_salida_es_404_y_borrar(cliente_de, croquista):
    c = cliente_de(croquista)
    a = sinc(c, uuid.uuid4(), datos())
    assert c.get(url("croquis_descargar", pk=a["servidor_id"])).status_code == 404
    assert c.delete(url("croquis_detalle", pk=a["servidor_id"])).status_code == 200 and Croquis.objects.count() == 0
