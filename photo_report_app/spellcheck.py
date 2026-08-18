from __future__ import annotations

import json
import re
import unicodedata
from difflib import get_close_matches
from dataclasses import dataclass, field
from datetime import datetime
from importlib import resources
from pathlib import Path
from tkinter import END, Listbox, StringVar, Text, Toplevel
from tkinter import ttk
from typing import Callable

from spellchecker import SpellChecker

from .app_storage import category_dir
from .ux_components import show_responsive_dialog


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

    def __init__(self, language: str = "es"):
        from wordfreq import zipf_frequency

        self.language = language if language in self.LANGUAGE_NAMES else "es"
        self.language_name = self.LANGUAGE_NAMES[self.language]
        self._zipf_frequency = zipf_frequency
        self.dictionary_dir = category_dir("dictionaries")
        self.personal_path = self.dictionary_dir / "diccionario_personal.json"
        self.reference_dir = self.dictionary_dir / "referencias_es_en"
        self.checker = SpellChecker(language=self.language, distance=1)
        self.technical_terms = COMMON_TECHNICAL_TERMS | (
            SPANISH_TECHNICAL_TERMS if self.language == "es" else ENGLISH_TECHNICAL_TERMS
        )
        self.custom_by_language = self._load_personal_words()
        self.custom_words = self.custom_by_language[self.language]
        if not self.personal_path.exists():
            self._save_personal_words()
        seeded = sorted(self.technical_terms | self.custom_words)
        self.checker.word_frequency.load_words(seeded)
        self._install_reference_files()

    def _load_personal_words(self) -> dict[str, set[str]]:
        try:
            raw = json.loads(self.personal_path.read_text(encoding="utf-8"))
            values = raw.get("words", {}) if isinstance(raw, dict) else raw
            if isinstance(values, dict):
                return {
                    language: {str(word).strip().casefold() for word in values.get(language, []) if str(word).strip()}
                    for language in ("es", "en")
                }
            legacy = {str(word).strip().casefold() for word in values if str(word).strip()}
            return {"es": set(legacy), "en": set(legacy)}
        except (OSError, ValueError):
            return {"es": set(), "en": set()}

    def _save_personal_words(self) -> None:
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

    def _install_reference_files(self) -> None:
        self.reference_dir.mkdir(parents=True, exist_ok=True)
        try:
            packaged = resources.files("spellchecker.resources")
            for filename in ("es.json.gz", "en.json.gz"):
                target = self.reference_dir / filename
                if not target.exists():
                    target.write_bytes(packaged.joinpath(filename).read_bytes())
        except Exception:
            pass
        try:
            wordfreq_data = resources.files("wordfreq").joinpath("data")
            for filename in (
                "large_es.msgpack.gz", "small_es.msgpack.gz",
                "large_en.msgpack.gz", "small_en.msgpack.gz",
            ):
                target = self.reference_dir / filename
                if not target.exists():
                    target.write_bytes(wordfreq_data.joinpath(filename).read_bytes())
        except Exception:
            pass
        readme = self.dictionary_dir / "LEEME_DICCIONARIOS.txt"
        readme.write_text(
            "Diccionarios de Grupo ITT App\n\n"
            "Validación principal: wordfreq 3.1.1 (Apache-2.0), con frecuencias amplias en español e inglés.\n"
            "Sugerencias: pyspellchecker 0.9.0 (MIT).\n"
            "diccionario_personal.json contiene las palabras agregadas por idioma desde Cotizaciones.\n"
            "referencias_es_en conserva las bases utilizadas como referencia y respaldo local.\n",
            encoding="utf-8",
        )

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


class SpellReviewDialog(Toplevel):
    def __init__(self, master, service: BilingualSpellService, documents: list[SpellDocument], issues: list[SpellingIssue]):
        super().__init__(master)
        self.withdraw()
        self.service = service
        self.documents = documents
        self.issues = issues
        self.position = 0
        self.ignored_words: set[str] = set()
        self.corrected = 0
        self.added = 0
        self.omitted = 0
        self.finished = False
        self.current_suggestions: list[str] = []
        self.replacement_var = StringVar()
        self.title(f"Corrección ortográfica · {service.language_name}")
        self.transient(master.winfo_toplevel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self._build()
        self._show_current()
        if self.winfo_exists():
            show_responsive_dialog(self, master, preferred_width=790, preferred_height=540)

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        header = ttk.Frame(self, style="Header.TFrame", padding=(18, 13))
        header.grid(row=0, column=0, sticky="ew")
        self.header_label = ttk.Label(header, text=f"Revisión ortográfica · {self.service.language_name}", style="HeaderTitle.TLabel")
        self.header_label.pack(side="left")
        self.counter_label = ttk.Label(header, text="", style="HeaderSub.TLabel")
        self.counter_label.pack(side="right")
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=max(len(self.issues), 1))
        self.progress.grid(row=1, column=0, sticky="ew")

        details = ttk.Frame(self, style="Card.TFrame", padding=18)
        details.grid(row=2, column=0, sticky="ew", padx=16, pady=(16, 8))
        details.columnconfigure(0, weight=1)
        self.field_label = ttk.Label(details, text="", style="Field.Card.TLabel")
        self.field_label.grid(row=0, column=0, sticky="w")
        self.word_label = ttk.Label(details, text="", style="Card.TLabel", font=("Segoe UI Semibold", 18), foreground="#B04C3D")
        self.word_label.grid(row=1, column=0, sticky="w", pady=(5, 7))
        self.context_label = ttk.Label(details, text="", style="Card.TLabel", wraplength=590, justify="left")
        self.context_label.grid(row=2, column=0, sticky="ew")
        self.bind("<Configure>", lambda event: self.context_label.configure(wraplength=max(360, event.width - 74)))

        ttk.Label(self, text="SUGERENCIAS", style="Section.TLabel").grid(row=3, column=0, sticky="w", padx=22, pady=(5, 3))
        suggestion_box = ttk.Frame(self, style="Card.TFrame", padding=(16, 5, 16, 10))
        suggestion_box.grid(row=4, column=0, sticky="nsew", padx=16)
        suggestion_box.columnconfigure(0, weight=1)
        suggestion_box.rowconfigure(0, weight=1)
        self.suggestion_list = Listbox(
            suggestion_box, height=6, font=("Segoe UI", 10), relief="solid", bd=1,
            activestyle="none", selectbackground="#B9DDEB", exportselection=False,
        )
        self.suggestion_list.grid(row=0, column=0, sticky="nsew")
        self.suggestion_list.bind("<<ListboxSelect>>", self._suggestion_selected)
        bar = ttk.Scrollbar(suggestion_box, orient="vertical", command=self.suggestion_list.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.suggestion_list.configure(yscrollcommand=bar.set)
        custom = ttk.Frame(suggestion_box, style="Card.TFrame")
        custom.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(9, 0))
        custom.columnconfigure(1, weight=1)
        ttk.Label(custom, text="Reemplazar por:", style="Field.Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.replacement_entry = ttk.Entry(custom, textvariable=self.replacement_var)
        self.replacement_entry.grid(row=0, column=1, sticky="ew")

        actions = ttk.Frame(self, style="Card.TFrame", padding=(16, 11, 16, 16))
        actions.grid(row=5, column=0, sticky="ew")
        ttk.Button(actions, text="Corregir sugerencia", style="Accent.TButton", command=self.correct_suggestion).pack(side="left")
        ttk.Button(actions, text="Cambiar por texto", style="Secondary.TButton", command=self.change_custom).pack(side="left", padx=5)
        ttk.Button(actions, text="Omitir", style="Secondary.TButton", command=self.ignore_once).pack(side="left", padx=(10, 3))
        ttk.Button(actions, text="Omitir todas", style="Secondary.TButton", command=self.ignore_all).pack(side="left", padx=3)
        ttk.Button(actions, text="Agregar al diccionario", style="Secondary.TButton", command=self.add_dictionary).pack(side="right")
        ttk.Button(self, text="Cancelar revisión", style="Secondary.TButton", command=self.cancel).grid(row=6, column=0, sticky="e", padx=16, pady=(0, 13))

    def _show_current(self):
        while self.position < len(self.issues) and self.issues[self.position].word.casefold() in self.ignored_words:
            self.position += 1
            self.omitted += 1
        if self.position >= len(self.issues):
            self.finished = True
            self.destroy()
            return
        issue = self.issues[self.position]
        document = self.documents[issue.document_index]
        self.counter_label.configure(text=f"Palabra {self.position + 1} de {len(self.issues)}")
        self.progress["value"] = self.position
        self.field_label.configure(text=document.field.label.upper())
        self.word_label.configure(text=issue.word)
        self.context_label.configure(text=document.context(issue.token_index))
        self.current_suggestions = self.service.suggestions(issue.word)
        self.suggestion_list.delete(0, END)
        for suggestion in self.current_suggestions:
            self.suggestion_list.insert(END, suggestion)
        if self.current_suggestions:
            self.suggestion_list.selection_set(0)
            self.replacement_var.set(self.current_suggestions[0])
        else:
            self.replacement_var.set(issue.word)
        self.replacement_entry.icursor(END)

    def _suggestion_selected(self, _event=None):
        selected = self.suggestion_list.curselection()
        if selected:
            self.replacement_var.set(self.suggestion_list.get(selected[0]))

    def _replace_current(self, value: str):
        value = value.strip()
        if not value:
            self.bell()
            self.replacement_entry.focus_set()
            return
        issue = self.issues[self.position]
        self.documents[issue.document_index].replace(issue.token_index, value)
        self.corrected += 1
        self.position += 1
        self._show_current()

    def correct_suggestion(self):
        selected = self.suggestion_list.curselection()
        if not selected:
            self.bell()
            return
        self._replace_current(self.suggestion_list.get(selected[0]))

    def change_custom(self):
        self._replace_current(self.replacement_var.get())

    def ignore_once(self):
        self.omitted += 1
        self.position += 1
        self._show_current()

    def ignore_all(self):
        self.ignored_words.add(self.issues[self.position].word.casefold())
        self.omitted += 1
        self.position += 1
        self._show_current()

    def add_dictionary(self):
        word = self.issues[self.position].word
        self.service.add_word(word)
        self.ignored_words.add(word.casefold())
        self.added += 1
        self.position += 1
        self._show_current()

    def cancel(self):
        self.finished = False
        self.destroy()
