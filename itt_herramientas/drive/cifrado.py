"""Cifrado en reposo de los tokens de Google (Fernet: AES-128-CBC + HMAC-SHA256).

La llave se deriva de `SECRET_KEY` con HKDF: si esa llave cambia, los tokens guardados ya no se pueden leer y cada persona vuelve a
conectar Drive (no se pierde nada más). Nunca se registran ni se muestran.
"""
from __future__ import annotations

import base64

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings


class TokenIlegible(Exception):
    """El token guardado no se pudo descifrar (cambió la llave o el dato está dañado)."""


def _fernet() -> Fernet:
    llave = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"herramientas.drive.v1", info=b"token-de-actualizacion").derive(settings.SECRET_KEY.encode())
    return Fernet(base64.urlsafe_b64encode(llave))


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def descifrar(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as exc:
        raise TokenIlegible from exc
