from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from .module_index import _component_references, _transparent_page_components
from .project_layout import read_app_base_api, resolve_view_root


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
    r"import\s+(?P<name>[A-Za-z_$][\w$]*)(?:\s*,\s*\{[^}]*\})?\s+"
    r"from\s+['\"](?P<path>\.[^'\"]+)['\"]"
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
BRANCH_DIRECTIVE = re.compile(
    r"(?<![\w:-])(?P<directive>v-if|v-show|:required)\s*=\s*"
    r"(?P<quote>['\"])(?P<expression>.*?)(?P=quote)",
    re.I | re.S,
)
STATIC_BRANCH_FIELD_CODE = re.compile(
    r"(?<![:\w-])field-code\s*=\s*['\"]([^'\"]+)['\"]", re.I,
)
STATIC_BRANCH_PROP = re.compile(
    r"(?<![:\w-])prop\s*=\s*['\"]([^'\"]+)['\"]", re.I,
)
PRIMARY_BRANCH_V_MODEL = re.compile(
    r"(?<![:\w-])v-model\s*=\s*['\"]([^'\"]+)['\"]", re.I,
)
_MODEL_FIELD = r"(?:formData|dialogForm|form|model)\.[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*"
_STRING_LITERAL = r"(?:'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
DIRECT_FIELD_CONDITION = re.compile(
    rf"(?P<field>{_MODEL_FIELD})\s*(?P<operator>===|!==|==|!=)\s*"
    rf"(?P<literal>{_STRING_LITERAL})"
)
DIRECT_LITERAL_CONDITION = re.compile(
    rf"(?P<literal>{_STRING_LITERAL})\s*(?P<operator>===|!==|==|!=)\s*"
    rf"(?P<field>{_MODEL_FIELD})"
)
DEFAULT_IMPORT = re.compile(
    r"\bimport\s+(?P<name>[A-Za-z_$][\w$]*)\s+from\s+"
    r"(?P<quote>['\"])(?P<path>[^'\"]+)(?P=quote)"
)
LOCAL_ARROW_FUNCTION = re.compile(
    r"\b(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*"
    r"(?:async\s*)?\((?P<params>[^()]*)\)\s*=>"
)
OBJECT_ARROW_FUNCTION = re.compile(
    r"(?<![\w$])(?P<name>[A-Za-z_$][\w$]*)\s*:\s*"
    r"(?:async\s*)?\((?P<params>[^()]*)\)\s*=>"
)
REQUEST_OBJECT = re.compile(r"\brequest\s*\(\s*\{")
MEMBER_CALL_TEMPLATE = r"\b{owner}\.(?P<member>[A-Za-z_$][\w$]*)\s*\("
DETAIL_ACTIONS = frozenset({"detail", "detailbyid", "getbyid"})
CREATE_ACTIONS = frozenset({"add", "create", "insert", "save"})


@dataclass(frozen=True, slots=True)
class SourceBranchCandidate:
    """A source-proven branch driver or a conservative runtime probe hint."""

    driver_field: str
    operator: str
    value: str
    affected_field: str
    effect: str


@dataclass(frozen=True, slots=True)
class SourceDetailEndpoint:
    """A source-proven GET endpoint that reads one saved business record."""

    method: str
    path_template: str
    id_location: str
    id_query_key: str = ""
    api_base_path: str = ""


@dataclass(frozen=True, slots=True)
class SourceFormContract:
    """Static contracts collected from one uniquely selected Add form source."""

    fields: tuple[tuple[str, str, bool], ...] = ()
    branch_candidates: tuple[SourceBranchCandidate, ...] = ()
    detail_endpoints: tuple[SourceDetailEndpoint, ...] = ()


@dataclass(frozen=True, slots=True)
class _FormSource:
    path: Path
    text: str
    fields: tuple[tuple[str, str, bool], ...]


@dataclass(frozen=True, slots=True)
class _StaticRequest:
    method: str
    path_template: str
    id_location: str = ""
    id_query_key: str = ""


def discover_custom_form_fields(source_root: Path, component: str) -> list[tuple[str, str, bool]]:
    return list(discover_form_contract(source_root, component).fields)


def discover_form_branch_candidates(
    source_root: Path,
    component: str,
) -> list[SourceBranchCandidate]:
    """Return direct field branches without inferring complex Vue expressions."""
    return list(discover_form_contract(source_root, component).branch_candidates)


def discover_form_detail_endpoint(
    source_root: Path,
    component: str,
) -> SourceDetailEndpoint | None:
    """Return one source-proven detail API for the selected Add form.

    The endpoint must be a GET used by the uniquely selected form, bind its
    first function argument directly to a query/path ID, and share an exact
    resource prefix with a used Add/Create/Insert/Save request.  Ambiguous or
    computed source constructs deliberately return ``None``.
    """
    endpoints = discover_form_contract(source_root, component).detail_endpoints
    return endpoints[0] if len(endpoints) == 1 else None


def discover_form_contract(
    source_root: Path,
    component: str,
) -> SourceFormContract:
    """Collect fields, branch hints, and detail API from one source selection."""
    selected = _select_form_source(source_root, component)
    if selected is None:
        return SourceFormContract()
    detail_endpoint = _detail_endpoint_for_selected(source_root, selected)
    return SourceFormContract(
        fields=selected.fields,
        branch_candidates=tuple(_extract_branch_candidates(selected.text)),
        detail_endpoints=(detail_endpoint,) if detail_endpoint else (),
    )


def _detail_endpoint_for_selected(
    source_root: Path,
    selected: _FormSource,
) -> SourceDetailEndpoint | None:
    requests = _used_static_requests(source_root, selected)
    create_resources = {
        resource
        for request in requests
        if (resource := _create_resource_path(request))
    }
    paired: list[tuple[str, _StaticRequest]] = []
    for request in requests:
        resource = _detail_resource_path(request)
        if resource and resource in create_resources:
            paired.append((resource, request))
    resources = {resource for resource, _request in paired}
    detail_requests = {request for _resource, request in paired}
    if len(resources) != 1 or len(detail_requests) != 1:
        return None
    request = detail_requests.pop()
    return SourceDetailEndpoint(
        method=request.method,
        path_template=request.path_template,
        id_location=request.id_location,
        id_query_key=request.id_query_key,
        api_base_path=read_app_base_api(source_root),
    )


def _select_form_source(source_root: Path, component: str) -> _FormSource | None:
    page = _component_file(source_root, component)
    if not page:
        return None
    text = page.read_text(encoding="utf-8-sig", errors="ignore")
    related_pages = [(page, text), *_transparent_page_components(page, text)]
    for owner, owner_text in related_pages:
        add_dialog_sources = [
            source
            for candidate in _add_dialog_component_files(source_root, owner, owner_text)
            if (source := _form_source_from_page(candidate, set()))
        ]
        if len(add_dialog_sources) == 1:
            return add_dialog_sources[0]
        if len(add_dialog_sources) > 1:
            return None
        dialog_sources = [
            source
            for candidate in _dialog_component_files(source_root, owner, owner_text)
            if (source := _form_source_from_page(candidate, set()))
        ]
        if len(dialog_sources) == 1:
            return dialog_sources[0]
        if len(dialog_sources) > 1:
            return None
        if source := _form_source_from_text(owner, owner_text, set()):
            return source
    return None


def _add_dialog_component_files(source_root: Path, page: Path, text: str) -> list[Path]:
    """Resolve an explicit Add handler before considering unrelated dialogs."""
    source_family = "srcEi" if "/srcEi/" in page.as_posix() else "src"
    resolved: list[Path] = []
    for handler in ADD_DIALOG_HANDLER.finditer(text):
        body = handler.group("body")
        component = COMPONENT_PATH.search(body)
        local_component = LOCAL_COMPONENT.search(body)
        candidates = []
        if component is not None:
            candidates.append(_component_file(
                source_root,
                component.group(1),
                preferred_source_family=source_family,
            ))
        if local_component is not None:
            candidates.append(_local_component_file(page, local_component.group(1)))
        for candidate in candidates:
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
    source = _form_source_from_page(page, seen)
    return list(source.fields) if source else []


def _form_source_from_page(
    page: Path,
    seen: set[Path] | None = None,
) -> _FormSource | None:
    seen = seen if seen is not None else set()
    page = page.resolve()
    if page in seen:
        return None
    seen.add(page)
    text = page.read_text(encoding="utf-8-sig", errors="ignore")
    for owner, owner_text in [(page, text), *_transparent_page_components(page, text)]:
        if source := _form_source_from_text(owner, owner_text, seen):
            return source
    return None


def _fields_from_text(
    page: Path, text: str, seen: set[Path] | None = None,
) -> list[tuple[str, str, bool]]:
    source = _form_source_from_text(page, text, seen)
    return list(source.fields) if source else []


def _form_source_from_text(
    page: Path,
    text: str,
    seen: set[Path] | None = None,
) -> _FormSource | None:
    if fields := _extract_fields(text):
        return _FormSource(page.resolve(), text, tuple(fields))
    seen = seen if seen is not None else set()
    for match in LOCAL_FORM_IMPORT.finditer(text):
        if not re.search(rf"<{re.escape(match.group('name'))}\b", text):
            continue
        child = (page.parent / match.group("path")).resolve()
        if child.suffix.lower() != ".vue":
            child = child.with_suffix(".vue")
        if child.is_file():
            child_source = _form_source_from_page(child, seen)
            if child_source:
                return child_source
    local_component_sources: list[_FormSource] = []
    for match in LOCAL_COMPONENT_IMPORT.finditer(text):
        if not re.search(rf"<{re.escape(match.group('name'))}\b", text):
            continue
        child = (page.parent / match.group("path")).resolve()
        if child.suffix.lower() != ".vue":
            child = child.with_suffix(".vue")
        if child.is_file() and (child_source := _form_source_from_page(child, seen)):
            local_component_sources.append(child_source)
    if len(local_component_sources) == 1:
        return local_component_sources[0]
    return None


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


def _extract_branch_candidates(text: str) -> list[SourceBranchCandidate]:
    known_codes = _branch_field_codes(text)
    positioned: list[tuple[int, int, SourceBranchCandidate]] = []

    def collect(attrs: str, offset: int, affected_field: str) -> None:
        affected_field = _normalize_field_code(affected_field)
        if not affected_field or affected_field not in known_codes:
            return
        for directive in BRANCH_DIRECTIVE.finditer(attrs):
            expression = directive.group("expression")
            condition = _parse_direct_condition(expression)
            if condition is not None:
                branch_hints = (condition,)
            else:
                # Complex Vue expressions are not safe to evaluate statically.
                # Retain every explicit form-model dependency as a runtime probe
                # hint so a partially parseable source condition cannot silently
                # remove its driver from branch discovery.
                branch_hints = tuple(
                    (driver_field, "runtime", "")
                    for driver_field in _runtime_condition_fields(expression)
                )
            if not branch_hints:
                continue
            effect = (
                "required"
                if directive.group("directive").lower() == ":required"
                else "visible"
            )
            for driver_field, operator, value in branch_hints:
                positioned.append((
                    offset + directive.start(),
                    len(positioned),
                    SourceBranchCandidate(
                        driver_field=driver_field,
                        operator=operator,
                        value=value,
                        affected_field=affected_field,
                        effect=effect,
                    ),
                ))

    for field_block in FIELD_BLOCK.finditer(text):
        attrs = field_block.group("attrs")
        body = field_block.group("body")
        outer_field = _field_identity(attrs, body)
        collect(attrs, field_block.start("attrs"), outer_field)
        for form_item in FORM_ITEM_BLOCK.finditer(body):
            item_field = outer_field or _form_item_identity(
                form_item.group("attrs"),
                form_item.group("body"),
            )
            collect(
                form_item.group("attrs"),
                field_block.start("body") + form_item.start("attrs"),
                item_field,
            )

    for form_item in FORM_ITEM_BLOCK.finditer(text):
        collect(
            form_item.group("attrs"),
            form_item.start("attrs"),
            _form_item_identity(form_item.group("attrs"), form_item.group("body")),
        )

    candidates: list[SourceBranchCandidate] = []
    seen: set[SourceBranchCandidate] = set()
    for _position, _sequence, candidate in sorted(positioned, key=lambda item: item[:2]):
        if candidate in seen:
            continue
        seen.add(candidate)
        candidates.append(candidate)
    return candidates


def _branch_field_codes(text: str) -> set[str]:
    codes: set[str] = set()
    for field_block in FIELD_BLOCK.finditer(text):
        if not CONTROL_MARKER.search(field_block.group("body")):
            continue
        if code := _field_identity(field_block.group("attrs"), field_block.group("body")):
            codes.add(code)
    for form_item in FORM_ITEM_BLOCK.finditer(text):
        if not CONTROL_MARKER.search(form_item.group("body")):
            continue
        if code := _form_item_identity(form_item.group("attrs"), form_item.group("body")):
            codes.add(code)
    return codes


def _field_identity(attrs: str, body: str) -> str:
    code_match = STATIC_BRANCH_FIELD_CODE.search(attrs)
    if code_match:
        return _normalize_field_code(code_match.group(1))
    for form_item in FORM_ITEM_BLOCK.finditer(body):
        if prop_match := STATIC_BRANCH_PROP.search(form_item.group("attrs")):
            return _normalize_field_code(prop_match.group(1))
    model_match = PRIMARY_BRANCH_V_MODEL.search(body)
    return _model_field_identity(model_match.group(1)) if model_match else ""


def _form_item_identity(attrs: str, body: str) -> str:
    prop_match = STATIC_BRANCH_PROP.search(attrs)
    if prop_match:
        return _normalize_field_code(prop_match.group(1))
    model_match = PRIMARY_BRANCH_V_MODEL.search(body)
    return _model_field_identity(model_match.group(1)) if model_match else ""


def _model_field_identity(model: str) -> str:
    return _normalize_field_code(model).rsplit(".", 1)[-1]


def _parse_direct_condition(expression: str) -> tuple[str, str, str] | None:
    normalized = expression.strip()
    match = DIRECT_FIELD_CONDITION.fullmatch(normalized)
    if match is None:
        match = DIRECT_LITERAL_CONDITION.fullmatch(normalized)
    if match is None:
        return None
    try:
        value = ast.literal_eval(match.group("literal"))
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str):
        return None
    driver_field = match.group("field").split(".", 1)[1]
    operator = "eq" if match.group("operator") in {"===", "=="} else "neq"
    return _normalize_field_code(driver_field), operator, value


def _runtime_condition_fields(expression: str) -> list[str]:
    """Return explicit model fields from a condition we cannot safely evaluate."""
    fields = []
    for match in re.finditer(_MODEL_FIELD, expression):
        field = _normalize_field_code(match.group(0).split(".", 1)[1])
        if field and field not in fields:
            fields.append(field)
    return fields


def _used_static_requests(
    source_root: Path,
    source: _FormSource,
) -> list[_StaticRequest]:
    requests: list[_StaticRequest] = []
    local_definitions = _static_request_definitions(
        source.text,
        include_local=True,
        include_object=False,
    )
    for name, candidates in local_definitions.items():
        if _has_unqualified_call(source.text, name) and len(candidates) == 1:
            requests.append(candidates[0])

    for imported in DEFAULT_IMPORT.finditer(source.text):
        owner = imported.group("name")
        member_pattern = re.compile(
            MEMBER_CALL_TEMPLATE.format(owner=re.escape(owner))
        )
        members = {
            match.group("member")
            for match in member_pattern.finditer(source.text)
        }
        if not members:
            continue
        module = _resolve_source_import(
            source_root,
            source.path,
            imported.group("path"),
        )
        if module is None:
            continue
        try:
            module_text = module.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        definitions = _static_request_definitions(
            module_text,
            include_local=True,
            include_object=True,
        )
        for member in members:
            candidates = definitions.get(member, [])
            if len(candidates) == 1:
                requests.append(candidates[0])
    return list(dict.fromkeys(requests))


def _static_request_definitions(
    text: str,
    *,
    include_local: bool,
    include_object: bool,
) -> dict[str, list[_StaticRequest]]:
    definitions: dict[str, list[_StaticRequest]] = {}
    patterns = []
    if include_local:
        patterns.append(LOCAL_ARROW_FUNCTION)
    if include_object:
        patterns.append(OBJECT_ARROW_FUNCTION)
    positioned = sorted(
        (match.start(), match)
        for pattern in patterns
        for match in pattern.finditer(text)
    )
    for _position, match in positioned:
        body = _arrow_function_body(text, match.end())
        if body is None:
            continue
        request = _static_request_from_body(match.group("params"), body)
        if request is not None:
            definitions.setdefault(match.group("name"), []).append(request)
    for name, candidates in list(definitions.items()):
        definitions[name] = list(dict.fromkeys(candidates))
    return definitions


def _arrow_function_body(text: str, start: int) -> str | None:
    cursor = start
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text):
        return None
    if text[cursor] == "{":
        end = _matching_delimiter(text, cursor, "{", "}")
        return text[cursor + 1:end] if end is not None else None
    request_match = REQUEST_OBJECT.match(text, cursor)
    if request_match is None:
        return None
    open_paren = text.find("(", cursor, request_match.end())
    end = _matching_delimiter(text, open_paren, "(", ")")
    return text[cursor:end + 1] if end is not None else None


def _static_request_from_body(params: str, body: str) -> _StaticRequest | None:
    request_objects: list[str] = []
    for match in REQUEST_OBJECT.finditer(body):
        open_brace = match.end() - 1
        end = _matching_delimiter(body, open_brace, "{", "}")
        if end is not None:
            request_objects.append(body[open_brace + 1:end])
    if len(request_objects) != 1:
        return None
    properties = _object_properties(request_objects[0])
    method_value = _single_property(properties, "method")
    url_value = _single_property(properties, "url")
    method = _static_string(method_value).upper() if method_value else ""
    parameter_names = _parameter_names(params)
    first_parameter = parameter_names[0] if parameter_names else ""
    url_binding = _static_url_binding(url_value, first_parameter)
    if not method or url_binding is None:
        return None
    path_template, id_location = url_binding
    id_query_key = ""
    if not id_location and first_parameter:
        params_value = _single_property(properties, "params")
        id_query_key = _query_id_key(params_value, first_parameter)
        if id_query_key:
            id_location = "query"
    return _StaticRequest(
        method=method,
        path_template=path_template,
        id_location=id_location,
        id_query_key=id_query_key,
    )


def _static_url_binding(
    expression: str | None,
    first_parameter: str,
) -> tuple[str, str] | None:
    if expression is None:
        return None
    expression = expression.strip()
    literal = _static_string(expression)
    if literal:
        if not _valid_endpoint_path(literal):
            return None
        return literal, ""
    if len(expression) < 2 or expression[0] != "`" or expression[-1] != "`":
        return None
    template = expression[1:-1]
    interpolations = list(re.finditer(
        r"\$\{\s*([A-Za-z_$][\w$]*)\s*\}", template,
    ))
    if (
        len(interpolations) != 1
        or template.count("${") != 1
        or not first_parameter
        or interpolations[0].group(1) != first_parameter
    ):
        return None
    path_template = (
        template[:interpolations[0].start()]
        + "{business_id}"
        + template[interpolations[0].end():]
    )
    if (
        not _valid_endpoint_path(path_template)
        or not path_template.endswith("/{business_id}")
    ):
        return None
    return path_template, "path"


def _valid_endpoint_path(path: str) -> bool:
    return bool(path.startswith("/") and "?" not in path and "#" not in path)


def _query_id_key(expression: str | None, first_parameter: str) -> str:
    if expression is None:
        return ""
    expression = expression.strip()
    if len(expression) < 2 or expression[0] != "{":
        return ""
    end = _matching_delimiter(expression, 0, "{", "}")
    if end != len(expression) - 1:
        return ""
    matches: list[str] = []
    for entry in _split_top_level(expression[1:-1]):
        entry = entry.strip()
        shorthand = re.fullmatch(r"([A-Za-z_$][\w$]*)", entry)
        if shorthand:
            key = value = shorthand.group(1)
        else:
            explicit = re.fullmatch(
                r"(?:['\"])?(?P<key>[A-Za-z_$][\w$]*)(?:['\"])?\s*:\s*"
                r"(?P<value>[A-Za-z_$][\w$]*)",
                entry,
            )
            if explicit is None:
                continue
            key = explicit.group("key")
            value = explicit.group("value")
        if value == first_parameter and key.lower().endswith("id"):
            matches.append(key)
    return matches[0] if len(set(matches)) == 1 else ""


def _parameter_names(raw: str) -> tuple[str, ...]:
    names: list[str] = []
    for item in _split_top_level(raw):
        candidate = item.strip()
        if not candidate:
            continue
        match = re.match(r"^([A-Za-z_$][\w$]*)\??(?:\s*:|\s*=|\s*$)", candidate)
        if match is None:
            return ()
        names.append(match.group(1))
    return tuple(names)


def _object_properties(text: str) -> dict[str, list[str]]:
    properties: dict[str, list[str]] = {}
    for entry in _split_top_level(text):
        match = re.match(
            r"^\s*(?:['\"])?(?P<key>[A-Za-z_$][\w$]*)(?:['\"])?\s*:"
            r"(?P<value>.*)$",
            entry,
            re.S,
        )
        if match is None:
            continue
        properties.setdefault(match.group("key"), []).append(
            match.group("value").strip()
        )
    return properties


def _single_property(properties: dict[str, list[str]], key: str) -> str | None:
    values = properties.get(key, [])
    return values[0] if len(values) == 1 else None


def _static_string(expression: str) -> str:
    try:
        value = ast.literal_eval(expression.strip())
    except (SyntaxError, ValueError):
        return ""
    return value if isinstance(value, str) else ""


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    stack: list[str] = []
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    pairs = {"(": ")", "[": "]", "{": "}"}
    for index, char in enumerate(text):
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            continue
        if char in "'\"`":
            quote = char
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if stack and char == stack[-1]:
            stack.pop()
            continue
        if char == "," and not stack:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _matching_delimiter(
    text: str,
    start: int,
    opening: str,
    closing: str,
) -> int | None:
    if start < 0 or start >= len(text) or text[start] != opening:
        return None
    depth = 0
    quote = ""
    escaped = False
    line_comment = False
    block_comment = False
    index = start
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if char in "\r\n":
                line_comment = False
            index += 1
            continue
        if block_comment:
            if char == "*" and next_char == "/":
                block_comment = False
                index += 2
                continue
            index += 1
            continue
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char == "/" and next_char == "/":
            line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            block_comment = True
            index += 2
            continue
        if char in "'\"`":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def _has_unqualified_call(text: str, name: str) -> bool:
    return bool(re.search(rf"(?<![\w$.]){re.escape(name)}\s*\(", text))


def _resolve_source_import(
    source_root: Path,
    owner: Path,
    reference: str,
) -> Path | None:
    reference = reference.split("?", 1)[0].split("#", 1)[0]
    view_root = resolve_view_root(source_root)
    if reference.startswith(("./", "../")):
        base = owner.parent / reference
    elif reference.startswith("@/"):
        try:
            family = owner.resolve().relative_to(view_root.resolve()).parts[0]
        except (ValueError, IndexError):
            return None
        if family not in {"src", "srcEi"}:
            return None
        base = view_root / family / reference.removeprefix("@/")
    else:
        return None
    candidates = [base] if base.suffix else [
        base.with_suffix(".ts"),
        base.with_suffix(".js"),
        base.with_suffix(".vue"),
        base / "index.ts",
        base / "index.js",
    ]
    resolved_root = view_root.resolve()
    for candidate in candidates:
        candidate = candidate.resolve()
        try:
            candidate.relative_to(resolved_root)
        except ValueError:
            continue
        if candidate.is_file():
            return candidate
    return None


def _create_resource_path(request: _StaticRequest) -> str:
    if request.method not in {"POST", "PUT", "PATCH"} or request.id_location:
        return ""
    return _resource_before_action(request.path_template, CREATE_ACTIONS)


def _detail_resource_path(request: _StaticRequest) -> str:
    if request.method != "GET" or request.id_location not in {"query", "path"}:
        return ""
    if request.id_location == "query" and not request.id_query_key:
        return ""
    path = request.path_template
    if request.id_location == "path":
        suffix = "/{business_id}"
        if not path.endswith(suffix):
            return ""
        path = path[:-len(suffix)]
    return _resource_before_action(path, DETAIL_ACTIONS)


def _resource_before_action(path: str, actions: frozenset[str]) -> str:
    normalized = path.rstrip("/")
    if not _valid_endpoint_path(normalized) or "/" not in normalized[1:]:
        return ""
    resource, action = normalized.rsplit("/", 1)
    return resource if action.lower() in actions and resource else ""


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
