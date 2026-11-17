"""Croquis de ubicación: la página (HTML fijo, que el service worker guarda) y su API JSON."""
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

from ..comun import autoria, capturas, documentos, perfiles, vistas_documentos
from ..dominio import location_sketch as ls
from ..dominio.osm_vector import CATEGORY_LABELS
from ..models import Croquis
from ..tareas import generar_croquis, vectorizar_croquis
from . import servicio

usuario_de_croquis = requiere_rol("herramientas", "croquis", "admin")
MAX_CUERPO = 400_000
MARCA_ID = "00000000-0000-0000-0000-000000000000"


def pagina(request):
    respuesta = render(request, "herramientas/croquis/editor.html", {"urls": _urls()})
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


def _urls() -> dict:
    fijas = ("contexto", "lista", "sincronizar", "importar", "avisos")
    con_id = ("detalle", "vectorizar", "exportar", "descargar", "vectores", "editable", "bloqueo", "versiones", "recuperacion", "copiar_perfil")
    urls = {n: reverse(f"herramientas:croquis_{n}") for n in fijas}
    urls.update({n: reverse(f"herramientas:croquis_{n}", kwargs={"pk": MARCA_ID}) for n in con_id})
    urls["teselas"] = reverse("herramientas:teselas", kwargs={"capa": "osm", "z": 0, "x": 0, "y": 0}).replace("/osm/0/0/0.png", "/{capa}/{z}/{x}/{y}.png")
    urls["tarea"] = reverse("tvplataforma:tarea", kwargs={"tarea_id": MARCA_ID})
    urls["cad"] = reverse("herramientas:cad_desde_croquis", kwargs={"pk": MARCA_ID})
    urls["version"] = reverse("herramientas:croquis_version", kwargs={"pk": MARCA_ID, "vid": 999999999})
    urls["restaurar-version"] = reverse("herramientas:croquis_restaurar_version", kwargs={"pk": MARCA_ID, "vid": 999999999})
    urls["papelera"] = reverse("herramientas:papelera")
    urls["perfil-activo"] = reverse("herramientas:perfil_activo")
    urls["copiar-perfil"] = urls.pop("copiar_perfil")
    return urls


def _cuerpo(request) -> dict:
    if len(request.body) > MAX_CUERPO:
        raise servicio.CroquisInvalido("La petición es demasiado grande.")
    try:
        crudo = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise servicio.CroquisInvalido("La petición no es JSON válido.") from None
    if not isinstance(crudo, dict):
        raise servicio.CroquisInvalido("La petición debe ser un objeto JSON.")
    return crudo


def _uuid(valor, nombre: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(valor))
    except ValueError:
        raise servicio.CroquisInvalido(f"«{nombre}» no es un identificador válido.") from None


def _privado(respuesta):
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta



def _en_papelera(modelo, pk, usuario):
    """El detalle de algo de la papelera responde 410 con `eliminado`: el teléfono lo distingue de «no existe» (404) y de un fallo."""
    if documentos.alcanzables(modelo, usuario).filter(pk=pk, eliminado__isnull=False).exists():
        return _privado(JsonResponse({"error": "Está en la papelera.", "eliminado": True}, status=410))
    return None



def _error(exc, estado=422):
    return JsonResponse({"error": str(exc)}, status=estado)


def _propio(request, pk) -> Croquis:
    """Un croquis de la empresa que no esté en la papelera (los croquis son compartidos), de un perfil que la cuenta puede usar (si no, 404)."""
    return get_object_or_404(documentos.alcanzables(Croquis, request.user), pk=pk, eliminado__isnull=True)


def _detalle(c, usuario) -> dict:
    return {**servicio.resumen_fila(c, usuario), "datos": c.datos, "recuperaciones": documentos.recuperaciones_de(c, usuario),
            "otro_perfil": documentos.otro_perfil(c, usuario)}


def _en_curso(c: Croquis):
    t = Tarea.objects.filter(pk=c.tarea_id).first() if c.tarea_id else None
    return t if t is not None and not t.ha_terminado else None


@usuario_de_croquis
@require_GET
def contexto(request):
    perfil = perfiles.perfil_activo(request.user)
    base = ls.SketchData()
    return _privado(JsonResponse({
        "cuenta": request.user.pk, "autor": dict(zip(("nombre", "correo"), autoria.de_usuario(request.user))), "perfil": perfil.clave, "perfil_nombre": perfil.nombre,
        "perfiles": perfiles.opciones(request.user), "limite_km2": servicio.LIMITE_KM2, "max_vertices": servicio.MAX_VERTICES,
        "capas_mapa": [{"nombre": n, "capa": "osm" if n == ls.DEFAULT_MAP_LAYER else "topo" if n.startswith("Topográfico") else None, "atribucion": c["attribution"], "zoom_max": c["max_zoom"]}
                       for n, c in ls.MAP_LAYERS.items()],
        "latido": documentos.LATIDO_SEGUNDOS, "admin": documentos.es_admin(request.user), "categorias": CATEGORY_LABELS, "contornos": list(servicio.CONTORNOS), "base": {"title": base.title, "map_layer": base.map_layer, "contour_interval": base.contour_interval,
                                                                                     "geometry": base.geometry, "layer_visibility": base.layer_visibility, "points": []},
    }))


@usuario_de_croquis
@require_GET
def lista(request):
    return vistas_documentos.lista_incremental(request, "croquis", "croquis", servicio.resumen_fila)


@usuario_de_croquis
@require_http_methods(["GET", "DELETE"])
def detalle(request, pk):
    if request.method == "GET" and (papelera := _en_papelera(Croquis, pk, request.user)):
        return papelera
    c = _propio(request, pk)
    if request.method == "DELETE":
        fallo = vistas_documentos.borrar(c, request.user)
        return fallo or _privado(JsonResponse({"borrado": str(pk)}))
    return _privado(JsonResponse(_detalle(c, request.user)))


_comunes = vistas_documentos.vistas("croquis", usuario_de_croquis, servicio.aplicar_datos_guardados, _detalle)


@usuario_de_croquis
@require_POST
def sincronizar(request):
    try:
        cuerpo = _cuerpo(request)
        id_cliente = _uuid(cuerpo.get("id_cliente"), "id_cliente")
        servidor = _uuid(cuerpo["servidor_id"], "servidor_id") if cuerpo.get("servidor_id") else None
        base = cuerpo.get("revision_base")
        if base is not None and (isinstance(base, bool) or not isinstance(base, int) or base < 1):
            raise servicio.CroquisInvalido("La revisión base no es válida.")
        sesion = str(cuerpo.get("sesion") or "")
        resultado = servicio.sincronizar(request.user, id_cliente, servidor, base, cuerpo.get("datos"), vistas_documentos.perfil_de_nuevo(request, cuerpo),
                                         sesion if documentos.sesion_valida(sesion) else "")
    except servicio.CroquisInvalido as exc:
        return _error(exc)
    return _privado(JsonResponse(resultado))


def _encolar(request, c: Croquis, tarea_fn, *args):
    previa = _en_curso(c)
    if previa is not None:                                            # un solo trabajo a la vez por croquis
        return _privado(JsonResponse({"tarea": str(previa.pk)}, status=200))
    tarea = tarea_fn.encolar(str(c.pk), *args, usuario=request.user)
    Croquis.objects.filter(pk=c.pk).update(tarea_id=tarea.pk)
    return _privado(JsonResponse({"tarea": str(tarea.pk)}, status=202))


@usuario_de_croquis
@require_POST
def vectorizar(request, pk):
    c = _propio(request, pk)
    try:
        servicio.verificar_area(c.datos)
    except servicio.CroquisInvalido as exc:
        return _error(exc)
    return _encolar(request, c, vectorizar_croquis)


@usuario_de_croquis
@require_POST
def exportar(request, pk):
    c = _propio(request, pk)
    try:
        formato = _cuerpo(request).get("formato", "")
        if formato not in ("pdf", "dxf"):
            raise servicio.CroquisInvalido("El formato debe ser pdf o dxf.")
        servicio.verificar_exportable(c)
    except servicio.SinVectores as exc:
        return _error(exc, 409)
    except servicio.CroquisInvalido as exc:
        return _error(exc)
    return _encolar(request, c, generar_croquis, formato)


@usuario_de_croquis
@require_GET
def descargar(request, pk):
    c = _propio(request, pk)
    ruta = capturas.raiz() / c.salida_ruta if c.salida_ruta else None
    if ruta is None or not ruta.is_file():
        raise Http404
    tipo = "application/pdf" if c.salida_formato == "pdf" else "image/vnd.dxf"
    respuesta = FileResponse(open(ruta, "rb"), content_type=tipo)
    respuesta["Content-Disposition"] = content_disposition_header(request.GET.get("ver") != "1", c.salida_nombre or f"CROQUIS.{c.salida_formato}")
    respuesta["Cache-Control"] = "private, no-store"
    respuesta["Access-Control-Expose-Headers"] = "Content-Disposition"
    return respuesta


@usuario_de_croquis
@require_GET
def vectores(request, pk):
    c = _propio(request, pk)
    try:
        return _privado(JsonResponse(servicio.a_geojson(c)))
    except servicio.SinVectores as exc:
        return _error(exc, 409)
    except (OSError, ValueError):
        raise Http404 from None


@usuario_de_croquis
@require_GET
def editable(request, pk):
    """Proyecto completo para el módulo local (el `EDITABLE_*.json` / `RECUPERACION_CROQUIS_*.json` del escritorio)."""
    c = _propio(request, pk)
    nombre = servicio.nombre_salida(c, "json").replace("CROQUIS", "EDITABLE_CROQUIS", 1)
    respuesta = HttpResponse(json.dumps(servicio.editable(c), ensure_ascii=False, indent=2), content_type="application/json; charset=utf-8")
    respuesta["Content-Disposition"] = content_disposition_header(True, nombre)
    return _privado(respuesta)


@usuario_de_croquis
@require_POST
def importar(request):
    """Lee un CSV de puntos o un KML/KMZ de predio y devuelve los vértices (no guarda nada: el editor los pone en el borrador)."""
    archivo = request.FILES.get("archivo")
    if archivo is None or archivo.size > 20 * 1024 * 1024:
        return _error(servicio.CroquisInvalido("Elige un archivo .csv, .kml o .kmz de hasta 20 MB."))
    nombre = archivo.name.lower()
    try:
        if nombre.endswith((".kml", ".kmz")):
            puntos, cuantos = servicio.poligono_de_kml(archivo.read(), nombre)
            return _privado(JsonResponse({"puntos": puntos, "poligonos": cuantos, "reemplaza": True}))
        if nombre.endswith(".csv") or nombre.endswith(".txt"):
            return _privado(JsonResponse({"puntos": servicio.puntos_de_csv(archivo.read()), "reemplaza": False}))
    except servicio.CroquisInvalido as exc:
        return _error(exc)
    return _error(servicio.CroquisInvalido("El archivo debe ser .csv, .kml o .kmz."))
