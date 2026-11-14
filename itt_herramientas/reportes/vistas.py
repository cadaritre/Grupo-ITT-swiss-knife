"""Reportes fotográficos: la página (HTML fijo, que el service worker guarda) y su API JSON."""
from __future__ import annotations

import json
import uuid

from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from tvplataforma.models import Tarea
from tvplataforma.permisos import requiere_rol

from ..comun import autoria, capturas, clientes, perfiles, zips
from ..models import Reporte
from ..tareas import generar_reporte
from . import servicio

usuario_de_reportes = requiere_rol("herramientas", "reportes", "admin")
MAX_CUERPO = 400_000
MARCA_ID = "00000000-0000-0000-0000-000000000000"


def pagina(request):
    respuesta = render(request, "herramientas/reportes/editor.html", {"urls": _urls()})
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


def _urls() -> dict:
    fijas = ("contexto", "lista", "sincronizar", "zip")
    con_id = ("detalle", "pdf", "descargar")
    urls = {n: reverse(f"herramientas:reportes_{n}") for n in fijas}
    urls.update({n: reverse(f"herramientas:reportes_{n}", kwargs={"pk": MARCA_ID}) for n in con_id})
    urls["foto"] = reverse("herramientas:reportes_foto", kwargs={"sha256": "SHA"})
    urls["teselas"] = reverse("herramientas:teselas", kwargs={"capa": "osm", "z": 0, "x": 0, "y": 0}).replace("/osm/0/0/0.png", "/{capa}/{z}/{x}/{y}.png")
    urls["tarea"] = reverse("tvplataforma:tarea", kwargs={"tarea_id": MARCA_ID})
    urls["capturar"] = reverse("herramientas:capturar")
    return urls


def _cuerpo(request) -> dict:
    if len(request.body) > MAX_CUERPO:
        raise servicio.ReporteInvalido("La petición es demasiado grande.")
    try:
        crudo = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise servicio.ReporteInvalido("La petición no es JSON válido.") from None
    if not isinstance(crudo, dict):
        raise servicio.ReporteInvalido("La petición debe ser un objeto JSON.")
    return crudo


def _uuid(valor, nombre: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(valor))
    except ValueError:
        raise servicio.ReporteInvalido(f"«{nombre}» no es un identificador válido.") from None


def _privado(respuesta):
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


def _error(exc, estado=422):
    return JsonResponse({"error": str(exc), **({"faltan": exc.faltan} if isinstance(exc, servicio.FotosPendientes) else {})}, status=estado)


@usuario_de_reportes
@require_GET
def contexto(request):
    perfil = perfiles.perfil_activo(request.user)
    return _privado(JsonResponse({
        "cuenta": request.user.pk, "autor": dict(zip(("nombre", "correo"), autoria.de_usuario(request.user))), "perfil": perfil.clave, "perfil_nombre": perfil.nombre,
        "titulo": servicio.titulo_por_omision(perfil), "max_fotos": servicio.MAX_FOTOS,
        "credenciales": bool(perfil.firmante_reporte or perfil.cedula_profesional),
        "clientes": clientes.sugerencias(request.user.pk),
    }))


@usuario_de_reportes
@require_GET
def lista(request):
    return _privado(JsonResponse({"reportes": [servicio.resumen_fila(r) for r in Reporte.objects.filter(cuenta_id=request.user.pk)[:300]]}))


@usuario_de_reportes
@require_http_methods(["GET", "DELETE"])
def detalle(request, pk):
    r = get_object_or_404(Reporte, pk=pk, cuenta_id=request.user.pk)
    if request.method == "DELETE":
        r.delete()
        return _privado(JsonResponse({"borrado": str(pk)}))
    return _privado(JsonResponse({**servicio.resumen_fila(r), "datos": r.datos}))


@usuario_de_reportes
@require_POST
def sincronizar(request):
    try:
        cuerpo = _cuerpo(request)
        id_cliente = _uuid(cuerpo.get("id_cliente"), "id_cliente")
        servidor = _uuid(cuerpo["servidor_id"], "servidor_id") if cuerpo.get("servidor_id") else None
        base = cuerpo.get("revision_base")
        if base is not None and (isinstance(base, bool) or not isinstance(base, int) or base < 1):
            raise servicio.ReporteInvalido("La revisión base no es válida.")
        resultado = servicio.sincronizar(request.user, id_cliente, servidor, base, cuerpo.get("datos"), perfiles.perfil_activo(request.user).clave)
    except servicio.ReporteInvalido as exc:
        return _error(exc)
    return _privado(JsonResponse(resultado))


@usuario_de_reportes
@require_POST
def pdf(request, pk):
    """Pone en cola la generación del PDF. 409 si faltan fotos por subir; 422 si el reporte está incompleto."""
    r = get_object_or_404(Reporte, pk=pk, cuenta_id=request.user.pk)
    try:
        servicio.verificar_listo(r)
    except servicio.FotosPendientes as exc:
        return _error(exc, 409)
    except servicio.ReporteInvalido as exc:
        return _error(exc)
    previa = Tarea.objects.filter(pk=r.tarea_id).first() if r.tarea_id else None
    if previa is not None and not previa.ha_terminado:               # un solo PDF a la vez por reporte
        return _privado(JsonResponse({"tarea": str(previa.pk), "revision": r.revision}))
    tarea = generar_reporte.encolar(str(r.pk), usuario=request.user)
    Reporte.objects.filter(pk=r.pk).update(tarea_id=tarea.pk)
    return _privado(JsonResponse({"tarea": str(tarea.pk), "revision": r.revision}, status=202))


@usuario_de_reportes
@require_GET
def descargar(request, pk):
    r = get_object_or_404(Reporte, pk=pk, cuenta_id=request.user.pk)
    ruta = capturas.raiz() / r.salida_ruta if r.salida_ruta else None
    if ruta is None or not ruta.is_file():
        raise Http404
    respuesta = FileResponse(open(ruta, "rb"), content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(request.GET.get("ver") != "1", r.salida_nombre or "Reporte.pdf")
    respuesta["Cache-Control"] = "private, no-store"
    respuesta["X-Revision"] = str(r.salida_revision or 0)
    respuesta["Access-Control-Expose-Headers"] = "X-Revision, Content-Disposition"
    return respuesta


@usuario_de_reportes
@require_GET
def foto(request, sha256):
    """Miniatura (por omisión) u original (`?original=1`) de una foto de ESTA cuenta."""
    ruta = capturas.ruta(request.user.pk, sha256) if request.GET.get("original") == "1" else capturas.miniatura(request.user.pk, sha256)
    if ruta is None:
        raise Http404
    respuesta = FileResponse(open(ruta, "rb"), content_type="image/png" if ruta.suffix == ".png" else "image/jpeg")
    respuesta["Cache-Control"] = "private, max-age=86400"
    return respuesta


@usuario_de_reportes
@require_POST
def zip_lote(request):
    """Varios reportes propios en un ZIP: `{"elementos": [["reporte", id], …]}` (hasta 50). Los PDF que falten se generan antes."""
    try:
        elementos = zips.cuerpo_json(request).get("elementos")
    except zips.LoteInvalido as exc:
        return _error(exc)
    return zips.responder(request, elementos, "reportes")
