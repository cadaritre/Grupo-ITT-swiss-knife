"""«Imagen satelital desde una polilínea» (static/herramientas/cad/local/satelite.js): el proveedor, su clave y la CSP de la página.

El navegador baja las teselas DIRECTO del proveedor; el servidor no las ve ni las guarda. Aquí solo se dice a qué servicio ir, con qué clave
(configuración del servidor, nunca del repositorio) y se abre ese único origen en `connect-src` de la página del plano.
Proveedor y términos: docs/cad.md, «Imagen satelital».
"""
from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.templatetags.static import static

#: Esri World Imagery por ArcGIS Location Platform (teselas de 256 px en Web Mercator, con clave de API).
SERVICIO = "https://ibasemaps-api.arcgis.com/arcgis/rest/services/World_Imagery/MapServer"
#: La atribución si el servicio no da la suya (`copyrightText` de su descripción, que es la que se usa).
ATRIBUCION = "Powered by Esri · Esri, Vantor, Earthstar Geographics, and the GIS User Community"
PROVEEDOR = "Esri World Imagery (Esri, Vantor, Earthstar Geographics y la comunidad de usuarios SIG)"
#: Nivel de teselas más fino que se pide (≈ 7 cm por píxel en el ecuador; el proveedor dice dónde hay imagen).
NIVEL_MAX = 21


def clave() -> str:
    """La clave de API del proveedor (pública en el navegador, restringida a nuestro sitio por el proveedor). Vacía: la función se apaga."""
    return str(getattr(settings, "HERRAMIENTAS_CAD_SATELITE_CLAVE", "") or "").strip()


def servicio() -> str:
    return str(getattr(settings, "HERRAMIENTAS_CAD_SATELITE_SERVICIO", "") or SERVICIO).rstrip("/")


def origen() -> str:
    """El origen (esquema://host[:puerto]) del servicio: lo único que se agrega a `connect-src`."""
    u = urlsplit(servicio())
    return f"{u.scheme}://{u.netloc}"


def config_de_pagina() -> dict:
    """Lo que la página del plano necesita (solo para quien puede editar: insertar es dibujar)."""
    return {
        "clave": clave(), "servicio": servicio(), "zmax": NIVEL_MAX,
        # el proveedor y su atribución van en el panel de Civil (civil/pagina.py), no en cada página
        # Con su huella de estáticos: el trabajador nunca deriva estas direcciones de su propio nombre.
        "trabajador": static("herramientas/cad/local/satelite-trabajador.js"), "geo": static("herramientas/cad/local/satelite-geo.js"),
    }


def poner_csp(request, respuesta, config: dict | None):
    """La CSP de la página del plano con el origen del proveedor en `connect-src` (solo si la función está encendida en esta página).

    Es la del armazón (`tvplataforma.middleware.construir_csp`, con `TV_CSP_EXTRA`) más ese único origen: nada más se abre, y las demás
    páginas siguen con la suya. Las teselas se piden desde la página (no desde el trabajador), así que esta CSP es la que manda.
    """
    if not config or not config.get("clave"):
        return respuesta
    from tvplataforma import ajustes
    from tvplataforma.middleware import construir_csp

    nonce = getattr(request, "csp_nonce", None)
    if not nonce:
        return respuesta
    extra = {k: list(v) for k, v in dict(ajustes.valor("TV_CSP_EXTRA") or {}).items()}
    extra.setdefault("connect-src", [])
    if origen() not in extra["connect-src"]:
        extra["connect-src"].append(origen())
    respuesta["Content-Security-Policy"] = construir_csp(nonce, extra)
    return respuesta
