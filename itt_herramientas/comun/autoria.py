"""Autoría de los documentos: quién hizo cada cotización, reporte o croquis, tal como era su cuenta al crearlo."""
from __future__ import annotations

from django.contrib.auth import get_user_model


def de_usuario(usuario) -> tuple[str, str]:
    """(nombre, correo) de una cuenta. El nombre es el completo o, si no tiene, el de usuario; el correo puede faltar."""
    if usuario is None:
        return "", ""
    nombre = (usuario.get_full_name() or "").strip() or usuario.get_username()
    correo = (usuario.email or "").strip().lower()
    if not correo and "@" in usuario.get_username():
        correo = usuario.get_username().strip().lower()
    return nombre[:150], correo[:254]


def de_cuenta(cuenta_id) -> tuple[str, str]:
    if cuenta_id is None:
        return "", ""
    return de_usuario(get_user_model().objects.filter(pk=cuenta_id).first())


def fila(documento) -> dict:
    """Lo que ven las pantallas: nombre, correo y fecha de creación (ISO)."""
    return {"nombre": documento.autor_nombre, "correo": documento.autor_correo, "creada": documento.creada.isoformat()}
