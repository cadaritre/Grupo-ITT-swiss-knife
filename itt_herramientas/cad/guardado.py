"""CAD · el editor guardado en el equipo (computadora y teléfono): la lista de lo que hay que bajar y los parches entre versiones.

El navegador guarda el editor fijado en Cache Storage (`itt-cad-editor-<versión>`, static/herramientas/cad/instalador.js) y lo sirve el
service worker de Herramientas (offline/sw_cad_editor.js). En la computadora es automático al abrir un plano; en el teléfono, a pedido
(«Descargar CAD en este teléfono», detrás de TV_CAD_TACTIL). Ver docs/cad.md, «El editor guardado en el equipo».

- `paquete`: los archivos del editor (ruta, SHA-256 de MANIFIESTO.json, tamaño y lo que se baja) y, por archivo que cambió, los parches
  desde las versiones anteriores (`preparar.py`, «anteriores» de upstream.json). Solo cambia cuando cambia la versión fijada.
- `config_de_pagina`: lo que la página del plano necesita para guardar el editor (None si el editor no está preparado).
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from django.http import Http404, JsonResponse
from django.templatetags.static import static
from django.urls import reverse
from django.views.decorators.http import require_GET
from tvplataforma.permisos import requiere_rol

from . import editor

ver = requiere_rol("herramientas", "cad", "admin", "supervision")
log = logging.getLogger(__name__)
#: No se guardan: el manifiesto (no lo pide el editor), las declaraciones de tipos del trabajador, y las letras chinas, japonesas y
#: coreanas (24 MB comprimidas de 63): el editor carga al arrancar solo las del idioma de su interfaz y las demás cuando un plano las usa
#: (src/scene/text/web_font.rs). Con conexión se bajan solas si un plano las necesita; sin conexión ese texto sale con la letra de reemplazo.
SIN_DESCARGA = ("MANIFIESTO.json",)
SIN_DESCARGA_SUFIJOS = (".d.ts",)
SIN_DESCARGA_LETRAS = ("fonts/chinese.ttf", "fonts/traditional_chinese.ttf", "fonts/japanese.ttf", "fonts/korean.ttf")


@lru_cache(maxsize=4)
def _paquete(raiz: str, version: str) -> dict:
    final = Path(raiz) / version
    manifiesto = json.loads((final / "MANIFIESTO.json").read_text(encoding="utf-8"))
    base = f"/cad/editor/{version}/"
    parches = manifiesto.get("parches") or {}
    archivos = []
    for ruta, sha in sorted(manifiesto["archivos"].items()):
        if ruta in SIN_DESCARGA or ruta in SIN_DESCARGA_LETRAS or ruta.endswith(SIN_DESCARGA_SUFIJOS):
            continue
        p = final / ruta
        gz = Path(str(p) + ".gz")
        tam = p.stat().st_size
        a = {"ruta": ruta, "sha256": sha, "bytes": tam, "descarga": gz.stat().st_size if gz.is_file() else tam}
        # Por cada versión anterior que lo tiene: de qué archivo de esa versión sale (ruta y SHA-256) y el parche. «descarga» es lo
        # que viaja con brotli (HTTPS, producción); «descarga_gz», con gzip.
        lista = [{"desde": x["desde"], "ruta": x["ruta"], "sha256": x["sha256"], "url": base + x["archivo"],
                  "descarga": x["bytes_br"], "descarga_gz": x["bytes_gz"]} for x in parches.get(ruta, [])]
        if lista:
            a["parches"] = lista
        archivos.append(a)
    return {"version": version, "base": base, "archivos": archivos,
            "bytes": sum(a["bytes"] for a in archivos), "descarga": sum(a["descarga"] for a in archivos)}


def paquete_del_editor() -> dict | None:
    """El paquete del editor para guardarlo en el equipo, o None. Un editor sin su MANIFIESTO.json (o con uno dañado) no tumba la página
    del plano: el editor se abre de la red como siempre, sin guardarse, y queda en el registro."""
    r = editor.raiz()
    if r is None or not editor.disponible():
        return None
    try:
        return _paquete(str(r), editor.version())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.error("El editor %s no tiene un MANIFIESTO.json válido (%s): no se guarda en los equipos.", editor.version(), exc)
        return None


def config_de_pagina(request) -> dict | None:
    """Lo que la página del plano necesita para guardar el editor en el equipo y ponerlo al día (None si no hay paquete)."""
    p = paquete_del_editor()
    if p is None:
        return None
    return {"version": editor.version(), "descarga": p["descarga"], "bytes": p["bytes"],
            "urls": {"paquete": reverse("herramientas:cad_editor_paquete"), "trabajador": static("herramientas/cad/parche.worker.js")}}


@ver
@require_GET
def paquete(request):
    datos = paquete_del_editor()
    if datos is None:
        raise Http404
    r = JsonResponse(datos)
    r["Cache-Control"] = "no-cache"
    return r
