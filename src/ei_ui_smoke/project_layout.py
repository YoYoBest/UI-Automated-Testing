from __future__ import annotations

import re
from pathlib import Path


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
