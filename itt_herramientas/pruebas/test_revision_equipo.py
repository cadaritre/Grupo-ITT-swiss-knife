"""Defectos de la revisión independiente de `web-equipo` (16-11-2026): cada prueba falla sin su arreglo."""
from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from itt_herramientas.comun import documentos, sincronizacion
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.models import BorradorRecuperacion, Cotizacion, Reporte
from itt_herramientas.reportes import servicio as rep

from .conftest import url
from .test_autoria_supervision import _cot, _datos_cot, ana, beto, persona, supervisora  # noqa: F401  (fixtures)
from .test_reportes import crear_foto, datos as datos_reporte, foto

pytestmark = pytest.mark.django_db


def post(c, nombre, cuerpo, **kw):
    return c.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


# 1. El ZIP respeta la herramienta y los roles ---------------------------------------------------------------------------------

def test_zip_de_cotizaciones_no_entrega_reportes(cliente_de, crear_usuario, roles, ana, settings):
    solo_cot = crear_usuario("solo_cot")
    roles.conceder(solo_cot, "herramientas", "cotizaciones")
    r = rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([foto(crear_foto(ana, 1, settings))], titulo="Ajeno"), "grupo_itt")
    c = cliente_de(solo_cot)
    respuesta = post(c, "cotizaciones_zip", {"elementos": [["reporte", r["servidor_id"]]]})
    assert respuesta.status_code == 422
    assert Reporte.objects.get(pk=r["servidor_id"]).salida_ruta == ""              # ni se generó su PDF ni fue a ningún Drive
    mezcla = post(c, "cotizaciones_zip", {"elementos": [["cotizacion", str(_cot(ana).pk)], ["reporte", r["servidor_id"]]]})
    assert mezcla.status_code == 422


def test_zip_omite_lo_de_la_papelera(cliente_de, ana):
    a = _cot(ana)
    Cotizacion.objects.filter(pk=a.pk).update(eliminado=timezone.now())
    from itt_herramientas.comun import zips

    with pytest.raises(zips.LoteInvalido):
        zips.armar(zips.nuevo_token(), ana.pk, [["cotizacion", str(a.pk)]], "cotizaciones")


# 3. Una fusión que da datos inválidos no atora la cola: queda copia con lo del teléfono -----------------------------------------

def test_fusion_invalida_queda_como_copia(cliente_de, ana, beto, monkeypatch):
    monkeypatch.setattr(rep, "MAX_FOTOS", 3)
    a = rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([foto("a" * 64)], titulo="Visita"), "grupo_itt")
    rep.sincronizar(beto, uuid.uuid4(), uuid.UUID(a["servidor_id"]), 1, datos_reporte([foto("a" * 64), foto("b" * 64)], titulo="Visita"), "grupo_itt")
    campo = datos_reporte([foto("a" * 64), foto("c" * 64), foto("d" * 64)], titulo="Visita")           # válido solo; junto con lo de la oficina, 4 > 3
    r = rep.sincronizar(ana, uuid.uuid4(), uuid.UUID(a["servidor_id"]), 1, campo, "grupo_itt")
    assert r["resultado"] == "copia"
    assert [f["sha256"][0] for f in Reporte.objects.get(pk=r["servidor_id"]).datos["fotos"]] == ["a", "c", "d"]
    assert [f["sha256"][0] for f in Reporte.objects.get(pk=a["servidor_id"]).datos["fotos"]] == ["a", "b"]


# 7. Reintentar una copia en conflicto con datos más nuevos los guarda; una copia de la papelera no se reutiliza ------------------

def test_copia_existente_recibe_los_datos_nuevos_y_la_de_la_papelera_no_se_reusa(ana, beto):
    a = _cot(ana)
    cot.sincronizar(beto, uuid.uuid4(), a.pk, 1, {**a.datos, "project_title": "Oficina"}, "grupo_itt")
    idc = uuid.uuid4()
    primera = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo 1"}, "grupo_itt")
    otra = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo 2"}, "grupo_itt")
    assert otra["servidor_id"] == primera["servidor_id"] and otra["revision"] == primera["revision"] + 1
    assert Cotizacion.objects.get(pk=primera["servidor_id"]).proyecto == "Campo 2"
    Cotizacion.objects.filter(pk=primera["servidor_id"]).update(eliminado=timezone.now())
    tercera = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo 3"}, "grupo_itt")
    assert tercera["resultado"] == "copia" and tercera["servidor_id"] != primera["servidor_id"]
    assert Cotizacion.objects.get(pk=tercera["servidor_id"]).proyecto == "Campo 3"


# 8. Importar un editable cuyo folio alguien acaba de tomar no da 500: recibe el siguiente ----------------------------------------

def test_importar_con_folio_que_se_ocupa_a_la_vez(cliente_de, ana, beto, monkeypatch):
    from django.core.files.uploadedfile import SimpleUploadedFile

    a = _cot(beto)
    monkeypatch.setattr(cot, "folio_libre", lambda folio, excepto=None: True)        # la carrera: libre al revisar, ocupado al guardar
    editable = json.dumps({**a.datos, "quote_number": a.folio}).encode()
    r = cliente_de(ana).post(url("cotizaciones_importar"), {"archivo": SimpleUploadedFile("e.json", editable)})
    assert r.status_code == 201 and r.json()["folio"] != a.folio and r.json()["folio_original"] == a.folio


# 9. El bloqueo aguanta una pestaña en segundo plano (el navegador late cada ~60 s) -----------------------------------------------

def test_el_bloqueo_aguanta_dos_minutos_sin_latido(cliente_de, ana, beto):
    a = _cot(ana)
    post(cliente_de(ana), "cotizaciones_bloqueo", {"accion": "tomar", "sesion": "pagina-de-ana-0001"}, pk=a.pk)
    Cotizacion.objects.filter(pk=a.pk).update(bloqueo_latido=timezone.now() - timedelta(seconds=120))
    r = post(cliente_de(beto), "cotizaciones_bloqueo", {"accion": "tomar", "sesion": "pagina-de-beto-0001"}, pk=a.pk).json()
    assert r["mio"] is False and r["quien"] == "Ana López"


# 4. Listas por páginas, y lo eliminado se dice explícitamente ------------------------------------------------------------------

def test_la_lista_va_por_paginas_y_el_detalle_dice_si_esta_en_la_papelera(cliente_de, ana, monkeypatch):
    from itt_herramientas.cotizaciones import vistas

    monkeypatch.setattr(vistas, "POR_PAGINA", 3)
    for n in range(5):
        _cot(ana, proyecto=f"P{n}")
    c = cliente_de(ana)
    uno = c.get(url("cotizaciones_lista")).json()
    dos = c.get(url("cotizaciones_lista"), {"pagina": uno["siguiente"]}).json()
    assert len(uno["cotizaciones"]) == 3 and len(dos["cotizaciones"]) == 2 and dos["siguiente"] is None
    assert {f["id"] for f in uno["cotizaciones"]} | {f["id"] for f in dos["cotizaciones"]} == {str(x) for x in Cotizacion.objects.values_list("pk", flat=True)}
    tirada = Cotizacion.objects.first()
    Cotizacion.objects.filter(pk=tirada.pk).update(eliminado=timezone.now())
    r = c.get(url("cotizaciones_detalle", pk=tirada.pk))
    assert r.status_code == 410 and r.json()["eliminado"] is True


def test_quien_no_es_el_autor_y_edito_algo_que_se_elimino_recibe_copia_en_conflicto(ana, beto):
    a = _cot(ana)
    Cotizacion.objects.filter(pk=a.pk).update(eliminado=timezone.now())
    r = cot.sincronizar(beto, uuid.uuid4(), a.pk, 1, {**a.datos, "notes": "De Beto"}, "grupo_itt")
    assert r["resultado"] == "copia" and r["conflicto"].startswith("Copia en conflicto de") and "papelera" in r["conflicto"]


# 5. Supervisión: nada de la papelera y las fotos del equipo se ven --------------------------------------------------------------

def test_supervision_no_abre_lo_de_la_papelera_y_ve_las_fotos_del_equipo(cliente_de, ana, beto, supervisora, settings):
    sha = crear_foto(beto, 7, settings)                                              # la subió Beto
    r = rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([], titulo="Visita"), "grupo_itt")
    rep.sincronizar(beto, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 1, datos_reporte([foto(sha)], titulo="Visita"), "grupo_itt")
    s = cliente_de(supervisora)
    assert s.get(url("supervision_reporte_foto", pk=r["servidor_id"], sha256=sha)).status_code == 200
    c = _cot(ana)
    for modelo, pk in ((Reporte, r["servidor_id"]), (Cotizacion, c.pk)):
        modelo.objects.filter(pk=pk).update(eliminado=timezone.now())
    for ruta in (url("supervision_reporte", pk=r["servidor_id"]), url("supervision_reporte_foto", pk=r["servidor_id"], sha256=sha),
                 url("supervision_reporte_descargar", pk=r["servidor_id"]), url("supervision_cotizacion", pk=c.pk), url("supervision_cotizacion_pdf", pk=c.pk)):
        assert s.get(ruta).status_code == 404, ruta
    assert s.post(url("supervision_reporte_pdf", pk=r["servidor_id"])).status_code == 404


# 6. Dos pestañas que pierden el control guardan cada una su borrador de recuperación ---------------------------------------------

def test_dos_pestanas_conservan_cada_una_su_recuperacion(cliente_de, ana, beto):
    a = _cot(ana)
    ca = cliente_de(ana)
    for sesion in ("pestana-uno-0001", "pestana-dos-0001"):
        post(ca, "cotizaciones_bloqueo", {"accion": "forzar", "sesion": sesion}, pk=a.pk)
    post(cliente_de(beto), "cotizaciones_bloqueo", {"accion": "forzar", "sesion": "pagina-de-beto-0001"}, pk=a.pk)
    for sesion, titulo in (("pestana-uno-0001", "Uno"), ("pestana-dos-0001", "Dos")):
        r = post(ca, "cotizaciones_sincronizar", {"id_cliente": str(uuid.uuid4()), "servidor_id": str(a.pk), "revision_base": 1,
                                                  "datos": {**a.datos, "project_title": titulo}, "sesion": sesion}).json()
        assert r["resultado"] == "recuperacion"
    assert sorted(BorradorRecuperacion.objects.filter(cuenta_id=ana.pk).values_list("datos__project_title", flat=True)) == ["Dos", "Uno"]
    recs = ca.get(url("cotizaciones_detalle", pk=a.pk)).json()["recuperaciones"]
    assert len(recs) == 2
    assert ca.delete(url("cotizaciones_recuperacion", pk=a.pk) + f"?id={recs[0]['id']}").status_code == 200
    assert BorradorRecuperacion.objects.filter(cuenta_id=ana.pk).count() == 1


# 10. Las fotos se autorizan contra el documento que las usa --------------------------------------------------------------------

def test_no_se_ve_la_foto_ajena_metiendola_en_un_documento_propio(cliente_de, ana, beto, settings):
    sha = crear_foto(ana, 9, settings)                                               # de Ana, en un reporte que Beto nunca editó
    rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([foto(sha)], titulo="De Ana"), "grupo_itt")
    trampa = rep.sincronizar(beto, uuid.uuid4(), None, None, datos_reporte([foto(sha)], titulo="Trampa"), "grupo_itt")
    cb = cliente_de(beto)
    assert cb.get(url("reportes_foto", sha256=sha), {"doc": trampa["servidor_id"]}).status_code == 404
    assert cb.get(url("reportes_foto", sha256=sha)).status_code == 404                   # sin documento, solo las propias
    with pytest.raises(rep.FotosPendientes):                                             # ni entra a un PDF
        rep.verificar_listo(Reporte.objects.get(pk=trampa["servidor_id"]))
    propio = Reporte.objects.get(titulo="De Ana")
    assert cb.get(url("reportes_foto", sha256=sha), {"doc": str(propio.pk)}).status_code == 200      # el reporte del equipo, sí


def test_la_foto_de_cotizacion_se_autoriza_contra_su_cotizacion(cliente_de, ana, beto, settings):
    from itt_herramientas.models import Captura

    sha = crear_foto(ana, 11, settings)
    Captura.objects.filter(sha256=sha).update(cuenta_id=ana.pk)
    con_foto = lambda u: {**_datos_cot(u), "images": [{"path": f"captura:{sha}", "caption": ""}]}  # noqa: E731
    trampa = cot.sincronizar(beto, uuid.uuid4(), None, None, con_foto(beto), "grupo_itt")
    cb = cliente_de(beto)
    assert cb.get(url("cotizaciones_foto", sha256=sha), {"doc": trampa["servidor_id"]}).status_code == 404
    buena = cot.sincronizar(ana, uuid.uuid4(), None, None, con_foto(ana), "grupo_itt")
    assert cb.get(url("cotizaciones_foto", sha256=sha), {"doc": buena["servidor_id"]}).status_code == 200


def test_la_papelera_de_supervision_no_muestra_croquis(cliente_de, supervisora, ana):
    from itt_herramientas.models import Croquis

    k = Croquis.objects.create(cuenta_id=ana.pk, datos={"title": "Croquis tirado", "points": []}, titulo="Croquis tirado", eliminado=timezone.now())
    assert "Croquis tirado" not in cliente_de(supervisora).get(url("papelera")).content.decode()
    assert k.pk


def test_quien_administra_asigna_folios_nuevos(cliente_de, ana, beto, administradora):
    a, b = _cot(ana), _cot(beto)
    Cotizacion.objects.filter(pk=b.pk).update(folio=a.folio, folio_repetido=True)
    assert cliente_de(administradora).post(url("supervision_asignar_folio", pk=b.pk)).status_code == 302
    assert not Cotizacion.objects.get(pk=b.pk).folio_repetido
