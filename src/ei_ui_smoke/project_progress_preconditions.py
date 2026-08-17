"""Project-progress detail path helpers."""

from __future__ import annotations

def project_decision_add_module(module_name: str) -> str:
    """Derive the fixed decision prerequisite under the current detail root."""
    parts = [part.strip() for part in module_name.split("/") if part.strip()]
    try:
        detail_index = parts.index("详情")
    except ValueError as exc:
        raise AssertionError(
            "项目进度详情路径缺少“详情”节点，无法建立项目决策前置"
        ) from exc
    return "/".join(parts[: detail_index + 1] + ["投前管理", "项目决策", "新增"])
