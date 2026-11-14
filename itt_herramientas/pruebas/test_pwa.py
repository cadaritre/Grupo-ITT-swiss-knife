from __future__ import annotations

import base64
import hashlib
import io
import json
from urllib.parse import urlparse

import pytest
from PIL import Image

from itt_herramientas.models import Captura
from itt_herramientas.offline import sw

from .conftest import HOST, url

pytestmark = pytest.mark.django_db


def jpeg(color="#336699", tam=(64, 48)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", tam, color).save(b, "JPEG", quality=85)
    return b.getvalue()


def subir(cliente, datos: bytes, meta: dict, sha=True) -> "HttpResponse":
    """Sube `datos` con el protocolo tus, como lo hace la cola del teléfono."""
    cabeza = {"HTTP_TUS_RESUMABLE": "1.0.0"}
    campos = {"destino": "herramientas.captura", "name": "foto.jpg", "type": "image/jpeg", **meta}
    if sha:
        campos.setdefault("sha256", hashlib.sha256(datos).hexdigest())
    codificado = ",".join(f"{k} {base64.b64encode(str(v).encode()).decode()}" for k, v in campos.items())
    r = cliente.post("/subidas/", HTTP_UPLOAD_LENGTH=str(len(datos)), HTTP_UPLOAD_METADATA=codificado, **cabeza)
    if r.status_code != 201:
        return r
    ruta = urlparse(r["Location"]).path
    return cliente.generic("PATCH", ruta, datos, content_type="application/offset+octet-stream", HTTP_UPLOAD_OFFSET="0", **cabeza)


def test_manifiesto_en_la_raiz_y_sin_sesion(client):
    r = client.get(url("manifiesto"), HTTP_HOST=HOST)
    assert r.status_code == 200 and r["Content-Type"].startswith("application/manifest+json")
    m = r.json()
    assert (m["start_url"], m["scope"], m["display"], m["name"]) == ("/", "/", "standalone", "Herramientas Grupo ITT")
    assert {i["sizes"] for i in m["icons"]} >= {"192x192", "512x512"} and any(i["purpose"] == "maskable" for i in m["icons"])
    assert url("manifiesto") == "/manifest.webmanifest"


def test_service_worker_en_la_raiz_con_alcance_total(client):
    r = client.get(url("sw"), HTTP_HOST=HOST)
    assert url("sw") == "/sw.js"
    assert r.status_code == 200 and r["Service-Worker-Allowed"] == "/" and "no-cache" in r["Cache-Control"]
    texto = r.content.decode()
    assert sw.version() in texto and "/capturar/" in texto and "/subidas/" in texto and "__" not in texto.replace("__proto__", "")
    for activo in sw.ACTIVOS:
        assert (sw.ESTATICOS / activo).is_file(), activo


def test_el_service_worker_precarga_todo_lo_estatico_que_cargan_las_paginas_sin_conexion(cliente_de, propietario):
    """UX-ITT-02: la primera apertura sin señal sale con estilos. Todo `/static/` que traen las páginas de campo está en la precarga."""
    import re

    precarga = set(json.loads(re.search(r"const ACTIVOS = (\[.*?\]);", sw.contenido()).group(1)))
    assert {"/static/tvplataforma/tokens.css", "/static/tvplataforma/base.css", "/static/tvplataforma/componentes.css", "/static/tvplataforma/img/marca.png"} <= precarga
    cl = cliente_de(propietario)
    for nombre in ("capturar", "instalar", "cotizaciones", "reportes", "croquis"):
        html = cl.get(url(nombre)).content.decode()
        for recurso in re.findall(r"""(?:href|src)="(/static/[^"?#]+)""", html):
            assert recurso in precarga, f"{nombre}: {recurso} no se precarga"


def test_la_version_del_service_worker_cambia_si_cambia_el_sistema_de_diseno(monkeypatch, tmp_path):
    antes = sw.version()
    sw.version.cache_clear()
    copia = tmp_path / "base.css"
    copia.write_text(sw.ruta_plataforma("base.css").read_text() + "\n/* cambio */")
    original = sw.ruta_plataforma
    monkeypatch.setattr(sw, "ruta_plataforma", lambda r: copia if r == "base.css" else original(r))
    assert sw.version() != antes
    sw.version.cache_clear()


def test_la_version_del_service_worker_cambia_con_la_interfaz(monkeypatch, tmp_path):
    antes = sw.version()
    sw.version.cache_clear()
    copia = tmp_path / "herramientas"
    import shutil
    shutil.copytree(sw.ESTATICOS, copia)
    (copia / "offline" / "ui.js").write_text((copia / "offline" / "ui.js").read_text() + "\n// cambio")
    monkeypatch.setattr(sw, "ESTATICOS", copia)
    assert sw.version() != antes
    sw.version.cache_clear()


def test_paginas_publicas_sin_datos_de_cuenta(client, usuaria):
    for nombre in ("capturar", "instalar"):
        r = client.get(url(nombre), HTTP_HOST=HOST)
        assert r.status_code == 200, nombre
        html = r.content.decode()
        assert "csrfmiddlewaretoken" not in html and "csrf-token" not in html
        assert usuaria.get_username() not in html
        assert 'rel="manifest"' in html and "pendientes" in html.lower()
    guia = client.get(url("instalar"), HTTP_HOST=HOST).content.decode()
    assert "Agregar a inicio" in guia and "Instalar app" in guia and "Safari" in guia and "Chrome" in guia


def test_las_paginas_con_datos_siguen_pidiendo_sesion(client):
    assert client.get(url("inicio"), HTTP_HOST=HOST).status_code == 302
    assert client.get(url("capturas"), HTTP_HOST=HOST).status_code == 302


def test_sesion_da_cuenta_y_csrf_solo_con_sesion(client, cliente_de, usuaria, crear_usuario):
    assert client.get(url("sesion"), HTTP_HOST=HOST).status_code in (401, 302)
    r = cliente_de(usuaria).get(url("sesion"))
    assert r.status_code == 200 and r["Cache-Control"] == "no-store"
    d = r.json()
    assert d["cuenta"] == usuaria.pk and d["csrf"]
    sin_rol = crear_usuario("sin_rol")
    assert cliente_de(sin_rol).get(url("sesion")).status_code in (401, 404)


def test_captura_llega_con_metadatos_y_es_idempotente(cliente_de, usuaria):
    c = cliente_de(usuaria)
    datos = jpeg()
    r = subir(c, datos, {"tomada": "2026-11-13T10:00:00", "lat": "28.6330", "lon": "-106.0691", "precision": "12", "nota": "Esquina NE", "referencia": "rep-1", "orden": "3", "origen": "camara"})
    assert r.status_code == 204 and r["X-TV-Sha256"] == hashlib.sha256(datos).hexdigest()
    cap = Captura.objects.get()
    assert (cap.cuenta_id, cap.referencia, cap.orden, cap.lat, cap.lon, cap.nota, cap.ancho, cap.alto) == (usuaria.pk, "rep-1", 3, 28.633, -106.0691, "Esquina NE", 64, 48)
    assert not cap.sin_ubicacion and (c.defaults.get("HTTP_HOST") == HOST)
    from itt_herramientas.comun.perfiles import raiz_archivos
    assert (raiz_archivos() / cap.ruta).read_bytes() == datos and cap.ruta.endswith(f"{cap.sha256}.jpg")
    # la misma foto otra vez (se perdió la respuesta): misma fila, sin duplicar
    r = subir(c, datos, {"referencia": "rep-1"})
    assert r.status_code == 204 and Captura.objects.count() == 1
    assert c.get(url("capturas")).json()["capturas"][0]["sha256"] == cap.sha256
    assert c.get(url("capturas") + "?referencia=otra").json()["capturas"] == []


def test_captura_sin_ubicacion_y_metadatos_invalidos(cliente_de, usuaria):
    c = cliente_de(usuaria)
    assert subir(c, jpeg("#111111"), {"sin_ubicacion": "1"}).status_code == 204
    assert subir(c, jpeg("#444444"), {"lat": "28.5"}).status_code == 204                      # una sola coordenada: se guarda sin ubicación
    assert [(x.sin_ubicacion, x.lat) for x in Captura.objects.order_by("id")] == [(True, None), (True, None)]
    for malo in ({"lat": "999", "lon": "10"}, {"lat": "no", "lon": "x"}, {"desconocido": "1"}, {"orden": "mucho"}, {"nota": "x" * 1001}):
        r = subir(c, jpeg("#222222"), malo)
        assert r.status_code == 400, malo                                                       # el contrato rechaza metadatos que el destino no declara o no valida
    assert Captura.objects.count() == 2


def test_el_servidor_rechaza_lo_que_no_es_foto_o_llega_alterado(cliente_de, usuaria):
    c = cliente_de(usuaria)
    assert subir(c, b"esto no es una imagen", {}).status_code == 422
    gif = io.BytesIO(); Image.new("P", (4, 4)).save(gif, "GIF")
    assert subir(c, gif.getvalue(), {}).status_code == 422
    assert subir(c, jpeg(), {"sha256": "0" * 64}).status_code == 422
    assert not Captura.objects.exists()


def test_cuota_por_cuenta(cliente_de, usuaria, settings):
    settings.TV_HERRAMIENTAS_CUOTA = 1500
    c = cliente_de(usuaria)
    assert subir(c, jpeg("#101010", (200, 200)), {}).status_code in (204, 422)
    r = subir(c, jpeg("#202020", (400, 400)), {})
    assert r.status_code == 422 and "espacio" in r.content.decode()


def test_solo_roles_de_fotos_pueden_usar_el_destino(cliente_de, crear_usuario, roles):
    u = crear_usuario("croquista")
    roles.conceder(u, "herramientas", "croquis")
    r = subir(cliente_de(u), jpeg(), {})
    assert r.status_code == 404                                # misma respuesta que un destino que no existe
    assert not Captura.objects.exists()


def test_dos_cuentas_pueden_subir_la_misma_foto(cliente_de, usuaria, administradora, roles):
    roles.conceder(administradora, "herramientas", "reportes")
    datos = jpeg("#445566")
    assert subir(cliente_de(usuaria), datos, {}).status_code == 204
    assert subir(cliente_de(administradora), datos, {}).status_code == 204
    assert Captura.objects.count() == 2
