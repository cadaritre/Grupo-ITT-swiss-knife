"""Paridad de `totales.js` con la aritmética de `QuoteData` (Python): mismo redondeo, misma suma, mismo formato."""
from __future__ import annotations

import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from itt_herramientas.dominio import quotation_models as qm
from itt_herramientas.dominio.quotation_pdf import money

JS = Path(__file__).resolve().parents[1] / "static" / "herramientas" / "cotizaciones" / "totales.js"
SCRIPT = """
const t = require(process.argv[1]);
const casos = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const salida = casos.map((c) => { const r = t.totales(c); return { subtotal: r.subtotal, iva: r.iva, total: r.total, anticipo: r.anticipo, importes: r.importes, texto: t.dinero(r.total) }; });
console.log(JSON.stringify(salida));
"""
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node no está instalado")


def aleatorios(n=4000):
    azar = random.Random(20261113)
    casos = []
    # Empates exactos del redondeo (x.xx5 representable) y valores clásicos que engañan a toFixed/Math.round.
    empates = [0.125, 0.375, 0.625, 0.875, 2.5, 1.005, 2.675, 1.115, 0.045, 10.075, 1e-9, 0.005, 0.015, 0.025]
    for q, p in [(1, e) for e in empates] + [(e, 1) for e in empates] + [(3, 0.1), (0.1, 3), (7, 0.07), (1.1, 1.1)]:
        casos.append({"items": [{"quantity": q, "unit_price": p}], "include_vat": True, "vat_rate": 16.0, "advance_percent": 50.0})
    for _ in range(n):
        items = []
        for _ in range(azar.randint(0, 25)):
            cantidad = azar.choice([azar.randint(1, 50), round(azar.uniform(0.01, 500), azar.randint(0, 4)), azar.choice([0.5, 0.25, 1.5, 2.5])])
            precio = azar.choice([round(azar.uniform(0, 250000), 2), round(azar.uniform(0, 1000), azar.randint(0, 5)), azar.choice([0.125, 0.375, 33.335, 99.995])])
            items.append({"quantity": cantidad, "unit_price": precio})
        casos.append({"items": items, "include_vat": azar.random() < 0.8, "vat_rate": azar.choice([16.0, 8.0, 0.0, 10.5, 16.0]), "advance_percent": azar.choice([50.0, 30.0, 33.3, 0.0, 100.0, 12.5])})
    return casos


def en_python(caso):
    q = qm.QuoteData(include_vat=caso["include_vat"], vat_rate=caso["vat_rate"], advance_percent=caso["advance_percent"],
                     items=[qm.QuoteItem(quantity=i["quantity"], unit_price=i["unit_price"]) for i in caso["items"]])
    return {"subtotal": q.subtotal, "iva": q.vat, "total": q.total, "anticipo": q.advance, "importes": [i.amount for i in q.items], "texto": money(q.total)}


def test_totales_js_igual_a_python():
    casos = aleatorios()
    proceso = subprocess.run(["node", "-e", SCRIPT, str(JS)], input=json.dumps(casos), capture_output=True, text=True, timeout=120)
    assert proceso.returncode == 0, proceso.stderr
    js = json.loads(proceso.stdout)
    assert len(js) == len(casos)
    diferencias = [(c, p, j) for c, j in zip(casos, js) if (p := en_python(c)) != j]
    assert not diferencias, f"{len(diferencias)} diferencias; la primera: {diferencias[0]}"
