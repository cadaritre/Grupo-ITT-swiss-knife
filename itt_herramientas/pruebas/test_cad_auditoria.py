"""CAD: regresiones de la auditoría J (15-11-2026) que no necesitan navegador. Las de navegador están en
pruebas_navegador/test_cad_auditoria_navegador.py."""
from __future__ import annotations

import re
from pathlib import Path

from itt_herramientas.cad import editor

from .conftest import url
from .test_cad import dibujante, editor_falso, plano  # noqa: F401 (fixtures)

ESTATICOS = Path(editor.__file__).resolve().parents[2] / "static" / "herramientas" / "cad"


def test_b13_el_menu_mas_acciones_es_una_lista_de_botones_y_los_toques_miden_44(cliente_de, dibujante, plano, editor_falso):
    """Un `role="menu"` promete flechas, Inicio y Fin y lleva formularios dentro: el menú es un grupo de botones y enlaces normales."""
    html = cliente_de(dibujante).get(url("cad_plano", pk=plano.pk)).content.decode()
    assert 'role="menu"' not in html and 'role="menuitem"' not in html
    assert re.search(r'class="cad-menu__lista" role="group" aria-label="Más acciones"', html)
    css = (ESTATICOS / "cad.css").read_text()
    regla = re.search(r"\.cad-espacio__detalle > summary \{([^}]*)\}", css).group(1)
    assert "min-height: 44px" in regla

