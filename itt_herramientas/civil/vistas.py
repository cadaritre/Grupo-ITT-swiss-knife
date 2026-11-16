"""Civil: las órdenes que la página de un plano manda al servidor (POST /cad/civil/<orden>/) y sus resultados.

Solo quien puede editar planos (`cad`, `admin`): Civil dibuja en el plano. Nada de esto toca la biblioteca: el plano se guarda, como siempre,
con «Guardar» de la página.
"""
from __future__ import annotations

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET, require_POST
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
