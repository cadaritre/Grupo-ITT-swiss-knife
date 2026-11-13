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
