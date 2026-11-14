"""Páginas de triangulación, pendientes y escurrimiento. Cada trabajo es de una sola cuenta: las demás reciben 404."""
from __future__ import annotations

import math
from dataclasses import asdict

import numpy as np
from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from tvplataforma.models import Tarea
from tvplataforma.permisos import requiere_rol

from ..comun import errores
from ..dominio import triangulation as tri
from ..models import Triangulacion
from ..tareas import (calcular_escurrimiento_triangulacion, calcular_pendientes_triangulacion, calcular_superficie_triangulacion, exportar_triangulacion)
from . import raster, servicio

triangulacion = requiere_rol("herramientas", "triangulacion")
MAX_FLECHAS_JSON = 6000
MAX_PUNTOS_JSON = 2500


def _propia(request, pk) -> Triangulacion:
    t = Triangulacion.objects.filter(pk=pk, cuenta_id=request.user.pk).first()
    if t is None:
        raise Http404
    return t


def _en_curso(t: Triangulacion):
    tarea = Tarea.objects.filter(pk=t.tarea_id).first() if t.tarea_id else None
    return tarea, tarea is not None and not tarea.ha_terminado


@triangulacion
def inicio(request):
    lim = servicio.limites()
    return render(request, "herramientas/triangulacion/inicio.html", {
        "recientes": Triangulacion.objects.filter(cuenta_id=request.user.pk)[:15], "limite_mb": lim["bytes"] // (1024 * 1024), "limite_bytes": lim["bytes"],
        "limite_puntos": lim["puntos"], "limite_triangulos": lim["triangulos"]})


def _rangos_pendientes_por_omision():
    return [{**asdict(r), "etiqueta": r.label()} for r in tri.clone_default_ranges()]


def _rangos_flujo_por_omision():
    return [{**asdict(r), "etiqueta": r.label()} for r in tri.clone_default_flow_ranges()]


@triangulacion
def banco(request, pk):
    t = _propia(request, pk)
    tarea, en_curso = _en_curso(t)
    sup, pen, flu = t.superficie or {}, t.pendientes or {}, t.escurrimiento or {}
    opciones = sup.get("opciones") or {}
    return render(request, "herramientas/triangulacion/banco.html", {
        "t": t, "tarea": tarea, "en_curso": en_curso or t.estado == Triangulacion.Estado.ANALIZANDO, "lista": t.estado == Triangulacion.Estado.LISTO and not en_curso,
        "resumen": t.resumen, "sup": sup, "opciones": opciones, "pen": pen, "flu": flu, "limites": servicio.limites(),
        "rangos_pendientes": pen.get("rangos") or _rangos_pendientes_por_omision(), "rangos_flujo": flu.get("rangos") or _rangos_flujo_por_omision(),
        "opciones_flujo": flu.get("opciones") or {"longitud_base": -1, "punta": 28, "densidad": 1, "pendiente_min": 0.10},
        "capas_elegidas": set(opciones.get("capas") or ([c["nombre"] for c in t.resumen.get("capas", [])] if "TIN_3DFACE" not in [c["nombre"] for c in t.resumen.get("capas", [])] else ["TIN_3DFACE"])),
        "decimales": pen.get("decimales", 2), "con_puntos": bool(t.resumen.get("puntos") or t.resumen.get("inserts") or t.resumen.get("vertices_polilinea")),
        "con_capas": bool(t.resumen.get("capas")), "modo_inicial": "flujo" if flu else "pendientes" if pen else "tin", "limites_txt": ",".join(repr(float(x)) for x in sup.get("limites", []))})


def _encolar(request, t: Triangulacion, funcion, *args):
    _, en_curso = _en_curso(t)
    if en_curso:
        messages.warning(request, "Ya hay un cálculo en curso para este archivo; espera a que termine.")
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    t.error = ""
    t.save(update_fields=["error"])
    t.tarea_id = funcion.encolar(str(t.pk), *args, usuario=request.user).pk
    t.save(update_fields=["tarea_id"])
    return redirect("herramientas:triangulacion_banco", pk=t.pk)


def _listo(request, t):
    if t.estado != Triangulacion.Estado.LISTO:
        messages.error(request, "El archivo aún no está listo.")
        return False
    return True


@triangulacion
@require_POST
def superficie(request, pk):
    t = _propia(request, pk)
    if not _listo(request, t):
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    try:
        opciones = servicio.opciones_superficie(request.POST)
    except ValueError as exc:
        messages.error(request, errores.texto(exc, "Revisa los valores del formulario e inténtalo de nuevo."))
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    return _encolar(request, t, calcular_superficie_triangulacion, opciones)


@triangulacion
@require_POST
def pendientes(request, pk):
    t = _propia(request, pk)
    if not t.superficie:
        messages.error(request, "Primero calcula la superficie.")
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    try:
        rangos = servicio.rangos_pendientes(request.POST)
        decimales = servicio._num(request.POST.get("decimales"), "decimales", minimo=0, maximo=6, por_omision=2, entero=True)
    except ValueError as exc:
        messages.error(request, errores.texto(exc, "Revisa los valores del formulario e inténtalo de nuevo."))
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    return _encolar(request, t, calcular_pendientes_triangulacion, [{k: v for k, v in asdict(r).items() if k in ("name", "min_pct", "max_pct", "color_hex")} for r in rangos], decimales)


@triangulacion
@require_POST
def escurrimiento(request, pk):
    t = _propia(request, pk)
    if not t.superficie:
        messages.error(request, "Primero calcula la superficie.")
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    try:
        rangos = servicio.rangos_flujo(request.POST)
        opciones = servicio.opciones_flujo(request.POST)
    except ValueError as exc:
        messages.error(request, errores.texto(exc, "Revisa los valores del formulario e inténtalo de nuevo."))
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    return _encolar(request, t, calcular_escurrimiento_triangulacion, [{k: v for k, v in asdict(r).items() if k in ("name", "min_pct", "max_pct", "color_hex", "length_factor", "lineweight_mm")} for r in rangos], opciones)


@triangulacion
@require_POST
def exportar(request, pk):
    t = _propia(request, pk)
    tipo = request.POST.get("tipo", "")
    if tipo not in servicio.SUFIJOS:
        messages.error(request, "Elige qué exportar: TIN, pendientes o escurrimiento.")
        return redirect("herramientas:triangulacion_banco", pk=t.pk)
    post = request.POST
    opciones = {"conservar_puntos": "conservar_puntos" in post, "texto_pendiente": "texto_pendiente" in post, "caras": "caras" in post or tipo != "pendientes",
                "referencia_tin": "referencia_tin" in post or tipo != "escurrimiento", "decimales": servicio._num(post.get("decimales"), "decimales", minimo=0, maximo=6, por_omision=2, entero=True)}
    return _encolar(request, t, exportar_triangulacion, tipo, opciones)


@triangulacion
@require_POST
def borrar(request, pk):
    t = _propia(request, pk)
    servicio.borrar_archivos(t)
    t.delete()
    return redirect("herramientas:triangulacion")


@triangulacion
def descargar(request, pk):
    t = _propia(request, pk)
    ruta = servicio.raiz() / t.salida_ruta if t.salida_ruta else None
    if ruta is None or not ruta.is_file():
        raise Http404
    r = FileResponse(open(ruta, "rb"), as_attachment=True, filename=t.salida_nombre or "triangulacion.dxf", content_type="image/vnd.dxf")
    r["Cache-Control"] = "private, no-store"
    return r


# -- Vista previa --------------------------------------------------------------------------------------------------------------------

def _ventana(request):
    try:
        x0, y0, x1, y1 = (float(request.GET[k]) for k in ("x0", "y0", "x1", "y1"))
        ancho, alto = int(request.GET["w"]), int(request.GET["h"])
    except (KeyError, ValueError):
        raise Http404 from None
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)) or not 2 <= ancho <= raster.MAX_LADO or not 2 <= alto <= raster.MAX_LADO:
        raise Http404
    return (x0, y0, x1, y1), ancho, alto


@triangulacion
def vista(request, pk):
    """PNG con la superficie de la ventana pedida: `modo` = tin (elevación), pendientes (rangos) o flujo (superficie oscura)."""
    t = _propia(request, pk)
    modo = request.GET.get("modo", "tin")
    if not t.superficie or modo not in ("tin", "pendientes", "flujo"):
        raise Http404
    ventana, ancho, alto = _ventana(request)
    try:
        triangulos = np.load(servicio.carpeta(t) / "superficie.npz")["triangulos"]
        if modo == "pendientes":
            if not t.pendientes:
                raise Http404
            asign = np.load(servicio.carpeta(t) / "pendientes.npz")["asignaciones"]
            png = raster.pintar(triangulos, raster.colores_rangos(asign, [r["color_hex"] for r in t.pendientes["rangos"]]), ventana, ancho, alto, contorno=False)
        elif modo == "flujo":
            png = raster.pintar(triangulos, None, ventana, ancho, alto, color_fijo=(0x1B, 0x32, 0x41))
        else:
            png = raster.pintar(triangulos, raster.colores_elevacion(triangulos), ventana, ancho, alto)
    except (OSError, ValueError, KeyError):
        raise Http404 from None
    r = HttpResponse(png, content_type="image/png")
    r["Cache-Control"] = "private, max-age=300"
    return r


@triangulacion
def vector(request, pk):
    """Capa vectorial ligera de la ventana: puntos de origen (tin) o flechas de escurrimiento (flujo), decimados."""
    t = _propia(request, pk)
    modo = request.GET.get("modo", "tin")
    ventana, _, _ = _ventana(request)
    x0, y0, x1, y1 = ventana
    if modo == "tin" and t.superficie:
        p = np.load(servicio.carpeta(t) / "superficie.npz")["puntos"]
        if len(p):
            p = p[(p[:, 0] >= x0) & (p[:, 0] <= x1) & (p[:, 1] >= y0) & (p[:, 1] <= y1)]
            paso = max(1, math.ceil(len(p) / MAX_PUNTOS_JSON))
            return JsonResponse({"puntos": p[::paso, :2].round(4).tolist(), "total": int(len(p)), "paso": paso}, headers={"Cache-Control": "private, max-age=300"})
        return JsonResponse({"puntos": [], "total": 0, "paso": 1})
    if modo == "flujo" and t.escurrimiento:
        f = np.load(servicio.carpeta(t) / "flujo.npz")["flechas"]
        xs = f[:, [0, 2, 4, 6]]
        ys = f[:, [1, 3, 5, 7]]
        f = f[(xs.max(axis=1) >= x0) & (xs.min(axis=1) <= x1) & (ys.max(axis=1) >= y0) & (ys.min(axis=1) <= y1)]
        paso = max(1, math.ceil(len(f) / MAX_FLECHAS_JSON))
        return JsonResponse({"flechas": f[::paso][:, [0, 1, 2, 3, 4, 5, 6, 7, 12]].round(4).tolist(), "rangos": [{"color": r["color_hex"], "grosor": r["lineweight_mm"]} for r in t.escurrimiento["rangos"]],
                             "total": int(len(f)), "paso": paso}, headers={"Cache-Control": "private, max-age=300"})
    return JsonResponse({})
