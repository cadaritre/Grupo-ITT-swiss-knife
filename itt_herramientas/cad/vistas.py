"""CAD: biblioteca compartida de planos y editor (Open CAD Studio web, embebido en un marco del mismo origen).

Roles: `cad` (abre, crea, sube, edita y guarda; elimina lo suyo), `admin` (además elimina cualquier plano, vacía la papelera y toma el
control de un plano en edición) y `supervision` (solo lectura: abre, descarga e imprime).
"""
from __future__ import annotations

import json
import mimetypes
import re
from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from tvplataforma.permisos import requiere_rol

from ..comun import zips
from ..comun.errores import ErrorUsuario
from ..models import Plano, VersionPlano
from . import editor, nativo, salidas, servicio

ver = requiere_rol("herramientas", "cad", "admin", "supervision")
editar = requiere_rol("herramientas", "cad", "admin")
POR_PAGINA = 25
ORDENES = {"recientes": "Modificados recientemente", "nombre": "Nombre", "proyecto": "Proyecto o cliente", "tamano": "Tamaño"}
MARCA = "00000000-0000-0000-0000-000000000000"


def _privado(respuesta):
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


def _json(datos, status=200):
    return _privado(JsonResponse(datos, status=status))


def _error(texto: str, status=422, **extra):
    return _json({"error": texto, **extra}, status=status)


def _plano(pk, eliminado=False) -> Plano:
    p = Plano.objects.filter(pk=pk, eliminado__isnull=not eliminado).first()
    if p is None:
        raise Http404
    return p


def _cuerpo_json(request) -> dict:
    if len(request.body) > 16_000:
        raise ErrorUsuario("La petición es demasiado grande.")
    try:
        datos = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise ErrorUsuario("La petición no es válida.") from None
    if not isinstance(datos, dict):
        raise ErrorUsuario("La petición no es válida.")
    return datos


# -- Lista -------------------------------------------------------------------------------------------------------------------------

class Filtros:
    def __init__(self, g):
        self.q = " ".join(str(g.get("q", "")).split())[:80]
        self.formato = g.get("formato", "") if g.get("formato") in servicio.FORMATOS else ""
        self.proyecto = servicio.proyecto_limpio(g.get("proyecto", ""))
        self.orden = g.get("orden", "") if g.get("orden") in ORDENES else "recientes"
        autor = str(g.get("autor", "")).strip()
        self.autor = int(autor) if autor.isdigit() else None
        self.avisos = []
        self.desde = self.hasta = None
        for nombre in ("desde", "hasta"):
            valor = str(g.get(nombre, "")).strip()
            if valor:
                try:
                    setattr(self, nombre, date.fromisoformat(valor))
                except ValueError:
                    self.avisos.append("Una de las fechas no es válida; se ignoró.")
        if self.desde and self.hasta and self.desde > self.hasta:
            self.desde, self.hasta = self.hasta, self.desde

    @property
    def activos(self) -> bool:
        return bool(self.q or self.formato or self.proyecto or self.autor is not None or self.desde or self.hasta)

    @property
    def avanzados(self) -> bool:
        return bool(self.formato or self.autor is not None or self.desde or self.hasta or self.orden != "recientes")

    def aplicar(self, consulta):
        if self.q:
            consulta = consulta.filter(Q(nombre__icontains=self.q) | Q(proyecto__icontains=self.q) | Q(autor_nombre__icontains=self.q)
                                       | Q(modificado_por_nombre__icontains=self.q))
        if self.formato:
            consulta = consulta.filter(formato=self.formato)
        if self.proyecto:
            consulta = consulta.filter(proyecto__iexact=self.proyecto)
        if self.autor is not None:
            consulta = consulta.filter(cuenta_id=self.autor)
        zona = timezone.get_current_timezone()
        if self.desde:
            consulta = consulta.filter(actualizado__gte=timezone.make_aware(timezone.datetime.combine(self.desde, timezone.datetime.min.time()), zona))
        if self.hasta:
            consulta = consulta.filter(actualizado__lt=timezone.make_aware(timezone.datetime.combine(self.hasta + timedelta(days=1), timezone.datetime.min.time()), zona))
        orden = {"recientes": ("-actualizado",), "nombre": ("nombre", "-actualizado"), "proyecto": ("proyecto", "nombre"), "tamano": ("-bytes", "nombre")}[self.orden]
        return consulta.order_by(*orden, "pk")


def _autores() -> list[dict]:
    vistos: dict[int, dict] = {}
    for cuenta, nombre, correo in Plano.objects.filter(eliminado__isnull=True).order_by("cuenta_id", "-creado").values_list("cuenta_id", "autor_nombre", "autor_correo"):
        vistos.setdefault(cuenta, {"cuenta_id": cuenta, "nombre": nombre or correo or "Sin nombre"})
    return sorted(vistos.values(), key=lambda a: a["nombre"].casefold())


def _marcar_edicion(planos) -> None:
    ahora = timezone.now()
    for p in planos:
        p.en_edicion_por = p.bloqueo_nombre if servicio.bloqueo_activo(p, ahora) else ""


@ver
@require_GET
def lista(request):
    request.tv_seccion = "cad"
    filtros = Filtros(request.GET)
    base = Plano.objects.filter(eliminado__isnull=True)
    pagina = Paginator(filtros.aplicar(base), POR_PAGINA).get_page(request.GET.get("pagina"))
    filas = list(pagina.object_list)
    _marcar_edicion(filas)
    usuario = request.user
    papelera = servicio.papelera(usuario).count() if servicio.puede_editar(usuario) else 0
    return _privado(render(request, "herramientas/cad/lista.html", {
        "filtros": filtros, "pagina": pagina, "filas": filas, "hay_planos": filtros.activos or base.exists(),
        "autores": _autores(), "proyectos": servicio.sugerencias_proyecto(usuario.pk), "ordenes": ORDENES,
        "puede_editar": servicio.puede_editar(usuario), "es_admin": servicio.es_admin(usuario), "papelera": papelera,
        "limite_bytes": servicio.limites()["bytes"], "limite_mb": servicio.limites()["bytes"] // (1024 * 1024), "maximo_zip": zips.MAXIMO,
        "drive": _drive_disponible(usuario),
        # Declara el campo para que la subida lo mande; el navegador lo llena con lo que se escriba en «Proyecto o cliente».
        "meta_subida": {"proyecto": ""}}))


def _drive_disponible(usuario) -> bool:
    from ..drive import servicio as drive

    return drive.disponible() and drive.conexion_de(usuario.pk) is not None


@editar
@require_POST
def nuevo(request):
    formato = request.POST.get("formato", "dwg")
    try:
        p = servicio.crear_vacio(request.user, request.POST.get("nombre", ""), request.POST.get("proyecto", ""), formato)
    except ErrorUsuario as exc:
        messages.error(request, str(exc))
        return redirect("herramientas:cad")
    return redirect("herramientas:cad_plano", pk=p.pk)


# -- Editor --------------------------------------------------------------------------------------------------------------------------

def _version_pedida(request, p: Plano) -> VersionPlano | None:
    texto = request.GET.get("version", "")
    if not texto:
        return None
    if not texto.isdigit():
        raise Http404
    v = VersionPlano.objects.filter(plano=p, numero=int(texto)).first()
    if v is None:
        raise Http404
    return v


def _versiones(p: Plano) -> list[dict]:
    return [{"numero": v.numero, "formato": v.formato, "bytes": v.bytes, "autor": v.autor_nombre, "nota": v.nota, "creada": v.creada.isoformat(),
             "vigente": v.numero == p.revision} for v in VersionPlano.objects.filter(plano=p).order_by("-numero")]


def _urls(p: Plano) -> dict:
    nombres = ("archivo", "estado", "bloqueo", "guardar", "copia", "borrador", "borrador_descartar", "renombrar", "imprimir", "convertir", "drive",
               "duplicar", "eliminar")
    urls = {n: reverse(f"herramientas:cad_{n}", args=[p.pk]) for n in nombres}
    urls["restaurar_version"] = reverse("herramientas:cad_restaurar_version", args=[p.pk, 0]).replace("/0/", "/{n}/")
    urls["plano"] = reverse("herramientas:cad_plano", args=[p.pk])
    urls["lista"] = reverse("herramientas:cad")
    urls["tarea"] = reverse("tvplataforma:tarea", kwargs={"tarea_id": MARCA})
    urls["editor"] = editor.url_editor()
    return urls


@ver
@require_GET
def plano(request, pk):
    request.tv_seccion = "cad"
    p = _plano(pk)
    v = _version_pedida(request, p)
    usuario = request.user
    puede = servicio.puede_editar(usuario)
    borrador = servicio.borrador_de(p, usuario.pk) if puede and v is None else None
    config = {
        "id": str(p.pk), "nombre": p.nombre, "formato": p.formato, "revision": p.revision, "proyecto": p.proyecto,
        "version": v.numero if v else None, "version_formato": v.formato if v else p.formato,
        "puede_editar": puede and v is None, "es_admin": servicio.es_admin(usuario), "rol_lectura": not puede,
        "borrador": {"actualizado": borrador.actualizado.isoformat(), "revision_base": borrador.revision_base, "bytes": borrador.bytes,
                     "formato": borrador.formato} if borrador else None,
        "urls": _urls(p), "limite_bytes": servicio.limites()["bytes"],
        # Borrador de recuperación cada 2 minutos con cambios; latido del bloqueo cada 30 s (caduca a los 90). Las pruebas los acortan.
        "autoguardado_s": int(getattr(settings, "HERRAMIENTAS_CAD_AUTOGUARDADO_S", 120)), "latido_s": int(getattr(settings, "HERRAMIENTAS_CAD_LATIDO_S", 30)),
        "nativo": nativo.disponible(), "puente": bool(editor.upstream().get("puente")),
    }
    return _privado(render(request, "herramientas/cad/plano.html", {
        "p": p, "v": v, "config": config, "versiones": _versiones(p), "puede_editar": puede, "es_admin": servicio.es_admin(usuario),
        "puede_eliminar": servicio.puede_eliminar(usuario, p), "papeles": nativo.PAPELES, "papel": nativo.PAPEL_POR_OMISION,
        "orientaciones": nativo.ORIENTACIONES, "areas": nativo.AREAS, "escalas": nativo.ESCALAS, "nativo": nativo.disponible(),
        "proyectos": servicio.sugerencias_proyecto(usuario.pk), "drive": _drive_disponible(usuario), "editor_listo": editor.disponible()}))


@ver
@require_GET
def archivo(request, pk):
    """Los bytes de la versión vigente (o de `?version=n`, o del borrador con `?borrador=1`). `?descargar=1` lo baja con su nombre."""
    p = _plano(pk)
    if request.GET.get("borrador") == "1":
        b = servicio.borrador_de(p, request.user.pk)
        if b is None:
            raise Http404
        ruta, formato, nombre = servicio.raiz() / b.ruta, b.formato, f"{p.nombre} (sin guardar).{b.formato}"
    else:
        v = _version_pedida(request, p) or servicio.version_vigente(p)
        if v is None or not servicio.ruta_version(v).is_file():
            raise Http404
        ruta, formato = servicio.ruta_version(v), v.formato
        nombre = f"{p.nombre}.{formato}" if v.numero == p.revision else f"{p.nombre} (versión {v.numero}).{formato}"
    r = FileResponse(open(ruta, "rb"), content_type=servicio.TIPOS_MIME[formato])
    r["Content-Disposition"] = content_disposition_header(request.GET.get("descargar") == "1", nombre)
    r["X-TV-Revision"] = str(p.revision)
    return _privado(r)


@ver
@require_GET
def estado(request, pk):
    p = _plano(pk)
    sesion = request.GET.get("sesion", "")
    b = servicio.borrador_de(p, request.user.pk) if servicio.puede_editar(request.user) else None
    return _json({"revision": p.revision, "nombre": p.nombre, "proyecto": p.proyecto, "formato": p.formato, "modificado_por": p.modificado_por_nombre,
                  "actualizado": p.actualizado.isoformat(), "bloqueo": servicio.estado_bloqueo(p, request.user, sesion), "versiones": _versiones(p),
                  "borrador": {"actualizado": b.actualizado.isoformat(), "revision_base": b.revision_base} if b else None})


@editar
@require_POST
def bloqueo(request, pk):
    try:
        datos = _cuerpo_json(request)
        r = servicio.bloqueo(pk, request.user, str(datos.get("sesion", "")), str(datos.get("accion", "")))
    except servicio.SinPermiso as exc:
        return _error(str(exc), 403)
    except ErrorUsuario as exc:
        return _error(str(exc))
    if r.get("eliminado"):
        return _error("Este plano se eliminó.", 410, eliminado=True)
    return _json(r)


def _con_cuerpo(request, trabajo):
    temporal = None
    try:
        temporal = servicio.recibir_cuerpo(request)
        return trabajo(temporal)
    except servicio.Conflicto as exc:
        return _error(str(exc), 409, motivo=exc.motivo, **exc.datos)
    except servicio.SinPermiso as exc:
        return _error(str(exc), 403)
    except ErrorUsuario as exc:
        return _error(str(exc), 413 if "pesa más" in str(exc) else 422)
    finally:
        if temporal is not None:
            temporal.unlink(missing_ok=True)


@editar
@require_POST
def guardar(request, pk):
    """Versión nueva desde el editor. Cabeceras: X-TV-Sesion (la página) y X-TV-Revision (la versión sobre la que se editó)."""
    revision = request.headers.get("X-TV-Revision", "")
    if not revision.isdigit():
        return _error("Falta la versión de base. Recarga la página.", 400)

    def trabajo(temporal):
        p = servicio.guardar(pk, request.user, request.headers.get("X-TV-Sesion", ""), int(revision), temporal)
        return _json({"revision": p.revision, "formato": p.formato, "bytes": p.bytes, "nombre": p.nombre, "versiones": _versiones(p),
                      "proyecto": p.proyecto, "modificado_por": p.modificado_por_nombre})
    return _con_cuerpo(request, trabajo)


@editar
@require_POST
def copia(request, pk):
    """Lo que no se pudo guardar sobre el plano queda como un plano nuevo (conflicto, bloqueo perdido o plano eliminado)."""
    def trabajo(temporal):
        nuevo = servicio.guardar_copia(pk, request.user, temporal)
        return _json({"id": str(nuevo.pk), "nombre": nuevo.nombre, "url": reverse("herramientas:cad_plano", args=[nuevo.pk])}, status=201)
    return _con_cuerpo(request, trabajo)


@editar
@require_http_methods(["GET", "POST"])
def borrador(request, pk):
    p = _plano(pk)
    if request.method == "GET":
        return archivo(request, pk) if request.GET.get("borrador") == "1" else _error("", 400)
    revision = request.headers.get("X-TV-Revision", "")
    if not revision.isdigit():
        return _error("Falta la versión de base.", 400)

    def trabajo(temporal):
        b = servicio.guardar_borrador(p, request.user, int(revision), temporal)
        return _json({"actualizado": b.actualizado.isoformat()})
    return _con_cuerpo(request, trabajo)


@editar
@require_POST
def borrador_descartar(request, pk):
    servicio.descartar_borrador(_plano(pk), request.user.pk)
    return _json({"ok": True})


@editar
@require_POST
def restaurar_version(request, pk, numero):
    try:
        datos = _cuerpo_json(request) if request.content_type == "application/json" else {}
        p = servicio.restaurar_version(pk, numero, request.user, str(datos.get("sesion", "")))
    except ErrorUsuario as exc:
        if request.content_type != "application/json":
            messages.error(request, str(exc))
            return redirect("herramientas:cad_plano", pk=pk)
        return _error(str(exc), 409)
    if request.content_type != "application/json":
        messages.success(request, f"La versión {numero} es otra vez la vigente (versión {p.revision}).")
        return redirect("herramientas:cad_plano", pk=pk)
    return _json({"revision": p.revision, "versiones": _versiones(p), "proyecto": p.proyecto, "modificado_por": p.modificado_por_nombre})


@editar
@require_POST
def renombrar(request, pk):
    p = servicio.renombrar(_plano(pk), request.POST.get("nombre", ""), request.POST.get("proyecto", ""))
    if request.headers.get("Accept", "").startswith("application/json"):
        return _json({"nombre": p.nombre, "proyecto": p.proyecto})
    messages.success(request, "Cambios guardados.")
    return redirect(request.POST.get("volver") if request.POST.get("volver", "").startswith("/cad/") else reverse("herramientas:cad_plano", args=[p.pk]))


@editar
@require_POST
def duplicar(request, pk):
    try:
        nuevo = servicio.duplicar(_plano(pk), request.user)
    except ErrorUsuario as exc:
        messages.error(request, str(exc))
        return redirect("herramientas:cad")
    messages.success(request, f"Se creó «{nuevo.nombre}».")
    return redirect("herramientas:cad_plano", pk=nuevo.pk)


@editar
@require_POST
def eliminar(request, pk):
    p = _plano(pk)
    try:
        servicio.eliminar(p, request.user)
    except servicio.SinPermiso as exc:
        messages.error(request, str(exc))
        return redirect("herramientas:cad_plano", pk=pk)
    except ErrorUsuario as exc:
        messages.error(request, str(exc))
        return redirect("herramientas:cad_plano", pk=pk)
    messages.success(request, f"«{p.nombre}» se movió a la papelera. Se borra para siempre en {servicio.PAPELERA_DIAS} días.")
    return redirect("herramientas:cad")


# -- Papelera -----------------------------------------------------------------------------------------------------------------------

@editar
@require_GET
def papelera(request):
    request.tv_seccion = "cad"
    planos = list(servicio.papelera(request.user))
    for p in planos:
        p.vence = servicio.vence_en(p)
        p.pk_texto = p.pk.hex
    return _privado(render(request, "herramientas/cad/papelera.html", {"planos": planos, "es_admin": servicio.es_admin(request.user),
                                                                       "dias": servicio.PAPELERA_DIAS}))


@editar
@require_POST
def restaurar(request, pk):
    p = _plano(pk, eliminado=True)
    try:
        servicio.restaurar(p, request.user)
    except servicio.SinPermiso as exc:
        messages.error(request, str(exc))
        return redirect("herramientas:cad_papelera")
    messages.success(request, f"«{p.nombre}» volvió a la biblioteca.")
    return redirect("herramientas:cad_papelera")


@requiere_rol("herramientas", "admin")
@require_POST
def destruir(request, pk):
    p = _plano(pk, eliminado=True)
    nombre = p.nombre
    servicio.destruir(p)
    messages.success(request, f"«{nombre}» se eliminó para siempre.")
    return redirect("herramientas:cad_papelera")


@requiere_rol("herramientas", "admin")
@require_POST
def vaciar(request):
    if request.POST.get("confirmacion", "").strip().lower() != "vaciar":
        messages.error(request, "Escribe «vaciar» para confirmar.")
        return redirect("herramientas:cad_papelera")
    n = servicio.vaciar_papelera()
    messages.success(request, f"Papelera vacía: se eliminaron {n} plano{'s' if n != 1 else ''} para siempre.")
    return redirect("herramientas:cad_papelera")


# -- Imprimir, convertir, Drive, ZIP ---------------------------------------------------------------------------------------------

def _tarea_json(tarea):
    return _json({"tarea": str(tarea.pk), "terminada": tarea.ha_terminado, "estado": tarea.estado, "resultado": tarea.resultado, "error": tarea.error,
                  "fraccion": tarea.fraccion, "mensaje": tarea.mensaje}, status=202)


def _numero(request, p: Plano) -> int:
    texto = str(request.POST.get("version", "") or "")
    if texto.isdigit() and VersionPlano.objects.filter(plano=p, numero=int(texto)).exists():
        return int(texto)
    return p.revision


@ver
@require_POST
def imprimir(request, pk):
    from ..tareas import imprimir_plano

    p = _plano(pk)
    if not nativo.disponible():
        return _error("La impresión a PDF no está disponible en este momento.", 503)
    post = request.POST
    try:
        opciones = {"papel": post.get("papel", nativo.PAPEL_POR_OMISION), "orientacion": post.get("orientacion", "landscape"),
                    "escala": nativo.escala_valida(post.get("escala_otra") if post.get("escala") == "otra" else post.get("escala", "ajustar")),
                    "area": post.get("area", "modelo"), "grosores": post.get("grosores") == "1"}
        if opciones["papel"] not in nativo.PAPELES or opciones["orientacion"] not in nativo.ORIENTACIONES or opciones["area"] not in nativo.AREAS:
            raise ErrorUsuario("Revisa las opciones de impresión.")
    except ErrorUsuario as exc:
        return _error(str(exc))
    tarea = imprimir_plano.encolar(str(p.pk), _numero(request, p), request.user.pk, opciones, usuario=request.user)
    return _tarea_json(tarea)


@ver
@require_POST
def convertir(request, pk):
    from ..tareas import convertir_plano

    p = _plano(pk)
    if not nativo.disponible():
        return _error("La conversión no está disponible en este momento.", 503)
    formato = request.POST.get("formato", "")
    if formato not in servicio.FORMATOS:
        return _error("Elige DWG o DXF.")
    numero = _numero(request, p)
    if VersionPlano.objects.filter(plano=p, numero=numero, formato=formato).exists():
        return _error(f"El plano ya es {formato.upper()}.")
    tarea = convertir_plano.encolar(str(p.pk), numero, request.user.pk, formato, request.POST.get("permitir_perdida") == "1", usuario=request.user)
    return _tarea_json(tarea)


@ver
@require_GET
def salida(request, token):
    encontrado = salidas.de_la_cuenta(request.user.pk, token)
    if encontrado is None:
        raise Http404
    ruta, nombre = encontrado
    tipo = mimetypes.guess_type(nombre)[0] or ("image/vnd.dwg" if nombre.endswith(".dwg") else "application/octet-stream")
    if nombre.endswith(".dxf"):
        tipo = "image/vnd.dxf"
    r = FileResponse(open(ruta, "rb"), content_type=tipo)
    r["Content-Disposition"] = content_disposition_header(request.GET.get("ver") != "1", nombre)
    return _privado(r)


@ver
@require_POST
def drive(request, pk):
    from ..drive import servicio as drive_srv

    p = _plano(pk)
    v = servicio.version_vigente(p)
    if v is None:
        return _error("Este plano no tiene una versión guardada.")
    if not drive_srv.disponible() or drive_srv.conexion_de(request.user.pk) is None:
        return _error("Conecta tu Google Drive en Ajustes para enviar planos.", 409)
    e = drive_srv.encolar(request.user.pk, "planos", p.archivo_nombre, origen=servicio.ruta_version(v), forzar=True,
                          clave=drive_srv.clave_de("plano", p.pk, v.numero, v.sha256))
    if e is None:
        return _error("No se pudo poner en la cola de Google Drive. Revisa la conexión en Ajustes.", 409)
    return _json({"mensaje": "Enviando a tu Google Drive (carpeta «Planos CAD»)."})


@ver
@require_POST
def zip_lote(request):
    """ZIP con los planos elegidos (cada uno en su versión vigente). Hasta 50."""
    try:
        cuerpo = zips.cuerpo_json(request)
    except zips.LoteInvalido as exc:
        return _error(str(exc))
    elementos = cuerpo.get("elementos")
    if not isinstance(elementos, list) or not all(isinstance(x, str) for x in elementos):
        return _error("Elige al menos un plano.")
    # Solo planos: este lote se arma con permiso de leer toda la biblioteca, nunca documentos de otras herramientas.
    return zips.responder(request, [["plano", x] for x in elementos], "planos", supervision=True)


# -- Desde otras herramientas («Abrir en CAD») ---------------------------------------------------------------------------------

def _respuesta_desde(request, p: Plano):
    url = reverse("herramientas:cad_plano", args=[p.pk])
    if request.headers.get("Accept", "").startswith("application/json"):
        return _json({"id": str(p.pk), "url": url}, status=201)
    return redirect(url)


def _fallo_desde(request, texto: str, volver: str):
    if request.headers.get("Accept", "").startswith("application/json"):
        return _error(texto)
    messages.error(request, texto)
    return redirect(volver)


@editar
@require_POST
def desde_convertidor(request, pk):
    from ..convertidor import servicio as conv
    from ..models import Conversion

    c = Conversion.objects.filter(pk=pk, cuenta_id=request.user.pk).first()
    if c is None:
        raise Http404
    volver = reverse("herramientas:convertidor_banco", args=[c.pk])
    if c.salida_formato != "dxf" or not c.salida_ruta:
        return _fallo_desde(request, "Primero convierte el archivo a DXF.", volver)
    try:
        p = servicio.crear_desde_ruta(request.user, conv.raiz() / c.salida_ruta, Path(c.nombre).stem, "",
                                      {"tipo": "convertidor", "id": str(c.pk), "nombre": c.nombre})
    except ErrorUsuario as exc:
        return _fallo_desde(request, str(exc), volver)
    return _respuesta_desde(request, p)


@editar
@require_POST
def desde_triangulacion(request, pk):
    from ..models import Triangulacion
    from ..triangulacion import servicio as tri

    t = Triangulacion.objects.filter(pk=pk, cuenta_id=request.user.pk).first()
    if t is None:
        raise Http404
    volver = reverse("herramientas:triangulacion_banco", args=[t.pk])
    if not t.salida_ruta:
        return _fallo_desde(request, "Primero exporta el DXF.", volver)
    try:
        p = servicio.crear_desde_ruta(request.user, tri.raiz() / t.salida_ruta, t.salida_nombre or Path(t.nombre).stem, "",
                                      {"tipo": "triangulacion", "id": str(t.pk), "nombre": t.nombre})
    except ErrorUsuario as exc:
        return _fallo_desde(request, str(exc), volver)
    return _respuesta_desde(request, p)


@editar
@require_POST
def desde_croquis(request, pk):
    from ..comun import capturas
    from ..models import Croquis

    k = Croquis.objects.filter(pk=pk, cuenta_id=request.user.pk).first()
    if k is None:
        raise Http404
    volver = reverse("herramientas:croquis")
    if k.salida_formato != "dxf" or not k.salida_ruta:
        return _fallo_desde(request, "Primero genera el DXF del croquis.", volver)
    try:
        p = servicio.crear_desde_ruta(request.user, capturas.raiz() / k.salida_ruta, k.salida_nombre or k.titulo or "Croquis",
                                      (k.datos or {}).get("cliente", "") if isinstance(k.datos, dict) else "",
                                      {"tipo": "croquis", "id": str(k.pk), "nombre": k.titulo})
    except ErrorUsuario as exc:
        return _fallo_desde(request, str(exc), volver)
    return _respuesta_desde(request, p)


# -- El editor (archivos de la compilación web) ---------------------------------------------------------------------------------

@ver
@require_GET
def editor_archivo(request, version, ruta):
    """Sirve el editor en desarrollo y en pruebas. En producción lo sirve Caddy desde la misma ruta, con las mismas cabeceras
    (despliegue/caddy/sitios-disponibles/tools.caddy del portal), sin pasar por aquí."""
    archivo = editor.archivo(version, ruta)
    if archivo is None:
        r = HttpResponse(status=404)
        r["Cache-Control"] = "no-store"
        return r
    r = FileResponse(open(archivo, "rb"), content_type=editor.tipo(archivo))
    for k, v in editor.cabeceras(archivo).items():
        r[k] = v
    return r
