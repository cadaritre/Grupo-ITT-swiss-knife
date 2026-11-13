"""Control final contra los archivos dorados generados en Windows (`golden_windows.py`).

Los dorados son las salidas del escritorio real de Windows (Arial, su ReportLab). Las salidas de la web usan Liberation
Sans (métricamente igual a Arial) y los mismos datos de entrada, que viajan dentro del .zip. Se exige el mismo contenido:
igualdad exacta donde no interviene la tipografía y «tolerante» (mismo texto y apariencia) donde sí.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

import casos
import comparadores as C
from paridad import ejecutor

DORADOS = sorted((ejecutor.AQUI / "dorados").glob("golden_windows_*.zip"))

pytestmark = [pytest.mark.paridad,
              pytest.mark.skipif(not DORADOS, reason="Aún no hay archivos dorados: el propietario corre paridad/golden_windows.py en Windows y deja el .zip en paridad/dorados/")]


@pytest.fixture(scope="module")
def dorados(tmp_path_factory):
    trabajo = tmp_path_factory.mktemp("dorados")
    with zipfile.ZipFile(DORADOS[-1]) as zf:
        zf.extractall(trabajo / "zip")
    web = ejecutor.correr_lado("web", ejecutor.RAIZ, trabajo / "zip" / "entradas", trabajo / "salida_web")
    return {"web": web, "windows": trabajo / "zip" / "escritorio", "manifiesto": json.loads((trabajo / "zip" / "manifiesto.json").read_text(encoding="utf-8"))}


@pytest.mark.parametrize("caso", casos.claves())
def test_caso_igual_al_dorado_de_windows(dorados, caso):
    resultados = ejecutor.comparar_caso(caso, dorados["web"], dorados["windows"], tolerante=True)
    malos = [f"{n}: {r.motivo}" for n, r in resultados if not r.ok]
    assert not malos, "\n".join(malos)


def test_los_dorados_se_hicieron_con_arial():
    manifiesto = json.loads(zipfile.ZipFile(DORADOS[-1]).read("manifiesto.json"))
    assert manifiesto["sistema"].lower().startswith("windows")
    assert manifiesto["arial_instalada"], "los dorados deben generarse en una PC con Arial instalada"
