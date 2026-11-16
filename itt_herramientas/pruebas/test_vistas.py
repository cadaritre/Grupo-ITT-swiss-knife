from __future__ import annotations

import io
import zipfile
import json

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from itt_herramientas.comun import diccionarios, perfiles
from itt_herramientas.dominio.almacenamiento import DEFAULT_SETTINGS
from itt_herramientas.models import Machote, Perfil, PerfilActivo, TerminoTecnico

from .conftest import url

pytestmark = pytest.mark.django_db


def test_las_urls_son_relativas_a_la_raiz_del_host():
    assert url("inicio") == "/"
    assert url("perfil", clave="tresvizo") == "/perfiles/tresvizo/"
    assert url("importar_confirmar", token="00000000-0000-0000-0000-000000000001").startswith("/importar/")


def test_sin_sesion_redirige_y_sin_rol_es_404(client, crear_usuario):
    client.defaults["HTTP_HOST"] = "tools.tresvizo.com"
    assert client.get(url("inicio")).status_code == 302
    client.force_login(crear_usuario("sin_rol"))
    assert client.get(url("inicio")).status_code == 404


def test_inicio_lista_las_herramientas_del_rol(cliente_de, usuaria):
    r = cliente_de(usuaria).get(url("inicio"))
    cuerpo = r.content.decode()
    assert r.status_code == 200 and url("cotizaciones") in cuerpo and "Cotizaciones" in cuerpo
    assert url("convertidor") not in cuerpo                      # la cuenta de prueba solo tiene el rol de cotizaciones
    assert url("perfiles") not in r.content.decode()                       # el atajo de administración no se muestra


def test_paginas_de_administracion_son_solo_para_admin(cliente_de, usuaria, administradora):
    c = cliente_de(usuaria)
    for nombre, kw in (("perfiles", {}), ("perfil", {"clave": "tresvizo"}), ("perfil_textos", {"clave": "tresvizo"}),
                       ("perfil_paleta", {"clave": "tresvizo"})):
        assert c.get(url(nombre, **kw)).status_code == 403, nombre
    c = cliente_de(administradora)
    for nombre, kw in (("perfiles", {}), ("perfil", {"clave": "tresvizo"}), ("perfil_textos", {"clave": "tresvizo"}),
                       ("perfil_paleta", {"clave": "tresvizo"})):
        assert c.get(url(nombre, **kw)).status_code == 200, nombre
    assert c.get(url("perfil", clave="otro")).status_code == 404


def test_preferencias_se_guardan_por_cuenta(cliente_de, usuaria):
    c = cliente_de(usuaria)
    pagina = c.get(url("preferencias"))
    assert pagina.status_code == 200
    datos = {"geospatial_utm_zone": 14, "geospatial_hemisphere": "N", "geospatial_map_layer": "Calles - OpenStreetMap",
             "sketches_map_layer": "Calles - OpenStreetMap", "sketches_contour_interval": "10 m", "geospatial_labels": "on"}
    assert c.post(url("preferencias"), datos).status_code == 302
    from itt_herramientas.comun import preferencias
    p = preferencias.obtener(usuaria.pk)
    assert p["geospatial"]["utm_zone"] == 14 and p["sketches"]["contour_interval"] == "10 m"
    assert p["geospatial"]["hatches"] is False and p["geospatial"]["labels"] is True
    datos["geospatial_utm_zone"] = 99
    assert c.post(url("preferencias"), datos).status_code == 200


def test_diccionario_personal_y_tecnico(cliente_de, usuaria, administradora):
    c = cliente_de(usuaria)
    assert c.post(url("diccionario"), {"accion": "agregar", "palabra": "Mojonera", "idioma": "es"}).status_code == 302
    assert diccionarios.palabras_personales(usuaria.pk)["es"] == ["mojonera"]
    assert c.post(url("diccionario"), {"accion": "agregar_termino", "palabra": "xyzzy", "idioma": "ambos"}).status_code == 400
    assert not TerminoTecnico.objects.exists()
    assert c.post(url("diccionario"), {"accion": "quitar", "palabra": "mojonera", "idioma": "es"}).status_code == 302
    assert diccionarios.palabras_personales(usuaria.pk)["es"] == []
    a = cliente_de(administradora)
    a.post(url("diccionario"), {"accion": "agregar_termino", "palabra": "fotomosaico", "idioma": "ambos"})
    assert "fotomosaico" in a.get(url("diccionario")).content.decode()
    a.post(url("diccionario"), {"accion": "quitar_termino", "id": TerminoTecnico.objects.get().pk})
    assert not TerminoTecnico.objects.exists()


def png():
    b = io.BytesIO()
    Image.new("RGB", (30, 20), "#123456").save(b, "PNG")
    return b.getvalue()


def test_editar_perfil_con_logo_y_datos(cliente_de, administradora):
    c = cliente_de(administradora)
    datos = {"nombre": "TresVizo", "encabezado": "TresVizo Ingeniería", "sitio_web": "https://www.tresvizo.com/", "telefono": "55 0000 1111",
             "etiqueta_telefono": "Tel.", "etiqueta_telefono_en": "Phone", "firmante": "ING. PERSONA PRUEBA", "clabe": "012 345 678 901 234 567",
             "logo": SimpleUploadedFile("l.png", png(), "image/png")}
    r = c.post(url("perfil", clave="tresvizo"), datos)
    assert r.status_code == 302
    p = Perfil.objects.get(clave="tresvizo")
    assert (p.telefono, p.firmante, p.clabe, p.logo_ruta) == ("55 0000 1111", "ING. PERSONA PRUEBA", "012345678901234567", "perfiles/tresvizo/logo.png")
    malo = {**datos, "clabe": "123", "logo": SimpleUploadedFile("l.png", png(), "image/png")}
    assert c.post(url("perfil", clave="tresvizo"), malo).status_code == 200
    malo = {**datos, "logo": SimpleUploadedFile("l.png", b"no imagen", "image/png")}
    r = c.post(url("perfil", clave="tresvizo"), malo)
    assert r.status_code == 200 and "imagen válida" in r.content.decode()
    assert "no imagen" not in open(perfiles.raiz_archivos() / p.logo_ruta, "rb").read().decode("latin1")


def test_textos_y_paleta_del_perfil(cliente_de, administradora):
    c = cliente_de(administradora)
    pagina = c.get(url("perfil_textos", clave="grupo_itt"))
    assert pagina.status_code == 200 and "Reporte fotográfico" in pagina.content.decode()
    r = c.post(url("perfil_textos", clave="grupo_itt"), {"rep_header_title": "Informe fotográfico", "es_sincerely": "Cordialmente", "rep_photo_label": "FOTOGRAFÍA"})
    assert r.status_code == 302
    p = Perfil.objects.get(clave="grupo_itt")
    assert p.textos_reporte == {"header_title": "Informe fotográfico"} and p.textos_cotizacion_es == {"sincerely": "Cordialmente"}   # lo igual al original no se guarda
    assert c.post(url("perfil_textos", clave="grupo_itt"), {"restaurar": "1"}).status_code == 302
    assert Perfil.objects.get(clave="grupo_itt").textos_reporte == {}
    assert c.post(url("perfil_textos", clave="grupo_itt"), {"rep_months": "uno|dos"}).status_code == 200
    # paleta
    base = {"primary": "#FFFF00", "accent": "#B84600", "pale": "#FFF0E3", "bar_text": "#FFFFFF", "ink": "#242424"}
    r = c.post(url("perfil_paleta", clave="grupo_itt"), base, follow=True)
    assert Perfil.objects.get(clave="grupo_itt").paleta["primary"] == "#FFFF00"
    assert "Contraste bajo" in r.content.decode()
    c.post(url("perfil_paleta", clave="grupo_itt"), {**base, "preset": "Verde"})
    assert Perfil.objects.get(clave="grupo_itt").paleta["primary"] == "#176B4D"
    assert c.post(url("perfil_paleta", clave="grupo_itt"), {**base, "primary": "rojo"}).status_code == 200
    c.post(url("perfil_paleta", clave="grupo_itt"), {"restaurar": "1"})
    assert Perfil.objects.get(clave="grupo_itt").paleta == {}


def zip_simple() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as zf:
        zf.writestr("Configuracion/settings.json", json.dumps({**DEFAULT_SETTINGS, "geospatial": {"utm_zone": 11}, "branding": {"profile": "grupo_itt", "grupo_itt": {"phone": "55 9999 8888"}}}))
        zf.writestr("Diccionarios/diccionario_personal.json", json.dumps({"words": {"es": ["alfa"], "en": []}}))
    return b.getvalue()


def test_flujo_de_importacion_en_dos_pasos(cliente_de, usuaria, administradora):
    c = cliente_de(usuaria)
    r = c.post(url("importar"), {"zip": SimpleUploadedFile("c.zip", zip_simple())})
    assert r.status_code == 302
    confirmar = r["Location"]
    pagina = c.get(confirmar).content.decode()
    assert "Preferencias" in pagina and "Diccionario personal" in pagina and "Identidad" not in pagina       # una cuenta común no ve lo de perfiles
    token = confirmar.rstrip("/").rsplit("/", 1)[1]
    # otra cuenta no puede usar el mismo token
    assert cliente_de(administradora).get(confirmar).status_code == 302
    r = c.post(confirmar, {"elemento_diccionario": "on", "elemento_preferencias": "on", "elemento_identidad": "on"})
    assert r.status_code == 302 and r["Location"] == url("inicio")
    assert diccionarios.palabras_personales(usuaria.pk)["es"] == ["alfa"]
    assert Perfil.objects.get(clave="grupo_itt").telefono == ""                    # identidad no estaba permitida
    assert c.get(confirmar).status_code == 302                                    # el archivo se borró al aplicar


def test_importacion_de_administrador_cambia_perfiles(cliente_de, administradora):
    c = cliente_de(administradora)
    confirmar = c.post(url("importar"), {"zip": SimpleUploadedFile("c.zip", zip_simple())})["Location"]
    assert "Identidad" in c.get(confirmar).content.decode()
    c.post(confirmar, {"elemento_identidad": "on", "perfil_destino": "grupo_itt"})
    assert Perfil.objects.get(clave="grupo_itt").telefono == "55 9999 8888"


def test_zip_invalido_se_rechaza_con_mensaje(cliente_de, usuaria):
    c = cliente_de(usuaria)
    r = c.post(url("importar"), {"zip": SimpleUploadedFile("c.zip", b"nada")}, follow=True)
    assert "ZIP válido" in r.content.decode()
    assert not list((perfiles.raiz_archivos() / "importaciones").glob("*.zip"))
