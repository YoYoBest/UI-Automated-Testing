from __future__ import annotations

import json
from pathlib import Path
from .project_layout import resolve_view_root
from typing import Any, Iterable

from .contracts import normalize_field
from .models import DomField, FieldDefinition, ResolvedField


def _find_field_arrays(value: Any) -> Iterable[list[dict[str, Any]]]:
    if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
        if any("fieldCode" in item for item in value):
            yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _find_field_arrays(child)
    elif isinstance(value, list):
        for child in value:
            yield from _find_field_arrays(child)


def load_fields_from_json(path: Path) -> list[FieldDefinition]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    arrays = list(_find_field_arrays(data))
    if not arrays:
        return []
    fields = [normalize_field(raw, f"json:{path}") for raw in max(arrays, key=len)]
    return [field for field in fields if field.field_code]


def discover_form_json(source_root: Path, form_code: str) -> list[Path]:
    if not form_code:
        return []
    needle = form_code.lower()
    view_root = resolve_view_root(source_root)
    if not view_root.exists():
        return []
    matches: list[Path] = []
    for path in view_root.rglob("*.json"):
        if any(part in {"node_modules", "dist"} for part in path.parts):
            continue
        if needle in path.stem.lower():
            matches.append(path)
    return sorted(matches)


def extract_runtime_fields(response: Any) -> list[FieldDefinition]:
    """Accept common axios/envelope shapes and find the largest fieldCode array."""
    arrays = list(_find_field_arrays(response))
    if not arrays:
        return []
    fields = [normalize_field(raw, "runtime-api") for raw in max(arrays, key=len)]
    return [field for field in fields if field.field_code]


def merge_definitions(*sources: Iterable[FieldDefinition]) -> list[FieldDefinition]:
    """Later sources win, allowing runtime API to override checked-in JSON."""
    merged: dict[str, FieldDefinition] = {}
    for fields in sources:
        for field in fields:
            if field.field_code:
                merged[field.field_code] = field
    return sorted(merged.values(), key=lambda item: (item.sort_order or 10**9, item.field_code))


def match_dom_fields(definitions: Iterable[FieldDefinition], dom_fields: Iterable[DomField]) -> list[ResolvedField]:
    dom = list(dom_fields)
    by_code = {item.field_code: item for item in dom if item.field_code}
    result: list[ResolvedField] = []
    for definition in definitions:
        match = by_code.get(definition.field_code)
        if match is None and definition.field_name:
            match = next((item for item in dom if item.label.strip() == definition.field_name.strip()), None)
        result.append(ResolvedField(definition=definition, dom=match))
    return result
