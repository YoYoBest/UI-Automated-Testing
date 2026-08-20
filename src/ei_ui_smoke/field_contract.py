from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from .contracts import normalize_field
from .models import FieldDefinition
from .runtime_api import RuntimeApi
from .schema import extract_runtime_fields, merge_definitions


def _default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "field_contracts.json"


def _normalise_component(value: str) -> str:
    normalized = str(value or "").replace("\\", "/").split("?", 1)[0]
    normalized = normalized.split("#", 1)[0].strip().removesuffix(".vue")
    for prefix in ("@/", "/srcEi/views/", "srcEi/views/", "/src/views/", "src/views/", "views/"):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            break
    return normalized.strip("/").lower()


def _source_definitions(
    fields: Iterable[tuple[str, str, bool]],
) -> list[FieldDefinition]:
    return [
        FieldDefinition(
            field_code=str(code).strip(),
            field_name=str(label).strip(),
            source="source-readonly",
        )
        for code, label, *_rest in fields
        if str(code).strip()
    ]


def _manifest_fields(path: Path, form_code: str, component: str) -> list[FieldDefinition]:
    """Load only an exact form/component override; never infer a sibling form."""
    if not form_code or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AssertionError(f"字段契约 manifest 无法读取：{path}") from exc
    forms = raw.get("forms") if isinstance(raw, dict) else None
    form = forms.get(form_code) if isinstance(forms, dict) else None
    if not isinstance(form, dict):
        return []
    entries: list[Any] = [form.get("fields")]
    components = form.get("components")
    if isinstance(components, dict) and component:
        normalized_components = {
            _normalise_component(key): value for key, value in components.items()
        }
        component_entry = normalized_components.get(_normalise_component(component))
        if isinstance(component_entry, dict):
            entries.append(component_entry.get("fields"))
    result: list[FieldDefinition] = []
    for fields in entries:
        if not isinstance(fields, list):
            continue
        for item in fields:
            if not isinstance(item, dict):
                continue
            code = str(item.get("fieldCode") or item.get("field_code") or "").strip()
            if not code:
                continue
            definition = normalize_field(item, f"manifest:{path.name}")
            definition.props["_contract_explicit_fixed_type"] = "fixedType" in item
            definition.props["_contract_explicit_field_type"] = "fieldType" in item
            result.append(definition)
    return result


@dataclass(frozen=True, slots=True)
class FieldContract:
    """Stable business fields resolved for one opened form."""

    definitions: tuple[FieldDefinition, ...]
    runtime_codes: frozenset[str]

    @property
    def source_fields(self) -> tuple[tuple[str, str, bool], ...]:
        return tuple(
            (field.field_code, field.field_name or field.field_code, False)
            for field in self.definitions
        )


class FieldContractResolver:
    """Resolve source, UIM, and manifest metadata without using runtime DOM ids."""

    def __init__(
        self,
        page,
        *,
        form_code: str = "",
        component: str = "",
        source_fields: Iterable[tuple[str, str, bool]] = (),
        manifest_path: Path | None = None,
        api_factory: Callable[[Any], RuntimeApi] = RuntimeApi,
    ):
        self.page = page
        self.form_code = str(form_code or os.getenv("EI_FORM_CODE", "")).strip()
        self.component = str(component or os.getenv("EI_COMPONENT", "")).strip()
        self._source_fields = tuple(source_fields)
        configured_manifest = os.getenv("EI_FIELD_CONTRACT_MANIFEST", "").strip()
        self.manifest_path = Path(configured_manifest) if configured_manifest else (
            manifest_path or _default_manifest_path()
        )
        self.api_factory = api_factory
        self._contract: FieldContract | None = None

    def resolve(self) -> FieldContract:
        if self._contract is not None:
            return self._contract
        source = _source_definitions(self._source_fields)
        runtime: list[FieldDefinition] = []
        if self.form_code:
            try:
                runtime = extract_runtime_fields(
                    self.api_factory(self.page).get_form_config(self.form_code)
                )
            except Exception:
                # Source/manifest mapping remains a valid fallback. A later runtime
                # value readback still proves persisted values when the endpoint works.
                runtime = []
        manifest = _manifest_fields(self.manifest_path, self.form_code, self.component)
        merged = {field.field_code: field for field in merge_definitions(source, runtime)}
        for override in manifest:
            existing = merged.get(override.field_code)
            if existing is None:
                merged[override.field_code] = override
                continue
            merged[override.field_code] = replace(
                existing,
                field_name=override.field_name or existing.field_name,
                field_type=(
                    override.field_type
                    if override.props.get("_contract_explicit_field_type")
                    else existing.field_type
                ),
                fixed_type=(
                    override.fixed_type
                    if override.props.get("_contract_explicit_fixed_type")
                    else existing.fixed_type
                ),
                source=override.source,
            )
        definitions = tuple(sorted(
            merged.values(), key=lambda item: (item.sort_order or 10**9, item.field_code)
        ))
        runtime_codes = frozenset(
            field.field_code for field in definitions if field.is_runtime
        )
        self._contract = FieldContract(definitions, runtime_codes)
        return self._contract

    def runtime_data(self, business_id: str) -> Any:
        if not self.form_code or not business_id:
            return None
        return self.api_factory(self.page).get_form_data(self.form_code, business_id)
