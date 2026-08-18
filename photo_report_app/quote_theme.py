from __future__ import annotations

from dataclasses import dataclass

from reportlab.lib.colors import HexColor

from .app_storage import SETTINGS


QUOTE_PALETTE_PRESETS = {
    "Azul corporativo": {
        "primary": "#07356F", "accent": "#0B7FAB", "pale": "#EAF3F8",
        "bar_text": "#FFFFFF", "ink": "#263746",
    },
    "Naranja": {
        "primary": "#D85C0D", "accent": "#B84600", "pale": "#FFF0E3",
        "bar_text": "#FFFFFF", "ink": "#242424",
    },
    "Amarillo": {
        "primary": "#F2C230", "accent": "#8A6500", "pale": "#FFF8D6",
        "bar_text": "#111111", "ink": "#202020",
    },
    "Verde": {
        "primary": "#176B4D", "accent": "#207A58", "pale": "#E5F5EE",
        "bar_text": "#FFFFFF", "ink": "#21352D",
    },
}


@dataclass(frozen=True)
class QuotePalette:
    primary_hex: str
    accent_hex: str
    pale_hex: str
    bar_text_hex: str
    ink_hex: str

    @property
    def primary(self):
        return HexColor(self.primary_hex)

    @property
    def accent(self):
        return HexColor(self.accent_hex)

    @property
    def pale(self):
        return HexColor(self.pale_hex)

    @property
    def bar_text(self):
        return HexColor(self.bar_text_hex)

    @property
    def ink(self):
        return HexColor(self.ink_hex)


def quote_palette() -> QuotePalette:
    default = QUOTE_PALETTE_PRESETS["Azul corporativo"]
    return QuotePalette(
        primary_hex=str(SETTINGS.get("documents.quote_palette.primary", default["primary"])),
        accent_hex=str(SETTINGS.get("documents.quote_palette.accent", default["accent"])),
        pale_hex=str(SETTINGS.get("documents.quote_palette.pale", default["pale"])),
        bar_text_hex=str(SETTINGS.get("documents.quote_palette.bar_text", default["bar_text"])),
        ink_hex=str(SETTINGS.get("documents.quote_palette.ink", default["ink"])),
    )


def matching_preset(values: dict[str, str]) -> str:
    normalized = {key: str(value).upper() for key, value in values.items()}
    for name, preset in QUOTE_PALETTE_PRESETS.items():
        if normalized == {key: value.upper() for key, value in preset.items()}:
            return name
    return "Personalizada"
