from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .app_storage import SETTINGS


INSTALL_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
ITT_LOGO = INSTALL_ROOT / "assets" / "logo.png"
TRESVIZO_LOGO = INSTALL_ROOT / "assets" / "logo_tresvizo.png"
LEGACY_ITT_SIGNER = "ING. CARLOS RIVERA ABAID"


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
            values.append(f"{'Phone' if language == 'en' else 'Tel.'} {self.phone}")
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


def active_profile() -> CompanyProfile:
    if SETTINGS.get("branding.profile", "grupo_itt") == "tresvizo":
        website = str(SETTINGS.get("branding.tresvizo.website", "https://www.tresvizo.com/")).strip()
        website_label = website.removeprefix("https://").removeprefix("http://").rstrip("/")
        stored_name = str(SETTINGS.get("branding.tresvizo.name", "TresVizo")).strip() or "TresVizo"
        if stored_name.casefold() == "tresvizo":
            stored_name = "TresVizo"
        return CompanyProfile(
            key="tresvizo",
            name=stored_name,
            app_name="Herramientas TresVizo",
            document_heading="TresVizo Ingeniería",
            document_heading_en="TresVizo Engineering",
            description="Servicios de ingeniería, topografía de precisión, escaneo 3D, fotogrametría y avalúos",
            description_en="Engineering services, precision surveying, 3D scanning, photogrammetry and valuations",
            website=website or "https://www.tresvizo.com/",
            website_label=website_label or "www.tresvizo.com",
            phone=str(SETTINGS.get("branding.tresvizo.phone", "614 100 2069")).strip(),
            address="",
            email="",
            signer=str(SETTINGS.get("branding.tresvizo.signer", "ING. EDGAR TREVIZO")).strip() or "ING. EDGAR TREVIZO",
            logo_path=_existing_logo(SETTINGS.get("branding.tresvizo.custom_logo", ""), TRESVIZO_LOGO),
            document_footer="TresVizo | Ingeniería, Topografía y Geomática",
            document_footer_en="TresVizo | Engineering, Surveying and Geomatics",
        )
    return CompanyProfile(
        key="grupo_itt",
        name="Grupo ITT",
        app_name="Herramientas Grupo ITT",
        document_heading="INGENIERÍA TÉCNICA Y TOPOGRÁFICA",
        document_heading_en="TECHNICAL ENGINEERING AND SURVEYING",
        description="Ingeniería técnica, topografía, construcción y puentes",
        description_en="Technical engineering, surveying, construction and bridges",
        website="https://www.grupoitt.com/",
        website_label="www.grupoitt.com",
        phone="(614) 413 19 89",
        address="J. Domínguez de Mendoza #1704, Col. San Felipe, Chihuahua, Chih.",
        email="crivera@grupoitt.com",
        signer=LEGACY_ITT_SIGNER,
        logo_path=ITT_LOGO,
        document_footer="Ingeniería Técnica y Topográfica | Construcciones y Puentes",
        document_footer_en="Technical Engineering and Surveying | Construction and Bridges",
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
