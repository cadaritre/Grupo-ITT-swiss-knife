"""Documentos compartidos con el equipo (16-11-2026): dos cuentas que comparten, bloqueo y toma de control, cambios sin conexión
(fusión y copia en conflicto), historial y restauración, folios únicos de la empresa (también en paralelo), migración con folios
repetidos, machotes de la empresa, papelera, roles y cuentas eliminadas."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from itt_herramientas.comun import documentos, sincronizacion
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.models import (AvisoDocumento, BorradorRecuperacion, ContadorFolio, Cotizacion, Machote, Reporte, VersionDocumento)
from itt_herramientas.reportes import servicio as rep

from .conftest import url
from .test_autoria_supervision import _cot, _datos_cot, ana, beto, persona, supervisora  # noqa: F401  (fixtures)
from .test_reportes import crear_foto, datos as datos_reporte, foto

pytestmark = pytest.mark.django_db
SESION_A = "pagina-de-ana-0001"
SESION_B = "pagina-de-beto-0001"


def post(c, nombre, cuerpo, **kw):
    return c.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


def sinc_cot(c, idc, d, servidor=None, base=None, sesion=""):
    r = post(c, "cotizaciones_sincronizar", {"id_cliente": str(idc), "servidor_id": servidor, "revision_base": base, "datos": d, "sesion": sesion})
    assert r.status_code == 200, r.content
    return r.json()


def sinc_rep(c, idc, d, servidor=None, base=None, sesion=""):
    r = post(c, "reportes_sincronizar", {"id_cliente": str(idc), "servidor_id": servidor, "revision_base": base, "datos": d, "sesion": sesion})
    assert r.status_code == 200, r.content
    return r.json()


def bloqueo(c, pk, accion, sesion, tipo="cotizaciones"):
    r = post(c, f"{tipo}_bloqueo", {"accion": accion, "sesion": sesion}, pk=pk)
    assert r.status_code == 200, r.content
    return r.json()


# -- Compartidos ----------------------------------------------------------------------------------------------------------------

def test_dos_cuentas_comparten_y_cada_renglon_dice_quien_modifico(cliente_de, ana, beto):
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Deslinde"))
    filas = cb.get(url("cotizaciones_lista")).json()["cotizaciones"]
    assert [f["id"] for f in filas] == [a["servidor_id"]] and filas[0]["autor"]["nombre"] == "Ana López"
    b = sinc_cot(cb, uuid.uuid4(), _datos_cot(beto, project_title="Deslinde (Beto)"), a["servidor_id"], a["revision"])
    assert b["resultado"] == "actualizada" and b["folio"] == a["folio"]
    fila = ca.get(url("cotizaciones_detalle", pk=a["servidor_id"])).json()
    assert fila["autor"]["nombre"] == "Ana López" and fila["modificado"]["nombre"] == "beto@grupoitt.com"
    assert fila["puede_borrar"] is True and cb.get(url("cotizaciones_detalle", pk=a["servidor_id"])).json()["puede_borrar"] is False
    # Reportes: Beto ve las fotos que subió Ana (miniatura y PDF), aunque son de su cuenta.


def test_las_fotos_de_un_reporte_del_equipo_se_ven_y_entran_al_pdf(cliente_de, ana, beto, settings):
    sha = crear_foto(ana, 3, settings)
    r = rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([foto(sha, "Fachada")], titulo="Visita"), "grupo_itt")
    cb = cliente_de(beto)
    assert cb.get(url("reportes_foto", sha256=sha)).status_code == 200
    assert cb.get(url("reportes_foto", sha256=f"{99:064x}")).status_code == 404                   # una foto que ningún reporte usa: no
    respuesta = post(cb, "reportes_pdf", {}, pk=r["servidor_id"])
    assert respuesta.status_code in (200, 202)
    assert Reporte.objects.get(pk=r["servidor_id"]).salida_revision == 1


# -- Bloqueo y toma de control ----------------------------------------------------------------------------------------------------

def test_un_editor_a_la_vez_y_tomar_el_control(cliente_de, ana, beto):
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Original"))
    pk = a["servidor_id"]
    assert bloqueo(ca, pk, "tomar", SESION_A)["mio"] is True
    otro = bloqueo(cb, pk, "tomar", SESION_B)
    assert otro["mio"] is False and otro["quien"] == "Ana López" and otro["desde"]
    assert cb.get(url("cotizaciones_lista")).json()["cotizaciones"][0]["bloqueo"]["nombre"] == "Ana López"
    # Beto toma el control: Ana se entera en su siguiente latido y recibe un aviso.
    tomado = bloqueo(cb, pk, "forzar", SESION_B)
    assert tomado["mio"] is True and tomado["tomado_de"] == "Ana López"
    perdido = bloqueo(ca, pk, "latido", SESION_A)
    assert perdido["perdido"] is True and perdido["quien"] == "beto@grupoitt.com"
    assert "tomó el control" in ca.get(url("cotizaciones_avisos")).json()["avisos"][0]["texto"]
    # Lo que Ana no había guardado llega con su sesión: queda como SU borrador de recuperación, el documento no cambia.
    r = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Cambio de Ana sin guardar"), pk, a["revision"], sesion=SESION_A)
    assert r["resultado"] == "recuperacion" and r["revision"] == a["revision"]
    assert Cotizacion.objects.get(pk=pk).proyecto == "Original"
    rec = ca.get(url("cotizaciones_detalle", pk=pk)).json()["recuperacion"]
    assert rec["datos"]["project_title"] == "Cambio de Ana sin guardar" and rec["quien"] == "beto@grupoitt.com"
    assert cb.get(url("cotizaciones_detalle", pk=pk)).json()["recuperacion"] is None                # es solo de Ana
    # Beto edita con el bloqueo; Ana no puede restaurar ni usar su recuperación mientras Beto edita.
    b = sinc_cot(cb, uuid.uuid4(), _datos_cot(beto, project_title="De Beto"), pk, a["revision"], sesion=SESION_B)
    assert b["resultado"] == "actualizada"
    assert post(ca, "cotizaciones_recuperacion", {"sesion": SESION_A}, pk=pk).status_code == 409
    # Beto suelta; Ana toma el control y usa sus cambios: revisión nueva, lo de Beto queda en el historial.
    bloqueo(cb, pk, "soltar", SESION_B)
    assert bloqueo(ca, pk, "tomar", SESION_A)["mio"] is True
    usado = post(ca, "cotizaciones_recuperacion", {"sesion": SESION_A}, pk=pk).json()
    assert usado["datos"]["project_title"] == "Cambio de Ana sin guardar" and usado["recuperacion"] is None
    assert usado["datos"]["quote_number"] == a["folio"]
    assert not BorradorRecuperacion.objects.exists()


def test_el_bloqueo_caduca_sin_latido_y_la_misma_cuenta_en_otra_ventana(cliente_de, ana):
    ca = cliente_de(ana)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana))
    bloqueo(ca, a["servidor_id"], "tomar", SESION_A)
    otra = bloqueo(ca, a["servidor_id"], "tomar", "otra-ventana-0001")
    assert otra["mio"] is False and otra["otra_ventana"] is True
    Cotizacion.objects.filter(pk=a["servidor_id"]).update(bloqueo_latido=timezone.now() - timedelta(seconds=documentos.BLOQUEO_SEGUNDOS + 1))
    assert bloqueo(ca, a["servidor_id"], "tomar", "otra-ventana-0001")["mio"] is True
    assert post(ca, "cotizaciones_bloqueo", {"accion": "tomar", "sesion": "x"}, pk=a["servidor_id"]).status_code == 422


# -- Sin conexión: fusión y copia en conflicto --------------------------------------------------------------------------------------

def test_sin_conexion_se_fusiona_si_no_choca(cliente_de, ana, beto):
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Deslinde", client_name="Agrícola"))
    # En la oficina, Beto cambia el título; en campo y sin señal, Ana cambió el cliente sobre la revisión 1.
    sinc_cot(cb, uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "project_title": "Deslinde norte"}, a["servidor_id"], 1)
    r = sinc_cot(ca, uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "project_title": "Deslinde", "client_name": "Agrícola del Norte"},
                 a["servidor_id"], 1)
    assert r["resultado"] == "fusionada" and r["revision"] == 3
    c = Cotizacion.objects.get(pk=a["servidor_id"])
    assert (c.proyecto, c.cliente) == ("Deslinde norte", "Agrícola del Norte") and Cotizacion.objects.count() == 1
    assert VersionDocumento.objects.get(documento_id=c.pk, revision=3).nota.startswith("Fusionada")


def test_sin_conexion_si_choca_queda_copia_en_conflicto_y_se_avisa(cliente_de, ana, beto):
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Deslinde"))
    sinc_cot(cb, uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "project_title": "Título de Beto"}, a["servidor_id"], 1)
    r = sinc_cot(ca, uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "project_title": "Título de Ana"}, a["servidor_id"], 1)
    assert r["resultado"] == "copia" and r["copia_de"] == a["servidor_id"]
    assert r["conflicto"].startswith(f"Copia en conflicto de {a['folio']} (Ana López, ") and r["folio"] != a["folio"]
    assert Cotizacion.objects.get(pk=a["servidor_id"]).proyecto == "Título de Beto"
    aviso = cb.get(url("cotizaciones_avisos")).json()["avisos"][0]
    assert "Ana López" in aviso["texto"] and "copia en conflicto" in aviso["texto"].lower()
    assert post(cb, "cotizaciones_avisos", {"vistos": [aviso["id"]]}).json()["avisos"] == []


def test_sin_conexion_mientras_otra_persona_edita_es_copia(cliente_de, ana, beto):
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Deslinde"))
    bloqueo(cb, a["servidor_id"], "tomar", SESION_B)
    r = sinc_cot(ca, uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "notes": "De campo"}, a["servidor_id"], 1)
    assert r["resultado"] == "copia" and Cotizacion.objects.get(pk=a["servidor_id"]).revision == 1


def test_reporte_de_campo_con_fotos_se_fusiona_con_lo_de_la_oficina(cliente_de, ana, beto):
    """Ana agrega fotos sin señal; Beto, en la oficina, corrige la nota de otra foto y el título. No se pierde nada de nadie."""
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_rep(ca, uuid.uuid4(), datos_reporte([foto("a" * 64, "Entrada")], titulo="Visita"))
    sinc_rep(cb, uuid.uuid4(), datos_reporte([foto("a" * 64, "Entrada principal")], titulo="Visita de obra"), a["servidor_id"], 1)
    r = sinc_rep(ca, uuid.uuid4(), datos_reporte([foto("a" * 64, "Entrada"), foto("b" * 64, "Bodega"), foto("c" * 64, "Patio")], titulo="Visita"),
                 a["servidor_id"], 1)
    assert r["resultado"] == "fusionada"
    d = Reporte.objects.get(pk=a["servidor_id"]).datos
    assert d["titulo"] == "Visita de obra" and [(f["sha256"][0], f["nota"]) for f in d["fotos"]] == [("a", "Entrada principal"), ("b", "Bodega"), ("c", "Patio")]
    # Los dos cambiaron la nota de la misma foto: copia en conflicto con TODAS las fotos de campo.
    sinc_rep(cb, uuid.uuid4(), {**d, "fotos": [{**d["fotos"][0], "nota": "Oficina"}, *d["fotos"][1:]]}, a["servidor_id"], r["revision"])
    choque = sinc_rep(ca, uuid.uuid4(), {**d, "fotos": [{**d["fotos"][0], "nota": "Campo"}, *d["fotos"][1:], foto("e" * 64, "Nueva")]},
                      a["servidor_id"], r["revision"])
    assert choque["resultado"] == "copia"
    assert [f["sha256"][0] for f in Reporte.objects.get(pk=choque["servidor_id"]).datos["fotos"]] == ["a", "b", "c", "e"]


def test_editado_sin_conexion_y_eliminado_en_el_servidor_se_conserva(cliente_de, ana, beto):
    ca = cliente_de(ana)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Se va a eliminar"))
    assert ca.delete(url("cotizaciones_detalle", pk=a["servidor_id"])).status_code == 200
    r = sinc_cot(cliente_de(beto), uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "notes": "Hecho en campo"}, a["servidor_id"], 1)
    assert r["resultado"] == "copia" and "papelera" in r["conflicto"]
    assert Cotizacion.objects.get(pk=r["servidor_id"]).datos["notes"] == "Hecho en campo"


@pytest.mark.parametrize("base,actual,nuevos,esperado", [
    ({"a": 1, "b": 1}, {"a": 2, "b": 1}, {"a": 1, "b": 3}, {"a": 2, "b": 3}),
    ({"a": [1]}, {"a": [1, 2]}, {"a": [1]}, {"a": [1, 2]}),
    ({"a": {"x": 1, "y": 1}}, {"a": {"x": 2, "y": 1}}, {"a": {"x": 1, "y": 2}}, {"a": {"x": 2, "y": 2}}),
    ({"a": 1}, {"a": 1, "n": 1}, {"a": 2}, {"a": 2, "n": 1}),
])
def test_fusion_de_tres_vias(base, actual, nuevos, esperado):
    assert sincronizacion.fusionar(base, actual, nuevos) == esperado


@pytest.mark.parametrize("base,actual,nuevos", [({"a": 1}, {"a": 2}, {"a": 3}), ({"a": [1]}, {"a": [2]}, {"a": [3]}), ({"a": 1}, {}, {"a": 2})])
def test_fusion_que_choca(base, actual, nuevos):
    with pytest.raises(ValueError):
        sincronizacion.fusionar(base, actual, nuevos)


# -- Historial ---------------------------------------------------------------------------------------------------------------------

def test_historial_agrupa_guardados_seguidos_y_restaurar_crea_revision(cliente_de, ana, beto):
    ca, cb = cliente_de(ana), cliente_de(beto)
    idc = uuid.uuid4()
    a = sinc_cot(ca, idc, _datos_cot(ana, project_title="v1"))
    pk = a["servidor_id"]
    rev = a["revision"]
    for t in ("v2", "v3"):                                                      # Ana guarda seguido: una sola versión en el historial
        rev = sinc_cot(ca, idc, {**Cotizacion.objects.get(pk=pk).datos, "project_title": t}, pk, rev)["revision"]
    rev = sinc_cot(cb, uuid.uuid4(), {**Cotizacion.objects.get(pk=pk).datos, "project_title": "de Beto"}, pk, rev)["revision"]
    versiones = ca.get(url("cotizaciones_versiones", pk=pk)).json()["versiones"]
    assert [(v["revision"], v["nombre"]) for v in versiones] == [(4, "beto@grupoitt.com"), (3, "Ana López")] and versiones[0]["vigente"]
    assert VersionDocumento.objects.filter(documento_id=pk, visible=False).count() == 2      # los pasos intermedios siguen para fusionar
    v3 = versiones[1]
    assert ca.get(url("cotizaciones_version", pk=pk, vid=v3["id"])).json()["datos"]["project_title"] == "v3"
    pdf = ca.get(url("cotizaciones_version_pdf", pk=pk, vid=v3["id"]))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"
    # Restaurar exige poder editar: con Beto editando, no; tomando el control, sí, y es una revisión NUEVA.
    bloqueo(cb, pk, "tomar", SESION_B)
    assert post(ca, "cotizaciones_restaurar_version", {"sesion": SESION_A}, pk=pk, vid=v3["id"]).status_code == 409
    bloqueo(ca, pk, "forzar", SESION_A)
    r = post(ca, "cotizaciones_restaurar_version", {"sesion": SESION_A}, pk=pk, vid=v3["id"]).json()
    assert r["revision"] == 5 and r["datos"]["project_title"] == "v3" and r["datos"]["quote_number"] == a["folio"]
    nuevas = ca.get(url("cotizaciones_versiones", pk=pk)).json()["versiones"]
    assert nuevas[0]["nota"].startswith("Restaurada de la versión 3") and len(nuevas) == 3
    assert post(ca, "cotizaciones_restaurar_version", {"sesion": SESION_A}, pk=pk, vid=nuevas[0]["id"]).status_code == 409


def test_restaurar_un_reporte_deja_su_pdf_desactualizado(cliente_de, ana, settings):
    ca = cliente_de(ana)
    sha = crear_foto(ana, 4, settings)
    a = sinc_rep(ca, uuid.uuid4(), datos_reporte([foto(sha)], titulo="Uno"))
    r = Reporte.objects.get(pk=a["servidor_id"])
    rep.generar(r)
    v1 = VersionDocumento.objects.get(documento_id=r.pk, revision=1)
    VersionDocumento.objects.filter(pk=v1.pk).update(inicio=timezone.now() - timedelta(hours=1))      # para que no se agrupe
    sinc_rep(ca, uuid.uuid4(), datos_reporte([foto(sha)], titulo="Dos"), str(r.pk), 1)
    bloqueo(ca, r.pk, "tomar", SESION_A, tipo="reportes")
    restaurado = post(ca, "reportes_restaurar_version", {"sesion": SESION_A}, pk=r.pk, vid=v1.pk).json()
    assert restaurado["revision"] == 3 and restaurado["titulo"] == "Uno" and restaurado["pdf_al_dia"] is False


def test_el_historial_conserva_las_mas_recientes(ana, monkeypatch):
    monkeypatch.setattr(documentos, "VERSIONES_VISIBLES", 3)
    c = _cot(ana)
    for n in range(5):
        c.revision += 1
        c.save()
        documentos.registrar_version(c, ana, nota=f"paso {n}")
    assert list(VersionDocumento.objects.filter(documento_id=c.pk, visible=True).values_list("revision", flat=True)) == [6, 5, 4]


# -- Folios ------------------------------------------------------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_folios_en_paralelo_nunca_se_repiten(ana):
    """Ocho asignaciones a la vez, cada una en su conexión: la base las forma en fila con el renglón del contador."""
    folios, errores = [], []

    def asignar():
        try:
            for _ in range(5):
                folios.append(cot.asignar_folio())
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)
        finally:
            connection.close()

    hilos = [threading.Thread(target=asignar) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores and len(folios) == 40 and len(set(folios)) == 40
    assert ContadorFolio.objects.get(anio=timezone.localdate().year).ultimo == 40


def test_la_base_no_admite_folios_repetidos(ana):
    from django.db import IntegrityError, transaction

    a = _cot(ana)
    b = _cot(ana)
    with pytest.raises(IntegrityError), transaction.atomic():
        Cotizacion.objects.filter(pk=b.pk).update(folio=a.folio)


def test_folio_repetido_lo_reasigna_el_propietario(cliente_de, ana, beto, supervisora, propietario):
    a = _cot(ana)
    b = _cot(beto)
    Cotizacion.objects.filter(pk=b.pk).update(folio=a.folio, folio_repetido=True, datos={**b.datos, "quote_number": a.folio})
    html = cliente_de(supervisora).get(url("supervision")).content.decode()
    assert "Folios repetidos" in html and a.folio in html and "Asignar folio nuevo" not in html          # quien supervisa lo ve, no lo decide
    assert cliente_de(supervisora).post(url("supervision_asignar_folio", pk=b.pk)).status_code == 403
    r = cliente_de(propietario).post(url("supervision_asignar_folio", pk=b.pk))
    assert r.status_code == 302
    b.refresh_from_db()
    assert not b.folio_repetido and b.folio not in ("", a.folio) and b.datos["quote_number"] == b.folio
    assert VersionDocumento.objects.get(documento_id=b.pk, revision=b.revision).nota == f"Folio nuevo: antes {a.folio}"
    assert "Folios repetidos" not in cliente_de(supervisora).get(url("supervision")).content.decode()


@pytest.mark.django_db(transaction=True)
def test_migracion_con_folios_y_machotes_repetidos(django_user_model):
    """Antes se contaba por cuenta: dos cuentas con COT-2026-0003. Ningún folio cambia; el más nuevo queda marcado y el contador sigue."""
    from django.db.migrations.executor import MigrationExecutor

    ejecutor = MigrationExecutor(connection)
    antes, despues = [("herramientas", "0015_borrador_sesion")], [("herramientas", "0016_documentos_equipo")]
    ejecutor.migrate(antes)
    viejas = ejecutor.loader.project_state(antes).apps
    U = viejas.get_model(*django_user_model._meta.label.split("."))
    u1 = U.objects.create(username="uno", first_name="Uno")
    u2 = U.objects.create(username="dos", first_name="Dos")
    C = viejas.get_model("herramientas", "Cotizacion")
    M = viejas.get_model("herramientas", "MachoteCuenta")
    ahora = timezone.now()
    vieja = C.objects.create(cuenta_id=u1.pk, folio="COT-2026-0003", datos={"quote_number": "COT-2026-0003"}, creada=ahora - timedelta(days=2), actualizada=ahora)
    nueva = C.objects.create(cuenta_id=u2.pk, folio="COT-2026-0003", datos={"quote_number": "COT-2026-0003"}, creada=ahora - timedelta(days=1), actualizada=ahora)
    otra = C.objects.create(cuenta_id=u2.pk, folio="COT-2026-0009", datos={}, actualizada=ahora)
    pid = uuid.uuid4()
    M.objects.create(cuenta_id=u1.pk, plantilla_id=pid, nombre="Deslinde", concepto={}, creado=ahora - timedelta(days=3))
    M.objects.create(cuenta_id=u2.pk, plantilla_id=pid, nombre="deslinde", concepto={}, creado=ahora)
    ejecutor = MigrationExecutor(connection)
    ejecutor.loader.build_graph()
    ejecutor.migrate(despues)
    assert Cotizacion.objects.get(pk=vieja.pk).folio == "COT-2026-0003" and not Cotizacion.objects.get(pk=vieja.pk).folio_repetido
    assert Cotizacion.objects.get(pk=nueva.pk).folio == "COT-2026-0003" and Cotizacion.objects.get(pk=nueva.pk).folio_repetido
    assert not Cotizacion.objects.get(pk=otra.pk).folio_repetido
    assert ContadorFolio.objects.get(anio=2026).ultimo == 9
    assert cot.asignar_folio(timezone.datetime(2026, 5, 1).date()) == "COT-2026-0010"
    assert sorted(Machote.objects.values_list("nombre", flat=True)) == ["Deslinde", "deslinde (de Dos)"]
    assert Machote.objects.values("plantilla_id").distinct().count() == 2
    assert VersionDocumento.objects.filter(documento_id=vieja.pk, revision=1).exists()
    assert Cotizacion.objects.get(pk=nueva.pk).modificado_por_nombre == "Dos"
    assert [f["folio"] for f in cot.repetidos()] == ["COT-2026-0003"]
    U.objects.filter(pk__in=[u1.pk, u2.pk]).delete()


# -- Machotes de la empresa ---------------------------------------------------------------------------------------------------------

def test_machotes_de_la_empresa(cliente_de, ana, beto, administradora, roles):
    ca, cb = cliente_de(ana), cliente_de(beto)
    concepto = {"title": "Deslinde", "description": "", "subitems": [], "unit": "Lote", "quantity": 1, "unit_price": 100}
    m = post(ca, "cotizaciones_machotes", {"nombre": "Deslinde", "idioma": "es", "concepto": concepto}).json()
    assert m["autor"] == "Ana López" and m["puede_borrar"] is True
    vistos = cb.get(url("cotizaciones_machotes")).json()["machotes"]
    assert [x["nombre"] for x in vistos] == ["Deslinde"] and vistos[0]["puede_borrar"] is False
    assert post(cb, "cotizaciones_machotes", {"nombre": "DESLINDE", "idioma": "es", "concepto": concepto}).status_code == 422      # el nombre es de la empresa
    cambiado = post(cb, "cotizaciones_machotes", {"id": m["id"], "nombre": "Deslinde", "idioma": "es", "concepto": {**concepto, "unit_price": 200}}).json()
    assert cambiado["modificado_por"] == "beto@grupoitt.com" and cambiado["tiene_anterior"] is True
    assert cb.delete(url("cotizaciones_machote", pk=m["id"])).status_code == 409
    roles.conceder(administradora, "herramientas", "cotizaciones")
    assert cliente_de(administradora).delete(url("cotizaciones_machote", pk=m["id"])).status_code == 200
    assert "machotes" in ca.get(url("cotizaciones_contexto")).json() and "folio" not in ca.get(url("cotizaciones_contexto")).json()


# -- Papelera, roles y cuentas ----------------------------------------------------------------------------------------------------------

def test_papelera_recuperar_y_eliminar_para_siempre(cliente_de, ana, beto, administradora):
    ca, cb = cliente_de(ana), cliente_de(beto)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="A la papelera"))
    assert ca.delete(url("cotizaciones_detalle", pk=a["servidor_id"])).status_code == 200
    assert cb.get(url("cotizaciones_lista")).json()["cotizaciones"] == []
    html = cb.get(url("papelera")).content.decode()
    assert "A la papelera" in html and "Recuperar" not in html                                        # Beto no la creó ni la eliminó
    assert cb.post(url("papelera_recuperar", tipo="cotizacion", pk=a["servidor_id"])).status_code == 302
    assert Cotizacion.objects.get(pk=a["servidor_id"]).eliminado is not None
    ca.post(url("papelera_recuperar", tipo="cotizacion", pk=a["servidor_id"]))
    assert Cotizacion.objects.get(pk=a["servidor_id"]).eliminado is None
    ca.delete(url("cotizaciones_detalle", pk=a["servidor_id"]))
    ca.post(url("papelera_eliminar", tipo="cotizacion", pk=a["servidor_id"]))                  # solo quien administra
    assert Cotizacion.objects.filter(pk=a["servidor_id"]).exists()
    cliente_de(administradora).post(url("papelera_eliminar", tipo="cotizacion", pk=a["servidor_id"]))
    assert not Cotizacion.objects.filter(pk=a["servidor_id"]).exists() and not VersionDocumento.objects.filter(documento_id=a["servidor_id"]).exists()


def test_limpiar_vacia_la_papelera_vieja_y_los_pasos_intermedios(ana):
    c = _cot(ana)
    Cotizacion.objects.filter(pk=c.pk).update(eliminado=timezone.now() - timedelta(days=documentos.PAPELERA_DIAS + 1))
    d = _cot(ana)
    VersionDocumento.objects.create(tipo="cotizacion", documento_id=d.pk, revision=99, datos={}, visible=False,
                                    creada=timezone.now() - timedelta(days=documentos.INTERMEDIAS_DIAS + 1))
    assert documentos.limpiar() >= 2
    assert not Cotizacion.objects.filter(pk=c.pk).exists() and not VersionDocumento.objects.filter(revision=99).exists()


def test_la_supervision_solo_lee_y_no_ve_la_papelera_ajena_como_editable(cliente_de, ana, supervisora):
    a = _cot(ana, proyecto="Supervisada")
    s = cliente_de(supervisora)
    assert s.get(url("cotizaciones_lista")).status_code == 403
    assert post(s, "cotizaciones_bloqueo", {"accion": "forzar", "sesion": SESION_B}, pk=a.pk).status_code == 403
    Cotizacion.objects.filter(pk=a.pk).update(eliminado=timezone.now())
    html = s.get(url("papelera")).content.decode()
    assert "Supervisada" in html and "Recuperar" not in html


def test_al_eliminar_una_cuenta_sus_documentos_se_quedan(cliente_de, ana, beto):
    from django.db import transaction

    a = sinc_cot(cliente_de(ana), uuid.uuid4(), _datos_cot(ana, project_title="De la empresa"))
    bloqueo(cliente_de(ana), a["servidor_id"], "tomar", SESION_A)
    documentos.guardar_recuperacion(Cotizacion.objects.get(pk=a["servidor_id"]), ana, {"x": 1}, 1)
    with transaction.atomic():
        texto = documentos.al_eliminar_cuenta(ana.pk)
    assert "liberaron 1 documento" in texto and "borrador" in texto
    ana.delete()
    c = Cotizacion.objects.get(pk=a["servidor_id"])
    assert c.autor_nombre == "Ana López" and c.bloqueo_sesion == "" and not AvisoDocumento.objects.filter(cuenta_id=c.cuenta_id).exists()
    assert cliente_de(beto).get(url("cotizaciones_detalle", pk=c.pk)).json()["autor"]["nombre"] == "Ana López"


def test_lo_editado_sin_conexion_con_la_misma_cuenta_editando_se_fusiona(cliente_de, ana, beto):
    """Ana editó sin señal; al volver, su propia página ya tomó el bloqueo: no es «otra persona», se fusiona."""
    ca = cliente_de(ana)
    a = sinc_cot(ca, uuid.uuid4(), _datos_cot(ana, project_title="Deslinde", client_name="Agrícola"))
    sinc_cot(cliente_de(beto), uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "project_title": "Deslinde norte"}, a["servidor_id"], 1)
    bloqueo(ca, a["servidor_id"], "tomar", SESION_A)
    r = sinc_cot(ca, uuid.uuid4(), {**Cotizacion.objects.get(pk=a["servidor_id"]).datos, "project_title": "Deslinde", "client_name": "Agrícola del Norte"},
                 a["servidor_id"], 1)
    assert r["resultado"] == "fusionada" and Cotizacion.objects.count() == 1


@pytest.mark.parametrize("pagina,prefijo", [("cotizaciones", "cot"), ("reportes", "rep"), ("croquis", "cq")])
def test_las_paginas_de_campo_traen_lo_del_trabajo_en_equipo(cliente_de, ana, pagina, prefijo):
    """El HTML (el mismo para todas las cuentas) trae las URL del bloqueo, el historial y la recuperación, las zonas editables y el script."""
    html = cliente_de(ana).get(url(pagina)).content.decode()
    for nombre in ("bloqueo", "versiones", "version", "restaurar-version", "recuperacion", "avisos", "papelera"):
        assert f'data-url-{nombre}="' in html, nombre
    assert f'id="{prefijo}-bloqueo"' in html and f'id="{prefijo}-recuperacion"' in html and f'id="{prefijo}-avisos"' in html
    assert "data-editable" in html and 'id="mas-historial"' in html and "offline/compartido." in html
