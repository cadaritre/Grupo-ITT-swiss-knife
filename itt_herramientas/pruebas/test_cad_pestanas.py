"""CAD · varios planos en una ventana del editor (docs/cad.md, «Varios planos en una ventana»): el bloqueo es de cada pestaña, la hoja
«Abrir otro plano», los datos de una pestaña nueva, guardar en la biblioteca un dibujo hecho en el editor y duplicar, eliminar y
renombrar sin salir de la página. Permisos de cada camino."""
from __future__ import annotations

import json
from urllib.parse import quote

import pytest

from itt_herramientas.cad import servicio
from itt_herramientas.models import Plano, VersionPlano

from .conftest import url
from .test_cad import DWG, DXF, bloqueo, guardar

pytestmark = pytest.mark.django_db
PESTANA_1 = "pestana-uno-0000001"
PESTANA_2 = "pestana-dos-0000002"


@pytest.fixture
def ana(crear_usuario, roles):
    u = crear_usuario("ana")
    u.first_name, u.last_name = "Ana", "López"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def beto(crear_usuario, roles):
    u = crear_usuario("beto")
    u.first_name = "Beto"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def sara(crear_usuario, roles):
    u = crear_usuario("sara")
    roles.conceder(u, "herramientas", "supervision")
    return u


def test_cada_pestana_tiene_su_bloqueo_y_guarda_su_version(cliente_de, ana, beto):
    a = servicio.crear_vacio(ana, "Predio Norte", "Norte", "dwg")
    b = servicio.crear_vacio(ana, "Predio Sur", "Sur", "dxf")
    c = cliente_de(ana)
    assert bloqueo(c, a, "tomar", PESTANA_1).json()["mio"]
    assert bloqueo(c, b, "tomar", PESTANA_2).json()["mio"]            # la misma cuenta, otra pestaña, otro plano: también es suyo
    # Guardar todo: una versión de cada plano, cada una con el bloqueo de su pestaña.
    assert guardar(c, a, DWG, sesion=PESTANA_1).status_code == 200
    assert guardar(c, b, DXF, sesion=PESTANA_2).status_code == 200
    assert VersionPlano.objects.filter(plano=a).count() == 2 and VersionPlano.objects.filter(plano=b).count() == 2
    # La sesión de una pestaña no sirve para el plano de la otra: el bloqueo de B es de PESTANA_2.
    b.refresh_from_db()
    r = guardar(c, b, DXF, sesion=PESTANA_1)
    assert r.status_code == 409
    # Soltar una pestaña no toca la otra.
    bloqueo(c, a, "soltar", PESTANA_1)
    a.refresh_from_db(); b.refresh_from_db()
    assert not servicio.bloqueo_activo(a) and servicio.bloqueo_activo(b)
    # Con el plano A tomado por otra persona, Guardar todo guarda B y A responde 409 con quién lo edita.
    cb = cliente_de(beto)
    assert bloqueo(cb, a, "tomar", "sesion-de-beto-0001").json()["mio"]
    a.refresh_from_db(); b.refresh_from_db()
    ra = guardar(c, a, DWG, sesion=PESTANA_1)
    rb = guardar(c, b, DXF, sesion=PESTANA_2)
    assert ra.status_code == 409 and "Beto" in ra.json()["error"]
    assert rb.status_code == 200


def test_el_mismo_plano_en_dos_pestanas_es_otra_ventana(cliente_de, ana):
    a = servicio.crear_vacio(ana, "Predio", "", "dwg")
    c = cliente_de(ana)
    assert bloqueo(c, a, "tomar", PESTANA_1).json()["mio"]
    d = bloqueo(c, a, "tomar", PESTANA_2).json()
    assert not d["mio"] and d["otra_ventana"]


def test_datos_de_una_pestana(cliente_de, ana, sara):
    a = servicio.crear_vacio(ana, "Predio Norte", "Norte", "dwg")
    r = cliente_de(ana).get(url("cad_pestana", pk=a.pk))
    assert r.status_code == 200 and r["Cache-Control"] == "private, no-store"
    d = r.json()
    assert d["plano"]["id"] == str(a.pk) and d["plano"]["nombre"] == "Predio Norte" and d["plano"]["puede_editar"]
    assert d["plano"]["urls"]["guardar"] == url("cad_guardar", pk=a.pk)
    assert [v["numero"] for v in d["versiones"]] == [1]
    # Supervisión: la abre en solo lectura.
    d = cliente_de(sara).get(url("cad_pestana", pk=a.pk)).json()
    assert not d["plano"]["puede_editar"] and d["plano"]["rol_lectura"]
    # Un plano en la papelera no se abre.
    servicio.eliminar(a, ana)
    assert cliente_de(ana).get(url("cad_pestana", pk=a.pk)).status_code == 404


def test_la_pagina_del_plano_trae_las_pestanas_solo_en_la_vigente(cliente_de, ana):
    a = servicio.crear_vacio(ana, "Predio", "", "dwg")
    c = cliente_de(ana)

    def config(r):
        texto = r.content.decode()
        inicio = texto.index('id="cad-config"')
        return json.loads(texto[texto.index(">", inicio) + 1:texto.index("</script>", inicio)])

    cfg = config(c.get(url("cad_plano", pk=a.pk)))
    assert cfg["pestanas"]["max"] >= 2 and cfg["pestanas"]["elegir"] == url("cad_elegir")
    assert [v["numero"] for v in cfg["versiones"]] == [1]
    assert config(c.get(url("cad_plano", pk=a.pk) + "?version=1"))["pestanas"] is None


def test_abrir_otro_plano_busca_en_la_biblioteca(cliente_de, ana, beto, sara):
    servicio.crear_vacio(ana, "Levantamiento Norte", "Agrícola Norte", "dwg")
    sur = servicio.crear_vacio(beto, "Levantamiento Sur", "Constructora Sur", "dxf")
    servicio.crear_vacio(ana, "Otro", "Agrícola Norte", "dwg")
    c = cliente_de(ana)
    d = c.get(url("cad_elegir")).json()
    assert d["total"] == 3 and {p["nombre"] for p in d["planos"]} == {"Levantamiento Norte", "Levantamiento Sur", "Otro"}
    assert set(d["proyectos"]) >= {"Agrícola Norte", "Constructora Sur"}
    d = c.get(url("cad_elegir"), {"q": "sur"}).json()
    assert [p["nombre"] for p in d["planos"]] == ["Levantamiento Sur"]
    d = c.get(url("cad_elegir"), {"proyecto": "Agrícola Norte"}).json()
    assert d["total"] == 2
    # Quién lo está editando
    assert bloqueo(cliente_de(beto), sur, "tomar", "sesion-de-beto-0001").json()["mio"]
    d = c.get(url("cad_elegir"), {"q": "sur"}).json()
    assert d["planos"][0]["en_edicion_por"] == "Beto"
    # Solo lectura también ve la biblioteca; sin rol de CAD, no.
    assert cliente_de(sara).get(url("cad_elegir")).status_code == 200


def test_sin_rol_de_cad_no_hay_pestanas(cliente_de, crear_usuario, ana):
    a = servicio.crear_vacio(ana, "Predio", "", "dwg")
    nadie = crear_usuario("nadie")
    c = cliente_de(nadie)
    assert c.get(url("cad_elegir")).status_code in (403, 404)
    assert c.get(url("cad_pestana", pk=a.pk)).status_code in (403, 404)
    assert c.post(url("cad_desde_editor"), DWG, content_type="application/octet-stream").status_code in (403, 404)


def test_un_dibujo_del_editor_queda_en_la_biblioteca(cliente_de, ana, sara):
    c = cliente_de(ana)
    r = c.post(url("cad_desde_editor"), DXF, content_type="application/octet-stream", HTTP_X_TV_NOMBRE=quote("Croquis de obra ñ"))
    assert r.status_code == 201
    p = Plano.objects.get(pk=r.json()["id"])
    assert p.nombre == "Croquis de obra ñ" and p.formato == "dxf" and p.revision == 1
    assert VersionPlano.objects.get(plano=p).nota == "Hecho en el editor"
    # Basura: no es un plano
    assert c.post(url("cad_desde_editor"), b"no es un plano", content_type="application/octet-stream").status_code == 422
    # Solo lectura no crea planos
    assert cliente_de(sara).post(url("cad_desde_editor"), DXF, content_type="application/octet-stream").status_code == 403


def test_duplicar_eliminar_y_renombrar_sin_salir_de_la_pagina(cliente_de, ana, beto):
    a = servicio.crear_vacio(ana, "Predio", "Norte", "dwg")
    c = cliente_de(ana)
    r = c.post(url("cad_duplicar", pk=a.pk), HTTP_ACCEPT="application/json")
    assert r.status_code == 201 and Plano.objects.filter(pk=r.json()["id"]).exists()
    r = c.post(url("cad_renombrar", pk=a.pk), {"nombre": "Predio 2", "proyecto": "Sur"}, HTTP_ACCEPT="application/json")
    assert r.json() == {"nombre": "Predio 2", "proyecto": "Sur"}
    # Eliminar lo de otra persona: no (solo quien administra); lo suyo, sí, con el mensaje para la página.
    r = cliente_de(beto).post(url("cad_eliminar", pk=a.pk), HTTP_ACCEPT="application/json")
    assert r.status_code == 403 and r.json()["error"]
    r = c.post(url("cad_eliminar", pk=a.pk), HTTP_ACCEPT="application/json")
    assert r.status_code == 200 and "papelera" in r.json()["mensaje"]
    a.refresh_from_db()
    assert a.eliminado
    # Sin JSON, como siempre: redirige.
    b = servicio.crear_vacio(ana, "Otro", "", "dwg")
    assert c.post(url("cad_duplicar", pk=b.pk)).status_code == 302
