"""Service worker de Herramientas: se sirve en la raíz de `tools.tresvizo.com/sw.js` con alcance `/`.

La versión es un resumen del contenido de los archivos de la interfaz sin conexión: cualquier cambio produce otro
`sw.js` y el navegador lo detecta (aparece «Hay una versión nueva · Actualizar»).
"""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path

from django.templatetags.static import static

AQUI = Path(__file__).resolve().parent
ESTATICOS = AQUI.parent / "static" / "herramientas"
#: scripts que carga el service worker (los mismos que las páginas)
SCRIPTS_SW = ("offline/cola.js", "offline/tus.js", "offline/subidor.js")
#: todo lo que se guarda en caché al instalar
ACTIVOS = (
    "offline/cola.js", "offline/tus.js", "offline/subidor.js", "offline/exif.js", "offline/reducir.js", "offline/reducir.worker.js",
    "offline/compartir.js", "offline/ui.js", "offline/menu.js", "offline/captura.js", "offline/instalar.js", "offline/pwa.css",
    "offline/ayudas.js", "offline/fusion.js", "offline/documentos.js", "offline/compartido.js", "offline/vista-pdf.js", "offline/listas.js", "offline/combobox.js",
    "reportes/orden.js", "reportes/reportes.js", "reportes/reportes.css", "croquis/croquis.js", "croquis/croquis.css",
    "cotizaciones/totales.js", "cotizaciones/borradores.js", "cotizaciones/ortografia.js", "cotizaciones/editor.js", "cotizaciones/cotizaciones.css",
    "cad/sesion.js", "vendor/pdfjs/pdf.min.mjs", "vendor/pdfjs/pdf.worker.min.mjs",
    "vendor/maplibre/maplibre-gl.css", "vendor/maplibre/maplibre-gl-csp.js", "vendor/maplibre/maplibre-gl-csp-worker.js",     # el croquis abre el mapa sin señal
    "vendor/terradraw/terra-draw.umd.js", "vendor/terradraw/terra-draw-maplibre-gl-adapter.umd.js",
    "img/marca-grupo_itt.png", "img/modulo.png",
    *(f"img/herramientas/{h}.png" for h in ("cotizaciones", "reportes", "croquis", "convertidor", "triangulacion")),     # pestañas del módulo
    *(f"img/favicon-{clave}-32.png" for clave in ("grupo_itt", "tresvizo")),
    *(f"img/icono-{clave}-{tam}.png" for clave in ("grupo_itt", "tresvizo") for tam in (192, 512)),
    *(f"img/icono-maskable-{clave}-512.png" for clave in ("grupo_itt", "tresvizo")),
    *(f"img/apple-touch-icon-{clave}.png" for clave in ("grupo_itt", "tresvizo")),
)


#: lo compartido de `tvplataforma` que cargan las páginas sin conexión (hojas de estilo y logo del encabezado). Se precarga al instalar:
#: si solo se guardara cuando una página ya controlada por el service worker lo pide, la primera apertura sin señal saldría sin estilos.
ACTIVOS_PLATAFORMA = ("tokens.css", "base.css", "componentes.css", "img/marca.png", "js/base.js")


def ruta_plataforma(relativa: str) -> Path:
    """Archivo de `tvplataforma/static/tvplataforma/` (de la app instalada, no de una copia)."""
    import tvplataforma

    return Path(tvplataforma.__file__).resolve().parent / "static" / "tvplataforma" / relativa


@lru_cache(maxsize=1)
def version() -> str:
    resumen = hashlib.sha256((AQUI / "sw.js.tpl").read_bytes())
    for relativa in sorted(set(ACTIVOS)):
        resumen.update(relativa.encode())
        resumen.update((ESTATICOS / relativa).read_bytes())
    for relativa in sorted(set(ACTIVOS_PLATAFORMA)):          # si cambia el sistema de diseño, la versión cambia y el teléfono se entera
        resumen.update(b"tvplataforma/" + relativa.encode())
        resumen.update(ruta_plataforma(relativa).read_bytes())
    return resumen.hexdigest()[:12]


def _cad() -> str:
    """CAD sin conexión (solo con TV_CAD_TACTIL encendido): con el ajuste apagado el service worker queda igual byte a byte."""
    from ..cad import tactil

    return (AQUI / "sw_cad.js").read_text(encoding="utf-8") if tactil.activo() else ""


def version_servida() -> str:
    """La versión del service worker que se sirve: la de sus archivos y, con el modo táctil encendido, también la de su parte de CAD."""
    cad = _cad()
    return version() if not cad else hashlib.sha256((version() + cad).encode()).hexdigest()[:12]


def contenido() -> str:
    plantilla = (AQUI / "sw.js.tpl").read_text(encoding="utf-8")
    cad = _cad()
    if cad:
        plantilla = plantilla.replace("importScripts(...__SCRIPTS_SW__);", "importScripts(...__SCRIPTS_SW__);\n\n" + cad, 1)
    return (plantilla.replace("__VERSION__", version_servida())
            .replace("__ACTIVOS__", json.dumps([static(f"herramientas/{a}") for a in ACTIVOS] + [static(f"tvplataforma/{a}") for a in ACTIVOS_PLATAFORMA]))
            .replace("__SCRIPTS_SW__", json.dumps([static(f"herramientas/{a}") for a in SCRIPTS_SW])))
