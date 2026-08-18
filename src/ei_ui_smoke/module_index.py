from __future__ import annotations

import argparse
import html
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .module_resolver import FORM_CODE_PATTERNS
from .project_layout import resolve_view_root


ENTRY_NAMES = {"index.vue", "list.vue"}
LABEL_PATTERNS = (
    re.compile(r'"pageTitle"\s*:\s*"([^"]+)"'),
    re.compile(r"<PurvarSubTitle[^>]*\s+title=['\"]([^'\"]+)['\"]", re.I),
    re.compile(r"\bpageTitle\s*[:=]\s*['\"]([^'\"]+)['\"]"),
)
BUTTON_PATTERN = re.compile(
    r"<(?:el-button|button)\b[^>]*>(.*?)</(?:el-button|button)>",
    re.I | re.S,
)
TITLED_ACTION_PATTERN = re.compile(
    r"<PurvarExport\b[^>]*\btitle=['\"]([^'\"]+)['\"][^>]*/?>",
    re.I | re.S,
)
NON_PAGE_ACTIONS = {"保存", "确定", "取消", "关闭", "提交"}
DIALOG_ACTION_PREFIXES = (
    "新增", "编辑", "查看", "详情", "配置", "设置", "分配", "选择",
    "导入", "导出", "上传", "下载", "审批", "审核", "处理", "维护",
)


@dataclass(frozen=True, slots=True)
class ModuleItem:
    id: str
    name: str
    path: tuple[str, ...]
    source_file: str = ""
    component: str = ""
    route: str = ""
    form_code: str = ""
    runnable: bool = False
    requires_business_id: bool = False
    supports_add: bool = False
    operation: str = ""
    operation_path: tuple[str, ...] = ()
    permission_codes: tuple[str, ...] = ()


def modules_from_menu(payload: object, source_root: Path) -> list[ModuleItem]:
    """Convert getUserFuncPerm response data to the same tree model used by the launcher."""
    data = payload
    while isinstance(data, dict) and not isinstance(data.get("funcPerm"), list):
        next_data = data.get("data")
        if next_data is data or not isinstance(next_data, (dict, list)):
            break
        data = next_data
    routes = data.get("funcPerm", []) if isinstance(data, dict) else []
    source_items = discover_modules(source_root)
    by_component = {
        _source_identity(item): item
        for item in source_items
        if item.component and not item.operation
    }
    actions_by_component: dict[str, list[ModuleItem]] = {}
    for item in source_items:
        if item.component and item.operation:
            actions_by_component.setdefault(_source_identity(item), []).append(item)
    granted_button_codes = (
        set(payload.get("_buttonCodes", [])) if isinstance(payload, dict) and "_buttonCodes" in payload
        else None
    )
    result = [ModuleItem("ALL", "全部模块", ("ALL",), runnable=True)]

    def visit(nodes: list[object], parents: tuple[str, ...], parent_route: str = "") -> None:
        for position, raw in enumerate(nodes):
            if not isinstance(raw, dict):
                continue
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            if meta.get("hidden") is True:
                continue
            name = str(meta.get("title") or raw.get("title") or raw.get("name") or raw.get("funcCode") or "未命名模块").strip()
            path = str(raw.get("path") or "").strip()
            route = _join_route(parent_route, path)
            component = str(raw.get("component") or "").strip()
            source = None if _is_container_component(component) else _resolve_source_item(
                component, route, str(raw.get("funcCode") or ""), by_component
            )
            children = raw.get("children") if isinstance(raw.get("children"), list) else []
            item_path = parents + (name,)
            item_id = str(raw.get("funcCode") or raw.get("id") or route or f"menu-{position}")
            item = ModuleItem(
                id=item_id,
                name=name,
                path=item_path,
                source_file=source.source_file if source else "",
                component=component,
                route=route,
                form_code=source.form_code if source else "",
                runnable=bool(route and (not children or source is not None)),
                supports_add=source.supports_add if source else False,
            )
            result.append(item)
            visit(children, item_path, route)

    visit(routes, ())
    result = _demote_directory_nodes(result)
    detail_trees = payload.get("_detailTrees", {}) if isinstance(payload, dict) else {}
    detail_owners = {
        "ZGJJ_": ("自管基金", "/selfManagedFunds/detail"),
        "CGJJ_": ("参股基金", "/equityAffiliateFund/detail"),
        "JJGL_": ("参股基金", "/equityAffiliateFund/detail"),
    }
    for prefix, nodes in detail_trees.items() if isinstance(detail_trees, dict) else []:
        if not isinstance(nodes, list):
            continue
        if prefix in detail_owners:
            owner_name, detail_route = detail_owners[prefix]
            named_owners = [
                item for item in result
                if item.id != "ALL"
                and not item.operation
                and item.name == owner_name
                and item.path[:1] == (owner_name,)
            ]
            owner = max(
                named_owners,
                key=lambda item: (bool(item.source_file), len(item.path)),
                default=None,
            )
        else:
            normalized_prefix = _normalize_component(prefix)
            owner_candidates: list[tuple[tuple[int, int, int, int], ModuleItem]] = []
            for item in result:
                if item.id == "ALL" or item.operation:
                    continue
                normalized_component = _normalize_component(item.component)
                normalized_route = _normalize_component(item.route)
                component_score = (
                    3 if normalized_component == normalized_prefix
                    else 2 if normalized_component.startswith(normalized_prefix + "/")
                    else 0
                )
                route_score = (
                    2 if normalized_route == normalized_prefix
                    else 1 if normalized_route.startswith(normalized_prefix + "/")
                    else 0
                )
                if component_score or route_score:
                    owner_candidates.append((
                        (bool(item.source_file), component_score, route_score, len(item.path)), item
                    ))
            owner = max(owner_candidates, key=lambda candidate: candidate[0])[1] if owner_candidates else None
            owner_name = owner.name if owner else prefix
            detail_route = f"/{prefix.strip('_')}/detail"
        owner_path = owner.path if owner else (owner_name,)
        _append_detail_modules(result, nodes, owner_path + ("详情",), detail_route, prefix, by_component)
    detail_father_trees = payload.get("_detailFatherTrees", []) if isinstance(payload, dict) else []
    for tree in detail_father_trees if isinstance(detail_father_trees, list) else []:
        if not isinstance(tree, dict):
            continue
        nodes = tree.get("nodes")
        source_component = str(tree.get("sourceComponent") or "")
        father_id = str(tree.get("fatherId") or "")
        if not isinstance(nodes, list) or not source_component or not father_id:
            continue
        owner = _detail_father_tree_owner(result, source_component)
        if owner is None or not owner.route:
            continue
        _append_detail_modules(
            result,
            nodes,
            owner.path + ("详情",),
            owner.route.rstrip("/") + "/detail",
            f"father:{father_id}",
            by_component,
        )
    page_items = list(result)
    page_bindings: list[tuple[ModuleItem, ModuleItem | None, ModuleItem | None]] = []
    for item in page_items:
        if (
            item.id == "ALL"
            or not item.component
            or (not item.route and not item.requires_business_id)
        ):
            continue
        source = _resolve_source_item(item.component, item.route, item.id, by_component)
        structural_source = source or _resolve_source_item(
            item.component, item.route, item.id, by_component, allow_legacy_alias=True
        )
        page_bindings.append((item, source, structural_source))

    owner_indexes: dict[tuple[str, str, str, str], int] = {}
    owner_ranks: dict[tuple[str, str, str, str], tuple[bool, bool, bool, int]] = {}
    for index, (item, source, structural_source) in enumerate(page_bindings):
        if structural_source is None:
            continue
        identity = _operation_owner_identity(item, structural_source)
        rank = (
            source is not None,
            bool(item.source_file),
            _normalize_component(item.component) == _normalize_component(structural_source.component),
            len(item.path),
        )
        if identity not in owner_ranks or rank > owner_ranks[identity]:
            owner_indexes[identity] = index
            owner_ranks[identity] = rank

    for index, (item, source, structural_source) in enumerate(page_bindings):
        if structural_source is None:
            continue
        identity = _operation_owner_identity(item, structural_source)
        if owner_indexes.get(identity) != index:
            continue
        source_actions = actions_by_component.get(_source_identity(structural_source), [])
        structural_actions = [action for action in source_actions if action.operation_path]
        action_items = structural_actions if source is None else [
            action for action in source_actions
            if granted_button_codes is None
            or not action.permission_codes
            or bool(granted_button_codes.intersection(action.permission_codes))
        ]
        for position, action in enumerate(action_items):
            operation_tail = (
                action.path[-(len(action.operation_path) + 1):]
                if action.operation_path else (action.operation,)
            )
            result.append(ModuleItem(
                id=f"{item.id}::action::{position}",
                name=action.operation,
                path=item.path + operation_tail,
                source_file=action.source_file or item.source_file,
                component=action.component or item.component,
                route=item.route,
                form_code=action.form_code or item.form_code,
                # A routable page may also own child menu nodes. Keep the page
                # itself as a directory while allowing its explicit operations.
                runnable=item.runnable or (bool(item.route) and not item.requires_business_id),
                requires_business_id=item.requires_business_id,
                operation=action.operation,
                operation_path=action.operation_path,
                permission_codes=action.permission_codes,
            ))
    return result


def _demote_directory_nodes(items: list[ModuleItem]) -> list[ModuleItem]:
    """Demote pure containers while preserving parent nodes backed by real pages."""
    paths = [item.path for item in items if item.id != "ALL" and not item.operation]
    return [
        replace(item, runnable=False, supports_add=False)
        if item.id != "ALL" and not item.source_file and any(
            len(path) > len(item.path) and path[: len(item.path)] == item.path
            for path in paths
        )
        else item
        for item in items
    ]


def _append_detail_modules(
    result: list[ModuleItem], nodes: list[object], parents: tuple[str, ...],
    detail_route: str, prefix: str, by_component: dict[str, ModuleItem],
) -> None:
    for position, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            continue
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        name = str(meta.get("title") or raw.get("name") or raw.get("funcCode") or "未命名详情模块").strip()
        component = str(raw.get("component") or "").strip()
        source = _resolve_source_item(
            component, detail_route, str(raw.get("funcCode") or ""), by_component
        )
        item_path = parents + (name,)
        item_id = f"detail:{prefix}:{raw.get('funcCode') or raw.get('id') or position}"
        result.append(ModuleItem(
            id=item_id, name=name, path=item_path,
            source_file=source.source_file if source else "", component=component,
            route=detail_route, form_code=source.form_code if source else "",
            runnable=False, requires_business_id=True,
        ))
        children = raw.get("children") if isinstance(raw.get("children"), list) else []
        _append_detail_modules(result, children, item_path, detail_route, prefix, by_component)


def _detail_father_tree_owner(
    items: list[ModuleItem], source_component: str,
) -> ModuleItem | None:
    """Resolve a father-ID tree owner from the source component, never from its title."""
    target = _normalize_component(source_component)
    if not target:
        return None
    candidates = [
        item for item in items
        if item.id != "ALL"
        and not item.operation
        and not _is_container_component(item.component)
        and (component := _normalize_component(item.component))
        and target.startswith(component + "/")
    ]
    if not candidates:
        return None
    best_depth = max(len(_normalize_component(item.component)) for item in candidates)
    best = [
        item for item in candidates
        if len(_normalize_component(item.component)) == best_depth
    ]
    return best[0] if len(best) == 1 else None


def _normalize_component(value: str) -> str:
    normalized = value.replace("\\", "/").strip().removeprefix("@/").removeprefix("/")
    normalized = (
        normalized.removeprefix("srcei/views/")
        .removeprefix("src/views/")
        .removeprefix("views/")
    )
    normalized = re.sub(r"\.vue$", "", normalized, flags=re.I)
    return normalized.removesuffix("/index").lower()


def _is_container_component(value: str) -> bool:
    normalized = value.replace("\\", "/").strip().lower().removeprefix("@/").removeprefix("/")
    return normalized in {"layout", "routerview", "router-view", "parentview", "parent-view"}


def _compact_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _source_identity(item: ModuleItem) -> str:
    source_file = item.source_file.replace("\\", "/").lower()
    source_root = source_file.split("/", 1)[0] if "/" in source_file else ""
    return f"{source_root}:{_normalize_component(item.component)}"


def _operation_owner_identity(item: ModuleItem, source: ModuleItem) -> tuple[str, str, str, str]:
    source_file = source.source_file.replace("\\", "/").lower()
    execution_scope = item.id if item.requires_business_id else ""
    route = item.route.replace("\\", "/").strip().rstrip("/").lower()
    return source_file, _source_identity(source), route, execution_scope


def _resolve_source_item(
    component: str,
    route: str,
    func_code: str,
    by_component: dict[str, ModuleItem],
    allow_legacy_alias: bool = False,
) -> ModuleItem | None:
    """Resolve runtime aliases conservatively; ambiguous candidates stay unmatched."""
    raw_component = component.replace("\\", "/").strip().lower().removeprefix("@/").removeprefix("/")
    required_source_root = ""
    if raw_component.startswith("srcei/views/"):
        required_source_root = "srcei/"
    elif raw_component.startswith("src/views/"):
        required_source_root = "src/"

    def source_root_matches(source: ModuleItem) -> bool:
        if not required_source_root:
            return True
        source_root = source.source_file.replace("\\", "/").lower()
        if source_root.startswith(required_source_root):
            return True
        return (
            allow_legacy_alias
            and required_source_root == "srcei/"
            and source_root.startswith("src/")
        )

    normalized_component = _normalize_component(component)
    exact = [
        source for source in by_component.values()
        if _normalize_component(source.component) == normalized_component and source_root_matches(source)
    ]
    if len(exact) == 1:
        return exact[0]

    component_id = _compact_identifier(normalized_component)
    route_id = _compact_identifier(_normalize_component(route))
    route_leaf = _compact_identifier(route.rstrip("/").rsplit("/", 1)[-1])
    func_id = _compact_identifier(func_code)
    scored: list[tuple[int, ModuleItem]] = []
    for source in by_component.values():
        if not source_root_matches(source):
            continue
        source_component = _normalize_component(source.component)
        source_id = _compact_identifier(source_component)
        source_leaf = _compact_identifier(source_component.rsplit("/", 1)[-1])
        form_id = _compact_identifier(source.form_code)
        score = 0
        if func_id and form_id and func_id == form_id:
            score = max(score, 95)
        if component_id and len(component_id) >= 6 and (
            component_id.endswith(source_id) or source_id.endswith(component_id)
        ):
            score = max(score, 85)
        if route_id and len(route_id) >= 6 and (
            route_id.endswith(source_id) or source_id.endswith(route_id)
        ):
            score = max(score, 80)
        if "/" not in source_component and route_leaf and len(route_leaf) >= 6 and (
            route_leaf in source_id or source_leaf in route_leaf
        ):
            score = max(score, 70)
        if score:
            scored.append((score, source))
    if not scored:
        return None
    best_score = max(score for score, _source in scored)
    best = [source for score, source in scored if score == best_score]
    return best[0] if len(best) == 1 else None


def _join_route(parent: str, child: str) -> str:
    if not child:
        return parent
    if child.startswith(("http://", "https://")) or child.startswith("/"):
        return child
    return "/" + "/".join(part.strip("/") for part in (parent, child) if part.strip("/"))


def _words(name: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name.replace("_", " ").replace("-", " "))
    return value.strip() or name


def _label(path: Path, text: str) -> str:
    for pattern in LABEL_PATTERNS:
        match = pattern.search(text)
        if match and match.group(1).strip():
            return match.group(1).strip()
    return _words(path.parent.name if path.name.lower() in ENTRY_NAMES else path.stem)


def _form_code(text: str) -> str:
    hits: list[tuple[int, str]] = []
    for pattern, weight in FORM_CODE_PATTERNS:
        hits.extend((weight, match.strip()) for match in pattern.findall(text) if match.strip())
    return sorted(hits, reverse=True)[0][1] if hits else ""


def _supports_standard_add(text: str) -> bool:
    has_entry = bool(re.search(r"\b(?:openAddDialog|handleAdd)\b|@add\s*=", text))
    has_submit = bool(re.search(r"\b(?:dialogConfirm|handleSubmit|saveHandle)\b|@confirm\s*=", text))
    return has_entry and has_submit


def _page_actions(text: str) -> tuple[str, ...]:
    actions: list[str] = []
    for match in BUTTON_PATTERN.finditer(text):
        body = match.group(1)
        expressions = re.findall(r"{{(.*?)}}", body, flags=re.S)
        static_body = re.sub(r"{{.*?}}", " ", body, flags=re.S)
        static_body = re.sub(r"<[^>]+>", " ", static_body)
        candidates = [html.unescape(re.sub(r"\s+", " ", static_body)).strip()]
        for expression in expressions:
            candidates.extend(re.findall(r"['\"]([^'\"]{1,32})['\"]", expression))
        for candidate in candidates:
            label = re.sub(r"\s+", " ", candidate).strip()
            if (
                label
                and label not in NON_PAGE_ACTIONS
                and len(label) <= 32
                and re.search(r"[\w\u4e00-\u9fff]", label)
                and label not in actions
            ):
                actions.append(label)
    for match in TITLED_ACTION_PATTERN.finditer(text):
        label = html.unescape(match.group(1)).strip()
        if label and label not in NON_PAGE_ACTIONS and label not in actions:
            actions.append(label)
    return tuple(actions)


def _related_page_actions(page: Path, text: str) -> tuple[str, ...]:
    actions = list(_page_actions(text))
    for _component, component_text in _transparent_page_components(page, text):
        for action in _page_actions(component_text):
            if action not in actions:
                actions.append(action)
    return tuple(actions)


def _page_action_permissions(text: str) -> dict[str, tuple[str, ...]]:
    permissions: dict[str, tuple[str, ...]] = {}
    pattern = re.compile(
        r"<(?:el-button|button)\b(?P<attrs>[^>]*)>(?P<body>.*?)</(?:el-button|button)>",
        re.I | re.S,
    )
    for match in pattern.finditer(text):
        body = re.sub(r"{{.*?}}|<[^>]+>", " ", match.group("body"), flags=re.S)
        label = html.unescape(re.sub(r"\s+", " ", body)).strip()
        ancestor_attrs: list[str] = []
        for tag in re.finditer(r"<(?P<close>/)?(?P<name>[\w-]+)\b(?P<attrs>[^>]*)>", text[:match.start()]):
            name = tag.group("name").lower()
            if tag.group("close"):
                for position in range(len(ancestor_attrs) - 1, -1, -1):
                    if ancestor_attrs[position].startswith(name + "\0"):
                        ancestor_attrs.pop(position)
                        break
            elif not tag.group(0).rstrip().endswith("/>"):
                ancestor_attrs.append(name + "\0" + tag.group("attrs"))
        permission_text = " ".join(
            [entry.partition("\0")[2] for entry in ancestor_attrs] + [match.group("attrs")]
        )
        codes = tuple(dict.fromkeys(re.findall(
            r"\$hasButton\(\s*['\"]([^'\"]+)['\"]\s*\)", permission_text
        )))
        if label and codes:
            permissions[label] = codes
    return permissions


def _transparent_page_components(
    page: Path,
    text: str,
    seen: set[Path] | None = None,
) -> list[tuple[Path, str]]:
    """Follow a local component only when the entry is a transparent wrapper."""
    template = re.search(r"<template\b[^>]*>(.*?)</template>", text, flags=re.I | re.S)
    if not template:
        return []
    body = re.sub(r"<!--[\s\S]*?-->", "", template.group(1)).strip()
    root = re.fullmatch(r"<([A-Z][\w]*)\b[^>]*(?:/>|>[\s\S]*</\1>)", body)
    if not root:
        return []
    component_name = root.group(1)
    imported = re.search(
        rf"\bimport\s+{re.escape(component_name)}\s+from\s+['\"]([^'\"]+\.vue)['\"]",
        text,
    )
    if not imported or not imported.group(1).startswith(("./", "../")):
        return []
    component = (page.parent / imported.group(1)).resolve()
    seen = {page.resolve()} if seen is None else seen
    if not component.is_file() or component in seen:
        return []
    seen.add(component)
    component_text = component.read_text(encoding="utf-8-sig", errors="ignore")
    return [(component, component_text)] + _transparent_page_components(
        component, component_text, seen
    )


def _related_page_action_permissions(page: Path, text: str) -> dict[str, tuple[str, ...]]:
    permissions = _page_action_permissions(text)
    for _component, component_text in _transparent_page_components(page, text):
        permissions.update(_page_action_permissions(component_text))
    return permissions


def _resolve_view_component(views_root: Path, owner: Path, reference: str) -> Path | None:
    reference = reference.strip().removesuffix(".vue")
    if reference.startswith("@/views/"):
        candidate = views_root / reference.removeprefix("@/views/")
    elif reference.startswith("./") or reference.startswith("../"):
        candidate = owner.parent / reference
    else:
        candidate = views_root / reference
    candidate = candidate.with_suffix(".vue")
    return candidate.resolve() if candidate.is_file() else None


def _component_references(text: str) -> list[str]:
    constants: dict[str, list[str]] = {}
    for match in re.finditer(
        r"\bconst\s+([A-Za-z_$][\w$]*)\s*(?::\s*[^=;\r\n]+)?=\s*(['\"])([^'\"]+)\2",
        text,
    ):
        constants.setdefault(match.group(1), []).append(match.group(3).strip())
    static_constants = {
        name: values[0]
        for name, values in constants.items()
        if len(values) == 1 and values[0]
    }

    references: list[tuple[int, str]] = []
    for match in re.finditer(
        r"\bcomponentPath\s*:\s*(['\"])([^'\"]+)\1",
        text,
    ):
        references.append((match.start(), match.group(2)))
    for match in re.finditer(
        r"\bcomponentPath\s*:\s*([A-Za-z_$][\w$]*)\b",
        text,
    ):
        if reference := static_constants.get(match.group(1)):
            references.append((match.start(), reference))
    for match in re.finditer(
        r"\blocalComponent\s*:\s*(['\"])([^'\"]+)\1",
        text,
    ):
        references.append((match.start(), f"./{match.group(2)}"))
    return [reference for _position, reference in sorted(references)]


def _referenced_component_roots(views_root: Path, page: Path, text: str) -> list[Path]:
    roots: list[Path] = []
    for reference in _component_references(text):
        component = _resolve_view_component(views_root, page, reference)
        if component is not None and component not in roots:
            roots.append(component)
    return roots


def _referenced_components(views_root: Path, page: Path, text: str) -> list[Path]:
    pending = _referenced_component_roots(views_root, page, text)

    found: list[Path] = []
    seen: set[Path] = set()
    while pending:
        component = pending.pop(0)
        if component in seen:
            continue
        seen.add(component)
        found.append(component)
        component_text = component.read_text(encoding="utf-8-sig", errors="ignore")
        references = re.findall(
            r"\bimport\s+\w+\s+from\s+['\"]([^'\"]+\.vue)['\"]", component_text
        )
        pending.extend(
            child
            for reference in references
            if (child := _resolve_view_component(views_root, component, reference)) is not None
        )
    return found


def _dialog_titles(text: str, page_actions: tuple[str, ...]) -> tuple[str, ...]:
    direct_titles: list[str] = []
    candidates: list[tuple[int, str]] = []
    for match in re.finditer(r"\bdialogTitle\s*:\s*['\"]([^'\"]+)['\"]", text):
        title = match.group(1).strip()
        if title:
            direct_titles.append(title)
            candidates.append((match.start(), title))

    # Some dialog helpers receive titles through an options object. Only use
    # static titles paired with an explicit CRUD mode and a real page action.
    for object_match in re.finditer(r"\{([^{}]{0,500})\}", text, flags=re.S):
        body = object_match.group(1)
        title_match = re.search(r"\btitle\s*:\s*['\"]([^'\"]+)['\"]", body)
        mode_match = re.search(r"\bmode\s*:\s*['\"](?:add|edit|detail)['\"]", body)
        if not title_match or not mode_match:
            continue
        title = title_match.group(1).strip()
        if title:
            candidates.append((object_match.start() + title_match.start(), title))

    matched_titles: list[str] = []
    for _position, title in sorted(candidates):
        if (
            _dialog_outer_action(title, page_actions) in page_actions
            and title not in matched_titles
        ):
            matched_titles.append(title)
    if matched_titles:
        return tuple(matched_titles)
    return (direct_titles[0],) if direct_titles else ("新增对话框",)


def _component_section_actions(text: str) -> list[tuple[str, str]]:
    def default_title(variable: str) -> str | None:
        match = re.search(
            rf"\b{re.escape(variable)}\s*:\s*\{{.*?\bdefault\s*:\s*['\"]([^'\"]+)['\"]",
            text,
            flags=re.S,
        )
        return match.group(1).strip() if match and match.group(1).strip() else None

    boundaries: list[tuple[int, int, str | None]] = []
    for match in re.finditer(r"<PurvarSubTitle\b([^>]*)>", text, flags=re.I | re.S):
        attrs = match.group(1)
        static_title = re.search(r"(?:^|\s)title\s*=\s*(['\"])([^'\"]+)\1", attrs)
        bound_title = re.search(
            r"(?:^|\s)(?::title|v-bind:title)\s*=\s*['\"]([A-Za-z_$][\w$]*)['\"]",
            attrs,
        )
        title = (
            html.unescape(static_title.group(2)).strip()
            if static_title
            else default_title(bound_title.group(1)) if bound_title
            else None
        )
        boundaries.append((match.start(), match.end(), title or None))

    for match in re.finditer(r"{{\s*([A-Za-z_$][\w$]*Title)\s*}}", text):
        boundaries.append((match.start(), match.end(), default_title(match.group(1))))

    boundaries.sort(key=lambda boundary: (boundary[0], boundary[1]))
    actions: list[tuple[str, str]] = []
    for position, (_start, title_end, title) in enumerate(boundaries):
        if title is None:
            continue
        end = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(text)
        section_actions = _page_actions(text[title_end:end])
        for action in section_actions:
            actions.append((title, action))
    return actions


def _dialog_outer_action(title: str, page_actions: tuple[str, ...]) -> str | None:
    matches = [action for action in page_actions if title == action or title.startswith(action)]
    if matches:
        return max(matches, key=len)
    return next((action for action in DIALOG_ACTION_PREFIXES if title.startswith(action)), None)


def _dialog_section_actions(
    views_root: Path,
    page: Path,
    text: str,
    page_actions: tuple[str, ...],
) -> list[tuple[str, str, str, str]]:
    actions: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for dialog_page, dialog_text in [
        (page, text),
        *_transparent_page_components(page, text),
    ]:
        component_roots = _referenced_component_roots(views_root, dialog_page, dialog_text)
        if not component_roots:
            continue
        titles = _dialog_titles(dialog_text, page_actions)
        # Multiple roots need explicit title-to-component association. Keep the
        # legacy first context instead of cross-pairing unrelated dialogs.
        if len(component_roots) > 1:
            titles = titles[:1]
        components = _referenced_components(views_root, dialog_page, dialog_text)
        for title in titles:
            outer_action = _dialog_outer_action(title, page_actions)
            if outer_action is None:
                continue
            for component in components:
                component_text = component.read_text(encoding="utf-8-sig", errors="ignore")
                for section, action in _component_section_actions(component_text):
                    item = (outer_action, title, section, action)
                    if item not in seen:
                        seen.add(item)
                        actions.append(item)
    return actions


def discover_modules(source_root: Path) -> list[ModuleItem]:
    view_root = resolve_view_root(source_root)
    items: list[ModuleItem] = [ModuleItem("ALL", "全部模块", ("ALL",))]
    seen: set[str] = set()
    for views_root in (view_root / "src" / "views", view_root / "srcEi" / "views"):
        if not views_root.is_dir():
            continue
        files = sorted(p for p in views_root.rglob("*.vue") if p.name.lower() in ENTRY_NAMES)
        candidates: dict[
            str,
            tuple[
                tuple[int, int], ModuleItem, tuple[str, ...], list[tuple[str, str, str, str]],
                dict[str, tuple[str, ...]],
            ],
        ] = {}
        for page in files:
            relative = page.relative_to(views_root)
            key = f"{views_root.parent.name.lower()}:{relative.as_posix().lower()}"
            if key in seen or any(part.lower() in {"components", "component"} for part in relative.parts):
                continue
            seen.add(key)
            text = page.read_text(encoding="utf-8-sig", errors="ignore")
            action_permissions = _related_page_action_permissions(page, text)
            page_actions = _related_page_actions(page, text)
            directory_parts = relative.parts[:-1]
            name = _label(page, text)
            hierarchy = tuple(_words(part) for part in directory_parts[:-1]) + (name,)
            code = _form_code(text)
            item = ModuleItem(
                id=relative.with_suffix("").as_posix(),
                name=name,
                path=hierarchy,
                source_file=str(page.relative_to(view_root)),
                component=relative.with_suffix("").as_posix(),
                form_code=code,
                runnable=bool(code),
                supports_add=_supports_standard_add(text),
            )
            directory_key = relative.parent.as_posix().lower()
            score = (bool(code), page.name.lower() == "list.vue")
            if directory_key not in candidates or score > candidates[directory_key][0]:
                candidates[directory_key] = (
                    score,
                    item,
                    page_actions,
                    _dialog_section_actions(views_root, page, text, page_actions),
                    action_permissions,
                )
        for _score, item, actions, dialog_actions, action_permissions in candidates.values():
            items.append(item)
            flat_action_items = [
                ModuleItem(
                    id=f"{item.id}::action::{position}",
                    name=action,
                    path=item.path + (action,),
                    source_file=item.source_file,
                    component=item.component,
                    form_code=item.form_code,
                    runnable=item.runnable,
                    operation=action,
                    permission_codes=action_permissions.get(action, ()),
                )
                for position, action in enumerate(actions)
            ]
            dialog_action_items = [
                ModuleItem(
                    id=f"{item.id}::dialog-action::{position}",
                    name=action,
                    path=item.path + (outer_action, dialog_title, section, action),
                    source_file=item.source_file,
                    component=item.component,
                    form_code=item.form_code,
                    runnable=item.runnable,
                    operation=action,
                    operation_path=(outer_action, section, action),
                    permission_codes=action_permissions.get(outer_action, ()),
                )
                for position, (outer_action, dialog_title, section, action) in enumerate(dialog_actions)
            ]
            matched_dialog_ids: set[str] = set()
            for action_item in flat_action_items:
                items.append(action_item)
                for dialog_item in dialog_action_items:
                    if dialog_item.operation_path[:1] == (action_item.operation,):
                        items.append(dialog_item)
                        matched_dialog_ids.add(dialog_item.id)
            items.extend(
                dialog_item
                for dialog_item in dialog_action_items
                if dialog_item.id not in matched_dialog_ids
            )
    return items


def search_modules(items: list[ModuleItem], query: str) -> list[ModuleItem]:
    needle = re.sub(r"[\s_-]+", "", query).lower()
    if not needle:
        return items
    return [
        item
        for item in items
        if needle in re.sub(r"[\s_-]+", "", "/".join(item.path) + item.form_code).lower()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan ei-parent UI modules")
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    items = search_modules(discover_modules(args.source_root), args.query)
    payload = {"modules": [asdict(item) for item in items]}
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
