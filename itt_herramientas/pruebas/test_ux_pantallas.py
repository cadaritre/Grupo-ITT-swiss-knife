"""Auditoría UX 14-11-2026: pantallas del servidor (agrupación de formularios, números y ayudas)."""
from __future__ import annotations

import re

import pytest

from itt_herramientas.models import Perfil

from .conftest import url
from .test_triangulacion import topografa  # noqa: F401  (fixture)

pytestmark = pytest.mark.django_db


def test_preferencias_se_agrupan_por_herramienta_y_la_zona_es_una_lista(cliente_de, usuaria):
    html = cliente_de(usuaria).get(url("preferencias")).content.decode()
    for titulo in ("Reportes fotográficos", "Convertidor DXF ↔ KML/KMZ", "Croquis de ubicación", "Triangulación"):
        assert f"<legend>{titulo}</legend>" in html
    zona = re.search(r'<select[^>]*name="geospatial_utm_zone".*?</select>', html, re.S).group(0)
    assert zona.count("<option") == 60
    r = cliente_de(usuaria).post(url("preferencias"), {"geospatial_utm_zone": "99", "geospatial_hemisphere": "N", "geospatial_map_layer": "OpenStreetMap",
                                                       "sketches_map_layer": "OpenStreetMap", "sketches_contour_interval": "10"})
    assert r.status_code == 200                                              # una zona fuera de 1-60 no se acepta


def test_perfil_agrupa_los_campos_con_acentos_y_vista_del_logo(cliente_de, administradora):
    cl = cliente_de(administradora)
    html = cl.get(url("perfil", clave="grupo_itt")).content.decode()
    for titulo in ("Identidad", "Contacto", "Firma y pie de los documentos", "Datos bancarios", "Imágenes"):
        assert f"<legend>{titulo}</legend>" in html
    assert "Descripción" in html and "Descripcion" not in html
    assert url("perfil_logo_vista", clave="grupo_itt") in html
    assert cl.get(url("perfil_logo_vista", clave="grupo_itt"))["Content-Type"].startswith("image/")


def test_quitar_la_firma_de_un_perfil(cliente_de, administradora):
    import io

    from PIL import Image

    from itt_herramientas.comun import perfiles

    p = Perfil.objects.get(clave="grupo_itt")
    b = io.BytesIO()
    Image.new("RGB", (20, 10), "#123456").save(b, "PNG")
    perfiles.guardar_imagen(p, "firma", b.getvalue())
    p.save()
    assert perfiles.ruta_imagen(p, "firma") is not None
    campos = {f.name: getattr(p, f.name) for f in Perfil._meta.fields if f.name in ("nombre", "encabezado", "encabezado_en", "descripcion", "descripcion_en", "sitio_web", "telefono",
              "etiqueta_telefono", "etiqueta_telefono_en", "direccion", "correo", "firmante", "pie", "pie_en", "firmante_reporte", "cedula_profesional", "banco", "clabe", "cuenta")}
    r = cliente_de(administradora).post(url("perfil", clave="grupo_itt"), {**campos, "quitar_firma": "on"})
    assert r.status_code == 302
    p.refresh_from_db()
    assert perfiles.ruta_imagen(p, "firma") is None and not p.firma_ruta


def test_el_inicio_lleva_la_identidad_y_las_demas_paginas_no(cliente_de, administradora):
    cl = cliente_de(administradora)
    assert 'class="itt-marca"' in cl.get(url("inicio")).content.decode()
    for nombre in ("preferencias", "drive", "perfiles"):
        assert 'class="itt-marca"' not in cl.get(url(nombre)).content.decode(), nombre


def test_la_banda_de_pendientes_solo_esta_a_la_vista_en_el_inicio_y_las_paginas_de_campo(cliente_de, administradora):
    cl = cliente_de(administradora)
    assert 'data-banda="si"' in cl.get(url("inicio")).content.decode()
    assert 'data-banda="no"' in cl.get(url("preferencias")).content.decode()
    assert 'data-banda="si"' in cl.get(url("capturar")).content.decode()


def test_numeros_con_separador_de_miles_y_sin_notacion_cientifica(cliente_de, topografa):
    from .test_triangulacion import subir

    cl = cliente_de(topografa)
    html = cl.get(url("triangulacion")).content.decode()
    assert "75,000" in html and "75000" not in html
