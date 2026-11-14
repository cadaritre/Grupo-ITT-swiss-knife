"""Comprobaciones de despliegue de Herramientas (`manage.py check`)."""
from __future__ import annotations

from django.core import checks


@checks.register(checks.Tags.compatibility)
def agente_de_mapas(app_configs, **kwargs):
    """OpenStreetMap exige un agente de usuario identificable con una URL de contacto."""
    from tvplataforma import ajustes

    agente = str(ajustes.valor("TV_MAPAS_AGENTE") or "")
    if "(+http" not in agente or "tools." not in agente:
        return [checks.Warning(
            "TV_MAPAS_AGENTE no identifica a Herramientas.",
            hint="La política de teselas de OpenStreetMap pide un agente con contacto; usa, por ejemplo, «TresVizoHerramientas/1.0 (+https://tools.tresvizo.com)».",
            id="herramientas.W001")]
    return []
