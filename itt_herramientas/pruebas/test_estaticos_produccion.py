"""Estáticos como en producción: `collectstatic` con nombres con huella (ManifestStaticFilesStorage, igual que `tvportal.estaticos`).

En desarrollo los nombres no llevan huella y una URL deducida en JS («maplibre-gl-csp.js» → «…-worker.js») funciona por casualidad;
en producción no existe y el mapa del convertidor y el de los reportes se rompían. Aquí cada URL de estático de las páginas (incluidas
las de los atributos data-*) debe apuntar a un archivo con huella que exista, y ningún script debe deducir una de otra.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage
from django.core.management import call_command

from .conftest import url
from .test_convertidor import listo

pytestmark = pytest.mark.django_db

ESTATICOS = Path(__file__).resolve().parents[1] / "static" / "herramientas"


class AlmacenDePrueba(ManifestStaticFilesStorage):
    """Copia de `tvportal.estaticos.AlmacenDeEstaticos` (el armazón vive en otro repositorio)."""
    manifest_strict = False
    patterns = tuple((tipo, tuple(p for p in reglas if "sourceMappingURL" not in (p[0] if isinstance(p, tuple) else p)))
                     for tipo, reglas in ManifestStaticFilesStorage.patterns)


@pytest.fixture
def produccion(settings, tmp_path):
    settings.STATIC_ROOT = tmp_path / "static"
    settings.STORAGES = {**settings.STORAGES, "staticfiles": {"BACKEND": f"{__name__}.AlmacenDePrueba"}}
    call_command("collectstatic", interactive=False, verbosity=0)
    return settings.STATIC_ROOT


def _urls_estaticas(html: str) -> set[str]:
    return set(re.findall(r'(?:src|href|data-[\w-]+)="(/static/[^"?#]+)"', html))


def test_cada_url_estatica_de_las_paginas_existe_con_huella(produccion, cliente_de, crear_usuario, roles, entradas_geo):
    u = crear_usuario("todo")
    roles.conceder(u, "herramientas", "cotizaciones", "reportes", "croquis", "convertidor", "supervision")
    c = cliente_de(u)
    conversion = listo(c, entradas_geo["convertidor.dxf"])
    paginas = {n: c.get(url(n)).content.decode() for n in ("cotizaciones", "reportes", "croquis", "capturar", "instalar", "inicio", "supervision")}
    from itt_herramientas.reportes import servicio as rep

    reporte = rep.sincronizar(u, __import__("uuid").uuid4(), None, None, {"titulo": "Visita", "fotos": []}, "grupo_itt")["servidor_id"]
    paginas["supervision_reporte"] = c.get(url("supervision_reporte", pk=reporte)).content.decode()
    assert "supervision/supervision." in paginas["supervision"] and "supervision/supervision." in paginas["supervision_reporte"]
    paginas["convertidor_banco"] = c.get(url("convertidor_banco", pk=conversion.pk)).content.decode()
    vistos = set()
    for nombre, html in paginas.items():
        for u_ in _urls_estaticas(html):
            if u_.startswith("/static/herramientas/") or u_.startswith("/static/tvplataforma/"):
                assert re.search(r"\.[0-9a-f]{12}\.\w+$", u_), f"{nombre}: {u_} no lleva huella"
            assert (produccion / u_.removeprefix("/static/")).is_file(), f"{nombre}: {u_} no existe"
            vistos.add(u_)
    # Los workers que antes se deducían en JS ahora llegan de la plantilla, con huella.
    for parte in ("maplibre-gl-csp-worker.", "reducir.worker.", "offline/exif.", "pdf.worker.min."):
        assert any(parte in v for v in vistos), parte
    assert 'data-itt-maplibre-worker="/static/herramientas/vendor/maplibre/maplibre-gl-csp-worker.' in paginas["convertidor_banco"]
    assert 'data-maplibre-worker="/static/herramientas/vendor/maplibre/maplibre-gl-csp-worker.' in paginas["reportes"]
    assert 'data-itt-maplibre-worker="/static/herramientas/vendor/maplibre/maplibre-gl-csp-worker.' in paginas["croquis"]
    # Cada script con worker lleva su propio atributo: un `script[data-worker]` genérico tomaba el del reductor de fotos en el croquis.
    for html in paginas.values():
        assert "data-worker=" not in html


def test_el_service_worker_precarga_los_nombres_con_huella(produccion):
    from itt_herramientas.offline import sw

    for ruta in re.findall(r'"(/static/[^"]+)"', sw.contenido()):
        assert (produccion / ruta.removeprefix("/static/")).is_file(), ruta


def test_ningun_script_deduce_una_url_de_estatico_de_otra():
    """La clase de error que rompió el mapa en producción: selectores `src*=` o `.replace()` sobre nombres de archivos estáticos."""
    malos = re.compile(r"""src\*=|\.replace\(\s*["'][\w.-]+\.(?:js|mjs|css)["']|new URL\(\s*["'][\w.-]+\.(?:js|mjs)["']|importScripts\(\s*["']""")
    for js in ESTATICOS.rglob("*.js"):
        if "vendor" in js.parts:
            continue
        assert not malos.search(js.read_text(encoding="utf-8")), js.name
