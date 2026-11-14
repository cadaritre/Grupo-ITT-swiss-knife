"""Etiquetas de plantilla de Herramientas: identidad corporativa activa de la cuenta."""
from __future__ import annotations

from django import template
from tvplataforma import permisos

from ..comun import perfiles
from ..dominio import branding
from ..modulo import ROLES_DE_HERRAMIENTAS

register = template.Library()


def _usuario(context):
    peticion = context.get("request")
    usuario = getattr(peticion, "user", None)
    if usuario is None or not getattr(usuario, "is_authenticated", False):
        return None
    if not any(permisos.tiene_rol(usuario, "herramientas", r) for r in (*ROLES_DE_HERRAMIENTAS, "admin")):
        return None
    return usuario


@register.simple_tag(takes_context=True)
def perfil_clave(context) -> str:
    """Clave del perfil activo de la cuenta (`grupo_itt` si no hay sesión: las páginas públicas no llevan identidad)."""
    usuario = _usuario(context)
    return perfiles.perfil_activo(usuario).clave if usuario else perfiles.PERFIL_POR_OMISION


@register.inclusion_tag("herramientas/_marca.html", takes_context=True)
def marca_perfil(context):
    """Franja con el logo, el nombre y el sitio del perfil activo; con el permiso, también el selector de perfil."""
    usuario = _usuario(context)
    if usuario is None:
        return {"visible": False}
    perfil = perfiles.perfil_activo(usuario)
    with perfiles.activar(perfil):
        activo = branding.active_profile()
    sitio = activo.website if activo.website.lower().startswith(("http://", "https://")) else ""
    puede = perfiles.puede_cambiar(usuario)
    return {
        "visible": True, "request": context.get("request"), "csrf_token": context.get("csrf_token"), "perfil": perfil, "nombre": activo.name,
        "app": activo.app_name, "sitio": sitio, "sitio_etiqueta": activo.website_label, "puede_cambiar": puede,
        "opciones": [("grupo_itt", "Grupo ITT"), ("tresvizo", "TresVizo")] if puede else [],
    }


@register.inclusion_tag("herramientas/_confirmar_accion.html", takes_context=True)
def confirmar_accion(context, id, accion, abrir, titulo, texto, aceptar, detalle="", clase="tv-boton tv-boton--secundario", icono=""):
    """Botón que, en vez de ejecutar una acción que no se deshace, abre una hoja que dice exactamente qué va a pasar.

    `accion` es la URL del POST; `abrir` el texto del botón; `titulo` y `texto` lo que se le explica a la persona; `detalle` una segunda línea
    opcional (lo que SÍ se conserva). La acción solo se envía con el botón rojo de la hoja.
    """
    return {"id": id, "accion": accion, "abrir": abrir, "titulo": titulo, "texto": texto, "aceptar": aceptar, "detalle": detalle, "clase": clase, "icono": icono,
            "csrf_token": context.get("csrf_token")}


@register.inclusion_tag("herramientas/pwa/_lateral.html", takes_context=True)
def lateral_de_campo(context):
    """El lateral del armazón (mismo marcado que `tvplataforma/base.html`) para las páginas de campo, que abren sin conexión.

    Su HTML es igual para todas las cuentas: se pintan todas las entradas de `MODULO.menu` y `menu.js` deja visibles las de la
    cuenta con lo que dice `/sesion/` (y lo recuerda sin señal). La sección actual sale de la ruta, no de la cuenta.
    """
    from django.urls import NoReverseMatch, reverse

    from ..modulo import MODULO

    peticion = context.get("request")
    ruta = getattr(peticion, "path", "") or ""
    entradas, mejor = [], ""
    for e in MODULO.menu:
        try:
            url = reverse(e.url) if ":" in e.url else e.url
        except NoReverseMatch:
            continue
        entradas.append({"clave": e.clave, "etiqueta": e.etiqueta, "icono": e.icono, "url": url, "grupo": e.grupo})
        if ruta.startswith(url) and len(url) > len(mejor):
            mejor = url
    grupos: list[dict] = []
    for e in entradas:
        e["actual"] = e["url"] == mejor
        if not grupos or grupos[-1]["titulo"] != e["grupo"]:
            grupos.append({"titulo": e["grupo"], "entradas": []})
        grupos[-1]["entradas"].append(e)
    return {"grupos": grupos, "con_atajos": bool(context.get("tv_usuario"))}


@register.inclusion_tag("herramientas/_ajustes_nav.html", takes_context=True)
def nav_ajustes(context):
    """Secciones de Ajustes (Preferencias, Google Drive, Diccionario, Importar y, para la administración, Perfiles)."""
    from django.urls import reverse

    peticion = context.get("request")
    actual = getattr(getattr(peticion, "resolver_match", None), "url_name", "") or ""
    usuario = _usuario(context)
    entradas = [
        ("preferencias", "Preferencias", "sliders-horizontal", ("preferencias",)),
        ("drive", "Google Drive", "hard-drive-upload", ("drive",)),
        ("diccionario", "Diccionario", "spell-check", ("diccionario",)),
        ("importar", "Importar del escritorio", "folder-open", ("importar", "importar_confirmar")),
    ]
    if usuario is not None and perfiles.es_admin(usuario):
        entradas.append(("perfiles", "Perfiles", "building-2", ("perfiles", "perfil", "perfil_textos", "perfil_paleta")))
    return {"entradas": [{"url": reverse(f"herramientas:{u}"), "etiqueta": e, "icono": i, "actual": actual in paginas} for u, e, i, paginas in entradas]}
