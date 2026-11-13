from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent))

from paridad import ejecutor  # noqa: E402


@pytest.fixture(scope="session")
def corrida(tmp_path_factory):
    """Una sola corrida de los dos lados por sesión de pruebas (tarda ~40 s)."""
    ref = os.environ.get("ITT_PARIDAD_REF", "main")
    try:
        return ejecutor.correr_ambos(ref, tmp_path_factory.mktemp("paridad"))
    except ejecutor.ParidadError as exc:
        if "No existe la referencia" in str(exc):
            pytest.skip(f"{exc}. Fija ITT_PARIDAD_REF o trabaja en un clon que tenga la rama del escritorio.")
        raise
