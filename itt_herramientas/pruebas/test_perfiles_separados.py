"""Cada perfil corporativo es un espacio de documentos aparte (17-11-2026): listas, búsqueda, Supervisión, papelera, avisos, sincronización
y ZIP solo con el perfil activo; 404 entre perfiles para quien no puede usar el otro; folios por perfil (también en paralelo); «Copiar a
<otro perfil>»; cambios pendientes del otro perfil; y la migración 0017 de ida y vuelta con datos como los de producción."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import date, timedelta

import pytest
from django.db import connection
from django.utils import timezone

from itt_herramientas.comun import documentos, perfiles
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.croquis import servicio as crq
from itt_herramientas.models import AvisoDocumento, BorradorRecuperacion, ContadorFolio, Cotizacion, Croquis, Perfil, Reporte, VersionDocumento
from itt_herramientas.reportes import servicio as rep

from .conftest import url
from .test_croquis import datos as datos_croquis
from .test_reportes import datos as datos_reporte

pytestmark = pytest.mark.django_db
HOY = timezone.localdate().year
ROLES = ("cotizaciones", "reportes", "croquis")


@pytest.fixture
def ana(crear_usuario, roles):
    """Trabaja solo en Grupo ITT (sin el permiso «Cambiar perfil corporativo»)."""
    u = crear_usuario("ana")
    roles.conceder(u, "herramientas", *ROLES)
    return u


@pytest.fixture
def dora(crear_usuario, roles):
    """Puede usar los dos perfiles."""
    u = crear_usuario("dora")
    roles.conceder(u, "herramientas", *ROLES, "cambiar_perfil")
    return u


@pytest.fixture
def sofia(crear_usuario, roles):
    """Supervisa, sin el permiso de cambiar de perfil (solo Grupo ITT)."""
    u = crear_usuario("sofia")
    roles.conceder(u, "herramientas", "supervision")
    return u


def post(c, nombre, cuerpo, **kw):
    return c.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


def _datos_cot(perfil="grupo_itt", **campos):
    d = perfiles.nueva_cotizacion(Perfil.objects.get(clave=perfil)).to_dict()
    d.update(items=[], images=[], **campos)
    return d


def sinc(c, tipo, datos, servidor=None, base=None, perfil=None, idc=None):
    cuerpo = {"id_cliente": str(idc or uuid.uuid4()), "servidor_id": servidor, "revision_base": base, "datos": datos}
    if perfil is not None:
        cuerpo["perfil"] = perfil
    r = post(c, f"{tipo}_sincronizar", cuerpo)
    assert r.status_code == 200, r.content
    return r.json()


def cambiar(c, clave):
    r = c.post(url("perfil_activo"), {"perfil": clave}, HTTP_ACCEPT="application/json")
    assert r.status_code == 200 and r.json()["perfil"] == clave
    return r


def ids(c, nombre, clave):
    return {f["id"] for f in c.get(url(nombre)).json()[clave]}


@pytest.fixture
def espacios(cliente_de, ana, dora):
    """Grupo ITT: una cotización, un reporte y un croquis de Ana. TresVizo: lo mismo, de Dora."""
    ca, cd = cliente_de(ana), cliente_de(dora)
    itt = {"cot": sinc(ca, "cotizaciones", _datos_cot(project_title="Deslinde ITT", client_name="Cliente Norte")),
           "rep": sinc(ca, "reportes", datos_reporte([], titulo="Visita ITT", cliente="Cliente Norte")),
           "crq": sinc(ca, "croquis", datos_croquis(title="Croquis ITT"))}
    cambiar(cd, "tresvizo")
    tv = {"cot": sinc(cd, "cotizaciones", _datos_cot("tresvizo", project_title="Deslinde TV", client_name="Cliente Sur")),
          "rep": sinc(cd, "reportes", datos_reporte([], titulo="Visita TV", cliente="Cliente Sur")),
          "crq": sinc(cd, "croquis", datos_croquis(title="Croquis TV"))}
    return ca, cd, itt, tv


# -- Listas, búsqueda y sincronización -------------------------------------------------------------------------------------------

def test_cada_perfil_ve_solo_sus_documentos(espacios):
    ca, cd, itt, tv = espacios
    for d in itt.values():
        assert d["perfil"] == "grupo_itt" and "aviso" not in d
    for d in tv.values():
        assert d["perfil"] == "tresvizo"
    for nombre, clave, k in (("cotizaciones_lista", "cotizaciones", "cot"), ("reportes_lista", "reportes", "rep"), ("croquis_lista", "croquis", "crq")):
        assert ids(ca, nombre, clave) == {itt[k]["servidor_id"]}                     # solo Grupo ITT: nunca ve lo de TresVizo
        assert ids(cd, nombre, clave) == {tv[k]["servidor_id"]}                      # Dora en TresVizo
    assert ca.get(url("cotizaciones_lista")).json()["perfil"] == "grupo_itt" and cd.get(url("reportes_lista")).json()["perfil"] == "tresvizo"
    cambiar(cd, "grupo_itt")                                                         # al cambiar, el otro espacio
    assert ids(cd, "cotizaciones_lista", "cotizaciones") == {itt["cot"]["servidor_id"]}
    assert ids(cd, "croquis_lista", "croquis") == {itt["crq"]["servidor_id"]}


def test_la_busqueda_y_las_sugerencias_de_clientes_son_del_perfil_activo(espacios):
    ca, cd, itt, tv = espacios
    assert [r["titulo"] for r in ca.get(url("buscar"), {"q": "Deslinde"}).json()["resultados"]] == [f"{itt['cot']['folio']} · Deslinde ITT"]
    assert [r["titulo"] for r in cd.get(url("buscar"), {"q": "Visita"}).json()["resultados"]] == ["Visita TV"]
    assert ca.get(url("reportes_contexto")).json()["clientes"] == ["Cliente Norte"]
    assert cd.get(url("reportes_contexto")).json()["clientes"] == ["Cliente Sur"]


def test_el_contexto_dice_los_perfiles_que_la_cuenta_puede_usar(espacios):
    ca, cd, *_ = espacios
    assert ca.get(url("cotizaciones_contexto")).json()["perfiles"] == [{"clave": "grupo_itt", "nombre": "Grupo ITT", "activo": True}]
    assert [(o["clave"], o["activo"]) for o in cd.get(url("croquis_contexto")).json()["perfiles"]] == [("grupo_itt", False), ("tresvizo", True)]


def test_la_lista_incremental_lleva_un_cursor_por_perfil_y_los_vigentes_del_perfil(espacios, dora):
    ca, cd, itt, tv = espacios
    todo = cd.get(url("cotizaciones_lista")).json()
    cursor = todo["cursor"]
    sinc(ca, "cotizaciones", _datos_cot(project_title="Otra ITT"))                   # cambia Grupo ITT: TresVizo no ve nada nuevo
    nada = cd.get(url("cotizaciones_lista"), {"desde": cursor}).json()
    assert nada["cotizaciones"] == [] and nada["eliminados"] == []
    # Se borra para siempre una de Grupo ITT: los vigentes que recibe TresVizo son solo los suyos.
    documentos.destruir(Cotizacion.objects.get(pk=itt["cot"]["servidor_id"]))
    r = cd.get(url("cotizaciones_lista"), {"desde": cursor}).json()
    assert r["vigentes"] == [tv["cot"]["servidor_id"]]


def test_supervision_papelera_y_avisos_son_del_perfil_activo(espacios, cliente_de, sofia, dora, ana, roles):
    ca, cd, itt, tv = espacios
    cs = cliente_de(sofia)
    html = cs.get(url("supervision")).content.decode()
    assert "Deslinde ITT" in html and "Deslinde TV" not in html and "Visita TV" not in html
    assert 'data-itt="espacio-perfil"' not in html                                   # sin el permiso: no hay selector que confunda
    roles.conceder(sofia, "herramientas", "cambiar_perfil")
    cambiar(cs, "tresvizo")
    html = cs.get(url("supervision")).content.decode()
    assert "Deslinde TV" in html and "Deslinde ITT" not in html
    assert "Perfil: TresVizo" in html and "Cambiar a Grupo ITT" in html
    # Papelera: cada quien manda lo suyo; Ana (Grupo ITT) no ve lo de TresVizo.
    assert ca.delete(url("reportes_detalle", pk=itt["rep"]["servidor_id"])).status_code == 200
    assert cd.delete(url("reportes_detalle", pk=tv["rep"]["servidor_id"])).status_code == 200
    papelera_ana = ca.get(url("papelera")).content.decode()
    assert "Visita ITT" in papelera_ana and "Visita TV" not in papelera_ana
    papelera_dora = cd.get(url("papelera")).content.decode()
    assert "Visita TV" in papelera_dora and "Visita ITT" not in papelera_dora
    # Avisos: el de un documento de TresVizo espera a que Dora vuelva a TresVizo.
    documentos.avisar(dora.pk, "cotizacion", tv["cot"]["servidor_id"], "Aviso de TresVizo")
    assert [a["texto"] for a in cd.get(url("cotizaciones_avisos")).json()["avisos"]] == ["Aviso de TresVizo"]
    cambiar(cd, "grupo_itt")
    assert cd.get(url("cotizaciones_avisos")).json()["avisos"] == []
    assert AvisoDocumento.objects.filter(cuenta_id=dora.pk, visto__isnull=True).count() == 1


def test_un_zip_no_mezcla_perfiles(espacios):
    ca, cd, itt, tv = espacios
    r = post(ca, "cotizaciones_zip", {"elementos": [["cotizacion", tv["cot"]["servidor_id"]]]})
    assert r.status_code == 422 and "perfil activo" in r.json()["error"]
    assert post(ca, "cotizaciones_zip", {"elementos": [["cotizacion", itt["cot"]["servidor_id"]], ["cotizacion", tv["cot"]["servidor_id"]]]}).status_code == 202


# -- Abrir por su dirección -------------------------------------------------------------------------------------------------------

def test_quien_no_puede_usar_el_otro_perfil_recibe_404_en_todo(espacios, cliente_de, sofia, settings):
    ca, cd, itt, tv = espacios
    pk_c, pk_r, pk_q = tv["cot"]["servidor_id"], tv["rep"]["servidor_id"], tv["crq"]["servidor_id"]
    for respuesta in (ca.get(url("cotizaciones_detalle", pk=pk_c)), ca.delete(url("cotizaciones_detalle", pk=pk_c)),
                      post(ca, "cotizaciones_duplicar", {}, pk=pk_c), ca.get(url("cotizaciones_versiones", pk=pk_c)),
                      ca.get(url("cotizaciones_editable", pk=pk_c)), post(ca, "cotizaciones_restaurar_version", {"sesion": "pagina-ana-0001"}, pk=pk_c, vid=1),
                      ca.get(url("cotizaciones_recuperacion", pk=pk_c)), post(ca, "cotizaciones_copiar_perfil", {"perfil": "grupo_itt"}, pk=pk_c),
                      ca.get(url("reportes_detalle", pk=pk_r)), post(ca, "reportes_pdf", {}, pk=pk_r), ca.get(url("reportes_descargar", pk=pk_r)),
                      ca.get(url("croquis_detalle", pk=pk_q)), ca.get(url("croquis_editable", pk=pk_q)),
                      cliente_de(sofia).get(url("supervision_cotizacion", pk=pk_c)), cliente_de(sofia).get(url("supervision_reporte", pk=pk_r))):
        assert respuesta.status_code == 404, respuesta.request
    # El bloqueo de un documento ajeno: como si no existiera (no lo toma).
    r = post(ca, "cotizaciones_bloqueo", {"accion": "tomar", "sesion": "pagina-ana-0001"}, pk=pk_c)
    assert r.status_code == 200 and r.json()["eliminado"] is True
    assert Cotizacion.objects.get(pk=pk_c).bloqueo_cuenta_id is None
    # Un documento de la papelera del otro perfil: también 404 (no 410).
    Cotizacion.objects.filter(pk=pk_c).update(eliminado=timezone.now())
    assert ca.get(url("cotizaciones_detalle", pk=pk_c)).status_code == 404


def test_quien_puede_usar_los_dos_ve_de_que_perfil_es_y_puede_cambiar(espacios, cliente_de, dora, roles):
    ca, cd, itt, tv = espacios
    cambiar(cd, "grupo_itt")
    detalle = cd.get(url("cotizaciones_detalle", pk=tv["cot"]["servidor_id"])).json()
    assert detalle["otro_perfil"] == {"clave": "tresvizo", "nombre": "TresVizo"} and detalle["perfil"] == "tresvizo"
    assert cd.get(url("cotizaciones_detalle", pk=itt["cot"]["servidor_id"])).json()["otro_perfil"] is None
    roles.conceder(dora, "herramientas", "supervision")
    html = cd.get(url("supervision_cotizacion", pk=tv["cot"]["servidor_id"])).content.decode()
    assert "Es de TresVizo" in html and "Cambiar a TresVizo" in html and "Deslinde TV" not in html     # no muestra el documento mezclado
    r = cd.post(url("perfil_activo"), {"perfil": "tresvizo", "siguiente": url("supervision_cotizacion", pk=tv["cot"]["servidor_id"])})
    assert r.status_code == 302
    assert "Deslinde TV" in cd.get(r["Location"]).content.decode()


# -- Folios por perfil ------------------------------------------------------------------------------------------------------------

def test_cada_perfil_tiene_su_serie_de_folios(espacios):
    ca, cd, itt, tv = espacios
    assert itt["cot"]["folio"] == f"COT-{HOY}-0001" and tv["cot"]["folio"] == f"COT-{HOY}-0001"
    assert sinc(cd, "cotizaciones", _datos_cot("tresvizo"))["folio"] == f"COT-{HOY}-0002"
    assert sinc(ca, "cotizaciones", _datos_cot())["folio"] == f"COT-{HOY}-0002"
    assert ContadorFolio.objects.get(perfil="tresvizo", anio=HOY).ultimo == 2 and ContadorFolio.objects.get(perfil="grupo_itt", anio=HOY).ultimo == 2


def test_la_base_no_admite_el_mismo_folio_dos_veces_en_un_perfil(espacios):
    from django.db import IntegrityError, transaction

    ca, cd, itt, tv = espacios
    otra = sinc(ca, "cotizaciones", _datos_cot())
    with pytest.raises(IntegrityError), transaction.atomic():
        Cotizacion.objects.filter(pk=otra["servidor_id"]).update(folio=itt["cot"]["folio"])


def test_importar_un_editable_conserva_su_folio_si_esta_libre_en_el_perfil(espacios, dora):
    ca, cd, itt, tv = espacios
    archivo = json.dumps(_datos_cot(quote_number=f"COT-{HOY}-0001")).encode()        # 0001 ya existe en TresVizo (y en Grupo ITT)
    c = cot.importar_editable(dora, archivo, "tresvizo")
    assert c.folio == f"COT-{HOY}-0002" and c.folio_original == f"COT-{HOY}-0001"
    c = cot.importar_editable(dora, json.dumps(_datos_cot(quote_number=f"COT-{HOY}-0040")).encode(), "grupo_itt")
    assert c.folio == f"COT-{HOY}-0040" and c.perfil == "grupo_itt"
    assert cot.asignar_folio("grupo_itt") == f"COT-{HOY}-0041" and cot.asignar_folio("tresvizo") == f"COT-{HOY}-0003"


@pytest.mark.django_db(transaction=True)
def test_folios_en_paralelo_en_los_dos_perfiles_nunca_se_repiten():
    """Seis hilos por perfil a la vez, cada uno en su conexión: cada serie sale completa y sin repetidos."""
    folios = {"grupo_itt": [], "tresvizo": []}
    errores = []

    def asignar(perfil):
        try:
            for _ in range(5):
                folios[perfil].append(cot.asignar_folio(perfil))
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)
        finally:
            connection.close()

    hilos = [threading.Thread(target=asignar, args=(p,)) for p in ("grupo_itt", "tresvizo") for _ in range(6)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert not errores
    for perfil in folios:
        assert sorted(folios[perfil]) == [f"COT-{HOY}-{n:04d}" for n in range(1, 31)]
        assert ContadorFolio.objects.get(perfil=perfil, anio=HOY).ultimo == 30


def test_folios_repetidos_y_asignar_folio_nuevo_dentro_del_perfil(espacios, cliente_de, propietario):
    ca, cd, itt, tv = espacios
    otra = sinc(cd, "cotizaciones", _datos_cot("tresvizo", project_title="Repetida TV"))
    Cotizacion.objects.filter(pk=otra["servidor_id"]).update(folio=tv["cot"]["folio"], folio_repetido=True)
    assert [g["folio"] for g in cot.repetidos("tresvizo")] == [tv["cot"]["folio"]] and cot.repetidos("grupo_itt") == []
    cp = cliente_de(propietario)
    assert "Folios repetidos" not in cp.get(url("supervision")).content.decode()      # el propietario está en Grupo ITT
    cambiar(cp, "tresvizo")
    assert "Repetida TV" in cp.get(url("supervision")).content.decode()
    assert cp.post(url("supervision_asignar_folio", pk=otra["servidor_id"])).status_code == 302
    c = Cotizacion.objects.get(pk=otra["servidor_id"])
    assert c.folio == f"COT-{HOY}-0003" and not c.folio_repetido and c.perfil == "tresvizo"          # 0002 ya salió de la serie


# -- El perfil de un documento es fijo; lo pendiente de otro perfil sube igual -----------------------------------------------------

def test_editar_restaurar_o_duplicar_nunca_cambia_el_perfil(espacios, dora):
    ca, cd, itt, tv = espacios
    cambiar(cd, "grupo_itt")                                                         # Dora ahora trabaja en Grupo ITT
    r = sinc(cd, "cotizaciones", _datos_cot("tresvizo", project_title="Editada"), tv["cot"]["servidor_id"], tv["cot"]["revision"])
    assert r["resultado"] == "actualizada" and r["perfil"] == "tresvizo" and r["folio"] == tv["cot"]["folio"]
    c = Cotizacion.objects.get(pk=tv["cot"]["servidor_id"])
    assert c.perfil == "tresvizo" and c.proyecto == "Editada"
    v = VersionDocumento.objects.get(documento_id=c.pk, revision=1)
    r = post(cd, "cotizaciones_restaurar_version", {"sesion": ""}, pk=c.pk, vid=v.pk)
    assert Cotizacion.objects.get(pk=c.pk).perfil == "tresvizo"
    dup = post(cd, "cotizaciones_duplicar", {}, pk=c.pk).json()
    assert dup["perfil"] == "tresvizo" and dup["folio"] == f"COT-{HOY}-0002"         # el duplicado, en el espacio y la serie del original


def test_un_borrador_nuevo_sube_a_su_perfil_aunque_la_cuenta_ya_este_en_otro(espacios, ana):
    ca, cd, itt, tv = espacios
    cambiar(cd, "grupo_itt")
    r = sinc(cd, "reportes", datos_reporte([], titulo="Hecho en TresVizo sin señal"), perfil="tresvizo")
    assert r["perfil"] == "tresvizo" and Reporte.objects.get(pk=r["servidor_id"]).perfil == "tresvizo"
    r = sinc(cd, "cotizaciones", _datos_cot("tresvizo"), perfil="tresvizo")
    assert r["folio"] == f"COT-{HOY}-0002"                                           # de la serie de TresVizo
    # Quien no puede usar TresVizo: su borrador entra en su perfil activo (nunca se pierde).
    r = sinc(ca, "croquis", datos_croquis(title="De Ana"), perfil="tresvizo")
    assert r["perfil"] == "grupo_itt"


@pytest.mark.parametrize("caso", ["al_dia", "base_vieja_sin_choque", "base_vieja_con_choque", "con_sesion_y_otro_editando", "reintento_por_id_cliente"])
def test_lo_pendiente_de_un_perfil_que_ya_no_se_puede_usar_queda_como_copia_y_el_original_no_se_toca(espacios, ana, dora, caso):
    """Ana editó sin señal un reporte de TresVizo cuando todo era de la empresa (o antes de perder el permiso). Para ella ese perfil no
    existe: en ningún caso se aplica, se fusiona, se lee ni se toma el bloqueo del original; lo suyo queda como copia en su perfil activo y
    se le dice dónde. Quien editaba en TresVizo no recibe aviso (no hay nada de Ana en su espacio)."""
    ca, cd, itt, tv = espacios
    pk = tv["rep"]["servidor_id"]
    antes = Reporte.objects.get(pk=pk)
    base, sesion, idc = tv["rep"]["revision"], "", uuid.uuid4()
    if caso != "al_dia":                                                              # TresVizo siguió editando
        sinc(cd, "reportes", datos_reporte([], titulo="Visita TV", cliente="Cliente Sur, oficina"), pk, 1)
        antes = Reporte.objects.get(pk=pk)
    if caso == "con_sesion_y_otro_editando":
        post(cd, "reportes_bloqueo", {"accion": "tomar", "sesion": "pagina-dora-0001"}, pk=pk)
        antes = Reporte.objects.get(pk=pk)
        sesion = "pagina-ana-0001"
    datos = datos_reporte([], titulo="Visita TV" if caso == "base_vieja_sin_choque" else "Corregida por Ana", cliente="Cliente Sur, Ana")
    if caso == "reintento_por_id_cliente":                                           # el borrador lo creó Ana y se perdió la respuesta
        Reporte.objects.filter(pk=pk).update(cuenta_id=ana.pk, id_cliente=idc)
        antes = Reporte.objects.get(pk=pk)
        cuerpo = {"id_cliente": str(idc), "servidor_id": None, "revision_base": None, "datos": datos, "perfil": "tresvizo"}
    else:
        cuerpo = {"id_cliente": str(idc), "servidor_id": pk, "revision_base": base, "datos": datos, "sesion": sesion, "perfil": "tresvizo"}
    r = post(ca, "reportes_sincronizar", cuerpo)
    assert r.status_code == 200, r.content
    r = r.json()
    assert r["resultado"] == "copia" and r["perfil"] == "grupo_itt" and r["copia_de"] is None and "datos" not in r
    assert r["aviso"] == "Ya no tienes acceso a ese perfil: tus cambios se guardaron como copia en Grupo ITT."
    assert "Visita TV" not in r["conflicto"] and "un documento de otro perfil" in r["conflicto"]        # nada del original
    copia = Reporte.objects.get(pk=r["servidor_id"])
    assert copia.perfil == "grupo_itt" and copia.cuenta_id == ana.pk and copia.cliente == "Cliente Sur, Ana" and copia.copia_de_id is None
    despues = Reporte.objects.get(pk=pk)
    assert (despues.revision, despues.titulo, despues.cliente, despues.cambio, despues.bloqueo_cuenta_id) == (
        antes.revision, antes.titulo, antes.cliente, antes.cambio, antes.bloqueo_cuenta_id)
    assert not BorradorRecuperacion.objects.filter(documento_id=pk).exists()
    assert not AvisoDocumento.objects.filter(documento_id=pk).exists() and not AvisoDocumento.objects.filter(cuenta_id=dora.pk).exists()
    assert ana.pk not in despues.autorizados
    # Reenviar lo mismo (respuesta perdida) no duplica la copia.
    again = post(ca, "reportes_sincronizar", cuerpo).json()
    assert again["servidor_id"] == r["servidor_id"] and Reporte.objects.filter(perfil="grupo_itt", cuenta_id=ana.pk, conflicto__startswith="Copia").count() == 1


def test_una_cotizacion_pendiente_de_otro_perfil_queda_como_copia_con_folio_de_grupo_itt(espacios, ana):
    ca, cd, itt, tv = espacios
    r = sinc(ca, "cotizaciones", _datos_cot(project_title="Cambio de Ana"), tv["cot"]["servidor_id"], tv["cot"]["revision"], perfil="tresvizo")
    assert r["resultado"] == "copia" and r["perfil"] == "grupo_itt" and r["folio"] == f"COT-{HOY}-0002" and "aviso" in r
    assert Cotizacion.objects.get(pk=tv["cot"]["servidor_id"]).proyecto == "Deslinde TV"


def test_un_borrador_nuevo_de_un_perfil_que_ya_no_se_puede_usar_dice_donde_quedo(espacios, ana, dora):
    ca, cd, itt, tv = espacios
    r = sinc(ca, "reportes", datos_reporte([], titulo="Hecho sin señal"), perfil="tresvizo")
    assert r["resultado"] == "creada" and r["perfil"] == "grupo_itt"
    assert r["aviso"] == "Ya no tienes acceso al perfil TresVizo: se guardó en Grupo ITT."
    assert "aviso" not in sinc(cd, "reportes", datos_reporte([], titulo="De Dora"), perfil="tresvizo")      # quien puede usarlo: sin aviso
    assert "aviso" not in sinc(ca, "reportes", datos_reporte([], titulo="Normal"), perfil="grupo_itt")


def test_las_sugerencias_de_cad_son_del_perfil_activo(espacios, ana, dora):
    from itt_herramientas.cad import servicio as cad

    assert "Cliente Sur" not in cad.sugerencias_proyecto(ana.pk, "grupo_itt") and "Cliente Norte" in cad.sugerencias_proyecto(ana.pk, "grupo_itt")
    assert "Cliente Sur" in cad.sugerencias_proyecto(dora.pk, "tresvizo") and "Cliente Norte" not in cad.sugerencias_proyecto(dora.pk, "tresvizo")


# -- Copiar a otro perfil ---------------------------------------------------------------------------------------------------------

def test_copiar_una_cotizacion_al_otro_perfil(espacios, dora, settings):
    ca, cd, itt, tv = espacios
    itt_perfil, tv_perfil = Perfil.objects.get(clave="grupo_itt"), Perfil.objects.get(clave="tresvizo")
    itt_perfil.banco, itt_perfil.clabe, itt_perfil.cuenta = "Banco Uno", "000000000000000001", "0001"
    tv_perfil.banco, tv_perfil.clabe, tv_perfil.cuenta = "Banco Dos", "000000000000000002", "0002"
    itt_perfil.save()
    tv_perfil.save()
    cambiar(cd, "grupo_itt")
    origen = sinc(cd, "cotizaciones", _datos_cot(project_title="Para copiar", notes="Nota escrita a mano"))
    Cotizacion.objects.filter(pk=origen["servidor_id"]).update(datos={**Cotizacion.objects.get(pk=origen["servidor_id"]).datos, "account": "Escrita a mano"})
    r = post(cd, "cotizaciones_copiar_perfil", {"perfil": "tresvizo"}, pk=origen["servidor_id"])
    assert r.status_code == 201, r.content
    copia = r.json()
    assert copia["perfil"] == "tresvizo" and copia["perfil_nombre"] == "TresVizo" and copia["folio"] == f"COT-{HOY}-0002"
    assert copia["datos"]["quote_number"] == copia["folio"] and copia["datos"]["project_title"] == "Para copiar"
    assert copia["datos"]["bank"] == "Banco Dos" and copia["datos"]["clabe"] == "000000000000000002"      # los de fábrica, del otro perfil
    assert copia["datos"]["account"] == "Escrita a mano"                                                # lo escrito a mano se respeta
    assert copia["otro_perfil"] == {"clave": "tresvizo", "nombre": "TresVizo"}
    versiones = VersionDocumento.objects.filter(documento_id=copia["id"])
    assert versiones.count() == 1 and versiones.get().nota == f"Copiada de «{origen['folio']}» (Grupo ITT)"
    original = Cotizacion.objects.get(pk=origen["servidor_id"])
    assert original.perfil == "grupo_itt" and original.folio == origen["folio"] and original.revision == 1
    assert ids(cd, "cotizaciones_lista", "cotizaciones") == {itt["cot"]["servidor_id"], origen["servidor_id"]}   # no aparece en Grupo ITT
    assert post(cd, "cotizaciones_copiar_perfil", {"perfil": "grupo_itt"}, pk=origen["servidor_id"]).status_code == 409   # ya es de ese perfil


def test_copiar_reporte_y_croquis_y_quien_no_puede_usar_el_otro_perfil(espacios, dora, ana, settings):
    from .test_reportes import crear_foto, foto

    ca, cd, itt, tv = espacios
    sha = crear_foto(ana, 4, settings)
    r = sinc(ca, "reportes", datos_reporte([foto(sha, "Fachada")], titulo="Con foto"))
    cambiar(cd, "grupo_itt")
    copia = post(cd, "reportes_copiar_perfil", {"perfil": "tresvizo"}, pk=r["servidor_id"]).json()
    assert copia["perfil"] == "tresvizo" and copia["titulo"] == "Con foto"
    cambiar(cd, "tresvizo")
    assert cd.get(url("reportes_foto", sha256=sha), {"doc": copia["id"]}).status_code == 200            # sus fotos siguen valiendo
    q = post(cd, "croquis_copiar_perfil", {"perfil": "grupo_itt"}, pk=tv["crq"]["servidor_id"])
    assert q.status_code == 201 and Croquis.objects.get(pk=q.json()["id"]).perfil == "grupo_itt"
    sin = post(ca, "reportes_copiar_perfil", {"perfil": "tresvizo"}, pk=itt["rep"]["servidor_id"])
    assert sin.status_code == 409 and sin.json()["sin_permiso"] is True
    assert Reporte.objects.filter(perfil="tresvizo", cuenta_id=ana.pk).count() == 0


# -- Cambiar de perfil ------------------------------------------------------------------------------------------------------------

def test_cambiar_de_perfil_por_json_y_sin_permiso(cliente_de, ana, dora):
    assert cliente_de(ana).post(url("perfil_activo"), {"perfil": "tresvizo"}, HTTP_ACCEPT="application/json").status_code == 403
    r = cambiar(cliente_de(dora), "tresvizo")
    assert r.json() == {"perfil": "tresvizo", "nombre": "TresVizo"} and r["Cache-Control"] == "private, no-store"


def test_las_paginas_de_campo_traen_las_direcciones_de_perfil(cliente_de, dora):
    for pagina in ("cotizaciones", "reportes", "croquis"):
        html = cliente_de(dora).get(url(pagina)).content.decode()
        assert f'data-url-perfil-activo="{url("perfil_activo")}"' in html and "data-url-copiar-perfil=" in html
        assert 'data-itt="espacio-perfil"' in html


# -- Migración 0017 ---------------------------------------------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_migracion_de_ida_y_vuelta_con_datos_como_los_de_produccion():
    """Grupo ITT 0001–0004 (con un par repetido de antes de compartir y una en la papelera) y TresVizo 0005, más una de TresVizo marcada
    solo porque Grupo ITT tenía su folio. Ningún folio cambia; Grupo ITT sigue en 0005 y TresVizo en 0006; de vuelta, los folios vuelven a
    ser únicos en la empresa."""
    from django.db.migrations.executor import MigrationExecutor

    antes, despues = [("herramientas", "0016_documentos_equipo")], [("herramientas", "0017_perfiles_separados")]
    ejecutor = MigrationExecutor(connection)
    ejecutor.migrate(antes)
    viejas = ejecutor.loader.project_state(antes).apps
    C = viejas.get_model("herramientas", "Cotizacion")
    K = viejas.get_model("herramientas", "ContadorFolio")
    t0 = timezone.now() - timedelta(days=30)

    def cot_vieja(folio, perfil, dias, **extra):
        return C.objects.create(cuenta_id=1, folio=folio, perfil=perfil, datos={"quote_number": folio}, creada=t0 + timedelta(days=dias), actualizada=t0, **extra)

    a1 = cot_vieja("COT-2026-0001", "grupo_itt", 1)
    a2 = cot_vieja("COT-2026-0002", "grupo_itt", 2)
    a2b = cot_vieja("COT-2026-0002", "grupo_itt", 3, folio_repetido=True)          # par repetido de antes de compartir
    a3 = cot_vieja("COT-2026-0003", "grupo_itt", 4)
    a4 = cot_vieja("COT-2026-0004", "grupo_itt", 5, eliminado=timezone.now())      # en la papelera
    t5 = cot_vieja("COT-2026-0005", "tresvizo", 6)
    t3 = cot_vieja("COT-2026-0003", "tresvizo", 7, folio_repetido=True)            # marcada solo por el folio de Grupo ITT
    K.objects.create(anio=2026, ultimo=5)
    ejecutor = MigrationExecutor(connection)
    ejecutor.loader.build_graph()
    ejecutor.migrate(despues)
    folios = dict(Cotizacion.objects.values_list("pk", "folio"))
    assert [folios[x.pk] for x in (a1, a2, a2b, a3, a4, t5, t3)] == ["COT-2026-0001", "COT-2026-0002", "COT-2026-0002", "COT-2026-0003",
                                                                      "COT-2026-0004", "COT-2026-0005", "COT-2026-0003"]
    repetidas = set(Cotizacion.objects.filter(folio_repetido=True).values_list("pk", flat=True))
    assert repetidas == {a2b.pk}                                                    # t3 ya no choca con nadie de su perfil
    assert dict(ContadorFolio.objects.values_list("perfil", "ultimo")) == {"grupo_itt": 4, "tresvizo": 5}
    assert cot.asignar_folio("grupo_itt", date(2026, 12, 1)) == "COT-2026-0005"
    assert cot.asignar_folio("tresvizo", date(2026, 12, 1)) == "COT-2026-0006"
    # De vuelta a 0016: únicos en la empresa (el más antiguo conserva el suyo), un contador por año, el mayor.
    ejecutor = MigrationExecutor(connection)
    ejecutor.loader.build_graph()
    ejecutor.migrate(antes)
    viejas = ejecutor.loader.project_state(antes).apps
    C, K = viejas.get_model("herramientas", "Cotizacion"), viejas.get_model("herramientas", "ContadorFolio")
    assert set(C.objects.filter(folio_repetido=True).values_list("pk", flat=True)) == {a2b.pk, t3.pk}
    assert dict(C.objects.values_list("pk", "folio"))[t3.pk] == "COT-2026-0003"
    assert list(K.objects.values_list("anio", "ultimo")) == [(2026, 6)]
    # Y otra vez hacia adelante: queda igual que la primera vez.
    ejecutor = MigrationExecutor(connection)
    ejecutor.loader.build_graph()
    ejecutor.migrate(despues)
    assert set(Cotizacion.objects.filter(folio_repetido=True).values_list("pk", flat=True)) == {a2b.pk}
    # El 0006 ya lo dio el contador (la asignación de arriba): ninguno de los dos lo vuelve a dar.
    assert dict(ContadorFolio.objects.values_list("perfil", "ultimo")) == {"grupo_itt": 6, "tresvizo": 6}


@pytest.mark.django_db(transaction=True)
def test_migracion_nunca_vuelve_a_dar_un_folio_borrado_para_siempre_ni_quita_el_folio_a_quien_lo_tomo():
    """El contador de la empresa llegó a 9: la 0009 (y la 0008) se borraron para siempre de la papelera y su PDF pudo llegar a un cliente;
    no se sabe de qué perfil eran, así que ninguno las vuelve a dar. Y el 0002 de Grupo ITT: quien lo conservaba se borró para siempre y una
    importación lo tomó después (sin marca); la más antigua que seguía marcada sigue marcada."""
    from django.db.migrations.executor import MigrationExecutor

    antes, despues = [("herramientas", "0016_documentos_equipo")], [("herramientas", "0017_perfiles_separados")]
    ejecutor = MigrationExecutor(connection)
    ejecutor.migrate(antes)
    viejas = ejecutor.loader.project_state(antes).apps
    C = viejas.get_model("herramientas", "Cotizacion")
    K = viejas.get_model("herramientas", "ContadorFolio")
    t0 = timezone.now() - timedelta(days=60)

    def cot_vieja(folio, perfil, dias, **extra):
        return C.objects.create(cuenta_id=1, folio=folio, perfil=perfil, datos={"quote_number": folio}, creada=t0 + timedelta(days=dias), actualizada=t0, **extra)

    cot_vieja("COT-2026-0001", "grupo_itt", 1)
    marcada = cot_vieja("COT-2026-0002", "grupo_itt", 2, folio_repetido=True)        # la que la conservaba ya no existe
    importada = cot_vieja("COT-2026-0002", "grupo_itt", 20)                          # la tomó después, legítimamente
    cot_vieja("COT-2026-0005", "tresvizo", 5)
    cot_vieja("COT-2026-0007", "tresvizo", 7)
    K.objects.create(anio=2026, ultimo=9)
    ejecutor = MigrationExecutor(connection)
    ejecutor.loader.build_graph()
    ejecutor.migrate(despues)
    assert Cotizacion.objects.get(pk=marcada.pk).folio_repetido and not Cotizacion.objects.get(pk=importada.pk).folio_repetido
    assert dict(ContadorFolio.objects.values_list("perfil", "ultimo")) == {"grupo_itt": 9, "tresvizo": 9}
    assert cot.asignar_folio("grupo_itt", date(2026, 12, 1)) == "COT-2026-0010" and cot.asignar_folio("tresvizo", date(2026, 12, 1)) == "COT-2026-0010"
