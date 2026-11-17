"""Supervisión: lista, consulta de solo lectura y PDF de los documentos de la empresa. Rol `supervision` (o el propietario).

Las vistas de consulta son de lectura. Las POST: el PDF de un reporte cuyo último PDF no corresponde a su versión actual (se genera aparte
y el reporte no cambia) y «Asignar folio nuevo» a una cotización con folio repetido de antes de compartir (solo el propietario o quien
administra las herramientas).

Aquí vive también la papelera de documentos (cotizaciones, reportes y croquis), para quien edita o supervisa.
"""
from __future__ import annotations

import re

from django.core.paginator import Paginator
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_POST
from tvplataforma.permisos import requiere_rol

from ..comun import capturas, documentos, perfiles, zips
from ..cotizaciones import servicio as cot
from ..models import TIPOS_DOCUMENTO, Cotizacion, Perfil, Reporte
from ..reportes import servicio as rep
from ..tareas import generar_reporte_supervision
from . import servicio

#: Supervisión: quien supervisa y quien administra las herramientas (que además decide los folios repetidos).
supervisor = requiere_rol("herramientas", "supervision", "admin")
con_documentos = requiere_rol("herramientas", "cotizaciones", "reportes", "croquis", "admin", "supervision")


def decide_folios(usuario) -> bool:
    """«Asignar folio nuevo» lo decide el propietario (o quien administra las herramientas)."""
    return bool(usuario.is_superuser) or documentos.es_admin(usuario)
_SHA = re.compile(r"^[0-9a-f]{64}$")


def _privado(respuesta):
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


def _de_la_cuenta(request, modelo, pk, **filtros):
    """Un documento que la cuenta puede abrir: de un perfil que puede usar (de otro, 404)."""
    return get_object_or_404(documentos.alcanzables(modelo, request.user), pk=pk, **filtros)


def _otro_perfil(request, doc):
    """Abrir por su dirección un documento de otro perfil que la cuenta puede usar: lo dice y ofrece cambiar (nunca se muestra mezclado)."""
    otro = documentos.otro_perfil(doc, request.user)
    if otro is None:
        return None
    return _privado(render(request, "herramientas/supervision/otro_perfil.html", {"otro": otro, "activo": perfiles.nombre_de(perfiles.clave_activa(request.user)),
                                                                                  "titulo": documentos.referencia(doc)}))


def _autor(doc) -> dict:
    return {"nombre": doc.autor_nombre, "correo": doc.autor_correo, "creada": doc.creada, "actualizada": doc.actualizada,
            "eliminada": not servicio.cuenta_existe(doc.cuenta_id), "modificado": doc.modificado_por_nombre or doc.autor_nombre}


@supervisor
@require_GET
def lista(request):
    request.tv_seccion = "supervision"
    perfil = perfiles.clave_activa(request.user)
    filtros = servicio.Filtros.de(request.GET, perfil)
    pagina = Paginator(servicio.consulta(filtros), servicio.POR_PAGINA).get_page(request.GET.get("pagina"))
    hay_documentos = filtros.activos or Cotizacion.objects.filter(perfil=perfil).exists() or Reporte.objects.filter(perfil=perfil).exists()
    return _privado(render(request, "herramientas/supervision/lista.html", {
        "filtros": filtros, "pagina": pagina, "filas": servicio.filas(pagina.object_list), "autores": servicio.autores(perfil),
        "parametros": filtros.como_parametros(), "hay_documentos": hay_documentos, "clientes": servicio.clientes(perfil),
        "estados": servicio.ESTADOS, "ordenes": servicio.ORDENES, "maximo_zip": zips.MAXIMO,
        "repetidos": cot.repetidos(perfil), "decide_folios": decide_folios(request.user)}))


@supervisor
@require_POST
def asignar_folio(request, pk):
    """«Asignar folio nuevo» a una cotización marcada con folio repetido. Nunca pasa solo: lo pide el propietario aquí."""
    from django.contrib import messages

    if not decide_folios(request.user):
        return _privado(HttpResponse("Solo el propietario o quien administra las herramientas puede asignar folios.", status=403))
    c = _de_la_cuenta(request, Cotizacion, pk, eliminado__isnull=True)
    anterior = c.folio
    try:
        c = cot.asignar_folio_nuevo(c, request.user)
        messages.success(request, f"La cotización que tenía {anterior} ahora es {c.folio}.")
    except cot.CotizacionInvalida as exc:
        messages.error(request, str(exc))
    return HttpResponseRedirect(reverse("herramientas:supervision") + "#sup-repetidos")


def _cotizacion(request, pk) -> tuple[Cotizacion, object]:
    c = _de_la_cuenta(request, Cotizacion, pk, eliminado__isnull=True)                # lo de la papelera no se consulta
    try:
        q = cot.validar(c.datos)
    except cot.CotizacionInvalida:
        q = None
    return c, q


@supervisor
@require_GET
def cotizacion(request, pk):
    request.tv_seccion = "supervision"
    c, q = _cotizacion(request, pk)
    if (aviso := _otro_perfil(request, c)) is not None:
        return aviso
    imagenes = []
    if q is not None:
        for i in q.images:
            m = re.match(r"^captura:([0-9a-f]{64})$", i.path)
            imagenes.append({"pie": i.caption, "url": reverse("herramientas:supervision_cotizacion_foto", kwargs={"pk": c.pk, "sha256": m.group(1)}) if m else ""})
    return _privado(render(request, "herramientas/supervision/cotizacion.html", {"c": c, "q": q, "autor": _autor(c), "imagenes": imagenes}))


@supervisor
@require_GET
def cotizacion_pdf(request, pk):
    c, q = _cotizacion(request, pk)
    if q is None:
        raise Http404
    perfil = Perfil.objects.filter(clave=c.perfil).first() or Perfil.objects.get(clave="grupo_itt")
    contenido, nombre, pendientes = cot.pdf_de(c.cuenta_id, perfil, c.datos, c)
    respuesta = HttpResponse(contenido, content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(request.GET.get("descargar") == "1", nombre)
    respuesta["X-Imagenes-Pendientes"] = str(len(pendientes))
    return _privado(respuesta)


@supervisor
@require_GET
def cotizacion_foto(request, pk, sha256):
    c, q = _cotizacion(request, pk)
    if q is None or not _SHA.match(sha256) or f"captura:{sha256}" not in {i.path for i in q.images}:
        raise Http404
    return _foto(request, c, sha256)


def _foto(request, documento, sha256: str):
    """La foto de un documento de la empresa, la haya subido quien sea de los que lo hicieron o editaron (`capturas.ruta_autorizada`)."""
    ruta = capturas.ruta_autorizada(sha256, documento) if request.GET.get("original") == "1" else capturas.miniatura(None, sha256, documento=documento)
    if ruta is None:
        raise Http404
    respuesta = FileResponse(open(ruta, "rb"), content_type="image/png" if ruta.suffix == ".png" else "image/jpeg")
    respuesta["Cache-Control"] = "private, max-age=86400"
    return respuesta


def _pdf_listo(r: Reporte):
    return rep.pdf_vigente(r, supervision=True)


@supervisor
@require_GET
def reporte(request, pk):
    request.tv_seccion = "supervision"
    r = _de_la_cuenta(request, Reporte, pk, eliminado__isnull=True)
    if (aviso := _otro_perfil(request, r)) is not None:
        return aviso
    try:
        datos = rep.validar(r.datos)
    except rep.ReporteInvalido:
        datos = {"titulo": r.titulo, "fecha": "", "fotos": [], "con_croquis": False, "con_credenciales": False, "fotos_por_pagina": 1}
    fotos = [{**f, "url": reverse("herramientas:supervision_reporte_foto", kwargs={"pk": r.pk, "sha256": f["sha256"]})} for f in datos["fotos"]]
    return _privado(render(request, "herramientas/supervision/reporte.html", {
        "r": r, "datos": datos, "fotos": fotos, "autor": _autor(r), "pdf_listo": _pdf_listo(r) is not None,
        "url_tarea": reverse("tvplataforma:tarea", kwargs={"tarea_id": "00000000-0000-0000-0000-000000000000"})}))


@supervisor
@require_POST
def reporte_pdf(request, pk):
    """El PDF de la versión actual: listo → su URL; si no, se genera (202 con la tarea). 409/422 si aún no se puede."""
    r = _de_la_cuenta(request, Reporte, pk, eliminado__isnull=True)
    descarga = reverse("herramientas:supervision_reporte_descargar", kwargs={"pk": r.pk}) + f"?revision={r.revision}"
    if _pdf_listo(r) is not None:
        return _privado(JsonResponse({"url": descarga}))
    try:
        rep.verificar_listo(r)
    except rep.FotosPendientes:
        return _privado(JsonResponse({"error": "Faltan fotos por subir de este reporte. Inténtalo cuando su autor tenga señal."}, status=409))
    except rep.ReporteInvalido as exc:
        return _privado(JsonResponse({"error": str(exc)}, status=422))
    tarea = generar_reporte_supervision.encolar(str(r.pk), usuario=request.user)
    if tarea.ha_terminado and _pdf_listo(r) is not None:                    # modo síncrono (pruebas): ya está
        return _privado(JsonResponse({"url": descarga, "tarea": str(tarea.pk)}))
    return _privado(JsonResponse({"tarea": str(tarea.pk)}, status=202))


@supervisor
@require_GET
def reporte_descargar(request, pk):
    r = _de_la_cuenta(request, Reporte, pk, eliminado__isnull=True)
    ruta = _pdf_listo(r)
    if ruta is None or request.GET.get("revision", str(r.revision)) != str(r.revision):
        raise Http404
    respuesta = FileResponse(open(ruta, "rb"), content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(request.GET.get("ver") != "1", rep.nombre_pdf(r.titulo or "Reporte"))
    return _privado(respuesta)


@supervisor
@require_GET
def reporte_foto(request, pk, sha256):
    r = _de_la_cuenta(request, Reporte, pk, eliminado__isnull=True)
    if not _SHA.match(sha256) or sha256 not in {f.get("sha256") for f in (r.datos or {}).get("fotos", []) if isinstance(f, dict)}:
        raise Http404
    return _foto(request, r, sha256)


@supervisor
@require_POST
def zip_lote(request):
    """ZIP con los PDF de documentos de cualquier cuenta: `{"elementos": [[tipo, id], …]}` o `{"todos": true, "filtros": {…}}`
    (todos los resultados de esos filtros). Hasta 50. Solo lee: los reportes sin PDF al día se generan aparte, como al descargar uno."""
    try:
        cuerpo = zips.cuerpo_json(request)
    except zips.LoteInvalido as exc:
        return _privado(JsonResponse({"error": str(exc)}, status=422))
    elementos = cuerpo.get("elementos")
    if cuerpo.get("todos"):
        filtros = servicio.Filtros.de(cuerpo.get("filtros") if isinstance(cuerpo.get("filtros"), dict) else {}, perfiles.clave_activa(request.user))
        elementos = servicio.elementos_de(filtros, zips.MAXIMO)
        if len(elementos) > zips.MAXIMO:
            total = servicio.consulta(filtros).count()
            return _privado(JsonResponse({"error": f"Son {total} documentos; el máximo por descarga es {zips.MAXIMO}. Ajusta los filtros."}, status=422))
    return zips.responder(request, elementos, "documentos", supervision=True)


# -- Papelera de documentos --------------------------------------------------------------------------------------------------------

def _tipos_de(usuario) -> list[str]:
    from tvplataforma import permisos

    """Los tipos de la papelera de la cuenta: los de sus herramientas y, si supervisa, cotizaciones y reportes (lo que supervisa)."""
    if documentos.es_admin(usuario):
        return list(documentos.MODELOS)
    supervisa = permisos.tiene_rol(usuario, "herramientas", "supervision")
    return [t for t in documentos.MODELOS if documentos.puede_editar(usuario, t) or (supervisa and t in ("cotizacion", "reporte"))]


def _titulo(tipo: str, d) -> str:
    if tipo == "cotizacion":
        return " · ".join(x for x in (d.folio, d.proyecto) if x) or "Cotización"
    return d.titulo or ("Reporte" if tipo == "reporte" else "Croquis")


@con_documentos
@require_GET
def papelera(request):
    """Los documentos eliminados de las herramientas de la cuenta, del perfil activo. Se borran para siempre a los 30 días."""
    filas = []
    for tipo, d in documentos.papelera(request.user, _tipos_de(request.user)):
        filas.append({"tipo": tipo, "tipo_nombre": dict(TIPOS_DOCUMENTO)[tipo], "id": d.pk, "titulo": _titulo(tipo, d), "autor": d.autor_nombre or "Cuenta eliminada", "eliminado": d.eliminado,
                      "eliminado_por": d.eliminado_por_nombre or "—", "vence": documentos.vence_en(d),
                      "puede_recuperar": documentos.puede_recuperar(request.user, d), "puede_destruir": documentos.es_admin(request.user),
                      "dialogo": f"doc-destruir-{tipo}-{d.pk}"})
    return _privado(render(request, "herramientas/supervision/papelera.html", {"filas": filas, "dias": documentos.PAPELERA_DIAS}))


def _de_papelera(request, tipo, pk):
    if tipo not in documentos.MODELOS:
        raise Http404
    return _de_la_cuenta(request, documentos.MODELOS[tipo], pk, eliminado__isnull=False)


@con_documentos
@require_POST
def papelera_recuperar(request, tipo, pk):
    from django.contrib import messages

    d = _de_papelera(request, tipo, pk)
    try:
        documentos.recuperar(d, request.user)
        messages.success(request, f"«{_titulo(tipo, d)}» volvió a su herramienta.")
    except documentos.SinPermiso as exc:
        messages.error(request, str(exc))
    return HttpResponseRedirect(reverse("herramientas:papelera"))


@con_documentos
@require_POST
def papelera_eliminar(request, tipo, pk):
    """Eliminar para siempre (con su historial): solo quien administra las herramientas."""
    from django.contrib import messages

    d = _de_papelera(request, tipo, pk)
    if not documentos.es_admin(request.user):
        messages.error(request, "Solo quien administra las herramientas puede eliminar para siempre.")
    else:
        titulo = _titulo(tipo, d)
        documentos.destruir(d)
        documentos.log.info("Documentos: la cuenta %s eliminó para siempre %s %s", request.user.pk, tipo, pk)
        messages.success(request, f"«{titulo}» se eliminó para siempre.")
    return HttpResponseRedirect(reverse("herramientas:papelera"))
