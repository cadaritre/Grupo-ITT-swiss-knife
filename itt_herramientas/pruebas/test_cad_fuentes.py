"""CAD: fuentes SHX de la empresa (las sube quien administra; la página del plano las registra en el editor)."""
from __future__ import annotations

import json
import struct

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from itt_herramientas.cad import fuentes, servicio
from itt_herramientas.models import FuenteCad

from .conftest import url


def url_fuente(vista, nombre):
    from django.urls import reverse
    from tvplataforma.middleware import urlconf_de

    return reverse(f"herramientas:{vista}", args=[nombre], urlconf=urlconf_de("herramientas"))

pytestmark = pytest.mark.django_db


def shx_de_prueba(avance=15, nombre=b"TVPRUEBA"):
    """Una fuente SHX nuestra («AutoCAD-86 shapes 1.1», sin trazos de terceros): cada mayúscula es un trazo horizontal."""
    cabeza = nombre + b"\x00" + bytes([6, 2, 0, 0])
    formas = [(0, cabeza)] + [(c, chr(c).encode() + b"\x00" + bytes([(avance & 15) << 4, 0])) for c in range(0x41, 0x5B)]
    datos = b"AutoCAD-86 shapes 1.1\r\n\x1a" + struct.pack("<HHH", 0, 0x5A, len(formas))
    datos += b"".join(struct.pack("<HH", n, len(d)) for n, d in formas)
    return datos + b"".join(d for _, d in formas)


@pytest.fixture
def jefa(crear_usuario, roles):
    u = crear_usuario("jefa")
    u.first_name, u.last_name = "Jefa", "Mora"
    u.save()
    roles.conceder(u, "herramientas", "cad", "admin")
    return u


@pytest.fixture
def ana(crear_usuario, roles):
    u = crear_usuario("ana")
    roles.conceder(u, "herramientas", "cad")
    return u


def subir(cliente, *archivos, reemplazar=""):
    datos = {"archivos": [SimpleUploadedFile(n, c, content_type="application/octet-stream") for n, c in archivos]}
    if reemplazar:
        datos["reemplazar"] = reemplazar
    return cliente.post(url("cad_fuentes_subir"), datos)


def test_leer_reconoce_los_tres_tipos_y_rechaza_lo_demas():
    assert fuentes.leer(shx_de_prueba()) == ("shapes", 26)
    assert fuentes.leer(b"AutoCAD-86 unifont 1.0\r\n\x1a" + b"\0" * 10)[0] == "unifont"
    assert fuentes.leer(b"AutoCAD-86 bigfont 1.0\r\n\x1a" + b"\0" * 10)[0] == "bigfont"
    for malo in (b"PK\x03\x04", b"AutoCAD-86 shapes 1.1 sin fin de encabezado", b""):
        with pytest.raises(Exception):
            fuentes.leer(malo)
    assert fuentes.nombre_limpio("C:\\ACAD\\Fonts\\TXT.SHX") == "txt.shx"
    with pytest.raises(Exception):
        fuentes.nombre_limpio("txt.ttf")


def test_quien_administra_sube_varias_y_quedan_fuera_de_cad(cliente_de, jefa):
    r = subir(cliente_de(jefa), ("TXT.SHX", shx_de_prueba()), ("romans.shx", shx_de_prueba(9)))
    assert r.status_code == 302
    assert sorted(FuenteCad.objects.values_list("nombre", flat=True)) == ["romans.shx", "txt.shx"]
    f = FuenteCad.objects.get(nombre="txt.shx")
    assert f.tipo == "shapes" and f.glifos == 26 and f.subida_por_nombre == "Jefa Mora"
    ruta = fuentes.ruta(f)
    assert ruta.is_file() and ruta.parent == servicio.raiz() / "fuentes" and "cad" not in ruta.relative_to(servicio.raiz()).parts
    html = cliente_de(jefa).get(url("cad_fuentes")).content.decode()
    assert "txt.shx" in html and "romans.shx" in html and "Fuente de trazos" in html and "Reemplazar" in html


def test_rechazos_claros(cliente_de, jefa, monkeypatch):
    cl = cliente_de(jefa)
    subir(cl, ("nada.shx", b"no es una fuente"))
    assert not FuenteCad.objects.exists()
    html = cl.get(url("cad_fuentes")).content.decode()
    assert "no es una fuente SHX" in html
    monkeypatch.setattr(fuentes, "MAX_BYTES", 100)
    subir(cl, ("grande.shx", shx_de_prueba()))
    assert not FuenteCad.objects.filter(nombre="grande.shx").exists()
    monkeypatch.setattr(fuentes, "MAX_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(fuentes, "MAX_TOTAL", len(shx_de_prueba()) + 10)
    subir(cl, ("a.shx", shx_de_prueba()))
    subir(cl, ("b.shx", shx_de_prueba()))
    assert list(FuenteCad.objects.values_list("nombre", flat=True)) == ["a.shx"]


def test_reemplazar_y_eliminar(cliente_de, jefa):
    cl = cliente_de(jefa)
    subir(cl, ("txt.shx", shx_de_prueba()))
    vieja = fuentes.ruta(FuenteCad.objects.get())
    subir(cl, ("otra-cosa.shx", shx_de_prueba(9)), reemplazar="txt.shx")
    f = FuenteCad.objects.get()
    assert f.nombre == "txt.shx" and fuentes.ruta(f) != vieja and not vieja.exists() and fuentes.ruta(f).is_file()
    cl.post(url_fuente("cad_fuente_eliminar", "txt.shx"))
    assert not FuenteCad.objects.exists() and not fuentes.ruta(f).exists()


def test_solo_quien_administra(cliente_de, ana):
    assert cliente_de(ana).get(url("cad_fuentes")).status_code in (302, 403)
    assert subir(cliente_de(ana), ("txt.shx", shx_de_prueba())).status_code in (302, 403)
    assert not FuenteCad.objects.exists()


def test_la_pagina_del_plano_las_trae_y_se_sirven_con_cache(cliente_de, jefa, ana, crear_usuario):
    subir(cliente_de(jefa), ("txt.shx", shx_de_prueba()))
    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    html = cliente_de(ana).get(url("cad_plano", pk=p.pk)).content.decode()
    cfg = json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    [f] = cfg["fuentes"]
    assert f["nombre"] == "txt.shx" and "?v=" in f["url"]
    cl = cliente_de(ana)
    r = cl.get(f["url"])
    assert r.status_code == 200 and b"".join(r.streaming_content) == shx_de_prueba()
    assert "private" in r["Cache-Control"] and "immutable" in r["Cache-Control"]
    assert cl.get(f["url"], HTTP_IF_NONE_MATCH=r["ETag"]).status_code == 304
    sin_cad = crear_usuario("teo")
    assert cliente_de(sin_cad).get(f["url"]).status_code in (302, 403, 404)   # sin CAD no se sirven: son de la empresa


def test_el_editor_informa_glifos_y_rechazos(cliente_de, jefa, ana):
    subir(cliente_de(jefa), ("txt.shx", shx_de_prueba()))
    cl = cliente_de(ana)
    cl.post(url_fuente("cad_fuente_informe", "txt.shx"), {"ok": False, "error": "font_invalid"}, content_type="application/json")
    assert FuenteCad.objects.get().error == "font_invalid"
    assert "El editor no la pudo usar" in cliente_de(jefa).get(url("cad_fuentes")).content.decode()
    cl.post(url_fuente("cad_fuente_informe", "txt.shx"), {"ok": True, "glifos": 27}, content_type="application/json")
    f = FuenteCad.objects.get()
    assert f.error == "" and f.glifos == 27
