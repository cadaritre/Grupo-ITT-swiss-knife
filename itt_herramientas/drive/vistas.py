"""Páginas de Google Drive: conectar (OAuth), elegir la carpeta (Picker), cola de envíos y desconectar. Todo es de UNA cuenta."""
from __future__ import annotations

import json
import secrets

from django.conf import settings
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from tvplataforma import permisos
from tvplataforma.permisos import requiere_rol

from ..models import ConexionDrive, EnvioDrive
from ..modulo import ROLES_DE_HERRAMIENTAS
from . import google, servicio

usuario_de_herramientas = requiere_rol("herramientas", *ROLES_DE_HERRAMIENTAS, "admin")
CLAVE_SESION = "drive_oauth"


def _url_callback(request) -> str:
    return request.build_absolute_uri(reverse("herramientas:drive_callback"))


def _conexion(request) -> ConexionDrive | None:
    return servicio.conexion_de(request.user.pk)


@usuario_de_herramientas
def pagina(request):
    c = _conexion(request)
    if c is not None and servicio.disponible():
        servicio.procesar(request.user.pk)                   # abrir la página también reintenta lo que ya tocaba
    envios = EnvioDrive.objects.filter(cuenta_id=request.user.pk).exclude(estado=EnvioDrive.Estado.DESCARTADO).order_by("-creada")[:40]
    resumen = {e: EnvioDrive.objects.filter(cuenta_id=request.user.pk, estado=e).count() for e in (EnvioDrive.Estado.PENDIENTE, EnvioDrive.Estado.ERROR, EnvioDrive.Estado.ENVIADO)}
    return render(request, "herramientas/drive/pagina.html", {
        "disponible": servicio.disponible(), "c": c, "envios": envios, "resumen": resumen, "categorias": {k: v[0] for k, v in servicio.CATEGORIAS.items()},
        "raiz": servicio.CARPETA_RAIZ, "picker": bool(getattr(settings, "HERRAMIENTAS_DRIVE_API_KEY", "") and getattr(settings, "HERRAMIENTAS_DRIVE_APP_ID", "")),
        "picker_url": getattr(settings, "HERRAMIENTAS_DRIVE_PICKER_URL", "https://apis.google.com/js/api.js"), "token_url": reverse("herramientas:drive_token"),
        "carpeta_url": reverse("herramientas:drive_carpeta")})


@usuario_de_herramientas
@require_POST
def autorizar(request):
    if not servicio.disponible():
        messages.error(request, "El envío a Drive no está activado en este servidor.")
        return redirect("herramientas:drive")
    verificador, desafio = google.pkce()
    estado = secrets.token_urlsafe(24)
    request.session[CLAVE_SESION] = {"estado": estado, "verificador": verificador}
    return redirect(servicio.cliente().url_autorizacion(_url_callback(request), estado, desafio))


@usuario_de_herramientas
@require_GET
def callback(request):
    guardado = request.session.pop(CLAVE_SESION, None)
    if not guardado or not secrets.compare_digest(guardado["estado"], request.GET.get("state", "")):
        messages.error(request, "La conexión con Google no se pudo verificar (el enlace caducó). Inténtalo de nuevo.")
        return redirect("herramientas:drive")
    if request.GET.get("error"):
        messages.warning(request, "No se concedió el acceso a Google Drive." if request.GET["error"] == "access_denied" else "Google no concedió el acceso a Drive. Inténtalo de nuevo.")
        return redirect("herramientas:drive")
    try:
        tokens = servicio.cliente().canjear(request.GET.get("code", ""), _url_callback(request), guardado["verificador"])
        c = servicio.conectar(request.user.pk, tokens)
    except google.ErrorGoogle as exc:
        messages.error(request, f"No se pudo conectar con Google: {exc}")
        return redirect("herramientas:drive")
    messages.success(request, f"Drive conectado como {c.correo or 'tu cuenta de Google'}. Ahora elige la carpeta donde se guardan tus archivos.")
    return redirect("herramientas:drive")


@usuario_de_herramientas
@require_GET
def token(request):
    """Token de acceso de corta vida para que Google Picker (en el navegador) abra el selector con la identidad de esta persona."""
    c = _conexion(request)
    if c is None:
        raise Http404
    try:
        t = servicio.acceso(c)
    except google.ErrorGoogle:
        return JsonResponse({"error": "Vuelve a conectar Drive."}, status=409)
    r = JsonResponse({"access_token": t, "api_key": getattr(settings, "HERRAMIENTAS_DRIVE_API_KEY", ""), "app_id": getattr(settings, "HERRAMIENTAS_DRIVE_APP_ID", "")})
    r["Cache-Control"] = "private, no-store"
    return r


@usuario_de_herramientas
@require_POST
def carpeta(request):
    c = _conexion(request)
    if c is None:
        raise Http404
    try:
        carpeta_id = json.loads(request.body or b"{}").get("id", "")
        servicio.elegir_carpeta(c, str(carpeta_id))
    except (ValueError, AttributeError):
        return JsonResponse({"error": "Petición no válida."}, status=400)
    except google.NoDisponible:
        return JsonResponse({"error": "Google no deja ver esa carpeta a esta app. Elígela con el selector de Google."}, status=422)
    except google.ErrorGoogle as exc:
        return JsonResponse({"error": str(exc)}, status=422)
    messages.success(request, "Carpeta elegida: tus archivos se guardan en «%s / %s»." % (servicio.CARPETA_RAIZ, c.persona))
    servicio.procesar(request.user.pk, forzar=True)
    return JsonResponse({"ok": True})


@usuario_de_herramientas
@require_POST
def crear_raiz(request):
    c = _conexion(request)
    if c is None:
        raise Http404
    try:
        servicio.crear_raiz_propia(c)
    except google.ErrorGoogle as exc:
        messages.error(request, f"No se pudo crear la carpeta en tu Drive: {exc}")
    else:
        messages.success(request, f"Se creó «{servicio.CARPETA_RAIZ}» en tu Drive.")
        servicio.procesar(request.user.pk, forzar=True)
    return redirect("herramientas:drive")


@usuario_de_herramientas
@require_POST
def ajustes_drive(request):
    c = _conexion(request)
    if c is None:
        raise Http404
    c.automatico = "automatico" in request.POST
    c.save(update_fields=["automatico"])
    servicio.cambiar_persona(c, request.POST.get("persona", c.persona))
    messages.success(request, "Ajustes de Drive guardados.")
    if c.carpeta_raiz_id:
        try:
            servicio.asegurar_jerarquia(c)
        except google.ErrorGoogle as exc:
            messages.error(request, str(exc))
    return redirect("herramientas:drive")


@usuario_de_herramientas
@require_POST
def reintentar(request):
    if _conexion(request) is None:
        raise Http404
    for e in EnvioDrive.objects.filter(cuenta_id=request.user.pk, estado__in=[EnvioDrive.Estado.ERROR, EnvioDrive.Estado.PENDIENTE]):
        servicio.reintentar_envio(e)
    r = servicio.procesar(request.user.pk, forzar=True)
    messages.info(request, f"{r['enviados']} enviados · {r['pendientes']} pendientes.")
    return redirect("herramientas:drive")


@usuario_de_herramientas
@require_POST
def descartar(request, pk):
    e = get_object_or_404(EnvioDrive, pk=pk, cuenta_id=request.user.pk)
    servicio.descartar_envio(e)
    return redirect("herramientas:drive")


@usuario_de_herramientas
@require_POST
def desconectar(request):
    c = _conexion(request)
    if c is None:
        raise Http404
    servicio.desconectar(c)
    messages.success(request, "Drive se desconectó. Lo que ya estaba en tu Drive se queda ahí.")
    return redirect("herramientas:drive")
