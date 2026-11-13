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
from django.views.decorators.http import require_http_methods, require_POST
from tvplataforma import cuentas
from tvplataforma.permisos import requiere_rol

from .comun import diccionarios, importacion, perfiles, preferencias
from .comun.formularios import PaletaForm, PerfilForm, PreferenciasForm, TextosForm
from .modulo import ROLES_DE_HERRAMIENTAS
from .models import AccesoPerfil, Perfil, TerminoTecnico

usuario_de_herramientas = requiere_rol("herramientas", *ROLES_DE_HERRAMIENTAS, "admin")
solo_admin = requiere_rol("herramientas", "admin")
TTL_IMPORTACION = 3600


def _es_admin(request) -> bool:
    return perfiles.es_admin(request.user)


@usuario_de_herramientas
def inicio(request):
    return render(request, "herramientas/inicio.html", {"es_admin": _es_admin(request), "perfiles": perfiles.perfiles_de(request.user)})


@usuario_de_herramientas
@require_http_methods(["GET", "POST"])
def preferencias_(request):
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
    return render(request, "herramientas/perfiles.html", {"perfiles": Perfil.objects.all()})


def _perfil(clave: str) -> Perfil:
    try:
        return Perfil.objects.get(clave=clave)
    except Perfil.DoesNotExist:
        raise Http404 from None


@solo_admin
@require_http_methods(["GET", "POST"])
def perfil_editar(request, clave):
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


@solo_admin
@require_http_methods(["GET", "POST"])
def perfil_accesos(request, clave):
    perfil = _perfil(clave)
    candidatas = {}
    for rol in (*ROLES_DE_HERRAMIENTAS, "admin"):
        for persona in cuentas.con_rol("herramientas", rol):
            candidatas[persona.id] = persona
    actuales = {a.cuenta_id: a for a in AccesoPerfil.objects.filter(perfil=perfil)}
    if request.method == "POST":
        elegidas = {i for i in candidatas if request.POST.get(f"acceso_{i}")}
        predeterminadas = {i for i in elegidas if request.POST.get(f"pred_{i}")}
        AccesoPerfil.objects.filter(perfil=perfil).exclude(cuenta_id__in=elegidas).delete()
        for i in elegidas:
            AccesoPerfil.objects.update_or_create(cuenta_id=i, perfil=perfil, defaults={"predeterminado": i in predeterminadas})
        messages.success(request, "Accesos guardados.")
        return redirect("herramientas:perfil_accesos", clave=clave)
    filas = [{"persona": p, "acceso": p.id in actuales, "predeterminado": bool(actuales.get(p.id) and actuales[p.id].predeterminado)}
             for p in sorted(candidatas.values(), key=lambda p: p.nombre.lower())]
    return render(request, "herramientas/perfil_accesos.html", {"perfil": perfil, "filas": filas})


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
