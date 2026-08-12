from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DynamicCollectionChild:
    field_code_template: str
    selector: str
    kind: str = "text"
    label: str = ""
    required: bool = True


@dataclass(frozen=True, slots=True)
class DynamicCollectionSpec:
    field_code: str
    mode: str
    root_selector: str
    create_selector: str
    item_selector: str
    min_rows: int
    children: tuple[DynamicCollectionChild, ...]
    form_code: str = ""
    component: str = ""


def load_dynamic_collection_specs(
    data_dir: Path,
    *,
    form_code: str = "",
    component: str = "",
) -> list[DynamicCollectionSpec]:
    path = data_dir / "dynamic_collections.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    entries = raw.get("collections") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path} 的 collections 必须是数组")
    return [
        _parse_spec(entry, path)
        for entry in entries
        if _matches(entry, form_code=form_code, component=component)
    ]


def _matches(entry: object, *, form_code: str, component: str) -> bool:
    if not isinstance(entry, dict):
        return False
    expected_form = str(entry.get("formCode") or "").strip()
    expected_component = _normalize_component(str(entry.get("component") or ""))
    actual_component = _normalize_component(component)
    if expected_form and expected_form != form_code:
        return False
    if expected_component and expected_component != actual_component:
        return False
    return bool(expected_form or expected_component)


def _parse_spec(entry: object, path: Path) -> DynamicCollectionSpec:
    if not isinstance(entry, dict):
        raise ValueError(f"{path} 的集合配置必须是对象")
    children_raw = entry.get("children")
    if not isinstance(children_raw, list) or not children_raw:
        raise ValueError(f"{path} 的集合子字段不能为空")
    children = tuple(_parse_child(child, path) for child in children_raw)
    spec = DynamicCollectionSpec(
        field_code=str(entry.get("fieldCode") or "").strip(),
        mode=str(entry.get("mode") or "").strip(),
        root_selector=str(entry.get("rootSelector") or "").strip(),
        create_selector=str(entry.get("createSelector") or "").strip(),
        item_selector=str(entry.get("itemSelector") or "").strip(),
        min_rows=int(entry.get("minRows") or 0),
        children=children,
        form_code=str(entry.get("formCode") or "").strip(),
        component=_normalize_component(str(entry.get("component") or "")),
    )
    if (
        not spec.field_code
        or spec.mode not in {"selection", "add-row"}
        or not spec.root_selector
        or not spec.create_selector
        or not spec.item_selector
        or spec.min_rows < 1
    ):
        raise ValueError(f"{path} 的动态集合配置不完整：{spec.field_code or '未命名集合'}")
    return spec


def _parse_child(value: object, path: Path) -> DynamicCollectionChild:
    if not isinstance(value, dict):
        raise ValueError(f"{path} 的集合子字段必须是对象")
    child = DynamicCollectionChild(
        field_code_template=str(value.get("fieldCodeTemplate") or "").strip(),
        selector=str(value.get("selector") or "").strip(),
        kind=str(value.get("kind") or "text").strip(),
        label=str(value.get("label") or "").strip(),
        required=bool(value.get("required", True)),
    )
    if "{index}" not in child.field_code_template or not child.selector:
        raise ValueError(f"{path} 的集合子字段缺少数组路径或 selector")
    return child


def _normalize_component(value: str) -> str:
    normalized = value.replace("\\", "/").strip().split("?", 1)[0].split("#", 1)[0]
    normalized = normalized.strip("/").removesuffix(".vue")
    normalized = normalized.removeprefix("@/")
    for prefix in ("src/views/", "srcEi/views/", "views/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return normalized
