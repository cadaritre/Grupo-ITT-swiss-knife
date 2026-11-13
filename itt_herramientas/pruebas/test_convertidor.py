from __future__ import annotations

import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from itt_herramientas.comun import preferencias
from itt_herramientas.convertidor import servicio
from itt_herramientas.dominio import geospatial_converter as gc
from itt_herramientas.models import Conversion
from paridad import comparadores

from .conftest import subir_tus, url

pytestmark = pytest.mark.django_db
CAPAS_DXF = {"PREDIO", "EJES", "TANQUES", "CURVAS", "VERTICES", "BORDE ABIERTO"}


def subir_archivo(cliente, ruta: Path) -> Conversion:
    r, _ = subir_tus(cliente, ruta.read_bytes(), "herramientas.convertidor_entrada", ruta.name)
    assert r.status_code == 204, getattr(r, "content", b"")
    return Conversion.objects.latest("creada")


def listo(cliente, ruta: Path) -> Conversion:
    c = subir_archivo(cliente, ruta)
    assert cliente.post(url("convertidor_analizar", pk=c.pk), {"zona": "13", "hemisferio": "N"}).status_code == 302
    c.refresh_from_db()
    assert c.estado == Conversion.Estado.LISTO, c.error
    return c


def test_solo_con_el_rol_del_convertidor(cliente_de, usuaria, crear_usuario):
    assert cliente_de(usuaria).get(url("convertidor")).status_code == 403          # tiene Herramientas, pero no este rol
    assert cliente_de(crear_usuario("nadie")).get(url("convertidor")).status_code == 404
    r, _ = subir_tus(cliente_de(usuaria), b"x", "herramientas.convertidor_entrada", "a.dxf")
    assert r.status_code == 404                                                       # el destino ni se revela


def test_subida_de_un_dxf_crea_la_conversion(cliente_de, convertidora, entradas_geo):
    c = subir_archivo(cliente_de(convertidora), entradas_geo["convertidor.dxf"])
    assert (c.cuenta_id, c.nombre, c.formato, c.estado) == (convertidora.pk, "convertidor.dxf", "dxf", "listo")   # el análisis empieza al terminar la subida
    assert servicio.ruta_origen(c).read_bytes() == entradas_geo["convertidor.dxf"].read_bytes()
    assert cliente_de(convertidora).get(url("convertidor")).status_code == 200


@pytest.mark.parametrize("nombre, contenido, mensaje", [
    ("nota.txt", b"hola", "DXF, KML o KMZ"),
    ("falso.kmz", b"esto no es un zip", "comprimido"),
    ("falso.kml", b"\x00\x01\x02binario", "XML"),
    ("binario.dxf", b"\x00" * 200, "DXF"),
])
def test_archivos_que_no_son_del_convertidor_se_rechazan(cliente_de, convertidora, nombre, contenido, mensaje):
    r, _ = subir_tus(cliente_de(convertidora), contenido, "herramientas.convertidor_entrada", nombre)
    assert r.status_code == 422 and mensaje in r.content.decode()
    assert not Conversion.objects.exists()


def test_limite_de_tamano_antes_de_subir(cliente_de, convertidora, settings, entradas_geo):
    settings.TV_HERRAMIENTAS_LIMITES = {"convertidor": {"bytes": 1000, "entidades": 200_000}}
    r, _ = subir_tus(cliente_de(convertidora), entradas_geo["convertidor.dxf"].read_bytes(), "herramientas.convertidor_entrada", "grande.dxf")
    assert r.status_code == 413 and not Conversion.objects.exists()


def test_analisis_del_dxf_da_capas_resumen_y_vista(cliente_de, convertidora, entradas_geo):
    c = listo(cliente_de(convertidora), entradas_geo["convertidor.dxf"])
    assert {x["nombre"] for x in c.resumen["capas"]} == CAPAS_DXF
    assert c.resumen["elementos"] > 0 and c.resumen["geometrias"]["poligonos"] >= 1 and c.resumen["limites"]
    vista = json.loads((servicio.raiz() / c.vista_ruta).read_text())
    assert vista["type"] == "FeatureCollection" and {f["properties"]["capa"] for f in vista["features"]} <= CAPAS_DXF
    oeste, sur, este, norte = c.resumen["limites"]
    assert -180 < oeste < este < 180 and -90 < sur < norte < 90                       # WGS84, no metros UTM
    assert -107 < oeste < -106 and 28 < sur < 29                                       # Chihuahua (UTM 13N)
    pagina = cliente_de(convertidora).get(url("convertidor_banco", pk=c.pk)).content.decode()
    for capa in CAPAS_DXF:
        assert capa in pagina
    assert "maplibre-gl-csp.js" in pagina and url("convertidor_vista", pk=c.pk) in pagina


def test_cambiar_la_zona_vuelve_a_leer_el_dxf(cliente_de, convertidora, entradas_geo):
    cl = cliente_de(convertidora)
    c = listo(cl, entradas_geo["convertidor.dxf"])
    antes = c.resumen["limites"]
    cl.post(url("convertidor_analizar", pk=c.pk), {"zona": "12", "hemisferio": "N"})
    c.refresh_from_db()
    assert (c.zona, c.estado) == (12, "listo") and c.resumen["limites"] != antes
    assert cl.post(url("convertidor_analizar", pk=c.pk), {"zona": "61", "hemisferio": "N"}).status_code == 302
    c.refresh_from_db()
    assert c.zona == 12                                                               # zona inválida: no cambia


def test_kml_y_kmz_sugieren_la_zona_utm(cliente_de, convertidora, entradas_geo):
    for nombre in ("capas.kml", "capas.kmz"):
        c = listo(cliente_de(convertidora), entradas_geo[nombre])
        assert c.resumen["zona_sugerida"] == [13, "N"]
        assert {x["nombre"] for x in c.resumen["capas"]} == {"Predios", "Ejes"}


def test_limite_de_entidades_manda_al_modulo_local(cliente_de, convertidora, settings, entradas_geo):
    settings.TV_HERRAMIENTAS_LIMITES = {"convertidor": {"bytes": 50 * 1024 * 1024, "entidades": 3}}
    c = subir_archivo(cliente_de(convertidora), entradas_geo["convertidor.dxf"])
    cliente_de(convertidora).post(url("convertidor_analizar", pk=c.pk), {"zona": "13", "hemisferio": "N"})
    c.refresh_from_db()
    assert c.estado == "error" and "módulo local" in c.error and "supera" in c.error
    pagina = cliente_de(convertidora).get(url("convertidor_banco", pk=c.pk)).content.decode()
    assert "módulo local" in pagina


def test_archivo_ilegible_muestra_el_error_sin_romper(cliente_de, convertidora, tmp_path):
    dxf = tmp_path / "roto.dxf"
    dxf.write_text("0\nSECTION\n2\nENTITIES\n0\nLINE\n10\nno-es-numero\n")
    c = subir_archivo(cliente_de(convertidora), dxf)
    cliente_de(convertidora).post(url("convertidor_analizar", pk=c.pk), {"zona": "13", "hemisferio": "N"})
    c.refresh_from_db()
    assert c.estado == "error" and c.error
    assert cliente_de(convertidora).get(url("convertidor_banco", pk=c.pk)).status_code == 200


def test_kmz_que_descomprime_demasiado_se_rechaza(cliente_de, convertidora, tmp_path, monkeypatch):
    monkeypatch.setattr(servicio, "MAX_DESCOMPRIMIDO", 1000)
    kmz = tmp_path / "bomba.kmz"
    with zipfile.ZipFile(kmz, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("doc.kml", "<kml>" + " " * 50_000 + "</kml>")
    c = subir_archivo(cliente_de(convertidora), kmz)
    cliente_de(convertidora).post(url("convertidor_analizar", pk=c.pk), {"zona": "13", "hemisferio": "N"})
    c.refresh_from_db()
    assert c.estado == "error" and "excesivo" in c.error


def test_dxf_a_kmz_igual_que_el_dominio_con_las_capas_elegidas(cliente_de, convertidora, entradas_geo, tmp_path):
    cl = cliente_de(convertidora)
    c = listo(cl, entradas_geo["convertidor.dxf"])
    r = cl.post(url("convertidor_convertir", pk=c.pk), {"formato": "kmz", "zona": "13", "hemisferio": "N", "capa": ["PREDIO", "CURVAS"], "etiquetas": "on"})
    assert r.status_code == 302
    c.refresh_from_db()
    assert c.salida_formato == "kmz" and (servicio.raiz() / c.salida_ruta).name == "convertidor_convertido.kmz"
    esperado = gc.convert_file(gc.read_geospatial(entradas_geo["convertidor.dxf"], zone=13, hemisphere="N", curve_tolerance=0.01), tmp_path / "convertidor_convertido.kmz",
                               zone=13, hemisphere="N", selected_layers={"PREDIO", "CURVAS"}, add_labels=True, add_hatches=False, clamp_to_ground=False)
    comparacion = comparadores.comparar_geometria_kml(servicio.raiz() / c.salida_ruta, esperado)
    assert comparacion.ok, comparacion.motivo                                           # misma estructura y geometría (la web aproxima las curvas a 1 cm)
    with zipfile.ZipFile(servicio.raiz() / c.salida_ruta) as zf:
        kml = zf.read("doc.kml").decode()
    assert "PREDIO" in kml and "TANQUES" not in kml                                    # las capas apagadas no se exportan
    d = cl.get(url("convertidor_descargar", pk=c.pk))
    assert d.status_code == 200 and d["Content-Type"] == "application/vnd.google-earth.kmz"
    assert 'filename="convertidor_convertido.kmz"' in d["Content-Disposition"] and b"".join(d.streaming_content) == (servicio.raiz() / c.salida_ruta).read_bytes()


def test_kml_a_dxf_igual_que_el_dominio(cliente_de, convertidora, entradas_geo, tmp_path):
    cl = cliente_de(convertidora)
    c = listo(cl, entradas_geo["capas.kml"])
    cl.post(url("convertidor_convertir", pk=c.pk), {"formato": "dxf", "zona": "13", "hemisferio": "N", "capa": ["Predios", "Ejes"], "etiquetas": "on", "rellenos": "on"})
    c.refresh_from_db()
    esperado = gc.convert_file(gc.read_geospatial(entradas_geo["capas.kml"]), tmp_path / "capas_convertido.dxf", zone=13, hemisphere="N",
                               selected_layers={"Predios", "Ejes"}, add_labels=True, add_hatches=True)
    assert comparadores.comparar_geometria_dxf(servicio.raiz() / c.salida_ruta, esperado).ok
    d = cl.get(url("convertidor_descargar", pk=c.pk))
    assert d["Content-Type"] == "image/vnd.dxf" and "capas_convertido.dxf" in d["Content-Disposition"]


def test_convertir_valida_formato_capas_y_zona(cliente_de, convertidora, entradas_geo):
    cl = cliente_de(convertidora)
    c = listo(cl, entradas_geo["convertidor.dxf"])
    base = {"formato": "kml", "zona": "13", "hemisferio": "N", "capa": ["PREDIO"]}
    for malo in ({**base, "formato": "dxf"}, {**base, "capa": []}, {**base, "capa": ["NO_EXISTE"]}, {**base, "zona": "0"}, {**base, "hemisferio": "X"}, {**base, "zona": "abc"}):
        assert cl.post(url("convertidor_convertir", pk=c.pk), malo).status_code == 302
        c.refresh_from_db()
        assert c.salida_ruta == "", malo
    assert cl.post(url("convertidor_convertir", pk=c.pk), base).status_code == 302
    c.refresh_from_db()
    assert c.salida_formato == "kml"


def test_convertir_recuerda_las_opciones_de_la_cuenta(cliente_de, convertidora, entradas_geo):
    cl = cliente_de(convertidora)
    c = listo(cl, entradas_geo["convertidor.dxf"])
    cl.post(url("convertidor_convertir", pk=c.pk), {"formato": "kml", "zona": "14", "hemisferio": "S", "capa": ["PREDIO"]})
    p = preferencias.obtener(convertidora.pk)["geospatial"]
    assert (p["utm_zone"], p["hemisphere"], p["labels"], p["hatches"], p["clamp_to_ground"]) == (14, "S", False, False, False)


def test_cada_conversion_es_de_una_sola_cuenta(cliente_de, convertidora, crear_usuario, roles, entradas_geo):
    c = listo(cliente_de(convertidora), entradas_geo["convertidor.dxf"])
    cliente_de(convertidora).post(url("convertidor_convertir", pk=c.pk), {"formato": "kml", "zona": "13", "hemisferio": "N", "capa": ["PREDIO"]})
    otra = crear_usuario("otra")
    roles.conceder(otra, "herramientas", "convertidor")
    o = cliente_de(otra)
    for nombre in ("convertidor_banco", "convertidor_vista", "convertidor_descargar"):
        assert o.get(url(nombre, pk=c.pk)).status_code == 404, nombre
    for nombre in ("convertidor_analizar", "convertidor_convertir", "convertidor_borrar"):
        assert o.post(url(nombre, pk=c.pk), {}).status_code == 404, nombre
    assert Conversion.objects.filter(pk=c.pk).exists()


def test_eliminar_borra_archivos_y_fila(cliente_de, convertidora, entradas_geo):
    cl = cliente_de(convertidora)
    c = listo(cl, entradas_geo["convertidor.dxf"])
    carpeta = servicio.carpeta(c)
    assert carpeta.is_dir()
    assert cl.post(url("convertidor_borrar", pk=c.pk)).status_code == 302
    assert not carpeta.exists() and not Conversion.objects.filter(pk=c.pk).exists()


def test_limpieza_diaria_borra_lo_de_mas_de_7_dias(cliente_de, convertidora, entradas_geo):
    cl = cliente_de(convertidora)
    vieja, nueva = listo(cl, entradas_geo["convertidor.dxf"]), listo(cl, entradas_geo["capas.kml"])
    Conversion.objects.filter(pk=vieja.pk).update(creada=timezone.now() - timedelta(days=8))
    assert servicio.limpiar() == 1
    assert not Conversion.objects.filter(pk=vieja.pk).exists() and not servicio.carpeta(vieja).exists()
    assert Conversion.objects.filter(pk=nueva.pk).exists()
    from itt_herramientas.modulo import MODULO
    assert "itt_herramientas.convertidor.servicio.limpiar" in MODULO.limpiezas


def test_vista_previa_se_simplifica_cuando_es_enorme(monkeypatch):
    dataset = gc.GeoDataset(features=[gc.GeoFeature("LineString", [[(-106 + i * 1e-6, 28.0 + (i % 7) * 1e-6, 0.0) for i in range(60_000)]], "CAPA", "larga")])
    vista = servicio.geojson(dataset)
    puntos = sum(len(f["geometry"]["coordinates"]) for f in vista["features"])
    assert 2 <= puntos < 60_000
    pocos = gc.GeoDataset(features=[gc.GeoFeature("Polygon", [[(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0)]], "P", "")])
    assert servicio.geojson(pocos)["features"][0]["geometry"]["coordinates"][0][0] == [0.0, 0.0]
    assert servicio.geojson(pocos)["features"][0]["geometry"]["coordinates"][0][-1] == [0.0, 0.0]                  # el anillo se cierra
