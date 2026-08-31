from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .app_storage import category_dir
from .quotation_models import QuoteItem


SCHEMA_VERSION = 1
TEMPLATE_TOOL_ID = "grupo_itt.quote_concept_templates"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _item_from_dict(raw: dict) -> QuoteItem:
    fields = set(QuoteItem.__dataclass_fields__)
    values = {key: value for key, value in raw.items() if key in fields}
    values["subitems"] = [str(value) for value in values.get("subitems", []) if str(value).strip()]
    for key, fallback in (("quantity", 1.0), ("unit_price", 0.0)):
        try:
            values[key] = float(values.get(key, fallback))
        except (TypeError, ValueError):
            values[key] = fallback
    return QuoteItem(**values)


@dataclass
class CustomConceptTemplate:
    template_id: str
    name: str
    language: str
    item: QuoteItem
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return {
            "id": self.template_id,
            "name": self.name,
            "language": self.language,
            "item": asdict(self.item),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "CustomConceptTemplate":
        name = " ".join(str(raw.get("name", "")).split()).strip()
        if not name:
            raise ValueError("El machote no tiene nombre.")
        language = str(raw.get("language", "es")).lower()
        if language not in {"es", "en"}:
            language = "es"
        created = str(raw.get("created_at", "") or _now_iso())
        return cls(
            template_id=str(raw.get("id", "") or uuid.uuid4()),
            name=name,
            language=language,
            item=_item_from_dict(raw.get("item", {}) if isinstance(raw.get("item"), dict) else {}),
            created_at=created,
            updated_at=str(raw.get("updated_at", "") or created),
        )

    def clone_item(self) -> QuoteItem:
        return _item_from_dict(asdict(self.item))


class CustomConceptTemplateStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else category_dir("quotes") / "Machotes" / "machotes_personalizados.json"
        self.backup_path = self.path.with_suffix(".backup.json")
        self.templates: list[CustomConceptTemplate] = []
        self.last_error = ""
        self.reload()

    def reload(self) -> list[CustomConceptTemplate]:
        self.last_error = ""
        if not self.path.exists():
            self.templates = []
            return self.templates
        try:
            self.templates = self._read(self.path)
        except (OSError, ValueError, TypeError) as exc:
            self.last_error = str(exc)
            try:
                self.templates = self._read(self.backup_path)
            except (OSError, ValueError, TypeError):
                self.templates = []
        self._sort()
        return self.templates

    @staticmethod
    def _read(path: Path) -> list[CustomConceptTemplate]:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("tool") != TEMPLATE_TOOL_ID:
            raise ValueError("El archivo no es una colección de machotes de cotización compatible.")
        if int(raw.get("schema_version", 0)) > SCHEMA_VERSION:
            raise ValueError("El archivo fue creado con una versión más reciente de la aplicación.")
        records = raw.get("templates", [])
        if not isinstance(records, list):
            raise ValueError("La colección de machotes está dañada.")
        result = []
        seen_ids = set()
        for raw_record in records:
            if not isinstance(raw_record, dict):
                continue
            record = CustomConceptTemplate.from_dict(raw_record)
            if record.template_id in seen_ids:
                record.template_id = str(uuid.uuid4())
            seen_ids.add(record.template_id)
            result.append(record)
        return result

    def _sort(self):
        self.templates.sort(key=lambda template: (template.name.casefold(), template.language, template.template_id))

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copy2(self.path, self.backup_path)
        payload = {
            "tool": TEMPLATE_TOOL_ID,
            "schema_version": SCHEMA_VERSION,
            "updated_at": _now_iso(),
            "templates": [template.to_dict() for template in self.templates],
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def by_id(self, template_id: str) -> CustomConceptTemplate | None:
        return next((template for template in self.templates if template.template_id == template_id), None)

    def find_name(self, name: str) -> CustomConceptTemplate | None:
        folded = " ".join(name.split()).casefold()
        return next((template for template in self.templates if template.name.casefold() == folded), None)

    def save_item(
        self,
        name: str,
        item: QuoteItem,
        language: str,
        template_id: str | None = None,
    ) -> CustomConceptTemplate:
        clean_name = " ".join(str(name).split()).strip()
        if not clean_name:
            raise ValueError("Escribe un nombre para el machote.")
        now = _now_iso()
        existing = self.by_id(template_id) if template_id else None
        conflict = self.find_name(clean_name)
        if conflict is not None and conflict is not existing:
            raise ValueError(f"Ya existe un machote personalizado llamado “{clean_name}”.")
        if existing is None:
            clean_language = language if language in {"es", "en"} else "es"
            existing = CustomConceptTemplate(str(uuid.uuid4()), clean_name, clean_language, _item_from_dict(asdict(item)), now, now)
            self.templates.append(existing)
        else:
            existing.name = clean_name
            existing.language = language if language in {"es", "en"} else "es"
            existing.item = _item_from_dict(asdict(item))
            existing.updated_at = now
        self._sort()
        self._save()
        return existing

    def rename(self, template_id: str, name: str) -> CustomConceptTemplate:
        record = self.by_id(template_id)
        if record is None:
            raise ValueError("El machote seleccionado ya no existe.")
        return self.save_item(name, record.item, record.language, record.template_id)

    def delete(self, template_id: str) -> bool:
        original = len(self.templates)
        self.templates = [template for template in self.templates if template.template_id != template_id]
        if len(self.templates) == original:
            return False
        self._save()
        return True

    def export_file(self, path: str | Path, template_ids: set[str] | None = None) -> Path:
        target = Path(path)
        selected = self.templates if template_ids is None else [
            template for template in self.templates if template.template_id in template_ids
        ]
        payload = {
            "tool": TEMPLATE_TOOL_ID,
            "schema_version": SCHEMA_VERSION,
            "exported_at": _now_iso(),
            "templates": [template.to_dict() for template in selected],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    def import_file(self, path: str | Path) -> tuple[int, int]:
        imported = self._read(Path(path))
        added = 0
        renamed = 0
        existing_ids = {template.template_id for template in self.templates}
        existing_names = {template.name.casefold() for template in self.templates}
        for record in imported:
            if record.template_id in existing_ids:
                record.template_id = str(uuid.uuid4())
            base_name = record.name
            counter = 2
            name_changed = False
            while record.name.casefold() in existing_names:
                record.name = f"{base_name} (importado {counter})"
                counter += 1
                name_changed = True
            if name_changed:
                renamed += 1
            existing_ids.add(record.template_id)
            existing_names.add(record.name.casefold())
            self.templates.append(record)
            added += 1
        self._sort()
        if added:
            self._save()
        return added, renamed
