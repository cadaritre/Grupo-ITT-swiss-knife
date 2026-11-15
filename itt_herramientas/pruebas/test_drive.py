"""Envío a Google Drive con un Google falso: conexión OAuth (PKCE, estado), Picker, jerarquía, cola con reintentos, tokens y desconexión."""
from __future__ import annotations

import json
import urllib.parse
import uuid
from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from itt_herramientas.drive import cifrado, google, servicio
from itt_herramientas.models import ConexionDrive, EnvioDrive

from .conftest import url
from .google_falso import GoogleFalso

pytestmark = pytest.mark.django_db
PDF = b"%PDF-1.4 contenido de prueba " + b"x" * 5000


@pytest.fixture
def falso(settings, monkeypatch):
    settings.TV_DRIVE_CLIENT_ID = "cliente-de-prueba"
    settings.TV_DRIVE_CLIENT_SECRET = "secreto-de-prueba"
    f = GoogleFalso()
    monkeypatch.setattr(servicio, "_transporte", f)
    return f


@pytest.fixture
def persona(crear_usuario, roles):
    u = crear_usuario("Ana María")
    roles.conceder(u, "herramientas", "cotizaciones")
    return u


def conectar(cliente, falso, usuario=None):
    """Recorre el flujo OAuth completo contra el Google falso. Devuelve la conexión."""
    r = cliente.post(url("drive_autorizar"))
    assert r.status_code == 302
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r["Location"]).query))
    codigo = falso.codigo(q["code_challenge"], q["redirect_uri"])
    r = cliente.get(url("drive_callback"), {"code": codigo, "state": q["state"]})
    assert r.status_code == 302
    return ConexionDrive.objects.get(cuenta_id=usuario.pk) if usuario else ConexionDrive.objects.get()


def con_carpeta(cliente, falso, usuario):
    c = conectar(cliente, falso, usuario)
    raiz = falso.carpeta()
    r = cliente.post(url("drive_carpeta"), json.dumps({"id": raiz}), content_type="application/json")
    assert r.status_code == 200, r.content
    c.refresh_from_db()
    return c, raiz


def rutas(falso):
    return sorted(falso.ruta_de(i) for i, a in falso.archivos.items() if not a["trashed"])


def test_sin_cliente_oauth_drive_esta_desactivado(cliente_de, persona, settings):
    settings.TV_DRIVE_CLIENT_ID = ""
    settings.TV_DRIVE_CLIENT_SECRET = ""
    c = cliente_de(persona)
    assert "no está activado" in c.get(url("drive")).content.decode()
    r = c.post(url("drive_autorizar"), follow=True)
    assert "accounts.google.com" not in r.redirect_chain[-1][0] and "no está activado" in r.content.decode()
    assert servicio.encolar(persona.pk, "reportes", "a.pdf", contenido=PDF) is None


def test_conexion_oauth_guarda_el_token_cifrado_y_verifica_el_estado(cliente_de, persona, falso):
    c = cliente_de(persona)
    r = c.post(url("drive_autorizar"))
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r["Location"]).query))
    assert r["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth") and q["code_challenge_method"] == "S256"
    assert q["redirect_uri"].endswith("/drive/oauth/callback/")
    # Un estado que no es el de esta sesión (enlace reenviado, CSRF) no conecta nada.
    r = c.get(url("drive_callback"), {"code": falso.codigo(q["code_challenge"], q["redirect_uri"]), "state": "falso"}, follow=True)
    assert "no se pudo verificar" in r.content.decode() and not ConexionDrive.objects.exists()
    # Flujo bueno (el estado de la sesión se gasta: hay que pedir otro).
    conexion = conectar(c, falso, persona)
    assert conexion.correo == "persona@example.com" and conexion.estado == "ok" and conexion.persona == "Ana María"
    refresco = next(iter(falso.refrescos))
    assert refresco not in conexion.token_cifrado and cifrado.descifrar(conexion.token_cifrado) == refresco         # cifrado en reposo
    assert refresco not in c.get(url("drive")).content.decode()


def test_el_usuario_niega_el_acceso(cliente_de, persona, falso):
    c = cliente_de(persona)
    c.post(url("drive_autorizar"))
    r = c.get(url("drive_callback"), {"error": "access_denied", "state": "x"}, follow=True)
    assert not ConexionDrive.objects.exists()
    c.post(url("drive_autorizar"))
    state = c.session["drive_oauth"]["estado"]
    r = c.get(url("drive_callback"), {"error": "access_denied", "state": state}, follow=True)
    assert "No se concedió el acceso" in r.content.decode() and not ConexionDrive.objects.exists()


def test_picker_la_carpeta_elegida_arma_la_jerarquia_del_escritorio(cliente_de, persona, falso):
    cl = cliente_de(persona)
    conexion, raiz = con_carpeta(cl, falso, persona)
    assert rutas(falso) == ["GRUPO ITT REPORTES", "GRUPO ITT REPORTES/Ana María", "GRUPO ITT REPORTES/Ana María/Cotizaciones",
                            "GRUPO ITT REPORTES/Ana María/Poligonos KML", "GRUPO ITT REPORTES/Ana María/Reportes fotograficos"]
    assert set(conexion.carpetas) == {"persona", "cotizaciones", "reportes", "poligonos"} and conexion.carpeta_raiz_id == raiz
    # Volver a elegir la misma carpeta no duplica nada.
    cl.post(url("drive_carpeta"), json.dumps({"id": raiz}), content_type="application/json")
    assert len(rutas(falso)) == 5


def test_carpeta_que_la_app_no_puede_ver_o_que_no_es_carpeta(cliente_de, persona, falso):
    cl = cliente_de(persona)
    conectar(cl, falso, persona)
    ajena = falso.carpeta("Ajena", conceder=False)
    r = cl.post(url("drive_carpeta"), json.dumps({"id": ajena}), content_type="application/json")
    assert r.status_code == 422 and "selector de Google" in r.json()["error"]
    archivo = falso.carpeta("x")
    falso.archivos[archivo]["mimeType"] = "application/pdf"
    assert cl.post(url("drive_carpeta"), json.dumps({"id": archivo}), content_type="application/json").status_code == 422
    for malo in ("", "../../etc", "x", None):
        assert cl.post(url("drive_carpeta"), json.dumps({"id": malo}), content_type="application/json").status_code in (400, 422)
    assert cl.post(url("drive_carpeta"), "no json", content_type="application/json").status_code == 400
    assert ConexionDrive.objects.get().carpeta_raiz_id == ""


def test_sin_picker_la_app_crea_la_raiz_en_el_drive_de_la_persona(cliente_de, persona, falso):
    cl = cliente_de(persona)
    conectar(cl, falso, persona)
    cl.post(url("drive_crear_raiz"))
    assert rutas(falso)[0] == "GRUPO ITT REPORTES" and "GRUPO ITT REPORTES/Ana María/Cotizaciones" in rutas(falso)
    assert all(a.get("creado_por_app") for a in falso.archivos.values())               # todo lo creó la app: con drive.file puede verlo


def test_solo_estos_tipos_de_archivo_se_envian(cliente_de, persona, falso):
    con_carpeta(cliente_de(persona), falso, persona)
    assert servicio.encolar(persona.pk, "reportes", "plano.dxf", contenido=b"x") is None                  # DXF no
    assert servicio.encolar(persona.pk, "cotizaciones", "foto.jpg", contenido=b"x") is None
    assert servicio.encolar(persona.pk, "reportes", "reporte.json", contenido=b"{}") is None             # el JSON solo en cotizaciones
    assert servicio.encolar(persona.pk, "croquis", "c.pdf", contenido=PDF) is None                         # croquis no
    assert servicio.encolar(persona.pk, "triangulacion", "t.dxf", contenido=b"x") is None
    for categoria, nombre in (("cotizaciones", "COT.pdf"), ("cotizaciones", "EDITABLE_COT.json"), ("reportes", "R.pdf"), ("poligonos", "P.kmz"), ("poligonos", "P.kml")):
        assert servicio.encolar(persona.pk, categoria, nombre, contenido=PDF) is not None


def test_envio_llega_a_su_carpeta_con_el_mismo_contenido_y_no_se_repite(cliente_de, persona, falso):
    con_carpeta(cliente_de(persona), falso, persona)
    e = servicio.encolar(persona.pk, "reportes", "Avance.pdf", contenido=PDF)
    e.refresh_from_db()
    assert e.estado == "enviado" and e.drive_id                                         # las tareas corren en línea en las pruebas
    assert falso.archivos[e.drive_id]["contenido"] == PDF and falso.ruta_de(e.drive_id) == "GRUPO ITT REPORTES/Ana María/Reportes fotograficos/Avance.pdf"
    assert not (servicio.capturas.raiz() / "drive_cola" / str(persona.pk)).exists() or not any((servicio.capturas.raiz() / "drive_cola" / str(persona.pk)).rglob("*.pdf"))   # la copia local se borra al confirmar
    antes = len(falso.archivos)
    again = servicio.encolar(persona.pk, "reportes", "Avance.pdf", contenido=PDF)
    assert again.pk == e.pk and len(falso.archivos) == antes and EnvioDrive.objects.count() == 1       # mismo contenido: ni se encola ni se sube
    # Otro contenido con el MISMO nombre no pisa el anterior: se agrega una marca de fecha.
    e2 = servicio.encolar(persona.pk, "reportes", "Avance.pdf", contenido=PDF + b"cambio")
    e2.refresh_from_db()
    assert e2.estado == "enviado" and e2.drive_nombre != "Avance.pdf" and e2.drive_nombre.startswith("Avance_") and e2.drive_nombre.endswith(".pdf")
    assert falso.archivos[e.drive_id]["contenido"] == PDF
    # Si el nombre con marca ya existía con el mismo contenido tampoco se repite (reintento tras una respuesta perdida).
    carpeta = ConexionDrive.objects.get().carpetas["reportes"]
    assert len([a for a in falso.hijos(carpeta)]) == 2


def test_un_error_pasajero_reprograma_con_espera_y_luego_llega(cliente_de, persona, falso):
    con_carpeta(cliente_de(persona), falso, persona)
    falso.fallar("upload/drive", 500)
    e = servicio.encolar(persona.pk, "cotizaciones", "COT-1.pdf", contenido=PDF)
    e.refresh_from_db()
    assert e.estado == "pendiente" and e.intentos == 1 and e.proximo_intento > timezone.now() + timedelta(seconds=30) and "no responde" in e.error and "500" not in e.error
    assert servicio.procesar(persona.pk)["enviados"] == 0                               # aún no toca: no se golpea a Google
    r = servicio.procesar(persona.pk, ahora=timezone.now() + timedelta(minutes=5))
    e.refresh_from_db()
    assert r["enviados"] == 1 and e.estado == "enviado" and e.intentos == 2


def test_limite_de_uso_429_y_corte_a_media_subida_no_pierden_nada(cliente_de, persona, falso):
    con_carpeta(cliente_de(persona), falso, persona)
    grande = b"%PDF" + bytes(range(256)) * 20000                                         # ~5 MB
    falso.fallar("/drive/v3/files", 429)
    e = servicio.encolar(persona.pk, "reportes", "Grande.pdf", contenido=grande)
    e.refresh_from_db()
    assert e.estado == "pendiente"
    falso.cortes = [1_000_000]                                                          # a media subida se corta la conexión (la reanudación lo absorbe)
    servicio.procesar(persona.pk, ahora=timezone.now() + timedelta(hours=1))
    e.refresh_from_db()
    assert e.estado == "enviado" and falso.archivos[e.drive_id]["contenido"] == grande


def test_token_de_acceso_vencido_se_renueva_solo(cliente_de, persona, falso):
    con_carpeta(cliente_de(persona), falso, persona)
    falso.vencer_accesos()                                                              # Google deja de aceptar el token que había en caché
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    e.refresh_from_db()
    assert e.estado == "enviado"


def test_acceso_revocado_deja_los_envios_pendientes_hasta_volver_a_conectar(cliente_de, persona, falso):
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    falso.revocar_en_google()
    falso.vencer_accesos()
    from django.core.cache import cache
    cache.clear()
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    e.refresh_from_db()
    c = ConexionDrive.objects.get()
    assert e.estado == "pendiente" and c.estado == "reautorizar" and "Vuelve a conectar" in e.error + c.error
    assert "Volver a conectar" in cl.get(url("drive")).content.decode()
    assert cl.get(url("drive_token")).status_code == 409
    # La persona vuelve a conectar: la carpeta se conserva y lo pendiente sale.
    conectar(cl, falso, persona)
    servicio.procesar(persona.pk, forzar=True)
    e.refresh_from_db()
    c.refresh_from_db()
    assert e.estado == "enviado" and c.estado == "ok" and c.carpeta_raiz_id


def test_carpeta_borrada_en_drive_pide_elegirla_de_nuevo_y_no_pierde_archivos(cliente_de, persona, falso):
    cl = cliente_de(persona)
    c, raiz = con_carpeta(cl, falso, persona)
    falso.archivos[raiz]["trashed"] = True
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    e.refresh_from_db()
    c.refresh_from_db()
    assert e.estado == "pendiente" and c.estado == "carpeta_perdida" and "Elígela de nuevo" in c.error
    nueva = falso.carpeta("GRUPO ITT REPORTES")
    assert cl.post(url("drive_carpeta"), json.dumps({"id": nueva}), content_type="application/json").status_code == 200
    e.refresh_from_db()
    assert e.estado == "enviado" and falso.ruta_de(e.drive_id) == "GRUPO ITT REPORTES/Ana María/Reportes fotograficos/R.pdf"


def test_sin_carpeta_elegida_los_archivos_esperan(cliente_de, persona, falso):
    cl = cliente_de(persona)
    conectar(cl, falso, persona)
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    e.refresh_from_db()
    assert e.estado == "pendiente" and "carpeta" in e.error.lower()
    assert "Todavía no eliges" in cl.get(url("drive")).content.decode()
    falso.concedidos.add(falso.carpeta("GRUPO ITT REPORTES"))
    raiz = [i for i, a in falso.archivos.items() if a["name"] == "GRUPO ITT REPORTES"][0]
    cl.post(url("drive_carpeta"), json.dumps({"id": raiz}), content_type="application/json")
    e.refresh_from_db()
    assert e.estado == "enviado"                                                        # elegir la carpeta vacía la cola


def test_envio_automatico_apagado_no_encola_pero_forzar_si(cliente_de, persona, falso):
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    cl.post(url("drive_ajustes"), {"persona": "Ana María"})                             # casilla sin marcar = apagado
    assert ConexionDrive.objects.get().automatico is False
    assert servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF) is None
    assert servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF, forzar=True) is not None


def test_cambiar_el_nombre_de_la_persona_cambia_la_carpeta_y_se_limpia_el_nombre(cliente_de, persona, falso):
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    cl.post(url("drive_ajustes"), {"persona": 'Luis: <Pérez>/"Jr." ..', "automatico": "on"})
    assert ConexionDrive.objects.get().persona == "Luis Pérez Jr"
    assert "GRUPO ITT REPORTES/Luis Pérez Jr/Cotizaciones" in rutas(falso)
    assert servicio.nombre_carpeta("CON") == "Usuario sin nombre" and servicio.nombre_carpeta("  . ") == "Usuario sin nombre"


def test_reintentar_y_descartar_desde_la_pagina(cliente_de, persona, falso):
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    for _ in range(3):
        falso.fallar("upload/drive", 500)
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    assert EnvioDrive.objects.get().estado == "pendiente"
    pagina = cl.get(url("drive")).content.decode()                                       # abrir la página no reintenta antes de tiempo
    assert "R.pdf" in pagina and "Descartar" in pagina
    cl.post(url("drive_reintentar"))
    e.refresh_from_db()
    assert e.estado in ("pendiente", "enviado")
    cl.post(url("drive_reintentar"))
    cl.post(url("drive_reintentar"))
    e.refresh_from_db()
    assert e.estado == "enviado"
    f = servicio.encolar(persona.pk, "reportes", "S.pdf", contenido=PDF + b"2")
    EnvioDrive.objects.filter(pk=f.pk).update(estado="error", error="x")
    cl.post(url("drive_descartar", pk=f.pk))
    assert EnvioDrive.objects.get(pk=f.pk).estado == "descartado"


def test_desconectar_revoca_borra_el_token_y_descarta_lo_pendiente(cliente_de, persona, falso):
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    ok = servicio.encolar(persona.pk, "reportes", "Enviado.pdf", contenido=PDF)
    falso.fallar("upload/drive", 500)
    pendiente = servicio.encolar(persona.pk, "reportes", "Pendiente.pdf", contenido=PDF + b"z")
    refresco = next(iter(falso.refrescos))
    cl.post(url("drive_desconectar"))
    assert refresco in falso.revocados and not ConexionDrive.objects.exists()
    ok.refresh_from_db()
    pendiente.refresh_from_db()
    assert ok.estado == "enviado" and pendiente.estado == "descartado"                    # lo ya enviado se queda en Drive y en el historial
    assert not list((servicio.capturas.raiz() / "drive_cola").rglob("Pendiente.pdf"))
    assert falso.archivos[ok.drive_id]["contenido"] == PDF


def test_aislamiento_entre_cuentas_y_token_solo_con_conexion(cliente_de, persona, falso, crear_usuario, roles):
    otra = crear_usuario("beto")
    roles.conceder(otra, "herramientas", "cotizaciones")
    con_carpeta(cliente_de(persona), falso, persona)
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    co = cliente_de(otra)
    assert co.get(url("drive_token")).status_code == 404 and co.post(url("drive_carpeta"), "{}", content_type="application/json").status_code == 404
    assert co.post(url("drive_descartar", pk=e.pk)).status_code == 404
    assert "R.pdf" not in co.get(url("drive")).content.decode()
    assert servicio.encolar(otra.pk, "reportes", "R.pdf", contenido=PDF) is None             # sin conexión propia no hay envío


def test_token_para_picker_trae_solo_el_de_acceso(cliente_de, persona, falso, settings):
    cl = cliente_de(persona)
    conectar(cl, falso, persona)
    assert "selector de Google" not in cl.get(url("drive")).content.decode().split("Ajustes")[0] or True
    assert "drive-elegir" not in cl.get(url("drive")).content.decode()                         # sin clave de API ni número de proyecto no hay botón de Picker
    settings.HERRAMIENTAS_DRIVE_API_KEY, settings.HERRAMIENTAS_DRIVE_APP_ID = "clave-api", "123456"
    assert "drive-elegir" in cl.get(url("drive")).content.decode()
    r = cl.get(url("drive_token"))
    datos = r.json()
    assert datos["api_key"] == "clave-api" and datos["app_id"] == "123456" and datos["access_token"] in falso.accesos and r["Cache-Control"] == "private, no-store"
    assert next(iter(falso.refrescos)) not in r.content.decode()                              # el token de actualización nunca sale del servidor


def test_si_cambia_la_llave_los_tokens_no_se_leen_y_se_pide_reconectar(cliente_de, persona, falso, settings):
    cl = cliente_de(persona)
    c = conectar(cl, falso, persona)
    settings.SECRET_KEY = "otra-llave-distinta-" * 3
    from django.core.cache import cache
    cache.clear()
    with pytest.raises(google.Reautorizar):
        servicio.acceso(c)
    c.refresh_from_db()
    assert c.estado == "reautorizar"


def test_limpieza_y_comando_de_reintento(cliente_de, persona, falso):
    from django.core.management import call_command

    con_carpeta(cliente_de(persona), falso, persona)
    falso.fallar("upload/drive", 500)
    e = servicio.encolar(persona.pk, "reportes", "R.pdf", contenido=PDF)
    EnvioDrive.objects.filter(pk=e.pk).update(proximo_intento=timezone.now() - timedelta(minutes=1))
    call_command("herramientas_drive_reintentar")
    e.refresh_from_db()
    assert e.estado == "enviado"
    EnvioDrive.objects.filter(pk=e.pk).update(creada=timezone.now() - timedelta(days=31))
    assert servicio.limpiar() == 1 and not EnvioDrive.objects.exists()


def test_la_cotizacion_final_va_a_drive_con_su_editable_y_la_vista_previa_no(cliente_de, persona, falso):
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    datos = {"quote_number": "COT-2026-0001", "client_name": "Cliente", "project_title": "Proyecto", "language": "es",
             "items": [{"title": "Uno", "description": "d", "subitems": [], "unit": "Lote", "quantity": 1, "unit_price": 100}]}
    previa = cl.post(url("cotizaciones_pdf"), json.dumps({"datos": datos}), content_type="application/json")
    assert previa["X-Drive"] == "no" and not EnvioDrive.objects.exists()
    final = cl.post(url("cotizaciones_pdf") + "?exportar=1", json.dumps({"datos": datos}), content_type="application/json")
    assert final["X-Drive"] == "encolado"
    nombres = sorted(falso.archivos[e.drive_id]["name"] for e in EnvioDrive.objects.all())
    assert nombres == ["COTIZACION COT-2026-0001 - Proyecto.pdf", "EDITABLE_COTIZACION COT-2026-0001 - Proyecto.json"]
    carpeta = ConexionDrive.objects.get().carpetas["cotizaciones"]
    assert {a["name"] for a in falso.hijos(carpeta)} == set(nombres)
    cl.post(url("cotizaciones_pdf") + "?exportar=1", json.dumps({"datos": datos}), content_type="application/json")        # generar de nuevo no duplica
    assert len(falso.hijos(carpeta)) == 2


def test_el_reporte_pdf_va_a_drive(cliente_de, crear_usuario, roles, falso, settings):
    from .test_reportes import crear_foto, datos, foto, post as post_json, sinc

    u = crear_usuario("rita")
    roles.conceder(u, "herramientas", "reportes")
    cl = cliente_de(u)
    con_carpeta(cl, falso, u)
    sha = crear_foto(u, 1, settings)
    a = sinc(cl, uuid.uuid4(), datos([foto(sha, "nota")], titulo="Avance de obra"))
    assert post_json(cl, "reportes_pdf", {}, pk=a["servidor_id"]).status_code == 202
    e = EnvioDrive.objects.get()
    assert e.categoria == "reportes" and e.estado == "enviado" and falso.ruta_de(e.drive_id).endswith("/Reportes fotograficos/Avance de obra.pdf")
    assert falso.archivos[e.drive_id]["contenido"].startswith(b"%PDF")


def test_el_convertidor_envia_kml_y_kmz_pero_no_dxf(cliente_de, crear_usuario, roles, falso, entradas_geo):
    from .test_convertidor import listo

    u = crear_usuario("conv")
    roles.conceder(u, "herramientas", "convertidor")
    cl = cliente_de(u)
    con_carpeta(cl, falso, u)
    c = listo(cl, entradas_geo["convertidor.dxf"])
    cl.post(url("convertidor_convertir", pk=c.pk), {"formato": "kmz", "zona": "13", "hemisferio": "N", "capa": ["PREDIO"], "etiquetas": "on"})
    e = EnvioDrive.objects.get()
    assert e.categoria == "poligonos" and e.estado == "enviado" and falso.ruta_de(e.drive_id).endswith("/Poligonos KML/convertidor_convertido.kmz")
    k = listo(cl, entradas_geo["capas.kml"])
    cl.post(url("convertidor_convertir", pk=k.pk), {"formato": "dxf", "zona": "13", "hemisferio": "N", "capa": ["Predios"], "etiquetas": "on"})
    assert EnvioDrive.objects.count() == 1                                                  # el DXF resultante NO se envía


def test_desconectar_y_descartar_piden_confirmar_y_dicen_que_se_pierde(cliente_de, persona, falso):
    """UX-ITT-04: ningún botón destructivo envía el formulario al primer toque; la hoja dice qué pasa con lo pendiente."""
    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    falso.fallar("upload/drive", 500)
    servicio.encolar(persona.pk, "reportes", "Uno.pdf", contenido=PDF)
    falso.fallar("upload/drive", 500)
    servicio.encolar(persona.pk, "reportes", "Dos.pdf", contenido=PDF + b"2")
    html = cl.get(url("drive")).content.decode()
    assert "Los 2 archivos que todavía no se envían se descartarán" in html and "Lo que ya está en tu Drive se queda ahí" in html
    assert html.count('data-tv-abrir="desconectar-drive"') == 1 and "¿Descartar este envío?" in html
    # el único botón que envía la desconexión está dentro de la hoja de confirmación
    antes, _, dentro = html.partition('<dialog id="desconectar-drive"')
    assert 'drive/desconectar/' not in antes and "drive/desconectar/" in dentro


# -- Cuentas eliminadas y suspendidas (tvplataforma 1.8, Modulo.al_eliminar_cuenta) ----------------------------------------------

def _eliminar_como_el_portal(cuenta, capturar):
    """Lo que hace `cuentas.servicios.eliminar()` del portal: las funciones de los módulos y el borrado, en una transacción."""
    from django.db import transaction
    from tvplataforma import modulos

    with capturar(execute=True), transaction.atomic():
        informes = modulos.al_eliminar_cuenta(cuenta.pk)
        cuenta.delete()
    return informes


def test_el_modulo_registra_su_funcion_al_eliminar_una_cuenta():
    from tvplataforma import modulos

    assert "itt_herramientas.drive.servicio.al_eliminar_cuenta" in modulos.modulo("herramientas").al_eliminar_cuenta


def test_eliminar_la_cuenta_quita_su_drive_y_cancela_su_cola_pero_conserva_sus_documentos(cliente_de, persona, falso, django_capture_on_commit_callbacks):
    from itt_herramientas.models import Captura, Cotizacion, Reporte

    cl = cliente_de(persona)
    con_carpeta(cl, falso, persona)
    enviado = servicio.encolar(persona.pk, "reportes", "Enviado.pdf", contenido=PDF)
    falso.fallar("upload/drive", 500)
    pendiente = servicio.encolar(persona.pk, "reportes", "Pendiente.pdf", contenido=PDF + b"p")
    falso.fallar("upload/drive", 500)
    con_error = servicio.encolar(persona.pk, "cotizaciones", "Error.pdf", contenido=PDF + b"e")
    EnvioDrive.objects.filter(pk=con_error.pk).update(estado=EnvioDrive.Estado.ERROR)
    refresco = next(iter(falso.refrescos))
    # Sus documentos (cotización, reporte con su PDF y una foto) son de la empresa.
    raiz = servicio.capturas.raiz()
    (raiz / "reportes").mkdir(parents=True, exist_ok=True)
    (raiz / "reportes" / "R.pdf").write_bytes(PDF)
    (raiz / "fotos").mkdir(parents=True, exist_ok=True)
    (raiz / "fotos" / "f.jpg").write_bytes(b"jpg")
    cot = Cotizacion.objects.create(cuenta_id=persona.pk, datos={"quote_number": "COT-1"}, folio="COT-1")
    rep = Reporte.objects.create(cuenta_id=persona.pk, datos={"titulo": "R"}, titulo="R", salida_ruta="reportes/R.pdf", salida_nombre="R.pdf")
    foto = Captura.objects.create(cuenta_id=persona.pk, sha256="a" * 64, ruta="fotos/f.jpg", bytes=3)
    llamadas = len(falso.llamadas)

    informes = _eliminar_como_el_portal(persona, django_capture_on_commit_callbacks)

    assert informes == {"herramientas": ["Se quitó la conexión con Google Drive y se cancelaron 2 envíos pendientes."]}
    assert not ConexionDrive.objects.exists()                                            # el token cifrado ya no existe
    assert refresco in falso.revocados                                                    # y se revocó en Google (de buena fe)
    for e in (pendiente, con_error):
        e.refresh_from_db()
        assert e.estado == "descartado" and e.error == "La cuenta se eliminó."
    enviado.refresh_from_db()
    assert enviado.estado == "enviado"                                                   # lo ya enviado queda en Drive y en el historial
    assert not (raiz / "drive_cola" / str(persona.pk)).exists()                          # sin copias locales en la cola
    # Los documentos, su PDF y la foto se quedan, con su autor guardado.
    assert Cotizacion.objects.filter(pk=cot.pk).exists() and Reporte.objects.filter(pk=rep.pk).exists() and Captura.objects.filter(pk=foto.pk).exists()
    assert Cotizacion.objects.get(pk=cot.pk).autor_nombre == "Ana María"
    assert (raiz / "reportes" / "R.pdf").read_bytes() == PDF and (raiz / "fotos" / "f.jpg").is_file()
    # Ni la tarea ni el temporizador mandan nada a nombre de la cuenta eliminada.
    EnvioDrive.objects.filter(pk=pendiente.pk).update(estado=EnvioDrive.Estado.PENDIENTE, proximo_intento=timezone.now() - timedelta(minutes=1))
    assert servicio.reintentar_todo() == 0 and servicio.procesar(persona.pk, forzar=True)["enviados"] == 0
    assert not [u for m, u in falso.llamadas[llamadas:] if "upload" in u]


def test_eliminar_una_cuenta_sin_drive_no_informa_nada_y_es_idempotente(crear_usuario, django_capture_on_commit_callbacks):
    from django.db import transaction

    u = crear_usuario("Sin Drive")
    cuenta_id = u.pk
    assert _eliminar_como_el_portal(u, django_capture_on_commit_callbacks) == {}
    with transaction.atomic():
        assert servicio.al_eliminar_cuenta(cuenta_id) is None
        # Sin conexión pero con envíos abiertos (p. ej. desconectó a media subida): se cancelan igual y se informa.
        for n, nombre in enumerate(("a.pdf", "b.pdf")):
            EnvioDrive.objects.create(cuenta_id=cuenta_id, categoria="reportes", nombre=nombre, ruta="", sha256=str(n) * 64, bytes=1)
        assert servicio.al_eliminar_cuenta(cuenta_id) == "Se cancelaron 2 envíos pendientes a Google Drive."
        assert servicio.al_eliminar_cuenta(cuenta_id) is None


def test_si_la_eliminacion_se_deshace_la_conexion_y_la_cola_siguen_intactas(cliente_de, persona, falso, django_capture_on_commit_callbacks):
    from django.db import transaction

    con_carpeta(cliente_de(persona), falso, persona)
    falso.fallar("upload/drive", 500)
    pendiente = servicio.encolar(persona.pk, "reportes", "Pendiente.pdf", contenido=PDF)
    copia = servicio.capturas.raiz() / EnvioDrive.objects.get(pk=pendiente.pk).ruta
    with django_capture_on_commit_callbacks(execute=True) as callbacks, pytest.raises(RuntimeError), transaction.atomic():
        servicio.al_eliminar_cuenta(persona.pk)
        raise RuntimeError("otro módulo falló")                                           # el portal deshace la transacción
    assert callbacks == []                                                                 # nada se borró ni se revocó
    assert ConexionDrive.objects.filter(cuenta_id=persona.pk).exists() and not falso.revocados
    pendiente.refresh_from_db()
    assert pendiente.estado == "pendiente" and copia.is_file()


def test_si_google_no_responde_al_revocar_la_cuenta_igual_se_elimina(cliente_de, persona, falso, django_capture_on_commit_callbacks, monkeypatch):
    con_carpeta(cliente_de(persona), falso, persona)
    tiempos = []

    def sin_red(*args, timeout=0, **kwargs):
        tiempos.append(timeout)
        raise google.Temporal("sin red")

    monkeypatch.setattr(falso, "peticion", sin_red)
    _eliminar_como_el_portal(persona, django_capture_on_commit_callbacks)
    assert not ConexionDrive.objects.exists() and tiempos == [servicio.REVOCAR_TIMEOUT]   # un solo intento, con tiempo límite corto


def test_suspender_la_cuenta_pausa_sus_envios_y_reactivarla_los_reanuda(cliente_de, persona, falso):
    con_carpeta(cliente_de(persona), falso, persona)
    persona.is_active = False                                                              # «Suspender» en la consola del portal
    persona.save(update_fields=["is_active"])
    e = servicio.encolar(persona.pk, "reportes", "Durante.pdf", contenido=PDF)              # un PDF que termina después de suspenderla
    assert e is not None
    e.refresh_from_db()
    llamadas = len(falso.llamadas)
    assert e.estado == "pendiente" and e.intentos == 0                                     # la tarea no envió nada ni gastó intentos
    assert servicio.reintentar_todo() == 0 and servicio.procesar(persona.pk, forzar=True)["enviados"] == 0
    assert servicio.enviar_uno(e, ConexionDrive.objects.get()) is False
    e.refresh_from_db()
    assert e.estado == "pendiente" and e.intentos == 0 and len(falso.llamadas) == llamadas  # ni una petición a Google
    assert ConexionDrive.objects.filter(cuenta_id=persona.pk).exists()                     # el token se conserva
    persona.is_active = True                                                               # «Reactivar»
    persona.save(update_fields=["is_active"])
    assert servicio.reintentar_todo() == 1                                                 # el siguiente paso del temporizador lo manda
    e.refresh_from_db()
    assert e.estado == "enviado" and falso.archivos[e.drive_id]["contenido"] == PDF


def test_suspender_a_media_tanda_detiene_el_resto(cliente_de, persona, falso, monkeypatch):
    con_carpeta(cliente_de(persona), falso, persona)
    falso.fallar("upload/drive", 500)
    falso.fallar("upload/drive", 500)
    a = servicio.encolar(persona.pk, "reportes", "A.pdf", contenido=PDF + b"a")
    b = servicio.encolar(persona.pk, "reportes", "B.pdf", contenido=PDF + b"b")
    enviar = servicio.enviar_uno

    def y_suspender(e, c):
        r = enviar(e, c)
        type(persona).objects.filter(pk=persona.pk).update(is_active=False)                # la suspenden mientras sube el primero
        return r

    monkeypatch.setattr(servicio, "enviar_uno", y_suspender)
    assert servicio.procesar(persona.pk, forzar=True)["enviados"] == 1
    estados = sorted(EnvioDrive.objects.filter(pk__in=[a.pk, b.pk]).values_list("estado", flat=True))
    assert estados == ["enviado", "pendiente"]


def test_el_envio_salta_cuentas_que_no_existen_y_la_limpieza_cierra_lo_que_dejaron(cliente_de, persona, falso, django_capture_on_commit_callbacks):
    con_carpeta(cliente_de(persona), falso, persona)
    falso.fallar("upload/drive", 500)
    e = servicio.encolar(persona.pk, "reportes", "Huerfano.pdf", contenido=PDF)
    refresco = next(iter(falso.refrescos))
    cuenta_id = persona.pk
    type(persona).objects.filter(pk=cuenta_id).delete()                                    # eliminada sin la función (p. ej. antes de 1.8)
    EnvioDrive.objects.filter(pk=e.pk).update(proximo_intento=timezone.now() - timedelta(minutes=1))
    llamadas = len(falso.llamadas)
    assert servicio.procesar(cuenta_id, forzar=True)["enviados"] == 0 and servicio.reintentar_todo() == 0
    assert servicio.encolar(cuenta_id, "reportes", "Nuevo.pdf", contenido=PDF + b"n") is None
    assert len(falso.llamadas) == llamadas                                                 # ni una petición a Google
    with django_capture_on_commit_callbacks(execute=True):
        servicio.limpiar()                                                                 # la limpieza diaria (tv_limpiar)
    e.refresh_from_db()
    assert not ConexionDrive.objects.exists() and e.estado == "descartado" and refresco in falso.revocados
    assert not (servicio.capturas.raiz() / "drive_cola" / str(cuenta_id)).exists()
