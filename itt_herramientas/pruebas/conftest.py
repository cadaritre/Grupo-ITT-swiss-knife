from __future__ import annotations

import pytest
from django.urls import reverse as _reverse
from tvplataforma.middleware import urlconf_de

HOST = "tools.tresvizo.com"


def url(nombre: str, **kwargs) -> str:
    """URL de una página de Herramientas, relativa a la raíz de su host (sin prefijo ni host escritos a mano)."""
    return _reverse(f"herramientas:{nombre}", kwargs=kwargs or None, urlconf=urlconf_de("herramientas"))


@pytest.fixture
def crear_usuario(db):
    from django.contrib.auth import get_user_model

    def crear(nombre, superusuario=False, activo=True):
        U = get_user_model()
        return U.objects.create_user(nombre, password="x", is_superuser=superusuario, is_staff=superusuario, is_active=activo)
    return crear


@pytest.fixture(autouse=True)
def archivos_aislados(settings, tmp_path):
    """Cada prueba escribe en su propia carpeta de archivos de Herramientas."""
    settings.TV_HERRAMIENTAS_ARCHIVOS = tmp_path / "archivos"


@pytest.fixture
def cliente_de():
    """Un cliente nuevo por cuenta, en el host de Herramientas."""
    from django.test import Client

    def entrar(usuario):
        cliente = Client(HTTP_HOST=HOST)
        cliente.force_login(usuario)
        return cliente
    return entrar


@pytest.fixture
def propietario(crear_usuario):
    return crear_usuario("propietario", superusuario=True)


@pytest.fixture
def usuaria(crear_usuario, roles):
    u = crear_usuario("ana")
    roles.conceder(u, "herramientas", "cotizaciones")
    return u


@pytest.fixture
def administradora(crear_usuario, roles):
    u = crear_usuario("admin")
    roles.conceder(u, "herramientas", "admin")
    return u


@pytest.fixture(scope="session")
def personales_escritorio(tmp_path_factory):
    """Datos de fábrica del escritorio (firmante, datos bancarios...), leídos de `main` en cada corrida.

    No están en este repositorio (es público): la prueba los lee del código de la rama del escritorio y comprueba que
    el perfil corporativo de la web suministra exactamente los mismos valores.
    """
    import json
    import os

    from paridad import ejecutor

    trabajo = tmp_path_factory.mktemp("personales")
    try:
        escritorio = ejecutor.extraer_rama(os.environ.get("ITT_PARIDAD_REF", "main"), trabajo / "main")
    except ejecutor.ParidadError as exc:
        pytest.skip(str(exc))
    destino = trabajo / "personales.json"
    proceso = ejecutor.subprocess.run([ejecutor.sys.executable, "-s", str(ejecutor.AQUI / "trabajador.py"), "--volcar-personales", str(escritorio), str(destino)],
                                      capture_output=True, text=True, env=ejecutor._entorno())
    assert proceso.returncode == 0, proceso.stderr
    return json.loads(destino.read_text(encoding="utf-8"))


def subir_tus(cliente, datos: bytes, destino: str, nombre: str, **meta):
    """Sube `datos` con el protocolo tus como lo hace el navegador. Devuelve (respuesta final, ruta de la subida)."""
    import base64
    import hashlib
    from urllib.parse import urlparse

    cabeza = {"HTTP_TUS_RESUMABLE": "1.0.0"}
    campos = {"destino": destino, "name": nombre, "sha256": hashlib.sha256(datos).hexdigest(), **meta}
    codificado = ",".join(f"{k} {base64.b64encode(str(v).encode()).decode()}" for k, v in campos.items())
    r = cliente.post("/subidas/", HTTP_UPLOAD_LENGTH=str(len(datos)), HTTP_UPLOAD_METADATA=codificado, **cabeza)
    if r.status_code != 201:
        return r, None
    ruta = urlparse(r["Location"]).path
    return cliente.generic("PATCH", ruta, datos, content_type="application/offset+octet-stream", HTTP_UPLOAD_OFFSET="0", **cabeza), ruta


@pytest.fixture
def convertidora(crear_usuario, roles):
    u = crear_usuario("conv")
    roles.conceder(u, "herramientas", "convertidor")
    return u


@pytest.fixture(scope="session")
def entradas_geo(tmp_path_factory):
    """Archivos de entrada deterministas del convertidor (los mismos de la paridad con el escritorio)."""
    from paridad import casos

    return casos.generar_entradas(tmp_path_factory.mktemp("entradas_geo"))
