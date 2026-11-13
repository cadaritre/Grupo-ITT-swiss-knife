"""Paridad web contra escritorio (rama `main`): mismas entradas, mismas salidas, bytes iguales en macOS/Linux."""
from __future__ import annotations

import hashlib
import json

import pytest

import casos
from paridad import ejecutor

pytestmark = pytest.mark.paridad


@pytest.mark.parametrize("caso", sorted(casos.CASOS))
def test_caso_igual_al_escritorio(corrida, caso):
    """Con Liberation Sans en los dos lados, los PDF coinciden byte a byte (ReportLab en modo invariante)."""
    resultados = ejecutor.comparar_caso(caso, corrida.web, corrida.escritorio, tolerante=False)
    assert resultados, f"el caso {caso} no produjo nada"
    malos = [f"{nombre}: {r.motivo}" for nombre, r in resultados if not r.ok]
    assert not malos, "\n".join(malos)
    inexactos = [nombre for nombre, r in resultados if r.nivel != "exacto"]
    assert not inexactos, f"se esperaba igualdad exacta en: {inexactos}"


@pytest.mark.parametrize("caso", sorted(casos.CASOS))
def test_ningun_caso_falla_por_error_del_dominio(corrida, caso):
    """Un caso que lanza una excepción en uno de los lados sería una paridad vacía: aquí ninguno debe fallar."""
    for lado in (corrida.web, corrida.escritorio):
        resultado = json.loads((lado / "resultados.json").read_text(encoding="utf-8"))[caso]
        assert resultado["error"] is None, resultado.get("traza") or resultado["error"]


def test_los_logos_de_la_web_son_los_del_escritorio(corrida):
    """La paridad usa los logos de las entradas en los dos lados; aquí se comprueba que los empaquetados son los mismos."""
    activos = ejecutor.RAIZ / "itt_herramientas" / "dominio" / "activos"
    for web, escritorio in (("logo.png", "logo_grupo_itt.png"), ("logo_tresvizo.png", "logo_tresvizo.png")):
        assert hashlib.sha256((activos / web).read_bytes()).digest() == hashlib.sha256((corrida.entradas / escritorio).read_bytes()).digest()


def test_cobertura_de_casos_del_dominio_movido():
    """Cada módulo de lógica movido a `dominio` aparece en al menos un caso (se detecta por su nombre)."""
    fuente = (ejecutor.AQUI / "casos.py").read_text(encoding="utf-8")
    sin_caso = {"almacenamiento": "no existe en el escritorio con esa forma (ver test_almacenamiento)", "fuentes": "nuevo en la web"}
    for archivo in sorted((ejecutor.RAIZ / "itt_herramientas" / "dominio").glob("*.py")):
        nombre = archivo.stem
        if nombre == "__init__" or nombre in sin_caso:
            continue
        assert f'"{nombre}"' in fuente, f"ningún caso de paridad usa el módulo {nombre}"
