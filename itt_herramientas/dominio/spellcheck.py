from __future__ import annotations

import json
import re
import unicodedata
from difflib import get_close_matches
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from spellchecker import SpellChecker


WORD_PATTERN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+(?:['’][A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+)?")
MINIMUM_ZIPF_FREQUENCY = 1.0
LANGUAGE_FREQUENCY_TOLERANCE = 0.5

COMMON_TECHNICAL_TERMS = {
    "autocad", "asbuilt", "bim", "built", "clabe", "dxf", "dwg", "e57", "epsg", "faro", "focus",
    "gnss", "inegi", "kmz", "kml", "lidar", "lod", "revit", "rgna", "rtk", "rvt",
    "utm", "wgs",
}
SPANISH_TECHNICAL_TERMS = {
    "altimetría", "altimétrico", "autodesk", "brocales", "camellones", "cubicaciones",
    "desmontajes", "fotogrametría", "fotogramétrico", "geodésica", "geodésico", "georreferenciada",
    "georreferenciado", "georreferenciación", "ortomosaico", "planimetría", "postproceso",
    "planimétrico", "plafones", "mojoneras", "obstruidos", "topografía", "topográfico",
    "topográfica", "volumetría",
}
ENGLISH_TECHNICAL_TERMS = {
    "geodetic", "georeferenced", "georeferencing", "orthomosaic", "photogrammetry",
    "photogrammetric", "planimetry", "postprocessing", "topographic", "topography", "volumetry",
}


@dataclass
class ReviewField:
    label: str
    value: str
    apply: Callable[[str], None]


@dataclass
class SpellDocument:
    field: ReviewField
    matches: list[re.Match] = field(default_factory=list)
    replacements: dict[int, str] = field(default_factory=dict)

    def word(self, token_index: int) -> str:
        return self.matches[token_index].group(0)

    def replace(self, token_index: int, value: str) -> None:
        self.replacements[token_index] = value

    def context(self, token_index: int, radius: int = 70) -> str:
        match = self.matches[token_index]
        start = max(0, match.start() - radius)
        end = min(len(self.field.value), match.end() + radius)
        prefix = "…" if start else ""
        suffix = "…" if end < len(self.field.value) else ""
        return f"{prefix}{self.field.value[start:match.start()]}⟦{match.group(0)}⟧{self.field.value[match.end():end]}{suffix}"

    def result(self) -> str:
        pieces = []
        cursor = 0
        for index, match in enumerate(self.matches):
            pieces.append(self.field.value[cursor:match.start()])
            pieces.append(self.replacements.get(index, match.group(0)))
            cursor = match.end()
        pieces.append(self.field.value[cursor:])
        return "".join(pieces)

    def apply_changes(self) -> None:
        self.field.apply(self.result())


@dataclass(frozen=True)
class SpellingIssue:
    document_index: int
    token_index: int
    word: str


class BilingualSpellService:
    LANGUAGE_NAMES = {"es": "Español", "en": "English"}

    def __init__(
        self,
        language: str = "es",
        directorio: str | Path | None = None,
        palabras_personales: dict[str, list[str]] | None = None,
    ):
        """Corrector bilingüe.

        `directorio`: carpeta donde se guarda `diccionario_personal.json` (como el escritorio). Sin carpeta, el
        diccionario personal vive en memoria y la capa de almacenamiento lo lee con `palabras_personales_json()`
        y lo entrega de nuevo en `palabras_personales` (diccionario por cuenta en la web).
        """
        from wordfreq import zipf_frequency

        self.language = language if language in self.LANGUAGE_NAMES else "es"
        self.language_name = self.LANGUAGE_NAMES[self.language]
        self._zipf_frequency = zipf_frequency
        self.dictionary_dir = Path(directorio) if directorio else None
        self.personal_path = self.dictionary_dir / "diccionario_personal.json" if self.dictionary_dir else None
        self.checker = SpellChecker(language=self.language, distance=1)
        self.technical_terms = COMMON_TECHNICAL_TERMS | (
            SPANISH_TECHNICAL_TERMS if self.language == "es" else ENGLISH_TECHNICAL_TERMS
        )
        self.custom_by_language = self._load_personal_words(palabras_personales)
        self.custom_words = self.custom_by_language[self.language]
        if self.personal_path is not None and not self.personal_path.exists():
            self._save_personal_words()
        seeded = sorted(self.technical_terms | self.custom_words)
        self.checker.word_frequency.load_words(seeded)

    @staticmethod
    def _normalize_personal(values) -> dict[str, set[str]]:
        if isinstance(values, dict):
            return {
                language: {str(word).strip().casefold() for word in values.get(language, []) if str(word).strip()}
                for language in ("es", "en")
            }
        legacy = {str(word).strip().casefold() for word in values if str(word).strip()}
        return {"es": set(legacy), "en": set(legacy)}

    def _load_personal_words(self, initial=None) -> dict[str, set[str]]:
        if initial is not None:
            return self._normalize_personal(initial)
        if self.personal_path is None:
            return {"es": set(), "en": set()}
        try:
            raw = json.loads(self.personal_path.read_text(encoding="utf-8"))
            values = raw.get("words", {}) if isinstance(raw, dict) else raw
            return self._normalize_personal(values)
        except (OSError, ValueError):
            return {"es": set(), "en": set()}

    def palabras_personales_json(self) -> dict[str, list[str]]:
        """Diccionario personal actual, listo para guardarlo (por idioma, ordenado)."""
        return {language: sorted(words) for language, words in self.custom_by_language.items()}

    def _save_personal_words(self) -> None:
        if self.personal_path is None:
            return
        self.personal_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "languages": ["es", "en"],
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "words": {
                language: sorted(words) for language, words in self.custom_by_language.items()
            },
        }
        temporary = self.personal_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.personal_path)

    def _skip_word(self, word: str) -> bool:
        clean = word.strip("'’")
        return (
            len(clean) <= 1
            or (clean.isupper() and len(clean) <= 8)
            or clean.casefold() in self.technical_terms
        )

    def is_known(self, word: str) -> bool:
        lower = word.casefold()
        target_frequency = self._zipf_frequency(lower, self.language)
        other_language = "en" if self.language == "es" else "es"
        other_frequency = self._zipf_frequency(lower, other_language)
        return (
            lower in self.custom_words
            or lower in self.checker
            or (
                target_frequency >= MINIMUM_ZIPF_FREQUENCY
                and target_frequency + LANGUAGE_FREQUENCY_TOLERANCE >= other_frequency
            )
        )

    def scan(self, fields: list[ReviewField]) -> tuple[list[SpellDocument], list[SpellingIssue]]:
        documents = []
        issues = []
        for document_index, review_field in enumerate(fields):
            document = SpellDocument(review_field, list(WORD_PATTERN.finditer(review_field.value or "")))
            documents.append(document)
            for token_index, match in enumerate(document.matches):
                word = match.group(0)
                if not self._skip_word(word) and not self.is_known(word):
                    issues.append(SpellingIssue(document_index, token_index, word))
        return documents, issues

    @staticmethod
    def _match_case(suggestion: str, original: str) -> str:
        if original.isupper():
            return suggestion.upper()
        if original[:1].isupper():
            return suggestion[:1].upper() + suggestion[1:]
        return suggestion

    @staticmethod
    def _accentless(value: str) -> str:
        normalized = unicodedata.normalize("NFD", value.casefold())
        return "".join(character for character in normalized if unicodedata.category(character) != "Mn")

    def suggestions(self, word: str, limit: int = 8) -> list[str]:
        lower = word.casefold()
        ordered = []
        technical_index = {self._accentless(value): value for value in self.technical_terms | self.custom_words}
        for normalized in get_close_matches(self._accentless(lower), technical_index, n=3, cutoff=0.78):
            candidate = technical_index[normalized]
            if candidate.casefold() != lower and candidate not in ordered:
                ordered.append(candidate)
        correction = self.checker.correction(lower)
        candidates = self.checker.candidates(lower) or set()
        for candidate in ([correction] if correction else []) + sorted(candidates):
            if candidate and candidate.casefold() != lower and candidate not in ordered:
                ordered.append(candidate)
        return [self._match_case(value, word) for value in ordered[:limit]]

    def add_word(self, word: str) -> None:
        value = word.strip().casefold()
        if not value:
            return
        self.custom_words.add(value)
        self.checker.word_frequency.add(value)
        self._save_personal_words()
