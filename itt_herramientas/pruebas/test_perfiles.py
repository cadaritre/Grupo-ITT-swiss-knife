from __future__ import annotations

import io
from datetime import date

import pytest
from PIL import Image

from itt_herramientas.comun import perfiles
from itt_herramientas.dominio import branding
from itt_herramientas.dominio.almacenamiento import SETTINGS, ajustes_activos
from itt_herramientas.models import AccesoPerfil, Perfil

pytestmark = pytest.mark.django_db


def png(tamano=(40, 30), formato="PNG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", tamano, "#336699").save(buffer, formato)
    return buffer.getvalue()


def test_los_dos_perfiles_nacen_con_la_marca_y_sin_datos_personales():
    ps = {p.clave: p for p in Perfil.objects.all()}
    assert set(ps) == {"grupo_itt", "tresvizo"}
    for p in ps.values():
        assert p.nombre and p.encabezado and p.sitio_web
        assert (p.telefono, p.direccion, p.correo, p.firmante, p.firmante_reporte, p.cedula_profesional, p.banco, p.clabe, p.cuenta) == ("",) * 9
    assert ps["tresvizo"].encabezado == "TresVizo Ingeniería"


def test_sembrar_no_pisa_lo_editado():
    p = Perfil.objects.get(clave="grupo_itt")
    p.telefono = "555 000 0000"
    p.save()
    perfiles.sembrar_perfiles()
    assert Perfil.objects.get(clave="grupo_itt").telefono == "555 000 0000"


def test_ajustes_de_perfil_llegan_al_dominio():
    p = Perfil.objects.get(clave="tresvizo")
    p.telefono, p.firmante, p.direccion = "55 5555 0000", "ING. PERSONA PRUEBA", "Calle Falsa 123"
    p.paleta = {"primary": "#112233"}
    p.textos_reporte = {"header_title": "Informe"}
    p.save()
    with perfiles.activar(p):
        activo = branding.active_profile()
        assert (activo.key, activo.phone, activo.signer, activo.address) == ("tresvizo", "55 5555 0000", "ING. PERSONA PRUEBA", "Calle Falsa 123")
        assert SETTINGS.get("documents.quote_palette.primary") == "#112233"
        assert SETTINGS.get("documents.report_texts.header_title") == "Informe"
    assert branding.active_profile().key == "grupo_itt" and branding.active_profile().phone == ""


def test_el_dominio_ya_no_trae_datos_personales_de_fabrica():
    from itt_herramientas.dominio.pdf_generator import ReportOptions
    from itt_herramientas.dominio.quotation_models import QuoteData

    q = QuoteData()
    assert (q.bank, q.clabe, q.account, q.prepared_by) == ("", "", "", "")
    campos = ReportOptions.__dataclass_fields__
    assert campos["signer"].default == "" and campos["professional_license"].default == ""
    for clave, valores in branding.PROFILE_DEFAULTS.items():
        assert (valores["phone"], valores["address"], valores["email"], valores["signer"]) == ("", "", "", ""), clave


def test_nueva_cotizacion_usa_los_datos_del_perfil():
    p = Perfil.objects.get(clave="grupo_itt")
    p.banco, p.clabe, p.cuenta, p.firmante = "BANCO DE PRUEBA", "012345678901234567", "0011223344", "ING. PERSONA PRUEBA"
    q = perfiles.nueva_cotizacion(p, client_name="Cliente")
    assert (q.bank, q.clabe, q.account, q.prepared_by, q.client_name) == ("BANCO DE PRUEBA", "012345678901234567", "0011223344", "ING. PERSONA PRUEBA", "Cliente")


def test_opciones_de_reporte_usan_firma_y_cedula_del_perfil(tmp_path):
    p = Perfil.objects.get(clave="grupo_itt")
    p.firmante_reporte, p.cedula_profesional = "ING. PERSONA PRUEBA", "9999999"
    perfiles.guardar_imagen(p, "firma", png())
    perfiles.guardar_imagen(p, "cedula", png(formato="JPEG"))
    opciones = perfiles.opciones_reporte(p, "Título", date(2026, 1, 2), [], tmp_path / "r.pdf", incluir_credenciales=True)
    assert (opciones.signer, opciones.professional_license) == ("ING. PERSONA PRUEBA", "9999999")
    assert opciones.signature.name == "firma.png" and opciones.license_card.name == "cedula.jpg"
    assert opciones.include_credentials and opciones.company == "INGENIERÍA TÉCNICA Y TOPOGRÁFICA"


def test_imagenes_se_validan_por_contenido():
    p = Perfil.objects.get(clave="grupo_itt")
    for malo in (b"", b"no soy una imagen", b"GIF89a" + b"\x00" * 50, b"x" * (perfiles.MAX_IMAGEN_BYTES + 1)):
        with pytest.raises(perfiles.PerfilInvalido):
            perfiles.guardar_imagen(p, "logo", malo)
    gif = io.BytesIO()
    Image.new("P", (4, 4)).save(gif, "GIF")
    with pytest.raises(perfiles.PerfilInvalido, match="PNG o JPEG"):
        perfiles.guardar_imagen(p, "logo", gif.getvalue())
    ruta = perfiles.guardar_imagen(p, "logo", png())
    assert ruta.is_file() and p.logo_ruta == "perfiles/grupo_itt/logo.png"
    assert perfiles.ruta_imagen(p, "logo") == ruta.resolve() or perfiles.ruta_imagen(p, "logo") == ruta
    with perfiles.activar(p):
        assert branding.active_profile().logo_path.name == "logo.png"
        assert "perfiles" in str(branding.active_profile().logo_path)


def test_ruta_de_imagen_no_sale_de_la_carpeta():
    p = Perfil.objects.get(clave="grupo_itt")
    p.logo_ruta = "../../etc/passwd"
    assert perfiles.ruta_imagen(p, "logo") is None


def test_validar_textos():
    perfiles.validar_textos({"header_title": "x"}, {"sincerely": "Cordialmente"}, {})
    for reporte, es in (({"no_existe": "x"}, {}), ({"header_title": 3}, {}), ({"months": "a|b"}, {}), ({}, {"no_existe": "x"})):
        with pytest.raises(perfiles.PerfilInvalido):
            perfiles.validar_textos(reporte, es, {})


def test_paleta_y_contraste():
    assert perfiles.contraste("#000000", "#FFFFFF") == 21.0
    assert perfiles.verificar_paleta({"primary": "#07356F", "bar_text": "#FFFFFF"}) == []
    avisos = perfiles.verificar_paleta({"primary": "#FFFF00", "bar_text": "#FFFFFF"})
    assert avisos and "barras" in avisos[0]
    with pytest.raises(perfiles.PerfilInvalido):
        perfiles.verificar_paleta({"primary": "azul"})
    with pytest.raises(perfiles.PerfilInvalido):
        perfiles.verificar_paleta({"fondo": "#FFFFFF"})


def test_perfiles_que_puede_usar_cada_cuenta(usuaria, administradora, propietario, crear_usuario):
    itt, tv = Perfil.objects.get(clave="grupo_itt"), Perfil.objects.get(clave="tresvizo")
    assert perfiles.perfiles_de(usuaria) == []
    with pytest.raises(perfiles.PerfilInvalido, match="aún no tiene"):
        perfiles.perfil_para(usuaria, None)
    AccesoPerfil.objects.create(cuenta_id=usuaria.pk, perfil=tv)
    AccesoPerfil.objects.create(cuenta_id=usuaria.pk, perfil=itt, predeterminado=True)
    assert {p.clave for p in perfiles.perfiles_de(usuaria)} == {"grupo_itt", "tresvizo"}
    assert perfiles.perfil_para(usuaria, None).clave == "grupo_itt"
    assert perfiles.perfil_para(usuaria, "tresvizo").clave == "tresvizo"
    AccesoPerfil.objects.filter(cuenta_id=usuaria.pk, perfil=tv).delete()
    with pytest.raises(perfiles.PerfilInvalido, match="permiso"):
        perfiles.perfil_para(usuaria, "tresvizo")
    assert len(perfiles.perfiles_de(administradora)) == 2 and len(perfiles.perfiles_de(propietario)) == 2


def test_el_perfil_suministra_los_valores_de_fabrica_del_escritorio(personales_escritorio):
    """Paridad: con los datos de fábrica del escritorio cargados en los perfiles, la web produce lo mismo que el escritorio."""
    for clave, datos in personales_escritorio["perfiles"].items():
        p = Perfil.objects.get(clave=clave)
        p.telefono, p.direccion, p.correo, p.firmante = datos["phone"], datos["address"], datos["email"], datos["signer"]
        p.save()
        with perfiles.activar(p):
            a = branding.active_profile()
            assert (a.phone, a.address, a.email, a.signer) == (datos["phone"], datos["address"], datos["email"], datos["signer"])
    cot = personales_escritorio["cotizacion"]
    itt = Perfil.objects.get(clave="grupo_itt")
    itt.banco, itt.clabe, itt.cuenta = cot["bank"], cot["clabe"], cot["account"]
    itt.firmante_reporte, itt.cedula_profesional = personales_escritorio["reporte"]["signer"], personales_escritorio["reporte"]["professional_license"]
    q = perfiles.nueva_cotizacion(itt)
    assert (q.bank, q.clabe, q.account, q.prepared_by) == (cot["bank"], cot["clabe"], cot["account"], cot["prepared_by"])
    o = perfiles.opciones_reporte(itt, "t", date(2026, 1, 1), [], itt.__class__ and __import__("pathlib").Path("x.pdf"))
    assert (o.signer, o.professional_license) == (personales_escritorio["reporte"]["signer"], personales_escritorio["reporte"]["professional_license"])
