"""Perfil corporativo activo: solo con el permiso «Cambiar perfil corporativo» (lo concede únicamente el propietario)."""
from __future__ import annotations

import pytest

from itt_herramientas.comun import perfiles
from itt_herramientas.dominio import branding
from itt_herramientas.models import Perfil, PerfilActivo

from .conftest import url

pytestmark = pytest.mark.django_db


@pytest.fixture
def con_permiso(crear_usuario, roles):
    u = crear_usuario("cambia")
    roles.conceder(u, "herramientas", "cotizaciones", "cambiar_perfil")
    return u


@pytest.fixture
def otra_con_permiso(crear_usuario, roles):
    u = crear_usuario("cambia2")
    roles.conceder(u, "herramientas", "reportes", "cambiar_perfil")
    return u


def test_el_permiso_se_declara_como_rol_de_herramientas():
    from itt_herramientas.modulo import MODULO

    rol = MODULO.rol("cambiar_perfil")
    assert rol.nombre == "Cambiar perfil corporativo"


def test_sin_el_permiso_no_hay_selector_ni_pistas(cliente_de, usuaria):
    html = cliente_de(usuaria).get(url("inicio")).content.decode()
    assert 'data-perfil="grupo_itt"' in html and "Herramientas Grupo ITT" in html
    for pista in ("perfil-activo", "Perfil activo", "Cambiar perfil", "Herramientas TresVizo", "www.tresvizo.com", "?p=tresvizo", "itt-marca__cambio"):
        assert pista not in html, pista


def test_post_sin_permiso_da_403_aunque_se_fuerce_la_url(cliente_de, usuaria, administradora):
    for u in (usuaria, administradora):
        c = cliente_de(u)
        assert c.post(url("perfil_activo"), {"perfil": "tresvizo"}).status_code == 403
        assert c.post(url("perfil_activo"), {"perfil": "tresvizo", "siguiente": "/"}).status_code == 403
        assert not PerfilActivo.objects.filter(cuenta_id=u.pk).exists()
    assert cliente_de(usuaria).get(url("perfil_activo")).status_code == 405
    assert "Herramientas TresVizo" not in cliente_de(usuaria).get(url("inicio") + "?perfil=tresvizo").content.decode()      # un parámetro en la URL no cambia nada


def test_el_selector_muestra_el_perfil_activo_y_cambia(cliente_de, con_permiso):
    c = cliente_de(con_permiso)
    html = c.get(url("inicio")).content.decode()
    assert "Perfil activo: Grupo ITT" in html and 'aria-pressed="true" disabled>Grupo ITT' in html
    r = c.post(url("perfil_activo"), {"perfil": "tresvizo", "siguiente": url("preferencias")})
    assert r.status_code == 302 and r["Location"] == url("preferencias")
    html = c.get(url("preferencias")).content.decode()
    assert 'data-perfil="tresvizo"' in html and "Herramientas TresVizo" in html and "Perfil activo: TresVizo" in html
    assert "www.tresvizo.com" in html and 'href="https://www.tresvizo.com/"' in html
    assert 'href="/manifest.webmanifest?p=tresvizo"' in html and "apple-touch-icon-tresvizo.png" in html
    assert c.post(url("perfil_activo"), {"perfil": "grupo_itt"}).status_code == 302
    assert 'data-perfil="grupo_itt"' in c.get(url("inicio")).content.decode()          # apagarlo restaura Grupo ITT
    assert c.post(url("perfil_activo"), {"perfil": "otro"}).status_code == 400


def test_la_redireccion_no_sale_del_sitio(cliente_de, con_permiso):
    c = cliente_de(con_permiso)
    for malo in ("https://malo.example/", "//malo.example/", "javascript:alert(1)"):
        assert c.post(url("perfil_activo"), {"perfil": "tresvizo", "siguiente": malo})["Location"] == url("inicio")


def test_la_eleccion_se_recuerda_entre_sesiones(cliente_de, con_permiso):
    cliente_de(con_permiso).post(url("perfil_activo"), {"perfil": "tresvizo"})
    assert 'data-perfil="tresvizo"' in cliente_de(con_permiso).get(url("inicio")).content.decode()        # otro navegador, misma cuenta


def test_dos_cuentas_a_la_vez_no_se_pisan(cliente_de, con_permiso, otra_con_permiso, usuaria):
    a, b, c = cliente_de(con_permiso), cliente_de(otra_con_permiso), cliente_de(usuaria)
    a.post(url("perfil_activo"), {"perfil": "tresvizo"})
    assert 'data-perfil="tresvizo"' in a.get(url("inicio")).content.decode()
    assert 'data-perfil="grupo_itt"' in b.get(url("inicio")).content.decode()
    assert 'data-perfil="grupo_itt"' in c.get(url("inicio")).content.decode()
    b.post(url("perfil_activo"), {"perfil": "tresvizo"})
    a.post(url("perfil_activo"), {"perfil": "grupo_itt"})
    assert 'data-perfil="grupo_itt"' in a.get(url("inicio")).content.decode()
    assert 'data-perfil="tresvizo"' in b.get(url("inicio")).content.decode()
    assert perfiles.perfil_activo(con_permiso).clave == "grupo_itt" and perfiles.perfil_activo(otra_con_permiso).clave == "tresvizo"


def test_logo_y_paleta_siguen_al_perfil_activo(cliente_de, con_permiso, usuaria):
    tv = Perfil.objects.get(clave="tresvizo")
    tv.paleta = {"primary": "#176B4D", "accent": "#207A58"}
    tv.save()
    c = cliente_de(con_permiso)
    itt_logo = branding.ITT_LOGO.read_bytes()
    tv_logo = branding.TRESVIZO_LOGO.read_bytes()
    assert b"".join(c.get(url("perfil_logo")).streaming_content) == itt_logo
    assert "paleta de fábrica" in c.get(url("perfil_css")).content.decode()
    c.post(url("perfil_activo"), {"perfil": "tresvizo"})
    assert b"".join(c.get(url("perfil_logo")).streaming_content) == tv_logo
    css = c.get(url("perfil_css")).content.decode()
    assert "--tv-primario: #176B4D;" in css and "--tv-acento: #207A58;" in css
    otra = cliente_de(usuaria)                                                              # quien no tiene el permiso sigue viendo Grupo ITT
    assert b"".join(otra.get(url("perfil_logo")).streaming_content) == itt_logo and "paleta de fábrica" in otra.get(url("perfil_css")).content.decode()


def test_un_logo_propio_del_perfil_se_ve_al_activarlo(cliente_de, con_permiso):
    import io

    from PIL import Image

    b = io.BytesIO(); Image.new("RGB", (30, 10), "#FF00FF").save(b, "PNG")
    tv = Perfil.objects.get(clave="tresvizo")
    perfiles.guardar_imagen(tv, "logo", b.getvalue())
    tv.save()
    c = cliente_de(con_permiso)
    c.post(url("perfil_activo"), {"perfil": "tresvizo"})
    assert b"".join(c.get(url("perfil_logo")).streaming_content) == b.getvalue()


def test_manifiesto_por_perfil(client):
    from itt_herramientas.offline.sw import ESTATICOS as sw_estaticos

    from .conftest import HOST

    itt = client.get(url("manifiesto"), HTTP_HOST=HOST).json()
    tv = client.get(url("manifiesto") + "?p=tresvizo", HTTP_HOST=HOST).json()
    malo = client.get(url("manifiesto") + "?p=../etc", HTTP_HOST=HOST).json()
    assert itt["name"] == "Herramientas Grupo ITT" and tv["name"] == "Herramientas TresVizo" and malo["name"] == itt["name"]
    assert all("tresvizo" in i["src"] for i in tv["icons"]) and all("grupo_itt" in i["src"] for i in itt["icons"])
    assert tv["scope"] == tv["start_url"] == "/"
    for m in (itt, tv):
        for icono in m["icons"]:
            ruta = sw_estaticos / icono["src"].removeprefix("/static/herramientas/")
            assert ruta.is_file(), ruta


def test_el_perfil_tresvizo_produce_lo_mismo_que_el_escritorio(personales_escritorio):
    """Con los datos de fábrica del escritorio cargados en el perfil TresVizo, la identidad y la firma de cotizaciones coinciden."""
    datos = personales_escritorio["perfiles"]["tresvizo"]
    tv = Perfil.objects.get(clave="tresvizo")
    tv.telefono, tv.direccion, tv.correo, tv.firmante = datos["phone"], datos["address"], datos["email"], datos["signer"]
    cot = personales_escritorio["cotizacion"]
    tv.banco, tv.clabe, tv.cuenta = cot["bank"], cot["clabe"], cot["account"]
    with perfiles.activar(tv):
        activo = branding.active_profile()
        assert (activo.key, activo.phone, activo.address, activo.signer) == ("tresvizo", datos["phone"], datos["address"], datos["signer"])
        assert activo.document_heading == "TresVizo Ingeniería" and activo.website == "https://www.tresvizo.com/"
        assert activo.logo_path == branding.TRESVIZO_LOGO
    q = perfiles.nueva_cotizacion(tv)
    assert q.prepared_by == datos["signer"] and (q.bank, q.clabe, q.account) == (cot["bank"], cot["clabe"], cot["account"])
