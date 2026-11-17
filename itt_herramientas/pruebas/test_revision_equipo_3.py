"""Tercera revisión de `web-equipo` (16-11-2026): candados en orden, purga y cursor, base normalizada de la fusión y bloqueos viejos en
las listas. Cada prueba falla sin su arreglo."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import timedelta

import pytest
from django.db import connection, transaction
from django.utils import timezone

from itt_herramientas.comun import documentos
from itt_herramientas.cotizaciones import servicio as cot
from itt_herramientas.models import Cotizacion, Reporte, tocar
from itt_herramientas.reportes import servicio as rep

from .conftest import url
from .test_autoria_supervision import _cot, ana, beto, persona  # noqa: F401  (fixtures)
from .test_reportes import datos as datos_reporte

pytestmark = pytest.mark.django_db


# 1. El candado de los números de cambio va siempre después del renglón: una sincronización y la tarea del PDF no se atoran -------

@pytest.mark.django_db(transaction=True)
def test_sincronizacion_y_tocar_a_la_vez_no_se_atoran(ana):
    r = Reporte.objects.get(pk=rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([], titulo="Uno"), "grupo_itt")["servidor_id"])
    fila_tomada, errores = threading.Event(), []

    def sincronizacion():                                   # tiene el renglón y luego guarda (pide el candado de cambios)
        try:
            with transaction.atomic():
                doc = Reporte.objects.select_for_update().get(pk=r.pk)
                fila_tomada.set()
                time.sleep(0.6)                             # mientras tanto la tarea del PDF ya pidió lo suyo
                doc.titulo = "Dos"
                doc.save()
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)
        finally:
            connection.close()

    def tarea_pdf():                                        # `tocar` tras escribir el PDF
        try:
            fila_tomada.wait(5)
            tocar(Reporte, r.pk)
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)
        finally:
            connection.close()

    hilos = [threading.Thread(target=sincronizacion), threading.Thread(target=tarea_pdf)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(30)
    assert not errores, errores
    assert Reporte.objects.get(pk=r.pk).titulo == "Dos"


# 2. Tras una purga sin otros cambios, el cursor pasa la marca: la segunda sincronización ya no trae todos los ids ----------------

def test_tras_una_purga_el_cursor_pasa_la_marca(cliente_de, ana):
    a, b = _cot(ana), _cot(ana)
    c = cliente_de(ana)
    cursor = c.get(url("cotizaciones_lista")).json()["cursor"]
    c.delete(url("cotizaciones_detalle", pk=b.pk))
    cursor = c.get(url("cotizaciones_lista"), {"desde": cursor}).json()["cursor"]          # ve la marca de la papelera
    documentos.destruir(Cotizacion.objects.get(pk=b.pk))                                    # y luego se borra para siempre
    primera = c.get(url("cotizaciones_lista"), {"desde": cursor}).json()
    segunda = c.get(url("cotizaciones_lista"), {"desde": primera["cursor"]}).json()
    assert "vigentes" not in segunda and segunda["cotizaciones"] == [] and primera["cursor"] > cursor
    assert a.pk


# 3. «fusionada» devuelve lo que se mandó tal como el servidor lo guarda (normalizado): es la base de la fusión del teléfono -------

def test_fusionada_devuelve_lo_enviado_normalizado(ana, beto):
    r = rep.sincronizar(ana, uuid.uuid4(), None, None, datos_reporte([], titulo="Visita", cliente="Norte"), "grupo_itt")
    rep.sincronizar(beto, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 1, datos_reporte([], titulo="Visita", cliente="Sur"), "grupo_itt")
    f = rep.sincronizar(ana, uuid.uuid4(), uuid.UUID(r["servidor_id"]), 1, datos_reporte([], titulo="Visita nueva   ", cliente="Norte"), "grupo_itt")
    assert f["resultado"] == "fusionada" and f["enviados"]["titulo"] == "Visita nueva" and f["datos"]["cliente"] == "Sur"


# 4. Un bloqueo viejo no se queda en las listas: el resumen dice cuándo caduca, y borrar una cuenta mueve el cursor ----------------

def test_el_resumen_dice_cuando_caduca_el_bloqueo_y_borrar_la_cuenta_mueve_el_cursor(cliente_de, ana, beto):
    a = _cot(ana)
    cb = cliente_de(beto)
    cb.post(url("cotizaciones_bloqueo", pk=a.pk), {"accion": "tomar", "sesion": "pagina-de-beto-0001"}, content_type="application/json")
    fila = cliente_de(ana).get(url("cotizaciones_lista")).json()["cotizaciones"][0]
    caduca = timezone.datetime.fromisoformat(fila["bloqueo"]["caduca"])
    assert timedelta(seconds=documentos.BLOQUEO_SEGUNDOS - 5) < caduca - timezone.now() <= timedelta(seconds=documentos.BLOQUEO_SEGUNDOS)
    cursor = cliente_de(ana).get(url("cotizaciones_lista")).json()["cursor"]
    with transaction.atomic():
        documentos.al_eliminar_cuenta(beto.pk)
    despues = cliente_de(ana).get(url("cotizaciones_lista"), {"desde": cursor}).json()["cotizaciones"]
    assert [f["id"] for f in despues] == [str(a.pk)] and despues[0]["bloqueo"] is None
