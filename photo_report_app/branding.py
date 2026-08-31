from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .app_storage import SETTINGS


INSTALL_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ITT_LOGO = INSTALL_ROOT / "assets" / "logo.png"
TRESVIZO_LOGO = INSTALL_ROOT / "assets" / "logo_tresvizo.png"
LEGACY_ITT_SIGNER = "ING. CARLOS RIVERA ABAID"

PROFILE_DEFAULTS = {
    "grupo_itt": {
        "name": "Grupo ITT",
        "document_heading": "INGENIERÍA TÉCNICA Y TOPOGRÁFICA",
        "document_heading_en": "TECHNICAL ENGINEERING AND SURVEYING",
        "description": "Ingeniería técnica y topografía",
        "description_en": "Technical engineering and surveying",
        "website": "https://www.grupoitt.com/",
        "phone": "(614) 413 19 89",
        "phone_label": "Tel.",
        "phone_label_en": "Phone",
        "address": "J. Domínguez de Mendoza #1704, Col. San Felipe, Chihuahua, Chih.",
        "email": "crivera@grupoitt.com",
        "signer": LEGACY_ITT_SIGNER,
        "document_footer": "Grupo ITT | Ingeniería y Topografía",
        "document_footer_en": "Grupo ITT | Engineering and Surveying",
    },
    "tresvizo": {
        "name": "TresVizo",
        "document_heading": "TresVizo Ingeniería",
        "document_heading_en": "TresVizo Engineering",
        "description": "Servicios de ingeniería, topografía de precisión, escaneo 3D, fotogrametría y avalúos",
        "description_en": "Engineering services, precision surveying, 3D scanning, photogrammetry and valuations",
        "website": "https://www.tresvizo.com/",
        "phone": "614 100 2069",
        "phone_label": "Tel.",
        "phone_label_en": "Phone",
        "address": "",
        "email": "",
        "signer": "ING. EDGAR TREVIZO",
        "document_footer": "TresVizo | Ingeniería y Topografía",
        "document_footer_en": "TresVizo | Engineering and Surveying",
    },
}


@dataclass(frozen=True)
class CompanyProfile:
    key: str
    name: str
    app_name: str
    document_heading: str
    document_heading_en: str
    description: str
    description_en: str
    website: str
    website_label: str
    phone: str
    phone_label: str
    phone_label_en: str
    address: str
    email: str
    signer: str
    logo_path: Path
    document_footer: str
    document_footer_en: str

    @property
    def contact_line(self) -> str:
        return self.contact_line_for("es")

    def contact_line_for(self, language: str) -> str:
        values = []
        if self.phone:
            label = self.phone_label_en if language == "en" else self.phone_label
            values.append(f"{label} {self.phone}".strip())
        if self.website_label:
            values.append(self.website_label)
        if self.email:
            values.append(self.email)
        return "  |  ".join(values)

    def heading_for(self, language: str) -> str:
        return self.document_heading_en if language == "en" else self.document_heading

    def description_for(self, language: str) -> str:
        return self.description_en if language == "en" else self.description

    def footer_for(self, language: str) -> str:
        return self.document_footer_en if language == "en" else self.document_footer


def _existing_logo(raw: str | None, fallback: Path) -> Path:
    if raw:
        candidate = Path(raw)
        if candidate.exists() and candidate.is_file():
            return candidate
    return fallback


def profile_defaults(key: str) -> dict[str, str]:
    return dict(PROFILE_DEFAULTS["tresvizo" if key == "tresvizo" else "grupo_itt"])


def _profile_value(key: str, field: str) -> str:
    default = PROFILE_DEFAULTS[key][field]
    value = SETTINGS.get(f"branding.{key}.{field}", default)
    return str(value) if value is not None else default


def active_profile() -> CompanyProfile:
    key = "tresvizo" if SETTINGS.get("branding.profile", "grupo_itt") == "tresvizo" else "grupo_itt"
    website = _profile_value(key, "website").strip()
    website_label = website.removeprefix("https://").removeprefix("http://").rstrip("/")
    fallback_logo = TRESVIZO_LOGO if key == "tresvizo" else ITT_LOGO
    custom_logo = SETTINGS.get(f"branding.{key}.custom_logo", "")
    return CompanyProfile(
        key=key,
        name=_profile_value(key, "name").strip(),
        app_name="Herramientas TresVizo" if key == "tresvizo" else "Herramientas Grupo ITT",
        document_heading=_profile_value(key, "document_heading"),
        document_heading_en=_profile_value(key, "document_heading_en"),
        description=_profile_value(key, "description"),
        description_en=_profile_value(key, "description_en"),
        website=website,
        website_label=website_label,
        phone=_profile_value(key, "phone").strip(),
        phone_label=_profile_value(key, "phone_label").strip(),
        phone_label_en=_profile_value(key, "phone_label_en").strip(),
        address=_profile_value(key, "address").strip(),
        email=_profile_value(key, "email").strip(),
        signer=_profile_value(key, "signer").strip(),
        logo_path=_existing_logo(custom_logo, fallback_logo),
        document_footer=_profile_value(key, "document_footer"),
        document_footer_en=_profile_value(key, "document_footer_en"),
    )


def signer_for_new_quote() -> str:
    return active_profile().signer


def normalize_quote_signer(value: str) -> str:
    """Replace only the old built-in signer when the alternate profile is active."""
    profile = active_profile()
    clean = (value or "").strip()
    if profile.key == "tresvizo" and (not clean or clean.upper() == LEGACY_ITT_SIGNER):
        return profile.signer
    return clean or profile.signer
