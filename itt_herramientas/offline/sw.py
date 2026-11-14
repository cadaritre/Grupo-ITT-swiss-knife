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
    "offline/compartir.js", "offline/ui.js", "offline/captura.js", "offline/instalar.js", "offline/pwa.css",
    "offline/ayudas.js", "offline/documentos.js", "offline/vista-pdf.js",
    "reportes/reportes.js", "reportes/reportes.css", "croquis/croquis.js", "croquis/croquis.css",
    "cotizaciones/totales.js", "cotizaciones/borradores.js", "cotizaciones/ortografia.js", "cotizaciones/editor.js", "cotizaciones/cotizaciones.css",
    "vendor/pdfjs/pdf.min.mjs", "vendor/pdfjs/pdf.worker.min.mjs",
    *(f"img/icono-{clave}-{tam}.png" for clave in ("grupo_itt", "tresvizo") for tam in (192, 512)),
    *(f"img/icono-maskable-{clave}-512.png" for clave in ("grupo_itt", "tresvizo")),
    *(f"img/apple-touch-icon-{clave}.png" for clave in ("grupo_itt", "tresvizo")),
)


@lru_cache(maxsize=1)
def version() -> str:
    resumen = hashlib.sha256((AQUI / "sw.js.tpl").read_bytes())
    for relativa in sorted(set(ACTIVOS)):
        resumen.update(relativa.encode())
        resumen.update((ESTATICOS / relativa).read_bytes())
    return resumen.hexdigest()[:12]


def contenido() -> str:
    plantilla = (AQUI / "sw.js.tpl").read_text(encoding="utf-8")
    return (plantilla.replace("__VERSION__", version())
            .replace("__ACTIVOS__", json.dumps([static(f"herramientas/{a}") for a in ACTIVOS]))
            .replace("__SCRIPTS_SW__", json.dumps([static(f"herramientas/{a}") for a in SCRIPTS_SW])))
