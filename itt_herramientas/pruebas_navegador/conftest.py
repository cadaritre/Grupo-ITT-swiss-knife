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


#: Tope por prueba (s). Una prueba que se cuelga (p. ej. un `evaluate` cuya promesa nunca se cumple: Playwright no le pone plazo) falla
#: aquí con la pila de todos los hilos, en vez de retener el candado de la máquina. Se cambia con ITT_LIMITE_PRUEBA.
LIMITE_PRUEBA = int(os.environ.get("ITT_LIMITE_PRUEBA", "600"))


class PruebaColgada(Exception):
    pass


@pytest.fixture(autouse=True)
def limite_por_prueba(request):
    import faulthandler
    import signal
    import sys

    vencio = []

    def vencida(signum, frame):
        vencio.append(True)
        sys.stderr.write(f"\n*** {request.node.nodeid} pasó de {LIMITE_PRUEBA} s: pila de los hilos ***\n")
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        raise PruebaColgada(f"La prueba pasó de {LIMITE_PRUEBA} s (ITT_LIMITE_PRUEBA)")

    previo = signal.signal(signal.SIGALRM, vencida)
    signal.alarm(LIMITE_PRUEBA)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previo)
    if vencio:
        # Tras interrumpir el bucle de Playwright, el resto de la sesión no es confiable: se detiene la corrida para soltar el candado.
        pytest.exit(f"Corrida detenida: {request.node.nodeid} se colgó", returncode=3)


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


# --- Ensayo en modo producción (TV_PRUEBAS_PRODUCCION=1) -------------------------------------------------------------------------------
# Antes de un despliegue: las mismas pruebas con los estáticos como en producción (collectstatic con huella en los nombres) y DEBUG
# apagado. El 14-11 un nombre con huella rompió en producción lo que en desarrollo pasaba. Los estáticos se recolectan una vez por sesión.

@pytest.fixture(scope="session")
def _estaticos_produccion(tmp_path_factory):
    if os.environ.get("TV_PRUEBAS_PRODUCCION") != "1":
        return None
    from django.core.management import call_command
    from django.test import override_settings

    raiz = tmp_path_factory.mktemp("estaticos-produccion")
    almacen = {"BACKEND": "itt_herramientas.pruebas.test_estaticos_produccion.AlmacenDePrueba"}
    from django.conf import settings as s

    with override_settings(STATIC_ROOT=raiz, STORAGES={**s.STORAGES, "staticfiles": almacen}):
        call_command("collectstatic", interactive=False, verbosity=0)
    return raiz, almacen


@pytest.fixture(autouse=True)
def _modo_produccion(request, _estaticos_produccion):
    if not _estaticos_produccion:
        yield
        return
    from django.contrib.staticfiles import finders

    settings = request.getfixturevalue("settings")
    raiz, almacen = _estaticos_produccion
    settings.DEBUG = False
    settings.STATIC_ROOT = raiz
    settings.STORAGES = {**settings.STORAGES, "staticfiles": almacen}
    settings.STATICFILES_DIRS = [*getattr(settings, "STATICFILES_DIRS", []), str(raiz)]
    finders.get_finder.cache_clear()
    yield
    finders.get_finder.cache_clear()
