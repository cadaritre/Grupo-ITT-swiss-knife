"""CAD: espacio de la biblioteca (18 GB en su reserva de 20 GB, sin respaldo), archivos que faltan tras restaurar y quién guardó qué."""
from __future__ import annotations

import io
import uuid

import pytest
from django.core.management import call_command

from itt_herramientas.cad import servicio
from itt_herramientas.models import BorradorPlano, Plano, VersionPlano

from .conftest import url
from .test_cad import DWG, SESION_A

pytestmark = pytest.mark.django_db


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
    u.first_name, u.last_name = "Beto", "Sáenz"
    u.save()
    roles.conceder(u, "herramientas", "cad")
    return u


@pytest.fixture
def jefa(crear_usuario, roles):
    u = crear_usuario("jefa")
    roles.conceder(u, "herramientas", "cad", "admin")
    return u


def temporal(contenido: bytes = DWG):
    p = servicio.carpeta_temporal() / f"{uuid.uuid4().hex}.tmp"
    p.write_bytes(contenido)
    return p


def test_la_biblioteca_cabe_en_su_reserva_de_20_gb():
    # 18 GiB: la reserva de CAD es de 20 GB y deja 2 GB para salidas y temporales (auditoría B-5: tv-salud no queda en «crítico»)
    assert servicio.LIMITES["biblioteca_bytes"] == 18 * 1024 ** 3
    assert servicio.VERSIONES_MINIMAS == 5


def test_el_espacio_se_desglosa(ana, beto):
    p = servicio.crear_vacio(ana, "Predio", "", "dwg")
    servicio.guardar(p.pk, beto, SESION_A, 1, temporal())
    q = servicio.crear_vacio(ana, "Otro", "", "dwg")
    servicio.eliminar(q, ana) if hasattr(servicio, "eliminar") else Plano.objects.filter(pk=q.pk).update(eliminado=q.creado)
    d = servicio.uso_detalle()
    tam = len(DWG)
    assert d["vigentes"] == tam and d["anteriores"] == tam and d["papelera"] == tam and d["borradores"] == 0
    e = servicio.espacio()
    assert e["usado"] == 3 * tam and e["tope"] == 18 * 1024 ** 3 and not e["aviso"]


def test_la_biblioteca_muestra_el_espacio_y_el_desglose_solo_a_quien_administra(cliente_de, ana, jefa, settings):
    servicio.crear_vacio(ana, "Predio", "", "dwg")
    html = cliente_de(ana).get(url("cad")).content.decode()
    assert "Planos: 0 de 18 GB" in html and "cad-espacio-detalle" not in html
    html = cliente_de(jefa).get(url("cad")).content.decode()
    assert "Ver en qué se usa" in html and "Versiones anteriores" in html and "Papelera" in html and "copia de seguridad" in html
    # al 90 % la línea se vuelve aviso
    settings.TV_HERRAMIENTAS_LIMITES = {**(settings.TV_HERRAMIENTAS_LIMITES or {}), "cad": {"biblioteca_bytes": int(len(DWG) / 0.95)}}
    html = cliente_de(ana).get(url("cad")).content.decode()
    assert "cad-espacio--aviso" in html and "casi lleno" in html


def test_quien_guardo_que(cliente_de, ana, beto):
    p = servicio.crear_vacio(ana, "Predio Norte", "", "dwg")
    servicio.guardar(p.pk, beto, SESION_A, 1, temporal())
    html = cliente_de(ana).get(url("cad")).content.decode()
    assert "por Beto Sáenz" in html and "Creado por Ana López" in html
    html = cliente_de(ana).get(url("cad_plano", pk=p.pk)).content.decode()
    assert 'id="cad-autoria"' in html and "por Beto Sáenz" in html and "creado por Ana López" in html
    assert html.count("Guardada por") == 2 and "Guardada por Beto Sáenz" in html and "Guardada por Ana López" in html


def _quitar_archivo(v: VersionPlano):
    servicio.ruta_version(v).unlink()


def test_un_plano_sin_archivo_no_rompe_nada(cliente_de, ana, beto):
    p = servicio.crear_vacio(ana, "Perdido", "", "dwg")
    servicio.guardar(p.pk, beto, SESION_A, 1, temporal())
    vigente = VersionPlano.objects.get(plano=p, numero=2)
    _quitar_archivo(vigente)
    cl = cliente_de(ana)
    html = cl.get(url("cad")).content.decode()
    assert "Archivo no disponible" in html
    r = cl.get(url("cad_plano", pk=p.pk))
    assert r.status_code == 200
    html = r.content.decode()
    assert "no está en el servidor" in html and 'id="cad-editor"' not in html and "civil-barra" not in html
    assert cl.get(url("cad_archivo", pk=p.pk)).status_code == 404
    # la versión 1 sí está: se puede abrir y restaurar; la 2 se marca
    assert cl.get(url("cad_plano", pk=p.pk) + "?version=1").status_code == 200
    r = cl.post(url("cad_restaurar_version", pk=p.pk, numero=1), {"sesion": SESION_A}, content_type="application/json")
    assert r.status_code == 200
    _quitar_archivo(VersionPlano.objects.get(plano=p, numero=1))
    r = cl.post(url("cad_restaurar_version", pk=p.pk, numero=1), {"sesion": SESION_A}, content_type="application/json")
    assert r.status_code in (409, 422) and "no está en el servidor" in r.json()["error"]


def test_se_puede_eliminar_un_plano_sin_archivo(cliente_de, ana):
    p = servicio.crear_vacio(ana, "Perdido", "", "dwg")
    _quitar_archivo(VersionPlano.objects.get(plano=p))
    html = cliente_de(ana).get(url("cad_plano", pk=p.pk)).content.decode()
    assert "Eliminar el plano" in html
    r = cliente_de(ana).post(url("cad_eliminar", pk=p.pk))
    assert r.status_code in (200, 302) and Plano.objects.get(pk=p.pk).eliminado is not None


def test_cad_reconciliar_es_idempotente(ana, beto):
    p = servicio.crear_vacio(ana, "A", "", "dwg")
    servicio.guardar(p.pk, beto, SESION_A, 1, temporal())
    q = servicio.crear_vacio(ana, "B", "", "dxf")
    v_p2 = VersionPlano.objects.get(plano=p, numero=2)
    _quitar_archivo(v_p2)
    _quitar_archivo(VersionPlano.objects.get(plano=q, numero=1))
    b = BorradorPlano.objects.create(plano=p, cuenta_id=ana.pk, formato="dwg", ruta="cad/no-existe.dwg", bytes=10, revision_base=1) if "revision_base" in [f.name for f in BorradorPlano._meta.fields] else None
    salida = io.StringIO()
    call_command("cad_reconciliar", "--simular", stdout=salida)
    assert "2 se marcarían" in salida.getvalue() and VersionPlano.objects.filter(sin_archivo__isnull=False).count() == 0
    salida = io.StringIO()
    call_command("cad_reconciliar", stdout=salida)
    texto = salida.getvalue()
    assert "Planos sin archivo: 2 marcados ahora, 0 ya estaban" in texto and "Versiones sin archivo: 2 marcados ahora, 0 ya estaban" in texto
    assert VersionPlano.objects.filter(sin_archivo__isnull=False).count() == 2
    if b is not None:
        assert not BorradorPlano.objects.filter(pk=b.pk).exists()
    salida = io.StringIO()
    call_command("cad_reconciliar", stdout=salida)
    assert "Planos sin archivo: 0 marcados ahora, 2 ya estaban" in salida.getvalue()
    # el archivo vuelve (p. ej. se copió a mano): se desmarca
    servicio.ruta_version(v_p2).write_bytes(DWG)
    salida = io.StringIO()
    call_command("cad_reconciliar", stdout=salida)
    assert "volvió: 1" in salida.getvalue() and VersionPlano.objects.get(pk=v_p2.pk).sin_archivo is None
    # las versiones sin archivo no cuentan en el espacio
    assert servicio.uso() == sum(v.bytes for v in VersionPlano.objects.filter(sin_archivo__isnull=True))
