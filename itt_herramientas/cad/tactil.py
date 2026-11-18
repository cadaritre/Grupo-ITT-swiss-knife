"""CAD · «Modo táctil» (teléfono y tableta) y CAD sin conexión, detrás del ajuste del servidor `TV_CAD_TACTIL` (apagado por omisión).

Mientras el ajuste está apagado nada de esto se ve ni se sirve: la página del plano es la de siempre (en el teléfono, solo ver), el
service worker es el mismo byte a byte y estas vistas responden 404. Se enciende cuando la versión descargable esté terminada y probada.

- `activo()`: el ajuste (`settings.TV_CAD_TACTIL`, o la variable de entorno del mismo nombre: «1», «true», «si»).
- `paquete`: la lista de archivos del editor fijado (ruta, SHA-256 y tamaño, el comprimido incluido) para descargarlo una sola vez al
  teléfono (Cache Storage por el service worker de Herramientas). Solo cambia cuando cambia la versión fijada.
- `sin_conexion`: la página de CAD sin conexión. Su HTML no lleva datos de ninguna cuenta (como /capturar/), así que se guarda en el
  teléfono junto con el editor; los planos y sus cambios viven en el almacenamiento del navegador de esa cuenta.

Ver docs/cad.md, «Modo táctil» y «CAD sin conexión».
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from tvplataforma.permisos import requiere_rol

from . import editor, fuentes

ver = requiere_rol("herramientas", "cad", "admin", "supervision")
#: No se bajan al teléfono: el manifiesto (no lo pide el editor), las declaraciones de tipos del trabajador, y las letras chinas,
#: japonesas y coreanas (24 MB comprimidas de 63): el editor carga al arrancar solo las del idioma de su interfaz y las demás cuando un
#: plano las usa (src/scene/text/web_font.rs). Con conexión se bajan solas si un plano las necesita; sin conexión ese texto sale con la
#: letra de reemplazo.
SIN_DESCARGA = ("MANIFIESTO.json",)
SIN_DESCARGA_SUFIJOS = (".d.ts",)
SIN_DESCARGA_LETRAS = ("fonts/chinese.ttf", "fonts/traditional_chinese.ttf", "fonts/japanese.ttf", "fonts/korean.ttf")


def activo() -> bool:
    valor = getattr(settings, "TV_CAD_TACTIL", None)
    if valor is None:
        valor = os.environ.get("TV_CAD_TACTIL", "")
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


@lru_cache(maxsize=4)
def _paquete(raiz: str, version: str) -> dict:
    final = Path(raiz) / version
    manifiesto = json.loads((final / "MANIFIESTO.json").read_text(encoding="utf-8"))
    archivos = []
    for ruta, sha in sorted(manifiesto["archivos"].items()):
        if ruta in SIN_DESCARGA or ruta in SIN_DESCARGA_LETRAS or ruta.endswith(SIN_DESCARGA_SUFIJOS):
            continue
        p = final / ruta
        gz = Path(str(p) + ".gz")
        tam = p.stat().st_size
        archivos.append({"ruta": ruta, "sha256": sha, "bytes": tam, "descarga": gz.stat().st_size if gz.is_file() else tam})
    return {"version": version, "base": f"/cad/editor/{version}/", "archivos": archivos,
            "bytes": sum(a["bytes"] for a in archivos), "descarga": sum(a["descarga"] for a in archivos)}


log = logging.getLogger(__name__)


def paquete_del_editor() -> dict | None:
    """El paquete del editor para bajarlo al teléfono, o None. Un editor sin su MANIFIESTO.json (o con uno dañado) no tumba la página
    del plano: el modo táctil sigue, solo que sin «Descargar CAD en este teléfono», y queda en el registro."""
    r = editor.raiz()
    if r is None or not editor.disponible():
        return None
    try:
        return _paquete(str(r), editor.version())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("El editor %s no tiene un MANIFIESTO.json válido (%s): no se ofrece bajarlo al teléfono.", editor.version(), exc)
        return None


def _contexto_sin_conexion() -> dict:
    urls = {"paquete": reverse("herramientas:cad_tactil_paquete"), "sin_conexion": reverse("herramientas:cad_sin_conexion"),
            "sesion": reverse("herramientas:sesion"), "instalar": reverse("herramientas:instalar"), "lista": reverse("herramientas:cad")}
    return {"config": {"campo": True, "tactil": {"version": editor.version(), "urls": urls}, "urls": urls}}


def version_pagina(request) -> str:
    """Huella de la página sin conexión tal como la recibe esta cuenta (su HTML, que nombra cada estilo y script con su huella, y depende del
    perfil) más las fuentes SHX. La página del plano la lleva en su configuración: el teléfono vuelve a guardar la página sin conexión
    solo cuando cambia, sin preguntar nada al servidor en cada apertura."""
    html = render_to_string("herramientas/cad/sin_conexion.html", _contexto_sin_conexion(), request=request)
    resumen = hashlib.sha256(html.encode())
    resumen.update(json.dumps([[f["nombre"], f["url"]] for f in fuentes.para_editor()]).encode())
    return resumen.hexdigest()[:16]


def config_de_pagina(request) -> dict | None:
    """Lo que la página del plano necesita del modo táctil (None si el ajuste está apagado)."""
    if not activo():
        return None
    p = paquete_del_editor()
    return {"version": editor.version(), "cuenta": request.user.pk, "pagina": version_pagina(request),
            "descarga": p["descarga"] if p else 0, "bytes": p["bytes"] if p else 0,
            "urls": {"paquete": reverse("herramientas:cad_tactil_paquete"), "sin_conexion": reverse("herramientas:cad_sin_conexion"),
                     "sesion": reverse("herramientas:sesion"), "instalar": reverse("herramientas:instalar")}}


@ver
@require_GET
def paquete(request):
    if not activo():
        raise Http404
    datos = paquete_del_editor()
    if datos is None:
        raise Http404
    r = JsonResponse(datos)
    r["Cache-Control"] = "no-cache"
    return r


@ver
@require_GET
def sin_conexion(request):
    """CAD sin conexión: lista de planos guardados en este teléfono y el plano en modo táctil. Mismo HTML para todas las cuentas."""
    if not activo():
        raise Http404
    r = render(request, "herramientas/cad/sin_conexion.html", _contexto_sin_conexion())
    r["Cache-Control"] = "no-cache"
    return r
