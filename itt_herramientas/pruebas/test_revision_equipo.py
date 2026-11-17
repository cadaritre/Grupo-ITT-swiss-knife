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

def test_la_lista_es_incremental_con_cursor_y_marca_lo_de_la_papelera(cliente_de, ana, monkeypatch):
    from itt_herramientas.comun import vistas_documentos

    monkeypatch.setattr(vistas_documentos, "POR_PAGINA", 3)
    for n in range(5):
        _cot(ana, proyecto=f"P{n}")
    c = cliente_de(ana)
    uno = c.get(url("cotizaciones_lista")).json()
    dos = c.get(url("cotizaciones_lista"), {"desde": uno["cursor"]}).json()
    assert len(uno["cotizaciones"]) == 3 and uno["mas"] and len(dos["cotizaciones"]) == 2 and not dos["mas"]
    assert {f["id"] for f in uno["cotizaciones"]} | {f["id"] for f in dos["cotizaciones"]} == {str(x) for x in Cotizacion.objects.values_list("pk", flat=True)}
    assert "datos" not in uno["cotizaciones"][0]                                              # solo resúmenes: los datos, aparte
    nada = c.get(url("cotizaciones_lista"), {"desde": dos["cursor"]}).json()
    assert nada["cotizaciones"] == [] and nada["eliminados"] == [] and nada["cursor"] == dos["cursor"]
    # Un latido no mueve el cursor; tirar a la papelera, sí (y llega como eliminado, no como renglón).
    tirada = Cotizacion.objects.first()
    post(c, "cotizaciones_bloqueo", {"accion": "tomar", "sesion": "pagina-de-ana-0001"}, pk=tirada.pk)
    con_bloqueo = c.get(url("cotizaciones_lista"), {"desde": dos["cursor"]}).json()
    post(c, "cotizaciones_bloqueo", {"accion": "latido", "sesion": "pagina-de-ana-0001"}, pk=tirada.pk)
    assert c.get(url("cotizaciones_lista"), {"desde": con_bloqueo["cursor"]}).json()["cotizaciones"] == []
    post(c, "cotizaciones_bloqueo", {"accion": "soltar", "sesion": "pagina-de-ana-0001"}, pk=tirada.pk)
    assert c.delete(url("cotizaciones_detalle", pk=tirada.pk)).status_code == 200
    despues = c.get(url("cotizaciones_lista"), {"desde": con_bloqueo["cursor"]}).json()
    assert str(tirada.pk) in despues["eliminados"]
    r = c.get(url("cotizaciones_detalle", pk=tirada.pk))
    assert r.status_code == 410 and r.json()["eliminado"] is True


def test_tras_borrar_para_siempre_un_cursor_viejo_recibe_los_vigentes(cliente_de, ana, administradora):
    a, b = _cot(ana), _cot(ana)
    c = cliente_de(ana)
    cursor = c.get(url("cotizaciones_lista")).json()["cursor"]
    c.delete(url("cotizaciones_detalle", pk=b.pk))
    documentos.destruir(Cotizacion.objects.get(pk=b.pk))
    r = c.get(url("cotizaciones_lista"), {"desde": cursor}).json()
    assert r["vigentes"] == [str(a.pk)]


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


# D. Las fotos del equipo siguen valiendo en duplicados, copias en conflicto y con el historial recortado --------------------------

def test_fotos_del_equipo_en_duplicado_copia_y_con_historial_recortado(cliente_de, ana, beto, crear_usuario, roles, settings, monkeypatch):
    from itt_herramientas.models import Captura

    carla = crear_usuario("carla")
    roles.conceder(carla, "herramientas", "cotizaciones", "reportes")
    # Reporte de Ana; Beto le agrega su foto. La oficina (Carla) cambia el título mientras el campo (Ana) agrega otra foto que choca.
    sha_b = crear_foto(beto, 21, settings)
    r = rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([], titulo="Visita"), "grupo_itt")
    rep.sincronizar(beto, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 1, datos_reporte([foto(sha_b, "De Beto")], titulo="Visita"), "grupo_itt")
    rep.sincronizar(carla, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 2, datos_reporte([foto(sha_b, "Oficina")], titulo="Visita"), "grupo_itt")
    copia = rep.sincronizar(ana, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 2, datos_reporte([foto(sha_b, "Campo")], titulo="Visita"), "grupo_itt")
    assert copia["resultado"] == "copia"
    copia_r = Reporte.objects.get(pk=copia["servidor_id"])
    rep.verificar_listo(copia_r)                                                    # la foto de Beto entra al PDF de la copia
    assert cliente_de(carla).get(url("reportes_foto", sha256=sha_b), {"doc": str(copia_r.pk)}).status_code == 200
    # Cotización de Ana con la foto de Beto; Carla la duplica: el duplicado ve la foto.
    sha_c = crear_foto(beto, 22, settings)
    a = _cot(ana)
    cot.sincronizar(beto, uuid.uuid4(), a.pk, 1, {**Cotizacion.objects.get(pk=a.pk).datos, "images": [{"path": f"captura:{sha_c}", "caption": ""}]}, "grupo_itt")
    dup = cliente_de(carla).post(url("cotizaciones_duplicar", pk=a.pk)).json()
    _, _, pendientes = cot.generar_pdf(carla, dup["datos"], Cotizacion.objects.get(pk=dup["id"]))
    assert pendientes == []
    assert cliente_de(carla).get(url("cotizaciones_foto", sha256=sha_c), {"doc": dup["id"]}).status_code == 200
    # Recortar el historial (Beto ya no está entre las versiones que quedan) no quita su foto.
    monkeypatch.setattr(documentos, "VERSIONES_VISIBLES", 1)
    for n in range(3):
        cot.sincronizar(ana, uuid.uuid4(), a.pk, Cotizacion.objects.get(pk=a.pk).revision, {**Cotizacion.objects.get(pk=a.pk).datos, "notes": f"n{n}"}, "grupo_itt")
    from itt_herramientas.models import VersionDocumento

    assert not VersionDocumento.objects.filter(documento_id=a.pk, cuenta_id=beto.pk, visible=True).exists()
    assert Captura.objects.filter(sha256=sha_c, cuenta_id=beto.pk).exists()
    assert cliente_de(carla).get(url("cotizaciones_foto", sha256=sha_c), {"doc": str(a.pk)}).status_code == 200


# 7. Lo que la oficina hizo en una copia en conflicto no lo pisa un reintento del teléfono ----------------------------------------

def test_reintento_no_pisa_lo_que_la_oficina_hizo_en_la_copia(cliente_de, ana, beto):
    a = _cot(ana, proyecto="Original")
    cot.sincronizar(beto, uuid.uuid4(), a.pk, 1, {**a.datos, "project_title": "Oficina"}, "grupo_itt")
    idc = uuid.uuid4()
    primera = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo"}, "grupo_itt")
    copia = Cotizacion.objects.get(pk=primera["servidor_id"])
    # En la oficina, Beto corrige el cliente de la copia.
    cot.sincronizar(beto, uuid.uuid4(), copia.pk, copia.revision, {**copia.datos, "client_name": "Cliente corregido"}, "grupo_itt")
    # El teléfono reintenta con un cambio que no choca (notas): se fusiona, nada de Beto se pierde.
    otra = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo", "notes": "Más notas"}, "grupo_itt")
    c = Cotizacion.objects.get(pk=otra["servidor_id"])
    assert otra["servidor_id"] == primera["servidor_id"] and c.cliente == "Cliente corregido" and c.datos["notes"] == "Más notas"
    # Y si choca con lo de Beto, va a otra copia.
    tercera = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo", "notes": "Más notas", "client_name": "Del campo"}, "grupo_itt")
    assert tercera["servidor_id"] != primera["servidor_id"]
    assert Cotizacion.objects.get(pk=primera["servidor_id"]).cliente == "Cliente corregido"


def test_reintento_no_toca_una_copia_que_alguien_edita(cliente_de, ana, beto):
    a = _cot(ana, proyecto="Original")
    cot.sincronizar(beto, uuid.uuid4(), a.pk, 1, {**a.datos, "project_title": "Oficina"}, "grupo_itt")
    idc = uuid.uuid4()
    primera = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo"}, "grupo_itt")
    post(cliente_de(beto), "cotizaciones_bloqueo", {"accion": "tomar", "sesion": "pagina-de-beto-0001"}, pk=primera["servidor_id"])
    otra = cot.sincronizar(ana, idc, a.pk, 1, {**a.datos, "project_title": "Campo 2"}, "grupo_itt")
    assert otra["servidor_id"] != primera["servidor_id"] and Cotizacion.objects.get(pk=primera["servidor_id"]).proyecto == "Campo"


# 10. El PDF del editor solo usa las imágenes guardadas en la cotización (o las propias) -------------------------------------------

def test_pdf_con_servidor_id_ajeno_no_descubre_fotos(cliente_de, ana, beto, settings):
    sha = crear_foto(ana, 23, settings)                                             # de Ana, en otra cotización suya
    rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([foto(sha)], titulo="Cualquiera"), "grupo_itt")
    cualquiera = _cot(ana)                                                          # sin imágenes; Beto la usa de llave
    datos = {**cualquiera.datos, "items": [{"title": "x", "unit": "Lote", "quantity": 1, "unit_price": 1}], "images": [{"path": f"captura:{sha}", "caption": "A"}]}
    r = post(cliente_de(beto), "cotizaciones_pdf", {"datos": datos, "servidor_id": str(cualquiera.pk)})
    assert r.status_code == 200 and r["X-Imagenes-Pendientes"] == "1"
