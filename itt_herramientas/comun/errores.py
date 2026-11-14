"""Errores que ve la persona: siempre un mensaje claro en español, nunca rutas del servidor ni texto de excepciones.

Regla: lo que escribimos nosotros (validaciones, límites) viaja en un `ErrorUsuario` y se muestra tal cual. Todo lo demás
(una biblioteca que falla, un archivo corrupto, un fallo de red) se registra completo en el servidor y la persona recibe un
mensaje fijo que dice qué hacer. Lo usan las tareas, las vistas y Drive.
"""
from __future__ import annotations

import logging
import re
import zipfile
from xml.etree.ElementTree import ParseError

log = logging.getLogger("itt_herramientas")

GENERICO = "No se pudo completar la operación. Inténtalo de nuevo; si sigue fallando, avisa a soporte."
ARCHIVO_ILEGIBLE = "No se pudo leer el archivo. Revisa que sea un DXF, KML o KMZ válido y que no esté dañado, y vuelve a subirlo."


class ErrorUsuario(ValueError):
    """Un error cuyo mensaje está escrito para la persona: se puede mostrar tal cual."""


# Texto que delata una excepción de biblioteca (rutas, nombres de clase o inglés) en un ValueError/RuntimeError que no marcamos nosotros.
_RUTA = re.compile(r"(?:/[\w.\-]+){2,}|[A-Za-z]:\\|\\\\|\.py\b|\.pyc?\b|Traceback|0x[0-9a-f]{4,}|<[\w. ]+ object")
_INGLES = re.compile(r"\b(?:is|are|was|were|not|invalid|expected|cannot|can't|unable|object|argument|got|file|must|failed|has|have|missing|index|key|attribute|type|value|line|column|found|unexpected|could|does|the|of)\b", re.I)


def es_presentable(texto: str) -> bool:
    """¿Se puede enseñar este texto? No debe traer rutas, trazas ni inglés de una excepción de biblioteca."""
    texto = (texto or "").strip()
    return bool(texto) and len(texto) <= 400 and not _RUTA.search(texto) and not _INGLES.search(texto)


def texto(exc: BaseException, por_omision: str = GENERICO) -> str:
    """El mensaje para la persona. Registra el detalle técnico en el servidor cuando lo oculta."""
    if isinstance(exc, ErrorUsuario):
        return str(exc)
    if isinstance(exc, MemoryError):
        return "El archivo es demasiado grande para procesarlo en la web. Ábrelo en Herramientas Grupo ITT (módulo local), que no tiene este límite."
    if isinstance(exc, (zipfile.BadZipFile, ParseError, UnicodeDecodeError)):
        log.warning("Archivo ilegible: %s: %s", type(exc).__name__, exc)
        return ARCHIVO_ILEGIBLE
    if _es_dxf_invalido(exc):
        log.warning("DXF inválido: %s: %s", type(exc).__name__, exc)
        return "El archivo no es un DXF válido o está dañado. Guárdalo de nuevo desde tu programa de dibujo y vuelve a subirlo."
    if isinstance(exc, (ValueError, RuntimeError)) and not isinstance(exc, OSError) and es_presentable(str(exc)):
        return str(exc)
    log.error("Error oculto a la persona: %s: %s", type(exc).__name__, exc, exc_info=exc)
    return por_omision


def _es_dxf_invalido(exc: BaseException) -> bool:
    try:
        import ezdxf
    except ImportError:                                                                # pragma: no cover
        return False
    return isinstance(exc, ezdxf.DXFError) or (isinstance(exc, OSError) and "not a DXF file" in str(exc))    # ezdxf avisa así de un texto que no es DXF


class ErrorDeTarea(Exception):
    """Falla de una tarea en segundo plano con el mensaje que sí se muestra.

    El armazón guarda `NombreDeLaClase: mensaje`; la clase se llama «Error» para que se lea «Error: …» y no un nombre interno.
    """


ErrorDeTarea.__name__ = ErrorDeTarea.__qualname__ = "Error"
