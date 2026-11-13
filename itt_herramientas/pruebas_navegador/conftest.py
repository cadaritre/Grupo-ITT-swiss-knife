"""Pruebas de navegador (Playwright) de la PWA de Herramientas: Chromium con el perfil de un Pixel y WebKit con el de un iPhone.

WebKit de Playwright NO es iOS Safari: sirve para el motor y los tamaños, no para los límites del sistema iOS (ver
docs/pwa.md, «Lo que Playwright no puede probar»). Corre bajo el candado de máquina compartida:
    pytest itt_herramientas/pruebas_navegador
"""
from __future__ import annotations

import io
import os

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "1")        # Playwright (sync) y el ORM de Django comparten el hilo de la prueba

import pytest
from PIL import Image

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

DISPOSITIVOS = {"chromium": "Pixel 7", "webkit": "iPhone 13"}


@pytest.fixture(scope="session")
def playwright_():
    with sync_playwright() as p:
        yield p


@pytest.fixture(params=sorted(DISPOSITIVOS))
def motor(request, playwright_):
    nombre = request.param
    tipo = getattr(playwright_, nombre)
    try:
        ruta = tipo.executable_path
    except Exception:
        ruta = ""
    if not ruta or not os.path.exists(ruta):
        pytest.skip(f"El navegador {nombre} no está instalado (python -m playwright install {nombre})")
    return {"nombre": nombre, "tipo": tipo, "dispositivo": playwright_.devices[DISPOSITIVOS[nombre]]}


@pytest.fixture
def servidor(live_server, settings):
    """Servidor en vivo que se hace pasar por el host de Herramientas (en producción, tools.tresvizo.com)."""
    settings.TV_HOSTS = {"herramientas": "localhost"}
    settings.ALLOWED_HOSTS = ["*"]
    return live_server


@pytest.fixture
def sesion_de(servidor):
    """Cookie de sesión de `usuario` para el navegador."""
    from django.conf import settings
    from django.test import Client

    def cookie(usuario):
        c = Client()
        c.force_login(usuario)
        valor = c.cookies[settings.SESSION_COOKIE_NAME].value
        return {"name": settings.SESSION_COOKIE_NAME, "value": valor, "url": servidor.url}
    return cookie


def foto(tam=(3000, 2000), color="#2F6F9F", gps=True, orientacion=None, fecha="2026:11:13 09:30:00") -> bytes:
    from PIL import ExifTags, ImageDraw

    imagen = Image.new("RGB", tam, color)
    d = ImageDraw.Draw(imagen)
    for k in range(0, tam[0], max(1, tam[0] // 12)):
        d.line((k, 0, tam[0] - k, tam[1]), fill="#FFFFFF", width=4)
    exif = Image.Exif()
    exif[ExifTags.Base.DateTimeOriginal] = fecha
    exif[ExifTags.Base.DateTime] = fecha
    if orientacion:
        exif[ExifTags.Base.Orientation] = orientacion
    if gps:
        exif[ExifTags.IFD.GPSInfo] = {1: "N", 2: (28.0, 37.0, 58.8), 3: "W", 4: (106.0, 4.0, 8.76)}
    b = io.BytesIO()
    imagen.save(b, "JPEG", quality=90, exif=exif.tobytes())
    return b.getvalue()
