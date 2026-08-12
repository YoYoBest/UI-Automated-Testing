from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .common_field_cases import read_xlsx_records


@dataclass(frozen=True)
class CommonDeleteCase:
    case_id: str
    scenario: str
    expected: str

    @property
    def behavior(self) -> str:
        if "取消" in self.scenario or "删除不成功" in self.expected:
            return "cancel"
        if "删除成功" in self.expected:
            return "confirm"
        # A generic case can prove that the confirmation is shown, but cannot
        # invent page-specific associations solely to trigger a business block.
        return "confirm_then_cancel"


def load_common_delete_cases(
    workbook: Path, sheet_name: str, case_ids: Iterable[str] | None = None,
) -> list[CommonDeleteCase]:
    selected = {case_id.strip() for case_id in case_ids or () if case_id.strip()}
    cases = []
    for row in read_xlsx_records(workbook, sheet_name):
        case_id = str(row.get("用例ID") or row.get("序号") or "").strip()
        if not case_id or (selected and case_id not in selected):
            continue
        cases.append(CommonDeleteCase(
            case_id=case_id,
            scenario=str(row.get("测试场景") or row.get("场景") or "删除验证").strip(),
            expected=str(row.get("预期结果") or "").strip(),
        ))
    if not cases:
        raise ValueError(f"删除页签 {sheet_name} 没有匹配的可执行用例")
    return cases
