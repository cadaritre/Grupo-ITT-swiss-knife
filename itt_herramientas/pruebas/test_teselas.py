"""Servicio de teselas: caché en disco y política de uso de OpenStreetMap (agente, vigencia, revalidación, sin precarga, volumen)."""
from __future__ import annotations

import time
import urllib.error
from email.message import Message
from unittest import mock

import pytest

from itt_herramientas.comun import teselas as servicio
from itt_herramientas.dominio import teselas as dominio

from .conftest import url

pytestmark = pytest.mark.django_db
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 50


def respuesta(cabeceras=None, cuerpo=PNG):
    m = Message()
    for k, v in (cabeceras or {}).items():
        m[k] = v
    r = mock.MagicMock()
    r.__enter__ = lambda s: r
    r.__exit__ = lambda *a: None
    r.read = lambda n=-1: cuerpo
    r.headers = m
    return r


class Origen:
    """urlopen falso que registra cada petición."""

    def __init__(self, *respuestas):
        self.respuestas, self.peticiones = list(respuestas), []

    def __call__(self, peticion, timeout=None):
        self.peticiones.append(peticion)
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(r, Exception):
            raise r
        return r


def test_primera_vez_baja_con_agente_identificable_y_despues_sale_de_cache(cliente_de, reportera):
    origen = Origen(respuesta({"Cache-Control": "max-age=3600"}))
    with mock.patch("urllib.request.urlopen", origen):
        c = cliente_de(reportera)
        r = c.get(url("teselas", capa="osm", z=3, x=2, y=1))
        assert r.status_code == 200 and r.content == PNG and r["Content-Type"] == "image/png"
        assert 3000 < int(r["Cache-Control"].split("max-age=")[1]) <= 3600                # al navegador se le da la vigencia que quedaba
        assert c.get(url("teselas", capa="osm", z=3, x=2, y=1)).status_code == 200
    assert len(origen.peticiones) == 1                                                  # la segunda salió de la caché
    p = origen.peticiones[0]
    assert p.full_url == "https://tile.openstreetmap.org/3/2/1.png"
    assert "(+https://tools.tresvizo.com)" in p.get_header("User-agent")                # agente con la URL de contacto


def test_solo_se_pide_la_tesela_visible_nunca_rangos(cliente_de, reportera):
    origen = Origen(respuesta())
    with mock.patch("urllib.request.urlopen", origen):
        cliente_de(reportera).get(url("teselas", capa="osm", z=10, x=200, y=400))
    assert [p.full_url for p in origen.peticiones] == ["https://tile.openstreetmap.org/10/200/400.png"]


def test_vigencia_de_las_cabeceras(monkeypatch):
    ahora = 1_000_000.0
    assert dominio.vigencia({"Cache-Control": "public, max-age=600"}, ahora) == ahora + 600
    assert dominio.vigencia({"Cache-Control": "max-age=999999999"}, ahora) == ahora + dominio.VIGENCIA_MAXIMA
    assert dominio.vigencia({"Expires": "Thu, 01 Jan 1970 00:20:00 GMT"}, 0) == 1200
    assert dominio.vigencia({}, ahora) == ahora + dominio.VIGENCIA_POR_OMISION          # sin cabeceras: 7 días
    assert dominio.vigencia({"Cache-Control": "no-cache"}, ahora) == ahora              # se revalida siempre


def test_tesela_vencida_se_revalida_y_un_304_no_baja_la_imagen(cliente_de, reportera):
    vencida = respuesta({"Cache-Control": "max-age=1", "ETag": '"abc"', "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"})
    c = cliente_de(reportera)
    with mock.patch("urllib.request.urlopen", Origen(vencida)):
        c.get(url("teselas", capa="osm", z=4, x=3, y=3))
    time.sleep(1.1)
    cabeceras = Message()
    cabeceras["Cache-Control"] = "max-age=7200"
    origen = Origen(urllib.error.HTTPError("u", 304, "Not Modified", cabeceras, None))
    with mock.patch("urllib.request.urlopen", origen):
        r = c.get(url("teselas", capa="osm", z=4, x=3, y=3))
    assert r.status_code == 200 and r.content == PNG
    p = origen.peticiones[0]
    assert p.get_header("If-none-match") == '"abc"' and p.get_header("If-modified-since") == "Wed, 01 Jan 2025 00:00:00 GMT"
    with mock.patch("urllib.request.urlopen", Origen(OSError("no debe llamarse"))) as sin_red:
        assert c.get(url("teselas", capa="osm", z=4, x=3, y=3)).status_code == 200      # la revalidación renovó la vigencia


def test_sin_red_se_sirve_la_copia_vieja_pero_sin_copia_es_404(cliente_de, reportera):
    c = cliente_de(reportera)
    with mock.patch("urllib.request.urlopen", Origen(respuesta({"Cache-Control": "max-age=1"}))):
        c.get(url("teselas", capa="topo", z=5, x=1, y=1))
    time.sleep(1.1)
    with mock.patch("urllib.request.urlopen", Origen(OSError("sin red"))):
        assert c.get(url("teselas", capa="topo", z=5, x=1, y=1)).status_code == 200      # vencida pero es la que hay
        assert c.get(url("teselas", capa="topo", z=5, x=1, y=2)).status_code == 404


def test_rechaza_lo_que_no_es_una_imagen(cliente_de, reportera):
    with mock.patch("urllib.request.urlopen", Origen(respuesta(cuerpo=b"<html>error</html>"))):
        assert cliente_de(reportera).get(url("teselas", capa="osm", z=3, x=0, y=0)).status_code == 404


def test_limites_de_rango_y_capa(cliente_de, reportera):
    c = cliente_de(reportera)
    with mock.patch("urllib.request.urlopen", Origen(OSError("no debe llamarse"))) as origen:
        assert c.get(url("teselas", capa="osm", z=3, x=9, y=1)).status_code == 404
        assert c.get(url("teselas", capa="osm", z=25, x=0, y=0)).status_code == 404
        assert c.get(url("teselas", capa="topo", z=18, x=0, y=0)).status_code == 404       # OpenTopoMap llega a 17
        assert c.get(url("teselas", capa="satelite", z=3, x=0, y=0)).status_code == 404


def test_tope_de_teselas_nuevas_por_hora_no_cuenta_la_cache(cliente_de, reportera, monkeypatch):
    monkeypatch.setattr(servicio, "DESCARGAS_NUEVAS_POR_HORA", 3)
    c = cliente_de(reportera)
    with mock.patch("urllib.request.urlopen", Origen(respuesta({"Cache-Control": "max-age=3600"}))):
        for i in range(3):
            assert c.get(url("teselas", capa="osm", z=8, x=i, y=0)).status_code == 200
        assert c.get(url("teselas", capa="osm", z=8, x=0, y=0)).status_code == 200         # de la caché: no cuenta
        r = c.get(url("teselas", capa="osm", z=8, x=5, y=0))
    assert r.status_code == 429 and r["Retry-After"]


def test_pide_sesion_y_rol(cliente_de, convertidora):
    from django.test import Client

    assert Client(HTTP_HOST="tools.tresvizo.com").get(url("teselas", capa="osm", z=0, x=0, y=0)).status_code in (302, 401, 403, 404)
    assert cliente_de(convertidora).get(url("teselas", capa="osm", z=0, x=0, y=0)).status_code == 403
