"""El editor CAD: la compilación web oficial de Open CAD Studio, fijada en `upstream.json` (versión y SHA-256) y servida en
`/cad/editor/<versión>/` del mismo host, fuera del manifiesto de estáticos de Django (su cargador pide sus archivos por nombre).

- La prepara `preparar.py` (desarrollo, pruebas y `preparar-release.sh` del portal): baja el ZIP, comprueba el SHA-256, lo extrae y pone
  encima nuestra página de arranque (`index.html`, `arranque.js`, `editor.css`): sin scripts en línea, sin analítica, sin service worker.
- En producción la sirve Caddy con las mismas cabeceras que `cabeceras()`; en desarrollo y pruebas, la vista `cad.vistas.editor_archivo`.
"""
from __future__ import annotations

import json
import mimetypes
import re
from functools import lru_cache
from pathlib import Path

from django.conf import settings

AQUI = Path(__file__).resolve().parent
#: Archivos cuyo nombre lleva el hash de Trunk (16 hex): se guardan un año; todo lo demás, 5 minutos.
CON_HASH = re.compile(r"-[0-9a-f]{16}(_bg)?\.(js|wasm|svg)$")
#: CSP solo de la página del editor. 'wasm-unsafe-eval' compila el WebAssembly; el trabajador que lee y escribe DWG/DXF es un módulo
#: del mismo origen. Sin red a terceros: connect-src, img-src y font-src son 'self' (la página de inicio del editor pedía videos y
#: miniaturas a YouTube; quedan bloqueados, y `preparar.py` sirve esas listas vacías). frame-ancestors 'self': solo lo puede contener una página de Herramientas.
CSP = "; ".join((
    "default-src 'self'",
    "script-src 'self' 'wasm-unsafe-eval'",
    # El motor de ventanas del editor (winit) dimensiona el lienzo con estilos en línea; sin esto el lienzo queda de 300 × 150 px.
    # Solo en esta ruta: los scripts siguen limitados al mismo origen y la página no muestra HTML de nadie.
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "worker-src 'self' blob:",
    "child-src 'self' blob:",
    "frame-ancestors 'self'",
    "base-uri 'self'",
    "form-action 'none'",
    "object-src 'none'",
))


@lru_cache(maxsize=1)
def upstream() -> dict:
    return json.loads((AQUI / "upstream.json").read_text(encoding="utf-8"))


def version() -> str:
    return upstream()["version"]


def raiz() -> Path | None:
    """Carpeta con una subcarpeta por versión (`<raiz>/<versión>/index.html`)."""
    ruta = getattr(settings, "HERRAMIENTAS_CAD_EDITOR_DIR", "") or ""
    return Path(ruta) if ruta else None


def disponible() -> bool:
    r = raiz()
    return r is not None and (r / version() / "index.html").is_file()


def url_editor() -> str:
    """URL del editor incrustado: `embed=1` (puente de guardado por postMessage) y `automation=1` (canal de control oficial,
    `window.ocsControl`, que usa `static/herramientas/cad/control.js` para las extensiones de la página, p. ej. «Civil»).

    Es seguro porque el canal solo se alcanza desde el mismo origen: el editor vive en un marco de tools.tresvizo.com, su CSP pone
    `frame-ancestors 'self'` y el navegador no deja que otro origen toque `contentWindow` del marco.
    """
    return f"/cad/editor/{version()}/index.html?embed=1&automation=1"


def archivo(ver: str, ruta: str) -> Path | None:
    r = raiz()
    if r is None or ver != version() or not ruta or ruta.endswith("/"):
        return None
    base = (r / ver).resolve()
    destino = (base / ruta).resolve()
    if base not in destino.parents or not destino.is_file() or destino.suffix in (".gz", ".br"):
        return None
    return destino


def tipo(ruta: Path) -> str:
    if ruta.suffix == ".wasm":
        return "application/wasm"
    if ruta.suffix in (".js", ".mjs"):
        return "text/javascript; charset=utf-8"
    return mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"


def cabeceras(ruta: Path) -> dict:
    # La CSP va en TODAS las respuestas: el trabajador (un módulo del mismo origen) toma la de su propio script, y también compila WebAssembly.
    datos = {"Cache-Control": "public, max-age=31536000, immutable" if CON_HASH.search(ruta.name) else "public, max-age=300",
             "X-Content-Type-Options": "nosniff", "Cross-Origin-Resource-Policy": "same-origin", "Content-Security-Policy": CSP,
             "X-Frame-Options": "SAMEORIGIN"}
    if ruta.suffix == ".html":
        datos["Cache-Control"] = "no-cache"
    return datos
