"""CAD: proyectos y carpetas de la biblioteca (crear, renombrar, mover, papelera, ZIP, sin conexión) y las referencias externas que se
resuelven desde la biblioteca. Mismos roles que los planos: `cad` y `admin` crean, renombran y mueven; `supervision` solo ve."""
from __future__ import annotations

import hashlib
import json

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from tvplataforma.permisos import requiere_rol

from ..comun import condicional, zips
from ..comun.errores import ErrorUsuario
from ..models import CarpetaCad
from . import carpetas, servicio
from .vistas import _cuerpo_json, _error, _json, _plano, editar, ver


def _carpeta(pk, eliminada=False) -> CarpetaCad:
    c = CarpetaCad.objects.filter(pk=pk, eliminada__isnull=not eliminada).first()
    if c is None:
        raise Http404
    return c


def _fila(c: CarpetaCad, arbol: carpetas.Arbol | None = None) -> dict:
    arbol = arbol or carpetas.Arbol(conteos=False)
    return {"id": str(c.pk), "nombre": c.nombre, "cliente": c.cliente, "padre": str(c.padre_id) if c.padre_id else None,
            "ubicacion": arbol.ubicacion(c.pk), "url": reverse("herramientas:cad_carpeta", args=[c.pk])}


def _responder_error(exc):
    return _error(str(exc), 403 if isinstance(exc, servicio.SinPermiso) else 422)


@ver
@require_GET
def lista(request):
    """El árbol entero (para «Mover a…», las hojas del editor y la página): cada carpeta con su padre, su nivel y cuántos planos tiene.
    Dos consultas para cualquier tamaño. `ETag` con su contenido: si nada cambió, 304."""
    arbol = carpetas.Arbol()
    datos = {"carpetas": arbol.como_lista(), "raiz": arbol.en_raiz, "total": arbol.total}
    cuerpo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    etiqueta = f'"{hashlib.sha256(cuerpo.encode()).hexdigest()[:20]}"'
    r = HttpResponse(status=304) if condicional.coincide(request, etiqueta) else HttpResponse(cuerpo, content_type="application/json")
    r["ETag"] = etiqueta
    r["Cache-Control"] = "private, no-cache"
    return r


@editar
@require_POST
def nueva(request):
    """Proyecto (sin `padre`) o carpeta nueva. JSON: {nombre, padre?, cliente?}."""
    try:
        d = _cuerpo_json(request)
        c = carpetas.crear(request.user, str(d.get("nombre", "")), d.get("padre") or None, str(d.get("cliente", "")))
    except ErrorUsuario as exc:
        return _responder_error(exc)
    return _json({"carpeta": _fila(c)}, status=201)


@editar
@require_POST
def rutas(request):
    """Subir una carpeta de la computadora: sus subcarpetas (rutas relativas) dentro de `padre`, reusando las que ya existen."""
    try:
        d = _cuerpo_json_grande(request)
        mapa = carpetas.asegurar_rutas(request.user, d.get("padre") or None, d.get("rutas") or [])
    except ErrorUsuario as exc:
        return _responder_error(exc)
    return _json({"rutas": mapa})


def _cuerpo_json_grande(request) -> dict:
    """Como `_cuerpo_json`, con lugar para 500 rutas de carpetas."""
    if len(request.body) > 512_000:
        raise ErrorUsuario("La carpeta tiene demasiadas subcarpetas para una sola vez.")
    try:
        d = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise ErrorUsuario("La petición no es válida.") from None
    if not isinstance(d, dict):
        raise ErrorUsuario("La petición no es válida.")
    return d


@editar
@require_POST
def renombrar(request, pk):
    c = _carpeta(pk)
    try:
        d = _cuerpo_json(request)
        c = carpetas.renombrar(request.user, c, str(d.get("nombre", "")), str(d["cliente"]) if "cliente" in d else None)
    except ErrorUsuario as exc:
        return _responder_error(exc)
    return _json({"carpeta": _fila(c)})


@editar
@require_POST
def mover(request):
    """JSON {planos: [ids], carpetas: [ids], destino: id | null}. Un plano en edición se mueve igual: su bloqueo va con él."""
    try:
        d = _cuerpo_json_grande(request)
        planos, lista_carpetas = d.get("planos") or [], d.get("carpetas") or []
        if not (isinstance(planos, list) and isinstance(lista_carpetas, list) and all(isinstance(x, str) for x in [*planos, *lista_carpetas])):
            raise ErrorUsuario("La petición no es válida.")
        r = carpetas.mover(request.user, planos, lista_carpetas, d.get("destino") or None)
    except ErrorUsuario as exc:
        return _responder_error(exc)
    destino = carpetas.carpeta_activa(d.get("destino")) if d.get("destino") else None
    n = r["planos"] + r["carpetas"]
    donde = f"«{destino.nombre}»" if destino else "Planos"
    arbol = carpetas.Arbol(conteos=False)
    proyecto = arbol.proyecto(destino.pk) if destino else None
    return _json({**r, "destino": {"id": str(destino.pk) if destino else None, "ubicacion": arbol.ubicacion(destino.pk, "Planos") if destino else "Planos",
                                   "proyecto": proyecto.nombre if proyecto else ""},
                  "mensaje": f"Se movi{'ó' if n == 1 else 'eron'} {n} elemento{'s' if n != 1 else ''} a {donde}." if n else f"Ya estaba en {donde}."})


@editar
@require_POST
def eliminar(request, pk):
    c = _carpeta(pk)
    try:
        r = carpetas.eliminar(request.user, c)
    except ErrorUsuario as exc:
        return _responder_error(exc)
    partes = []
    if r["planos"]:
        partes.append(f"{r['planos']} plano{'s' if r['planos'] != 1 else ''}")
    if r["carpetas"] > 1:
        partes.append(f"{r['carpetas'] - 1} carpeta{'s' if r['carpetas'] != 2 else ''}")
    con = f" con {' y '.join(partes)}" if partes else ""
    padre = reverse("herramientas:cad_carpeta", args=[c.padre_id]) if c.padre_id else reverse("herramientas:cad")
    return _json({**r, "volver": padre,
                  "mensaje": f"«{c.nombre}» se movió a la papelera{con}. Se borra para siempre en {servicio.PAPELERA_DIAS} días."})


@editar
@require_POST
def restaurar(request, pk):
    c = _carpeta(pk, eliminada=True)
    try:
        r = carpetas.restaurar(request.user, c)
    except ErrorUsuario as exc:
        messages.error(request, str(exc))
        return redirect("herramientas:cad_papelera")
    texto = f"«{r['nombre']}» volvió a la biblioteca"
    if r["planos"]:
        texto += f" con {r['planos']} plano{'s' if r['planos'] != 1 else ''}"
    texto += "." + (" Su carpeta de arriba ya no está: quedó en la raíz de Planos." if r["a_raiz"] else "")
    messages.success(request, texto)
    return redirect("herramientas:cad_papelera")


@requiere_rol("herramientas", "admin")
@require_POST
def destruir(request, pk):
    c = _carpeta(pk, eliminada=True)
    nombre = c.nombre
    carpetas.destruir(c)
    messages.success(request, f"«{nombre}» se eliminó para siempre.")
    return redirect("herramientas:cad_papelera")


@ver
@require_POST
def zip_carpeta(request, pk):
    """«Descargar en ZIP» de una carpeta: sus planos y subcarpetas, cada uno en su versión vigente, en una tarea."""
    from ..tareas import armar_zip_carpeta

    c = _carpeta(pk)
    n, total = carpetas.resumen_zip(c)
    if not n:
        return _error("Esta carpeta no tiene planos para descargar.")
    if n > carpetas.ZIP_MAX_PLANOS:
        return _error(f"Esta carpeta tiene {n} planos; el ZIP lleva hasta {carpetas.ZIP_MAX_PLANOS}. Descarga sus carpetas por separado.")
    if total > zips.MAX_BYTES_PLANOS:
        return _error(f"Esta carpeta pesa más de {zips.MAX_BYTES_PLANOS // 1024 ** 3} GB. Descarga sus carpetas por separado.")
    tarea = armar_zip_carpeta.encolar(zips.nuevo_token(), request.user.pk, str(c.pk), usuario=request.user)
    return _json({"tarea": str(tarea.pk), "terminada": tarea.ha_terminado, "estado": tarea.estado, "resultado": tarea.resultado, "error": tarea.error,
                  "fraccion": tarea.fraccion, "mensaje": tarea.mensaje}, status=202)


@ver
@require_GET
def planos(request, pk):
    """Los planos de la carpeta y sus subcarpetas, para dejarla «Disponible sin conexión» en el teléfono (cada uno con su versión y su
    SHA-256: el teléfono sabe cuáles ya tiene al día sin bajar nada)."""
    c = _carpeta(pk)
    filas = carpetas.planos_debajo(c)
    return _json({"carpeta": {"id": str(c.pk), "nombre": c.nombre}, "planos": [
        {"id": f["id"], "nombre": f["nombre"], "formato": f["formato"], "bytes": f["bytes"], "revision": f["revision"], "sha256": f["sha256"],
         "ruta": f["ruta"], "proyecto": f["proyecto"], "disponible": bool(f["archivo_ruta"])} for f in filas]})


@ver
@require_POST
def referencias(request, pk):
    """Las referencias externas (xref) del plano que están en la biblioteca: JSON {rutas: [...], bases: [ids de carpetas]}. Para cada
    ruta, el plano que la resuelve (misma carpeta, ruta relativa, todo el proyecto) con su versión vigente y su SHA-256, o `id: null`."""
    p = _plano(pk)
    try:
        d = _cuerpo_json_grande(request)
    except ErrorUsuario as exc:
        return _error(str(exc))
    rutas_ = d.get("rutas") or []
    bases = d.get("bases") or []
    if not (isinstance(rutas_, list) and isinstance(bases, list) and all(isinstance(x, str) and len(x) <= 1000 for x in [*rutas_, *bases])):
        return _error("La petición no es válida.")
    return _json({"referencias": carpetas.resolver_referencias(p, rutas_[:200], bases[:50])})

