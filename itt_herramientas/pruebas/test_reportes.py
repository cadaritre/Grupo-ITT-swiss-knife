from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from unittest import mock

import pytest
from PIL import Image

from itt_herramientas.comun import capturas, perfiles, teselas
from itt_herramientas.models import Captura, Perfil, Reporte

from .conftest import url

pytestmark = pytest.mark.django_db


@pytest.fixture
def reportera(crear_usuario, roles):
    u = crear_usuario("rita")
    roles.conceder(u, "herramientas", "reportes")
    return u


def crear_foto(cuenta, n, settings, tam=(640, 480)):
    """Una foto ya subida (fila + archivo), como la dejaría el destino de subida."""
    sha = f"{n:064x}"
    relativa = f"capturas/{cuenta.pk}/{sha[:2]}/{sha}.jpg"
    destino = settings.TV_HERRAMIENTAS_ARCHIVOS / relativa
    destino.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", tam, (20 + n * 7 % 200, 90, 160)).save(destino, "JPEG", quality=85)
    Captura.objects.create(cuenta_id=cuenta.pk, sha256=sha, ruta=relativa, bytes=destino.stat().st_size, ancho=tam[0], alto=tam[1])
    return sha


def foto(sha, nota="", lat=None, lon=None, tomada="2026-11-13T09:30:00"):
    return {"sha256": sha, "nota": nota, "tomada": tomada, "lat": lat, "lon": lon, "precision": 5.0 if lat is not None else None}


def datos(fotos, **campos):
    base = {"titulo": "Avance de obra", "fecha": "2026-11-13", "con_croquis": False, "con_credenciales": False, "fotos": fotos}
    base.update(campos)
    return base


def post(c, nombre, cuerpo, **kw):
    return c.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


def sinc(c, idc, d, servidor=None, base=None):
    r = post(c, "reportes_sincronizar", {"id_cliente": str(idc), "servidor_id": servidor, "revision_base": base, "datos": d})
    assert r.status_code == 200, r.content
    return r.json()


def test_solo_con_el_rol(cliente_de, usuaria, reportera):
    assert cliente_de(usuaria).get(url("reportes_lista")).status_code == 403          # tiene Herramientas, pero no este rol
    assert cliente_de(reportera).get(url("reportes_lista")).status_code == 200


def test_contexto_usa_el_texto_del_perfil(cliente_de, reportera):
    r = cliente_de(reportera).get(url("reportes_contexto")).json()
    assert r["perfil"] == "grupo_itt" and r["titulo"] and r["max_fotos"] == 150


def test_sincronizacion_idempotente_conflicto_y_aislamiento(cliente_de, reportera, crear_usuario, roles):
    c = cliente_de(reportera)
    idc = uuid.uuid4()
    a = sinc(c, idc, datos([foto("a" * 64)]))
    assert sinc(c, idc, datos([foto("a" * 64)]))["resultado"] == "sin_cambios" and Reporte.objects.count() == 1
    b = sinc(c, idc, datos([foto("a" * 64, "nota")]), a["servidor_id"], a["revision"])
    assert b["resultado"] == "actualizada" and b["revision"] == 2
    r = sinc(c, idc, datos([foto("a" * 64, "del teléfono")]), a["servidor_id"], 1)
    assert r["resultado"] == "copia"
    assert Reporte.objects.get(pk=a["servidor_id"]).datos["fotos"][0]["nota"] == "nota"
    copia = Reporte.objects.get(pk=r["servidor_id"])
    assert copia.titulo.endswith("(desde el teléfono)") and copia.copia_de_id == uuid.UUID(a["servidor_id"])
    assert sinc(c, idc, datos([foto("a" * 64, "del teléfono")]), a["servidor_id"], 1)["servidor_id"] == r["servidor_id"]
    otra = crear_usuario("otra")
    roles.conceder(otra, "herramientas", "reportes")
    co = cliente_de(otra)
    assert co.get(url("reportes_detalle", pk=a["servidor_id"])).status_code == 404
    assert co.get(url("reportes_lista")).json()["reportes"] == []
    assert post(co, "reportes_pdf", {}, pk=a["servidor_id"]).status_code == 404


def test_validacion(cliente_de, reportera):
    c = cliente_de(reportera)
    malos = [datos([foto(f"{i:064x}") for i in range(151)]), datos([foto("zz")]), datos([foto("a" * 64)] * 2), datos([foto("a" * 64, lat=95.0, lon=0.0)]),
             datos([foto("a" * 64, lat=10.0)]), datos([foto("a" * 64, tomada="ayer")]), datos([foto("a" * 64, nota="x" * 1001)]), datos([], fecha="13/11/2026"), []]
    for malo in malos:
        r = post(c, "reportes_sincronizar", {"id_cliente": str(uuid.uuid4()), "datos": malo})
        assert r.status_code == 422, malo
    assert Reporte.objects.count() == 0


def test_no_genera_pdf_si_faltan_fotos_por_subir(cliente_de, reportera, settings):
    c = cliente_de(reportera)
    sha = crear_foto(reportera, 1, settings)
    a = sinc(c, uuid.uuid4(), datos([foto(sha), foto("b" * 64)]))
    r = post(c, "reportes_pdf", {}, pk=a["servidor_id"])
    assert r.status_code == 409 and r.json()["faltan"] == 1
    sin_titulo = sinc(c, uuid.uuid4(), datos([foto(sha)], titulo=""))
    assert post(c, "reportes_pdf", {}, pk=sin_titulo["servidor_id"]).status_code == 422
    vacio = sinc(c, uuid.uuid4(), datos([]))
    assert post(c, "reportes_pdf", {}, pk=vacio["servidor_id"]).status_code == 422


@pytest.mark.parametrize("clave", ["grupo_itt", "tresvizo"])
@pytest.mark.parametrize("caso", ["sin_croquis", "con_croquis_y_cedula"])
def test_pdf_de_la_web_identico_al_del_dominio(cliente_de, reportera, settings, roles, clave, caso, monkeypatch):
    """Mismas fotos y mismos textos por la web y directo por `generate_report` (el del escritorio): el mismo PDF, byte a byte."""
    from reportlab import rl_config

    from itt_herramientas.dominio import pdf_generator
    from itt_herramientas.dominio.metadata import PhotoInfo

    monkeypatch.setattr(rl_config, "invariant", 1)
    roles.conceder(reportera, "herramientas", "cambiar_perfil")
    perfiles.cambiar_perfil(reportera, clave)
    con_mapa = caso == "con_croquis_y_cedula"
    shas = [crear_foto(reportera, n, settings, (800, 600) if n % 2 else (600, 800)) for n in range(1, 6)]
    fotos = [foto(shas[0], "Frente del predio", 28.633, -106.069), foto(shas[1], "", 28.6335, -106.0685, "2026-11-13T09:41:00"), foto(shas[2], "Sin ubicación"),
             foto(shas[3], "Esquina norte " * 8, 28.634, -106.07), foto(shas[4])]
    c = cliente_de(reportera)
    a = sinc(c, uuid.uuid4(), datos(fotos, titulo="Reporte de avance: ñandú/cañería", con_croquis=con_mapa, con_credenciales=con_mapa))
    with mock.patch("itt_herramientas.dominio.maps._tile", side_effect=OSError("sin red")):
        r = post(c, "reportes_pdf", {}, pk=a["servidor_id"])
        assert r.status_code == 202, r.content
        web = c.get(url("reportes_descargar", pk=a["servidor_id"]))
        assert web.status_code == 200 and web["Content-Type"] == "application/pdf"
        assert "Reporte de avance" in web["Content-Disposition"] or "Reporte%20de%20avance" in web["Content-Disposition"]
        perfil = Perfil.objects.get(clave=clave)
        info = [PhotoInfo(path=settings.TV_HERRAMIENTAS_ARCHIVOS / Captura.objects.get(sha256=f["sha256"]).ruta, taken_at=datetime.strptime(f["tomada"], "%Y-%m-%dT%H:%M:%S"),
                          latitude=f["lat"], longitude=f["lon"], width=Captura.objects.get(sha256=f["sha256"]).ancho, height=Captura.objects.get(sha256=f["sha256"]).alto,
                          description=f["nota"]) for f in fotos]
        destino = settings.TV_HERRAMIENTAS_ARCHIVOS / "directo.pdf"
        opciones = perfiles.opciones_reporte(perfil, "Reporte de avance: ñandú/cañería", date(2026, 11, 13), info, destino, incluir_credenciales=con_mapa, include_map=con_mapa)
        with perfiles.activar(perfil):
            pdf_generator.generate_report(opciones)
    assert b"".join(web.streaming_content) == destino.read_bytes()
    assert Reporte.objects.get().salida_revision == 1


def test_descargar_solo_el_propio_y_con_pdf(cliente_de, reportera, settings):
    c = cliente_de(reportera)
    a = sinc(c, uuid.uuid4(), datos([foto(crear_foto(reportera, 1, settings))]))
    assert c.get(url("reportes_descargar", pk=a["servidor_id"])).status_code == 404         # aún sin PDF
    assert Reporte.objects.get().tarea_id is None


def test_miniatura_y_original(cliente_de, reportera, settings):
    sha = crear_foto(reportera, 3, settings, (1600, 1200))
    c = cliente_de(reportera)
    m = c.get(url("reportes_foto", sha256=sha))
    assert m.status_code == 200
    with Image.open(__import__("io").BytesIO(b"".join(m.streaming_content))) as im:
        assert max(im.size) == capturas.LADO_MINIATURA
    assert c.get(url("reportes_foto", sha256=sha) + "?original=1").status_code == 200
    assert c.get(url("reportes_foto", sha256="f" * 64)).status_code == 404
    assert c.get(url("reportes_foto", sha256="no-es-sha")).status_code == 404


def test_teselas_con_cache_y_limites(cliente_de, reportera, settings, monkeypatch):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 50
    llamadas = []
    monkeypatch.setattr(teselas.urllib.request, "urlopen", lambda *a, **k: llamadas.append(a[0].full_url) or mock.MagicMock(__enter__=lambda s: mock.MagicMock(read=lambda n=-1: png), __exit__=lambda *x: None))
    c = cliente_de(reportera)
    r = c.get(url("teselas", capa="osm", z=3, x=2, y=1))
    assert r.status_code == 200 and r.content == png and r["Content-Type"] == "image/png"
    assert c.get(url("teselas", capa="osm", z=3, x=2, y=1)).status_code == 200
    assert len(llamadas) == 1 and llamadas[0] == "https://tile.openstreetmap.org/3/2/1.png"       # la segunda salió de la caché
    assert c.get(url("teselas", capa="osm", z=3, x=9, y=1)).status_code == 404                     # fuera del mundo
    assert c.get(url("teselas", capa="osm", z=25, x=0, y=0)).status_code == 404
    assert c.get(url("teselas", capa="satelite", z=3, x=0, y=0)).status_code == 404
    monkeypatch.setattr(teselas.urllib.request, "urlopen", mock.Mock(side_effect=OSError("sin red")))
    assert c.get(url("teselas", capa="topo", z=3, x=0, y=0)).status_code == 404                    # sin red y sin caché: 404, no 500


def test_teselas_piden_sesion_y_rol(client, cliente_de, convertidora):
    from django.test import Client

    assert Client(HTTP_HOST="tools.tresvizo.com").get(url("teselas", capa="osm", z=0, x=0, y=0)).status_code in (302, 401, 403, 404)
    assert cliente_de(convertidora).get(url("teselas", capa="osm", z=0, x=0, y=0)).status_code == 403
