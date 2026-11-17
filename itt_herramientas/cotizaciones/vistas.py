"""Cotizaciones: la página (HTML fijo, igual para todas las cuentas, que el service worker guarda para abrirla sin
conexión) y la API JSON que usa. Las URL se obtienen con `reverse("herramientas:…")`."""
from __future__ import annotations

import json
import uuid

from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from tvplataforma.permisos import requiere_rol

from ..comun import autoria, diccionarios, documentos, perfiles, vistas_documentos, zips
from ..models import Cotizacion, Machote
from . import servicio

usuario_de_cotizaciones = requiere_rol("herramientas", "cotizaciones", "admin")
MAX_CUERPO = 600_000


def pagina(request):
    """HTML idéntico para todas las cuentas: los datos llegan por la API (y de IndexedDB sin conexión)."""
    respuesta = render(request, "herramientas/cotizaciones/editor.html", {"urls": _urls()})
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


MARCA_ID = "00000000-0000-0000-0000-000000000000"


def _urls() -> dict:
    """URL de la API para el editor. Las que llevan identificador usan una marca que el JS sustituye (nada escrito a mano)."""
    fijas = ("contexto", "lista", "sincronizar", "pdf", "ortografia", "traducir", "palabra", "machotes", "machotes_exportar", "machotes_importar", "importar", "zip",
             "avisos")
    con_id = ("detalle", "duplicar", "editable", "machote", "machote_restaurar", "bloqueo", "versiones", "recuperacion", "copiar_perfil")
    urls = {n.replace("_", "-"): reverse(f"herramientas:cotizaciones_{n}") for n in fijas}
    urls.update({n.replace("_", "-"): reverse(f"herramientas:cotizaciones_{n}", kwargs={"pk": MARCA_ID}) for n in con_id})
    urls["foto"] = reverse("herramientas:cotizaciones_foto", kwargs={"sha256": "SHA"})
    urls["version"] = reverse("herramientas:cotizaciones_version", kwargs={"pk": MARCA_ID, "vid": 999999999})
    urls["restaurar-version"] = reverse("herramientas:cotizaciones_restaurar_version", kwargs={"pk": MARCA_ID, "vid": 999999999})
    urls["version-pdf"] = reverse("herramientas:cotizaciones_version_pdf", kwargs={"pk": MARCA_ID, "vid": 999999999})
    urls["papelera"] = reverse("herramientas:papelera")
    urls["perfil-activo"] = reverse("herramientas:perfil_activo")
    urls["capturar"] = reverse("herramientas:capturar")
    urls["tarea"] = reverse("tvplataforma:tarea", kwargs={"tarea_id": MARCA_ID})
    return urls


def _cuerpo(request) -> dict:
    if len(request.body) > MAX_CUERPO:
        raise servicio.CotizacionInvalida("La petición es demasiado grande.")
    try:
        crudo = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise servicio.CotizacionInvalida("La petición no es JSON válido.") from None
    if not isinstance(crudo, dict):
        raise servicio.CotizacionInvalida("La petición debe ser un objeto JSON.")
    return crudo


def _uuid(valor, nombre: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(valor))
    except ValueError:
        raise servicio.CotizacionInvalida(f"«{nombre}» no es un identificador válido.") from None


def _error(exc: Exception, estado: int = 400) -> JsonResponse:
    return JsonResponse({"error": str(exc)}, status=estado)


def _privado(respuesta):
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta



def _en_papelera(modelo, pk, usuario):
    """El detalle de algo de la papelera responde 410 con `eliminado`: el teléfono lo distingue de «no existe» (404) y de un fallo."""
    if documentos.alcanzables(modelo, usuario).filter(pk=pk, eliminado__isnull=False).exists():
        return _privado(JsonResponse({"error": "Está en la papelera.", "eliminado": True}, status=410))
    return None



@usuario_de_cotizaciones
@require_GET
def contexto(request):
    """Todo lo que el editor necesita para trabajar sin conexión: machotes de la empresa, valores del perfil activo, los perfiles que la cuenta
    puede usar y unidades. El folio no: lo asigna el servidor al sincronizar (es único en la serie del perfil)."""
    perfil = perfiles.perfil_activo(request.user)
    base = perfiles.nueva_cotizacion(perfil).to_dict()
    for clave in ("items", "images"):
        base.pop(clave, None)
    return _privado(JsonResponse({
        "cuenta": request.user.pk, "autor": dict(zip(("nombre", "correo"), autoria.de_usuario(request.user))), "perfil": perfil.clave, "perfil_nombre": perfil.nombre,
        "perfiles": perfiles.opciones(request.user), "base": base, "latido": documentos.LATIDO_SEGUNDOS, "admin": documentos.es_admin(request.user),
        "plantillas": servicio.plantillas_integradas(),
        "machotes": [servicio.fila_machote(m, request.user) for m in Machote.objects.order_by("nombre")],
        "unidades": servicio.UNIDADES, "limites": {"conceptos": servicio.MAX_CONCEPTOS, "incisos": servicio.MAX_INCISOS, "imagenes": servicio.MAX_IMAGENES},
    }))


@usuario_de_cotizaciones
@require_GET
def lista(request):
    """Las cotizaciones del perfil activo que cambiaron desde `?desde=` (lista incremental, `vistas_documentos.lista_incremental`)."""
    return vistas_documentos.lista_incremental(request, "cotizacion", "cotizaciones", servicio.resumen_fila)


def _detalle(c, usuario) -> dict:
    return {**servicio.resumen_fila(c, usuario), "datos": c.datos, "recuperaciones": documentos.recuperaciones_de(c, usuario),
            "otro_perfil": documentos.otro_perfil(c, usuario)}


def _visible(pk, usuario) -> Cotizacion:
    """Una cotización que la cuenta puede abrir (de un perfil que puede usar); de otro perfil, no existe para ella (404)."""
    return get_object_or_404(documentos.alcanzables(Cotizacion, usuario), pk=pk, eliminado__isnull=True)


@usuario_de_cotizaciones
@require_http_methods(["GET", "DELETE"])
def detalle(request, pk):
    if request.method == "GET" and (papelera := _en_papelera(Cotizacion, pk, request.user)):
        return papelera
    c = _visible(pk, request.user)
    if request.method == "DELETE":
        fallo = vistas_documentos.borrar(c, request.user)
        return fallo or _privado(JsonResponse({"borrada": str(pk)}))
    return _privado(JsonResponse(_detalle(c, request.user)))


@usuario_de_cotizaciones
@require_POST
def duplicar(request, pk):
    copia = servicio.duplicar(request.user, _visible(pk, request.user))
    return _privado(JsonResponse(_detalle(copia, request.user), status=201))


_comunes = vistas_documentos.vistas("cotizacion", usuario_de_cotizaciones, servicio.aplicar_datos_guardados, _detalle, servicio.preparar_para_perfil)


@usuario_de_cotizaciones
@require_GET
def version_pdf(request, pk, vid):
    """El PDF de una versión del historial, tal como era (con el perfil de la cotización)."""
    from django.utils.http import content_disposition_header

    from ..models import Perfil

    c = _visible(pk, request.user)
    v = documentos.version_de(c, vid)
    if v is None:
        raise Http404
    perfil = Perfil.objects.filter(clave=v.perfil or c.perfil).first() or Perfil.objects.get(clave=perfiles.PERFIL_POR_OMISION)
    try:
        contenido, nombre, _ = servicio.pdf_de(c.cuenta_id, perfil, v.datos, c, servicio.imagenes_de(v.datos))     # las de esa versión
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    respuesta = HttpResponse(contenido, content_type="application/pdf")
    respuesta["Content-Disposition"] = content_disposition_header(request.GET.get("descargar") == "1", nombre.replace(".pdf", f" (versión {v.revision}).pdf"))
    return _privado(respuesta)


@usuario_de_cotizaciones
@require_POST
def sincronizar(request):
    """Aplica un borrador del teléfono. Idempotente por `id_cliente`; ante un conflicto crea una COPIA, nunca pisa."""
    try:
        cuerpo = _cuerpo(request)
        id_cliente = _uuid(cuerpo.get("id_cliente"), "id_cliente")
        servidor = _uuid(cuerpo["servidor_id"], "servidor_id") if cuerpo.get("servidor_id") else None
        base = cuerpo.get("revision_base")
        if base is not None and (isinstance(base, bool) or not isinstance(base, int) or base < 1):
            raise servicio.CotizacionInvalida("La revisión base no es válida.")
        sesion = str(cuerpo.get("sesion") or "")
        resultado = servicio.sincronizar(request.user, id_cliente, servidor, base, cuerpo.get("datos"), vistas_documentos.perfil_de_nuevo(request, cuerpo),
                                         sesion if documentos.sesion_valida(sesion) else "")
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    return _privado(JsonResponse(vistas_documentos.con_aviso_de_perfil(request, cuerpo, resultado)))


@usuario_de_cotizaciones
@require_POST
def pdf(request):
    """PDF del borrador que se está editando (no hace falta guardarlo antes) con el perfil activo de la cuenta.

    El folio es el del servidor (`servidor_id`): una cotización que aún no se sincroniza no tiene folio, así que su vista previa dice
    «BORRADOR» y el PDF final (`?exportar=1`) espera a que se sincronice. Sin `servidor_id` (editable suelto) vale el de los datos."""
    try:
        cuerpo = _cuerpo(request)
        datos = cuerpo.get("datos")
        folio = str(datos.get("quote_number") or "").strip() if isinstance(datos, dict) else ""
        c = None
        if cuerpo.get("servidor_id"):
            c = documentos.alcanzables(Cotizacion, request.user).filter(pk=_uuid(cuerpo["servidor_id"], "servidor_id"), eliminado__isnull=True).first()
            folio = c.folio if c is not None else ""
        if isinstance(datos, dict) and datos.get("quote_number") != (folio or "BORRADOR"):
            datos = {**datos, "quote_number": folio or "BORRADOR"}
        if not folio and request.GET.get("exportar") == "1":
            return _error(servicio.CotizacionInvalida("Esta cotización aún no tiene folio: se asigna al sincronizar. Conéctate un momento y vuelve a generar el PDF."), 409)
        contenido, nombre, pendientes = servicio.generar_pdf(request.user, datos, c, vistas_documentos.perfil_de_nuevo(request, cuerpo))
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    respuesta = HttpResponse(contenido, content_type="application/pdf")
    from django.utils.http import content_disposition_header

    respuesta["X-Drive"] = "no"
    if request.GET.get("exportar") == "1":                                       # el PDF final (no la vista previa): a Drive el PDF y su editable, como el escritorio
        from ..drive import servicio as drive

        editable = nombre.replace("COTIZACION", "EDITABLE_COTIZACION", 1).rsplit(".", 1)[0] + ".json"
        clave = drive.clave_de("cotizacion", json.dumps(datos, sort_keys=True), c.perfil if c is not None else vistas_documentos.perfil_de_nuevo(request, cuerpo))
        e1 = drive.encolar(request.user.pk, "cotizaciones", nombre, contenido=contenido, clave=clave)
        e2 = drive.encolar(request.user.pk, "cotizaciones", editable, contenido=json.dumps(datos, ensure_ascii=False, indent=2).encode("utf-8"), clave=clave)
        respuesta["X-Drive"] = "encolado" if (e1 or e2) else "no"

    respuesta["Content-Disposition"] = content_disposition_header(request.GET.get("descargar") == "1", nombre)
    respuesta["X-Imagenes-Pendientes"] = str(len(pendientes))
    respuesta["Access-Control-Expose-Headers"] = "X-Imagenes-Pendientes, Content-Disposition, X-Drive"
    return _privado(respuesta)


@usuario_de_cotizaciones
@require_POST
def ortografia(request):
    try:
        cuerpo = _cuerpo(request)
        problemas = servicio.revisar_ortografia(request.user, cuerpo.get("idioma", "es"), cuerpo.get("campos", []))
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    return _privado(JsonResponse({"problemas": problemas}))


@usuario_de_cotizaciones
@require_POST
def palabra(request):
    """Agrega una palabra al diccionario personal de la cuenta («Agregar al diccionario» del corrector)."""
    try:
        cuerpo = _cuerpo(request)
        palabras = diccionarios.agregar_palabra(request.user.pk, str(cuerpo.get("palabra", "")), cuerpo.get("idioma", "es"))
    except (servicio.CotizacionInvalida, diccionarios.PalabraInvalida) as exc:
        return _error(exc, 422)
    return _privado(JsonResponse({"palabras": palabras}))


@usuario_de_cotizaciones
@require_http_methods(["GET", "POST"])
def machotes(request):
    if request.method == "GET":
        return _privado(JsonResponse({"machotes": [servicio.fila_machote(m, request.user) for m in Machote.objects.order_by("nombre")]}))
    try:
        cuerpo = _cuerpo(request)
        m = servicio.guardar_machote(request.user, cuerpo.get("nombre", ""), cuerpo.get("idioma", "es"), cuerpo.get("concepto"), cuerpo.get("id"))
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    return _privado(JsonResponse(servicio.fila_machote(m, request.user)))


@usuario_de_cotizaciones
@require_http_methods(["DELETE"])
def machote(request, pk):
    """Los machotes son de la empresa: los elimina quien los creó o quien administra las herramientas."""
    m = get_object_or_404(Machote, plantilla_id=pk)
    if not servicio.puede_borrar_machote(request.user, m):
        return _error(servicio.CotizacionInvalida(f"Solo {m.autor_nombre or 'quien lo creó'} o quien administra las herramientas puede eliminar este machote."), 409)
    m.delete()
    documentos.log.info("Documentos: la cuenta %s eliminó el machote %s", request.user.pk, pk)
    return _privado(JsonResponse({"borrado": str(pk)}))


@usuario_de_cotizaciones
@require_POST
def machote_restaurar(request, pk):
    """Regresa el concepto del machote a la versión que tenía antes de la última sobrescritura."""
    m = get_object_or_404(Machote, plantilla_id=pk)
    if not m.anterior:
        return _error(servicio.CotizacionInvalida("Este machote no tiene una versión anterior."), 409)
    m.concepto, m.anterior = m.anterior, None
    m.modificado_por_id, m.modificado_por_nombre = request.user.pk, autoria.de_usuario(request.user)[0]
    m.save()
    return _privado(JsonResponse(servicio.fila_machote(m, request.user)))


@usuario_de_cotizaciones
@require_GET
def machotes_exportar(request):
    ids = None
    if request.GET.getlist("id"):
        try:
            ids = [uuid.UUID(v) for v in request.GET.getlist("id")[:500]]
        except ValueError:
            return _error(servicio.CotizacionInvalida("El machote no es válido."), 422)
    cuerpo = json.dumps(servicio.exportar_machotes(request.user, ids), ensure_ascii=False, indent=2)
    respuesta = HttpResponse(cuerpo, content_type="application/json; charset=utf-8")
    respuesta["Content-Disposition"] = 'attachment; filename="machotes_cotizacion.json"'
    return _privado(respuesta)


@usuario_de_cotizaciones
@require_POST
def machotes_importar(request):
    archivo = request.FILES.get("archivo")
    if archivo is None or archivo.size > MAX_CUERPO * 4:
        return _error(servicio.CotizacionInvalida("Elige un archivo de machotes (.json)."), 422)
    try:
        agregados, renombrados = servicio.importar_machotes(request.user, archivo.read())
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    return _privado(JsonResponse({"agregados": agregados, "renombrados": renombrados}))


@usuario_de_cotizaciones
@require_GET
def editable(request, pk):
    """El JSON editable de la cotización, compatible con el que guarda el escritorio (`EDITABLE_COTIZACION_*.json`)."""
    c = _visible(pk, request.user)
    nombre = servicio.nombre_pdf(servicio.validar(c.datos)).replace("COTIZACION", "EDITABLE_COTIZACION", 1).rsplit(".", 1)[0] + ".json"
    from django.utils.http import content_disposition_header

    respuesta = HttpResponse(json.dumps(c.datos, ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    respuesta["Content-Disposition"] = content_disposition_header(True, nombre)
    return _privado(respuesta)


@usuario_de_cotizaciones
@require_POST
def importar(request):
    archivo = request.FILES.get("archivo")
    if archivo is None or archivo.size > MAX_CUERPO:
        return _error(servicio.CotizacionInvalida("Elige un archivo editable (.json) de una cotización."), 422)
    try:
        c = servicio.importar_editable(request.user, archivo.read(), perfiles.perfil_activo(request.user).clave)
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    return _privado(JsonResponse({**_detalle(c, request.user), "folio_original": c.folio_original}, status=201))


@usuario_de_cotizaciones
@require_POST
def traducir(request):
    try:
        cuerpo = _cuerpo(request)
        datos = servicio.traducir(cuerpo.get("datos"), cuerpo.get("idioma", "es"))
    except servicio.CotizacionInvalida as exc:
        return _error(exc, 422)
    return _privado(JsonResponse({"datos": datos}))


@usuario_de_cotizaciones
@require_GET
def foto(request, sha256):
    """La foto de referencia de una cotización: de esta cuenta o, con `?doc=<id>`, la que usa esa cotización de la empresa (si la subió
    alguien que la hizo o la editó)."""
    doc = None
    if request.GET.get("doc"):
        try:
            doc = documentos.alcanzables(Cotizacion, request.user).filter(pk=uuid.UUID(request.GET["doc"]), eliminado__isnull=True).first()
        except ValueError:
            raise Http404 from None
    ruta = servicio.ruta_foto(request.user, sha256, doc)
    if ruta is None:
        raise Http404
    respuesta = FileResponse(open(ruta, "rb"), content_type="image/png" if ruta.suffix == ".png" else "image/jpeg")
    respuesta["Cache-Control"] = "private, max-age=86400"
    return respuesta


@usuario_de_cotizaciones
@require_POST
def zip_lote(request):
    """Varias cotizaciones del perfil activo en un ZIP de PDF: `{"elementos": [["cotizacion", id], …]}` (hasta 50)."""
    try:
        elementos = zips.cuerpo_json(request).get("elementos")
    except zips.LoteInvalido as exc:
        return _error(exc, 422)
    return zips.responder(request, elementos, "cotizaciones")
