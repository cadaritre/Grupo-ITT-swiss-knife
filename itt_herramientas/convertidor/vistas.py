"""Páginas del convertidor DXF ↔ KML/KMZ. Cada conversión es de una sola cuenta: las demás reciben 404."""
from __future__ import annotations

import json

from django.contrib import messages
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST
from tvplataforma import ajustes
from tvplataforma.models import Tarea
from tvplataforma.permisos import requiere_rol

from ..comun import errores, preferencias
from ..models import Conversion
from ..tareas import analizar_conversion, convertir_conversion
from . import servicio

convertidor = requiere_rol("herramientas", "convertidor")
TIPOS = {"kml": "application/vnd.google-earth.kml+xml", "kmz": "application/vnd.google-earth.kmz", "dxf": "image/vnd.dxf"}


def _propia(request, pk) -> Conversion:
    c = Conversion.objects.filter(pk=pk, cuenta_id=request.user.pk).first()
    if c is None:
        raise Http404
    return c


@convertidor
def inicio(request):
    lim = servicio.limites()
    return render(request, "herramientas/convertidor/inicio.html", {
        "recientes": Conversion.objects.filter(cuenta_id=request.user.pk)[:15], "limite_mb": lim["bytes"] // (1024 * 1024), "limite_bytes": lim["bytes"], "limite_entidades": lim["entidades"],
        "destino_siguiente": "convertidor_banco"})


@convertidor
def banco(request, pk):
    c = _propia(request, pk)
    tarea = Tarea.objects.filter(pk=c.tarea_id).first() if c.tarea_id else None
    en_curso = c.estado == Conversion.Estado.ANALIZANDO or (tarea is not None and not tarea.ha_terminado)
    prefs = preferencias.obtener(request.user.pk)["geospatial"]
    op = c.opciones or {}
    salidas = servicio.FORMATOS_SALIDA[c.formato]
    return render(request, "herramientas/convertidor/banco.html", {
        "c": c, "resumen": c.resumen, "tarea": tarea, "en_curso": en_curso, "salidas": salidas, "zonas": range(1, 61),
        "formato": op.get("formato") if op.get("formato") in salidas else salidas[0],
        "etiquetas": op.get("etiquetas", prefs["labels"]), "rellenos": op.get("rellenos", prefs["hatches"]), "relieve": op.get("relieve", prefs["clamp_to_ground"]),
        "capas_elegidas": set(op.get("capas") or [x["nombre"] for x in c.resumen.get("capas", [])]),
        "precisiones": servicio.PRECISIONES, "precision": op.get("precision", "topografica"), "es_dxf": c.formato == "dxf", "lista": c.estado == Conversion.Estado.LISTO and not en_curso,
        "tiene_salida": bool(c.salida_ruta)})


def _zona(post, c):
    try:
        zona = int(post.get("zona", c.zona))
    except ValueError:
        raise ValueError("Selecciona una zona UTM entre 1 y 60.") from None
    hem = post.get("hemisferio", c.hemisferio).upper()
    if not 1 <= zona <= 60 or hem not in ("N", "S"):
        raise ValueError("Selecciona una zona UTM entre 1 y 60 y el hemisferio N o S.")
    return zona, hem


@convertidor
@require_POST
def analizar(request, pk):
    """Vuelve a leer el archivo con otra zona UTM (en un DXF cambia las coordenadas geográficas)."""
    c = _propia(request, pk)
    try:
        c.zona, c.hemisferio = _zona(request.POST, c)
    except ValueError as exc:
        messages.error(request, errores.texto(exc, "Revisa los valores del formulario e inténtalo de nuevo."))
        return redirect("herramientas:convertidor_banco", pk=c.pk)
    c.estado = Conversion.Estado.ANALIZANDO
    c.save(update_fields=["zona", "hemisferio", "estado"])
    c.tarea_id = analizar_conversion.encolar(str(c.pk), usuario=request.user).pk
    c.save(update_fields=["tarea_id"])
    return redirect("herramientas:convertidor_banco", pk=c.pk)


@convertidor
@require_POST
def convertir(request, pk):
    c = _propia(request, pk)
    if c.estado != Conversion.Estado.LISTO:
        messages.error(request, "El archivo aún no está listo para convertirse.")
        return redirect("herramientas:convertidor_banco", pk=c.pk)
    post = request.POST
    formato = post.get("formato", "")
    if formato not in servicio.FORMATOS_SALIDA[c.formato]:
        messages.error(request, f"Un archivo {c.formato.upper()} se convierte a {' o '.join(f.upper() for f in servicio.FORMATOS_SALIDA[c.formato])}.")
        return redirect("herramientas:convertidor_banco", pk=c.pk)
    conocidas = {x["nombre"] for x in c.resumen.get("capas", [])}
    capas = [n for n in post.getlist("capa") if n in conocidas]
    if not capas:
        messages.warning(request, "Activa al menos una capa para exportar.")
        return redirect("herramientas:convertidor_banco", pk=c.pk)
    try:
        zona, hem = _zona(post, c)
    except ValueError as exc:
        messages.error(request, errores.texto(exc, "Revisa los valores del formulario e inténtalo de nuevo."))
        return redirect("herramientas:convertidor_banco", pk=c.pk)
    precision = post.get("precision", "topografica")
    if precision not in servicio.PRECISIONES:
        messages.error(request, "Precisión de curvas desconocida.")
        return redirect("herramientas:convertidor_banco", pk=c.pk)
    c.opciones = {"formato": formato, "precision": precision, "capas": capas, "zona": zona, "hemisferio": hem, "etiquetas": "etiquetas" in post, "rellenos": "rellenos" in post, "relieve": "relieve" in post}
    c.salida_ruta = ""
    c.save(update_fields=["opciones", "salida_ruta"])
    preferencias.guardar(request.user.pk, {"geospatial": {"utm_zone": zona, "hemisphere": hem, "labels": "etiquetas" in post, "hatches": "rellenos" in post, "clamp_to_ground": "relieve" in post}})
    c.tarea_id = convertir_conversion.encolar(str(c.pk), usuario=request.user).pk
    c.save(update_fields=["tarea_id"])
    return redirect("herramientas:convertidor_banco", pk=c.pk)


@convertidor
def vista(request, pk):
    c = _propia(request, pk)
    if not c.vista_ruta:
        raise Http404
    ruta = servicio.raiz() / c.vista_ruta
    if not ruta.is_file():
        raise Http404
    r = FileResponse(open(ruta, "rb"), content_type="application/geo+json")
    r["Cache-Control"] = "private, no-store"
    return r


@convertidor
def descargar(request, pk):
    c = _propia(request, pk)
    ruta = servicio.raiz() / c.salida_ruta if c.salida_ruta else None
    if ruta is None or not ruta.is_file():
        raise Http404
    r = FileResponse(open(ruta, "rb"), as_attachment=True, filename=servicio.nombre_salida(c, c.salida_formato), content_type=TIPOS[c.salida_formato])
    r["Cache-Control"] = "private, no-store"
    return r


@convertidor
@require_POST
def borrar(request, pk):
    c = _propia(request, pk)
    servicio.borrar_archivos(c)
    c.delete()
    messages.success(request, "Archivo eliminado.")
    return redirect("herramientas:convertidor")
