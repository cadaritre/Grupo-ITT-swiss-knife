from __future__ import annotations

import io
import json
import zipfile

import pytest
from PIL import Image

from itt_herramientas.comun import diccionarios, importacion, perfiles, preferencias
from itt_herramientas.dominio.almacenamiento import DEFAULT_SETTINGS
from itt_herramientas.dominio.quotation_models import QuoteItem
from itt_herramientas.dominio.quotation_templates import CustomConceptTemplateStore
from itt_herramientas.models import Machote, Perfil

pytestmark = pytest.mark.django_db


def logo() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (20, 10), "#ABCDEF").save(b, "PNG")
    return b.getvalue()


def ajustes(**extra) -> dict:
    base = json.loads(json.dumps(DEFAULT_SETTINGS))
    base["branding"]["profile"] = "tresvizo"
    base["branding"]["tresvizo"].update({"name": "TresVizo MX", "phone": "55 1111 2222", "signer": "ING. PERSONA PRUEBA", "custom_logo": "C:\\Users\\x\\logo_propio.png"})
    base["branding"]["grupo_itt"] = {"website": "https://itt.example.org/"}
    base["documents"] = {"report_texts": {"header_title": "Informe", "texto_nuevo_del_futuro": "x"},
                         "quote_texts": {"es": {"sincerely": "Cordialmente"}, "en": {"sincerely": "Kind regards"}},
                         "quote_palette": {"primary": "#112233", "accent": "#0B7FAB", "pale": "#EAF3F8", "bar_text": "#FFFFFF", "ink": "#263746"}}
    base["geospatial"].update({"utm_zone": 12, "hemisphere": "N"})
    base["reports"]["include_map"] = False
    base["cloud"] = {"enabled": True, "drive_url": "https://drive.example/x"}
    base.update(extra)
    return base


def zip_de_escritorio(tmp_path, settings=None, diccionario=True, machotes=True, logo_bytes=True, extra=None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        if settings is not False:
            zf.writestr("Configuracion/settings.json", json.dumps(settings or ajustes()))
        if logo_bytes:
            zf.writestr("Configuracion/logo_propio.png", logo())
        if diccionario:
            zf.writestr("Diccionarios/diccionario_personal.json", json.dumps({"words": {"es": ["mojonera", "Zorplón", "dos palabras"], "en": ["benchmark"]}}))
        if machotes:
            tienda = CustomConceptTemplateStore(tmp_path / "m.json")
            tienda.save_item("Inspección", QuoteItem(title="Inspección", unit="Día", quantity=2, unit_price=900.0), "es")
            tienda.save_item("Survey", QuoteItem(title="Survey"), "en")
            zf.writestr("Cotizaciones/Machotes/machotes_personalizados.json", (tmp_path / "m.json").read_text(encoding="utf-8"))
        for nombre, contenido in (extra or {}).items():
            zf.writestr(nombre, contenido)
    return buffer.getvalue()


def test_vista_previa_dice_que_se_importa(tmp_path):
    paquete = importacion.leer(zip_de_escritorio(tmp_path))
    claves = {e.clave: e for e in importacion.vista_previa(paquete)}
    assert set(claves) == {"identidad", "logo", "textos", "paleta", "preferencias", "diccionario", "machotes"}
    assert claves["machotes"].cantidad == 2 and claves["diccionario"].cantidad == 4
    assert claves["logo"].detalle == "Perfiles: tresvizo"


def test_aplicar_todo_como_administrador(tmp_path):
    paquete = importacion.leer(zip_de_escritorio(tmp_path))
    r = importacion.aplicar(paquete, 7, set(importacion.ELEMENTOS), es_admin=True)
    tv, itt = Perfil.objects.get(clave="tresvizo"), Perfil.objects.get(clave="grupo_itt")
    assert (tv.nombre, tv.telefono, tv.firmante) == ("TresVizo MX", "55 1111 2222", "ING. PERSONA PRUEBA")
    assert itt.sitio_web == "https://itt.example.org/"
    assert tv.logo_ruta == "perfiles/tresvizo/logo.png" and perfiles.ruta_imagen(tv, "logo")
    assert tv.textos_reporte == {"header_title": "Informe"}                        # el rótulo desconocido se omite
    assert tv.textos_cotizacion_es == {"sincerely": "Cordialmente"} and tv.textos_cotizacion_en == {"sincerely": "Kind regards"}
    assert tv.paleta["primary"] == "#112233"
    assert any("desconocidos" in a for a in r.avisos)
    prefs = preferencias.obtener(7)
    assert (prefs["geospatial"]["utm_zone"], prefs["reports"]["include_map"]) == (12, False)
    assert "cloud" not in prefs                                                      # Drive se reemplaza por la API de Drive
    assert diccionarios.palabras_personales(7) == {"es": ["mojonera", "zorplón"], "en": ["benchmark"]}
    assert sorted(Machote.objects.filter(cuenta_id=7).values_list("nombre", flat=True)) == ["Inspección", "Survey"]
    assert Perfil.objects.get(clave="grupo_itt").textos_reporte == {}
    assert r.aplicado["machotes"] == 2 and r.aplicado["diccionario"] == 3


def test_usuario_sin_permiso_no_cambia_perfiles(tmp_path):
    paquete = importacion.leer(zip_de_escritorio(tmp_path))
    with pytest.raises(importacion.ImportacionInvalida, match="administrador"):
        importacion.aplicar(paquete, 7, {"identidad", "diccionario"}, es_admin=False)
    assert Perfil.objects.get(clave="tresvizo").telefono == "" and diccionarios.palabras_personales(7)["es"] == []
    r = importacion.aplicar(paquete, 7, {"diccionario", "preferencias", "machotes"}, es_admin=False)
    assert set(r.aplicado) == {"diccionario", "preferencias", "machotes"}


def test_importar_dos_veces_no_duplica(tmp_path):
    paquete = importacion.leer(zip_de_escritorio(tmp_path))
    importacion.aplicar(paquete, 7, {"machotes", "diccionario"}, es_admin=False)
    r = importacion.aplicar(paquete, 7, {"machotes", "diccionario"}, es_admin=False)
    assert Machote.objects.filter(cuenta_id=7).count() == 2
    assert r.aplicado == {"machotes": 0, "diccionario": 0} and "ya existían" in r.omitido[0]


def test_todo_o_nada_si_algo_falla(tmp_path):
    malo = ajustes()
    malo["documents"]["quote_palette"] = {"primary": "azul"}
    paquete = importacion.leer(zip_de_escritorio(tmp_path, settings=malo))
    with pytest.raises(importacion.ImportacionInvalida, match="Paleta"):
        importacion.aplicar(paquete, 7, {"identidad", "paleta", "diccionario"}, es_admin=True)
    assert Perfil.objects.get(clave="tresvizo").nombre != "TresVizo MX"
    assert diccionarios.palabras_personales(7)["es"] == []


def test_destino_de_textos_y_paleta(tmp_path):
    paquete = importacion.leer(zip_de_escritorio(tmp_path))
    itt = Perfil.objects.get(clave="grupo_itt")
    importacion.aplicar(paquete, 7, {"textos", "paleta"}, es_admin=True, perfil_destino=itt)
    assert itt.__class__.objects.get(clave="grupo_itt").paleta["primary"] == "#112233"
    assert Perfil.objects.get(clave="tresvizo").paleta == {}


@pytest.mark.parametrize("construir, mensaje", [
    (lambda t: b"esto no es un zip", "ZIP válido"),
    (lambda t: zip_de_escritorio(t, settings=False, diccionario=False, machotes=False, logo_bytes=False), "ninguna configuración"),
    (lambda t: zip_de_escritorio(t, settings={"version": 99}), "más reciente"),
    (lambda t: zip_de_escritorio(t, extra={"../fuera.txt": "x"}), "Ruta no permitida"),
    (lambda t: zip_de_escritorio(t, extra={"/abs.txt": "x"}), "Ruta no permitida"),
    (lambda t: zip_de_escritorio(t, settings=False, extra={"Configuracion/settings.json": "{no es json"}), "JSON válido"),
])
def test_zips_invalidos(tmp_path, construir, mensaje):
    with pytest.raises(importacion.ImportacionInvalida, match=mensaje):
        importacion.leer(construir(tmp_path))


def test_zip_demasiado_grande(tmp_path):
    with pytest.raises(importacion.ImportacionInvalida, match="25 MB"):
        importacion.leer(b"0" * (importacion.MAX_ZIP_BYTES + 1))


def test_logo_invalido_no_rompe_la_importacion(tmp_path):
    paquete = importacion.leer(zip_de_escritorio(tmp_path, extra={"Configuracion/logo_propio.png": b"no es imagen"}, logo_bytes=False))
    r = importacion.aplicar(paquete, 7, {"logo", "identidad"}, es_admin=True)
    assert any("Logo de tresvizo" in a for a in r.avisos) and Perfil.objects.get(clave="tresvizo").logo_ruta == ""
    assert Perfil.objects.get(clave="tresvizo").nombre == "TresVizo MX"


def test_formato_antiguo_del_diccionario(tmp_path):
    z = zip_de_escritorio(tmp_path, settings=False, machotes=False, logo_bytes=False, diccionario=False,
                          extra={"Diccionarios/diccionario_personal.json": json.dumps(["alfa", "beta"])})
    paquete = importacion.leer(z)
    importacion.aplicar(paquete, 3, {"diccionario"}, es_admin=False)
    assert diccionarios.palabras_personales(3) == {"es": ["alfa", "beta"], "en": ["alfa", "beta"]}
