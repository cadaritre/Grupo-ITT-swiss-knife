from __future__ import annotations

import inspect
import json

import pytest

from itt_herramientas.dominio.quotation_models import QuoteItem
from itt_herramientas.dominio.quotation_templates import CustomConceptTemplateStore


def test_la_coleccion_exige_una_ruta_explicita():
    assert "path" in inspect.signature(CustomConceptTemplateStore).parameters
    with pytest.raises(TypeError):
        CustomConceptTemplateStore()  # type: ignore[call-arg]


def test_machotes_se_guardan_y_recargan(tmp_path):
    ruta = tmp_path / "cuenta" / "machotes.json"
    tienda = CustomConceptTemplateStore(ruta)
    assert tienda.templates == []
    guardado = tienda.save_item("Levantamiento", QuoteItem(title="Levantamiento", description="Levantamiento topográfico", quantity=1, unit="Lote", unit_price=1500), "es")
    assert ruta.is_file()
    nueva = CustomConceptTemplateStore(ruta)
    assert [t.name for t in nueva.templates] == ["Levantamiento"]
    assert json.loads(ruta.read_text(encoding="utf-8"))["tool"]
    assert guardado.template_id == nueva.templates[0].template_id
