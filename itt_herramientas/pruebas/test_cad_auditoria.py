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
