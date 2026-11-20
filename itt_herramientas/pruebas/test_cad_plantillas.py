"""CAD · presentaciones desde plantilla (docs/cad.md, «Presentaciones desde plantilla»): «Mis plantillas» por cuenta, la estrella de la
biblioteca, «Guardar como plantilla…» en el proyecto «Plantillas», el orden por uso, la papelera, permisos de cada camino y la página."""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from itt_herramientas.cad import servicio
from itt_herramientas.models import Plano, PlantillaCad

from .conftest import url
from .test_cad import DWG, DXF

pytestmark = pytest.mark.django_db


@pytest.fixture
def ana(crear_usuario, roles):
    u = crear_usuario("ana")
    u.first_name = "Ana"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def beto(crear_usuario, roles):
    u = crear_usuario("beto")
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def sara(crear_usuario, roles):
    u = crear_usuario("sara")
    roles.conceder(u, "herramientas", "supervision")
    return u


def marcar(c, p, **cuerpo):
    return c.post(url("cad_plantilla_marcar", pk=p.pk), json.dumps(cuerpo), content_type="application/json")


def mias(c):
    r = c.get(url("cad_plantillas"))
    assert r.status_code == 200
    assert r["Cache-Control"] == "private, no-store"
    return [x["nombre"] for x in r.json()["plantillas"]]


def test_mis_plantillas_son_de_cada_cuenta_y_se_ordenan_por_uso(cliente_de, ana, beto):
    marco = servicio.crear_vacio(ana, "Marco A1", "Obra", "dwg")
    rotulo = servicio.crear_vacio(ana, "Rótulo A3", "", "dxf")
    c = cliente_de(ana)
    assert mias(c) == []
    assert marcar(c, marco, en=True).json() == {"ok": True, "plantilla": True}
    assert marcar(c, rotulo, en=True).json()["plantilla"] is True
    assert marcar(c, rotulo, en=True).status_code == 200                   # idempotente
    assert PlantillaCad.objects.filter(cuenta_id=ana.pk).count() == 2
    assert set(mias(c)) == {"Marco A1", "Rótulo A3"}
    # La usada más reciente primero.
    assert marcar(c, marco, usada=True).status_code == 200
    assert mias(c)[0] == "Marco A1"
    # Beto no ve las de Ana; su marca es suya.
    cb = cliente_de(beto)
    assert mias(cb) == []
    marcar(cb, rotulo, en=True)
    assert mias(cb) == ["Rótulo A3"]
    # «usada» de una que no es suya no la agrega.
    marcar(cb, marco, usada=True)
    assert mias(cb) == ["Rótulo A3"]
    # Quitar.
    assert marcar(c, marco, en=False).json()["plantilla"] is False
    assert mias(c) == ["Rótulo A3"]


def test_la_hoja_de_la_biblioteca_dice_cuales_son_mis_plantillas_y_su_archivo(cliente_de, ana):
    marco = servicio.crear_vacio(ana, "Marco A1", "Plantillas", "dwg")
    servicio.crear_vacio(ana, "Planta", "Obra", "dwg")
    c = cliente_de(ana)
    marcar(c, marco, en=True)
    d = c.get(url("cad_elegir"), {"q": "a", "en": "todo"}).json()
    filas = {x["nombre"]: x for x in d["planos"]}
    assert filas["Marco A1"]["ubicacion"] == "Plantillas" and filas["Planta"]["ubicacion"] == "Obra"
    assert filas["Marco A1"]["plantilla"] is True and filas["Planta"]["plantilla"] is False
    assert filas["Marco A1"]["archivo"] == url("cad_archivo", pk=marco.pk)
    assert c.get(filas["Marco A1"]["archivo"]).status_code == 200


def test_en_la_papelera_no_sale_y_vuelve_al_restaurar(cliente_de, ana):
    marco = servicio.crear_vacio(ana, "Marco", "", "dwg")
    c = cliente_de(ana)
    marcar(c, marco, en=True)
    Plano.objects.filter(pk=marco.pk).update(eliminado="2026-11-18T10:00:00Z")
    assert mias(c) == []
    Plano.objects.filter(pk=marco.pk).update(eliminado=None)
    assert mias(c) == ["Marco"]


def test_guardar_como_plantilla_crea_el_plano_en_plantillas_y_lo_marca(cliente_de, ana):
    de = servicio.crear_vacio(ana, "Planta", "Obra", "dwg")
    c = cliente_de(ana)
    r = c.post(url("cad_plantilla_guardar"), DWG, content_type="application/octet-stream", HTTP_X_TV_NOMBRE=quote("Marco ITT A1 ñ"),
               HTTP_X_TV_PLANO=str(de.pk))
    assert r.status_code == 201, r.content
    d = r.json()
    p = Plano.objects.get(pk=d["id"])
    assert p.nombre == "Marco ITT A1 ñ" and p.proyecto == "Plantillas" and p.formato == "dwg"
    assert p.origen == {"tipo": "plantilla", "id": str(de.pk)}
    assert p.versiones.get().nota == "Plantilla de presentación"
    assert d["plantilla"]["plantilla"] is True and d["plantilla"]["archivo"] == url("cad_archivo", pk=p.pk)
    assert mias(c) == ["Marco ITT A1 ñ"]
    # Un DXF también; algo que no es un plano, no.
    assert c.post(url("cad_plantilla_guardar"), DXF, content_type="application/octet-stream", HTTP_X_TV_NOMBRE="A3").status_code == 201
    assert c.post(url("cad_plantilla_guardar"), b"no es un plano", content_type="application/octet-stream").status_code == 422
    # Un X-TV-Plano que no es un id se ignora.
    r = c.post(url("cad_plantilla_guardar"), DWG, content_type="application/octet-stream", HTTP_X_TV_PLANO="../../x")
    assert r.status_code == 201 and Plano.objects.get(pk=r.json()["id"]).origen == {"tipo": "plantilla"}


def test_el_tope_de_plantillas_se_dice_claro(cliente_de, ana, monkeypatch):
    monkeypatch.setattr(servicio, "PLANTILLAS_MAX", 2)
    a, b, x = (servicio.crear_vacio(ana, n, "", "dwg") for n in ("A", "B", "X"))
    c = cliente_de(ana)
    marcar(c, a, en=True); marcar(c, b, en=True)
    r = marcar(c, x, en=True)
    assert r.status_code == 422 and "2 plantillas" in r.json()["error"]
    antes = Plano.objects.count()
    r = c.post(url("cad_plantilla_guardar"), DWG, content_type="application/octet-stream")
    assert r.status_code == 422 and Plano.objects.count() == antes      # nada a medias: ni plano ni archivo


def test_permisos(cliente_de, ana, sara, crear_usuario):
    marco = servicio.crear_vacio(ana, "Marco", "", "dwg")
    cs = cliente_de(sara)
    # Supervisión (solo lectura) ve y marca su lista, pero no guarda plantillas en la biblioteca.
    assert cs.get(url("cad_plantillas")).status_code == 200
    assert marcar(cs, marco, en=True).status_code == 200
    assert cs.post(url("cad_plantilla_guardar"), DWG, content_type="application/octet-stream").status_code == 403
    # Sin CAD: nada.
    nadie = cliente_de(crear_usuario("nadie"))
    assert nadie.get(url("cad_plantillas")).status_code in (403, 404)
    assert marcar(nadie, marco, en=True).status_code in (403, 404)
    assert nadie.post(url("cad_plantilla_guardar"), DWG, content_type="application/octet-stream").status_code in (403, 404)
    # GET no marca; un plano que no existe, 404.
    c = cliente_de(ana)
    assert c.get(url("cad_plantilla_marcar", pk=marco.pk)).status_code == 405
    assert c.post(url("cad_plantilla_marcar", pk="11111111-1111-1111-1111-111111111111"), "{}", content_type="application/json").status_code == 404


def test_al_eliminar_la_cuenta_se_va_su_lista_y_los_planos_se_quedan(ana):
    marco = servicio.crear_vacio(ana, "Marco", "", "dwg")
    servicio.marcar_plantilla(ana.pk, marco, True)
    servicio.al_eliminar_cuenta(ana.pk)
    assert not PlantillaCad.objects.filter(cuenta_id=ana.pk).exists()
    assert Plano.objects.filter(pk=marco.pk).exists()


def test_la_pagina_lleva_la_hoja_solo_a_quien_puede_editar_la_vigente(cliente_de, ana, sara, settings):
    marco = servicio.crear_vacio(ana, "Marco", "", "dwg")
    c = cliente_de(ana)
    html = c.get(url("cad_plano", pk=marco.pk)).content.decode()
    config = json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    if config.get("pestanas") is None and "cad-editor" not in html:
        pytest.skip("sin el editor instalado en esta máquina")
    assert config["plantillas"]["mias"] == url("cad_plantillas")
    assert config["plantillas"]["guardar"] == url("cad_plantilla_guardar")
    if 'id="cad-editor"' in html:
        assert "plantillas.js" in html
    # Las dos hojas no viajan en cada página (Datos móviles): llegan aparte, con su huella, y se guardan un año.
    assert 'id="cad-plantilla"' not in html and "De esta computadora" not in html
    hojas = config["plantillas"]["hojas"]
    assert hojas.startswith(url("cad_plantillas_hojas") + "?v=")
    r = c.get(hojas)
    assert r.status_code == 200 and r["Cache-Control"] == "private, max-age=31536000, immutable"
    h = r.content.decode()
    assert 'id="cad-plantilla"' in h and 'id="cad-plantilla-guardar"' in h
    assert "Mis plantillas" in h and "De la biblioteca" in h and "De esta computadora" in h
    assert c.get(hojas, HTTP_IF_NONE_MATCH=r["ETag"]).status_code == 304
    assert c.get(url("cad_plantillas_hojas"))["Cache-Control"] == "private, no-cache"
    # Solo lectura (supervisión) y una versión anterior: sin plantillas.
    hs = cliente_de(sara).get(url("cad_plano", pk=marco.pk)).content.decode()
    cs = json.loads(hs.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    assert cs["plantillas"] is None and 'id="cad-plantilla"' not in hs
    hv = c.get(url("cad_plano", pk=marco.pk) + "?version=1").content.decode()
    cv = json.loads(hv.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    assert cv["plantillas"] is None
