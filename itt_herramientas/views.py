"""Páginas comunes de Herramientas: preferencias, diccionario, perfiles corporativos e importación del escritorio.

Todas las URL se obtienen con `reverse("herramientas:…")`: ni el prefijo ni el host de la app se escriben aquí.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from django.contrib import messages
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from tvplataforma import permisos
from tvplataforma.permisos import requiere_rol

from .comun import diccionarios, importacion, perfiles, preferencias
from .dominio import branding
from .comun.formularios import PaletaForm, PerfilForm, PreferenciasForm, TextosForm
from .modulo import ROLES_DE_HERRAMIENTAS
from .models import Perfil, TerminoTecnico

usuario_de_herramientas = requiere_rol("herramientas", *ROLES_DE_HERRAMIENTAS, "admin")
solo_admin = requiere_rol("herramientas", "admin")
TTL_IMPORTACION = 3600
#: (rol, título, descripción, nombre de la URL) de las herramientas que se listan en el inicio según el rol de la cuenta
HERRAMIENTAS_DE_INICIO = (
    ("cotizaciones", "Cotizaciones", "Cotiza en campo o en la oficina y comparte el PDF.", "herramientas:cotizaciones"),
    ("reportes", "Reportes fotográficos", "Fotos con hora y ubicación, listas en un PDF.", "herramientas:reportes"),
    ("croquis", "Croquis de ubicación", "Dibuja el predio sobre el mapa y genera PDF y DXF.", "herramientas:croquis"),
    ("convertidor", "Convertidor DXF ↔ KML/KMZ", "Lleva planos a Google Earth y polígonos a AutoCAD.", "herramientas:convertidor"),
    ("triangulacion", "Triangulación y pendientes", "TIN, pendientes y escurrimiento a partir de un DXF.", "herramientas:triangulacion"),
    ("cad", "CAD", "Planos DWG y DXF del equipo: abre, dibuja, guarda e imprime.", "herramientas:cad"),
)
#: Además de su propio rol y de `admin`, quien supervisa abre CAD (solo lectura).
_INICIO_EXTRA = {"cad": ("supervision",)}


def _es_admin(request) -> bool:
    return perfiles.es_admin(request.user)


@usuario_de_herramientas
def inicio(request):
    herramientas = [(titulo, descripcion, reverse(nombre), f"herramientas/img/herramientas/{rol}.png") for rol, titulo, descripcion, nombre in HERRAMIENTAS_DE_INICIO
                    if any(permisos.tiene_rol(request.user, "herramientas", r) for r in (rol, "admin", *_INICIO_EXTRA.get(rol, ())))]
    return render(request, "herramientas/inicio.html", {"es_admin": _es_admin(request), "puede_cambiar": perfiles.puede_cambiar(request.user), "herramientas": herramientas, "mostrar_banda": "si", "mostrar_marca": True,
                                                    "supervision": permisos.tiene_rol(request.user, "herramientas", "supervision")})


@usuario_de_herramientas
@require_http_methods(["GET", "POST"])
def preferencias_(request):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    formulario = PreferenciasForm.desde(preferencias.obtener(request.user.pk), request.POST or None)
    if request.method == "POST" and formulario.is_valid():
        try:
            preferencias.guardar(request.user.pk, formulario.valores())
        except preferencias.PreferenciaInvalida as exc:
            formulario.add_error(None, str(exc))
        else:
            messages.success(request, "Preferencias guardadas.")
            return redirect("herramientas:preferencias")
    return render(request, "herramientas/preferencias.html", {"formulario": formulario})


@usuario_de_herramientas
@require_http_methods(["GET", "POST"])
def diccionario(request):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    admin = _es_admin(request)
    if request.method == "POST":
        accion = request.POST.get("accion", "")
        idioma = request.POST.get("idioma", "es")
        try:
            if accion == "agregar":
                diccionarios.agregar_palabra(request.user.pk, request.POST.get("palabra", ""), idioma)
                messages.success(request, "Palabra agregada a tu diccionario.")
            elif accion == "quitar":
                diccionarios.quitar_palabra(request.user.pk, request.POST.get("palabra", ""), idioma)
                messages.success(request, "Palabra quitada.")
            elif accion == "agregar_termino" and admin:
                diccionarios.agregar_termino(request.POST.get("palabra", ""), request.POST.get("idioma", "ambos"), request.user.pk)
                messages.success(request, "Término técnico agregado para todas las cuentas.")
            elif accion == "quitar_termino" and admin:
                diccionarios.quitar_termino(int(request.POST.get("id", "0") or 0))
                messages.success(request, "Término técnico quitado.")
            else:
                return HttpResponseBadRequest("Acción no permitida.")
        except diccionarios.PalabraInvalida as exc:
            messages.error(request, str(exc))
        return redirect("herramientas:diccionario")
    return render(request, "herramientas/diccionario.html", {
        "palabras": diccionarios.palabras_personales(request.user.pk), "es_admin": admin,
        "terminos": TerminoTecnico.objects.all() if admin else [], "idiomas": TerminoTecnico.IDIOMAS})


@solo_admin
def perfiles_(request):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    return render(request, "herramientas/perfiles.html", {"perfiles": Perfil.objects.all()})


def _perfil(clave: str) -> Perfil:
    try:
        return Perfil.objects.get(clave=clave)
    except Perfil.DoesNotExist:
        raise Http404 from None


@solo_admin
@require_http_methods(["GET", "POST"])
def perfil_editar(request, clave):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    perfil = _perfil(clave)
    formulario = PerfilForm(request.POST or None, request.FILES or None, instance=perfil)
    if request.method == "POST" and formulario.is_valid():
        if formulario.guardar():
            messages.success(request, "Perfil guardado.")
            return redirect("herramientas:perfil", clave=clave)
    return render(request, "herramientas/perfil_editar.html", {
        "perfil": perfil, "formulario": formulario,
        "imagenes": {t: perfiles.ruta_imagen(perfil, t) is not None for t in perfiles.IMAGENES}})


@solo_admin
@require_http_methods(["GET", "POST"])
def perfil_textos(request, clave):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    perfil = _perfil(clave)
    if request.method == "POST" and "restaurar" in request.POST:
        perfil.textos_reporte, perfil.textos_cotizacion_es, perfil.textos_cotizacion_en = {}, {}, {}
        perfil.save()
        messages.success(request, "Textos originales restaurados.")
        return redirect("herramientas:perfil_textos", clave=clave)
    formulario = TextosForm(request.POST or None, perfil=perfil)
    if request.method == "POST" and formulario.is_valid():
        formulario.guardar()
        messages.success(request, "Textos guardados.")
        return redirect("herramientas:perfil_textos", clave=clave)
    grupos = [(titulo, [formulario[n] for n in nombres]) for titulo, nombres in formulario.grupos]
    return render(request, "herramientas/perfil_textos.html", {"perfil": perfil, "formulario": formulario, "grupos": grupos})


@solo_admin
@require_http_methods(["GET", "POST"])
def perfil_paleta(request, clave):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    from .dominio import quote_theme

    perfil = _perfil(clave)
    actual = {**quote_theme.QUOTE_PALETTE_PRESETS["Azul corporativo"], **(perfil.paleta or {})}
    if request.method == "POST" and "restaurar" in request.POST:
        perfil.paleta = {}
        perfil.save()
        messages.success(request, "Paleta original restaurada.")
        return redirect("herramientas:perfil_paleta", clave=clave)
    formulario = PaletaForm(request.POST or None, initial={**actual, "preset": quote_theme.matching_preset(actual) if quote_theme.matching_preset(actual) != "Personalizada" else ""})
    avisos: list[str] = []
    if request.method == "POST" and formulario.is_valid():
        perfil.paleta = formulario.paleta()
        perfil.save()
        for aviso in formulario.avisos:
            messages.warning(request, aviso)
        messages.success(request, "Paleta guardada.")
        return redirect("herramientas:perfil_paleta", clave=clave)
    if formulario.is_bound and not formulario.errors:
        avisos = formulario.avisos
    mostrada = formulario.cleaned_data if formulario.is_bound and formulario.is_valid() else actual
    return render(request, "herramientas/perfil_paleta.html", {
        "perfil": perfil, "formulario": formulario, "avisos": avisos, "muestra": {k: mostrada.get(k, actual[k]) for k in actual},
        "contrastes": [(nombre.capitalize(), perfiles.contraste(actual[t], actual[f]), minimo) for t, f, nombre, minimo in perfiles.UMBRALES]})


# -- Importación de la configuración del escritorio -----------------------------------------------------------

def _carpeta_importaciones() -> Path:
    carpeta = perfiles.raiz_archivos() / "importaciones"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _purgar_importaciones() -> None:
    limite = time.time() - TTL_IMPORTACION
    for archivo in _carpeta_importaciones().glob("*.zip"):
        if archivo.stat().st_mtime < limite:
            archivo.unlink(missing_ok=True)


def _archivo_importacion(usuario, token) -> Path:
    return _carpeta_importaciones() / f"{usuario.pk}-{token}.zip"


@usuario_de_herramientas
@require_http_methods(["GET", "POST"])
def importar(request):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    if request.method == "POST":
        archivo = request.FILES.get("zip")
        if archivo is None:
            messages.error(request, "Elige el ZIP de configuración del escritorio.")
            return redirect("herramientas:importar")
        datos = archivo.read(importacion.MAX_ZIP_BYTES + 1)
        try:
            paquete = importacion.leer(datos)
        except importacion.ImportacionInvalida as exc:
            messages.error(request, str(exc))
            return redirect("herramientas:importar")
        _purgar_importaciones()
        token = uuid.uuid4()
        _archivo_importacion(request.user, token).write_bytes(datos)
        return redirect("herramientas:importar_confirmar", token=token)
    return render(request, "herramientas/importar.html")


@usuario_de_herramientas
@require_http_methods(["GET", "POST"])
def importar_confirmar(request, token):
    request.tv_seccion = "ajustes"                 # la sección «Ajustes» del lateral queda marcada
    ruta = _archivo_importacion(request.user, token)
    if not ruta.is_file() or time.time() - ruta.stat().st_mtime > TTL_IMPORTACION:
        messages.error(request, "La importación caducó. Sube el ZIP otra vez.")
        return redirect("herramientas:importar")
    paquete = importacion.leer(ruta.read_bytes())
    admin = _es_admin(request)
    elementos = [e for e in importacion.vista_previa(paquete) if admin or e.clave not in importacion.SOLO_ADMIN]
    if request.method == "POST":
        elegidos = {e.clave for e in elementos if request.POST.get(f"elemento_{e.clave}")}
        destino = None
        if admin and request.POST.get("perfil_destino"):
            destino = Perfil.objects.filter(clave=request.POST["perfil_destino"]).first()
        try:
            resultado = importacion.aplicar(paquete, request.user.pk, elegidos, es_admin=admin, perfil_destino=destino)
        except (importacion.ImportacionInvalida, perfiles.PerfilInvalido) as exc:
            messages.error(request, str(exc))
            return redirect("herramientas:importar_confirmar", token=token)
        ruta.unlink(missing_ok=True)
        for aviso in (*resultado.avisos, *resultado.omitido):
            messages.warning(request, aviso)
        messages.success(request, "Importado: " + (", ".join(f"{importacion.ELEMENTOS[k].split(' (')[0].lower()} ({n})" for k, n in resultado.aplicado.items()) or "nada que aplicar") + ".")
        return redirect("herramientas:inicio")
    return render(request, "herramientas/importar_confirmar.html", {
        "elementos": elementos, "token": token, "es_admin": admin, "perfiles": Perfil.objects.all(),
        "activo": paquete.ajustes.get("branding", {}).get("profile", "grupo_itt") if isinstance(paquete.ajustes.get("branding"), dict) else "grupo_itt"})


# -- PWA: manifiesto, service worker, captura, guía de instalación ---------------------------------------------------

def manifiesto(request):
    """Manifiesto de la PWA: `scope` y `start_url` son la raíz del host (arquitectura §18.2).

    `?p=tresvizo` lo personaliza con la identidad (nombre e íconos); las páginas con sesión enlazan el de su perfil activo.
    Sin sesión (es público) cualquiera puede pedir cualquiera de los dos: solo contiene marca, nunca datos de cuenta.
    """
    from django.http import JsonResponse
    from django.templatetags.static import static

    from .dominio import branding

    clave = request.GET.get("p") if request.GET.get("p") in ("grupo_itt", "tresvizo") else perfiles.PERFIL_POR_OMISION
    nombre = branding.profile_defaults(clave)["name"]
    datos = {
        "id": "/", "name": f"Herramientas {nombre}", "short_name": "Herramientas", "description": f"Reportes, cotizaciones y herramientas de campo de {nombre}.",
        "start_url": "/", "scope": "/", "display": "standalone", "orientation": "any", "lang": "es-MX",
        "background_color": "#FFFFFF", "theme_color": "#07356F",
        "icons": [
            {"src": static(f"herramientas/img/icono-{clave}-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": static(f"herramientas/img/icono-{clave}-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": static(f"herramientas/img/icono-maskable-{clave}-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }
    respuesta = JsonResponse(datos, content_type="application/manifest+json")
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


def service_worker(request):
    from django.http import HttpResponse

    from .offline import sw

    # El navegador vuelve a pedir sw.js al abrir cada página de la app (busca una versión nueva): con su ETag, si no cambió, Chromium recibe
    # un 304 sin cuerpo en vez de los ~5 KB del archivo (WebKit lo pide entero igual; ver sw.js.tpl, «importScripts»). docs/cad.md, «Datos móviles».
    import hashlib

    from .comun import condicional

    texto = sw.contenido()
    etiqueta = '"%s"' % hashlib.sha256(texto.encode()).hexdigest()[:20]
    if condicional.coincide(request, etiqueta):
        respuesta = HttpResponse(status=304)
    else:
        respuesta = HttpResponse(texto, content_type="text/javascript; charset=utf-8")
    respuesta["ETag"] = etiqueta
    respuesta["Service-Worker-Allowed"] = "/"
    respuesta["Cache-Control"] = "no-cache, max-age=0"
    return respuesta


def capturar(request):
    """Página de captura: HTML idéntico para todas las cuentas (sin datos personales), así el service worker puede
    guardarla y abrirla sin conexión. Los datos de la cuenta llegan por /sesion/."""
    respuesta = render(request, "herramientas/pwa/capturar.html")
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


def instalar(request):
    respuesta = render(request, "herramientas/pwa/instalar.html")
    respuesta["Cache-Control"] = "no-cache"
    return respuesta


def _herramientas_de_campo(usuario) -> list[str]:
    """Las páginas de campo (las que abren sin conexión) a las que esta cuenta tiene acceso: las lista el menú de la app."""
    def tiene(rol):
        return permisos.tiene_rol(usuario, "herramientas", rol) or permisos.tiene_rol(usuario, "herramientas", "admin")
    campo = [t for t in ("cotizaciones", "reportes", "croquis") if tiene(t)]
    return (["capturar"] if tiene("reportes") or tiene("cotizaciones") else []) + campo


def _navegacion(usuario) -> list[str]:
    """Pestañas del módulo que ve esta cuenta (las páginas sin conexión las muestran con esto)."""
    def tiene(rol):
        return permisos.tiene_rol(usuario, "herramientas", rol) or permisos.tiene_rol(usuario, "herramientas", "admin")
    from .modulo import MODULO

    return [e.clave for e in MODULO.menu if not e.roles or any(tiene(r) for r in e.roles)]


def _puede_cad(usuario) -> bool:
    """¿Puede abrir en CAD lo que genera (el DXF del croquis)? Lo usan las páginas de campo, que no reciben datos de la cuenta en su HTML."""
    from .cad import servicio as cad

    return cad.puede_editar(usuario)


def _tema(usuario) -> str:
    """Tema elegido por la cuenta (claro por omisión; nunca el del sistema)."""
    from tvplataforma.models import Preferencias

    return Preferencias.de(usuario).tema


def sesion(request):
    """Quién está con sesión y su token CSRF, para que la cola (y el service worker) puedan subir. 401 si no hay sesión."""
    from django.http import HttpResponse, JsonResponse
    from django.middleware.csrf import get_token

    u = request.user
    if not (u.is_authenticated and u.is_active) or not any(permisos.tiene_rol(u, "herramientas", r) for r in (*ROLES_DE_HERRAMIENTAS, "admin")):
        return HttpResponse(status=401)
    respuesta = JsonResponse({"cuenta": u.pk, "nombre": u.get_full_name() or u.get_username(), "csrf": get_token(request), "herramientas": _herramientas_de_campo(u), "nav": _navegacion(u),
                              "tema": _tema(u), "cad": _puede_cad(u)})
    respuesta["Cache-Control"] = "no-store"
    return respuesta


@requiere_rol("herramientas", "reportes", "cotizaciones", "admin")
def capturas(request):
    """Fotos ya recibidas de la cuenta (para pantallas que las listan)."""
    from django.http import JsonResponse

    from .models import Captura

    consulta = Captura.objects.filter(cuenta_id=request.user.pk)
    if request.GET.get("referencia"):
        consulta = consulta.filter(referencia=request.GET["referencia"])
    campos = ("id", "sha256", "nombre", "referencia", "orden", "tomada", "lat", "lon", "precision", "sin_ubicacion", "nota", "bytes", "ancho", "alto")
    return JsonResponse({"capturas": list(consulta.values(*campos)[:500])})


# -- Perfil corporativo activo ----------------------------------------------------------------------------------------

@usuario_de_herramientas
@require_POST
def perfil_activo(request):
    """Cambia el perfil activo de LA CUENTA que lo pide. 403 si no tiene el permiso «Cambiar perfil corporativo».

    Cada perfil es un espacio de documentos aparte: al cambiar, las listas muestran los del otro. Las herramientas de campo lo piden con
    `Accept: application/json` y reciben `{perfil, nombre}` (la página se vuelve a abrir en el otro espacio); el resto vuelve a `siguiente`.
    """
    from django.core.exceptions import PermissionDenied
    from django.http import HttpResponseRedirect, JsonResponse
    from django.utils.http import url_has_allowed_host_and_scheme

    try:
        perfil = perfiles.cambiar_perfil(request.user, request.POST.get("perfil", ""))
    except perfiles.SinPermiso:
        raise PermissionDenied from None
    except perfiles.PerfilInvalido as exc:
        return HttpResponseBadRequest(str(exc))
    if "application/json" in request.headers.get("Accept", ""):
        respuesta = JsonResponse({"perfil": perfil.clave, "nombre": perfil.nombre})
        respuesta["Cache-Control"] = "private, no-store"
        return respuesta
    destino = request.POST.get("siguiente", "")
    if not url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()) or not destino.startswith("/"):
        destino = reverse("herramientas:inicio")
    return HttpResponseRedirect(destino)


@usuario_de_herramientas
def perfil_css(request):
    """Tokens de color del perfil activo (CSP: hoja externa, nunca estilos en línea). Vacía si el perfil usa la paleta de fábrica."""
    from django.http import HttpResponse

    paleta = perfiles.perfil_activo(request.user).paleta or {}
    reglas = []
    for clave, token in (("primary", "--tv-primario"), ("accent", "--tv-acento")):
        valor = str(paleta.get(clave, ""))
        if len(valor) == 7 and valor.startswith("#"):
            reglas.append(f"  {token}: {valor};")
    cuerpo = ":root {\n" + "\n".join(reglas) + "\n}\n" if reglas else "/* paleta de fábrica */\n"
    respuesta = HttpResponse(cuerpo, content_type="text/css; charset=utf-8")
    respuesta["Cache-Control"] = "private, no-cache"
    return respuesta


@solo_admin
def perfil_logo_vista(request, clave):
    """Vista previa del logo de un perfil (propio o de fábrica) para quien lo administra. Solo el logo: nunca la firma ni la cédula."""
    from django.http import FileResponse

    perfil = _perfil(clave)
    with perfiles.activar(perfil):
        ruta = branding.active_profile().logo_path
    respuesta = FileResponse(open(ruta, "rb"), content_type="image/png" if str(ruta).lower().endswith(".png") else "image/jpeg")
    respuesta["Cache-Control"] = "private, no-cache"
    return respuesta


@usuario_de_herramientas
def perfil_logo(request):
    """Logo del perfil activo (el propio del perfil o el de fábrica). Solo el logo: la firma y la cédula nunca salen por aquí."""
    from django.http import FileResponse

    perfil = perfiles.perfil_activo(request.user)
    with perfiles.activar(perfil):
        ruta = branding.active_profile().logo_path
    respuesta = FileResponse(open(ruta, "rb"), content_type="image/png" if str(ruta).lower().endswith(".png") else "image/jpeg")
    respuesta["Cache-Control"] = "private, no-cache"
    return respuesta


@requiere_rol("herramientas", "reportes", "croquis", "admin")
def teselas(request, capa, z, x, y):
    """Teselas de OpenStreetMap/OpenTopoMap con caché en disco (el navegador no sale a internet: CSP).

    Respeta la política de OSM: solo se pide lo que el mapa muestra, la vigencia viene de las cabeceras del origen y las
    descargas nuevas por cuenta tienen tope por hora (429).
    """
    from django.http import HttpResponse

    from .comun import teselas as servicio_teselas

    try:
        datos, restante = servicio_teselas.obtener(capa, z, x, y, cuenta_id=request.user.pk)
    except servicio_teselas.DemasiadasDescargas:
        respuesta = HttpResponse("Demasiadas teselas nuevas por hora.", status=429, content_type="text/plain; charset=utf-8")
        respuesta["Retry-After"] = "600"
        return respuesta
    except servicio_teselas.SinTesela:
        respuesta = HttpResponse(status=404)
        respuesta["Cache-Control"] = "no-store"
        return respuesta
    respuesta = HttpResponse(datos, content_type="image/png")
    respuesta["Cache-Control"] = f"private, max-age={int(max(60, restante))}"
    return respuesta


@requiere_rol("herramientas", "cotizaciones", "reportes", "croquis", "cad", "admin", "supervision")
@require_GET
def buscar(request):
    """Búsqueda de la paleta (contrato 1.5, §12.1): los documentos de la empresa de las herramientas que la cuenta tiene (cotizaciones,
    reportes y croquis son compartidos con el equipo), solo los de su perfil activo, y los planos de la biblioteca de CAD. Hasta 20."""
    from django.db.models import Q
    from django.http import JsonResponse

    from .models import Cotizacion, Croquis, Reporte

    q = " ".join(request.GET.get("q", "").split())[:80]
    resultados: list[dict] = []
    if len(q) >= 2:
        def tiene(rol):
            return permisos.tiene_rol(request.user, "herramientas", rol) or permisos.tiene_rol(request.user, "herramientas", "admin")
        propio = {"eliminado__isnull": True, "perfil": perfiles.clave_activa(request.user)}
        if tiene("cotizaciones"):
            for c in Cotizacion.objects.filter(Q(folio__icontains=q) | Q(cliente__icontains=q) | Q(proyecto__icontains=q), **propio).order_by("-actualizada")[:10]:
                resultados.append({"titulo": " · ".join(x for x in (c.folio, c.proyecto) if x) or "Cotización", "subtitulo": f"Cotización · {c.cliente}" if c.cliente else "Cotización",
                                   "url": f"{reverse('herramientas:cotizaciones')}#/s/{c.pk}"})
        if tiene("reportes"):
            for r in Reporte.objects.filter(titulo__icontains=q, **propio).order_by("-actualizada")[:10]:
                resultados.append({"titulo": r.titulo or "Reporte", "subtitulo": f"Reporte fotográfico · {r.fotos} foto{'s' if r.fotos != 1 else ''}", "url": f"{reverse('herramientas:reportes')}#/s/{r.pk}"})
        if tiene("croquis"):
            for k in Croquis.objects.filter(titulo__icontains=q, **propio).order_by("-actualizada")[:10]:
                resultados.append({"titulo": k.titulo or "Croquis", "subtitulo": "Croquis de ubicación", "url": f"{reverse('herramientas:croquis')}#/s/{k.pk}"})
        from .cad import servicio as cad

        if cad.puede_ver(request.user):
            from .models import Plano

            for p in Plano.objects.filter(Q(nombre__icontains=q) | Q(proyecto__icontains=q), eliminado__isnull=True).order_by("-actualizado")[:10]:
                resultados.append({"titulo": p.nombre, "subtitulo": " · ".join(x for x in (f"Plano {p.formato.upper()}", p.proyecto) if x),
                                   "url": reverse("herramientas:cad_plano", args=[p.pk])})
    respuesta = JsonResponse({"resultados": resultados[:20]})
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta


@usuario_de_herramientas
@require_GET
def zip_descargar(request, token):
    """El ZIP que armó una tarea de ESTA cuenta (nadie más lo descarga). Se sirve por partes, sin cargarlo en memoria."""
    from django.http import FileResponse
    from django.utils.http import content_disposition_header

    from .comun import zips

    encontrado = zips.de_la_cuenta(request.user.pk, token)
    if encontrado is None:
        raise Http404
    ruta, nombre = encontrado
    respuesta = FileResponse(open(ruta, "rb"), content_type="application/zip")
    respuesta["Content-Disposition"] = content_disposition_header(True, nombre)
    respuesta["Cache-Control"] = "private, no-store"
    return respuesta
