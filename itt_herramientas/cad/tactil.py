"""CAD · «Modo táctil» (teléfono y tableta) y CAD sin conexión, detrás del ajuste del servidor `TV_CAD_TACTIL` (apagado por omisión).

Mientras el ajuste está apagado nada de esto se ve ni se sirve: la página del plano es la de siempre (en el teléfono, solo ver), el
service worker no lleva la parte sin conexión y estas vistas responden 404 (el editor guardado en el equipo no depende del ajuste).

- `activo()`: el ajuste (`settings.TV_CAD_TACTIL`, o la variable de entorno del mismo nombre: «1», «true», «si»).
- El editor guardado en el teléfono usa lo mismo que la computadora (`guardado.py`): aquí solo se decide que en el teléfono sea a pedido.
- `sin_conexion`: la página de CAD sin conexión. Su HTML no lleva datos de ninguna cuenta (como /capturar/), así que se guarda en el
  teléfono junto con el editor; los planos y sus cambios viven en el almacenamiento del navegador de esa cuenta.

Ver docs/cad.md, «Modo táctil» y «CAD sin conexión».
"""
from __future__ import annotations

import hashlib
import json
import os

from django.conf import settings
from django.http import Http404
from django.shortcuts import render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET
from tvplataforma.permisos import requiere_rol

from . import editor, fuentes, guardado

ver = requiere_rol("herramientas", "cad", "admin", "supervision")


def activo() -> bool:
    valor = getattr(settings, "TV_CAD_TACTIL", None)
    if valor is None:
        valor = os.environ.get("TV_CAD_TACTIL", "")
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "on", "yes")


def _contexto_sin_conexion() -> dict:
    urls = {"paquete": reverse("herramientas:cad_editor_paquete"), "sin_conexion": reverse("herramientas:cad_sin_conexion"),
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
    p = guardado.paquete_del_editor()
    return {"version": editor.version(), "cuenta": request.user.pk, "pagina": version_pagina(request),
            "descarga": p["descarga"] if p else 0, "bytes": p["bytes"] if p else 0,
            "urls": {"paquete": reverse("herramientas:cad_editor_paquete"), "sin_conexion": reverse("herramientas:cad_sin_conexion"),
                     "sesion": reverse("herramientas:sesion"), "instalar": reverse("herramientas:instalar")}}


@ver
@require_GET
def sin_conexion(request):
    """CAD sin conexión: lista de planos guardados en este teléfono y el plano en modo táctil. Mismo HTML para todas las cuentas."""
    if not activo():
        raise Http404
    r = render(request, "herramientas/cad/sin_conexion.html", _contexto_sin_conexion())
    r["Cache-Control"] = "no-cache"
    return r
