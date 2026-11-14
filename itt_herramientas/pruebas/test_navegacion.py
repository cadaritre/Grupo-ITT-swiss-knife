"""Navegación como datos (contrato tvplataforma 1.5, §12): menú, acciones de la paleta, búsqueda y lateral de las páginas de campo."""
from __future__ import annotations

import uuid

import pytest

from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.modulo import MODULO
from itt_herramientas.models import Cotizacion

from .conftest import url

pytestmark = pytest.mark.django_db


def _cotizacion(usuario, folio, proyecto, cliente):
    from itt_herramientas.comun import perfiles

    p = perfiles.perfil_activo(usuario)
    d = perfiles.nueva_cotizacion(p).to_dict()
    d.update(quote_number=folio, project_title=proyecto, client_name=cliente, items=[], images=[])
    return cot.sincronizar(usuario, uuid.uuid4(), None, None, d, p.clave)


def test_el_menu_y_las_acciones_resuelven_y_no_usan_atajos_reservados():
    for e in (*MODULO.menu, *MODULO.acciones):
        assert e.url.startswith("/") or url(e.url.split(":", 1)[1])
        assert e.atajo not in ("?", "/", "[", "g i", "g c")
    assert {e.clave for e in MODULO.menu} >= {"inicio", "cotizaciones", "reportes", "croquis", "convertidor", "triangulacion", "ajustes"}


def test_la_busqueda_solo_devuelve_lo_propio_y_de_sus_herramientas(cliente_de, usuaria, crear_usuario, roles):
    _cotizacion(usuaria, "COT-2026-0101", "Deslinde Rancho Viejo", "Agrícola Norte")
    otra = crear_usuario("otra")
    roles.conceder(otra, "herramientas", "cotizaciones")
    _cotizacion(otra, "COT-2026-0909", "Deslinde ajeno", "Otro cliente")
    c = cliente_de(usuaria)
    r = c.get(url("buscar"), {"q": "deslinde"}).json()["resultados"]
    assert [x["titulo"] for x in r] == ["COT-2026-0101 · Deslinde Rancho Viejo"]
    pk = Cotizacion.objects.get(folio="COT-2026-0101").pk
    assert r[0]["url"] == f"/cotizaciones/#/s/{pk}" and "Agrícola Norte" in r[0]["subtitulo"]
    assert c.get(url("buscar"), {"q": "d"}).json()["resultados"] == []                        # mínimo dos letras
    assert c.get(url("buscar"), {"q": "rancho"}).status_code == 200


def test_la_busqueda_pide_un_rol_de_documentos(cliente_de, convertidora):
    assert cliente_de(convertidora).get(url("buscar"), {"q": "algo"}).status_code in (403, 404)


def test_las_paginas_de_campo_traen_el_lateral_completo_y_la_sesion_dice_cuales_ver(cliente_de, usuaria):
    c = cliente_de(usuaria)
    html = c.get(url("reportes")).content.decode()
    for e in MODULO.menu:
        assert f'data-itt-nav="{e.clave}"' in html                                                 # el HTML es el mismo para todas las cuentas
    assert 'data-itt-nav="reportes" aria-current="page"' in html
    sesion = c.get(url("sesion")).json()
    assert sesion["nav"] == ["inicio", "cotizaciones", "capturar", "ajustes"] and sesion["tema"] == "claro"


def test_las_paginas_de_ajustes_marcan_la_seccion(cliente_de, usuaria):
    html = cliente_de(usuaria).get(url("diccionario")).content.decode()
    assert 'aria-label="Ajustes"' in html                                                        # subnavegación de Ajustes
