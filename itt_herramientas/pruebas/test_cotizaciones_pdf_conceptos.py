"""Conceptos largos en el PDF de la cotización: cruzan de página sin «continuación» y sin repetir sus importes."""
from __future__ import annotations

import re

from pypdf import PdfReader

from itt_herramientas.dominio.quotation_models import QuoteData, QuoteItem
from itt_herramientas.dominio.quotation_pdf import generate_quote_pdf


def _cotizacion(n_puntos: int, idioma: str = "es") -> QuoteData:
    q = QuoteData(quote_number="PRUEBA-1", client_name="Cliente de prueba", project_title="Concepto largo", language=idioma)
    q.items = [
        QuoteItem(title="Levantamiento topográfico", description="Con GNSS RTK.", subitems=["Poligonal", "Detalles"],
                  quantity=1, unit_price=25000),
        QuoteItem(title="Proyecto ejecutivo", description="Incluye lo siguiente:",
                  subitems=[f"Entregable {i + 1}: planos, memorias de cálculo y especificaciones de la etapa {i + 1}"
                            for i in range(n_puntos)], quantity=1, unit_price=180000),
        QuoteItem(title="Deslinde", description="Del predio.", subitems=["Amojonamiento"], quantity=2, unit_price=8000),
    ]
    return q


def _paginas(tmp_path, q: QuoteData) -> list[str]:
    salida = generate_quote_pdf(q, tmp_path / "cotizacion.pdf")
    return [p.extract_text() or "" for p in PdfReader(str(salida)).pages]


def test_concepto_largo_cruza_la_pagina_sin_repetir_nada(tmp_path):
    paginas = _paginas(tmp_path, _cotizacion(40))
    texto = "\n".join(paginas)
    assert len(paginas) >= 2
    # Sus 40 puntos, una sola vez cada uno y en orden.
    posiciones = [texto.index(f"Entregable {i + 1}:") for i in range(40)]
    assert posiciones == sorted(posiciones)
    # Ni «continuación» ni el título repetido en la página siguiente.
    assert "continuación" not in texto.lower() and "continued" not in texto.lower()
    assert texto.count("2. Proyecto ejecutivo") == 1
    # Sus importes salen una sola vez (antes se repetían en cada bloque de «continuación»).
    assert texto.count("$ 180,000.00") == 2          # P.U. e importe, una vez cada uno
    # Lo que hay después del concepto largo sigue bien.
    assert "3. Deslinde" in paginas[-1] or "3. Deslinde" in texto


def test_concepto_corto_y_en_ingles_igual(tmp_path):
    texto = "\n".join(_paginas(tmp_path, _cotizacion(5, "en")))
    assert "continued" not in texto.lower()
    assert texto.count("$ 180,000.00") == 2


def test_concepto_muy_largo_no_rompe_la_hoja(tmp_path):
    # Más alto que una página entera: el PDF se genera y reparte sus puntos en varias.
    paginas = _paginas(tmp_path, _cotizacion(120))
    assert len(paginas) >= 3
    assert sum(len(re.findall(r"Entregable \d+:", p)) for p in paginas) == 120
