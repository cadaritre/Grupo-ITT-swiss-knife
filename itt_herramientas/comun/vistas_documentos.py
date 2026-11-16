"""API común de los documentos compartidos (cotizaciones, reportes, croquis): bloqueo, historial, recuperación y avisos.

Cada herramienta la monta con `rutas(tipo, decorador, aplicar)` en sus URL (`<herramienta>/api/<pk>/bloqueo/`, …). Todas responden JSON
con `Cache-Control: private, no-store`; los errores para la persona van en `{"error": "…"}` con 409 (otra persona edita, sin permiso para
eliminar…): nunca 403, que la cola del teléfono entiende como sesión caducada.
"""
from __future__ import annotations

import json

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from . import documentos
from .errores import ErrorUsuario


def _privado(respuesta):
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


def _cuerpo(request) -> dict:
    if len(request.body) > 20_000:
        raise ErrorUsuario("La petición es demasiado grande.")
    try:
        datos = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise ErrorUsuario("La petición no es JSON válido.") from None
    if not isinstance(datos, dict):
        raise ErrorUsuario("La petición debe ser un objeto JSON.")
    return datos


def _error(exc, estado=409):
    return _privado(JsonResponse({"error": str(exc), "sin_permiso": isinstance(exc, documentos.SinPermiso)}, status=estado))


def vistas(tipo: str, decorador, aplicar, detalle):
    """Las vistas de un tipo. `aplicar(doc, datos)` vuelca datos guardados (restaurar, recuperar); `detalle(doc, usuario)` es lo que el
    editor recibe del documento (fila + datos)."""
    modelo = documentos.MODELOS[tipo]

    def _doc(pk):
        return get_object_or_404(modelo, pk=pk, eliminado__isnull=True)

    @decorador
    @require_POST
    def bloqueo(request, pk):
        try:
            cuerpo = _cuerpo(request)
            estado = documentos.bloqueo(modelo, pk, request.user, str(cuerpo.get("sesion", "")), str(cuerpo.get("accion", "")))
        except ErrorUsuario as exc:
            return _error(exc, 422)
        return _privado(JsonResponse(estado))

    @decorador
    @require_GET
    def versiones(request, pk):
        doc = _doc(pk)
        return _privado(JsonResponse({"versiones": documentos.versiones(doc), "revision": doc.revision,
                                      "retencion": {"visibles": documentos.VERSIONES_VISIBLES, "agrupar_minutos": documentos.AGRUPAR_MINUTOS}}))

    @decorador
    @require_GET
    def version(request, pk, vid):
        doc = _doc(pk)
        v = documentos.version_de(doc, vid)
        if v is None:
            raise Http404
        return _privado(JsonResponse({**documentos.fila_version(v, doc), "datos": v.datos}))

    @decorador
    @require_POST
    def restaurar(request, pk, vid):
        try:
            sesion = str(_cuerpo(request).get("sesion", ""))
            doc = documentos.restaurar_version(modelo, pk, vid, request.user, sesion, aplicar)
        except ErrorUsuario as exc:
            return _error(exc)
        return _privado(JsonResponse(detalle(doc, request.user)))

    @decorador
    @require_http_methods(["GET", "POST", "DELETE"])
    def recuperacion(request, pk):
        doc = _doc(pk)
        if request.method == "GET":
            return _privado(JsonResponse({"recuperacion": documentos.recuperacion_de(doc, request.user)}))
        if request.method == "DELETE":
            documentos.descartar_recuperacion(doc, request.user)
            return _privado(JsonResponse({"recuperacion": None}))
        try:
            sesion = str(_cuerpo(request).get("sesion", ""))
            doc = documentos.aplicar_recuperacion(modelo, pk, request.user, sesion, aplicar)
        except ErrorUsuario as exc:
            return _error(exc)
        return _privado(JsonResponse(detalle(doc, request.user)))

    @decorador
    @require_http_methods(["GET", "POST"])
    def avisos(request):
        if request.method == "POST":
            try:
                ids = _cuerpo(request).get("vistos", [])
            except ErrorUsuario as exc:
                return _error(exc, 422)
            documentos.marcar_vistos(request.user, ids if isinstance(ids, list) else [])
        return _privado(JsonResponse({"avisos": documentos.avisos_de(request.user, tipo)}))

    return {"bloqueo": bloqueo, "versiones": versiones, "version": version, "restaurar": restaurar, "recuperacion": recuperacion, "avisos": avisos}


def rutas(prefijo: str, nombre: str, v: dict) -> list:
    """Las URL de `vistas()` bajo `<prefijo>/api/`, con nombres `<nombre>_bloqueo`, `<nombre>_versiones`, …"""
    from django.urls import path

    return [
        path(f"{prefijo}/api/avisos/", v["avisos"], name=f"{nombre}_avisos"),
        path(f"{prefijo}/api/<uuid:pk>/bloqueo/", v["bloqueo"], name=f"{nombre}_bloqueo"),
        path(f"{prefijo}/api/<uuid:pk>/versiones/", v["versiones"], name=f"{nombre}_versiones"),
        path(f"{prefijo}/api/<uuid:pk>/versiones/<int:vid>/", v["version"], name=f"{nombre}_version"),
        path(f"{prefijo}/api/<uuid:pk>/versiones/<int:vid>/restaurar/", v["restaurar"], name=f"{nombre}_restaurar_version"),
        path(f"{prefijo}/api/<uuid:pk>/recuperacion/", v["recuperacion"], name=f"{nombre}_recuperacion"),
    ]


def borrar(doc, usuario):
    """DELETE de un documento: a la papelera. 409 sin permiso o si otra persona lo edita."""
    try:
        documentos.eliminar(doc, usuario)
    except ErrorUsuario as exc:
        return _error(exc)
    return None
