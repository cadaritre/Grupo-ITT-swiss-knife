"""CAD: proyectos y carpetas (docs/cad.md, «Proyectos y carpetas»): el modelo, la migración de las etiquetas «Proyecto o cliente», mover,
renombrar, la papelera de una carpeta, permisos, perfiles, el ZIP de una carpeta, subir una carpeta, las referencias externas resueltas
desde la biblioteca y cuántas consultas cuesta una biblioteca grande."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from itt_herramientas.cad import carpetas, servicio
from itt_herramientas.comun.errores import ErrorUsuario
from itt_herramientas.models import CarpetaCad, Plano, VersionPlano

from .conftest import subir_tus, url

PLANTILLAS = Path(servicio.__file__).resolve().parent / "plantillas"
DWG = (PLANTILLAS / "vacio.dwg").read_bytes()
DXF = (PLANTILLAS / "vacio.dxf").read_bytes()


@pytest.fixture
def ana(crear_usuario, roles):
    u = crear_usuario("ana")
    u.first_name, u.last_name = "Ana", "López"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def beto(crear_usuario, roles):
    u = crear_usuario("beto")
    u.first_name = "Beto"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def sara(crear_usuario, roles):
    u = crear_usuario("sara")
    roles.conceder(u, "herramientas", "supervision")
    return u


@pytest.fixture
def admin(crear_usuario, roles):
    u = crear_usuario("adm")
    roles.conceder(u, "herramientas", "admin")
    return u


def post(c, nombre, datos=None, **kw):
    return c.post(url(nombre, **kw), json.dumps(datos or {}), content_type="application/json", HTTP_ACCEPT="application/json")


def plano_en(usuario, nombre, carpeta=None, formato="dwg"):
    return servicio.crear_vacio(usuario, nombre, "", formato, carpeta=carpeta)


# -- El modelo -------------------------------------------------------------------------------------------------------------------

def test_proyectos_y_carpetas_anidadas_con_nombres_unicos_entre_hermanas(ana):
    obra = carpetas.crear(ana, "Obra Norte", cliente="Agrícola Norte")
    base = carpetas.crear(ana, "Base", obra)
    sub = carpetas.crear(ana, "Topografía/2026", base)                 # «/» no vale en un nombre de carpeta (ZIP, rutas de las referencias)
    assert (obra.padre, obra.cliente, base.padre, sub.nombre) == (None, "Agrícola Norte", obra, "Topografía 2026")
    with pytest.raises(ErrorUsuario, match="Ya hay una carpeta «base»"):
        carpetas.crear(ana, "base", obra)
    with pytest.raises(ErrorUsuario, match="Ya hay un proyecto"):
        carpetas.crear(ana, "OBRA NORTE")
    carpetas.crear(ana, "Base", sub)                                      # en otra carpeta, sí
    assert base.cliente == "" and carpetas.Arbol().ubicacion(sub.pk) == "Obra Norte › Base › Topografía 2026"
    p = plano_en(ana, "Planta", sub)
    assert (p.carpeta, p.proyecto) == (sub, "Obra Norte")


def test_profundidad_maxima(ana, settings, monkeypatch):
    monkeypatch.setattr(carpetas, "PROFUNDIDAD_MAX", 3)
    a = carpetas.crear(ana, "A")
    b = carpetas.crear(ana, "B", a)
    c = carpetas.crear(ana, "C", b)
    with pytest.raises(ErrorUsuario, match="hasta 3 niveles"):
        carpetas.crear(ana, "D", c)
    with pytest.raises(ErrorUsuario, match="más de 3 niveles"):
        carpetas.mover(ana, carpetas=[str(b.pk)], destino=str(carpetas.crear(ana, "X", carpetas.crear(ana, "Y")).pk))


def test_el_proyecto_escrito_de_antes_crea_o_usa_el_proyecto(ana):
    """Quien crea un plano con un proyecto escrito (croquis con su cliente, «Guardar como plantilla») lo deja en ese proyecto."""
    p1 = servicio.crear_vacio(ana, "Uno", "Rancho Sur", "dwg")
    p2 = servicio.crear_vacio(ana, "Dos", "rancho sur", "dwg")
    assert p1.carpeta_id == p2.carpeta_id and CarpetaCad.objects.get().nombre == "Rancho Sur" and p2.proyecto == "Rancho Sur"
    assert servicio.crear_vacio(ana, "Tres", "", "dwg").carpeta is None


# -- La migración de las etiquetas ---------------------------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_la_migracion_hace_un_proyecto_por_etiqueta_y_se_deshace():
    antes = [("herramientas", "0019_cad_plantillas")]
    despues = [("herramientas", "0020_cad_carpetas")]
    ejecutor = MigrationExecutor(connection)
    ejecutor.migrate(antes)
    try:
        estado = ejecutor.loader.project_state(antes)
        P = estado.apps.get_model("herramientas", "Plano")
        ahora = timezone.now()
        datos = [("A", "Obra Norte", 1), ("B", "obra  norte", 2), ("C", "Óbra Norte", 3), ("D", "Familia Pérez", 4), ("E", "", 5), ("F", "Planos/2026", 6),
                 ("G", "Plantillas", 7)]
        for nombre, proyecto, h in datos:
            P.objects.create(cuenta_id=1, nombre=nombre, proyecto=proyecto, formato="dwg", actualizado=ahora - timedelta(hours=h))
        P.objects.filter(nombre="D").update(eliminado=ahora)                 # también los de la papelera
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(despues)
        estado = ejecutor.loader.project_state(despues)
        P = estado.apps.get_model("herramientas", "Plano")
        K = estado.apps.get_model("herramientas", "CarpetaCad")
        assert sorted(K.objects.values_list("nombre", flat=True)) == ["Familia Pérez", "Obra Norte", "Planos 2026", "Plantillas"]
        assert all(k.padre_id is None for k in K.objects.all())
        obra = K.objects.get(nombre="Obra Norte")                             # la forma más reciente; las tres variantes, juntas
        assert set(P.objects.filter(carpeta=obra).values_list("nombre", flat=True)) == {"A", "B", "C"}
        assert set(P.objects.filter(carpeta=obra).values_list("proyecto", flat=True)) == {"Obra Norte"}
        assert P.objects.get(nombre="D").carpeta.nombre == "Familia Pérez" and P.objects.get(nombre="E").carpeta_id is None
        assert K.objects.get(nombre="Planos 2026").etiqueta == "Planos/2026"
        # Deshacerla deja los planos con su etiqueta (la del proyecto: «Planos/2026» quedó «Planos 2026»), sin carpetas.
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(antes)
        estado = ejecutor.loader.project_state(antes)
        P = estado.apps.get_model("herramientas", "Plano")
        assert P.objects.count() == 7 and P.objects.get(nombre="F").proyecto == "Planos 2026" and P.objects.get(nombre="A").proyecto == "Obra Norte"
    finally:
        ejecutor = MigrationExecutor(connection)
        ejecutor.migrate(ejecutor.loader.graph.leaf_nodes())


# -- Mover y renombrar ---------------------------------------------------------------------------------------------------------

def test_mover_planos_y_carpetas_y_el_proyecto_de_cada_plano(cliente_de, ana, beto):
    norte, sur = carpetas.crear(ana, "Norte"), carpetas.crear(ana, "Sur")
    base = carpetas.crear(ana, "Base", norte)
    p = plano_en(ana, "Planta", base)
    q = plano_en(beto, "Suelto")
    # Un plano que otra persona edita se mueve igual: el bloqueo es del plano y va con él.
    servicio.bloqueo(q.pk, beto, "sesion-de-beto-0001", "tomar")
    c = cliente_de(ana)
    r = post(c, "cad_mover", {"planos": [str(q.pk)], "carpetas": [str(base.pk)], "destino": str(sur.pk)})
    assert r.status_code == 200 and r.json()["mensaje"] == "Se movieron 2 elementos a «Sur»." and r.json()["destino"]["proyecto"] == "Sur"
    p.refresh_from_db(), q.refresh_from_db(), base.refresh_from_db()
    assert (base.padre, p.proyecto, q.carpeta, q.proyecto, q.bloqueo_sesion) == (sur, "Sur", sur, "Sur", "sesion-de-beto-0001")
    # Dentro de sí misma, no; con un nombre repetido en el destino, tampoco.
    r = post(c, "cad_mover", {"carpetas": [str(sur.pk)], "destino": str(base.pk)})
    assert r.status_code == 422 and "dentro de sí misma" in r.json()["error"]
    carpetas.crear(ana, "Base", norte)
    r = post(c, "cad_mover", {"carpetas": [str(base.pk)], "destino": str(norte.pk)})
    assert r.status_code == 422 and "Ya hay una carpeta «Base» en «Norte»" in r.json()["error"]
    # A la raíz: el proyecto del plano queda vacío.
    assert post(c, "cad_mover", {"planos": [str(p.pk)], "destino": None}).status_code == 200
    p.refresh_from_db()
    assert (p.carpeta, p.proyecto) == (None, "")


def test_renombrar_un_proyecto_cambia_el_proyecto_de_sus_planos(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    sub = carpetas.crear(ana, "Sub", obra)
    p = plano_en(ana, "Planta", sub)
    r = post(cliente_de(ana), "cad_carpeta_renombrar", {"nombre": "Obra Las Lomas", "cliente": "Constructora Lomas"}, pk=obra.pk)
    assert r.status_code == 200 and r.json()["carpeta"]["nombre"] == "Obra Las Lomas"
    p.refresh_from_db(), obra.refresh_from_db()
    assert (p.proyecto, obra.cliente) == ("Obra Las Lomas", "Constructora Lomas")
    # Una carpeta no lleva cliente.
    post(cliente_de(ana), "cad_carpeta_renombrar", {"nombre": "Sub 2", "cliente": "X"}, pk=sub.pk)
    sub.refresh_from_db()
    assert (sub.nombre, sub.cliente) == ("Sub 2", "")


def test_renombrar_un_plano_sin_proyecto_no_lo_mueve(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    p = plano_en(ana, "Planta", carpetas.crear(ana, "Sub", obra))
    cliente_de(ana).post(url("cad_renombrar", pk=p.pk), {"nombre": "Planta final"})
    p.refresh_from_db()
    assert (p.nombre, p.carpeta.nombre, p.proyecto) == ("Planta final", "Sub", "Obra")


# -- Papelera ------------------------------------------------------------------------------------------------------------------

def test_eliminar_una_carpeta_y_restaurarla_entera(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    base = carpetas.crear(ana, "Base", obra)
    a, b = plano_en(ana, "A", obra), plano_en(ana, "B", base)
    aparte = plano_en(ana, "Aparte", base)
    servicio.eliminar(aparte, ana)                                         # eliminado antes, por su cuenta
    c = cliente_de(ana)
    r = post(c, "cad_carpeta_eliminar", pk=obra.pk)
    assert r.status_code == 200 and r.json()["mensaje"].startswith("«Obra» se movió a la papelera con 2 planos y 1 carpeta.")
    assert not Plano.objects.filter(eliminado__isnull=True).exists() and c.get(url("cad_carpeta", pk=obra.pk)).status_code == 302
    html = c.get(url("cad_papelera")).content.decode()
    assert "Obra" in html and "con 2 planos y 1 carpeta" in html and "Aparte" in html
    assert ">B<" not in html                                                # va dentro de su carpeta, no suelto
    c.post(url("cad_carpeta_restaurar", pk=obra.pk))
    for x in (a, b, aparte, base, obra):
        x.refresh_from_db()
    assert a.eliminado is None and b.eliminado is None and base.eliminada is None and obra.eliminada is None
    assert aparte.eliminado is not None                                     # se eliminó aparte: sigue en la papelera
    assert c.get(url("cad_carpeta", pk=base.pk)).status_code == 200


def test_restaurar_cuando_su_carpeta_ya_no_esta_o_su_nombre_se_uso(ana):
    obra = carpetas.crear(ana, "Obra")
    base = carpetas.crear(ana, "Base", obra)
    carpetas.eliminar(ana, base)
    carpetas.eliminar(ana, obra)
    r = carpetas.restaurar(ana, base)                                       # su carpeta de arriba sigue en la papelera: a la raíz
    base.refresh_from_db()
    assert r["a_raiz"] and base.padre is None and base.eliminada is None
    otra = carpetas.crear(ana, "Obra")
    carpetas.restaurar(ana, obra)
    obra.refresh_from_db()
    assert obra.nombre == "Obra (2)" and otra.nombre == "Obra"
    # Un plano cuya carpeta está en la papelera vuelve a la raíz.
    p = plano_en(ana, "P", otra)
    servicio.eliminar(p, ana)
    carpetas.eliminar(ana, otra)
    p.refresh_from_db()
    assert servicio.restaurar(p, ana) is True
    p.refresh_from_db()
    assert (p.carpeta, p.proyecto, p.eliminado) == (None, "", None)


def test_permisos_de_la_papelera_de_carpetas(cliente_de, ana, beto, admin, sara):
    obra = carpetas.crear(ana, "Obra")
    plano_en(beto, "De Beto", obra)
    r = post(cliente_de(ana), "cad_carpeta_eliminar", pk=obra.pk)
    assert r.status_code == 403 and "planos de otras personas" in r.json()["error"]
    vacia = carpetas.crear(ana, "Vacía")
    assert post(cliente_de(beto), "cad_carpeta_eliminar", pk=vacia.pk).status_code == 200        # vacía: cualquiera que edita
    assert post(cliente_de(sara), "cad_carpeta_eliminar", pk=obra.pk).status_code == 403
    mio = carpetas.crear(ana, "Mío")
    q = plano_en(ana, "Mi plano", mio)
    servicio.bloqueo(q.pk, beto, "sesion-de-beto-0001", "tomar")
    r = post(cliente_de(ana), "cad_carpeta_eliminar", pk=mio.pk)
    assert r.status_code == 422 and "Beto está editando «Mi plano»" in r.json()["error"]
    assert post(cliente_de(admin), "cad_carpeta_eliminar", pk=obra.pk).status_code == 200      # quien administra, sí
    obra.refresh_from_db()
    assert obra.eliminada is not None
    # Restaurar: quien la eliminó, quien la creó o quien administra.
    cliente_de(beto).post(url("cad_carpeta_restaurar", pk=obra.pk))
    obra.refresh_from_db()
    assert obra.eliminada is not None
    cliente_de(ana).post(url("cad_carpeta_restaurar", pk=obra.pk))
    obra.refresh_from_db()
    assert obra.eliminada is None


def test_vencida_o_destruida_se_borra_con_lo_suyo(cliente_de, ana, admin):
    obra = carpetas.crear(ana, "Obra")
    p = plano_en(ana, "P", carpetas.crear(ana, "Sub", obra))
    carpetas.eliminar(ana, obra)
    CarpetaCad.objects.update(eliminada=timezone.now() - timedelta(days=31))
    Plano.objects.update(eliminado=timezone.now() - timedelta(days=31))
    servicio.limpiar()
    assert not CarpetaCad.objects.exists() and not Plano.objects.exists() and not servicio.carpeta(p).exists()
    otra = carpetas.crear(ana, "Otra")
    plano_en(ana, "Q", otra)
    carpetas.eliminar(ana, otra)
    assert cliente_de(ana).post(url("cad_carpeta_destruir", pk=otra.pk)).status_code == 403
    cliente_de(admin).post(url("cad_carpeta_destruir", pk=otra.pk))
    assert not CarpetaCad.objects.exists() and not Plano.objects.exists()


def test_vaciar_la_papelera_borra_carpetas_y_planos(cliente_de, ana, admin):
    obra = carpetas.crear(ana, "Obra")
    plano_en(ana, "P", obra)
    carpetas.eliminar(ana, obra)
    suelto = plano_en(ana, "Suelto")
    servicio.eliminar(suelto, ana)
    cliente_de(admin).post(url("cad_vaciar"), {"confirmacion": "vaciar"})
    assert not CarpetaCad.objects.exists() and not Plano.objects.exists()


# -- Permisos, perfiles, lista ---------------------------------------------------------------------------------------------------

def test_solo_lectura_ve_el_arbol_pero_no_lo_cambia(cliente_de, ana, sara):
    obra = carpetas.crear(ana, "Obra")
    plano_en(ana, "P", obra)
    c = cliente_de(sara)
    d = c.get(url("cad_carpetas")).json()
    assert [(x["nombre"], x["total"]) for x in d["carpetas"]] == [("Obra", 1)]
    assert c.get(url("cad_carpeta", pk=obra.pk)).status_code == 200
    for nombre, kw in (("cad_carpeta_nueva", {}), ("cad_mover", {}), ("cad_carpetas_rutas", {}), ("cad_carpeta_renombrar", {"pk": obra.pk})):
        assert post(c, nombre, {"nombre": "x"}, **kw).status_code == 403


def test_el_arbol_trae_etag_y_responde_304(cliente_de, ana):
    carpetas.crear(ana, "Obra")
    c = cliente_de(ana)
    r = c.get(url("cad_carpetas"))
    assert c.get(url("cad_carpetas"), HTTP_IF_NONE_MATCH=r["ETag"]).status_code == 304
    carpetas.crear(ana, "Otra")
    assert c.get(url("cad_carpetas"), HTTP_IF_NONE_MATCH=r["ETag"]).status_code == 200


def test_las_carpetas_son_de_la_empresa_en_los_dos_perfiles(cliente_de, ana, beto, roles):
    """Como los planos, una sola biblioteca: quien trabaja en TresVizo ve las mismas carpetas que quien trabaja en Grupo ITT."""
    roles.conceder(beto, "herramientas", "cambiar_perfil")
    cb = cliente_de(beto)
    r = cb.post(url("perfil_activo"), {"perfil": "tresvizo"}, HTTP_ACCEPT="application/json")
    assert r.status_code == 200 and r.json()["perfil"] == "tresvizo"
    obra = carpetas.crear(ana, "Obra")
    assert "Obra" in cb.get(url("cad")).content.decode() and cb.get(url("cad_carpeta", pk=obra.pk)).status_code == 200
    assert post(cb, "cad_carpeta_nueva", {"nombre": "Sub", "padre": str(obra.pk)}).status_code == 201
    assert "Sub" in cliente_de(ana).get(url("cad_carpeta", pk=obra.pk)).content.decode()


def test_la_lista_muestra_la_carpeta_con_sus_migas_y_busca_dentro_o_en_todo(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    base = carpetas.crear(ana, "Base", obra)
    plano_en(ana, "Planta baja", base)
    plano_en(ana, "Planta alta", carpetas.crear(ana, "Otra"))
    c = cliente_de(ana)
    html = c.get(url("cad_carpeta", pk=base.pk)).content.decode()
    assert 'data-cad-destino="">Planos</a>' in html and f'data-cad-destino="{obra.pk}">Obra</a>' in html and "Planta baja" in html
    html = c.get(url("cad_carpeta", pk=obra.pk), {"q": "planta"}).content.decode()
    assert "Planta baja" in html and "Planta alta" not in html and "En Obra › Base" in html
    html = c.get(url("cad_carpeta", pk=obra.pk), {"q": "planta", "en": "todo"}).content.decode()
    assert "Planta baja" in html and "Planta alta" in html
    # Las carpetas también salen en la búsqueda, y en la paleta.
    assert "Base" in c.get(url("cad"), {"q": "bas"}).content.decode()
    r = c.get(url("buscar"), {"q": "base"}).json()
    assert any(x["titulo"] == "Base" and x["url"] == url("cad_carpeta", pk=base.pk) for x in r["resultados"])
    # Ordenar por tipo y por fecha
    assert c.get(url("cad_carpeta", pk=base.pk), {"orden": "tipo"}).status_code == 200
    assert c.get(url("cad"), {"orden": "recientes"}).status_code == 200


def test_nuevo_plano_en_la_carpeta_abierta(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    cliente_de(ana).post(url("cad_nuevo"), {"nombre": "Lote 1", "formato": "dxf", "carpeta": str(obra.pk)})
    assert Plano.objects.get().carpeta == obra


# -- Subir una carpeta de la computadora --------------------------------------------------------------------------------------

def test_subir_una_carpeta_recrea_sus_subcarpetas_y_cada_plano_va_a_la_suya(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    c = cliente_de(ana)
    r = post(c, "cad_carpetas_rutas", {"padre": str(obra.pk), "rutas": ["Levantamiento", "Levantamiento/Base", "Levantamiento/Planos/Hojas"]})
    assert r.status_code == 200
    mapa = r.json()["rutas"]
    assert set(mapa) == {"Levantamiento", "Levantamiento/Base", "Levantamiento/Planos/Hojas"}
    assert carpetas.Arbol().ubicacion(mapa["Levantamiento/Planos/Hojas"]) == "Obra › Levantamiento › Planos › Hojas"
    # Otra vez la misma carpeta: se reusan (no se duplican).
    assert post(c, "cad_carpetas_rutas", {"padre": str(obra.pk), "rutas": ["levantamiento/base"]}).json()["rutas"]["levantamiento/base"] == mapa["Levantamiento/Base"]
    assert CarpetaCad.objects.count() == 5
    r, _ = subir_tus(c, DWG, "herramientas.cad_plano", "base.dwg", carpeta=mapa["Levantamiento/Base"])
    assert r.status_code == 204, r.content
    p = Plano.objects.get()
    assert (str(p.carpeta_id), p.proyecto) == (mapa["Levantamiento/Base"], "Obra")
    r, _ = subir_tus(c, DWG, "herramientas.cad_plano", "x.dwg", carpeta="00000000-0000-0000-0000-000000000001")
    assert r.status_code == 422 and "ya no existe" in r.content.decode()


# -- ZIP de una carpeta --------------------------------------------------------------------------------------------------------

def test_zip_de_una_carpeta_con_sus_subcarpetas(cliente_de, ana, sara):
    obra = carpetas.crear(ana, "Obra")
    base = carpetas.crear(ana, "Base", obra)
    plano_en(ana, "Planta", obra)
    plano_en(ana, "base", base)
    plano_en(ana, "base", base, "dxf")
    plano_en(ana, "Otro proyecto", carpetas.crear(ana, "Otra"))
    r = post(cliente_de(sara), "cad_carpeta_zip", pk=obra.pk)                 # solo lectura también descarga
    assert r.status_code == 202
    from itt_herramientas.comun import zips

    token = "a" * 32
    resultado = carpetas.armar_zip(token, sara.pk, str(obra.pk))
    assert resultado["incluidos"] == 3
    ruta, nombre = zips.de_la_cuenta(sara.pk, token)
    assert nombre == "Obra.zip"
    with zipfile.ZipFile(ruta) as zf:
        assert sorted(zf.namelist()) == ["Obra/Base/base.dwg", "Obra/Base/base.dxf", "Obra/Planta.dwg"]
        assert zf.read("Obra/Planta.dwg") == DWG
    vacia = carpetas.crear(ana, "Vacía")
    assert post(cliente_de(sara), "cad_carpeta_zip", pk=vacia.pk).status_code == 422


def test_los_planos_de_una_carpeta_para_llevarla_al_telefono(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    p = plano_en(ana, "Planta", carpetas.crear(ana, "Base", obra))
    d = cliente_de(ana).get(url("cad_carpeta_planos", pk=obra.pk)).json()
    v = VersionPlano.objects.get(plano=p)
    assert d["planos"] == [{"id": str(p.pk), "nombre": "Planta", "formato": "dwg", "bytes": p.bytes, "revision": 1, "sha256": v.sha256,
                            "ruta": "Base", "proyecto": "Obra", "disponible": True}]


# -- Referencias externas desde la biblioteca ----------------------------------------------------------------------------------

def test_referencias_se_resuelven_en_la_carpeta_la_ruta_y_el_proyecto(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    planos = carpetas.crear(ana, "Planos", obra)
    base_dir = carpetas.crear(ana, "Base", obra)
    anidada = carpetas.crear(ana, "Detalles", base_dir)
    host = plano_en(ana, "Planta", planos)
    junto = plano_en(ana, "Ejes", planos)
    base = plano_en(ana, "base", base_dir)
    plano_en(ana, "base", planos, "dxf")                                     # otro formato: no es la referencia «base.dwg»
    detalle = plano_en(ana, "detalle", anidada)
    plano_en(ana, "base", carpetas.crear(ana, "Otra obra"))                  # otro proyecto: nunca
    c = cliente_de(ana)
    rutas = ["Ejes.dwg", "..\\Base\\base.dwg", "C:\\Proyectos\\Obra\\Base\\BASE.DWG", "Detalles\\detalle.dwg", "detalle.dwg", "nada.dwg", "base.dxf"]
    r = post(c, "cad_referencias", {"rutas": rutas, "bases": [str(base_dir.pk)]}, pk=host.pk)
    assert r.status_code == 200
    res = {x["ruta"]: x for x in r.json()["referencias"]}
    assert (res["Ejes.dwg"]["id"], res["Ejes.dwg"]["como"], res["Ejes.dwg"]["ubicacion"]) == (str(junto.pk), "carpeta", "Obra › Planos")
    assert (res["..\\Base\\base.dwg"]["id"], res["..\\Base\\base.dwg"]["como"]) == (str(base.pk), "ruta")
    assert res["C:\\Proyectos\\Obra\\Base\\BASE.DWG"]["id"] == str(base.pk)
    assert (res["Detalles\\detalle.dwg"]["id"], res["Detalles\\detalle.dwg"]["como"]) == (str(detalle.pk), "ruta")   # anidada: desde su padre
    assert (res["detalle.dwg"]["id"], res["detalle.dwg"]["como"], res["detalle.dwg"]["ubicacion"]) == (str(detalle.pk), "nombre", "Obra › Base › Detalles")
    assert res["nada.dwg"]["id"] is None
    assert res["base.dxf"]["formato"] == "dxf" and res["base.dxf"]["como"] == "carpeta"
    v = VersionPlano.objects.get(plano=base)
    assert res["..\\Base\\base.dwg"]["sha256"] == v.sha256 and res["..\\Base\\base.dwg"]["archivo"] == url("cad_archivo", pk=base.pk)


def test_referencias_de_un_plano_en_la_raiz_solo_buscan_en_la_raiz(cliente_de, ana):
    host = plano_en(ana, "Suelto")
    raiz = plano_en(ana, "marco")
    plano_en(ana, "marco", carpetas.crear(ana, "Obra"))
    res = post(cliente_de(ana), "cad_referencias", {"rutas": ["marco.dwg"]}, pk=host.pk).json()["referencias"]
    assert res[0]["id"] == str(raiz.pk) and res[0]["ubicacion"] == "Planos"


def test_varias_con_el_mismo_nombre_en_el_proyecto_se_dice(cliente_de, ana):
    obra = carpetas.crear(ana, "Obra")
    host = plano_en(ana, "Host", carpetas.crear(ana, "Hojas", obra))
    cerca = plano_en(ana, "eje", carpetas.crear(ana, "A", obra))
    plano_en(ana, "eje", carpetas.crear(ana, "Z", carpetas.crear(ana, "B", obra)))
    res = post(cliente_de(ana), "cad_referencias", {"rutas": ["eje.dwg"]}, pk=host.pk).json()["referencias"][0]
    assert (res["id"], res["varias"]) == (str(cerca.pk), 2)


# -- Rendimiento: 2000 planos en 300 carpetas ------------------------------------------------------------------------------

@pytest.fixture
def biblioteca_grande(ana):
    """300 carpetas (30 proyectos × 9 subcarpetas, 2 niveles) y 2000 planos, en bloque (sin archivos: la cuenta de consultas no los lee)."""
    ahora = timezone.now()
    proyectos = CarpetaCad.objects.bulk_create([CarpetaCad(cuenta_id=ana.pk, nombre=f"Proyecto {i:02d}") for i in range(30)])
    subs = []
    for p in proyectos:
        nivel1 = CarpetaCad.objects.bulk_create([CarpetaCad(cuenta_id=ana.pk, padre=p, nombre=f"Carpeta {j}") for j in range(3)])
        for n in nivel1:
            subs += CarpetaCad.objects.bulk_create([CarpetaCad(cuenta_id=ana.pk, padre=n, nombre=f"Sub {k}") for k in range(2)])
    todas = list(CarpetaCad.objects.all())
    assert len(todas) == 30 + 90 + 180
    planos = Plano.objects.bulk_create([Plano(cuenta_id=ana.pk, nombre=f"Plano {i:04d}", formato="dwg", revision=1, bytes=1000,
                                              carpeta=todas[i % len(todas)], proyecto="", actualizado=ahora - timedelta(minutes=i)) for i in range(2000)])
    VersionPlano.objects.bulk_create([VersionPlano(plano=p, numero=1, formato="dwg", ruta=f"cad/planos/{p.pk}/v1.dwg", bytes=1000, sha256="0" * 64)
                                      for p in planos])
    carpetas.sincronizar_proyecto()
    return todas


def test_una_biblioteca_grande_cuesta_las_mismas_consultas(cliente_de, ana, biblioteca_grande, django_assert_max_num_queries):
    c = cliente_de(ana)
    hoja = next(x for x in biblioteca_grande if x.nombre == "Sub 1")
    c.get(url("cad"))                                                          # calienta la sesión y los permisos
    with django_assert_max_num_queries(24):
        r = c.get(url("cad_carpeta", pk=hoja.pk))
    assert r.status_code == 200
    with django_assert_max_num_queries(24):
        assert c.get(url("cad"), {"q": "plano 1", "en": "todo"}).status_code == 200
    with django_assert_max_num_queries(10):
        d = c.get(url("cad_carpetas")).json()
    assert len(d["carpetas"]) == 300 and d["total"] == 2000 and sum(x["planos"] for x in d["carpetas"]) == 2000
    with django_assert_max_num_queries(14):
        assert c.get(url("cad_elegir"), {"carpeta": str(hoja.pk)}).status_code == 200
    proyecto = next(x for x in biblioteca_grande if x.nombre == "Proyecto 07")
    with django_assert_max_num_queries(16):
        destino = next(x for x in biblioteca_grande if x.nombre == "Proyecto 00")
        assert post(c, "cad_mover", {"carpetas": [str(proyecto.pk)], "destino": str(destino.pk)}).status_code == 200
    assert Plano.objects.filter(carpeta__padre__padre=proyecto).values_list("proyecto", flat=True).distinct().get() == "Proyecto 00"
