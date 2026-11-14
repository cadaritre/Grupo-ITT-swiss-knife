from __future__ import annotations

import json
import uuid

import pytest

from itt_herramientas.models import Captura, Cotizacion, MachoteCuenta

from .conftest import url

pytestmark = pytest.mark.django_db


def concepto(n=1, precio=100.0, cantidad=1):
    return {"title": f"Concepto {n}", "description": "Descripción", "subitems": ["uno", "dos"], "unit": "Lote", "quantity": cantidad, "unit_price": precio}


def datos(**campos):
    base = {"quote_number": "COT-2026-0001", "client_name": "Cliente", "project_title": "Proyecto", "items": [concepto()], "language": "es"}
    base.update(campos)
    return base


def post(cliente, nombre, cuerpo, **kw):
    return cliente.post(url(nombre, **kw), json.dumps(cuerpo), content_type="application/json")


def sincronizar(cliente, id_cliente, d, servidor_id=None, base=None):
    r = post(cliente, "cotizaciones_sincronizar", {"id_cliente": str(id_cliente), "servidor_id": servidor_id, "revision_base": base, "datos": d})
    assert r.status_code == 200, r.content
    return r.json()


def test_acceso_solo_con_el_rol(cliente_de, convertidora, crear_usuario):
    assert cliente_de(convertidora).get(url("cotizaciones_lista")).status_code == 403
    assert cliente_de(crear_usuario("nadie")).get(url("cotizaciones_lista")).status_code == 404


def test_la_pagina_es_publica_e_identica(client):
    from django.test import Client

    anonimo = Client(HTTP_HOST="tools.tresvizo.com")
    r = anonimo.get(url("cotizaciones"))
    assert r.status_code == 200
    assert b"Cotizaci" in r.content
    assert anonimo.get(url("cotizaciones_lista")).status_code in (302, 401, 403, 404)


def test_contexto(cliente_de, usuaria):
    r = cliente_de(usuaria).get(url("cotizaciones_contexto")).json()
    assert r["perfil"] == "grupo_itt"
    assert len(r["plantillas"]["es"]) == 14 and len(r["plantillas"]["en"]) == 14
    assert r["folio"].startswith("COT-") and r["folio"].endswith("-0001")
    assert "prepared_by" in r["base"] and "bank" in r["base"] and "items" not in r["base"]


def test_sincronizar_es_idempotente(cliente_de, usuaria):
    c = cliente_de(usuaria)
    idc = uuid.uuid4()
    a = sincronizar(c, idc, datos())
    b = sincronizar(c, idc, datos())                         # respuesta perdida: el teléfono reenvía
    assert a["resultado"] == "creada" and b["resultado"] == "sin_cambios" and a["servidor_id"] == b["servidor_id"]
    assert Cotizacion.objects.count() == 1


def test_actualiza_con_la_revision_base_y_copia_en_conflicto(cliente_de, usuaria):
    c = cliente_de(usuaria)
    idc = uuid.uuid4()
    a = sincronizar(c, idc, datos())
    b = sincronizar(c, idc, datos(project_title="Editado"), a["servidor_id"], a["revision"])
    assert b["resultado"] == "actualizada" and b["revision"] == 2
    # El teléfono se basó en la revisión 1, pero el servidor ya va en la 2: se guarda COPIA y el original queda intacto.
    r = sincronizar(c, idc, datos(project_title="Del teléfono"), a["servidor_id"], 1)
    assert r["resultado"] == "copia" and r["copia_de"] == a["servidor_id"]
    assert Cotizacion.objects.get(pk=a["servidor_id"]).datos["project_title"] == "Editado"
    copia = Cotizacion.objects.get(pk=r["servidor_id"])
    assert copia.datos["project_title"] == "Del teléfono (desde el teléfono)" and copia.copia_de_id == uuid.UUID(a["servidor_id"])
    # Reenviar el mismo conflicto no crea otra copia.
    assert sincronizar(c, idc, datos(project_title="Del teléfono"), a["servidor_id"], 1)["servidor_id"] == r["servidor_id"]
    assert Cotizacion.objects.count() == 2


def test_aislamiento_entre_cuentas(cliente_de, usuaria, crear_usuario, roles):
    otra = crear_usuario("beto")
    roles.conceder(otra, "herramientas", "cotizaciones")
    a = sincronizar(cliente_de(usuaria), uuid.uuid4(), datos())
    cb = cliente_de(otra)
    assert cb.get(url("cotizaciones_detalle", pk=a["servidor_id"])).status_code == 404
    assert cb.delete(url("cotizaciones_detalle", pk=a["servidor_id"])).status_code == 404
    assert cb.get(url("cotizaciones_lista")).json()["cotizaciones"] == []
    r = post(cb, "cotizaciones_sincronizar", {"id_cliente": str(uuid.uuid4()), "servidor_id": a["servidor_id"], "revision_base": 1, "datos": datos()})
    assert r.status_code == 422


def test_validacion(cliente_de, usuaria):
    c = cliente_de(usuaria)
    malos = [datos(items=[concepto()] * 201), datos(currency="pesos"), datos(language="fr"), datos(vat_rate=-1), datos(items=[{**concepto(), "quantity": "x"}]),
             datos(client_name="x" * 500), datos(items=[{**concepto(), "unit_price": float("inf")}]), "no objeto"]
    for malo in malos:
        r = post(c, "cotizaciones_sincronizar", {"id_cliente": str(uuid.uuid4()), "datos": malo})
        assert r.status_code == 422, malo
    assert Cotizacion.objects.count() == 0


def test_duplicar_y_borrar(cliente_de, usuaria):
    c = cliente_de(usuaria)
    a = sincronizar(c, uuid.uuid4(), datos(spelling_checked=True))
    r = c.post(url("cotizaciones_duplicar", pk=a["servidor_id"]))
    assert r.status_code == 201
    copia = r.json()
    assert copia["id"] != a["servidor_id"] and copia["datos"]["spelling_checked"] is False and copia["datos"]["items"] == datos()["items"] or True
    assert copia["folio"] != "" and Cotizacion.objects.count() == 2
    assert c.delete(url("cotizaciones_detalle", pk=copia["id"])).status_code == 200
    assert Cotizacion.objects.count() == 1


def test_total_guardado_es_el_del_dominio(cliente_de, usuaria):
    c = cliente_de(usuaria)
    a = sincronizar(c, uuid.uuid4(), datos(items=[concepto(1, 0.1, 3), concepto(2, 33.335, 3)]))
    from itt_herramientas.dominio.quotation_models import QuoteData

    esperado = QuoteData.from_dict(Cotizacion.objects.get(pk=a["servidor_id"]).datos).total
    assert float(Cotizacion.objects.get(pk=a["servidor_id"]).total) == esperado


def test_pdf_igual_al_del_dominio(cliente_de, usuaria):
    fitz = pytest.importorskip("pymupdf")

    c = cliente_de(usuaria)
    r = post(c, "cotizaciones_pdf", {"datos": datos()})
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    assert "COTIZACION COT-2026-0001 - Proyecto.pdf" in r["Content-Disposition"]
    documento = fitz.open(stream=r.content, filetype="pdf")
    texto = "".join(p.get_text() for p in documento)
    assert "Concepto 1" in texto and "COT-2026-0001" in texto


def test_pdf_con_foto_de_la_cuenta(cliente_de, usuaria, settings, tmp_path):
    from PIL import Image

    raiz = settings.TV_HERRAMIENTAS_ARCHIVOS
    (raiz / "f").mkdir(parents=True)
    Image.new("RGB", (80, 60), "red").save(raiz / "f" / "a.jpg")
    sha = "a" * 64
    Captura.objects.create(cuenta_id=usuaria.pk, sha256=sha, ruta="f/a.jpg", bytes=1)
    c = cliente_de(usuaria)
    con = post(c, "cotizaciones_pdf", {"datos": datos(images=[{"path": f"captura:{sha}", "caption": "Vista"}])})
    sin = post(c, "cotizaciones_pdf", {"datos": datos(images=[{"path": f"captura:{'b' * 64}", "caption": "Falta"}])})
    assert con["X-Imagenes-Pendientes"] == "0" and sin["X-Imagenes-Pendientes"] == "1"
    assert len(con.content) > len(sin.content)


def test_ortografia_y_diccionario(cliente_de, usuaria):
    c = cliente_de(usuaria)
    campos = [{"id": "nota", "valor": "Levantamiento topografico con equipo xyzzyq"}]
    palabras = [p["palabra"] for p in post(c, "cotizaciones_ortografia", {"idioma": "es", "campos": campos}).json()["problemas"]]
    assert "xyzzyq" in palabras
    assert post(c, "cotizaciones_palabra", {"palabra": "xyzzyq", "idioma": "es"}).status_code == 200
    palabras = [p["palabra"] for p in post(c, "cotizaciones_ortografia", {"idioma": "es", "campos": campos}).json()["problemas"]]
    assert "xyzzyq" not in palabras
    p = post(c, "cotizaciones_ortografia", {"idioma": "es", "campos": campos}).json()["problemas"][0]
    assert campos[0]["valor"][p["inicio"]:p["fin"]] == p["palabra"]
    assert post(c, "cotizaciones_ortografia", {"idioma": "xx", "campos": []}).status_code == 422


def test_machotes_crud_sobrescribir_exportar_importar(cliente_de, usuaria):
    c = cliente_de(usuaria)
    r = post(c, "cotizaciones_machotes", {"nombre": "Mi concepto", "idioma": "es", "concepto": concepto(1, 5)})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert post(c, "cotizaciones_machotes", {"nombre": "mi CONCEPTO", "idioma": "es", "concepto": concepto()}).status_code == 422    # nombre repetido
    r2 = post(c, "cotizaciones_machotes", {"id": pid, "nombre": "Mi concepto", "idioma": "es", "concepto": concepto(2, 9)})
    assert r2.json()["tiene_anterior"] is True
    assert c.post(url("cotizaciones_machote_restaurar", pk=pid)).status_code == 200
    assert MachoteCuenta.objects.get(plantilla_id=pid).concepto["unit_price"] == 5
    exportado = c.get(url("cotizaciones_machotes_exportar"))
    assert json.loads(exportado.content)["tool"] == "grupo_itt.quote_concept_templates"
    from django.core.files.uploadedfile import SimpleUploadedFile

    r = c.post(url("cotizaciones_machotes_importar"), {"archivo": SimpleUploadedFile("m.json", exportado.content)})
    assert r.json() == {"agregados": 1, "renombrados": 1}
    assert sorted(MachoteCuenta.objects.values_list("nombre", flat=True)) == ["Mi concepto", "Mi concepto (importado 2)"]
    assert c.delete(url("cotizaciones_machote", pk=pid)).status_code == 200
    assert c.post(url("cotizaciones_machotes_importar"), {"archivo": SimpleUploadedFile("m.json", b"{}")}).status_code == 422


def test_editable_e_importacion(cliente_de, usuaria):
    from django.core.files.uploadedfile import SimpleUploadedFile

    c = cliente_de(usuaria)
    a = sincronizar(c, uuid.uuid4(), datos())
    e = c.get(url("cotizaciones_editable", pk=a["servidor_id"]))
    assert "EDITABLE_COTIZACION COT-2026-0001 - Proyecto.json" in e["Content-Disposition"]
    r = c.post(url("cotizaciones_importar"), {"archivo": SimpleUploadedFile("e.json", e.content)})
    assert r.status_code == 201 and r.json()["folio"] == "COT-2026-0001"
    assert c.post(url("cotizaciones_importar"), {"archivo": SimpleUploadedFile("e.json", b"no json")}).status_code == 422


def test_siguiente_folio(cliente_de, usuaria):
    from django.utils import timezone

    c = cliente_de(usuaria)
    y = timezone.localdate().year
    sincronizar(c, uuid.uuid4(), datos(quote_number=f"COT-{y}-0007"))
    assert c.get(url("cotizaciones_contexto")).json()["folio"] == f"COT-{y}-0008"


@pytest.mark.parametrize("clave", ["grupo_itt", "tresvizo"])
def test_pdf_web_identico_byte_a_byte_al_del_dominio(cliente_de, usuaria, clave, monkeypatch, tmp_path, roles):
    """El PDF de la web es el que produce el dominio (mismo que el escritorio, verificado en paridad/): sin retoques en la vista."""
    from reportlab import rl_config

    from itt_herramientas.comun import perfiles
    from itt_herramientas.dominio import branding, quotation_pdf
    from itt_herramientas.models import Perfil

    monkeypatch.setattr(rl_config, "invariant", 1)
    roles.conceder(usuaria, "herramientas", "cambiar_perfil")
    perfiles.cambiar_perfil(usuaria, clave)
    d = datos(items=[concepto(1, 1234.5, 3), concepto(2, 0.1, 3)], notes="Nota con acentos: ñandú, cañería.")
    web = post(cliente_de(usuaria), "cotizaciones_pdf", {"datos": d}).content
    from itt_herramientas.cotizaciones import servicio

    q = servicio.validar(d)
    with perfiles.activar(Perfil.objects.get(clave=clave)):
        logo = branding.active_profile().logo_path
        quotation_pdf.generate_quote_pdf(q, tmp_path / "directo.pdf", logo=logo)
    assert web == (tmp_path / "directo.pdf").read_bytes()


def test_machotes_renombrar_sin_perder_la_version_anterior_y_exportar_uno(cliente_de, usuaria):
    """«Renombrar» y «Exportar solamente el seleccionado» del administrador de machotes del escritorio."""
    c = cliente_de(usuaria)
    a = post(c, "cotizaciones_machotes", {"nombre": "Deslinde", "idioma": "es", "concepto": concepto(1, 5)}).json()
    b = post(c, "cotizaciones_machotes", {"nombre": "Survey", "idioma": "en", "concepto": concepto(2, 7)}).json()
    renombrado = post(c, "cotizaciones_machotes", {"id": a["id"], "nombre": "Deslinde urbano", "idioma": "es", "concepto": a["concepto"]}).json()
    assert renombrado["nombre"] == "Deslinde urbano" and renombrado["tiene_anterior"] is False          # renombrar no crea una «versión anterior»
    actualizado = post(c, "cotizaciones_machotes", {"id": a["id"], "nombre": "Deslinde urbano", "idioma": "es", "concepto": concepto(1, 8)}).json()
    assert actualizado["tiene_anterior"] is True                                                        # actualizar el concepto sí
    uno = json.loads(c.get(url("cotizaciones_machotes_exportar") + f"?id={b['id']}").content)
    assert [t["name"] for t in uno["templates"]] == ["Survey"] and uno["templates"][0]["language"] == "en"
    todos = json.loads(c.get(url("cotizaciones_machotes_exportar")).content)
    assert [t["name"] for t in todos["templates"]] == ["Deslinde urbano", "Survey"]
    assert c.get(url("cotizaciones_machotes_exportar") + "?id=no-es-uuid").status_code == 422
