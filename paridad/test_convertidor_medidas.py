"""El convertidor de la web contra el del escritorio (rama main) sobre archivos de muestra: debe reducir vértices y bytes donde hay
curvas, no agregar nada a las rectas y nunca producir un archivo más grande."""
from __future__ import annotations

import os

import pytest

from paridad import ejecutor, medir_convertidor

pytestmark = pytest.mark.paridad


@pytest.fixture(scope="module")
def datos(tmp_path_factory):
    try:
        return medir_convertidor.medir(os.environ.get("ITT_PARIDAD_REF", "main"), tmp_path_factory.mktemp("medir"))
    except ejecutor.ParidadError as exc:
        pytest.skip(str(exc))


def test_nunca_mas_grande_que_el_escritorio(datos):
    for nombre, antes in datos["escritorio"].items():
        despues = datos["web"][nombre]
        assert despues["elementos"] == antes["elementos"], nombre
        assert despues["vertices"] <= antes["vertices"], nombre
        assert despues["bytes"] < antes["bytes"], nombre


def test_las_curvas_bajan_y_las_rectas_no_cambian(datos):
    e, w = datos["escritorio"], datos["web"]
    assert w["curvas_de_nivel.dxf"]["vertices"] < e["curvas_de_nivel.dxf"]["vertices"] * 0.30
    assert w["planta_construccion.dxf"]["vertices"] < e["planta_construccion.dxf"]["vertices"] * 0.45
    assert w["levantamiento_topografico.dxf"]["vertices"] == e["levantamiento_topografico.dxf"]["vertices"]
    assert w["predios.kml"]["vertices"] == e["predios.kml"]["vertices"]
