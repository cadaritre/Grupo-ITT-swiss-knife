"""CAD con datos móviles (docs/cad.md, «Datos móviles»): lo que el servidor hace para que un plano que está en el teléfono casi no gaste datos.

- `archivo`: ETag (SHA-256 de la versión) y 304 con `If-None-Match`, con la misma sesión y el mismo rol que bajarlo;
- subidas (guardar, copia, borrador) con `Content-Encoding: gzip`, con el límite sobre lo descomprimido;
- `estado?ligero=1` sin la lista de versiones; latido cada 60 s y bloqueo de 150 s;
- la huella de la página sin conexión en la página del plano; el panel de Civil aparte (huella, 304); sw.js con ETag y 304;
- los planos son una sola biblioteca para los dos perfiles corporativos: la copia del teléfono es de la cuenta, no del perfil.
"""
from __future__ import annotations

import gzip
import hashlib
import json

import pytest

from itt_herramientas.cad import guardado, servicio, tactil
from itt_herramientas.models import BorradorPlano, VersionPlano

from .conftest import url
from .test_cad import DWG, DXF, SESION_A, bloqueo, dibujante, otro, plano, supervisora  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db


def _etag(p):
    return f'"{servicio.version_vigente(p).sha256}"'


# -- «¿Mi copia sigue siendo la vigente?» -----------------------------------------------------------------------------------------

def test_archivo_lleva_su_etag_y_responde_304_sin_cuerpo(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    r = cl.get(url("cad_archivo", pk=plano.pk))
    assert r.status_code == 200 and r["ETag"] == _etag(plano) and r["X-TV-Revision"] == "1"
    assert r["ETag"] == '"%s"' % hashlib.sha256(r.getvalue()).hexdigest()              # el teléfono lo calcula de su copia
    r = cl.get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=_etag(plano))
    assert r.status_code == 304 and r.content == b"" and r["ETag"] == _etag(plano) and r["X-TV-Revision"] == "1"
    assert "no-store" in r["Cache-Control"]
    r = cl.get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=f'W/{_etag(plano)}, "otro"')
    assert r.status_code == 304
    # Otra versión: la copia vieja ya no coincide y el plano llega entero (una vez).
    viejo = _etag(plano)
    bloqueo(cl, plano, "tomar")
    cl.post(url("cad_guardar", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_SESION=SESION_A, HTTP_X_TV_REVISION="1")
    plano.refresh_from_db()
    r = cl.get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=viejo)
    assert r.status_code == 200 and r.getvalue() == DXF and r["X-TV-Revision"] == "2" and r["ETag"] == _etag(plano)


def test_descargar_y_el_borrador_nunca_responden_304(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    r = cl.get(url("cad_archivo", pk=plano.pk), {"descargar": "1"}, HTTP_IF_NONE_MATCH=_etag(plano))
    assert r.status_code == 200 and r.getvalue() == DWG
    cl.post(url("cad_borrador", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_REVISION="1")
    r = cl.get(url("cad_archivo", pk=plano.pk), {"borrador": "1"}, HTTP_IF_NONE_MATCH='"%s"' % hashlib.sha256(DXF).hexdigest())
    assert r.status_code == 200 and r.getvalue() == DXF and "ETag" not in r


def test_una_version_restaurada_con_el_mismo_archivo_es_304_con_su_numero_nuevo(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    original = _etag(plano)
    bloqueo(cl, plano, "tomar")
    cl.post(url("cad_guardar", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_SESION=SESION_A, HTTP_X_TV_REVISION="1")
    r = cl.post(url("cad_restaurar_version", pk=plano.pk, numero=1), {"sesion": SESION_A}, content_type="application/json")
    assert r.status_code == 200 and r.json()["revision"] == 3
    r = cl.get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=original)
    assert r.status_code == 304 and r["X-TV-Revision"] == "3"                            # no se baja: solo cambia el número


def test_la_pregunta_pide_la_misma_sesion_y_el_mismo_rol(client, cliente_de, crear_usuario, roles, dibujante, supervisora, plano):
    r = client.get(url("cad_archivo", pk=plano.pk), HTTP_HOST="tools.tresvizo.com", HTTP_IF_NONE_MATCH=_etag(plano))
    assert r.status_code in (302, 401, 403) and r.status_code != 304
    sin_cad = crear_usuario("rita")
    roles.conceder(sin_cad, "herramientas", "reportes")
    r = cliente_de(sin_cad).get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=_etag(plano))
    assert r.status_code in (403, 404)
    assert cliente_de(supervisora).get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=_etag(plano)).status_code == 304   # ver basta
    plano.eliminado = plano.actualizado
    plano.save(update_fields=["eliminado"])
    assert cliente_de(dibujante).get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=_etag(plano)).status_code == 404


def test_los_planos_son_una_biblioteca_para_los_dos_perfiles(cliente_de, dibujante, roles, plano, con_editor):
    """Desde 1.2.17 cotizaciones, reportes y croquis son de un perfil; los planos de CAD no (una sola biblioteca de la empresa). La copia
    «Disponible sin conexión» es de la cuenta (tv-cad-campo) y la pregunta de versión responde igual en cualquiera de sus perfiles."""
    from itt_herramientas.comun import perfiles

    roles.conceder(dibujante, "herramientas", "cambiar_perfil")
    cl = cliente_de(dibujante)
    en_itt = cl.get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=_etag(plano))
    perfiles.cambiar_perfil(dibujante, "tresvizo")
    en_tresvizo = cl.get(url("cad_archivo", pk=plano.pk), HTTP_IF_NONE_MATCH=_etag(plano))
    assert en_itt.status_code == en_tresvizo.status_code == 304
    html = cl.get(url("cad_plano", pk=plano.pk)).content.decode()
    assert '"cuenta": %d' % dibujante.pk in html                                            # las copias del teléfono son de esta cuenta


# -- Subir comprimido ---------------------------------------------------------------------------------------------------------------

def _gz(datos: bytes) -> bytes:
    return gzip.compress(datos, 6)


def test_guardar_copia_y_borrador_aceptan_gzip_y_guardan_el_plano_tal_cual(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    grande = DXF + b"999\n" + b"comentario de relleno para que se note la compresion\n" * 2000 + b"  0\nEOF\n"
    assert len(_gz(grande)) < len(grande) / 5
    r = cl.post(url("cad_borrador", pk=plano.pk), _gz(grande), content_type="application/octet-stream", HTTP_X_TV_REVISION="1",
                HTTP_CONTENT_ENCODING="gzip")
    assert r.status_code == 200
    b = BorradorPlano.objects.get(plano=plano)
    assert (servicio.raiz() / b.ruta).read_bytes() == grande and b.bytes == len(grande)
    bloqueo(cl, plano, "tomar")
    r = cl.post(url("cad_guardar", pk=plano.pk), _gz(grande), content_type="application/octet-stream", HTTP_X_TV_SESION=SESION_A,
                HTTP_X_TV_REVISION="1", HTTP_CONTENT_ENCODING="gzip")
    assert r.status_code == 200 and r.json()["bytes"] == len(grande)
    v = VersionPlano.objects.get(plano=plano, numero=2)
    assert servicio.ruta_version(v).read_bytes() == grande and v.sha256 == hashlib.sha256(grande).hexdigest()
    r = cl.post(url("cad_copia", pk=plano.pk), _gz(grande), content_type="application/octet-stream", HTTP_CONTENT_ENCODING="gzip")
    assert r.status_code == 201
    from itt_herramientas.models import Plano

    copia = Plano.objects.get(pk=r.json()["id"])
    assert servicio.ruta_version(servicio.version_vigente(copia)).read_bytes() == grande


def test_un_comprimido_que_crece_de_mas_se_corta_sin_dejar_nada(cliente_de, dibujante, plano, settings):
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"bytes": 200_000}}
    cl = cliente_de(dibujante)
    bomba = _gz(DXF + b"\n" * 20_000_000)                                                   # 20 MB de saltos en ~20 KB
    assert len(bomba) < 200_000
    antes = set(servicio.carpeta_temporal().iterdir())
    r = cl.post(url("cad_borrador", pk=plano.pk), bomba, content_type="application/octet-stream", HTTP_X_TV_REVISION="1", HTTP_CONTENT_ENCODING="gzip")
    assert r.status_code == 413 and "pesa más" in r.json()["error"]
    assert set(servicio.carpeta_temporal().iterdir()) == antes and not BorradorPlano.objects.exists()


@pytest.mark.parametrize("cuerpo, cabecera, texto", [
    (b"\x1f\x8b\x08\x00no es gzip de verdad", "gzip", "dañado"),
    (None, "gzip", "incompleto"),
    (DXF, "br", "no es válida"),
])
def test_un_comprimido_danado_o_desconocido_se_rechaza(cliente_de, dibujante, plano, cuerpo, cabecera, texto):
    cuerpo = _gz(DXF)[:-40] if cuerpo is None else cuerpo
    r = cliente_de(dibujante).post(url("cad_borrador", pk=plano.pk), cuerpo, content_type="application/octet-stream", HTTP_X_TV_REVISION="1",
                                   HTTP_CONTENT_ENCODING=cabecera)
    assert r.status_code == 422 and texto in r.json()["error"]
    assert not BorradorPlano.objects.exists()


def test_recibir_cuerpo_descomprime_de_a_trozos():
    """Un gzip de varios trozos (más de 1 MB comprimido) llega entero."""
    import os

    from django.test import RequestFactory

    datos = os.urandom(1_500_000) + DXF * 50
    cuerpo = gzip.compress(datos, 1)
    assert len(cuerpo) > 1024 * 1024
    pet = RequestFactory().post("/", cuerpo, content_type="application/octet-stream", HTTP_CONTENT_ENCODING="gzip")
    ruta = servicio.recibir_cuerpo(pet, maximo=10_000_000)
    try:
        assert ruta.read_bytes() == datos
    finally:
        ruta.unlink()


# -- Lo que se pregunta mientras el plano está abierto ----------------------------------------------------------------------------

def test_estado_ligero_no_trae_la_lista_de_versiones(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    completo = cl.get(url("cad_estado", pk=plano.pk)).json()
    ligero = cl.get(url("cad_estado", pk=plano.pk), {"ligero": "1", "sesion": SESION_A}).json()
    assert "versiones" in completo and "versiones" not in ligero
    assert ligero["revision"] == 1 and ligero["bloqueo"]["libre"] is True and ligero["borrador"] is None


def test_el_latido_es_cada_60_s_y_el_bloqueo_dura_150(cliente_de, dibujante, plano, settings):
    from datetime import timedelta

    from django.utils import timezone

    from itt_herramientas.models import Plano

    for nombre in ("HERRAMIENTAS_CAD_LATIDO_S", "HERRAMIENTAS_CAD_AUTOGUARDADO_S"):
        if hasattr(settings, nombre):
            delattr(settings, nombre)
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    cfg = json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    assert cfg["latido_s"] == 60 and cfg["autoguardado_s"] == 120
    assert servicio.BLOQUEO_SEGUNDOS == 150
    bloqueo(cliente_de(dibujante), plano, "tomar")
    Plano.objects.filter(pk=plano.pk).update(bloqueo_latido=timezone.now() - timedelta(seconds=140))
    plano.refresh_from_db()
    assert servicio.bloqueo_activo(plano)                                                    # dos latidos perdidos no lo sueltan
    Plano.objects.filter(pk=plano.pk).update(bloqueo_latido=timezone.now() - timedelta(seconds=151))
    plano.refresh_from_db()
    assert not servicio.bloqueo_activo(plano)


# -- La página del plano: su peso y lo que el teléfono guarda aparte -------------------------------------------------------------

@pytest.fixture
def con_editor(tmp_path, settings):
    from itt_herramientas.cad import editor

    base = tmp_path / "editor" / editor.version()
    base.mkdir(parents=True)
    (base / "index.html").write_text("<!doctype html><title>x</title>")
    (base / "MANIFIESTO.json").write_text(json.dumps({"archivos": {"index.html": hashlib.sha256(b"<!doctype html><title>x</title>").hexdigest()}}))
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(tmp_path / "editor")
    settings.TV_CAD_TACTIL = True
    guardado._paquete.cache_clear()
    return base


def _config(html):
    return json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])


def test_la_huella_de_la_pagina_sin_conexion_es_estable_y_cambia_con_las_fuentes(cliente_de, dibujante, plano, con_editor):
    cl = cliente_de(dibujante)
    h1 = _config(cl.get(url("cad_plano", pk=plano.pk)).content.decode())["tactil"]["pagina"]
    h2 = _config(cl.get(url("cad_plano", pk=plano.pk)).content.decode())["tactil"]["pagina"]
    assert h1 == h2 and len(h1) == 16
    from itt_herramientas.models import FuenteCad

    from itt_herramientas.cad import fuentes

    datos = b"AutoCAD-86 shapes 1.0\r\n\x1a" + b"\x00" * 64
    (fuentes.carpeta() / f"{hashlib.sha256(datos).hexdigest()}.shx").write_bytes(datos)
    FuenteCad.objects.create(nombre="romans.shx", tipo="shapes", sha256=hashlib.sha256(datos).hexdigest(), bytes=len(datos))
    h3 = _config(cl.get(url("cad_plano", pk=plano.pk)).content.decode())["tactil"]["pagina"]
    assert h3 != h1                                                                          # una fuente nueva: se vuelve a guardar


def test_la_pagina_del_plano_ya_no_trae_las_hojas_de_civil(cliente_de, dibujante, plano, con_editor):
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content
    texto = html.decode()
    assert 'id="civil-panel"' in texto and "herramientas/cad/civil-panel.js" in texto and 'id="civil-barra"' not in texto
    assert len(gzip.compress(html, 6)) < 20 * 1024, len(gzip.compress(html, 6))         # antes, ~29 KB comprimida


def test_el_panel_de_civil_lleva_huella_se_guarda_un_ano_y_responde_304(cliente_de, dibujante, supervisora, plano, con_editor):
    cl = cliente_de(dibujante)
    html = cl.get(url("cad_plano", pk=plano.pk)).content.decode()
    direccion = html.split('id="civil-panel" hidden data-url="')[1].split('"')[0].replace("&amp;", "&")
    huella = direccion.split("?v=")[1]
    r = cl.get(direccion)
    assert r.status_code == 200 and 'id="civil-barra"' in r.content.decode() and "Cuadro de construcción" in r.content.decode()
    assert r["ETag"] == f'"{huella}"' and "immutable" in r["Cache-Control"] and "private" in r["Cache-Control"]
    assert "civil-config" not in r.content.decode()                                          # nada de un plano ni de una cuenta
    assert cl.get(url("cad_panel_civil"), HTTP_IF_NONE_MATCH=f'"{huella}"').status_code == 304
    assert "no-cache" in cl.get(url("cad_panel_civil"), {"v": "vieja"})["Cache-Control"]
    assert cliente_de(supervisora).get(direccion).status_code in (403, 404)                # Civil es de quien edita


def test_sw_js_lleva_etag_y_responde_304(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    r = cl.get("/sw.js")
    assert r.status_code == 200 and r["ETag"] and "no-cache" in r["Cache-Control"]
    r2 = cl.get("/sw.js", HTTP_IF_NONE_MATCH=r["ETag"])
    assert r2.status_code == 304 and r2.content == b"" and r2["Service-Worker-Allowed"] == "/"


def test_detras_de_caddy_la_etag_comprimida_tambien_vale(cliente_de, dibujante):
    """Caddy comprime sw.js y puede devolver su ETag como «"…-gzip"» o débil: el navegador la manda así y sigue siendo un 304."""
    cl = cliente_de(dibujante)
    etiqueta = cl.get("/sw.js")["ETag"]
    for forma in (etiqueta[:-1] + '-gzip"', etiqueta[:-1] + '-zstd"', f"W/{etiqueta}"):
        assert cl.get("/sw.js", HTTP_IF_NONE_MATCH=forma).status_code == 304, forma
    assert cl.get("/sw.js", HTTP_IF_NONE_MATCH='"otra-gzip"').status_code == 200


def test_el_service_worker_importa_la_cola_solo_con_background_sync():
    """WebKit baja entero sw.js y todo lo que importa al abrir cada página (sin preguntar si cambió): los scripts de la cola solo los usa
    la subida en segundo plano, que WebKit no tiene, así que se importan solo donde existe."""
    from itt_herramientas.offline import sw

    c = sw.contenido()
    assert 'if (self.registration && "sync" in self.registration) importScripts(' in c
    assert c.count("importScripts(") == 1 and "self.ITT.subidor.procesar" in c.split('addEventListener("sync"')[1]


def test_sw_js_se_sirve_sin_comentarios_y_sigue_siendo_valido(settings, tmp_path):
    """Sin las líneas de comentario (WebKit lo baja entero en cada página) y JavaScript válido, con y sin la parte de CAD."""
    import shutil
    import subprocess

    from itt_herramientas.offline import sw

    for tactil_activo in (False, True):
        settings.TV_CAD_TACTIL = tactil_activo
        c = sw.contenido()
        assert not any(l.strip().startswith(("//", "/*")) for l in c.split("\n")), "quedó un comentario"
        assert f'const VERSION = "{sw.version_servida()}"' in c
        if shutil.which("node"):
            archivo = tmp_path / f"sw-{tactil_activo}.js"
            archivo.write_text(c)
            subprocess.run(["node", "--check", str(archivo)], check=True)
    assert sw.sin_comentarios("a();\n// x\n  /* y\n  z */\nb(); // queda\n/* w */\nc();") == "a();\nb(); // queda\nc();"
