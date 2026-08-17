from __future__ import annotations

import re
from pathlib import Path

from .module_index import _component_references, _transparent_page_components
from .project_layout import resolve_view_root


CONTROL_MARKER = re.compile(
    r"<(?:el-input|el-date-picker|el-select|el-tree-select|el-radio(?:-group)?|"
    r"el-checkbox(?:-group)?|Purvar\w+|\w*Select\w*|\w*Department\w*)\b",
    re.I,
)
FIELD_BLOCK = re.compile(
    r"<PurvarCol\b(?P<attrs>[^>]*)>(?P<body>.*?)</PurvarCol>", re.I | re.S
)
FIELD_CODE = re.compile(r"\bfield-code=['\"]([^'\"]+)['\"]", re.I)
FORM_PROP = re.compile(r"<el-form-item\b[^>]*\bprop=['\"]([^'\"]+)['\"]", re.I)
FORM_ITEM_BLOCK = re.compile(
    r"<el-form-item\b(?P<attrs>[^>]*)>(?P<body>.*?)</el-form-item>", re.I | re.S
)
V_MODEL = re.compile(r"\bv-model(?::[\w-]+)?=['\"]([^'\"]+)['\"]", re.I)
LABEL = re.compile(r"fieldLabel\(\s*['\"][^'\"]+['\"]\s*,\s*['\"]([^'\"]+)['\"]", re.I)
PLAIN_LABEL = re.compile(r"(?:^|\s)(?::?label)=['\"]([^'\"]+)['\"]", re.I)
LOCAL_COMPONENT = re.compile(r"localComponent\s*:\s*['\"]([^'\"]+)['\"]")
LOCAL_FORM_IMPORT = re.compile(
    r"import\s+(?P<name>[A-Za-z_$][\w$]*Form)\s+from\s+['\"](?P<path>\.[^'\"]+)['\"]"
)
LOCAL_COMPONENT_IMPORT = re.compile(
    r"import\s+(?P<name>[A-Za-z_$][\w$]*)\s+from\s+['\"](?P<path>\.[^'\"]+)['\"]"
)
ADD_DIALOG_HANDLER = re.compile(
    r"\b(?:open(?:Add|Create|New)[\w$]*|(?:add|create|new)[\w$]*Dialog)\s*"
    r"=\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{(?P<body>.*?)^\s*\};",
    re.I | re.M | re.S,
)
COMPONENT_PATH = re.compile(r"\bcomponentPath\s*:\s*['\"]([^'\"]+)", re.I)
TABLE_COLUMN_LABEL = re.compile(
    r"\{\s*[^{}]*\bprop\s*:\s*['\"]([^'\"]+)['\"][^{}]*\blabel\s*:\s*['\"]([^'\"]+)['\"]",
    re.I | re.S,
)
STATIC_FIELD_ARRAY = re.compile(
    r"\bconst\s+(?P<name>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*\[(?P<body>.*?)\]\s*;",
    re.S,
)
STATIC_FIELD_ENTRY = re.compile(
    r"\{(?P<body>[^{}]*)\}", re.S,
)
STATIC_FIELD_CODE = re.compile(
    r"\bfieldCode\s*:\s*['\"](?P<value>[^'\"]+)['\"]"
)
STATIC_FIELD_LABEL = re.compile(
    r"\blabel\s*:\s*['\"](?P<value>[^'\"]+)['\"]"
)
V_FOR_STATIC_ARRAY = re.compile(
    r"\bv-for=['\"]\s*(?P<item>[A-Za-z_$][\w$]*)\s+in\s+"
    r"(?P<array>[A-Za-z_$][\w$]*)\s*['\"]",
    re.I,
)


def discover_custom_form_fields(source_root: Path, component: str) -> list[tuple[str, str, bool]]:
    page = _component_file(source_root, component)
    if not page:
        return []
    text = page.read_text(encoding="utf-8-sig", errors="ignore")
    related_pages = [(page, text), *_transparent_page_components(page, text)]
    for owner, owner_text in related_pages:
        add_dialog_fields = [
            fields
            for candidate in _add_dialog_component_files(source_root, owner, owner_text)
            if (fields := _fields_from_page(candidate, set()))
        ]
        if len(add_dialog_fields) == 1:
            return add_dialog_fields[0]
        if len(add_dialog_fields) > 1:
            return []
        dialog_fields = [
            fields
            for candidate in _dialog_component_files(source_root, owner, owner_text)
            if (fields := _fields_from_page(candidate, set()))
        ]
        if len(dialog_fields) == 1:
            return dialog_fields[0]
        if len(dialog_fields) > 1:
            return []
        if fields := _fields_from_text(owner, owner_text, set()):
            return fields
    return []


def _add_dialog_component_files(source_root: Path, page: Path, text: str) -> list[Path]:
    """Resolve an explicit Add handler before considering unrelated dialogs."""
    source_family = "srcEi" if "/srcEi/" in page.as_posix() else "src"
    resolved: list[Path] = []
    for handler in ADD_DIALOG_HANDLER.finditer(text):
        component = COMPONENT_PATH.search(handler.group("body"))
        if component is None:
            continue
        candidate = _component_file(
            source_root,
            component.group(1),
            preferred_source_family=source_family,
        )
        if candidate is not None and candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _dialog_component_files(source_root: Path, page: Path, text: str) -> list[Path]:
    local_references = LOCAL_COMPONENT.findall(text)
    local_components = [
        candidate
        for reference in local_references
        if (candidate := _local_component_file(page, reference)) is not None
    ]
    if local_references:
        return list(dict.fromkeys(local_components))

    source_family = "srcEi" if "/srcEi/" in page.as_posix() else "src"
    resolved: list[Path] = []
    for reference in _component_references(text):
        if not reference.strip():
            continue
        if reference.startswith(("./", "../")):
            candidate = _local_component_file(page, reference)
        else:
            candidate = _component_file(
                source_root, reference, preferred_source_family=source_family,
            )
        if candidate is not None and candidate not in resolved:
            resolved.append(candidate)
    return resolved


def _fields_from_page(page: Path, seen: set[Path] | None = None) -> list[tuple[str, str, bool]]:
    seen = seen or set()
    page = page.resolve()
    if page in seen:
        return []
    seen.add(page)
    text = page.read_text(encoding="utf-8-sig", errors="ignore")
    for owner, owner_text in [(page, text), *_transparent_page_components(page, text)]:
        if fields := _fields_from_text(owner, owner_text, seen):
            return fields
    return []


def _fields_from_text(
    page: Path, text: str, seen: set[Path] | None = None,
) -> list[tuple[str, str, bool]]:
    if fields := _extract_fields(text):
        return fields
    seen = seen or set()
    for match in LOCAL_FORM_IMPORT.finditer(text):
        if not re.search(rf"<{re.escape(match.group('name'))}\b", text):
            continue
        child = (page.parent / match.group("path")).resolve()
        if child.suffix.lower() != ".vue":
            child = child.with_suffix(".vue")
        if child.is_file():
            child_fields = _fields_from_page(child, seen)
            if child_fields:
                return child_fields
    local_component_fields: list[list[tuple[str, str, bool]]] = []
    for match in LOCAL_COMPONENT_IMPORT.finditer(text):
        if not re.search(rf"<{re.escape(match.group('name'))}\b", text):
            continue
        child = (page.parent / match.group("path")).resolve()
        if child.suffix.lower() != ".vue":
            child = child.with_suffix(".vue")
        if child.is_file() and (child_fields := _fields_from_page(child, seen)):
            local_component_fields.append(child_fields)
    if len(local_component_fields) == 1:
        return local_component_fields[0]
    return []


def _local_component_file(page: Path, component: str) -> Path | None:
    normalized = re.sub(r"\.vue$", "", component.replace("\\", "/"))
    for candidate in (
        page.parent / f"{normalized}.vue",
        page.parent / normalized / "index.vue",
    ):
        if candidate.is_file():
            return candidate
    return None


def _extract_fields(text: str) -> list[tuple[str, str, bool]]:
    fields: list[tuple[str, str, bool]] = []
    column_labels = _table_column_labels(text)
    static_field_arrays = _static_field_arrays(text)
    expanded_loop_placeholders: set[str] = set()
    for match in FIELD_BLOCK.finditer(text):
        body = match.group("body")
        if not CONTROL_MARKER.search(body):
            continue
        loop = V_FOR_STATIC_ARRAY.search(match.group("attrs"))
        if loop and loop.group("array") in static_field_arrays:
            expanded_loop_placeholders.add(f"{loop.group('item')}.fieldCode")
            fields.extend(
                (
                    code,
                    label,
                    bool(re.search(r"<(?:QccSelect|qcc-select)\b", body, re.I)),
                )
                for code, label in static_field_arrays[loop.group("array")]
            )
            continue
        code_match = FIELD_CODE.search(match.group("attrs")) or FORM_PROP.search(body)
        if not code_match:
            continue
        label_match = LABEL.search(match.group("attrs")) or PLAIN_LABEL.search(match.group("attrs"))
        code = _normalize_field_code(code_match.group(1))
        label = label_match.group(1) if label_match else _label_from_column(code, column_labels)
        fields.append((
            code,
            label or code,
            bool(re.search(r"<(?:QccSelect|qcc-select)\b", body, re.I)),
        ))
    known_codes = {code for code, _label, _qcc in fields}
    for match in FORM_ITEM_BLOCK.finditer(text):
        attrs = match.group("attrs")
        body = match.group("body")
        if not CONTROL_MARKER.search(body):
            continue
        code_match = re.search(r"\bprop=['\"]([^'\"]+)['\"]", attrs, re.I)
        model_match = V_MODEL.search(body)
        if code_match:
            code = code_match.group(1)
        elif model_match:
            code = model_match.group(1).split(".")[-1]
        else:
            continue
        code = _normalize_field_code(code)
        if code in known_codes or code in expanded_loop_placeholders:
            continue
        label_match = PLAIN_LABEL.search(attrs)
        label = label_match.group(1) if label_match else _label_from_column(code, column_labels)
        fields.append((code, label, bool(re.search(r"<(?:QccSelect|qcc-select)\b", body, re.I))))
        known_codes.add(code)
    return fields


def _static_field_arrays(text: str) -> dict[str, list[tuple[str, str]]]:
    arrays: dict[str, list[tuple[str, str]]] = {}
    for array_match in STATIC_FIELD_ARRAY.finditer(text):
        fields: list[tuple[str, str]] = []
        for entry in STATIC_FIELD_ENTRY.finditer(array_match.group("body")):
            code_match = STATIC_FIELD_CODE.search(entry.group("body"))
            label_match = STATIC_FIELD_LABEL.search(entry.group("body"))
            if not code_match or not label_match:
                continue
            fields.append((code_match.group("value"), label_match.group("value")))
        if fields:
            arrays[array_match.group("name")] = fields
    return arrays


def _table_column_labels(text: str) -> dict[str, str]:
    return {
        prop.strip(): label.strip()
        for prop, label in TABLE_COLUMN_LABEL.findall(text)
        if prop.strip() and label.strip()
    }


def _normalize_field_code(code: str) -> str:
    normalized = (code or "").strip().strip("`")
    normalized = re.sub(r"\$\{[^}]+\}", "*", normalized)
    return normalized


def _label_from_column(code: str, labels: dict[str, str]) -> str:
    normalized = _normalize_field_code(code)
    if normalized in labels:
        return labels[normalized]
    tail = normalized.rsplit(".", 1)[-1]
    return labels.get(tail, normalized)


def _component_file(
    source_root: Path, component: str, *, preferred_source_family: str = "",
) -> Path | None:
    normalized = component.replace("\\", "/").removeprefix("@/")
    source_family = preferred_source_family
    for prefix, family in (
        ("/srcEi/views/", "srcEi"), ("srcEi/views/", "srcEi"),
        ("/src/views/", "src"), ("src/views/", "src"), ("views/", ""),
    ):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix)
            source_family = family or source_family
            break
    normalized = re.sub(r"\.vue$", "", normalized)
    view_root = resolve_view_root(source_root)
    families = (source_family,) if source_family else ("src", "srcEi")
    for family in families:
        root = view_root / family / "views"
        for candidate in (root / f"{normalized}.vue", root / normalized / "index.vue"):
            if candidate.is_file():
                return candidate
    return None
