"""Civil: las órdenes que la página de un plano manda al servidor (POST /cad/civil/<orden>/) y sus resultados.

Solo quien puede editar planos (`cad`, `admin`): Civil dibuja en el plano. Nada de esto toca la biblioteca: el plano se guarda, como siempre,
con «Guardar» de la página.
"""
from __future__ import annotations

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from tvplataforma.permisos import requiere_rol

from ..comun.errores import ErrorUsuario
from . import servicio

editar = requiere_rol("herramientas", "cad", "admin")


def _json(datos, status=200):
    r = JsonResponse(datos, status=status)
    r["Cache-Control"] = "private, no-store"
    return r


@editar
@require_POST
def orden(request, orden):
    from ..tareas import civil_orden

    if orden not in servicio.ORDENES:
        raise Http404
    try:
        datos = servicio.leer_cuerpo(request)
        if orden in servicio.PESADAS:
            servicio.revisar(orden, datos)
            token = servicio.guardar_entrada(datos)
            tarea = civil_orden.encolar(orden, token, request.user.pk, usuario=request.user)
            return _json({"tarea": str(tarea.pk), "terminada": tarea.ha_terminado, "estado": tarea.estado, "resultado": tarea.resultado,
                          "error": tarea.error, "fraccion": tarea.fraccion, "mensaje": tarea.mensaje}, status=202)
        return _json({"terminada": True, "estado": "completada", "resultado": servicio.calcular(orden, datos)})
    except ErrorUsuario as exc:
        return _json({"error": str(exc)}, status=422)


@editar
@require_GET
def resultado(request, token):
    r = servicio.resultado(request.user.pk, token)
    if r is None:
        raise Http404
    return _json(r)


@editar
@require_GET
def panel(request):
    """La barra y las hojas de Civil (`civil_panel.html`), aparte de la página del plano: son ~16 KB comprimidas y no cambian de un plano a
    otro, así que viajan una vez por versión. Con la huella vigente en `?v=` se guardan un año; `ETag` y 304 para lo demás."""
    from django.http import HttpResponse

    from ..comun import condicional
    from . import pagina

    html, huella = pagina.panel()
    etiqueta = f'"{huella}"'
    if condicional.coincide(request, etiqueta):
        r = HttpResponse(status=304)
    else:
        r = HttpResponse(html, content_type="text/html; charset=utf-8")
    r["ETag"] = etiqueta
    r["Cache-Control"] = "private, max-age=31536000, immutable" if request.GET.get("v") == huella else "private, no-cache"
    r["X-Content-Type-Options"] = "nosniff"
    return r


@editar
@require_http_methods(["GET", "POST", "DELETE"])
def formatos(request):
    """Los formatos de puntos que la cuenta guardó con nombre (`?uso=importar|exportar`). GET los lista; POST `{nombre, datos}` guarda o
    reemplaza uno; DELETE `?nombre=` lo borra. Solo los de la propia cuenta, y solo quien puede editar planos."""
    import json

    uso = request.GET.get("uso", "")
    try:
        if request.method == "GET":
            return _json({"formatos": servicio.formatos_de(request.user.pk, uso)})
        if request.method == "DELETE":
            if not servicio.borrar_formato(request.user.pk, uso, request.GET.get("nombre")):
                raise Http404
            return _json({"formatos": servicio.formatos_de(request.user.pk, uso)})
        if int(request.META.get("CONTENT_LENGTH") or 0) > 16 * 1024:
            raise ErrorUsuario("El formato es demasiado grande.")
        try:
            cuerpo = json.loads(request.body or b"{}")
        except ValueError:
            raise ErrorUsuario("Los datos llegaron dañados. Inténtalo de nuevo.") from None
        if not isinstance(cuerpo, dict):
            raise ErrorUsuario("Los datos llegaron dañados. Inténtalo de nuevo.")
        guardado = servicio.guardar_formato(request.user.pk, uso, cuerpo.get("nombre"), cuerpo.get("datos"))
        return _json({"guardado": guardado, "formatos": servicio.formatos_de(request.user.pk, uso)})
    except ErrorUsuario as exc:
        return _json({"error": str(exc)}, status=422)
