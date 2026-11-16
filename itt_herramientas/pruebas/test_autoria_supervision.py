"""Autoría de los documentos (quién los hizo, fijado en el servidor) y la pantalla de supervisión de solo lectura."""
from __future__ import annotations

import json
import uuid

import pytest
from django.apps import apps as apps_reales

from itt_herramientas.comun import perfiles
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.models import Cotizacion, Croquis, Reporte
from itt_herramientas.reportes import servicio as rep

from .conftest import url
from .test_reportes import crear_foto, datos as datos_reporte, foto

pytestmark = pytest.mark.django_db


@pytest.fixture
def persona(crear_usuario):
    def crear(usuario, nombre="", correo="", *roles_):
        u = crear_usuario(usuario)
        u.first_name, u.last_name, u.email = (nombre.split(" ", 1) + [""])[:2] + [correo]
        u.save()
        return u
    return crear


@pytest.fixture
def ana(persona, roles):
    u = persona("ana", "Ana López", "Ana@Grupoitt.com")
    roles.conceder(u, "herramientas", "cotizaciones", "reportes")
    return u


@pytest.fixture
def beto(persona, roles):
    u = persona("beto@grupoitt.com")
    roles.conceder(u, "herramientas", "cotizaciones", "reportes")
    return u


@pytest.fixture
def supervisora(persona, roles):
    u = persona("sofia", "Sofía Ruiz", "sofia@grupoitt.com")
    roles.conceder(u, "herramientas", "supervision")
    return u


def _datos_cot(usuario, **campos):
    d = perfiles.nueva_cotizacion(perfiles.perfil_activo(usuario)).to_dict()
    d.update(items=[], images=[], **campos)
    return d


def _cot(usuario, folio=None, proyecto="Deslinde", cliente="Agrícola Norte"):
    """Una cotización. El folio lo asigna el servidor; con `folio`, la prueba lo fija después (como un dato de antes de compartir)."""
    r = cot.sincronizar(usuario, uuid.uuid4(), None, None, _datos_cot(usuario, project_title=proyecto, client_name=cliente), "grupo_itt")
    c = Cotizacion.objects.get(pk=r["servidor_id"])
    if folio and folio != c.folio:
        c.folio, c.datos = folio, {**c.datos, "quote_number": folio}
        c.save(update_fields=["folio", "datos"])
    return c


def _rep(usuario, titulo="Avance de obra", fotos=()):
    r = rep.sincronizar(usuario, uuid.uuid4(), None, None, datos_reporte(list(fotos), titulo=titulo), "grupo_itt")
    return Reporte.objects.get(pk=r["servidor_id"])


# -- Autoría ---------------------------------------------------------------------------------------------------------------

def test_el_servidor_fija_el_autor_al_crear_y_la_sincronizacion_no_lo_cambia(cliente_de, ana):
    c = cliente_de(ana)
    idc = uuid.uuid4()
    r = c.post(url("cotizaciones_sincronizar"), json.dumps({"id_cliente": str(idc), "datos": _datos_cot(ana, quote_number="COT-2026-0007")}), content_type="application/json").json()
    assert r["resultado"] == "creada" and r["autor"]["nombre"] == "Ana López" and r["autor"]["correo"] == "ana@grupoitt.com"
    doc = Cotizacion.objects.get(pk=r["servidor_id"])
    assert (doc.autor_nombre, doc.autor_correo) == ("Ana López", "ana@grupoitt.com")
    # La cuenta cambia de nombre y el documento se vuelve a guardar: el autor sigue siendo el de la creación.
    ana.first_name, ana.last_name = "Ana María", "Pérez"
    ana.save()
    cuerpo = {"id_cliente": str(idc), "servidor_id": r["servidor_id"], "revision_base": 1, "datos": _datos_cot(ana, quote_number="COT-2026-0007", project_title="Otro")}
    r2 = c.post(url("cotizaciones_sincronizar"), json.dumps(cuerpo), content_type="application/json").json()
    assert r2["resultado"] == "actualizada" and r2["autor"]["nombre"] == "Ana López"
    doc.refresh_from_db()
    assert doc.autor_nombre == "Ana López" and doc.revision == 2
    # Los datos del teléfono no pueden traer otro autor: no hay campo que lo reciba.
    cuerpo["datos"]["autor_nombre"] = "Alguien más"
    cuerpo["revision_base"] = 2
    c.post(url("cotizaciones_sincronizar"), json.dumps(cuerpo), content_type="application/json")
    doc.refresh_from_db()
    assert doc.autor_nombre == "Ana López"


def test_la_lista_el_detalle_y_el_contexto_traen_el_autor(cliente_de, ana, beto):
    _cot(ana)
    _rep(beto, "Visita")
    fila = cliente_de(ana).get(url("cotizaciones_lista")).json()["cotizaciones"][0]
    assert fila["autor"]["nombre"] == "Ana López" and fila["autor"]["creada"]
    assert cliente_de(ana).get(url("cotizaciones_contexto")).json()["autor"] == {"nombre": "Ana López", "correo": "ana@grupoitt.com"}
    fila = cliente_de(beto).get(url("reportes_lista")).json()["reportes"][0]
    assert fila["autor"] == {"nombre": "beto@grupoitt.com", "correo": "beto@grupoitt.com", "creada": fila["autor"]["creada"]}   # sin nombre: el usuario, que es su correo


def test_duplicar_importar_y_la_copia_por_conflicto_son_de_quien_las_hace(cliente_de, ana, beto, croquista):
    original = _cot(ana)
    copia = cot.duplicar(beto, original)                                  # (la vista solo deja duplicar lo propio; aquí se prueba el servicio)
    assert copia.autor_nombre == "beto@grupoitt.com"
    imp = cot.importar_editable(ana, json.dumps(original.datos).encode(), "grupo_itt")
    assert imp.autor_nombre == "Ana López"
    r = _rep(ana)
    res = rep.sincronizar(ana, r.id_cliente, None, 1, datos_reporte([], titulo="Cambiado"), "grupo_itt")
    rep.sincronizar(ana, r.id_cliente, None, 1, datos_reporte([], titulo="Desde el teléfono"), "grupo_itt")
    assert res["resultado"] == "actualizada"
    assert all(x.autor_nombre == "Ana López" for x in Reporte.objects.filter(cuenta_id=ana.pk))
    from itt_herramientas.croquis import servicio as cq

    k = cq.sincronizar(croquista, uuid.uuid4(), None, None, {"titulo": "Predio", "puntos": []}, "grupo_itt")
    assert Croquis.objects.get(pk=k["servidor_id"]).autor_nombre == "croquista"


@pytest.fixture
def croquista(crear_usuario, roles):
    u = crear_usuario("croquista")
    roles.conceder(u, "herramientas", "croquis")
    return u


def test_la_migracion_llena_el_autor_de_lo_que_ya_existe(ana, beto):
    from importlib import import_module

    migracion = import_module("itt_herramientas.migrations.0010_autoria")
    a, b = _cot(ana), _rep(beto)
    huerfano = _cot(ana, folio="COT-2026-0099")
    Cotizacion.objects.filter(pk=huerfano.pk).update(cuenta_id=987654)                # su cuenta ya no existe
    for m in (Cotizacion, Reporte):
        m.objects.update(autor_nombre="", autor_correo="")
    migracion.llenar(apps_reales, None)
    a.refresh_from_db(), b.refresh_from_db(), huerfano.refresh_from_db()
    assert (a.autor_nombre, a.autor_correo) == ("Ana López", "ana@grupoitt.com")
    assert (b.autor_nombre, b.autor_correo) == ("beto@grupoitt.com", "beto@grupoitt.com")
    assert (huerfano.autor_nombre, huerfano.autor_correo) == ("", "")
    migracion.llenar(apps_reales, None)                                               # repetirla no cambia nada
    a.refresh_from_db()
    assert a.autor_nombre == "Ana López"


def test_eliminar_la_cuenta_no_borra_al_autor_del_documento(ana):
    c = _cot(ana)
    ana.delete()
    c.refresh_from_db()
    assert c.autor_nombre == "Ana López" and c.autor_correo == "ana@grupoitt.com"


# -- Supervisión: permisos ---------------------------------------------------------------------------------------------------

def test_la_supervision_pide_el_rol(cliente_de, ana, crear_usuario):
    c = _cot(ana)
    r = _rep(ana)
    rutas = [url("supervision"), url("supervision_cotizacion", pk=c.pk), url("supervision_cotizacion_pdf", pk=c.pk),
             url("supervision_reporte", pk=r.pk), url("supervision_reporte_descargar", pk=r.pk)]
    nadie = cliente_de(crear_usuario("nadie"))
    for ruta in rutas:
        assert cliente_de(ana).get(ruta).status_code == 403                          # tiene Herramientas, no supervisión
        assert nadie.get(ruta).status_code == 404                                    # sin Herramientas: ni se sabe que existe
    assert cliente_de(ana).post(url("supervision_reporte_pdf", pk=r.pk)).status_code == 403


def test_el_equipo_ve_lo_de_todos_pero_solo_su_autor_lo_elimina(cliente_de, ana, beto):
    c, r = _cot(ana), _rep(ana)
    cb = cliente_de(beto)
    assert cb.get(url("cotizaciones_detalle", pk=c.pk)).status_code == 200
    assert cb.get(url("reportes_detalle", pk=r.pk)).status_code == 200
    assert cb.delete(url("cotizaciones_detalle", pk=c.pk)).status_code == 409
    assert cb.delete(url("reportes_detalle", pk=r.pk)).status_code == 409
    assert [f["id"] for f in cb.get(url("cotizaciones_lista")).json()["cotizaciones"]] == [str(c.pk)]
    assert Cotizacion.objects.filter(pk=c.pk, eliminado__isnull=True).exists() and Reporte.objects.filter(pk=r.pk, eliminado__isnull=True).exists()


def test_quien_supervisa_ve_todo_pero_no_puede_escribir(cliente_de, ana, beto, supervisora, roles):
    c, r = _cot(ana), _rep(beto, "Visita de obra")
    s = cliente_de(supervisora)
    html = s.get(url("supervision")).content.decode()
    assert "Deslinde" in html and "Visita de obra" in html and "Ana López" in html and "beto@grupoitt.com" in html
    assert s.get(url("supervision_cotizacion", pk=c.pk)).status_code == 200
    assert s.get(url("supervision_reporte", pk=r.pk)).status_code == 200
    # Las rutas de supervisión solo leen.
    for ruta in (url("supervision"), url("supervision_cotizacion", pk=c.pk), url("supervision_reporte", pk=r.pk), url("supervision_cotizacion_pdf", pk=c.pk)):
        assert s.post(ruta).status_code == 405 and s.delete(ruta).status_code == 405
    # Sin los roles de edición, la supervisión no escribe (403).
    assert s.delete(url("cotizaciones_detalle", pk=c.pk)).status_code == 403
    cuerpo = {"id_cliente": str(uuid.uuid4()), "servidor_id": str(r.pk), "revision_base": 1, "datos": datos_reporte([], titulo="Pisado")}
    assert s.post(url("reportes_sincronizar"), json.dumps(cuerpo), content_type="application/json").status_code == 403
    c.refresh_from_db(), r.refresh_from_db()
    assert c.revision == 1 and r.titulo == "Visita de obra" and r.revision == 1


def test_el_propietario_supervisa_sin_rol_explicito(cliente_de, propietario, ana):
    _cot(ana)
    assert cliente_de(propietario).get(url("supervision")).status_code == 200


def test_la_entrada_del_menu_solo_la_ve_quien_supervisa(cliente_de, ana, supervisora):
    assert "supervision" in cliente_de(supervisora).get(url("sesion")).json()["nav"]
    assert "supervision" not in cliente_de(ana).get(url("sesion")).json()["nav"]
    html = cliente_de(supervisora).get(url("inicio")).content.decode()
    assert "Solo lectura" in html and "Supervisión" in html
    assert "Cotizaciones y reportes de la empresa, con su autor" not in cliente_de(ana).get(url("inicio")).content.decode()


# -- Supervisión: lista, filtros y PDF ---------------------------------------------------------------------------------------

def test_filtros_por_autor_tipo_texto_y_fechas_con_paginacion(cliente_de, ana, beto, supervisora):
    from datetime import timedelta

    from django.utils import timezone

    for n in range(30):
        _cot(ana, folio=f"COT-2026-{n:04d}", proyecto=f"Proyecto {n}")
    viejo = _rep(beto, "Levantamiento viejo")
    Reporte.objects.filter(pk=viejo.pk).update(creada=timezone.now() - timedelta(days=40))
    s = cliente_de(supervisora)
    r = s.get(url("supervision"))
    assert r.context["pagina"].paginator.count == 31 and len(r.context["filas"]) == 25 and r["Cache-Control"] == "private, no-store"
    assert len(s.get(url("supervision"), {"pagina": 2}).context["filas"]) == 6
    assert s.get(url("supervision"), {"autor": beto.pk}).context["pagina"].paginator.count == 1
    assert s.get(url("supervision"), {"tipo": "reporte"}).context["pagina"].paginator.count == 1
    assert s.get(url("supervision"), {"tipo": "cotizacion", "q": "proyecto 1"}).context["pagina"].paginator.count == 11   # 1 y 10–19
    assert s.get(url("supervision"), {"q": "BETO@"}).context["pagina"].paginator.count == 1                                 # por correo del autor
    hoy = timezone.localdate()
    assert s.get(url("supervision"), {"desde": hoy.isoformat()}).context["pagina"].paginator.count == 30
    assert s.get(url("supervision"), {"hasta": (hoy - timedelta(days=30)).isoformat()}).context["pagina"].paginator.count == 1
    # Fechas al revés se acomodan; una inválida se ignora con aviso.
    assert s.get(url("supervision"), {"desde": hoy.isoformat(), "hasta": (hoy - timedelta(days=50)).isoformat()}).context["pagina"].paginator.count == 31
    r = s.get(url("supervision"), {"desde": "ayer"})
    assert r.context["pagina"].paginator.count == 31 and "no es válida" in r.content.decode()
    # La paginación conserva los filtros.
    assert "tipo=cotizacion" in s.get(url("supervision"), {"tipo": "cotizacion"}).content.decode().split("Siguiente")[0].rsplit("href=", 1)[-1]
    vacio = s.get(url("supervision"), {"q": "no existe nada así"}).content.decode()
    assert "Nada coincide" in vacio and "Quitar filtros" in vacio


def test_lista_vacia(cliente_de, supervisora):
    assert "Todavía nadie ha guardado" in cliente_de(supervisora).get(url("supervision")).content.decode()


def test_la_autora_eliminada_se_sigue_viendo(cliente_de, ana, supervisora):
    c = _cot(ana)
    ana.delete()
    s = cliente_de(supervisora)
    assert "Ana López" in s.get(url("supervision")).content.decode()
    html = s.get(url("supervision_cotizacion", pk=c.pk)).content.decode()
    assert "Ana López" in html and "Cuenta eliminada" in html


def test_pdf_de_la_cotizacion_igual_al_de_su_autora(cliente_de, ana, supervisora, monkeypatch):
    from reportlab import rl_config

    monkeypatch.setattr(rl_config, "invariant", 1)                                   # sin fecha ni identificador al azar: se comparan los bytes
    c = _cot(ana)
    propio = cliente_de(ana).post(url("cotizaciones_pdf"), json.dumps({"datos": c.datos}), content_type="application/json").content
    r = cliente_de(supervisora).get(url("supervision_cotizacion_pdf", pk=c.pk), {"descargar": "1"})
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf" and "attachment" in r["Content-Disposition"]
    assert r.content == propio


def test_pdf_del_reporte_sin_tocar_el_reporte_ni_su_drive(cliente_de, ana, supervisora, settings, monkeypatch):
    shas = [crear_foto(ana, n, settings) for n in (1, 2)]
    r = _rep(ana, "Avance", [foto(s) for s in shas])
    antes = Reporte.objects.filter(pk=r.pk).values().get()
    from itt_herramientas.drive import servicio as drive

    encolados = []
    monkeypatch.setattr(drive, "encolar", lambda *a, **k: encolados.append(a))
    s = cliente_de(supervisora)
    respuesta = s.post(url("supervision_reporte_pdf", pk=r.pk))
    assert respuesta.status_code == 200 and respuesta.json()["url"].endswith(f"?revision={r.revision}")
    pdf = s.get(respuesta.json()["url"])
    assert pdf.status_code == 200 and b"".join(pdf.streaming_content).startswith(b"%PDF")
    assert Reporte.objects.filter(pk=r.pk).values().get() == antes and encolados == []
    # Fotos: solo las del reporte, y de la cuenta autora.
    assert s.get(url("supervision_reporte_foto", pk=r.pk, sha256=shas[0])).status_code == 200
    ajena = crear_foto(supervisora, 9, settings)
    assert s.get(url("supervision_reporte_foto", pk=r.pk, sha256=ajena)).status_code == 404
    # Una revisión vieja ya no se descarga.
    assert s.get(url("supervision_reporte_descargar", pk=r.pk), {"revision": "99"}).status_code == 404


def test_pdf_del_reporte_con_fotos_pendientes(cliente_de, ana, supervisora):
    r = _rep(ana, "Sin subir", [foto("ab" * 32)])
    respuesta = cliente_de(supervisora).post(url("supervision_reporte_pdf", pk=r.pk))
    assert respuesta.status_code == 409 and "Faltan fotos" in respuesta.json()["error"]
