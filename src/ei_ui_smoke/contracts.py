from __future__ import annotations

import json
from typing import Any, Iterable

from .models import FieldDefinition, FixedType

DYNAMIC_FIELD_LABELS_KEY = "dynamicFieldLabels"

FIELD_KIND_ALIASES = {
    "TEXT": "text", "INPUT": "text", "ElInput-TEXT": "text",
    "TEXTAREA": "textarea", "PurvarTextarea-TEXTAREA": "textarea",
    "NUMBER": "number", "ElInputNumber-NUMBER": "number",
    "SELECT": "select", "PurvarCodeSelect-SELECT": "select",
    "MULTI_SELECT": "multi_select", "PurvarCodeSelect-MULTI_SELECT": "multi_select",
    "RADIO": "radio", "PurvarCodeSelect-RADIO": "radio",
    "CHECKBOX": "checkbox", "PurvarCodeSelect-CHECKBOX": "checkbox",
    "DATE": "date", "ElDatePicker-DATE": "date",
    "DATETIME": "datetime", "ElDatePicker-DATETIME": "datetime",
    "TIME": "time", "ElTimePicker-TIME": "time",
    "SWITCH": "switch", "ElSwitch-SWITCH": "switch",
    "SLIDER": "slider", "ElSlider-SLIDER": "slider",
    "RATE": "rate", "ElRate-RATE": "rate",
    "USER_SELECT": "user_select", "PurvarSelectUser-USER_SELECT": "user_select",
    "USER_SELECT_DROP": "user_select", "PurvarSelectUserDropdown-USER_SELECT_DROP": "user_select",
    "USER_SELECT_TABLE": "user_select", "ORG_SELECT_DROP": "org_select",
    "ORG_SELECT": "org_select", "PurvarDepartment-tree": "org_select",
    "PurvarDepartment-company": "org_select", "PurvarDepartment-dept": "org_select",
    "PurvarDepartment-group": "org_select",
    "TREE_SELECT": "tree_select", "PurvarTreeSelect-TREE_SELECT": "tree_select",
    "ADDRESS": "address", "PurvarAddress-ADDRESS": "address",
    "FILE_LIBRARY": "file_library", "PurvarLibrary-FILE_LIBRARY": "file_library",
    "AI_PARSE": "ai_parse", "PurvarAiParsePanel-AI_PARSE": "ai_parse",
    "FILE": "file", "PurvarUpload-FILE": "file",
    "IMAGE": "image", "PurvarUploadImg-IMAGE": "image",
    "LINK_TAG": "multi_select", "PurvarLinkTag-LINK_TAG": "multi_select",
    "LINK_TAG_SINGLE": "select", "PurvarLinkTag-LINK_TAG_SINGLE": "select",
    "FORMULA": "formula", "FormulaConfig-FORMULA": "formula",
}


def parse_json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value or not isinstance(value, str):
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def normalize_field(raw: dict[str, Any], source: str) -> FieldDefinition:
    fixed = raw.get("fixedType", 1)
    try:
        fixed_type = FixedType(int(fixed))
    except (TypeError, ValueError):
        fixed_type = FixedType.FIXED
    return FieldDefinition(
        field_code=str(raw.get("fieldCode") or raw.get("field_code") or "").strip(),
        field_name=str(raw.get("fieldName") or raw.get("displayName") or raw.get("label") or "").strip(),
        field_type=str(raw.get("fieldType") or "ElInput-TEXT").strip(),
        fixed_type=fixed_type,
        sort_order=int(raw.get("sortOrder") or 0),
        required=as_bool(raw.get("required")),
        readonly=as_bool(raw.get("readonly")),
        locked=as_bool(raw.get("locked")),
        add_visible=as_bool(raw.get("addVisible"), True),
        edit_visible=as_bool(raw.get("editVisible"), True),
        view_visible=as_bool(raw.get("viewVisible"), True),
        default_value=raw.get("defaultValue"),
        props=parse_json_object(raw.get("propsJson")),
        linkage=parse_json_object(raw.get("linkageJson")),
        source=source,
    )


def field_kind(field_type: str) -> str:
    if field_type in FIELD_KIND_ALIASES:
        return FIELD_KIND_ALIASES[field_type]
    suffix = field_type.rsplit("-", 1)[-1].upper()
    return FIELD_KIND_ALIASES.get(suffix, "unknown")


def runtime_fields(fields: Iterable[FieldDefinition]) -> list[FieldDefinition]:
    return [f for f in fields if f.field_code != DYNAMIC_FIELD_LABELS_KEY and f.is_runtime and f.view_visible]


def build_runtime_data(form_data: dict[str, Any], fields: Iterable[FieldDefinition]) -> dict[str, Any]:
    selected = runtime_fields(fields)
    result = {field.field_code: _runtime_value(form_data.get(field.field_code, "")) for field in selected}
    labels = form_data.get(DYNAMIC_FIELD_LABELS_KEY, {})
    allowed = {field.field_code for field in selected}
    filtered = {key: value for key, value in labels.items() if key in allowed} if isinstance(labels, dict) else {}
    if selected:
        result[DYNAMIC_FIELD_LABELS_KEY] = json.dumps(filtered, ensure_ascii=False, separators=(",", ":"))
    return result


def _runtime_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def remove_runtime_fields(payload: dict[str, Any], fields: Iterable[FieldDefinition], keep: Iterable[str] = ()) -> dict[str, Any]:
    result = dict(payload)
    keep_set = set(keep)
    for field in runtime_fields(fields):
        result.pop(field.field_code, None)
        for target in field.extra_bindings.values():
            if target and target not in keep_set:
                result.pop(target, None)
    result.pop(DYNAMIC_FIELD_LABELS_KEY, None)
    return result
