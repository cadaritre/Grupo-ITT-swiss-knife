"""CAD: biblioteca compartida de planos, versiones, bloqueo, borradores, papelera, roles, «Abrir en CAD», ZIP, editor y motor nativo."""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import textwrap
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from itt_herramientas.cad import editor, nativo, servicio
from itt_herramientas.models import BorradorPlano, Plano, VersionPlano

from .conftest import subir_tus, url

PLANTILLAS = Path(servicio.__file__).resolve().parent / "plantillas"
DXF = (PLANTILLAS / "vacio.dxf").read_bytes()
DWG = (PLANTILLAS / "vacio.dwg").read_bytes()
SESION_A = "sesion-de-ana-0001"
SESION_B = "sesion-de-beto-002"


@pytest.fixture
def dibujante(crear_usuario, roles):
    u = crear_usuario("dana")
    u.first_name, u.last_name, u.email = "Ana", "Dibujo", "ana@empresa.mx"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def otro(crear_usuario, roles):
    u = crear_usuario("beto")
    u.first_name = "Beto"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def supervisora(crear_usuario, roles):
    u = crear_usuario("sara")
    roles.conceder(u, "herramientas", "supervision")
    return u


@pytest.fixture
def plano(dibujante):
    return servicio.crear_vacio(dibujante, "Predio Norte", "Agrícola Norte", "dwg")


def guardar(cliente, p, cuerpo=DWG, revision=None, sesion=SESION_A):
    return cliente.post(url("cad_guardar", pk=p.pk), cuerpo, content_type="application/octet-stream",
                        HTTP_X_TV_SESION=sesion, HTTP_X_TV_REVISION=str(p.revision if revision is None else revision))


def bloqueo(cliente, p, accion, sesion=SESION_A):
    return cliente.post(url("cad_bloqueo", pk=p.pk), {"accion": accion, "sesion": sesion}, content_type="application/json")


# -- Formatos y nombres ------------------------------------------------------------------------------------------------------------

def test_detecta_dwg_y_dxf_por_el_contenido():
    assert servicio.detectar_formato(DWG[:8192]) == "dwg"
    assert servicio.detectar_formato(DXF[:8192]) == "dxf"
    assert servicio.detectar_formato(b"AutoCAD Binary DXF\r\n\x1a\x00") == "dxf"
    assert servicio.detectar_formato(b"%PDF-1.7\n") is None
    assert servicio.detectar_formato(b"\x00\x01\x02 SECTION") is None


def test_nombre_limpio_quita_extension_y_caracteres_invalidos():
    assert servicio.nombre_limpio("  Plano: lote/3.DWG ") == "Plano lote 3"
    assert servicio.nombre_limpio("") == "Plano sin nombre"
    assert len(servicio.nombre_limpio("x" * 400)) == servicio.NOMBRE_MAX


# -- Roles ----------------------------------------------------------------------------------------------------------------------

def test_el_rol_cad_existe_y_entra_en_los_roles_de_herramientas():
    from itt_herramientas.modulo import MODULO, ROLES_DE_HERRAMIENTAS

    assert "cad" in {r.clave for r in MODULO.roles}
    assert "cad" in ROLES_DE_HERRAMIENTAS
    entrada = next(e for e in MODULO.menu if e.clave == "cad")
    assert entrada.grupo == "Gabinete" and set(entrada.roles) == {"cad", "admin", "supervision"}
    assert "itt_herramientas.cad.servicio.limpiar" in MODULO.limpiezas
    assert "itt_herramientas.cad.servicio.al_eliminar_cuenta" in MODULO.al_eliminar_cuenta


def test_sin_rol_de_cad_no_entra(cliente_de, usuaria, plano):
    cl = cliente_de(usuaria)                                                 # solo cotizaciones
    assert cl.get(url("cad")).status_code == 403
    assert cl.get(url("cad_plano", pk=plano.pk)).status_code == 403
    assert cl.get(url("cad_archivo", pk=plano.pk)).status_code == 403


def test_supervision_ve_y_descarga_pero_no_edita(cliente_de, supervisora, plano):
    cl = cliente_de(supervisora)
    r = cl.get(url("cad"))
    assert r.status_code == 200 and "Predio Norte" in r.content.decode()
    assert "Nuevo plano" not in r.content.decode()
    assert cl.get(url("cad_archivo", pk=plano.pk)).status_code == 200
    pagina = cl.get(url("cad_plano", pk=plano.pk)).content.decode()
    config = json.loads(pagina.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    assert config["puede_editar"] is False and config["rol_lectura"] is True
    assert cl.post(url("cad_nuevo"), {"nombre": "x", "formato": "dwg"}).status_code == 403
    assert guardar(cl, plano).status_code == 403
    assert bloqueo(cl, plano, "tomar").status_code == 403
    assert cl.post(url("cad_eliminar", pk=plano.pk)).status_code == 403


def test_inicio_muestra_la_tarjeta_de_cad_a_quien_la_tiene_y_a_supervision(cliente_de, dibujante, supervisora, usuaria):
    assert "Planos DWG y DXF" in cliente_de(dibujante).get(url("inicio")).content.decode()
    assert "Planos DWG y DXF" in cliente_de(supervisora).get(url("inicio")).content.decode()
    assert "Planos DWG y DXF" not in cliente_de(usuaria).get(url("inicio")).content.decode()


# -- Crear, subir, abrir ------------------------------------------------------------------------------------------------------------

def test_nuevo_plano_crea_la_version_1_y_abre_el_editor(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    r = cl.post(url("cad_nuevo"), {"nombre": "Lote 7.dwg", "proyecto": "  Rancho   Sur ", "formato": "dxf"})
    p = Plano.objects.get()
    assert r.status_code == 302 and r["Location"] == url("cad_plano", pk=p.pk)
    assert (p.nombre, p.proyecto, p.formato, p.revision) == ("Lote 7", "Rancho Sur", "dxf", 1)
    assert (p.autor_nombre, p.autor_correo, p.modificado_por_nombre) == ("Ana Dibujo", "ana@empresa.mx", "Ana Dibujo")
    v = VersionPlano.objects.get(plano=p)
    assert v.numero == 1 and v.nota == "Plano nuevo" and servicio.ruta_version(v).read_bytes() == DXF


def test_subir_un_dwg_por_tus_lo_agrega_a_la_biblioteca(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    r, _ = subir_tus(cl, DWG, "herramientas.cad_plano", "Plano del predio.dwg", proyecto="Agrícola Norte")
    assert r.status_code == 204, r.content
    p = Plano.objects.get()
    assert (p.nombre, p.formato, p.proyecto, p.origen["tipo"]) == ("Plano del predio", "dwg", "Agrícola Norte", "subida")


def test_la_zona_de_subida_declara_el_proyecto(cliente_de, dibujante):
    """Sin declararlo, la subida no manda el campo y el plano quedaba sin proyecto (lo destapó el ensayo por la pila real)."""
    html = cliente_de(dibujante).get(url("cad")).content.decode()
    assert "data-meta=" in html and "&quot;proyecto&quot;" in html


def test_subir_un_archivo_que_no_es_cad_o_con_extension_falsa_se_rechaza(cliente_de, dibujante):
    cl = cliente_de(dibujante)
    r, _ = subir_tus(cl, b"%PDF-1.7 hola", "herramientas.cad_plano", "plano.dwg")
    assert r.status_code == 422 and "DWG ni un DXF" in r.content.decode()
    r, _ = subir_tus(cl, DXF, "herramientas.cad_plano", "plano.dwg")
    assert r.status_code == 422 and "es un DXF" in r.content.decode()
    assert not Plano.objects.exists()


def test_la_subida_es_solo_de_quien_edita(cliente_de, supervisora):
    r, _ = subir_tus(cliente_de(supervisora), DWG, "herramientas.cad_plano", "x.dwg")
    assert r.status_code == 404


def test_lista_busca_filtra_y_marca_quien_edita(cliente_de, dibujante, otro):
    a = servicio.crear_vacio(dibujante, "Casa habitación", "Familia Pérez", "dwg")
    servicio.crear_vacio(otro, "Bodega", "Agrícola Norte", "dxf")
    servicio.bloqueo(a.pk, otro, SESION_B, "tomar")
    cl = cliente_de(dibujante)
    html = cl.get(url("cad"), {"q": "pérez"}).content.decode()
    assert "Casa habitación" in html and "Bodega" not in html and "Beto" in html            # «Editando: Beto»
    assert "Bodega" in cl.get(url("cad"), {"formato": "dxf"}).content.decode()
    assert "Casa habitación" not in cl.get(url("cad"), {"autor": otro.pk}).content.decode()
    assert "Bodega" in cl.get(url("cad"), {"proyecto": "agrícola norte"}).content.decode()
    assert cl.get(url("cad"), {"orden": "tamano", "desde": "no-es-fecha"}).status_code == 200


def test_lista_vacia_ofrece_crear_el_primero(cliente_de, dibujante):
    html = cliente_de(dibujante).get(url("cad")).content.decode()
    assert "Todavía no hay planos." in html and "Nuevo plano" in html


def test_descargar_la_vigente_una_version_y_su_nombre(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    bloqueo(cl, plano, "tomar")
    assert guardar(cl, plano, DWG).status_code == 200
    r = cl.get(url("cad_archivo", pk=plano.pk), {"descargar": "1"})
    assert r.status_code == 200 and r["Content-Type"] == "image/vnd.dwg" and 'filename="Predio Norte.dwg"' in r["Content-Disposition"]
    r = cl.get(url("cad_archivo", pk=plano.pk), {"descargar": "1", "version": "1"})
    assert "versi%C3%B3n%201" in r["Content-Disposition"]
    assert cl.get(url("cad_archivo", pk=plano.pk), {"version": "9"}).status_code == 404


def test_la_pagina_del_editor_lo_incrusta_con_el_canal_de_control_y_el_lugar_para_extensiones(cliente_de, dibujante, plano, editor_falso):
    """`?embed=1&automation=1`: el puente de guardado y el canal de control oficial (solo del mismo origen) para el módulo cad/control.js."""
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    config = json.loads(html.split('id="cad-config" type="application/json">')[1].split("</script>")[0])
    assert config["urls"]["editor"] == f"/cad/editor/{editor.version()}/index.html?embed=1&automation=1"
    assert 'id="cad-extensiones"' in html and "herramientas/cad/control.js" in html


# -- Bloqueo ---------------------------------------------------------------------------------------------------------------------

def test_el_segundo_abre_en_solo_lectura_y_ve_quien_edita(cliente_de, dibujante, otro, plano):
    assert bloqueo(cliente_de(dibujante), plano, "tomar").json()["mio"] is True
    d = bloqueo(cliente_de(otro), plano, "tomar", SESION_B).json()
    assert d["mio"] is False and d["quien"] == "Ana Dibujo"


def test_la_misma_cuenta_en_otra_ventana_lo_ve_como_otra_ventana(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    bloqueo(cl, plano, "tomar")
    d = bloqueo(cl, plano, "tomar", "otra-ventana-0003").json()
    assert d["mio"] is False and d["otra_ventana"] is True
    assert bloqueo(cl, plano, "forzar", "otra-ventana-0003").json()["mio"] is True     # la propia cuenta sí puede «editar aquí»


def test_el_bloqueo_caduca_sin_latido(cliente_de, dibujante, otro, plano):
    bloqueo(cliente_de(dibujante), plano, "tomar")
    Plano.objects.filter(pk=plano.pk).update(bloqueo_latido=timezone.now() - timedelta(seconds=servicio.BLOQUEO_SEGUNDOS + 5))
    assert bloqueo(cliente_de(otro), plano, "tomar", SESION_B).json()["mio"] is True


def test_solo_quien_administra_toma_el_control_y_el_otro_lo_pierde(cliente_de, dibujante, otro, administradora, plano):
    bloqueo(cliente_de(dibujante), plano, "tomar")
    r = bloqueo(cliente_de(otro), plano, "forzar", SESION_B)
    assert r.status_code == 403
    d = bloqueo(cliente_de(administradora), plano, "forzar", "sesion-admin-0004").json()
    assert d["mio"] is True and d["tomado_de"] == "Ana Dibujo"
    latido = bloqueo(cliente_de(dibujante), plano, "latido").json()
    assert latido["perdido"] is True and latido["quien"]
    r = guardar(cliente_de(dibujante), plano)
    assert r.status_code == 409 and r.json()["motivo"] == "bloqueo"


def test_soltar_libera_el_plano(cliente_de, dibujante, otro, plano):
    cl = cliente_de(dibujante)
    bloqueo(cl, plano, "tomar")
    bloqueo(cl, plano, "soltar")
    assert bloqueo(cliente_de(otro), plano, "tomar", SESION_B).json()["mio"] is True


def test_una_sesion_invalida_se_rechaza(cliente_de, dibujante, plano):
    assert bloqueo(cliente_de(dibujante), plano, "tomar", "x").status_code == 422


# -- Guardar, conflicto y copia ------------------------------------------------------------------------------------------------

def test_guardar_crea_una_version_nueva_con_quien_la_guardo(cliente_de, dibujante, otro, plano):
    cl = cliente_de(otro)
    bloqueo(cl, plano, "tomar", SESION_B)
    r = guardar(cl, plano, DXF, sesion=SESION_B)
    assert r.status_code == 200, r.content
    d = r.json()
    assert d["revision"] == 2 and d["formato"] == "dxf" and [v["numero"] for v in d["versiones"]] == [2, 1]
    plano.refresh_from_db()
    assert (plano.revision, plano.formato, plano.modificado_por_nombre, plano.autor_nombre) == (2, "dxf", "Beto", "Ana Dibujo")


def test_nunca_se_pisa_un_cambio_ajeno(cliente_de, dibujante, otro, plano):
    a, b = cliente_de(dibujante), cliente_de(otro)
    bloqueo(a, plano, "tomar")
    assert guardar(a, plano).status_code == 200                              # versión 2
    bloqueo(a, plano, "soltar")
    bloqueo(b, plano, "tomar", SESION_B)
    r = guardar(b, plano, revision=1, sesion=SESION_B)                       # editaba la 1
    assert r.status_code == 409 and r.json()["motivo"] == "cambio" and "copia" in r.json()["error"]
    plano.refresh_from_db()
    assert plano.revision == 2
    r = b.post(url("cad_copia", pk=plano.pk), DXF, content_type="application/octet-stream")
    assert r.status_code == 201
    copia = Plano.objects.get(pk=r.json()["id"])
    assert copia.nombre == "Predio Norte (copia)" and copia.origen == {"tipo": "copia", "id": str(plano.pk), "nombre": "Predio Norte"}
    assert copia.proyecto == plano.proyecto and copia.autor_nombre == "Beto"


def test_guardar_sin_bloqueo_libre_lo_toma_y_guarda(cliente_de, dibujante, plano):
    assert guardar(cliente_de(dibujante), plano).status_code == 200


def test_guardar_un_archivo_que_no_es_cad_o_demasiado_grande(cliente_de, dibujante, plano, settings):
    cl = cliente_de(dibujante)
    assert guardar(cl, plano, b"no es un plano").status_code == 422
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"bytes": 1000}}
    r = guardar(cl, plano, DWG)
    assert r.status_code == 413 and "pesa más" in r.json()["error"]


def test_guardar_un_plano_eliminado_se_ofrece_como_copia(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    servicio.eliminar(plano, dibujante)
    r = guardar(cl, plano)
    assert r.status_code == 409 and r.json()["motivo"] == "eliminado"


def test_se_conservan_las_versiones_mas_recientes(cliente_de, dibujante, plano, settings):
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"versiones": 3}}
    cl = cliente_de(dibujante)
    for _ in range(4):
        plano.refresh_from_db()
        assert guardar(cl, plano).status_code == 200
    assert list(VersionPlano.objects.filter(plano=plano).values_list("numero", flat=True)) == [5, 4, 3]
    assert not (servicio.carpeta(plano) / "v1.dwg").exists()


def test_la_biblioteca_llena_recorta_versiones_viejas_y_luego_se_niega(dibujante, plano, settings):
    tam = len(DWG)
    for _ in range(7):
        servicio.guardar(plano.pk, dibujante, SESION_A, Plano.objects.get(pk=plano.pk).revision, _temporal(DWG))
    assert VersionPlano.objects.filter(plano=plano).count() == 8
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"biblioteca_bytes": tam * 7}}
    servicio.guardar(plano.pk, dibujante, SESION_A, 8, _temporal(DWG))
    assert VersionPlano.objects.filter(plano=plano).count() <= 7 and VersionPlano.objects.filter(plano=plano, numero=9).exists()
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"biblioteca_bytes": tam * 3}}
    with pytest.raises(servicio.ErrorUsuario, match="límite"):
        servicio.guardar(plano.pk, dibujante, SESION_A, 9, _temporal(DWG))
    assert VersionPlano.objects.filter(plano=plano).count() == servicio.VERSIONES_MINIMAS


def _temporal(contenido: bytes) -> Path:
    import uuid

    p = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    p.write_bytes(contenido)
    return p


def test_restaurar_una_version_crea_una_nueva(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    bloqueo(cl, plano, "tomar")
    guardar(cl, plano, DXF)
    r = cl.post(url("cad_restaurar_version", pk=plano.pk, numero=1), {"sesion": SESION_A}, content_type="application/json")
    assert r.status_code == 200 and r.json()["revision"] == 3
    v = VersionPlano.objects.get(plano=plano, numero=3)
    assert v.nota == "Restaurada de la versión 1" and v.formato == "dwg" and servicio.ruta_version(v).read_bytes() == DWG


def test_no_se_restaura_mientras_otra_persona_edita(cliente_de, dibujante, otro, plano):
    bloqueo(cliente_de(otro), plano, "tomar", SESION_B)
    guardar(cliente_de(otro), plano, DXF, sesion=SESION_B)
    r = cliente_de(dibujante).post(url("cad_restaurar_version", pk=plano.pk, numero=1), {"sesion": SESION_A}, content_type="application/json")
    assert r.status_code == 409 and "Beto" in r.json()["error"]


# -- Borradores de recuperación ---------------------------------------------------------------------------------------------------

def test_borrador_se_guarda_se_ofrece_y_se_borra_al_guardar(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    r = cl.post(url("cad_borrador", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_REVISION="1")
    assert r.status_code == 200
    assert VersionPlano.objects.filter(plano=plano).count() == 1                      # NO es una versión
    estado = cl.get(url("cad_estado", pk=plano.pk)).json()
    assert estado["borrador"]["revision_base"] == 1
    html = cl.get(url("cad_plano", pk=plano.pk)).content.decode()
    assert '"borrador": {' in html
    assert cl.get(url("cad_archivo", pk=plano.pk), {"borrador": "1"}).getvalue() == DXF
    bloqueo(cl, plano, "tomar")
    guardar(cl, plano)
    assert not BorradorPlano.objects.exists()
    assert not list((servicio.carpeta(plano) / "borradores").glob("*"))


def test_el_borrador_es_de_cada_persona(cliente_de, dibujante, otro, plano):
    cliente_de(dibujante).post(url("cad_borrador", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_REVISION="1")
    assert cliente_de(otro).get(url("cad_archivo", pk=plano.pk), {"borrador": "1"}).status_code == 404
    assert cliente_de(otro).get(url("cad_estado", pk=plano.pk)).json()["borrador"] is None


def test_descartar_el_borrador(cliente_de, dibujante, plano):
    cl = cliente_de(dibujante)
    cl.post(url("cad_borrador", pk=plano.pk), DXF, content_type="application/octet-stream", HTTP_X_TV_REVISION="1")
    assert cl.post(url("cad_borrador_descartar", pk=plano.pk)).status_code == 200
    assert not BorradorPlano.objects.exists()


# -- Renombrar, duplicar, papelera --------------------------------------------------------------------------------------------------

def test_renombrar_y_duplicar(cliente_de, otro, plano):
    cl = cliente_de(otro)
    cl.post(url("cad_renombrar", pk=plano.pk), {"nombre": "Predio Norte final", "proyecto": "Agrícola Norte"})
    plano.refresh_from_db()
    assert plano.nombre == "Predio Norte final"
    r = cl.post(url("cad_duplicar", pk=plano.pk))
    copia = Plano.objects.exclude(pk=plano.pk).get()
    assert r["Location"] == url("cad_plano", pk=copia.pk) and copia.nombre == "Predio Norte final (copia)" and copia.cuenta_id == otro.pk


def test_eliminar_lo_propio_si_lo_ajeno_no(cliente_de, dibujante, otro, plano):
    r = cliente_de(otro).post(url("cad_eliminar", pk=plano.pk))
    plano.refresh_from_db()
    assert plano.eliminado is None and r.status_code == 302
    cliente_de(dibujante).post(url("cad_eliminar", pk=plano.pk))
    plano.refresh_from_db()
    assert plano.eliminado is not None and plano.eliminado_por_nombre == "Ana Dibujo"
    assert cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).status_code == 404
    assert "Predio Norte" not in cliente_de(dibujante).get(url("cad")).content.decode()


def test_quien_administra_elimina_cualquiera_y_vacia_la_papelera(cliente_de, administradora, otro, plano):
    cl = cliente_de(administradora)
    cl.post(url("cad_eliminar", pk=plano.pk))
    html = cl.get(url("cad_papelera")).content.decode()
    assert "Predio Norte" in html and "Vaciar papelera" in html
    assert cliente_de(otro).post(url("cad_vaciar"), {"confirmacion": "vaciar"}).status_code == 403
    cl.post(url("cad_vaciar"), {"confirmacion": "no"})
    assert Plano.objects.filter(pk=plano.pk).exists()
    cl.post(url("cad_vaciar"), {"confirmacion": "vaciar"})
    assert not Plano.objects.filter(pk=plano.pk).exists() and not servicio.carpeta(plano).exists()


def test_restaurar_de_la_papelera(cliente_de, dibujante, otro, plano):
    servicio.eliminar(plano, dibujante)
    assert "Predio Norte" not in cliente_de(otro).get(url("cad_papelera")).content.decode()     # ni la creó ni la eliminó
    cliente_de(otro).post(url("cad_restaurar", pk=plano.pk))
    plano.refresh_from_db()
    assert plano.eliminado is not None
    cliente_de(dibujante).post(url("cad_restaurar", pk=plano.pk))
    plano.refresh_from_db()
    assert plano.eliminado is None


def test_limpiar_borra_la_papelera_vencida_y_los_borradores_viejos(dibujante, otro, plano):
    otro_plano = servicio.crear_vacio(otro, "Bodega", "", "dxf")
    servicio.eliminar(plano, dibujante)
    Plano.objects.filter(pk=plano.pk).update(eliminado=timezone.now() - timedelta(days=31))
    servicio.guardar_borrador(otro_plano, otro, 1, _temporal(DXF))
    BorradorPlano.objects.update(actualizado=timezone.now() - timedelta(days=31))
    assert servicio.limpiar() >= 2
    assert not Plano.objects.filter(pk=plano.pk).exists() and not servicio.carpeta(plano).exists()
    assert Plano.objects.filter(pk=otro_plano.pk).exists() and not BorradorPlano.objects.exists()


# -- Cuenta eliminada ----------------------------------------------------------------------------------------------------------------

def test_al_eliminar_la_cuenta_se_sueltan_sus_planos_y_sus_borradores(dibujante, plano):
    servicio.bloqueo(plano.pk, dibujante, SESION_A, "tomar")
    servicio.guardar_borrador(plano, dibujante, 1, _temporal(DXF))
    texto = servicio.al_eliminar_cuenta(dibujante.pk)
    assert "liberaron 1 plano" in texto and "1 borrador" in texto and len(texto) <= 300
    plano.refresh_from_db()
    assert plano.bloqueo_cuenta_id is None and not BorradorPlano.objects.exists()
    assert servicio.al_eliminar_cuenta(dibujante.pk) is None                                  # idempotente
    assert Plano.objects.filter(pk=plano.pk).exists()                                        # el plano es de la empresa


# -- Búsqueda, ZIP, Drive ------------------------------------------------------------------------------------------------------------

def test_la_busqueda_de_la_paleta_encuentra_planos(cliente_de, supervisora, plano):
    r = cliente_de(supervisora).get(url("buscar"), {"q": "norte"}).json()
    assert any(x["titulo"] == "Predio Norte" and x["url"] == url("cad_plano", pk=plano.pk) for x in r["resultados"])


def test_zip_de_planos_con_su_version_vigente(cliente_de, dibujante, otro, plano):
    b = servicio.crear_vacio(otro, "Predio Norte", "", "dxf")
    cl = cliente_de(dibujante)
    r = cl.post(url("cad_zip"), {"elementos": [str(plano.pk), str(b.pk)]}, content_type="application/json")
    assert r.status_code == 202 and r.json()["terminada"], r.content
    z = zipfile.ZipFile(io.BytesIO(b"".join(cl.get(r.json()["resultado"]["url"]).streaming_content)))
    assert sorted(z.namelist()) == ["Predio Norte.dwg", "Predio Norte.dxf"]
    assert r.json()["resultado"]["mensaje"].startswith("ZIP listo: 2 planos")


def test_un_lote_de_cad_no_lleva_documentos_de_otras_herramientas(cliente_de, dibujante):
    r = cliente_de(dibujante).post(url("cad_zip"), {"elementos": [["reporte", "6b4e1d2a-6a9e-4b1e-9d0e-2c7f1b9a3c11"]]}, content_type="application/json")
    assert r.status_code == 422


def test_los_lotes_de_otras_herramientas_no_llevan_planos(cliente_de, reportera, plano):
    from itt_herramientas.comun import zips

    with pytest.raises(zips.LoteInvalido):
        zips.armar(zips.nuevo_token(), reportera.pk, [["plano", str(plano.pk)]], "reportes")


def test_enviar_un_plano_a_drive_sin_conexion_lo_dice(cliente_de, dibujante, plano):
    r = cliente_de(dibujante).post(url("cad_drive", pk=plano.pk))
    assert r.status_code == 409 and "Conecta tu Google Drive" in r.json()["error"]


# -- «Abrir en CAD» desde otras herramientas ------------------------------------------------------------------------------------

def test_abrir_en_cad_desde_el_convertidor(cliente_de, crear_usuario, roles):
    from itt_herramientas.convertidor import servicio as conv
    from itt_herramientas.models import Conversion

    u = crear_usuario("carla")
    roles.conceder(u, "herramientas", "convertidor", "cad")
    c = Conversion.objects.create(cuenta_id=u.pk, nombre="poligono.kmz", formato="kmz", bytes=10, ruta_origen="x", estado="listo")
    salida = conv.carpeta(c) / "salida" / "poligono_convertido.dxf"
    salida.parent.mkdir(parents=True)
    salida.write_bytes(DXF)
    c.salida_ruta, c.salida_formato = str(salida.relative_to(conv.raiz())), "dxf"
    c.save()
    html = cliente_de(u).get(url("convertidor_banco", pk=c.pk)).content.decode()
    assert "Abrir en CAD" in html
    r = cliente_de(u).post(url("cad_desde_convertidor", pk=c.pk))
    p = Plano.objects.get()
    assert r["Location"] == url("cad_plano", pk=p.pk)
    assert p.nombre == "poligono" and p.origen == {"tipo": "convertidor", "id": str(c.pk), "nombre": "poligono.kmz"}
    assert VersionPlano.objects.get(plano=p).nota == "Desde el convertidor"


def test_abrir_en_cad_no_aparece_sin_el_rol(cliente_de, convertidora):
    from itt_herramientas.models import Conversion

    c = Conversion.objects.create(cuenta_id=convertidora.pk, nombre="p.kmz", formato="kmz", bytes=1, ruta_origen="x", estado="listo",
                                  salida_ruta="convertidor/x.dxf", salida_formato="dxf")
    assert "Abrir en CAD" not in cliente_de(convertidora).get(url("convertidor_banco", pk=c.pk)).content.decode()
    assert cliente_de(convertidora).post(url("cad_desde_convertidor", pk=c.pk)).status_code == 403


def test_abrir_en_cad_desde_triangulacion_y_croquis(cliente_de, crear_usuario, roles):
    from itt_herramientas.comun import capturas
    from itt_herramientas.models import Croquis, Triangulacion
    from itt_herramientas.triangulacion import servicio as tri

    u = crear_usuario("tito")
    roles.conceder(u, "herramientas", "triangulacion", "croquis", "cad")
    t = Triangulacion.objects.create(cuenta_id=u.pk, nombre="puntos.dxf", bytes=1, ruta_origen="x", estado="listo")
    salida = tri.raiz() / "triangulacion" / "tin.dxf"
    salida.parent.mkdir(parents=True)
    salida.write_bytes(DXF)
    t.salida_ruta, t.salida_nombre = str(salida.relative_to(tri.raiz())), "puntos_TIN.dxf"
    t.save()
    assert "Abrir en CAD" in cliente_de(u).get(url("triangulacion_banco", pk=t.pk)).content.decode()
    r = cliente_de(u).post(url("cad_desde_triangulacion", pk=t.pk))
    assert Plano.objects.get().nombre == "puntos_TIN" and r.status_code == 302
    k = Croquis.objects.create(cuenta_id=u.pk, datos={"cliente": "Rancho Sur"}, titulo="Croquis lote 3")
    ruta = capturas.raiz() / "croquis" / "c.dxf"
    ruta.parent.mkdir(parents=True)
    ruta.write_bytes(DXF)
    k.salida_ruta, k.salida_formato, k.salida_nombre = str(ruta.relative_to(capturas.raiz())), "dxf", "CROQUIS_LOTE_3.dxf"
    k.save()
    r = cliente_de(u).post(url("cad_desde_croquis", pk=k.pk), HTTP_ACCEPT="application/json")
    assert r.status_code == 201
    p = Plano.objects.get(pk=r.json()["id"])
    assert (p.nombre, p.proyecto, p.origen["tipo"]) == ("CROQUIS_LOTE_3", "Rancho Sur", "croquis")


def test_abrir_en_cad_sin_salida_lo_explica(cliente_de, crear_usuario, roles):
    from itt_herramientas.models import Croquis

    u = crear_usuario("tito")
    roles.conceder(u, "herramientas", "croquis", "cad")
    k = Croquis.objects.create(cuenta_id=u.pk, datos={}, titulo="Sin DXF")
    r = cliente_de(u).post(url("cad_desde_croquis", pk=k.pk), HTTP_ACCEPT="application/json")
    assert r.status_code == 422 and "DXF" in r.json()["error"]


# -- Editor servido por la app (desarrollo y pruebas; en producción, Caddy con las mismas cabeceras) -------------------------------

@pytest.fixture
def editor_falso(tmp_path, settings):
    base = tmp_path / "editor" / editor.version()
    (base / "worker_pkg").mkdir(parents=True)
    (base / "index.html").write_text("<!doctype html><title>x</title>")
    (base / "OpenCADStudio-0123456789abcdef_bg.wasm").write_bytes(b"\0asm\1\0\0\0")
    (base / "worker_pkg" / "ocs_web_worker.js").write_text("export default 1;")
    (tmp_path / "secreto.txt").write_text("no")
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = str(tmp_path / "editor")
    return base


def test_el_editor_se_sirve_con_su_csp_y_cache(cliente_de, dibujante, editor_falso):
    cl = cliente_de(dibujante)
    v = editor.version()
    r = cl.get(f"/cad/editor/{v}/index.html")
    assert r.status_code == 200 and r["Cache-Control"] == "no-cache" and r["X-Frame-Options"] == "SAMEORIGIN"
    csp = r["Content-Security-Policy"]
    assert "'wasm-unsafe-eval'" in csp and "frame-ancestors 'self'" in csp and "connect-src 'self'" in csp and "https:" not in csp
    r = cl.get(f"/cad/editor/{v}/OpenCADStudio-0123456789abcdef_bg.wasm")
    assert r["Content-Type"] == "application/wasm" and "immutable" in r["Cache-Control"]
    r = cl.get(f"/cad/editor/{v}/worker_pkg/ocs_web_worker.js")
    assert r["Content-Type"].startswith("text/javascript") and "immutable" not in r["Cache-Control"] and "'wasm-unsafe-eval'" in r["Content-Security-Policy"]
    for malo in (f"/cad/editor/{v}/../secreto.txt", f"/cad/editor/{v}/%2e%2e/secreto.txt", "/cad/editor/v1999.1/index.html", f"/cad/editor/{v}/no-existe.js"):
        r = cl.get(malo)
        assert r.status_code == 404 and r["Cache-Control"] == "no-store", malo


def test_la_csp_de_las_demas_paginas_no_cambia(cliente_de, dibujante, plano):
    csp = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk))["Content-Security-Policy"]
    assert "wasm-unsafe-eval" not in csp and "frame-ancestors 'none'" in csp


def test_sin_editor_preparado_la_pagina_lo_dice_y_ofrece_descargar(cliente_de, dibujante, plano, settings):
    settings.HERRAMIENTAS_CAD_EDITOR_DIR = ""
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    assert "El editor no está disponible" in html and "Descargar el plano" in html


def test_upstream_fijado_con_sha256():
    d = editor.upstream()
    assert d["version"].startswith("v20") and len(d["web"]["sha256"]) == 64
    assert all(len(n["sha256"]) == 64 for n in d["nativo"].values())


def test_el_service_worker_no_guarda_el_editor_en_cache():
    from itt_herramientas.offline import sw

    contenido = sw.contenido()
    assert "/cad/editor" not in contenido and ".wasm" not in contenido


def test_preparar_editor_comprueba_el_sha256_y_pone_nuestra_pagina(tmp_path):
    """Con un ZIP «oficial» de mentira: un hash distinto se rechaza; el bueno se extrae sin la página, el SW ni la analítica de arriba."""
    import hashlib
    import importlib.util

    spec = importlib.util.spec_from_file_location("preparar", Path(editor.__file__).parent / "preparar.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    zip_ruta = tmp_path / "web.zip"
    with zipfile.ZipFile(zip_ruta, "w") as z:
        z.writestr("app/index.html", "<script type=module>import init, * as b from '/app/OpenCADStudio-0123456789abcdef.js';"
                                     "await init({ module_or_path: '/app/OpenCADStudio-fedcba9876543210_bg.wasm' });</script>"
                                     "<script src=\"https://analitica.example/a.js\"></script>")
        z.writestr("app/OpenCADStudio-0123456789abcdef.js", "export default async () => ({});")
        z.writestr("app/OpenCADStudio-fedcba9876543210_bg.wasm", b"\0asm" + b"\1" * 5000)
        z.writestr("app/ocs-parse-worker.js", "x")
        z.writestr("app/worker_pkg/ocs_web_worker.js", "x")
        z.writestr("app/worker_pkg/ocs_web_worker_bg.wasm", b"\0asm")
        z.writestr("app/sw.js", "self.addEventListener('fetch', () => {})")
        z.writestr("app/videos.json", '[{"id": "abc"}]')
    datos = prep.datos()
    falso = {**datos, "web": {"archivo": "web.zip", "sha256": hashlib.sha256(zip_ruta.read_bytes()).hexdigest(), "bytes": zip_ruta.stat().st_size}}
    prep.datos = lambda: falso
    final = prep.preparar_editor(tmp_path / "salida", tmp_path / "cache", zip_ruta)
    index = (final / "index.html").read_text()
    assert "OpenCADStudio-0123456789abcdef.js" in index and "analitica" not in index and "<script type=\"module\" src=\"arranque.js\">" in index
    assert not (final / "sw.js").exists() and (final / "videos.json").read_text().strip() == "[]" and (final / "arranque.js").exists()
    assert (final / "OpenCADStudio-fedcba9876543210_bg.wasm.gz").exists()
    prep.verificar(tmp_path / "salida")
    falso["web"]["sha256"] = "0" * 64
    (tmp_path / "cache" / "web.zip").unlink(missing_ok=True)
    with pytest.raises(SystemExit, match="no coincide"):                      # un ZIP dado a mano que no es el fijado no se usa (ni se baja otro)
        prep.obtener({"archivo": "web.zip", "sha256": "0" * 64, "bytes": falso["web"]["bytes"]}, tmp_path / "cache2", zip_ruta)


def test_preparar_baja_del_repositorio_privado_con_gh_y_comprueba_el_hash(tmp_path, monkeypatch):
    """La versión corregida del editor vive en un repositorio privado: se baja con la sesión de gh de la Mac y se comprueba igual."""
    import hashlib
    import importlib.util

    spec = importlib.util.spec_from_file_location("preparar", Path(editor.__file__).parent / "preparar.py")
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    contenido = b"zip de la version corregida"
    llamadas = []

    def gh(cmd, **kw):
        llamadas.append(cmd)
        Path(cmd[cmd.index("-D") + 1], cmd[cmd.index("-p") + 1]).write_bytes(contenido)
        return type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(prep.subprocess, "run", gh)
    monkeypatch.setattr(prep.shutil, "which", lambda n: "/opt/homebrew/bin/gh")
    pieza = {"archivo": "OpenCADStudio-tv1-web.zip", "sha256": hashlib.sha256(contenido).hexdigest(), "bytes": len(contenido),
             "fuente": {"tipo": "gh", "repo": "cadaritre/OpenCADStudio-TresVizo", "tag": "tv-2026.45.1"}}
    ruta = prep.obtener(pieza, tmp_path / "cache")
    assert ruta.read_bytes() == contenido
    assert llamadas == [["gh", "release", "download", "tv-2026.45.1", "-R", "cadaritre/OpenCADStudio-TresVizo", "-p", "OpenCADStudio-tv1-web.zip",
                         "-D", llamadas[0][-1]]]
    ruta.unlink()
    with pytest.raises(SystemExit, match="no coincide"):
        prep.obtener({**pieza, "sha256": "1" * 64}, tmp_path / "cache")
    with pytest.raises(SystemExit, match="fuente inválida"):
        prep.obtener({**pieza, "fuente": {"tipo": "gh", "repo": "x; rm -rf /", "tag": "v1"}}, tmp_path / "cache3")


# -- Motor nativo (impresión y conversión), con un motor de prueba que habla el mismo protocolo --------------------------------------

MOTOR = textwrap.dedent('''\
    #!{python}
    import json, os, signal, sys
    if sys.argv[1:] != ["--serve"]:
        sys.exit(2)
    print(json.dumps({{"ok": True, "ready": True, "version": "2026.45"}}), flush=True)
    abierto = None
    for linea in sys.stdin:
        r = json.loads(linea)
        op = r.get("op")
        if op == "open":
            abierto = r["path"]
            print(json.dumps({{"ok": True, "request_id": r["request_id"], "result": {{}}, "state": {{"document_id": 2}}}}), flush=True)
        elif "ENORME" in (abierto or ""):
            os.kill(os.getpid(), signal.SIGKILL)
        elif op == "plot":
            assert r["document_id"] == 2
            open(r["path"], "wb").write(b"%PDF-1.3\\n" + json.dumps(r).encode())
            print(json.dumps({{"ok": True, "request_id": r["request_id"], "result": {{"pages": 1, "page_sizes": [[279.4, 215.9]]}}}}), flush=True)
        elif op == "save":
            if "PERDIDA" in abierto and not r.get("allow_lossy"):
                print(json.dumps({{"ok": False, "code": "lossy_conversion_not_acknowledged", "error": "x"}}), flush=True)
                continue
            open(r["path"], "wb").write(open(abierto, "rb").read())
            print(json.dumps({{"ok": True, "request_id": r["request_id"], "result": {{"dropped_on_save": 2 if r.get("allow_lossy") else 0}}}}), flush=True)
''')


@pytest.fixture
def motor(tmp_path, settings):
    ruta = tmp_path / "OpenCADStudio"
    ruta.write_text(MOTOR.format(python=sys.executable))
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
    settings.HERRAMIENTAS_CAD_NATIVO = str(ruta)
    return ruta


def test_imprimir_pdf_por_la_cola(cliente_de, supervisora, plano, motor):
    cl = cliente_de(supervisora)                                         # quien supervisa también imprime
    r = cl.post(url("cad_imprimir", pk=plano.pk), {"papel": "ISO_A3_(297.00_x_420.00_MM)", "orientacion": "portrait", "escala": "1:500", "area": "modelo", "grosores": "1"})
    assert r.status_code == 202, r.content
    d = r.json()
    assert d["estado"] == "completada", d
    salida = cl.get(d["resultado"]["url"])
    contenido = b"".join(salida.streaming_content)
    pedido = json.loads(contenido.split(b"\n", 1)[1])
    assert contenido.startswith(b"%PDF") and 'filename="Predio Norte.pdf"' in salida["Content-Disposition"]
    assert (pedido["paper"], pedido["orientation"], pedido["scale"], pedido["layout"], pedido["lineweights"]) == ("ISO_A3_(297.00_x_420.00_MM)", "portrait", "1:500", "Model", True)


def test_la_salida_es_solo_de_quien_la_pidio(cliente_de, supervisora, dibujante, plano, motor):
    d = cliente_de(supervisora).post(url("cad_imprimir", pk=plano.pk), {"area": "presentaciones", "escala": "ajustar"}).json()
    assert cliente_de(dibujante).get(d["resultado"]["url"]).status_code == 404


def test_imprimir_valida_las_opciones(cliente_de, dibujante, plano, motor):
    cl = cliente_de(dibujante)
    assert cl.post(url("cad_imprimir", pk=plano.pk), {"papel": "Servilleta"}).status_code == 422
    assert "1:100" in cl.post(url("cad_imprimir", pk=plano.pk), {"escala": "otra", "escala_otra": "cien"}).json()["error"]


def test_imprimir_sin_motor_no_esta_disponible(cliente_de, dibujante, plano, settings):
    settings.HERRAMIENTAS_CAD_NATIVO = ""
    assert cliente_de(dibujante).post(url("cad_imprimir", pk=plano.pk), {}).status_code == 503
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    assert 'id="cad-boton-imprimir" hidden' in html and 'id="cad-imprimir"' not in html


def test_un_plano_que_no_cabe_en_memoria_falla_con_un_mensaje_claro(dibujante, motor, settings):
    p = servicio.crear_vacio(dibujante, "ENORME", "", "dwg")
    servicio.carpeta(p).joinpath("v1.dwg").rename(servicio.carpeta(p) / "v1-ENORME.dwg")
    VersionPlano.objects.filter(plano=p).update(ruta=str((servicio.carpeta(p) / "v1-ENORME.dwg").relative_to(servicio.raiz())))
    with pytest.raises(nativo.DemasiadoGrande):
        from itt_herramientas.cad import salidas

        salidas.imprimir(p.pk, 1, dibujante.pk, {})


def test_convertir_avisa_la_perdida_y_la_permite_con_confirmacion(cliente_de, dibujante, motor):
    p = servicio.crear_vacio(dibujante, "Con PERDIDA", "", "dwg")
    servicio.carpeta(p).joinpath("v1.dwg").rename(servicio.carpeta(p) / "v1-PERDIDA.dwg")
    VersionPlano.objects.filter(plano=p).update(ruta=str((servicio.carpeta(p) / "v1-PERDIDA.dwg").relative_to(servicio.raiz())))
    cl = cliente_de(dibujante)
    d = cl.post(url("cad_convertir", pk=p.pk), {"formato": "dxf"}).json()
    assert d["resultado"]["perdida"] is True and "url" not in d["resultado"]
    d = cl.post(url("cad_convertir", pk=p.pk), {"formato": "dxf", "permitir_perdida": "1"}).json()
    assert "Se dejaron fuera 2 elementos" in d["resultado"]["mensaje"]
    r = cl.get(d["resultado"]["url"])
    assert r["Content-Type"] == "image/vnd.dxf" and 'filename="Con PERDIDA.dxf"' in r["Content-Disposition"]
    assert cl.post(url("cad_convertir", pk=p.pk), {"formato": "dwg"}).status_code == 422            # ya es DWG
