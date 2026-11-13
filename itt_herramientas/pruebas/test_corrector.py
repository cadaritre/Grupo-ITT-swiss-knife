from __future__ import annotations

from itt_herramientas.dominio.spellcheck import BilingualSpellService, ReviewField


def _campo(texto):
    resultado = {}
    return ReviewField("Descripción", texto, lambda nuevo: resultado.setdefault("v", nuevo)), resultado


def test_corrector_en_memoria_no_escribe_en_disco(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    servicio = BilingualSpellService("es")
    campo, _ = _campo("Levantamiento topográfico con ortomosaico y pared de blokes")
    _, problemas = servicio.scan([campo])
    assert [p.word for p in problemas] == ["blokes"]
    assert list(tmp_path.iterdir()) == []


def test_palabra_personal_se_recuerda_y_se_exporta():
    servicio = BilingualSpellService("es")
    servicio.add_word("Mojonera2")
    assert "mojonera2" in servicio.palabras_personales_json()["es"]
    otro = BilingualSpellService("es", palabras_personales=servicio.palabras_personales_json())
    assert otro.is_known("mojonera2")


def test_diccionario_personal_en_carpeta_como_el_escritorio(tmp_path):
    servicio = BilingualSpellService("en", directorio=tmp_path)
    servicio.add_word("tresvizo")
    assert (tmp_path / "diccionario_personal.json").is_file()
    assert BilingualSpellService("en", directorio=tmp_path).is_known("tresvizo")


def test_sugerencias_respetan_mayusculas():
    servicio = BilingualSpellService("es")
    sugerencias = servicio.suggestions("Topografia")
    assert sugerencias and sugerencias[0] == "Topografía"
