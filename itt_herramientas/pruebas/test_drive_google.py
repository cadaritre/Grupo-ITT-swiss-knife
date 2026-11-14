"""Cliente de Google contra el Google falso: PKCE, renovación, carpetas, búsqueda y carga reanudable con cortes."""
from __future__ import annotations

import hashlib
import urllib.parse

import pytest

from itt_herramientas.drive import google

from .google_falso import GoogleFalso

pytestmark = pytest.mark.django_db


@pytest.fixture
def g(settings):
    settings.TV_DRIVE_CLIENT_ID = "cliente-de-prueba"
    settings.TV_DRIVE_CLIENT_SECRET = "secreto"
    falso = GoogleFalso()
    return falso, google.Google(falso)


def test_url_de_autorizacion_trae_pkce_acceso_sin_conexion_y_solo_drive_file(g):
    _, cli = g
    verificador, desafio = google.pkce()
    url = cli.url_autorizacion("https://tools.tresvizo.com/drive/oauth/", "estado123", desafio)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] == desafio and len(verificador) >= 43
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["state"] == "estado123"
    assert q["scope"].split() == ["openid", "email", "https://www.googleapis.com/auth/drive.file"]          # nada de «drive» completo
    assert q["redirect_uri"] == "https://tools.tresvizo.com/drive/oauth/" and q["client_id"] == "cliente-de-prueba"


def test_canje_con_pkce_y_renovacion(g):
    falso, cli = g
    verificador, desafio = google.pkce()
    codigo = falso.codigo(desafio, "https://x/cb")
    t = cli.canjear(codigo, "https://x/cb", verificador)
    assert t["refresh_token"] and t["access_token"]
    with pytest.raises(google.Reautorizar):                                    # el código es de un solo uso
        cli.canjear(codigo, "https://x/cb", verificador)
    with pytest.raises(google.Reautorizar):                                    # verificador equivocado
        cli.canjear(falso.codigo(desafio, "https://x/cb"), "https://x/cb", "otro")
    assert cli.renovar(t["refresh_token"])["access_token"]
    falso.revocar_en_google()
    with pytest.raises(google.Reautorizar):
        cli.renovar(t["refresh_token"])


def test_drive_file_la_app_solo_ve_lo_que_creo_o_le_concedieron(g):
    falso, cli = g
    ajena = falso.carpeta("Ajena", conceder=False)
    propia = falso.carpeta("Elegida con Picker")
    acceso = falso._nuevo_acceso()
    with pytest.raises(google.NoDisponible):
        cli.archivo(acceso, ajena)
    assert cli.archivo(acceso, propia)["name"] == "Elegida con Picker"
    hija = cli.crear_carpeta(acceso, "Persona", propia)
    assert cli.buscar(acceso, propia, "Persona", carpeta=True)[0]["id"] == hija["id"]
    assert cli.buscar(acceso, propia, "No existe", carpeta=True) == []
    with pytest.raises(google.NoDisponible):
        cli.crear_carpeta(acceso, "X", ajena)


def test_busqueda_escapa_comillas(g):
    falso, cli = g
    raiz = falso.carpeta()
    acceso = falso._nuevo_acceso()
    cli.crear_carpeta(acceso, "Pérez 'Hnos' \\ S.A.", raiz)
    assert len(cli.buscar(acceso, raiz, "Pérez 'Hnos' \\ S.A.", carpeta=True)) == 1


def test_token_vencido_y_limites_de_uso(g):
    falso, cli = g
    raiz = falso.carpeta()
    acceso = falso._nuevo_acceso()
    falso.vencer_accesos()
    with pytest.raises(google.TokenVencido):
        cli.buscar(acceso, raiz)
    acceso = falso._nuevo_acceso()
    falso.fallar("/drive/v3/files", 429)
    with pytest.raises(google.Temporal):
        cli.buscar(acceso, raiz)
    falso.fallar("/drive/v3/files", 500)
    with pytest.raises(google.Temporal):
        cli.buscar(acceso, raiz)


def test_carga_reanudable_en_trozos_y_con_cortes(g, tmp_path):
    falso, cli = g
    raiz = falso.carpeta()
    acceso = falso._nuevo_acceso()
    datos = bytes(range(256)) * 4096 * 3 + b"cola"                       # ~3 MB
    ruta = tmp_path / "grande.pdf"
    ruta.write_bytes(datos)
    r = cli.subir(acceso, ruta, "grande.pdf", raiz, "application/pdf", trozo=512 * 1024)
    assert r["md5Checksum"] == hashlib.md5(datos).hexdigest() and falso.archivos[r["id"]]["contenido"] == datos
    # Se corta la conexión dos veces a media subida: se consulta hasta dónde llegó Google y sigue desde ahí, sin repetir ni perder bytes.
    falso.cortes = [100_000, 300_000]
    r2 = cli.subir(acceso, ruta, "otra.pdf", raiz, "application/pdf", trozo=512 * 1024)
    assert falso.archivos[r2["id"]]["contenido"] == datos
    # Una carpeta que no existe: 404 antes de mandar un solo byte.
    with pytest.raises(google.NoDisponible):
        cli.subir(acceso, ruta, "x.pdf", "no-existe", "application/pdf")
    # Si se corta más veces de las permitidas: falla pasajera (se reintenta luego), no pérdida.
    falso.cortes = [10] * 20
    with pytest.raises(google.Temporal):
        cli.subir(acceso, ruta, "y.pdf", raiz, "application/pdf", trozo=512 * 1024, intentos=3)


def test_archivo_vacio(g, tmp_path):
    falso, cli = g
    raiz = falso.carpeta()
    acceso = falso._nuevo_acceso()
    vacio = tmp_path / "vacio.json"
    vacio.write_bytes(b"")
    assert cli.subir(acceso, vacio, "vacio.json", raiz, "application/json")["size"] == "0"
