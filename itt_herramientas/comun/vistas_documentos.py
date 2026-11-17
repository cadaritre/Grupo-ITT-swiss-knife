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
        """GET: los borradores de recuperación de la cuenta. POST `{sesion, id?}`: usar uno. DELETE `?id=`: descartar uno (sin id, todos)."""
        doc = _doc(pk)
        if request.method == "GET":
            return _privado(JsonResponse({"recuperaciones": documentos.recuperaciones_de(doc, request.user)}))
        if request.method == "DELETE":
            ident = request.GET.get("id", "")
            documentos.descartar_recuperacion(doc, request.user, int(ident) if ident.isdigit() else None)
            return _privado(JsonResponse({"recuperaciones": documentos.recuperaciones_de(doc, request.user)}))
        try:
            cuerpo = _cuerpo(request)
            ident = cuerpo.get("id")
            doc = documentos.aplicar_recuperacion(modelo, pk, request.user, str(cuerpo.get("sesion", "")), aplicar,
                                                  ident if isinstance(ident, int) and not isinstance(ident, bool) else None)
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


#: Renglones por respuesta de la lista incremental; el teléfono sigue pidiendo mientras `mas` sea verdadero.
POR_PAGINA = 200


def lista_incremental(request, tipo: str, clave: str, fila):
    """Lista incremental de los documentos de `tipo` para el teléfono: `?desde=<cursor>` (0 o nada: todo).

    Responde `{clave: [resúmenes de lo que cambió], "eliminados": [ids que se fueron a la papelera], "cursor": N, "mas": bool}`, en el orden
    de su número de cambio (`models.siguiente_cambio`), así el cursor es estable y no se salta nada. Si desde ese cursor se borraron
    documentos para siempre, agrega `"vigentes": [ids]` para que el teléfono quite los demás. Solo van resúmenes: los datos de cada
    documento se piden aparte, cuando hacen falta.
    """
    from ..models import PurgaDocumentos

    modelo = documentos.MODELOS[tipo]
    try:
        desde = max(0, int(request.GET.get("desde", "0") or 0))
    except ValueError:
        desde = 0
    purga = PurgaDocumentos.objects.filter(tipo=tipo).values_list("hasta", flat=True).first() or 0     # antes que los renglones
    filas = list(modelo.objects.filter(cambio__gt=desde).order_by("cambio")[:POR_PAGINA + 1])
    mas = len(filas) > POR_PAGINA
    filas = filas[:POR_PAGINA]
    cuerpo = {clave: [fila(d, request.user) for d in filas if d.eliminado is None], "eliminados": [str(d.pk) for d in filas if d.eliminado is not None],
              "cursor": filas[-1].cambio if filas else desde, "mas": mas}
    if desde and purga and desde < purga:
        cuerpo["vigentes"] = [str(pk) for pk in documentos.visibles(modelo).values_list("pk", flat=True)]
    if not mas:
        cuerpo["cursor"] = max(cuerpo["cursor"], purga)             # ya vio la purga: la siguiente no vuelve a mandar los vigentes
    return _privado(JsonResponse(cuerpo))


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
