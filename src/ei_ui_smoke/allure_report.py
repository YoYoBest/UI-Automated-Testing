from __future__ import annotations

import os
import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class AllurePaths:
    results: Path
    report: Path


def format_allure_case_ids(case_ids: Iterable[str]) -> str:
    """Keep every selected business case ID visible in a merged Allure title."""
    values = [str(case_id).strip() for case_id in case_ids]
    return " / ".join(dict.fromkeys(value for value in values if value))


def format_allure_common_case_title(*, title: str, display_case_id: str) -> str:
    """Put business text and case IDs on separate Allure title lines."""
    clean_title = str(title).strip()
    clean_case_id = str(display_case_id).strip()
    if clean_title and clean_case_id:
        return f"{clean_title}\n【{clean_case_id}】"
    if clean_case_id:
        return f"【{clean_case_id}】"
    return clean_title


def set_allure_hidden_parameter(name: str, value: Any) -> None:
    """Keep a parameter available for Allure identity without cluttering the tree."""
    import allure

    hidden_mode = getattr(getattr(allure, "parameter_mode", None), "HIDDEN", None)
    if hidden_mode is None:
        allure.dynamic.parameter(name, value)
    else:
        allure.dynamic.parameter(name, value, mode=hidden_mode)


def set_allure_module_metadata(
    *,
    module_id: str,
    module_name: str,
    form_code: str = "",
    test_title: str = "UI 冒烟测试",
    sub_suite_override: str = "",
) -> None:
    import allure

    labels = tuple(part.strip() for part in module_name.split("/") if part.strip())
    display_name = labels[-1] if labels else module_id
    allure.dynamic.title(f"{display_name} - {test_title}")
    allure.dynamic.parameter("module_id", module_id)
    if form_code:
        allure.dynamic.parameter("form_code", form_code, excluded=True)
    if labels:
        allure.dynamic.parent_suite(labels[0])
        allure.dynamic.feature(labels[0])
        allure.dynamic.story(display_name)
    if len(labels) > 1:
        allure.dynamic.suite(labels[-2])
        allure.dynamic.sub_suite(sub_suite_override.strip() or display_name)
    elif labels:
        allure.dynamic.suite(labels[0])


def set_allure_common_case_metadata(
    *,
    title: str,
    case_id: str,
    display_case_id: str | None = None,
    parameter_name: str = "common_field_case",
) -> None:
    """Keep parametrized common-case names compact in the Allure suites tree."""
    import allure

    display_case_id = (display_case_id or case_id).strip()
    allure.dynamic.title(
        format_allure_common_case_title(
            title=title, display_case_id=display_case_id
        )
    )
    set_allure_hidden_parameter(parameter_name, case_id)


def apply_allure_report_layout_overrides(report_dir: Path) -> None:
    styles = report_dir / "styles.css"
    if not styles.exists():
        return
    marker = "/* UI-Test-Automation: preserve test title line breaks */"
    text = styles.read_text(encoding="utf-8")
    if marker in text:
        return
    overrides = (
        f"\n{marker}\n"
        ".node__title .long-line,.test-result__name{white-space:pre-line;}\n"
        ".node__title{align-items:flex-start;}\n"
    )
    styles.write_text(text.rstrip() + overrides, encoding="utf-8")


def apply_allure_report_data_overrides(report_dir: Path) -> None:
    """Keep generated report display clean without changing raw Allure results."""
    json_files = [
        *report_dir.glob("data/*.json"),
        *report_dir.glob("data/test-cases/*.json"),
        *report_dir.glob("widgets/*.json"),
    ]
    for path in json_files:
        data = json.loads(path.read_text(encoding="utf-8"))
        if _strip_display_parameters(data):
            path.write_text(
                json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )


def _strip_display_parameters(value: Any) -> bool:
    changed = False
    if isinstance(value, dict):
        is_two_line_case = "\n【" in str(value.get("name", ""))
        if is_two_line_case:
            if value.get("parameters"):
                value["parameters"] = []
                changed = True
            if value.get("parameterValues"):
                value["parameterValues"] = []
                changed = True
        for child in value.values():
            changed = _strip_display_parameters(child) or changed
    elif isinstance(value, list):
        for child in value:
            changed = _strip_display_parameters(child) or changed
    return changed


def apply_allure_report_display_overrides(report_dir: Path) -> None:
    apply_allure_report_layout_overrides(report_dir)
    apply_allure_report_data_overrides(report_dir)


def create_allure_paths(project_root: Path, *, stamp: str | None = None) -> AllurePaths:
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    root = project_root / "artifacts" / "allure"
    paths = AllurePaths(
        results=root / f"allure-results-{stamp}",
        report=root / f"allure-report-{stamp}",
    )
    paths.results.mkdir(parents=True, exist_ok=False)
    return paths


def write_environment(results_dir: Path, values: Mapping[str, object]) -> None:
    lines = [f"{key}={value}" for key, value in values.items() if value not in (None, "")]
    (results_dir / "environment.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_allure_report(paths: AllurePaths, *, project_root: Path) -> Path:
    allure = shutil.which("allure")
    if not allure:
        raise FileNotFoundError("未找到 Allure CLI，请安装后确保 allure 命令已加入 PATH")
    completed = subprocess.run(
        [allure, "generate", str(paths.results), "-o", str(paths.report), "--clean"],
        cwd=project_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if completed.returncode:
        raise RuntimeError(f"Allure 报告生成失败：\n{completed.stdout.strip()}")
    apply_allure_report_display_overrides(paths.report)
    write_latest_paths(project_root, paths)
    return paths.report


def write_latest_paths(project_root: Path, paths: AllurePaths) -> Path:
    record = project_root / "artifacts" / "allure" / "latest_allure_paths.txt"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(f"RESULTS={paths.results.resolve()}\nREPORT={paths.report.resolve()}\n", encoding="utf-8")
    return record


def read_latest_paths(project_root: Path) -> AllurePaths:
    record = project_root / "artifacts" / "allure" / "latest_allure_paths.txt"
    values = dict(
        line.split("=", 1)
        for line in record.read_text(encoding="utf-8-sig").splitlines()
        if "=" in line
    )
    return AllurePaths(results=Path(values["RESULTS"]), report=Path(values["REPORT"]))


def open_allure_report(report_dir: Path) -> subprocess.Popen[str]:
    allure = shutil.which("allure")
    if not allure:
        raise FileNotFoundError("未找到 Allure CLI，请安装后确保 allure 命令已加入 PATH")
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(
        [allure, "open", str(report_dir)],
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
    )
