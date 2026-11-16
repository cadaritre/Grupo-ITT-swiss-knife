"""CAD: los hallazgos de la auditoría independiente del 15-11-2026, cada uno con su prueba (falla con el defecto, pasa con el arreglo)."""
from __future__ import annotations

import uuid

import pytest

from itt_herramientas.cad import servicio
from itt_herramientas.models import Plano, VersionPlano

from .conftest import url
from .test_cad import DWG

pytestmark = pytest.mark.django_db
SA, SB = "sesion-de-ana-0001", "sesion-de-beto-002"


@pytest.fixture
def ana(crear_usuario, roles):
    u = crear_usuario("ana1")
    u.first_name = "Ana"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def beto(crear_usuario, roles):
    u = crear_usuario("beto1")
    u.first_name = "Beto"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


def tmp(datos=DWG):
    t = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    t.write_bytes(datos)
    return t


def guardar(cl, p, revision, sesion=SA):
    return cl.post(url("cad_guardar", pk=p.pk), DWG, content_type="application/octet-stream", HTTP_X_TV_SESION=sesion, HTTP_X_TV_REVISION=str(revision))


def bloqueo(cl, p, accion, sesion=SA, **extra):
    return cl.post(url("cad_bloqueo", pk=p.pk), {"accion": accion, "sesion": sesion, **extra}, content_type="application/json")


def con_versiones(usuario, n, nombre="Predio"):
    p = servicio.crear_vacio(usuario, nombre, "", "dwg")
    for _ in range(n - 1):
        servicio.guardar(p.pk, usuario, SA, Plano.objects.get(pk=p.pk).revision, tmp())
    return p


def tope(settings, bytes_):
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"biblioteca_bytes": bytes_}}


# -- A-2: restaurar una versión vieja con la biblioteca llena --------------------------------------------------------------------

def test_a2_restaurar_con_la_biblioteca_llena_no_pierde_la_version(cliente_de, ana, settings, django_capture_on_commit_callbacks):
    p = con_versiones(ana, 8)
    tope(settings, servicio.uso() + 10)                    # no cabe una versión más sin recortar
    with django_capture_on_commit_callbacks(execute=True):
        r = cliente_de(ana).post(url("cad_restaurar_version", pk=p.pk, numero=1), {"sesion": SA}, content_type="application/json")
    assert r.status_code == 200, r.content
    p.refresh_from_db()
    assert p.revision == 9
    v9 = VersionPlano.objects.get(plano=p, numero=9)
    assert servicio.archivo_disponible(v9) and v9.nota == "Restaurada de la versión 1"
    # la restaurada se conservó; se recortó otra vieja, con su archivo, y ningún renglón quedó sin archivo
    assert VersionPlano.objects.filter(plano=p, numero=1).exists()
    for v in VersionPlano.objects.filter(plano=p):
        assert servicio.archivo_disponible(v), v.numero
    assert not list(servicio.carpeta_temporal().glob("*.tmp"))


def test_a2_una_transaccion_deshecha_no_deja_renglones_sin_archivo(ana, settings, monkeypatch):
    p = con_versiones(ana, 8)
    tope(settings, servicio.uso() + 10)
    monkeypatch.setattr(servicio, "_nueva_version", lambda *a, **k: (_ for _ in ()).throw(OSError("disco")))
    with pytest.raises(OSError):
        servicio.restaurar_version(p.pk, 2, ana, SA)
    assert VersionPlano.objects.filter(plano=p).count() == 8
    for v in VersionPlano.objects.filter(plano=p):
        assert servicio.archivo_disponible(v), v.numero
    assert not list(servicio.carpeta_temporal().glob("*.tmp"))     # B-12: el temporal no se queda


# -- M-2: un guardado rechazado no recorta nada; los borradores nunca recortan ---------------------------------------------------

def test_m2_guardado_rechazado_no_recorta_versiones_ajenas(cliente_de, ana, beto, settings):
    a = con_versiones(ana, 8, "De Ana")
    b = servicio.crear_vacio(beto, "De Beto", "", "dwg")
    assert bloqueo(cliente_de(beto), b, "tomar", SB).json()["mio"]
    tope(settings, servicio.uso() + 10)
    r = guardar(cliente_de(ana), b, 1, SA)                   # Ana no tiene el bloqueo de «De Beto»
    assert r.status_code == 409
    assert VersionPlano.objects.filter(plano=a).count() == 8


def test_m2_un_borrador_que_no_cabe_se_niega_sin_recortar(cliente_de, ana, settings):
    a = con_versiones(ana, 8)
    tope(settings, servicio.uso() + 10)
    r = cliente_de(ana).post(url("cad_borrador", pk=a.pk), DWG, content_type="application/octet-stream", HTTP_X_TV_REVISION="8")
    assert r.status_code == 422 and "límite" in r.json()["error"]
    assert VersionPlano.objects.filter(plano=a).count() == 8


def test_m2_el_espacio_se_calcula_de_uno_en_uno():
    from django.db import connection, transaction

    with transaction.atomic():
        servicio.asegurar_espacio(0)
        with connection.cursor() as c:
            c.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()")
            assert c.fetchone()[0] == 1


# -- M-5, B-3, B-11, M-1: el motor nativo --------------------------------------------------------------------------------------

def _motor(tmp_path, settings, cuerpo):
    import stat
    import sys
    import textwrap

    ruta = tmp_path / "OpenCADStudio"
    ruta.write_text("#!" + sys.executable + "\n" + textwrap.dedent(cuerpo))
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
    settings.HERRAMIENTAS_CAD_NATIVO = str(ruta)


def test_m5_muchos_avisos_del_motor_no_lo_bloquean(tmp_path, settings, monkeypatch, caplog):
    from itt_herramientas.cad import nativo

    _motor(tmp_path, settings, '''
        import json, resource, sys
        print(json.dumps({"ok": True, "ready": True, "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0]}), flush=True)
        for linea in sys.stdin:
            r = json.loads(linea)
            if r["op"] == "open":
                sys.stderr.write("AVISO entidad PREDIO DE JUAN PEREZ\\n" * 20000)      # ~700 kB, más que una tubería (64 kB)
                sys.stderr.flush()
                print(json.dumps({"ok": True, "state": {"document_id": 1}}), flush=True)
            else:
                open(r["path"], "wb").write(b"%PDF-1.3")
                print(json.dumps({"ok": True, "result": {"pages": 1}}), flush=True)
    ''')
    monkeypatch.setattr(nativo, "TIEMPO_ABRIR", 8)
    entrada = tmp_path / "a.dwg"
    entrada.write_bytes(DWG)
    r = nativo.imprimir(entrada, tmp_path / "s.pdf", nativo.PAPEL_POR_OMISION, "landscape", "ajustar", "modelo")
    assert r["paginas"] == 1 and (tmp_path / "s.pdf").read_bytes().startswith(b"%PDF")


def test_m1_el_motor_corre_con_tope_de_archivo_y_entorno_minimo(tmp_path, settings, monkeypatch):
    from itt_herramientas.cad import nativo

    monkeypatch.setenv("TV_SECRETO_DE_PRUEBA", "no-debe-llegar")
    _motor(tmp_path, settings, '''
        import json, os, resource
        print(json.dumps({"ok": True, "ready": True, "version": json.dumps([resource.getrlimit(resource.RLIMIT_FSIZE)[0],
              resource.getrlimit(resource.RLIMIT_CORE)[0], "TV_SECRETO_DE_PRUEBA" in os.environ])}), flush=True)
    ''')
    with nativo.Sesion() as s:
        fsize, core, secreto = __import__("json").loads(s.version)
    assert fsize == nativo.TOPE_ARCHIVO and core == 0 and not secreto


def test_b3_un_motor_que_no_arranca_no_deja_su_carpeta(tmp_path, settings, monkeypatch):
    import glob
    import tempfile

    from itt_herramientas.cad import nativo

    _motor(tmp_path, settings, '''
        import sys
        sys.exit(3)
    ''')
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    with pytest.raises(servicio.ErrorUsuario):
        nativo.Sesion()
    assert not glob.glob(str(tmp_path / "ocs-*"))


def test_b3_b11_un_motor_que_cierra_su_salida_da_un_error_claro(tmp_path, settings, caplog):
    from itt_herramientas.cad import nativo

    _motor(tmp_path, settings, '''
        import json, os, sys, time
        print(json.dumps({"ok": True, "ready": True}), flush=True)
        sys.stderr.write("panico al leer PREDIO DE JUAN PEREZ\\nlinea 2 con mas datos del plano\\n"); sys.stderr.flush()
        os.close(1)
        time.sleep(30)
    ''')
    entrada = tmp_path / "a.dwg"
    entrada.write_bytes(DWG)
    with pytest.raises(servicio.ErrorUsuario, match="se detuvo de forma inesperada"):
        nativo.imprimir(entrada, tmp_path / "s.pdf", nativo.PAPEL_POR_OMISION, "landscape", "ajustar", "modelo")
    registro = caplog.text
    assert "linea 2" not in registro          # del motor solo la primera línea


# -- B-1: entradas mal formadas ---------------------------------------------------------------------------------------------------

@pytest.fixture
def jefa(crear_usuario, roles):
    u = crear_usuario("jefa1")
    roles.conceder(u, "herramientas", "admin")
    return u


@pytest.fixture
def sara(crear_usuario, roles):
    u = crear_usuario("sara1")
    roles.conceder(u, "herramientas", "supervision")
    return u


SHX = b"AutoCAD-86 shapes 1.0\r\n\x1a" + b"\x00" * 64
INFORME = "/cad/fuentes/txt.shx/informe/"


def test_b1_entradas_mal_formadas_no_dan_500(cliente_de, ana, jefa):
    import json

    from itt_herramientas.cad import fuentes

    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    cl = cliente_de(ana)
    cl.raise_request_exception = False
    res = {
        "lista ?autor=²": cl.get(url("cad") + "?autor=²").status_code,
        "plano ?version=²": cl.get(url("cad_plano", pk=p.pk) + "?version=²").status_code,
        "archivo ?version=²": cl.get(url("cad_archivo", pk=p.pk) + "?version=²").status_code,
        "archivo ?version=enorme": cl.get(url("cad_archivo", pk=p.pk) + "?version=99999999999999999999").status_code,
        "guardar ²": cl.post(url("cad_guardar", pk=p.pk), DWG, content_type="application/octet-stream", HTTP_X_TV_SESION=SA, HTTP_X_TV_REVISION="²").status_code,
        "borrador enorme": cl.post(url("cad_borrador", pk=p.pk), DWG, content_type="application/octet-stream", HTTP_X_TV_REVISION="99999999999").status_code,
        "imprimir ²": cl.post(url("cad_imprimir", pk=p.pk), {"version": "²"}).status_code,
    }
    fuentes.guardar("txt.shx", SHX, jefa)
    for nombre, cuerpo in (("informe []", "[]"), ("informe glifos enorme", json.dumps({"ok": True, "glifos": 2 ** 40})),
                           ("informe NUL", json.dumps({"ok": False, "codigo": "a\x00b"}))):
        res[nombre] = cl.post(INFORME, cuerpo, content_type="application/json").status_code
    assert 500 not in res.values(), res      # (imprimir responde 503 aquí: no hay motor nativo en las pruebas)
    assert res["lista ?autor=²"] == 200 and res["guardar ²"] == 400 and res["borrador enorme"] == 400


# -- B-2: el informe de fuentes es de quien edita, sin texto libre -----------------------------------------------------------

def test_b2_supervision_no_escribe_el_catalogo_de_fuentes(cliente_de, sara, ana, jefa):
    import json

    from itt_herramientas.cad import fuentes
    from itt_herramientas.models import FuenteCad

    fuentes.guardar("txt.shx", SHX, jefa)
    r = cliente_de(sara).post(INFORME, json.dumps({"ok": False, "error": "Texto puesto por supervision"}), content_type="application/json")
    assert r.status_code == 403 and FuenteCad.objects.get(nombre="txt.shx").error == ""
    cliente_de(ana).post(INFORME, json.dumps({"ok": False, "error": "Texto libre", "codigo": "otro"}), content_type="application/json")
    assert FuenteCad.objects.get(nombre="txt.shx").error == fuentes.MOTIVOS["rechazada"]
    cliente_de(ana).post(INFORME, json.dumps({"ok": True, "glifos": 7}), content_type="application/json")
    f = FuenteCad.objects.get(nombre="txt.shx")
    assert f.glifos == 7 and f.error == ""


# -- M-6: el borrador de recuperación pendiente no se pisa --------------------------------------------------------------------

def _borrador(cl, p, sesion="", previo=""):
    extra = {"HTTP_X_TV_SESION": sesion} if sesion else {}
    if previo:
        extra["HTTP_X_TV_BORRADOR_PREVIO"] = previo
    return cl.post(url("cad_borrador", pk=p.pk), DWG, content_type="application/octet-stream", HTTP_X_TV_REVISION="1", **extra)


def test_m6_el_borrador_de_otra_pagina_no_se_pisa(cliente_de, ana):
    from itt_herramientas.models import BorradorPlano

    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    cl = cliente_de(ana)
    assert _borrador(cl, p, SA).status_code == 200                     # la página de ayer dejó cambios sin guardar
    assert _borrador(cl, p, SA).status_code == 200                     # la misma página lo va reemplazando
    previo = BorradorPlano.objects.get(plano=p).actualizado
    r = _borrador(cl, p, "sesion-de-hoy-00001")                         # la página de hoy, sin haber decidido qué hacer con él
    assert r.status_code == 409 and r.json()["motivo"] == "borrador_pendiente"
    assert BorradorPlano.objects.get(plano=p).actualizado == previo
    # al recuperarlo (o elegir guardarlo como copia) la página lo vio: ya puede reemplazarlo
    assert _borrador(cl, p, "sesion-de-hoy-00001", previo.isoformat()).status_code == 200
    assert BorradorPlano.objects.get(plano=p).sesion == "sesion-de-hoy-00001"


# -- M-7: el latido no retoma un bloqueo caducado si mientras tanto hubo otra versión ------------------------------------------

def test_m7_el_latido_tras_un_guardado_ajeno_dice_que_hubo_cambio(cliente_de, ana, beto):
    from datetime import timedelta

    from django.utils import timezone

    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    ca, cb = cliente_de(ana), cliente_de(beto)
    assert bloqueo(ca, p, "tomar", SA).json()["mio"]
    Plano.objects.filter(pk=p.pk).update(bloqueo_latido=timezone.now() - timedelta(seconds=200))   # la laptop de Ana durmió
    assert bloqueo(cb, p, "tomar", SB).json()["mio"]
    assert guardar(cb, p, 1, SB).status_code == 200
    bloqueo(cb, p, "soltar", SB)
    d = bloqueo(ca, p, "latido", SA, revision=1).json()
    assert d["perdido"] and d["motivo"] == "cambio" and d["revision"] == 2 and not d["mio"] and d["quien"] == "Beto"
    # sin cambios de por medio, el latido sí retoma su bloqueo caducado
    Plano.objects.filter(pk=p.pk).update(bloqueo_latido=timezone.now() - timedelta(seconds=200))
    assert bloqueo(ca, p, "latido", SA, revision=2).json()["mio"]


# -- B-9: textos --------------------------------------------------------------------------------------------------------------

def test_b9_duplicar_un_plano_sin_archivo_lo_dice(ana):
    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    servicio.ruta_version(servicio.version_vigente(p)).unlink()
    with pytest.raises(servicio.ErrorUsuario, match="no está en el servidor"):
        servicio.duplicar(p, ana)


def test_b9_la_pagina_de_fuentes_no_nombra_marcas_y_pide_confirmar(cliente_de, jefa):
    from itt_herramientas.cad import fuentes

    fuentes.guardar("txt.shx", SHX, jefa)
    html = cliente_de(jefa).get(url("cad_fuentes")).content.decode()
    assert "AutoCAD" not in html and "SHX" not in html
    assert "¿Eliminar la fuente «txt.shx»?" in html


def test_b9_la_hoja_de_versiones_dice_el_limite_configurado(cliente_de, ana, settings):
    tope(settings, 20 * 1024 ** 3)
    settings.TV_HERRAMIENTAS_LIMITES["cad"]["versiones"] = 12
    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    html = cliente_de(ana).get(url("cad_plano", pk=p.pk)).content.decode()
    assert "Se conservan las 12 más recientes" in html and f"al menos {servicio.VERSIONES_MINIMAS}" in html


# -- B-4: lo que está en el disco sin renglón ---------------------------------------------------------------------------------

def test_b4_reconciliar_cuenta_los_huerfanos_y_solo_los_borra_si_se_pide(ana):
    import os
    import time

    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    huerfano = servicio.raiz() / "cad" / "planos" / str(uuid.uuid4())
    huerfano.mkdir(parents=True)
    (huerfano / "v1.dwg").write_bytes(b"x" * 5_000_000)
    sobrante = servicio.carpeta(p) / "v9.dwg"
    sobrante.write_bytes(b"x" * 3_000_000)
    reciente = servicio.carpeta(p) / "v10.dwg"                   # un guardado en curso: todavía sin renglón, pero no es huérfano
    reciente.write_bytes(b"x")
    viejo = time.time() - 2 * 3600
    for f in (huerfano / "v1.dwg", sobrante):
        os.utime(f, (viejo, viejo))
    r = servicio.reconciliar()
    assert r["huerfanos"] == 2 and r["huerfanos_bytes"] == 8_000_000 and r["huerfanos_borrados"] == 0
    assert huerfano.is_dir() and sobrante.is_file()              # sin pedirlo no se borra: puede ser la única copia
    servicio.limpiar()
    assert huerfano.is_dir()
    r = servicio.reconciliar(borrar_huerfanos=True)
    assert r["huerfanos_borrados"] == 2 and not huerfano.exists() and not sobrante.exists() and reciente.is_file()
    assert servicio.archivo_disponible(servicio.version_vigente(p))


def test_b5_el_tope_deja_lugar_a_salidas_y_temporales():
    assert servicio.LIMITES["biblioteca_bytes"] <= 18 * 1024 ** 3


def test_b10_la_lista_de_autores_es_una_consulta(cliente_de, ana, beto, django_assert_max_num_queries):
    from itt_herramientas.cad import vistas

    for i in range(3):
        servicio.crear_vacio(ana, f"A{i}", "", "dwg")
        servicio.crear_vacio(beto, f"B{i}", "", "dwg")
    with django_assert_max_num_queries(1):
        autores = vistas._autores()
    assert [a["nombre"] for a in autores] == ["Ana", "Beto"]
