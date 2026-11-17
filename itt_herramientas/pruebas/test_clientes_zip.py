"""Cliente de los reportes (sugerencias, columna, migración), filtros de supervisión y descarga de varios PDF en un ZIP."""
from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import date

import pytest
from django.apps import apps as apps_reales

from itt_herramientas.comun import clientes, zips
from itt_herramientas.models import Cotizacion, Reporte
from itt_herramientas.reportes import servicio as rep

from .conftest import url
from .test_autoria_supervision import _cot, ana, beto, persona, supervisora  # noqa: F401  (fixtures)
from .test_reportes import crear_foto, datos as datos_reporte, foto

pytestmark = pytest.mark.django_db


def _rep(usuario, titulo="Avance", cliente="", fotos=()):
    r = rep.sincronizar(usuario, uuid.uuid4(), None, None, datos_reporte(list(fotos), titulo=titulo, cliente=cliente), "grupo_itt")
    return Reporte.objects.get(pk=r["servidor_id"])


def _zip(cliente, nombre, elementos):
    r = cliente.post(url(nombre), json.dumps({"elementos": elementos}), content_type="application/json")
    return r


def _descargar(c, respuesta):
    assert respuesta.status_code == 202, respuesta.content
    d = respuesta.json()
    assert d["terminada"] and d["estado"] == "completada", d
    archivo = c.get(d["resultado"]["url"])
    assert archivo.status_code == 200 and archivo["Content-Type"] == "application/zip"
    return d["resultado"], archivo, zipfile.ZipFile(io.BytesIO(b"".join(archivo.streaming_content)))


# -- Cliente -------------------------------------------------------------------------------------------------------------------

def test_el_cliente_se_guarda_en_los_datos_y_en_su_columna(ana):
    r = _rep(ana, cliente="  Agrícola   Norte ")
    assert r.datos["cliente"] == "Agrícola Norte" and r.cliente == "Agrícola Norte"
    with pytest.raises(rep.ReporteInvalido):
        rep.validar(datos_reporte([], cliente="x" * 161))


def test_un_reporte_de_antes_sin_cliente_no_cuenta_como_cambio(ana):
    r = _rep(ana)
    viejos = {k: v for k, v in r.datos.items() if k != "cliente"}
    Reporte.objects.filter(pk=r.pk).update(datos=viejos)
    res = rep.sincronizar(ana, r.id_cliente, r.pk, 1, viejos, "grupo_itt")
    assert res["resultado"] == "sin_cambios"
    res = rep.sincronizar(ana, r.id_cliente, r.pk, 5, dict(viejos, cliente=""), "grupo_itt")          # aunque la revisión sea otra: sin copia
    assert res["resultado"] == "sin_cambios" and Reporte.objects.filter(cuenta_id=ana.pk).count() == 1


def test_sugerencias_de_la_empresa_sin_repetidos_y_recientes_primero(ana, beto):
    _rep(ana, cliente="Agrícola Norte")
    _cot(ana, cliente="Constructora Sur")
    _rep(ana, cliente="AGRICOLA  NORTE")                                   # la forma más reciente gana
    _rep(beto, cliente="Cliente de Beto")
    esperadas = ["Cliente de Beto", "AGRICOLA NORTE", "Constructora Sur"]          # los documentos son del equipo: los clientes también
    assert clientes.sugerencias(ana.pk) == esperadas and clientes.sugerencias(beto.pk) == esperadas


def test_el_contexto_de_reportes_trae_las_sugerencias(cliente_de, ana, beto):
    _rep(ana, cliente="Agrícola Norte")
    _rep(beto, cliente="Otro cliente")
    assert cliente_de(ana).get(url("reportes_contexto")).json()["clientes"] == ["Otro cliente", "Agrícola Norte"]
    assert {f["cliente"] for f in cliente_de(ana).get(url("reportes_lista")).json()["reportes"]} == {"Otro cliente", "Agrícola Norte"}   # en orden de cambio


def test_la_migracion_llena_la_columna_desde_los_datos(ana):
    from importlib import import_module

    r = _rep(ana, cliente="Agrícola Norte")
    Reporte.objects.filter(pk=r.pk).update(cliente="")
    import_module("itt_herramientas.migrations.0011_reporte_cliente").llenar(apps_reales, None)
    r.refresh_from_db()
    assert r.cliente == "Agrícola Norte"


def test_el_cliente_no_cambia_el_pdf(ana, settings, monkeypatch):
    from reportlab import rl_config

    monkeypatch.setattr(rl_config, "invariant", 1)
    shas = [crear_foto(ana, n, settings) for n in (1, 2)]
    sin, con = _rep(ana, fotos=[foto(s) for s in shas]), _rep(ana, cliente="Agrícola Norte", fotos=[foto(s) for s in shas])
    assert rep.generar(sin).read_bytes() == rep.generar(con).read_bytes()


# -- ZIP de los propios ----------------------------------------------------------------------------------------------------------

def test_zip_de_reportes_genera_lo_que_falta_y_nombres_unicos(cliente_de, ana, beto, settings):
    shas = [crear_foto(ana, n, settings) for n in (1, 2, 3)]
    a, b = _rep(ana, "Avance", fotos=[foto(shas[0])]), _rep(ana, "Avance", fotos=[foto(shas[1]), foto(shas[2])])
    rep.generar(a)                                                       # uno ya tiene PDF; el otro se genera al pedir el ZIP
    ajeno = _rep(beto, "Ajeno")
    c = cliente_de(ana)
    resultado, archivo, zf = _descargar(c, _zip(c, "reportes_zip", [["reporte", str(a.pk)], ["reporte", str(b.pk)], ["reporte", str(ajeno.pk)]]))
    assert sorted(zf.namelist()) == ["Avance (2).pdf", "Avance.pdf"]
    assert all(zf.read(n).startswith(b"%PDF") for n in zf.namelist())
    assert resultado["incluidos"] == 2 and f"Reportes {date.today().isoformat()}.zip" in archivo["Content-Disposition"]
    b.refresh_from_db()
    assert b.salida_revision == b.revision                              # el propio quedó con su PDF al día, como al generarlo a mano
    # Solo quien lo pidió lo descarga.
    assert cliente_de(beto).get(resultado["url"]).status_code == 404


def test_zip_omite_lo_que_aun_no_se_puede_y_avisa(cliente_de, ana, settings):
    listo = _rep(ana, "Listo", fotos=[foto(crear_foto(ana, 1, settings))])
    faltan = _rep(ana, "Sin subir", fotos=[foto("ab" * 32)])
    c = cliente_de(ana)
    resultado, _, zf = _descargar(c, _zip(c, "reportes_zip", [["reporte", str(listo.pk)], ["reporte", str(faltan.pk)]]))
    assert zf.namelist() == ["Listo.pdf"] and resultado["omitidos"] and "Sin subir" in resultado["omitidos"][0]
    from tvplataforma.models import Tarea

    with pytest.raises(Exception):                                         # en pruebas la tarea corre en línea y su error sube
        _zip(c, "reportes_zip", [["reporte", str(faltan.pk)]])
    t = Tarea.objects.order_by("-creada").first()
    assert t.estado == "fallida" and "ningún documento" in t.error


def test_zip_con_tope_y_seleccion_invalida(cliente_de, ana):
    c = cliente_de(ana)
    r = _zip(c, "reportes_zip", [["reporte", str(uuid.uuid4())] for _ in range(zips.MAXIMO + 1)])
    assert r.status_code == 422 and "máximo por descarga es 50" in r.json()["error"]
    assert _zip(c, "reportes_zip", []).status_code == 422
    assert _zip(c, "reportes_zip", [["otra-cosa", "x"]]).status_code == 422
    assert cliente_de(ana).post(url("reportes_zip"), "no es json", content_type="application/json").status_code == 422


def test_zip_de_cotizaciones(cliente_de, ana, beto):
    a, b = _cot(ana, folio="COT-2026-0001", proyecto="Deslinde"), _cot(ana, folio="COT-2026-0002", proyecto="Topografía")
    de_beto = _cot(beto, folio="COT-2026-0003")
    en_papelera = _cot(beto, folio="COT-2026-0004")
    Cotizacion.objects.filter(pk=en_papelera.pk).update(eliminado=en_papelera.creada)
    c = cliente_de(ana)
    _, archivo, zf = _descargar(c, _zip(c, "cotizaciones_zip", [["cotizacion", str(x.pk)] for x in (a, b, de_beto, en_papelera)]))
    assert sorted(zf.namelist()) == ["COTIZACION COT-2026-0001 - Deslinde.pdf", "COTIZACION COT-2026-0002 - Topografía.pdf",
                                     "COTIZACION COT-2026-0003 - Deslinde.pdf"]                    # las del equipo sí; las de la papelera no
    assert "Cotizaciones " in archivo["Content-Disposition"]
    assert cliente_de(beto).post(url("cotizaciones_zip"), "{}", content_type="application/json").status_code == 422


def test_zip_pide_el_rol_de_cada_lista(cliente_de, crear_usuario, roles):
    solo_cot = crear_usuario("solo_cot")
    roles.conceder(solo_cot, "herramientas", "cotizaciones")
    assert _zip(cliente_de(solo_cot), "reportes_zip", [["reporte", str(uuid.uuid4())]]).status_code == 403
    assert _zip(cliente_de(solo_cot), "supervision_zip", [["reporte", str(uuid.uuid4())]]).status_code == 403


def test_limpiar_borra_los_zip_viejos(cliente_de, ana, settings):
    import os
    import time

    a = _cot(ana)
    c = cliente_de(ana)
    resultado, _, _ = _descargar(c, _zip(c, "cotizaciones_zip", [["cotizacion", str(a.pk)]]))
    archivo = next((settings.TV_HERRAMIENTAS_ARCHIVOS / "zips").glob("*.zip"))
    assert zips.limpiar() == 0
    viejo = time.time() - 2 * 86400
    os.utime(archivo, (viejo, viejo))
    assert zips.limpiar() == 1 and c.get(resultado["url"]).status_code == 404


# -- Supervisión ---------------------------------------------------------------------------------------------------------------

def test_supervision_filtra_por_cliente_estado_y_ordena(cliente_de, ana, beto, supervisora, settings):
    con_pdf = _rep(ana, "Bodega", cliente="Agrícola Norte", fotos=[foto(crear_foto(ana, 1, settings))])
    rep.generar(con_pdf)
    _rep(beto, "Almacén", cliente="agrícola norte")
    _rep(beto, "Zanja", cliente="Constructora Sur")
    _cot(ana, proyecto="Deslinde", cliente="Agrícola Norte")
    s = cliente_de(supervisora)
    contar = lambda **f: s.get(url("supervision"), f).context["pagina"].paginator.count  # noqa: E731
    assert contar(cliente="Agrícola Norte") == 3                                        # «agrícola norte» también: sin distinguir mayúsculas
    assert contar(estado="con_pdf") == 1 and contar(estado="pendiente") == 2
    assert contar(q="constructora") == 1                                                # el texto busca también en el cliente del reporte
    titulos = [f.titulo for f in s.get(url("supervision"), {"orden": "titulo"}).context["filas"]]
    assert titulos == ["Almacén", "Bodega", "Deslinde", "Zanja"]
    clientes_ = [f.cliente for f in s.get(url("supervision"), {"orden": "cliente", "tipo": "reporte"}).context["filas"]]
    assert clientes_[-1] == "Constructora Sur"
    html = s.get(url("supervision")).content.decode()
    assert 'id="sup-clientes"' in html and "Constructora Sur" in html


def test_supervision_zip_de_solo_lectura(cliente_de, ana, beto, supervisora, settings, monkeypatch):
    from itt_herramientas.drive import servicio as drive

    encolados = []
    monkeypatch.setattr(drive, "encolar", lambda *a, **k: encolados.append(a))
    r = _rep(ana, "Bodega", cliente="Agrícola Norte", fotos=[foto(crear_foto(ana, 1, settings))])
    c = _cot(beto, folio="COT-2026-0005", proyecto="Deslinde", cliente="Agrícola Norte")
    antes = Reporte.objects.filter(pk=r.pk).values().get()
    s = cliente_de(supervisora)
    resultado, _, zf = _descargar(s, _zip(s, "supervision_zip", [["reporte", str(r.pk)], ["cotizacion", str(c.pk)]]))
    assert sorted(zf.namelist()) == ["Bodega.pdf", "COTIZACION COT-2026-0005 - Deslinde.pdf"]
    assert Reporte.objects.filter(pk=r.pk).values().get() == antes and encolados == []
    assert Cotizacion.objects.get(pk=c.pk).revision == 1
    # «Todos los resultados» con los filtros de la pantalla; con más de 50, un mensaje claro.
    todos = s.post(url("supervision_zip"), json.dumps({"todos": True, "filtros": {"cliente": "agrícola norte"}}), content_type="application/json")
    assert todos.status_code == 202 and todos.json()["resultado"]["incluidos"] == 2
    for n in range(zips.MAXIMO):
        _cot(ana, folio=f"COT-2026-{100 + n}")
    tope = s.post(url("supervision_zip"), json.dumps({"todos": True, "filtros": {}}), content_type="application/json")
    assert tope.status_code == 422 and "Son 52 documentos" in tope.json()["error"]
