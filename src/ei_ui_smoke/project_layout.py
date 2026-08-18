from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DetailFatherTreeRequest:
    """A source-declared, read-only detail tree request."""

    father_id: str
    source_component: str


def resolve_view_root(source_root: Path) -> Path:
    source_root = source_root.resolve()
    if (source_root / "src" / "views").is_dir() and source_root.name.lower().endswith("-view"):
        return source_root
    preferred = [source_root / "ei-view", source_root / "fi-view"]
    candidates = preferred + sorted(source_root.glob("*-view"))
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "src" / "views").is_dir():
            return resolved
    raise ValueError(f"所选目录不是受支持的项目（缺少 *-view/src/views）：{source_root}")


def read_app_id(source_root: Path, default: str = "") -> str:
    env_file = resolve_view_root(source_root) / ".env"
    try:
        text = env_file.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return default
    match = re.search(r"^\s*VITE_APP_ID\s*=\s*([^\s#]+)", text, re.M)
    return match.group(1).strip("'\"") if match else default


def read_app_base_api(source_root: Path, default: str = "") -> str:
    """Read the frontend HTTP base path used by Axios requests."""
    env_file = resolve_view_root(source_root) / ".env"
    try:
        text = env_file.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError:
        return default
    match = re.search(r"^\s*VITE_APP_BASE_API\s*=\s*([^\s#]+)", text, re.M)
    return match.group(1).strip("'\"") if match else default


def discover_detail_prefixes(source_root: Path) -> tuple[str, ...]:
    view_root = resolve_view_root(source_root)
    prefixes: list[str] = []
    pattern = re.compile(r"getUserFuncPermTreeByFuncCode\(\s*APP_ID\s*,\s*['\"]([^'\"]+)['\"]")
    for path in (view_root / "src" / "views").rglob("*.vue"):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        prefixes.extend(pattern.findall(text))
    return tuple(dict.fromkeys(prefixes))


def discover_detail_father_tree_requests(source_root: Path) -> tuple[DetailFatherTreeRequest, ...]:
    """Find source-declared read-only detail-tree requests across both view roots."""
    view_root = resolve_view_root(source_root)
    request_pattern = re.compile(
        r"getUserFuncPermTree\s*\(\s*\{(?P<params>.*?)\}\s*\)", re.S
    )
    father_id_pattern = re.compile(r"\bfatherId\s*:\s*(?P<value>[^,}\s]+)")
    requests: list[DetailFatherTreeRequest] = []
    for views_root in (view_root / "src" / "views", view_root / "srcEi" / "views"):
        if not views_root.is_dir():
            continue
        for path in sorted(views_root.rglob("*.vue")):
            try:
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            for match in request_pattern.finditer(text):
                father_id = father_id_pattern.search(match.group("params"))
                if father_id is None:
                    continue
                resolved_father_id = _resolve_static_father_id(
                    text, father_id.group("value")
                )
                if not resolved_father_id:
                    continue
                source_component = path.relative_to(views_root).with_suffix("").as_posix()
                requests.append(DetailFatherTreeRequest(
                    father_id=resolved_father_id, source_component=source_component,
                ))
    return tuple(dict.fromkeys(requests))


def _resolve_static_father_id(source: str, expression: str) -> str:
    """Resolve a literal or a computed variable with one source-declared fallback."""
    literal = re.fullmatch(r"['\"]([^'\"]+)['\"]", expression)
    if literal:
        return literal.group(1)
    reference = re.fullmatch(r"(?P<name>[A-Za-z_$][\w$]*)(?:\.value)?", expression)
    if reference is None:
        return ""
    name = re.escape(reference.group("name"))
    declaration = re.search(
        rf"\b(?:const|let)\s+{name}\s*=\s*(?P<value>[^;]+);", source, re.S
    )
    if declaration is None:
        return ""
    fallback = re.search(r"\|\|\s*['\"]([^'\"]+)['\"]", declaration.group("value"))
    if fallback:
        return fallback.group(1)
    literal = re.fullmatch(r"\s*['\"]([^'\"]+)['\"]\s*", declaration.group("value"))
    return literal.group(1) if literal else ""
