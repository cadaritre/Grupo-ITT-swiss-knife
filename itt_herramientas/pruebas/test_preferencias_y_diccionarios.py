from __future__ import annotations

import pytest

from itt_herramientas.comun import diccionarios, preferencias
from itt_herramientas.models import Preferencias

pytestmark = pytest.mark.django_db


def test_preferencias_de_fabrica_y_guardado():
    assert preferencias.obtener(1) == preferencias.de_fabrica()
    assert preferencias.obtener(1)["geospatial"]["utm_zone"] == 13
    guardado = preferencias.guardar(1, {"geospatial": {"utm_zone": 12, "hemisphere": "S"}, "sketches": {"contour_interval": "5 m"}})
    assert guardado["geospatial"]["utm_zone"] == 12 and guardado["geospatial"]["labels"] is True
    assert preferencias.obtener(2)["geospatial"]["utm_zone"] == 13           # por cuenta
    preferencias.guardar(1, {"geospatial": {"labels": False}})
    assert preferencias.obtener(1)["geospatial"] == {**preferencias.obtener(1)["geospatial"], "utm_zone": 12, "labels": False}


@pytest.mark.parametrize("malo", [
    {"geospatial": {"utm_zone": 61}}, {"geospatial": {"utm_zone": True}}, {"geospatial": {"hemisphere": "X"}},
    {"geospatial": {"map_layer": "Mapa inventado"}}, {"sketches": {"contour_interval": "7 m"}}, {"reports": {"include_map": "sí"}},
    {"otra": {"x": 1}}, {"reports": {"open_pdf": True}}, {"reports": [1]},
])
def test_preferencias_invalidas(malo):
    with pytest.raises(preferencias.PreferenciaInvalida):
        preferencias.guardar(1, malo)
    assert not Preferencias.objects.filter(cuenta_id=1).exists() or preferencias.obtener(1) == preferencias.de_fabrica()


def test_importacion_laxa_ignora_lo_que_no_conoce():
    assert preferencias.validar({"reports": {"open_pdf": True, "include_map": False}, "cloud": {"enabled": True}}, estricto=False) == {"reports": {"include_map": False}}


def test_diccionario_personal_por_cuenta_y_corrector():
    diccionarios.agregar_palabra(1, "Zorplón", "es")
    diccionarios.agregar_palabra(1, "Zorplón", "es")              # sin duplicados
    assert diccionarios.palabras_personales(1)["es"] == ["zorplón"]
    assert diccionarios.palabras_personales(2) == {"es": [], "en": []}
    assert diccionarios.corrector(1, "es").is_known("zorplón")
    assert not diccionarios.corrector(2, "es").is_known("zorplón")
    diccionarios.quitar_palabra(1, "zorplón", "es")
    assert not diccionarios.corrector(1, "es").is_known("zorplón")


@pytest.mark.parametrize("palabra", ["", "dos palabras", "x" * 61, "abc123", "<script>"])
def test_palabras_invalidas(palabra):
    with pytest.raises(diccionarios.PalabraInvalida):
        diccionarios.agregar_palabra(1, palabra, "es")


def test_diccionario_tecnico_global_se_suma_a_todos():
    diccionarios.agregar_termino("Fotomosaicox", "es", 9)
    diccionarios.agregar_termino("Fotomosaicox", "es", 9)
    from itt_herramientas.dominio.spellcheck import ReviewField

    def problemas(cuenta, idioma):
        _, issues = diccionarios.corrector(cuenta, idioma).scan([ReviewField("t", "fotomosaicox", lambda v: None)])
        return [i.word for i in issues]
    assert problemas(5, "es") == [] and problemas(6, "es") == []
    assert problemas(5, "en") == ["fotomosaicox"]               # el término era solo español
    diccionarios.agregar_termino("fotomosaicox", "ambos")
    assert problemas(5, "en") == []
    from itt_herramientas.models import TerminoTecnico
    assert TerminoTecnico.objects.filter(palabra="fotomosaicox").count() == 2
    for t in TerminoTecnico.objects.all():
        assert diccionarios.quitar_termino(t.pk)
    assert problemas(5, "es") == ["fotomosaicox"]
