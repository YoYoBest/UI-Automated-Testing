from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .allure_report import create_allure_paths, generate_allure_report, open_allure_report, write_environment
from .common_field_cases import (
    case_selection_label,
    count_common_field_report_items,
    group_case_selections_by_sheet,
    list_xlsx_case_ids,
    list_xlsx_sheets,
    load_bound_common_cases,
    plan_common_case_transactions,
    read_xlsx_records,
)
from .execution_guard import (
    RuntimeVersion,
    capture_runtime_version,
    clear_launcher_process_record,
    register_launcher_process,
    runtime_version_changed,
    runtime_version_mismatch_message,
)
from .environment_api import (
    DEFAULT_PROBES_FILE,
    DEFAULT_VERSION_STATE_FILE,
    block_unavailable_commands,
    load_environment_api_probes,
    matching_probes,
    probe_environment_apis,
    source_revision,
    update_version_mismatch_state,
    write_environment_preflight_report,
)
from .menu_capture import capture_menu
from .module_index import ModuleItem, discover_modules, modules_from_menu, search_modules
from .project_layout import resolve_view_root
from .pytest_progress import (
    LOGICAL_COLLECTED_EVENT,
    LOGICAL_FINISHED_EVENT,
    PROGRESS_COMMAND_ENV,
    PROGRESS_FILE_ENV,
)
from .source_sync import SourceSyncError, pull_latest_source
from .urls import align_application_url, build_module_url, detail_parent_url
from .zentao import ZentaoRunResult, process_allure_failures


DEFAULT_SOURCE = r"D:\Auto_Testing\Project_Purvar\SHZY\ei-parent"
DEFAULT_COMMON_CASES_DIR = Path(r"D:\Auto_Testing\UI-Smoke-Testing\tests\Common_Test_Cases")
DEFAULT_COMMON_CASES_FILENAME = "公共用例_UI自动化.xlsx"
DEFAULT_MODULE_CASES_FILENAME = "建设项目_个性化用例.xlsx"
URL_HISTORY_LIMIT = 10
EXECUTION_MODES = (
    ("标准自动化", "standard"),
    ("快速探测", "probe"),
    ("稳定冒烟", "stable"),
)
DEFAULT_EXECUTION_MODE = "standard"
ADD_ACTION_PREFIXES = ("新增", "添加", "新建")
EDIT_ACTION_PREFIXES = ("编辑", "修改")
DELETE_ACTION_PREFIXES = ("删除", "移除", "清空")
NON_FORM_ACTION_PREFIXES = (
    *DELETE_ACTION_PREFIXES,
    "取消", "关闭", "查询", "重置", "刷新", "导出", "下载", "打印",
)
RUN_BUTTON_IDLE_TEXT = "开始执行"
ZENTAO_REQUIRED_SETTINGS = ("ZENTAO_URL", "ZENTAO_USERNAME", "ZENTAO_PASSWORD")
DEFER_SKILL_GATE_ENV = "EI_DEFER_SKILL_MAINTENANCE_GATE"


def run_button_running_text(headless: bool) -> str:
    return "测试中，请稍后…" if headless else "执行中…"


def execution_progress_percent(completed: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(min(max(completed, 0), total) * 100 / total)


def case_progress_text(
    completed: int,
    total: int | None,
    *,
    running: bool,
    discovery_completed: int | None = None,
    discovery_total: int | None = None,
    registered_total: int | None = None,
) -> str:
    if total is None:
        if running and discovery_total is not None:
            discovered = min(max(discovery_completed or 0, 0), discovery_total)
            parts = [
                f"已完成 {completed} 个用例",
                f"字段发现进度 {discovered}/{discovery_total}",
            ]
            if registered_total is not None:
                parts.append(f"已登记 {registered_total} 个用例")
            remaining = max(discovery_total - discovered, 0)
            parts.append(
                f"剩余 {remaining} 组待发现"
                if remaining
                else "等待后置字段校验登记总数"
            )
            return " · ".join(parts)
        return (
            f"已完成 {completed} 个用例 · 等待后置字段校验登记总数"
            if running else f"执行结束 · 已完成 {completed} 个用例"
        )
    percent = execution_progress_percent(completed, total)
    state = "执行中" if running else ("执行完成" if completed >= total else "执行结束")
    return f"{completed}/{total} 用例{state} · {percent}%"


class CaseProgressTracker:
    def __init__(self, command_ids) -> None:
        self._collected = {str(command_id): None for command_id in command_ids}
        self._finished: set[tuple[str, str]] = set()
        self._logical_commands: set[str] = set()
        self._omitted: set[str] = set()

    def set_collected(
        self, command_id: str, count: int, *, logical: bool = False,
    ) -> bool:
        command_id = str(command_id)
        if command_id not in self._collected or command_id in self._omitted:
            return False
        if command_id in self._logical_commands and not logical:
            return False
        if logical:
            self._logical_commands.add(command_id)
        normalized = max(0, int(count))
        changed = self._collected[command_id] != normalized
        self._collected[command_id] = normalized
        return changed

    def mark_finished(
        self, command_id: str, nodeid: str, *, logical: bool = False,
    ) -> bool:
        command_id = str(command_id)
        nodeid = str(nodeid or "").strip()
        if not nodeid or command_id in self._omitted or command_id not in self._collected:
            return False
        if command_id in self._logical_commands and not logical:
            return False
        identity = (command_id, nodeid)
        if identity in self._finished:
            return False
        self._finished.add(identity)
        return True

    def resolve_remaining(self, command_id: str, *, reason: str) -> bool:
        """Resolve interrupted logical work so progress cannot remain permanently stale."""
        command_id = str(command_id)
        if command_id not in self._logical_commands:
            return False
        count = self._collected.get(command_id)
        if count is None:
            return False
        completed = sum(
            finished_command == command_id
            for finished_command, _nodeid in self._finished
        )
        changed = False
        for index in range(completed + 1, int(count) + 1):
            changed = self.mark_finished(
                command_id,
                f"logical-{reason}-{index}",
                logical=True,
            ) or changed
        return changed

    def omit(self, command_id: str) -> bool:
        command_id = str(command_id)
        if command_id not in self._collected or command_id in self._omitted:
            return False
        self._omitted.add(command_id)
        return True

    @property
    def completed(self) -> int:
        return sum(command_id not in self._omitted for command_id, _nodeid in self._finished)

    @property
    def total(self) -> int | None:
        active_counts = [
            count for command_id, count in self._collected.items()
            if command_id not in self._omitted
        ]
        if any(count is None for count in active_counts):
            return None
        return sum(int(count) for count in active_counts)

    @property
    def registered_total(self) -> int:
        return sum(
            int(count)
            for command_id, count in self._collected.items()
            if command_id not in self._omitted and count is not None
        )

    def snapshot(self) -> tuple[int, int | None]:
        return self.completed, self.total


def read_pytest_progress_events(path: Path, offset: int = 0) -> tuple[list[dict], int]:
    try:
        with path.open("rb") as stream:
            stream.seek(offset)
            chunk = stream.read()
    except OSError:
        return [], offset
    last_newline = chunk.rfind(b"\n")
    if last_newline < 0:
        return [], offset
    lines = chunk[:last_newline + 1].decode("utf-8", errors="replace").splitlines()
    next_offset = offset + last_newline + 1
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict) and event.get("event") in {
            "collected", "finished", LOGICAL_COLLECTED_EVENT, LOGICAL_FINISHED_EVENT,
        }:
            events.append(event)
    return events, next_offset


def common_field_validation_selection_key(command_env) -> tuple[str, str, str, str]:
    """Identify one discovery/validation pair without relying on command order."""
    return (
        command_env.get("EI_COMMON_CASES_EXCEL", ""),
        command_env.get("EI_COMMON_FIELDS_MANIFEST", ""),
        command_env.get("EI_COMMON_CASES_SHEET", ""),
        command_env.get("EI_COMMON_CASE_IDS_JSON", "[]"),
    )


def common_field_discovery_identity(command_env) -> tuple[str, ...] | None:
    """Identify the physical form that produces a runtime field manifest."""
    manifest = str(command_env.get("EI_COMMON_FIELDS_MANIFEST", "")).strip()
    if not manifest:
        return None
    context_keys = (
        "EI_MODULE_ID",
        "EI_FORM_URL",
        "EI_ENTRY_URL",
        "EI_COMPONENT",
        "EI_FORM_CODE",
        "EI_ACTION",
        "EI_COMMON_FORM_ACTION",
        "EI_ACTION_PATH",
        "EI_REQUIRES_BUSINESS_ID",
        "EI_REQUIRE_ADD",
    )
    return (manifest, *(str(command_env.get(key, "")).strip() for key in context_keys))


def common_field_validation_item_count(command_env) -> int:
    """Count a validation command from the fresh discovery output, without pytest."""
    raw_case_ids = command_env.get("EI_COMMON_CASE_IDS_JSON", "[]")
    selected_case_ids = json.loads(raw_case_ids)
    if not isinstance(selected_case_ids, list):
        raise ValueError("通用用例编号配置必须是 JSON 数组")
    return count_common_field_report_items(
        Path(command_env["EI_COMMON_CASES_EXCEL"]),
        Path(command_env["EI_COMMON_FIELDS_MANIFEST"]),
        sheet_name=command_env["EI_COMMON_CASES_SHEET"],
        case_ids=selected_case_ids,
    )


def common_field_batch_plan_counts(command_env) -> tuple[int, int]:
    """Return logical bindings and physical transactions for one fresh Batch manifest."""
    selected_case_ids = json.loads(command_env.get("EI_COMMON_CASE_IDS_JSON", "[]"))
    if not isinstance(selected_case_ids, list):
        raise ValueError("通用用例编号配置必须是 JSON 数组")
    cases = load_bound_common_cases(
        Path(command_env["EI_COMMON_CASES_EXCEL"]),
        Path(command_env["EI_COMMON_FIELDS_MANIFEST"]),
        sheet_name=command_env["EI_COMMON_CASES_SHEET"],
        case_ids=selected_case_ids,
    )
    return len(cases), len(plan_common_case_transactions(cases))


def common_field_batch_timeout_seconds(
    command_env,
    default_timeout_seconds: int,
) -> int:
    """Budget Batch runtime by physical transactions, with a bounded fallback."""
    explicit = str(os.getenv("EI_COMMON_BATCH_TIMEOUT_SECONDS", "")).strip()
    if explicit:
        return max(default_timeout_seconds, int(explicit))
    base = max(30, int(os.getenv("EI_COMMON_BATCH_TIMEOUT_BASE_SECONDS", "180")))
    per_transaction = max(
        1, int(os.getenv("EI_COMMON_BATCH_TIMEOUT_PER_TRANSACTION_SECONDS", "120"))
    )
    ceiling = max(
        base, int(os.getenv("EI_COMMON_BATCH_TIMEOUT_MAX_SECONDS", "3600"))
    )
    try:
        _logical_count, transaction_count = common_field_batch_plan_counts(command_env)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        try:
            selected = json.loads(command_env.get("EI_COMMON_CASE_IDS_JSON", "[]"))
        except (TypeError, json.JSONDecodeError):
            selected = []
        transaction_count = max(1, len(selected) if isinstance(selected, list) else 1)
    dynamic_budget = min(ceiling, base + per_transaction * max(1, transaction_count))
    return max(default_timeout_seconds, dynamic_budget)


def register_discovered_validation_counts(
    command_entries, discovery_env, case_progress: CaseProgressTracker,
) -> int:
    """Register dependent validation totals immediately after discovery succeeds."""
    discovery_identity = common_field_discovery_identity(discovery_env)
    selection_key = common_field_validation_selection_key(discovery_env)
    changed = 0
    for command_id, _target, command_env, test_file in command_entries:
        same_discovery = (
            common_field_discovery_identity(command_env) == discovery_identity
            if discovery_identity is not None
            else common_field_validation_selection_key(command_env) == selection_key
        )
        if (
            test_file == "tests/test_common_field_validation.py"
            and same_discovery
            and case_progress.set_collected(
                command_id, common_field_validation_item_count(command_env)
            )
        ):
            changed += 1
    return changed


def omit_discovered_validation_commands(
    command_entries, discovery_env, case_progress: CaseProgressTracker,
) -> int:
    """Remove every validation command that depends on a failed discovery."""
    discovery_identity = common_field_discovery_identity(discovery_env)
    selection_key = common_field_validation_selection_key(discovery_env)
    changed = 0
    for command_id, _target, command_env, test_file in command_entries:
        same_discovery = (
            common_field_discovery_identity(command_env) == discovery_identity
            if discovery_identity is not None
            else common_field_validation_selection_key(command_env) == selection_key
        )
        if (
            test_file == "tests/test_common_field_validation.py"
            and same_discovery
            and case_progress.omit(command_id)
        ):
            changed += 1
    return changed


def execution_stage_label(test_file: str, sheet_name: str = "") -> str:
    labels = {
        "tests/test_common_field_discovery.py": "通用字段发现",
        "tests/test_common_field_validation.py": "通用字段验证",
        "tests/test_common_field_batch.py": "批量字段验证",
        "tests/test_build_project_add_personalized.py": "模块用例",
        "tests/test_module_action.py": "页面操作",
        "tests/test_form_smoke.py": "表单测试",
        "tests/test_module_smoke.py": "模块测试",
    }
    label = labels.get(test_file, Path(test_file).stem)
    return f"{label}（{sheet_name}）" if sheet_name else label


def maintenance_gate_failure_message(output: str) -> str:
    if (
        "SKILL_MAINTENANCE_GATE=FAIL" not in output
        and "Skill maintenance decision required:" not in output
    ):
        return ""
    affected = [
        line.strip()
        for line in output.splitlines()
        if line.strip().startswith("- ") and " -> " in line
    ]
    file_list = "\n".join(affected) or "- 请查看本批次完整日志"
    return (
        "维护Skill门禁阻止了 pytest 启动，本轮尚未执行任何业务测试。\n\n"
        f"待处理文件：\n{file_list}\n\n"
        "请先更新对应 Skill，或登记明确的 no-Skill 原因，然后重新执行。"
    )


def run_skill_maintenance_gate_check(project_root: Path) -> tuple[int, str]:
    script = project_root / "skills" / "skill-maintenance-gate" / "scripts" / "skill_gate.py"
    if not script.is_file():
        return 0, ""
    try:
        completed = subprocess.run(
            [console_python_executable(sys.executable), str(script), "check"],
            cwd=project_root, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"无法检查维护Skill门禁：{exc}"
    return completed.returncode, completed.stdout or ""


def check_maintenance_gate_once(check_gate) -> tuple[bool, str]:
    """Check once before a batch; never pause an already-started method."""
    returncode, output = check_gate()
    return returncode == 0, output


def generate_report_before_maintenance_gate(
    allure_paths,
    *,
    project_root: Path,
    check_gate,
    generate_report=generate_allure_report,
    open_report=open_allure_report,
) -> tuple[Path | None, str, bool]:
    """Preserve the completed test report even when the post-run gate fails."""
    report_error = ""
    try:
        report_dir = generate_report(allure_paths, project_root=project_root)
        open_report(report_dir)
    except (FileNotFoundError, RuntimeError) as exc:
        report_dir = None
        report_error = f"\n\nAllure：{exc}\n结果目录：{allure_paths.results}"
    return report_dir, report_error, check_gate()


def environment_flag(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def default_submit_zentao() -> bool:
    return False


def missing_zentao_settings(environment) -> list[str]:
    return [name for name in ZENTAO_REQUIRED_SETTINGS if not environment.get(name)]


def requires_add_cycle(operation: str, operation_path: tuple[str, ...] = ()) -> bool:
    """Return whether an action needs a provisioned parent CRUD lifecycle."""
    return bool(operation_path) or operation.startswith(
        ADD_ACTION_PREFIXES + DELETE_ACTION_PREFIXES
    )


def load_url_history(cache_file: Path) -> list[str]:
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    values = payload.get("systemUrls", []) if isinstance(payload, dict) else []
    return list(dict.fromkeys(
        value.strip() for value in values
        if isinstance(value, str) and value.strip()
    ))[:URL_HISTORY_LIMIT]


def save_url_history(cache_file: Path, url: str) -> list[str]:
    normalized = url.strip()
    history = load_url_history(cache_file)
    if not normalized:
        return history
    history = [normalized, *(item for item in history if item != normalized)][:URL_HISTORY_LIMIT]
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_file.with_suffix(cache_file.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"systemUrls": history}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(cache_file)
    return history


def should_open_tree_branch(item: ModuleItem, depth: int, query: str) -> bool:
    return bool(query.strip()) or depth < 2


def is_detail_page_target(item: ModuleItem) -> bool:
    """Whether an item is the read-only detail page, rather than a child action."""
    return bool(
        item.requires_business_id
        and not item.operation
        and not item.operation_path
        and "详情" in item.path
    )


def is_executable_target(item: ModuleItem) -> bool:
    return item.runnable or bool(item.operation) or is_detail_page_target(item)


def storage_dialog_defaults(project_root: Path, current_value: str) -> tuple[Path, str]:
    fallback = project_root / "artifacts"
    current = current_value.strip()
    if not current:
        return (fallback if fallback.is_dir() else project_root), ""

    path = Path(current).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    if path.is_dir():
        return path, ""
    if path.parent.is_dir():
        return path.parent, path.name
    return (fallback if fallback.is_dir() else project_root), path.name


def common_cases_dialog_defaults(project_root: Path, current_value: str) -> tuple[Path, str]:
    default_dir = project_root / "tests" / "Common_Test_Cases"
    if not default_dir.is_dir():
        default_dir = DEFAULT_COMMON_CASES_DIR
    if not default_dir.is_dir():
        default_dir = project_root
    current = current_value.strip()
    if not current:
        return default_dir, ""
    path = Path(current).expanduser()
    if not path.is_absolute():
        path = (project_root / path).resolve()
    return (path.parent if path.parent.is_dir() else default_dir), path.name


def default_common_cases_workbook(project_root: Path) -> str:
    """Return the project-local default common-case workbook path."""
    workbook = project_root / "tests" / "Common_Test_Cases" / DEFAULT_COMMON_CASES_FILENAME
    if not workbook.is_file():
        workbook = DEFAULT_COMMON_CASES_DIR / DEFAULT_COMMON_CASES_FILENAME
    return workbook.as_posix()


def default_module_cases_workbook(project_root: Path) -> str:
    workbook = project_root / "tests" / "Common_Test_Cases" / DEFAULT_MODULE_CASES_FILENAME
    if not workbook.is_file():
        workbook = DEFAULT_COMMON_CASES_DIR / DEFAULT_MODULE_CASES_FILENAME
    return workbook.as_posix()


def default_storage_state(project_root: Path) -> str:
    configured = os.getenv("EI_STORAGE_STATE", "").strip()
    if configured:
        return configured
    state_file = project_root / "artifacts" / "auth-state.json"
    return str(state_file.resolve()) if state_file.is_file() else ""


def console_python_executable(executable: str) -> str:
    """Use console Python for pytest even when the launcher runs under pythonw."""
    path = Path(executable)
    if path.name.lower() == "pythonw.exe":
        console = path.with_name("python.exe")
        if console.is_file():
            return str(console)
    return str(path)


def safe_run_log_name(module_id: str) -> str:
    """Return a Windows-safe, stable log filename for pages and action nodes."""
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", module_id).strip(" ._")
    return f"{safe or 'module'}.log"


def preferred_common_cases_sheet(sheets: list[str], current: str = "") -> str:
    """Keep a valid selection, otherwise prefer the conventional add sheet."""
    if current in sheets:
        return current
    return "新增" if "新增" in sheets else (sheets[0] if sheets else "")


def parse_sheet_selection(value: str) -> list[str]:
    """Parse the launcher display value while preserving worksheet order."""
    return list(dict.fromkeys(
        item.strip() for item in re.split(r"[、,]", value) if item.strip()
    ))


def preferred_case_sheets(
    sheets: list[str], current: list[str], *, preferred: str,
) -> list[str]:
    """Keep only explicit, still-valid worksheet selections.

    The launcher intentionally starts with no worksheet selected. An empty
    selection means that the whole workbook will run when execution starts.
    """
    del preferred
    return [sheet for sheet in sheets if sheet in current]


def effective_case_sheets(sheets: list[str], selected_sheets: list[str]) -> list[str]:
    """Expand an empty UI selection to every workbook worksheet at run time."""
    selected = [sheet for sheet in sheets if sheet in selected_sheets]
    return selected or list(sheets)


def preferred_case_ids(case_ids: list[str], current: list[str]) -> list[str]:
    return [case_id for case_id in case_ids if case_id in current]


def linked_case_id_options(
    references: list[tuple[str, str]], selected_sheets: list[str],
) -> list[tuple[str, str]]:
    """Return internal selection identities and user-facing IDs for selected sheets."""
    sheet_order = list(dict.fromkeys(sheet for sheet, _case_id in references))
    selected = set(effective_case_sheets(sheet_order, selected_sheets))
    filtered = [reference for reference in references if reference[0] in selected]
    counts: dict[str, int] = {}
    for _sheet, case_id in filtered:
        counts[case_id] = counts.get(case_id, 0) + 1
    return [
        (
            case_selection_label(sheet, case_id),
            case_id if counts[case_id] == 1 else case_selection_label(sheet, case_id),
        )
        for sheet, case_id in filtered
    ]


def command_log_name(module_id: str, test_file: str, sheet_name: str = "") -> str:
    stage = Path(test_file).stem.removeprefix("test_")
    suffix = f"_{sheet_name}" if sheet_name else ""
    return safe_run_log_name(f"{module_id}_{stage}{suffix}")


def build_pytest_command(
    executable: str, test_file: str, command_env, mode: str,
    results_dir: Path | None, *, collect_only: bool = False,
) -> list[str]:
    test_target = (
        f"{test_file}::test_selected_page_action"
        if test_file == "tests/test_module_action.py"
        else (
            f"{test_file}::test_build_project_add_personalized"
            if test_file == "tests/test_build_project_add_personalized.py"
            else test_file
        )
    )
    command = [
        console_python_executable(executable), "-m", "pytest",
        "-p", "no:cacheprovider", "-p", "ei_ui_smoke.pytest_progress",
        test_target, "--browser-smoke", "--data-mode", mode,
    ]
    if collect_only:
        command.extend(["--collect-only", "-q"])
    else:
        if results_dir is None:
            raise ValueError("执行 pytest 时必须提供 Allure results 目录")
        command.extend(["--alluredir", str(results_dir), "-v", "-s"])
    if test_file == "tests/test_common_field_discovery.py":
        command.append("--discover-common-fields")
    if test_file in {
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
        "tests/test_common_field_batch.py",
        "tests/test_common_detail_validation.py",
    }:
        command.extend([
            "--common-cases-excel", command_env["EI_COMMON_CASES_EXCEL"],
            "--common-cases-sheet", command_env["EI_COMMON_CASES_SHEET"],
            "--common-case-ids", command_env.get("EI_COMMON_CASE_IDS_JSON", "[]"),
        ])
    if test_file in {
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
        "tests/test_common_field_batch.py",
    }:
        command.extend([
            "--common-fields-manifest", command_env["EI_COMMON_FIELDS_MANIFEST"],
        ])
    if test_file == "tests/test_module_action.py" and command_env.get(
        "EI_COMMON_DELETE_CASES_EXCEL"
    ):
        command.extend([
            "--common-cases-excel", command_env["EI_COMMON_DELETE_CASES_EXCEL"],
            "--common-cases-sheet", command_env["EI_COMMON_DELETE_CASES_SHEET"],
            "--common-case-ids", command_env.get("EI_COMMON_DELETE_CASE_IDS_JSON", "[]"),
        ])
    if test_file == "tests/test_build_project_add_personalized.py":
        command.extend([
            "--module-cases-excel", command_env["EI_MODULE_CASES_EXCEL"],
            "--module-cases-sheet", command_env["EI_MODULE_CASES_SHEET"],
            "--module-case-ids", command_env.get("EI_MODULE_CASE_IDS_JSON", "[]"),
        ])
    return command


def collect_pytest_case_count(
    command: list[str], *, cwd: Path, environment, progress_file: Path,
    command_id: str, timeout_seconds: int = 60,
) -> tuple[int | None, subprocess.CompletedProcess[str]]:
    progress_file.unlink(missing_ok=True)
    collect_env = environment.copy()
    collect_env[PROGRESS_FILE_ENV] = str(progress_file)
    collect_env[PROGRESS_COMMAND_ENV] = command_id
    completed = subprocess.run(
        command, cwd=cwd, env=collect_env,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=timeout_seconds,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    events, _offset = read_pytest_progress_events(progress_file)
    counts = [
        event.get("count") for event in events
        if event.get("event") == "collected"
        and event.get("command_id") == command_id
        and isinstance(event.get("count"), int)
    ]
    count = counts[-1] if completed.returncode == 0 and counts else None
    return count, completed


def run_logged_pytest(
    command: list[str], *, cwd: Path, environment, log,
    progress_file: Path, command_id: str, timeout_seconds: int, on_event,
) -> subprocess.CompletedProcess[str]:
    progress_file.unlink(missing_ok=True)
    run_env = environment.copy()
    run_env[PROGRESS_FILE_ENV] = str(progress_file)
    run_env[PROGRESS_COMMAND_ENV] = command_id
    process = subprocess.Popen(
        command, cwd=cwd, env=run_env,
        text=True, encoding="utf-8", errors="replace",
        stdout=log, stderr=subprocess.STDOUT,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    offset = 0
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            events, offset = read_pytest_progress_events(progress_file, offset)
            for event in events:
                on_event(event)
            returncode = process.poll()
            if returncode is not None:
                events, offset = read_pytest_progress_events(progress_file, offset)
                for event in events:
                    on_event(event)
                return subprocess.CompletedProcess(command, returncode)
            if time.monotonic() >= deadline:
                process.kill()
                process.wait()
                events, _offset = read_pytest_progress_events(progress_file, offset)
                for event in events:
                    on_event(event)
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            time.sleep(0.1)
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise


def add_standard_common_field_commands(
    commands, *, mode: str, workbook: str, case_ids: str | list[str],
    project_root: Path,
):
    """Append two-stage common-field checks for matching standard form targets."""
    if mode != "standard" or not workbook.strip():
        return commands
    workbook_path = Path(workbook).expanduser()
    if not workbook_path.is_absolute():
        workbook_path = (project_root / workbook_path).resolve()
    selected_case_ids = [case_ids] if isinstance(case_ids, str) else case_ids
    selected_groups = group_case_selections_by_sheet(
        list_xlsx_case_ids(workbook_path), selected_case_ids
    )
    expanded = []
    for target, command_env, test_file in commands:
        expanded.append((target, command_env, test_file))
        # Nested dialog operations own their own focused smoke cycle.  Common
        # field checks belong only to the outer Add form, including detail
        # modules whose test command restores parent-record navigation first.
        if target.requires_business_id and target.operation_path:
            continue
        manifest = project_root / "artifacts" / "common-fields" / (
            Path(safe_run_log_name(target.id)).stem + ".json"
        )
        for selected_sheet, sheet_case_ids in selected_groups:
            if not common_case_sheet_matches_target(target, selected_sheet):
                continue
            family = common_case_sheet_family(selected_sheet)
            if family == "detail":
                sheet_case_ids = detail_case_ids_for_target(
                    workbook_path, selected_sheet, sheet_case_ids, target
                )
                if not sheet_case_ids:
                    continue
            common_env = command_env.copy()
            if common_case_sheet_family(selected_sheet) == "delete":
                common_env.update({
                    "EI_COMMON_DELETE_CASES_EXCEL": str(workbook_path),
                    "EI_COMMON_DELETE_CASES_SHEET": selected_sheet,
                    "EI_COMMON_DELETE_CASE_IDS_JSON": json.dumps(
                        sheet_case_ids, ensure_ascii=False
                    ),
                })
                expanded.append((target, common_env, "tests/test_module_action.py"))
                continue
            common_env.update({
                "EI_COMMON_CASES_EXCEL": str(workbook_path),
                "EI_COMMON_CASES_SHEET": selected_sheet,
                "EI_COMMON_CASE_IDS_JSON": json.dumps(
                    sheet_case_ids, ensure_ascii=False
                ),
                "EI_COMMON_FIELDS_MANIFEST": str(manifest),
            })
            if family == "detail":
                common_env["EI_ALLURE_SUB_SUITE"] = "详情"
            if family == "detail" and not target.operation:
                # A detail module is already the destination page.  It has no
                # synthetic "详情" button to click, so use the navigation
                # sentinel and a read-only detail-case entry point.
                common_env["EI_ACTION"] = "详情"
                common_env.pop("EI_COMMON_FORM_ACTION", None)
                expanded.append(
                    (target, common_env, "tests/test_common_detail_validation.py")
                )
                continue
            if target.operation:
                common_env["EI_COMMON_FORM_ACTION"] = target.operation
            else:
                common_env.pop("EI_COMMON_FORM_ACTION", None)
            # Discovery produces this run's manifest before validation is
            # counted or executed.  Reusing the same environment guarantees
            # both stages bind the identical action, worksheet, and selection.
            expanded.append((target, common_env.copy(), "tests/test_common_field_discovery.py"))
            # Parametrized report items share one browser/transaction cache, so
            # every bound field has an independent pytest/Allure result without
            # reopening the form or repeating a save.
            expanded.append((target, common_env, "tests/test_common_field_validation.py"))
    return expanded


def common_case_sheet_matches_target(target: ModuleItem, sheet_name: str) -> bool:
    """Keep operation-specific Excel sheets on matching operation targets."""
    family = common_case_sheet_family(sheet_name)
    operation = (target.operation or "").strip()
    if not family:
        return bool(
            (not operation and target.supports_add)
            or (
                operation.startswith(ADD_ACTION_PREFIXES)
                and not target.operation_path
            )
        )
    if family == "add":
        return bool(
            (not operation and target.supports_add)
            or (
                operation.startswith(ADD_ACTION_PREFIXES)
                and not target.operation_path
            )
        )
    if family == "edit":
        return bool(
            operation.startswith(EDIT_ACTION_PREFIXES)
            and not target.operation_path
        )
    if family == "delete":
        return bool(
            operation.startswith(DELETE_ACTION_PREFIXES)
            and not target.operation_path
        )
    if family == "detail":
        return bool(
            target.requires_business_id
            and not target.operation_path
            and "详情" in target.path
            and not operation.startswith(NON_FORM_ACTION_PREFIXES)
        )
    return True


def detail_case_ids_for_target(
    workbook_path: Path,
    sheet_name: str,
    case_ids: list[str],
    target: ModuleItem,
) -> list[str]:
    """Keep each detail-sheet row on the workflow that creates its precondition."""
    selected = set(case_ids)
    operation = (target.operation or "").strip()
    if not operation:
        accepted_features = {"页面", "详情", "查看"}
    elif operation.startswith(ADD_ACTION_PREFIXES):
        accepted_features = {"新增", "添加", "新建", "取消", "关闭", "取消/关闭"}
    elif operation.startswith(EDIT_ACTION_PREFIXES):
        accepted_features = {"编辑", "修改"}
    elif operation.startswith(("提交", "提交审批", "提交审核")):
        accepted_features = {"提交"}
    else:
        return []
    result = []
    for row in read_xlsx_records(workbook_path, sheet_name):
        case_id = str(row.get("用例ID") or row.get("序号") or "").strip()
        feature = str(row.get("功能") or "").strip()
        if case_id in selected and feature in accepted_features:
            result.append(case_id)
    return result


def common_case_sheet_family(sheet_name: str) -> str:
    normalized = re.sub(r"\s+", "", str(sheet_name or ""))
    if normalized.startswith(ADD_ACTION_PREFIXES):
        return "add"
    if normalized.startswith(EDIT_ACTION_PREFIXES):
        return "edit"
    if normalized.startswith(("详情", "查看")):
        return "detail"
    if normalized.startswith(DELETE_ACTION_PREFIXES):
        return "delete"
    return ""


def add_standard_module_case_commands(
    commands, *, mode: str, workbook: str, case_ids: str | list[str], project_root: Path,
):
    """Append configured module-specific cases to matching standard add targets."""
    selected_case_ids = [case_ids] if isinstance(case_ids, str) else case_ids
    selected_case_ids = [case_id.strip() for case_id in selected_case_ids if case_id.strip()]
    if mode != "standard" or not workbook.strip() or not selected_case_ids:
        return commands
    workbook_path = Path(workbook).expanduser()
    if not workbook_path.is_absolute():
        workbook_path = (project_root / workbook_path).resolve()
    selected_groups = group_case_selections_by_sheet(
        list_xlsx_case_ids(workbook_path), selected_case_ids
    )
    expanded = []
    added = False
    for target, command_env, test_file in commands:
        expanded.append((target, command_env, test_file))
        is_add_target = (
            (target.operation and target.operation.startswith(ADD_ACTION_PREFIXES))
            or (not target.operation and target.supports_add)
        )
        is_build_project = target.component.strip("/") == "buildProject/index"
        if added or not is_add_target or not is_build_project:
            continue
        for selected_sheet, sheet_case_ids in selected_groups:
            module_env = command_env.copy()
            module_env.update({
                "EI_MODULE_CASES_EXCEL": str(workbook_path),
                "EI_MODULE_CASES_SHEET": selected_sheet,
                "EI_MODULE_CASE_IDS_JSON": json.dumps(
                    sheet_case_ids, ensure_ascii=False
                ),
            })
            expanded.append(
                (target, module_env, "tests/test_build_project_add_personalized.py")
            )
        added = True
    return expanded


def suppress_base_commands_covered_by_excel_cases(commands):
    """Run selected Excel cases without an extra generic add/action command."""
    excel_test_files = {
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
        "tests/test_common_field_batch.py",
        "tests/test_common_detail_validation.py",
        "tests/test_build_project_add_personalized.py",
    }
    base_test_files = {
        "tests/test_module_action.py",
        "tests/test_module_smoke.py",
        "tests/test_form_smoke.py",
    }
    covered_target_ids = {
        target.id
        for target, _command_env, test_file in commands
        if test_file in excel_test_files or _command_env.get("EI_COMMON_DELETE_CASES_EXCEL")
    }
    return [
        command
        for command in commands
        if not (
            command[0].id in covered_target_ids
            and command[2] in base_test_files
            and not command[1].get("EI_COMMON_DELETE_CASES_EXCEL")
        )
    ]


def group_action_commands(commands):
    """Run all selected page actions in a single pytest/browser session."""
    grouped = []
    batch_index = None
    for target, command_env, test_file in commands:
        # Delete worksheet cases carry their own workbook selection and expand
        # into one pytest item per DELETE-* row.  The action batch serializes
        # only ordinary action metadata, so merging this command would drop
        # those environment variables and silently run a generic delete.
        if command_env.get("EI_COMMON_DELETE_CASES_EXCEL"):
            grouped.append((target, command_env, test_file))
            continue
        if test_file != "tests/test_module_action.py":
            grouped.append((target, command_env, test_file))
            continue
        action_data = {
            "module_id": target.id,
            "module_name": "/".join(target.path),
            "action": target.operation,
            "action_path": list(target.operation_path),
            "form_url": command_env.get("EI_FORM_URL", ""),
            "component": target.component,
            "form_code": target.form_code,
            "require_add": command_env.get("EI_REQUIRE_ADD", ""),
            "requires_business_id": target.requires_business_id,
        }
        if batch_index is None:
            batch_index = len(grouped)
            batch_env = command_env.copy()
            batch_env["EI_ACTION"] = "批量操作"
            batch_env["EI_ACTIONS_JSON"] = json.dumps([action_data], ensure_ascii=False)
            grouped.append((target, batch_env, test_file))
            continue
        batch_target, batch_env, batch_test_file = grouped[batch_index]
        actions = json.loads(batch_env["EI_ACTIONS_JSON"])
        actions.append(action_data)
        batch_env["EI_ACTIONS_JSON"] = json.dumps(actions, ensure_ascii=False)
        grouped[batch_index] = (batch_target, batch_env, batch_test_file)
    return grouped


def prioritize_progress_discovery_commands(commands):
    """Run manifest-producing discovery before unrelated page actions.

    Validation totals are unknowable until their fresh manifests exist.  Moving
    only the non-mutating discovery phase forward exposes an exact denominator
    early while preserving the relative order of every selected page action.
    """
    discoveries = []
    seen_discoveries: set[tuple[str, ...]] = set()
    for command in commands:
        if command[2] != "tests/test_common_field_discovery.py":
            continue
        identity = common_field_discovery_identity(command[1])
        if identity is not None and identity in seen_discoveries:
            continue
        discoveries.append(command)
        if identity is not None:
            seen_discoveries.add(identity)
    if not discoveries:
        return commands
    return discoveries + [
        command for command in commands
        if command[2] != "tests/test_common_field_discovery.py"
    ]


def suppress_pages_covered_by_actions(commands):
    """Do not run a page-level smoke when selected operations cover that page."""
    action_pages = {
        (target.route, target.component)
        for target, _env, test_file in commands
        if test_file == "tests/test_module_action.py"
    }
    return [
        command for command in commands
        if command[2] == "tests/test_module_action.py"
        or is_detail_page_target(command[0])
        or (command[0].route, command[0].component) not in action_pages
    ]


def command_target_names(commands) -> list[str]:
    """Expand grouped pytest commands back into user-selected logical targets."""
    names: list[str] = []
    for target, command_env, _test_file in commands:
        raw_actions = command_env.get("EI_ACTIONS_JSON", "")
        if raw_actions:
            try:
                actions = json.loads(raw_actions)
            except (TypeError, json.JSONDecodeError):
                actions = []
            if isinstance(actions, list) and actions:
                names.extend(
                    str(action.get("module_name") or action.get("action") or "未命名操作")
                    for action in actions
                    if isinstance(action, dict)
                )
                continue
        names.append(" / ".join(target.path) or target.name)
    return names


def command_failure_names(target, command_env, test_file: str, output: str = "") -> list[str]:
    """Map a failed pytest command back to the user-visible operations or stage."""
    base_name = " / ".join(target.path) or target.name
    stage_labels = {
        "tests/test_common_field_discovery.py": "通用字段发现",
        "tests/test_common_field_validation.py": "通用字段验证",
        "tests/test_common_field_batch.py": "批量字段验证",
        "tests/test_common_detail_validation.py": "通用详情验证",
        "tests/test_build_project_add_personalized.py": "模块用例",
    }
    if test_file in stage_labels:
        return [f"{base_name}（{stage_labels[test_file]}）"]
    raw_actions = command_env.get("EI_ACTIONS_JSON", "")
    if not raw_actions:
        return [base_name]
    try:
        actions = json.loads(raw_actions)
    except (TypeError, json.JSONDecodeError):
        return [base_name]
    if not isinstance(actions, list) or not actions:
        return [base_name]
    failed_indexes = {
        int(match) - 1
        for match in re.findall(
            r"FAILED\s+tests[/\\]test_module_action\.py::test_selected_page_action\[(\d+)-",
            output,
        )
    }
    selected = [
        action for index, action in enumerate(actions)
        if not failed_indexes or index in failed_indexes
    ]
    return [
        str(action.get("module_name") or action.get("action") or "未命名操作")
        for action in selected
        if isinstance(action, dict)
    ] or [base_name]


def resolve_selected_targets(items: list[ModuleItem], selected_items: list[ModuleItem]) -> list[ModuleItem]:
    select_all = any(item.id == "ALL" for item in selected_items)
    selected_ids = {item.id for item in selected_items}
    selected = [
        item for item in items
        if item.id != "ALL"
        and is_executable_target(item)
        and (select_all or item.id in selected_ids)
    ]
    return selected


def exclude_delete_targets(items: list[ModuleItem]) -> list[ModuleItem]:
    return [item for item in items if "删除" not in (item.operation or "")]


def target_preflight_error(item: ModuleItem) -> str:
    if (
        item.runnable
        and item.component
        and not item.source_file
    ):
        return (
            f"运行时组件 {item.component!r} 未匹配到所选源码，无法判断该模块是否应执行新增；"
            "已阻止降级为页面访问假通过。请确认源码分支与部署版本一致后重新获取菜单。"
        )
    return ""


def format_failure_message(
    failures: list[tuple[str, str, Path]],
    log_dir: Path,
    report_error: str = "",
) -> str:
    if len(failures) == 1:
        name, tail, log_file = failures[0]
        return f"失败模块：{name}\n\n{tail}\n\n完整日志：{log_file}{report_error}"
    names = "\n".join(f"{index}. {name}" for index, (name, _tail, _log) in enumerate(failures, 1))
    return f"共 {len(failures)} 个模块执行失败：\n\n{names}\n\n完整日志目录：{log_dir}{report_error}"


def format_environment_block_message(blocks, warnings) -> str:
    blocked_lines = [
        f"- {block.target_name}: {block.probe_id} 返回 HTTP {block.status}（{block.url}）"
        for block in blocks
    ]
    warning_lines = [f"- {warning}" for warning in warnings]
    sections = []
    if blocked_lines:
        sections.append(
            "环境版本不匹配，以下目标未执行且不计产品缺陷：\n" + "\n".join(blocked_lines)
        )
    if warning_lines:
        sections.append("部署版本预警：\n" + "\n".join(warning_lines))
    return "\n\n" + "\n\n".join(sections) if sections else ""


def append_environment_preflight_summary(
    sync_log_file: Path,
    preflight_report: Path,
    blocks: list,
    warnings: list,
) -> None:
    """Append the preflight outcome after source synchronization succeeds."""
    with sync_log_file.open("a", encoding="utf-8") as sync_log:
        sync_log.write(
            "\nENVIRONMENT_API_PREFLIGHT "
            f"blocked={len(blocks)} warnings={len(warnings)} "
            f"report={preflight_report}\n"
        )


class MultiSelectSheetMenu(tk.Frame):
    """Compact multi-select menu with stable internal option values."""

    def __init__(
        self, master, *, textvariable: tk.StringVar, on_change=None,
    ) -> None:
        super().__init__(
            master, background="#FFFFFF", borderwidth=0,
            highlightthickness=1, highlightbackground="#DDE2E8",
        )
        self._textvariable = textvariable
        self._button = ttk.Menubutton(
            self, textvariable=textvariable, direction="below",
            style="SheetSelect.TMenubutton",
        )
        self._button.pack(fill="both", expand=True)
        self._menu = tk.Menu(
            self._button, tearoff=False, background="#FFFFFF", foreground="#263244",
            activebackground="#EEF4FF", activeforeground="#174EA6",
            borderwidth=1, relief="solid",
        )
        self._button.configure(menu=self._menu)
        self._variables: dict[str, tk.BooleanVar] = {}
        self._display_labels: dict[str, str] = {}
        self._on_change = on_change

    def set_options(
        self, values: list[str], selected: list[str], *,
        display_labels: dict[str, str] | None = None,
    ) -> None:
        self._menu.delete(0, "end")
        self._variables.clear()
        self._display_labels = display_labels or {}
        selected_set = set(selected)
        for value in values:
            variable = tk.BooleanVar(value=value in selected_set)
            self._variables[value] = variable
            self._menu.add_checkbutton(
                label=self._display_labels.get(value, value),
                variable=variable,
                command=self._selection_changed,
            )
        self._button.configure(state="normal" if values else "disabled")
        self._sync_text()

    def option_values(self) -> list[str]:
        return list(self._variables)

    def selected_values(self) -> list[str]:
        return [name for name, variable in self._variables.items() if variable.get()]

    def _sync_text(self) -> None:
        self._textvariable.set("、".join(
            self._display_labels.get(value, value)
            for value in self.selected_values()
        ))

    def _selection_changed(self) -> None:
        self._sync_text()
        if self._on_change is not None:
            self._on_change()


class Launcher(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("冒烟测试")
        self.geometry("1400x900")
        self.minsize(900, 650)
        self.project_root = Path(__file__).resolve().parents[2]
        self.launch_version: RuntimeVersion | None = None
        try:
            self.launch_version = capture_runtime_version(self.project_root)
        except RuntimeError:
            # Execution performs the same check and shows the actionable error.
            pass
        register_launcher_process(self.project_root)
        self.protocol("WM_DELETE_WINDOW", self._close_launcher)
        self.url_history_file = self.project_root / "artifacts" / "launcher-history.json"
        self.url_history = load_url_history(self.url_history_file)
        self.configured_codes = self._load_configured_codes()
        self.items: list[ModuleItem] = []
        self.by_tree_id: dict[str, ModuleItem] = {}
        self.source = tk.StringVar(value=DEFAULT_SOURCE)
        self.system_url = tk.StringVar(value=os.getenv("EI_BASE_URL", ""))
        self.query = tk.StringVar()
        self.mode = tk.StringVar(value=DEFAULT_EXECUTION_MODE)
        self.headless = tk.BooleanVar(value=False)
        self.submit_zentao = tk.BooleanVar(value=default_submit_zentao())
        self.storage = tk.StringVar(value=default_storage_state(self.project_root))
        self.common_cases_excel = tk.StringVar(
            value=os.getenv("EI_COMMON_CASES_EXCEL")
            or default_common_cases_workbook(self.project_root)
        )
        self.common_cases_sheet = tk.StringVar()
        self.common_case_ids = tk.StringVar()
        self.module_cases_excel = tk.StringVar(
            value=os.getenv("EI_MODULE_CASES_EXCEL")
            or default_module_cases_workbook(self.project_root)
        )
        self.module_cases_sheet = tk.StringVar()
        self.module_case_ids = tk.StringVar()
        self.username = tk.StringVar(value=os.getenv("EI_USERNAME", ""))
        self.password = tk.StringVar(value=os.getenv("EI_PASSWORD", ""))
        self.status = tk.StringVar(value="请先选择源码目录并扫描模块。")
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_text = tk.StringVar(value="尚未执行")
        self.selection_status = tk.StringVar(value="已选 0 个模块")
        self._build()
        self.refresh_common_case_sheets()
        self.refresh_module_case_sheets()
        self.after_idle(self._fit_to_screen)

    def _fit_to_screen(self) -> None:
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = max(900, int(screen_width * 0.8))
        height = max(650, int(screen_height * 0.8))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _close_launcher(self) -> None:
        clear_launcher_process_record(self.project_root, pid=os.getpid())
        self.destroy()

    def _configure_styles(self) -> None:
        self.configure(background="#F4F6F8")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#F4F6F8")
        style.configure("Surface.TFrame", background="#FFFFFF")
        style.configure("Header.TFrame", background="#FFFFFF")
        style.configure("HeaderTitle.TLabel", background="#FFFFFF", foreground="#172033", font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("HeaderSub.TLabel", background="#FFFFFF", foreground="#7B8698", font=("Microsoft YaHei UI", 9))
        style.configure("AccentBar.TFrame", background="#377DFF")
        style.configure("Section.TLabel", background="#FFFFFF", foreground="#172033", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Field.TLabel", background="#FFFFFF", foreground="#657084", font=("Microsoft YaHei UI", 9))
        style.configure("Muted.TLabel", background="#FFFFFF", foreground="#657084", font=("Microsoft YaHei UI", 9))
        style.configure("Status.TLabel", background="#F4F6F8", foreground="#526071", font=("Microsoft YaHei UI", 9))
        style.configure("TEntry", padding=(9, 6), fieldbackground="#FFFFFF", bordercolor="#DDE2E8", lightcolor="#DDE2E8", darkcolor="#DDE2E8", borderwidth=1)
        style.map("TEntry", bordercolor=[("focus", "#377DFF")], lightcolor=[("focus", "#377DFF")], darkcolor=[("focus", "#377DFF")])
        style.configure("TCombobox", padding=(9, 6), fieldbackground="#FFFFFF", background="#FFFFFF", arrowcolor="#64748B", bordercolor="#DDE2E8", lightcolor="#DDE2E8", darkcolor="#DDE2E8", borderwidth=1)
        style.map("TCombobox", bordercolor=[("focus", "#377DFF")], lightcolor=[("focus", "#377DFF")], darkcolor=[("focus", "#377DFF")], fieldbackground=[("readonly", "#FFFFFF")])
        style.configure(
            "SheetSelect.TMenubutton", background="#FFFFFF", foreground="#263244",
            arrowcolor="#64748B", borderwidth=0, relief="flat", padding=(9, 6),
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "SheetSelect.TMenubutton",
            background=[("disabled", "#F1F4F7"), ("pressed", "#EEF4FF"), ("active", "#F8FAFC")],
            foreground=[("disabled", "#94A3B8")],
        )
        style.configure("TButton", background="#DCE3EA", foreground="#1F2937", bordercolor="#DCE3EA", lightcolor="#DCE3EA", darkcolor="#DCE3EA", padding=(13, 7), font=("Microsoft YaHei UI", 9), borderwidth=0, relief="flat")
        style.map("TButton", background=[("active", "#CAD4DE"), ("pressed", "#B8C5D1")], foreground=[("disabled", "#94A3B8")])
        style.configure("Primary.TButton", background="#2563EB", foreground="#FFFFFF", bordercolor="#2563EB", lightcolor="#2563EB", darkcolor="#2563EB", font=("Microsoft YaHei UI", 10, "bold"), padding=(22, 9), borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1D4ED8"), ("pressed", "#1E40AF")], foreground=[("disabled", "#D7E3FF")])
        style.configure("Accent.TButton", background="#377DFF", foreground="#FFFFFF", bordercolor="#377DFF", lightcolor="#377DFF", darkcolor="#377DFF", borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#256BE6"), ("pressed", "#1F5BC2")], foreground=[("disabled", "#D7E3FF")])
        style.configure(
            "Execution.Horizontal.TProgressbar", background="#2563EB",
            troughcolor="#E8EDF3", borderwidth=0, lightcolor="#2563EB",
            darkcolor="#2563EB", thickness=8,
        )
        style.configure("Modern.Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground="#263244", rowheight=30, borderwidth=0, relief="flat", font=("Microsoft YaHei UI", 9))
        style.configure("Modern.Treeview.Heading", background="#F1F4F7", foreground="#475569", font=("Microsoft YaHei UI", 9, "bold"), padding=(8, 9), relief="flat", borderwidth=0)
        style.map("Modern.Treeview", background=[("selected", "#DCE8FF")], foreground=[("selected", "#174EA6")])
        self._configure_tree_indicator(style)
        style.configure("TRadiobutton", background="#FFFFFF", foreground="#263244", font=("Microsoft YaHei UI", 9))
        style.configure("TCheckbutton", background="#FFFFFF", foreground="#263244", font=("Microsoft YaHei UI", 9))
        self._configure_check_indicator(style)

    def _configure_check_indicator(self, style: ttk.Style) -> None:
        unchecked = tk.PhotoImage(master=self, width=16, height=16)
        checked = tk.PhotoImage(master=self, width=16, height=16)
        unchecked.put("#FFFFFF", to=(0, 0, 16, 16))
        checked.put("#2563EB", to=(0, 0, 16, 16))
        for image, color in ((unchecked, "#AAB4C2"), (checked, "#2563EB")):
            image.put(color, to=(0, 0, 16, 1))
            image.put(color, to=(0, 15, 16, 16))
            image.put(color, to=(0, 0, 1, 16))
            image.put(color, to=(15, 0, 16, 16))
        for x, y in (
            (3, 8), (4, 9), (5, 10), (6, 9), (7, 8),
            (8, 7), (9, 6), (10, 5), (11, 4), (12, 3),
        ):
            checked.put("#FFFFFF", to=(x, y, x + 2, y + 2))
        self._check_unchecked_image = unchecked
        self._check_checked_image = checked
        style.element_create(
            "CleanCheck.indicator", "image", unchecked, ("selected", checked)
        )
        style.layout("Clean.TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("CleanCheck.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "w", "children": [
                    ("Checkbutton.label", {"sticky": "nswe"}),
                ]}),
            ]}),
        ])
        style.configure(
            "Clean.TCheckbutton", background="#FFFFFF", foreground="#263244",
            font=("Microsoft YaHei UI", 9),
        )

    def _configure_tree_indicator(self, style: ttk.Style) -> None:
        blank = tk.PhotoImage(master=self, width=28, height=28)
        collapsed = tk.PhotoImage(master=self, width=28, height=28)
        expanded = tk.PhotoImage(master=self, width=28, height=28)
        color = "#64748B"
        for x, y in ((10, 8), (11, 9), (12, 10), (13, 11), (12, 12), (11, 13), (10, 14)):
            collapsed.put(color, to=(x, y, x + 2, y + 2))
        for x, y in ((8, 10), (9, 11), (10, 12), (11, 13), (12, 12), (13, 11), (14, 10)):
            expanded.put(color, to=(x, y, x + 2, y + 2))
        self._tree_blank_image = blank
        self._tree_collapsed_image = collapsed
        self._tree_expanded_image = expanded
        style.layout("Modern.Treeview.Item", [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.text", {"side": "left", "sticky": ""}),
            ]}),
        ])

    def _load_configured_codes(self) -> set[str]:
        try:
            payload = json.loads((self.project_root / "data" / "pages.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return set()
        return {
            str(code)
            for code, config in payload.get("forms", {}).items()
            if isinstance(config, dict) and str(config.get("formUrl", "")).strip()
            and "replace-me" not in str(config.get("formUrl", ""))
        }

    def _can_run(self, item: ModuleItem) -> bool:
        return item.runnable and item.form_code in self.configured_codes

    def _test_type(self, item: ModuleItem) -> str:
        if item.operation:
            return f"页面操作：{item.operation}"
        if item.requires_business_id:
            return "详情模块"
        if self._can_run(item):
            return "新增保存+回显"
        if item.runnable and item.supports_add:
            return "自动识别新增"
        if item.runnable:
            return "页面访问"
        return "待获取路由"

    def _build(self) -> None:
        self._configure_styles()
        root = ttk.Frame(self, style="App.TFrame")
        root.pack(fill="both", expand=True)
        connection = ttk.Frame(root, style="Surface.TFrame", padding=(16, 11))
        connection.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 8))
        ttk.Label(connection, text="测试环境", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        ttk.Label(connection, text="源码目录", style="Field.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(connection, text="已部署系统地址", style="Field.TLabel").grid(row=1, column=2, sticky="w", padx=(22, 0))
        ttk.Entry(connection, textvariable=self.source).grid(row=2, column=0, sticky="ew", pady=(5, 0))
        source_actions = ttk.Frame(connection, style="Surface.TFrame")
        source_actions.grid(row=2, column=1, padx=(8, 0), pady=(5, 0))
        ttk.Button(source_actions, text="浏览", command=self.choose_source).pack(side="left")
        ttk.Button(source_actions, text="扫描", command=self.scan).pack(side="left", padx=(6, 0))
        self.system_url_input = ttk.Combobox(
            connection,
            textvariable=self.system_url,
            values=self.url_history,
            state="normal",
        )
        self.system_url_input.grid(row=2, column=2, sticky="ew", padx=(22, 0), pady=(5, 0))
        self.system_url_input.bind("<Button-1>", self._show_url_history)
        self.fetch_menu_button = ttk.Button(
            connection, text="连接并获取菜单", command=self.fetch_runtime_menu,
            style="Accent.TButton",
        )
        self.fetch_menu_button.grid(row=2, column=3, padx=(8, 0), pady=(5, 0))
        connection.columnconfigure(0, weight=1)
        connection.columnconfigure(2, weight=1)

        workspace = ttk.Frame(root, style="Surface.TFrame", padding=(16, 11))
        workspace.grid(row=1, column=0, sticky="nsew", padx=14)
        ttk.Label(workspace, text="模块选择", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 14))
        search = ttk.Entry(workspace, textvariable=self.query)
        search.grid(row=0, column=1, sticky="ew")
        search.bind("<KeyRelease>", lambda _event: self.render())
        tree_actions = ttk.Frame(workspace, style="Surface.TFrame")
        tree_actions.grid(row=0, column=2, sticky="e", padx=(14, 0))
        ttk.Label(tree_actions, textvariable=self.selection_status, style="Muted.TLabel").pack(side="left", padx=(0, 12))
        ttk.Button(tree_actions, text="全选可执行", command=self.select_all_runnable).pack(side="left", padx=(0, 6))
        ttk.Button(tree_actions, text="取消删除", command=self.deselect_delete_actions).pack(side="left", padx=(0, 6))
        ttk.Button(tree_actions, text="清空", command=self.clear_selection).pack(side="left")
        self.tree = ttk.Treeview(workspace, columns=("code", "state"), show="tree headings", selectmode="extended", style="Modern.Treeview")
        self.tree.heading("#0", text="模块层级")
        self.tree.heading("code", text="formCode")
        self.tree.heading("state", text="测试能力")
        self.tree.column("#0", width=480, minwidth=280)
        self.tree.column("code", width=230, minwidth=150)
        self.tree.column("state", width=170, minwidth=130, anchor="center")
        scrollbar = ttk.Scrollbar(workspace, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.tag_configure("all", background="#EEF4FF", foreground="#174EA6", font=("Microsoft YaHei UI", 9, "bold"))
        self.tree.tag_configure("branch", background="#F8FAFC", foreground="#334155", font=("Microsoft YaHei UI", 9, "bold"))
        self.tree.tag_configure("action", background="#F8FBFF", foreground="#2563EB")
        self.tree.tag_configure("leaf-even", background="#FFFFFF")
        self.tree.tag_configure("leaf-odd", background="#FBFCFE")
        self.tree.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=(9, 0))
        scrollbar.grid(row=1, column=3, sticky="ns", pady=(9, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.update_selection_status())
        self.tree.bind("<Button-1>", self._toggle_tree_item, add=True)
        self.tree.bind("<ButtonRelease-1>", self._cascade_tree_selection, add=True)
        self.tree.bind("<<TreeviewOpen>>", self._schedule_tree_indicator_sync, add=True)
        self.tree.bind("<<TreeviewClose>>", self._schedule_tree_indicator_sync, add=True)
        workspace.columnconfigure(1, weight=1)
        workspace.rowconfigure(1, weight=1)

        execution = ttk.Frame(root, style="Surface.TFrame", padding=(16, 10))
        execution.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        execution_header = ttk.Frame(execution, style="Surface.TFrame")
        execution_header.grid(row=0, column=0, sticky="ew")
        ttk.Label(execution_header, text="执行配置", style="Section.TLabel").pack(side="left")
        modes = ttk.Frame(execution_header, style="Surface.TFrame")
        modes.pack(side="left", padx=(28, 0))
        for index, (label, value) in enumerate(EXECUTION_MODES):
            ttk.Radiobutton(modes, text=label, variable=self.mode, value=value).pack(
                side="left", padx=(18, 0) if index else 0,
            )
        ttk.Checkbutton(
            modes, text="无头浏览器", variable=self.headless, style="Clean.TCheckbutton",
        ).pack(side="left", padx=(28, 0))
        ttk.Checkbutton(
            modes, text="失败后录入禅道", variable=self.submit_zentao,
            style="Clean.TCheckbutton",
        ).pack(side="left", padx=(20, 0))
        self.run_button = ttk.Button(
            execution_header,
            text=RUN_BUTTON_IDLE_TEXT,
            command=self.run_selected,
            style="Primary.TButton",
        )
        self.run_button.pack(side="right")
        execution_body = ttk.Frame(execution, style="Surface.TFrame")
        execution_body.grid(row=1, column=0, sticky="ew", pady=(9, 0))
        file_panel = ttk.Frame(execution_body, style="Surface.TFrame")
        file_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        value_panel = ttk.Frame(execution_body, style="Surface.TFrame")
        value_panel.grid(row=0, column=1, sticky="nsew", padx=(14, 0))

        ttk.Label(file_panel, text="通用用例 Excel（仅标准自动化）", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        common_row = ttk.Frame(file_panel, style="Surface.TFrame")
        common_row.grid(row=1, column=0, sticky="ew")
        common_cases_entry = ttk.Entry(common_row, textvariable=self.common_cases_excel)
        common_cases_entry.grid(row=0, column=0, sticky="ew")
        common_cases_entry.bind("<FocusOut>", lambda _event: self.refresh_common_case_sheets())
        ttk.Button(common_row, text="选择", width=7, command=self.choose_common_cases_excel).grid(
            row=0, column=1, padx=(8, 0)
        )
        common_row.columnconfigure(0, weight=1)

        ttk.Label(file_panel, text="模块用例 Excel（仅标准自动化）", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=(9, 4)
        )
        module_row = ttk.Frame(file_panel, style="Surface.TFrame")
        module_row.grid(row=3, column=0, sticky="ew")
        module_cases_entry = ttk.Entry(module_row, textvariable=self.module_cases_excel)
        module_cases_entry.grid(row=0, column=0, sticky="ew")
        module_cases_entry.bind("<FocusOut>", lambda _event: self.refresh_module_case_sheets())
        ttk.Button(module_row, text="选择", width=7, command=self.choose_module_cases_excel).grid(
            row=0, column=1, padx=(8, 0)
        )
        module_row.columnconfigure(0, weight=1)
        file_panel.columnconfigure(0, weight=1)

        ttk.Label(value_panel, text="通用用例页签（可多选，未选=全部）", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self.common_cases_sheet_input = MultiSelectSheetMenu(
            value_panel, textvariable=self.common_cases_sheet,
            on_change=self.refresh_common_case_ids,
        )
        self.common_cases_sheet_input.grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ttk.Label(value_panel, text="通用用例 ID（可选）", style="Field.TLabel").grid(
            row=0, column=1, sticky="w", pady=(0, 4)
        )
        self.common_case_ids_input = MultiSelectSheetMenu(
            value_panel, textvariable=self.common_case_ids,
        )
        self.common_case_ids_input.grid(row=1, column=1, sticky="ew", padx=(6, 0))
        ttk.Label(value_panel, text="模块用例页签（可多选，未选=全部）", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=(9, 4)
        )
        self.module_cases_sheet_input = MultiSelectSheetMenu(
            value_panel, textvariable=self.module_cases_sheet,
            on_change=self.refresh_module_case_ids,
        )
        self.module_cases_sheet_input.grid(row=3, column=0, sticky="ew", padx=(0, 6))
        ttk.Label(value_panel, text="模块用例 ID（可选）", style="Field.TLabel").grid(
            row=2, column=1, sticky="w", pady=(9, 4)
        )
        self.module_case_ids_input = MultiSelectSheetMenu(
            value_panel, textvariable=self.module_case_ids,
        )
        self.module_case_ids_input.grid(row=3, column=1, sticky="ew", padx=(6, 0))
        value_panel.columnconfigure(0, weight=2, uniform="case-selectors")
        value_panel.columnconfigure(1, weight=3, uniform="case-selectors")
        for row in range(4):
            file_panel.rowconfigure(row, uniform=f"execution-row-{row}")
            value_panel.rowconfigure(row, uniform=f"execution-row-{row}")
        execution_body.columnconfigure(0, weight=4, uniform="execution-panels")
        execution_body.columnconfigure(1, weight=5, uniform="execution-panels")
        execution.columnconfigure(0, weight=1)

        footer = ttk.Frame(root, style="App.TFrame", padding=(14, 0, 14, 12))
        footer.grid(row=3, column=0, sticky="ew")
        ttk.Label(footer, textvariable=self.status, style="Status.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(footer, textvariable=self.progress_text, style="Status.TLabel").grid(
            row=0, column=1, sticky="e"
        )
        ttk.Progressbar(
            footer, variable=self.progress_value, maximum=100,
            style="Execution.Horizontal.TProgressbar",
        ).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        footer.columnconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

    def _toggle_tree_item(self, event: tk.Event) -> str | None:
        tree_id = self.tree.identify_row(event.y)
        if not tree_id or self.tree.identify_column(event.x) != "#0":
            return None
        if not self.tree.get_children(tree_id):
            return None
        is_open = bool(self.tree.item(tree_id, "open"))
        self.tree.item(
            tree_id,
            open=not is_open,
            image=self._tree_collapsed_image if is_open else self._tree_expanded_image,
        )
        return None

    def _cascade_tree_selection(self, event: tk.Event) -> None:
        tree_id = self.tree.identify_row(event.y)
        if not tree_id:
            return
        descendants = self._tree_descendants(tree_id)
        if not descendants:
            return
        if tree_id in self.tree.selection():
            self.tree.selection_add(*descendants)
        else:
            self.tree.selection_remove(*descendants)
        self.update_selection_status()

    def _tree_descendants(self, tree_id: str) -> list[str]:
        descendants: list[str] = []
        pending = list(self.tree.get_children(tree_id))
        while pending:
            child = pending.pop(0)
            descendants.append(child)
            pending[0:0] = self.tree.get_children(child)
        return descendants

    def _schedule_tree_indicator_sync(self, _event: tk.Event) -> None:
        tree_id = self.tree.focus()
        if tree_id:
            self.after_idle(self._sync_tree_indicator, tree_id)

    def _sync_tree_indicator(self, tree_id: str) -> None:
        if not self.tree.exists(tree_id) or not self.tree.get_children(tree_id):
            return
        image = self._tree_expanded_image if self.tree.item(tree_id, "open") else self._tree_collapsed_image
        self.tree.item(tree_id, image=image)

    def choose_source(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.source.get() or DEFAULT_SOURCE)
        if selected:
            self.source.set(selected)
            self.scan()

    def choose_storage(self) -> None:
        initial_dir, initial_file = storage_dialog_defaults(self.project_root, self.storage.get())
        selected = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            initialfile=initial_file,
            filetypes=[("JSON", "*.json"), ("所有文件", "*.*")],
        )
        if selected:
            self.storage.set(selected)

    def choose_common_cases_excel(self) -> None:
        initial_dir, initial_file = common_cases_dialog_defaults(
            self.project_root, self.common_cases_excel.get()
        )
        selected = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            initialfile=initial_file,
            filetypes=[("Excel", "*.xlsx"), ("所有文件", "*.*")],
        )
        if selected:
            self.common_cases_excel.set(selected)
            self.refresh_common_case_sheets(show_error=True)

    def refresh_common_case_sheets(self, *, show_error: bool = False) -> list[str]:
        workbook = self.common_cases_excel.get().strip()
        if not workbook:
            self.common_cases_sheet_input.set_options([], [])
            self.common_case_ids_input.set_options([], [])
            return []
        path = Path(workbook).expanduser()
        if not path.is_absolute():
            path = (self.project_root / path).resolve()
        try:
            sheets = list_xlsx_sheets(path)
            references = list_xlsx_case_ids(path)
        except (FileNotFoundError, ValueError) as exc:
            self.common_cases_sheet_input.set_options([], [])
            self.common_case_ids_input.set_options([], [])
            if show_error:
                messagebox.showerror("无法读取 Excel 页签", str(exc))
            return []
        current = self.common_cases_sheet_input.selected_values()
        selected = preferred_case_sheets(sheets, current, preferred="新增")
        self.common_cases_sheet_input.set_options(sheets, selected)
        self.refresh_common_case_ids(references=references)
        return sheets

    def refresh_common_case_ids(
        self, *, references: list[tuple[str, str]] | None = None,
        show_error: bool = False,
    ) -> list[str]:
        workbook = self.common_cases_excel.get().strip()
        if not workbook:
            self.common_case_ids_input.set_options([], [])
            return []
        if references is None:
            path = Path(workbook).expanduser()
            if not path.is_absolute():
                path = (self.project_root / path).resolve()
            try:
                references = list_xlsx_case_ids(path)
            except (FileNotFoundError, ValueError) as exc:
                self.common_case_ids_input.set_options([], [])
                if show_error:
                    messagebox.showerror("无法读取 Excel 用例 ID", str(exc))
                return []
        options = linked_case_id_options(
            references, self.common_cases_sheet_input.selected_values()
        )
        values = [value for value, _label in options]
        selected = preferred_case_ids(
            values, self.common_case_ids_input.selected_values()
        )
        self.common_case_ids_input.set_options(
            values, selected, display_labels=dict(options)
        )
        return values

    def choose_module_cases_excel(self) -> None:
        initial_dir, initial_file = common_cases_dialog_defaults(
            self.project_root, self.module_cases_excel.get()
        )
        selected = filedialog.askopenfilename(
            initialdir=str(initial_dir),
            initialfile=initial_file,
            filetypes=[("Excel", "*.xlsx"), ("所有文件", "*.*")],
        )
        if selected:
            self.module_cases_excel.set(selected)
            self.refresh_module_case_sheets(show_error=True)

    def refresh_module_case_sheets(self, *, show_error: bool = False) -> list[str]:
        workbook = self.module_cases_excel.get().strip()
        if not workbook:
            self.module_cases_sheet_input.set_options([], [])
            self.module_case_ids_input.set_options([], [])
            return []
        path = Path(workbook).expanduser()
        if not path.is_absolute():
            path = (self.project_root / path).resolve()
        try:
            sheets = list_xlsx_sheets(path)
            references = list_xlsx_case_ids(path)
        except (FileNotFoundError, ValueError) as exc:
            self.module_cases_sheet_input.set_options([], [])
            self.module_case_ids_input.set_options([], [])
            if show_error:
                messagebox.showerror("无法读取模块用例页签", str(exc))
            return []
        current = self.module_cases_sheet_input.selected_values()
        preferred = "新增项目" if "新增项目" in sheets else "新增"
        selected = preferred_case_sheets(sheets, current, preferred=preferred)
        self.module_cases_sheet_input.set_options(sheets, selected)
        self.refresh_module_case_ids(references=references)
        return sheets

    def refresh_module_case_ids(
        self, *, references: list[tuple[str, str]] | None = None,
        show_error: bool = False,
    ) -> list[str]:
        workbook = self.module_cases_excel.get().strip()
        if not workbook:
            self.module_case_ids_input.set_options([], [])
            return []
        if references is None:
            path = Path(workbook).expanduser()
            if not path.is_absolute():
                path = (self.project_root / path).resolve()
            try:
                references = list_xlsx_case_ids(path)
            except (FileNotFoundError, ValueError) as exc:
                self.module_case_ids_input.set_options([], [])
                if show_error:
                    messagebox.showerror("无法读取模块用例 ID", str(exc))
                return []
        options = linked_case_id_options(
            references, self.module_cases_sheet_input.selected_values()
        )
        values = [value for value, _label in options]
        selected = preferred_case_ids(
            values, self.module_case_ids_input.selected_values()
        )
        self.module_case_ids_input.set_options(
            values, selected, display_labels=dict(options)
        )
        return values

    def scan(self) -> None:
        try:
            self.items = discover_modules(Path(self.source.get().strip()))
        except (OSError, ValueError) as exc:
            self.items = []
            self.render()
            self.status.set("扫描失败，请检查源码目录。")
            messagebox.showerror("扫描失败", str(exc))
            return
        self.render()
        runnable = sum(item.runnable for item in self.items)
        self.status.set(
            f"源码发现 {len(self.items) - 1} 个页面，{runnable} 个解析到 formCode；请登录系统获取真实中文菜单和路由。"
        )

    def fetch_runtime_menu(self) -> None:
        url = self.system_url.get().strip()
        if not url:
            messagebox.showwarning("缺少系统地址", "请填写已部署系统的登录页地址。")
            return
        source_root = Path(self.source.get().strip())
        username = self.username.get().strip()
        password = self.password.get()
        storage_state = self.storage.get().strip()
        headless = self.headless.get()
        self.fetch_menu_button.configure(state="disabled")
        self.status.set("正在登录并获取菜单，请在弹出的浏览器中完成登录...")

        def worker() -> None:
            try:
                aligned_url = align_application_url(
                    url, resolve_view_root(source_root).name
                )
                payload, saved_state = capture_menu(
                    aligned_url,
                    username=username,
                    password=password,
                    storage_state=storage_state,
                    headless=headless,
                    source_root=source_root,
                )
                items = modules_from_menu(payload, source_root)
            except Exception as exc:
                self.after(0, self._finish_runtime_menu_error, str(exc))
                return
            self.after(0, self._finish_runtime_menu, items, saved_state, url)

        threading.Thread(target=worker, name="runtime-menu-capture", daemon=True).start()

    def _finish_runtime_menu_error(self, error: str) -> None:
        self.fetch_menu_button.configure(state="normal")
        self.status.set("获取菜单失败。")
        messagebox.showerror("获取菜单失败", error)

    def _finish_runtime_menu(
        self,
        items: list[ModuleItem],
        saved_state: str,
        used_url: str = "",
    ) -> None:
        self.fetch_menu_button.configure(state="normal")
        self.url_history = save_url_history(self.url_history_file, used_url)
        self.system_url_input.configure(values=self.url_history)
        self.items = items
        self.storage.set(str((self.project_root / saved_state).resolve()))
        self.render()
        leaves = sum(is_executable_target(item) for item in self.items if item.id != "ALL")
        mismatches = sum(
            1 for item in self.items
            if item.id != "ALL" and item.runnable and item.component and not item.source_file
        )
        mismatch_text = f"；{mismatches} 个页面与所选源码不匹配，未生成猜测按钮" if mismatches else ""
        self.status.set(
            f"已从登录接口获取 {len(self.items) - 1} 个菜单节点，{leaves} 个叶子模块可执行"
            f"{mismatch_text}。"
        )


    def _show_url_history(self, _event=None) -> None:
        if self.url_history:
            self.after_idle(lambda: self.tk.call("ttk::combobox::Post", self.system_url_input))

    def render(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.by_tree_id.clear()
        filtered = search_modules(self.items, self.query.get())
        parent_paths = {
            item.path[:depth]
            for item in filtered
            for depth in range(1, len(item.path))
        }
        branch_ids: dict[tuple[str, ...], str] = {}
        visual_row = 0
        for item in filtered:
            parent = ""
            for depth, label in enumerate(item.path):
                branch = item.path[: depth + 1]
                if branch not in branch_ids:
                    leaf = depth == len(item.path) - 1
                    should_open = should_open_tree_branch(item, depth, self.query.get())
                    if item.id == "ALL" and leaf:
                        tag = "all"
                    elif item.operation and leaf:
                        tag = "action"
                    elif branch in parent_paths:
                        tag = "branch"
                    else:
                        tag = "leaf-even" if visual_row % 2 == 0 else "leaf-odd"
                    has_children = branch in parent_paths
                    indicator = (
                        self._tree_expanded_image if should_open else self._tree_collapsed_image
                    ) if has_children else self._tree_blank_image
                    tree_id = self.tree.insert(parent, "end", text=label, open=should_open,
                        values=(item.form_code if leaf else "", self._test_type(item) if leaf and item.id != "ALL" else ("全部可运行模块" if leaf else "")),
                        tags=(tag,), image=indicator)
                    branch_ids[branch] = tree_id
                    visual_row += 1
                    if leaf:
                        self.by_tree_id[tree_id] = item
                parent = branch_ids[branch]
        self.update_selection_status()

    def select_all_runnable(self) -> None:
        tree_ids = [
            tree_id for tree_id, item in self.by_tree_id.items()
            if is_executable_target(item) and item.id != "ALL"
        ]
        self.tree.selection_set(tree_ids)
        self.update_selection_status()

    def clear_selection(self) -> None:
        self.tree.selection_remove(*self.tree.selection())
        self.update_selection_status()

    def deselect_delete_actions(self) -> None:
        retained_ids = {
            item.id for item in exclude_delete_targets(self._selected_targets())
        }
        tree_ids = [
            tree_id for tree_id, item in self.by_tree_id.items()
            if item.id in retained_ids
        ]
        self.tree.selection_set(tree_ids)
        self.update_selection_status()

    def update_selection_status(self) -> None:
        count = len(self._selected_targets())
        self.selection_status.set(f"已选 {count} 个可执行模块")

    def _selected_targets(self) -> list[ModuleItem]:
        selected_items = [self.by_tree_id[tree_id] for tree_id in self.tree.selection() if tree_id in self.by_tree_id]
        return resolve_selected_targets(self.items, selected_items)

    def run_selected(self) -> None:
        if hasattr(self, "launch_version"):
            launch_version = self.launch_version
            if launch_version is None:
                messagebox.showwarning(
                    "无法校验代码版本",
                    "启动器未能读取启动版本。请关闭启动器并重新运行 run_test.vbs 后再执行测试。",
                )
                return
            try:
                current_version = capture_runtime_version(self.project_root)
            except RuntimeError as exc:
                messagebox.showwarning("无法校验代码版本", str(exc))
                return
            if runtime_version_changed(launch_version, current_version):
                messagebox.showwarning(
                    "检测到旧启动器",
                    runtime_version_mismatch_message(launch_version, current_version),
                )
                return
        targets = self._selected_targets()
        if not targets:
            messagebox.showwarning("请选择模块", "请选择一个或多个模块，父模块会自动包含其可执行子模块。")
            return
        if not self.storage.get() and (not self.username.get() or not self.password.get()):
            messagebox.showwarning("缺少登录信息", "请选择登录状态 JSON，或输入用户名和密码。")
            return
        try:
            aligned_url = align_application_url(
                self.system_url.get().strip(),
                resolve_view_root(Path(self.source.get().strip())).name,
            )
        except (OSError, ValueError) as exc:
            messagebox.showwarning("源码目录无效", str(exc))
            return
        mode = self.mode.get()
        submit_zentao = self.submit_zentao.get()
        if submit_zentao:
            missing = missing_zentao_settings(os.environ)
            if missing:
                messagebox.showwarning(
                    "缺少禅道配置",
                    "勾选‘失败后录入禅道’前，请先配置：" + ", ".join(missing),
                )
                return
        common_workbook = self.common_cases_excel.get().strip()
        module_workbook = self.module_cases_excel.get().strip()
        effective_common_case_ids: list[str] = []
        effective_module_case_ids: list[str] = []
        if mode == "standard":
            workbook_path = Path(common_workbook).expanduser() if common_workbook else None
            if workbook_path and not workbook_path.is_absolute():
                workbook_path = (self.project_root / workbook_path).resolve()
            if workbook_path is None or not workbook_path.is_file():
                messagebox.showwarning(
                    "缺少通用用例 Excel",
                    "标准自动化需要选择包含通用字段规则的 Excel 文件。",
                )
                return
            case_sheets = self.refresh_common_case_sheets(show_error=True)
            selected_case_sheets = effective_case_sheets(
                case_sheets, self.common_cases_sheet_input.selected_values()
            )
            if not case_sheets or any(
                sheet not in case_sheets for sheet in selected_case_sheets
            ):
                messagebox.showwarning("缺少用例页签", "请至少选择一个有效的 Excel 页签。")
                return
            case_ids = self.common_case_ids_input.option_values()
            selected_case_ids = self.common_case_ids_input.selected_values()
            if not case_ids:
                messagebox.showwarning(
                    "缺少用例 ID", "所选 Excel 页签中没有可执行的用例 ID。"
                )
                return
            if any(
                case_id not in case_ids for case_id in selected_case_ids
            ):
                messagebox.showwarning("用例 ID 无效", "所选 Excel 用例 ID 已失效，请重新选择。")
                return
            effective_common_case_ids = selected_case_ids or case_ids
            if module_workbook:
                module_case_sheets = self.refresh_module_case_sheets(show_error=True)
                selected_module_case_sheets = effective_case_sheets(
                    module_case_sheets, self.module_cases_sheet_input.selected_values()
                )
                if not module_case_sheets or any(
                    sheet not in module_case_sheets for sheet in selected_module_case_sheets
                ):
                    messagebox.showwarning(
                        "缺少模块用例页签", "请至少选择一个有效的模块用例页签。"
                    )
                    return
                module_case_ids = self.module_case_ids_input.option_values()
                selected_module_case_ids = self.module_case_ids_input.selected_values()
                if not module_case_ids:
                    messagebox.showwarning(
                        "缺少模块用例 ID", "所选模块用例页签中没有可执行的用例 ID。"
                    )
                    return
                if any(
                    case_id not in module_case_ids for case_id in selected_module_case_ids
                ):
                    messagebox.showwarning(
                        "模块用例 ID 无效", "所选模块用例 ID 已失效，请重新选择。"
                    )
                    return
                effective_module_case_ids = selected_module_case_ids or module_case_ids
        env = os.environ.copy()
        env.update({
            "EI_PARENT_ROOT": self.source.get(), "EI_DATA_MODE": self.mode.get(),
            "EI_BASE_URL": aligned_url.rstrip("/"),
            "EI_HEADLESS": str(self.headless.get()).lower(), "EI_STORAGE_STATE": self.storage.get(),
            "EI_USERNAME": self.username.get(), "EI_PASSWORD": self.password.get(),
            "EI_AUTOMATION_RUN_ID": (
                f"{time.strftime('%Y%m%d%H%M%S')}{time.time_ns() % 1_000_000:06d}_"
                f"{uuid.uuid4().hex[:8]}"
            ),
        })
        commands = []
        preflight_errors = []
        for target in targets:
            if error := target_preflight_error(target):
                preflight_errors.append((target, error))
                continue
            command_env = env.copy()
            command_env.pop("EI_REQUIRE_ADD", None)
            form_url = build_module_url(aligned_url, target.route)
            command_env.update({
                "EI_MODULE_ID": target.id,
                "EI_MODULE_NAME": "/".join(target.path),
                "EI_FORM_CODE": target.form_code,
                "EI_FORM_URL": form_url,
                "EI_COMPONENT": target.component,
                "EI_ACTION": target.operation,
                "EI_REQUIRES_BUSINESS_ID": str(target.requires_business_id).lower(),
            })
            if target.requires_business_id:
                command_env["EI_ENTRY_URL"] = detail_parent_url(form_url)
            else:
                command_env.pop("EI_ENTRY_URL", None)
            if target.operation_path:
                command_env["EI_ACTION_PATH"] = json.dumps(
                    target.operation_path, ensure_ascii=False
                )
            else:
                command_env.pop("EI_ACTION_PATH", None)
            if target.operation:
                test_file = "tests/test_module_action.py"
                if requires_add_cycle(target.operation, target.operation_path):
                    command_env["EI_REQUIRE_ADD"] = "true"
            else:
                test_file = "tests/test_form_smoke.py" if self._can_run(target) else "tests/test_module_smoke.py"
            if test_file == "tests/test_module_smoke.py" and target.supports_add:
                command_env["EI_REQUIRE_ADD"] = "true"
            commands.append((target, command_env, test_file))
        commands = suppress_pages_covered_by_actions(commands)
        commands = add_standard_common_field_commands(
            commands, mode=mode, workbook=common_workbook,
            case_ids=effective_common_case_ids,
            project_root=self.project_root,
        )
        commands = add_standard_module_case_commands(
            commands, mode=mode, workbook=module_workbook,
            case_ids=effective_module_case_ids,
            project_root=self.project_root,
        )
        commands = suppress_base_commands_covered_by_excel_cases(commands)
        commands = group_action_commands(commands)
        commands = prioritize_progress_discovery_commands(commands)
        logical_target_count = len(command_target_names(commands)) + len(preflight_errors)
        base_url = aligned_url.rstrip("/")
        headless = self.headless.get()
        self.run_button.configure(
            state="disabled", text=run_button_running_text(headless)
        )
        self._set_execution_progress(0, None, "正在准备本轮用例进度...")
        self.status.set(f"正在准备 {logical_target_count} 个测试目标的用例进度...")
        threading.Thread(
            target=self._execute_commands_worker,
            args=(commands, preflight_errors, mode, base_url, headless, submit_zentao),
            name="smoke-test-execution", daemon=True,
        ).start()

    def _execute_commands_worker(
        self, commands, preflight_errors, mode: str, base_url: str, headless: bool,
        submit_zentao: bool,
    ) -> None:
        failures = []
        environment_blocks = []
        environment_warnings = []
        failed_discovery_manifests: set[str] = set()
        log_dir = self.project_root / "artifacts" / "runs"
        try:
            allure_paths = create_allure_paths(self.project_root)
            log_dir.mkdir(parents=True, exist_ok=True)
            progress_dir = log_dir / ".pytest-progress"
            progress_dir.mkdir(parents=True, exist_ok=True)
            if commands:
                self.after(0, self.status.set, "正在同步源码仓库...")
                sync_log_file = log_dir / "source-sync.log"
                try:
                    sync_output = pull_latest_source(os.environ)
                except SourceSyncError as exc:
                    sync_log_file.write_text(str(exc) + "\n", encoding="utf-8")
                    self.after(
                        0, self._finish_execution_error,
                        f"源码同步失败，未启动 pytest。\n\n{exc}\n\n完整日志：{sync_log_file}",
                    )
                    return
                sync_log_file.write_text(sync_output + "\n", encoding="utf-8")
                configured_probes = load_environment_api_probes(
                    self.project_root / DEFAULT_PROBES_FILE
                )
                required_probes = {
                    probe.id: probe
                    for _target, command_env, _test_file in commands
                    for probe in matching_probes(configured_probes, command_env)
                }
                storage_state = str(commands[0][1].get("EI_STORAGE_STATE", ""))
                source_root = str(commands[0][1].get("EI_PARENT_ROOT", ""))
                probe_results = probe_environment_apis(
                    required_probes.values(), base_url=base_url,
                    storage_state=storage_state,
                )
                commands, environment_blocks = block_unavailable_commands(
                    commands, probe_results,
                )
                environment_warnings = update_version_mismatch_state(
                    probe_results,
                    source_version=source_revision(source_root),
                    state_file=self.project_root / DEFAULT_VERSION_STATE_FILE,
                )
                preflight_report = log_dir / "environment-api-preflight.json"
                write_environment_preflight_report(
                    preflight_report, probe_results, environment_blocks, environment_warnings,
                )
                append_environment_preflight_summary(
                    sync_log_file,
                    preflight_report,
                    environment_blocks,
                    environment_warnings,
                )

            command_entries = [
                (str(index), target, command_env, test_file)
                for index, (target, command_env, test_file) in enumerate(commands, 1)
            ]
            case_progress = CaseProgressTracker(
                command_id for command_id, _target, _env, _test_file in command_entries
            )
            discovery_command_ids = {
                command_id
                for command_id, _target, _env, test_file in command_entries
                if test_file == "tests/test_common_field_discovery.py"
            }
            completed_discovery_ids: set[str] = set()

            logical_target_names = command_target_names(commands)
            write_environment(allure_paths.results, {
                "base_url": base_url, "data_mode": mode, "headless": headless,
                "module_count": len(logical_target_names) + len(preflight_errors),
                "environment_blocked_count": len(environment_blocks),
                "environment_version_warnings": len(environment_warnings),
            })

            def post_case_progress(*, running: bool = True) -> None:
                completed_cases, total_cases = case_progress.snapshot()
                self.after(
                    0, self._set_execution_progress, completed_cases, total_cases,
                    case_progress_text(
                        completed_cases,
                        total_cases,
                        running=running,
                        discovery_completed=len(completed_discovery_ids),
                        discovery_total=len(discovery_command_ids),
                        registered_total=case_progress.registered_total,
                    ),
                )

            for target, error in preflight_errors:
                log_file = log_dir / safe_run_log_name(target.id)
                log_file.write_text(error + "\n", encoding="utf-8")
                failures.append((" / ".join(target.path) or target.name, error, log_file))

            for command_id, target, command_env, test_file in command_entries:
                command_env["EI_AUTOMATION_TARGET_SEQUENCE"] = command_id
                command_env["PYTHONUTF8"] = "1"
                command_env["PYTHONIOENCODING"] = "utf-8"
                command_env[DEFER_SKILL_GATE_ENV] = "true"
                if test_file == "tests/test_common_field_validation.py":
                    # Its parameters depend on the fresh manifest created by this run's
                    # discovery command. A previous run's manifest is not a valid total.
                    continue
                collect_command = build_pytest_command(
                    sys.executable, test_file, command_env, mode, None,
                    collect_only=True,
                )
                progress_file = progress_dir / f"collect-{uuid.uuid4().hex}.jsonl"
                try:
                    count, collection = collect_pytest_case_count(
                        collect_command, cwd=self.project_root,
                        environment=command_env, progress_file=progress_file,
                        command_id=command_id,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    count = None
                    collection = None
                finally:
                    progress_file.unlink(missing_ok=True)
                if collection is not None and collection.returncode:
                    output = collection.stdout or ""
                    if gate_message := maintenance_gate_failure_message(output):
                        selected_sheet = command_env.get(
                            "EI_COMMON_CASES_SHEET",
                            command_env.get("EI_MODULE_CASES_SHEET", ""),
                        )
                        log_file = log_dir / command_log_name(
                            target.id, test_file, selected_sheet
                        )
                        log_file.write_text(
                            "用例总数收集被维护Skill门禁阻断。\n\n" + output,
                            encoding="utf-8",
                        )
                        self.after(
                            0, self._finish_maintenance_gate, gate_message, log_file
                        )
                        return
                if count is not None:
                    case_progress.set_collected(command_id, count)
            post_case_progress()

            for command_id, target, command_env, test_file in command_entries:
                manifest = command_env.get("EI_COMMON_FIELDS_MANIFEST", "")
                if (
                    test_file == "tests/test_common_field_validation.py"
                    and manifest in failed_discovery_manifests
                ):
                    case_progress.omit(command_id)
                    post_case_progress()
                    continue
                selected_sheet = command_env.get(
                    "EI_COMMON_CASES_SHEET",
                    command_env.get("EI_MODULE_CASES_SHEET", ""),
                )
                target_name = " / ".join(target.path) or target.name
                stage = execution_stage_label(test_file, selected_sheet)
                self.after(
                    0, self.status.set,
                    f"正在执行：{target_name} · {stage}",
                )
                log_file = log_dir / command_log_name(
                    target.id, test_file, selected_sheet
                )
                command = build_pytest_command(
                    sys.executable, test_file, command_env, mode, allure_paths.results
                )
                action_count = len(json.loads(command_env.get("EI_ACTIONS_JSON", "[]"))) or 1
                timeout_seconds = max(30, int(os.getenv("EI_MODULE_TIMEOUT_SECONDS", "300"))) * action_count
                if test_file == "tests/test_common_field_validation.py":
                    timeout_seconds = max(
                        timeout_seconds,
                        int(os.getenv("EI_COMMON_VALIDATION_TIMEOUT_SECONDS", "3600")),
                    )
                elif test_file == "tests/test_common_field_batch.py":
                    timeout_seconds = common_field_batch_timeout_seconds(
                        command_env, timeout_seconds,
                    )
                progress_file = progress_dir / f"run-{uuid.uuid4().hex}.jsonl"

                def handle_progress_event(event, expected_command_id=command_id) -> None:
                    if event.get("command_id") != expected_command_id:
                        return
                    changed = False
                    event_name = event.get("event")
                    if event_name in {"collected", LOGICAL_COLLECTED_EVENT} and isinstance(
                        event.get("count"), int,
                    ):
                        changed = case_progress.set_collected(
                            expected_command_id,
                            event["count"],
                            logical=event_name == LOGICAL_COLLECTED_EVENT,
                        )
                    elif event_name in {"finished", LOGICAL_FINISHED_EVENT}:
                        changed = case_progress.mark_finished(
                            expected_command_id,
                            event.get("nodeid", ""),
                            logical=event_name == LOGICAL_FINISHED_EVENT,
                        )
                        if expected_command_id in discovery_command_ids:
                            completed_discovery_ids.add(expected_command_id)
                    if changed:
                        post_case_progress()

                try:
                    with log_file.open("w", encoding="utf-8") as log:
                        log.write(
                            f"module={target.id}\ntest_file={test_file}\n"
                            f"path={' / '.join(target.path)}\n"
                            f"action={target.operation}\n"
                            f"action_path={' / '.join(target.operation_path)}\n"
                            f"action_count={action_count}\n"
                            f"actions={command_env.get('EI_ACTIONS_JSON', '')}\n"
                            f"timeout_seconds={timeout_seconds}\ncommand={' '.join(command)}\n\n"
                        )
                        log.flush()
                        completed = run_logged_pytest(
                            command, cwd=self.project_root, environment=command_env,
                            log=log, progress_file=progress_file,
                            command_id=command_id, timeout_seconds=timeout_seconds,
                            on_event=handle_progress_event,
                        )
                    if (
                        test_file == "tests/test_common_field_batch.py"
                        and case_progress.resolve_remaining(
                            command_id, reason="process-exit",
                        )
                    ):
                        post_case_progress()
                except subprocess.TimeoutExpired:
                    message = f"测试目标执行超过 {timeout_seconds} 秒，已终止"
                    with log_file.open("a", encoding="utf-8") as log:
                        log.write("\n" + message + "\n")
                    for name in command_failure_names(target, command_env, test_file):
                        failures.append((name, message, log_file))
                    if (
                        test_file == "tests/test_common_field_batch.py"
                        and case_progress.resolve_remaining(
                            command_id, reason="timeout",
                        )
                    ):
                        post_case_progress()
                    if test_file == "tests/test_common_field_discovery.py":
                        failed_discovery_manifests.add(manifest)
                        completed_discovery_ids.add(command_id)
                        omit_discovered_validation_commands(
                            command_entries, command_env, case_progress,
                        )
                        post_case_progress()
                    continue
                except OSError as exc:
                    message = f"无法启动 pytest：{exc}"
                    with log_file.open("a", encoding="utf-8") as log:
                        log.write("\n" + message + "\n")
                    for name in command_failure_names(target, command_env, test_file):
                        failures.append((name, message, log_file))
                    if test_file == "tests/test_common_field_discovery.py":
                        failed_discovery_manifests.add(manifest)
                        completed_discovery_ids.add(command_id)
                        omit_discovered_validation_commands(
                            command_entries, command_env, case_progress,
                        )
                        post_case_progress()
                    continue
                finally:
                    progress_file.unlink(missing_ok=True)
                if completed.returncode:
                    output = log_file.read_text(encoding="utf-8", errors="replace")
                    if gate_message := maintenance_gate_failure_message(output):
                        self.after(
                            0, self._finish_maintenance_gate, gate_message, log_file
                        )
                        return
                    tail = "\n".join(output.splitlines()[-8:])
                    for name in command_failure_names(target, command_env, test_file, output):
                        failures.append((name, tail, log_file))
                    if test_file == "tests/test_common_field_discovery.py":
                        failed_discovery_manifests.add(manifest)
                        completed_discovery_ids.add(command_id)
                        omit_discovered_validation_commands(
                            command_entries, command_env, case_progress,
                        )
                        post_case_progress()
                elif test_file == "tests/test_common_field_discovery.py":
                    completed_discovery_ids.add(command_id)
                    failed_discovery_manifests.discard(manifest)
                    try:
                        registered = register_discovered_validation_counts(
                            command_entries, command_env, case_progress,
                        )
                    except (FileNotFoundError, KeyError, TypeError, ValueError,
                              json.JSONDecodeError, OSError) as exc:
                        with log_file.open("a", encoding="utf-8") as log:
                            log.write(
                                "\nCOMMON_FIELD_VALIDATION_TOTAL_PENDING "
                                f"reason={exc}\n"
                            )
                    else:
                        if registered:
                            with log_file.open("a", encoding="utf-8") as log:
                                log.write(
                                    "\nCOMMON_FIELD_VALIDATION_TOTAL_READY "
                                    f"commands={registered}\n"
                                )
                        post_case_progress()
            self.after(0, self.status.set, "测试批次已完成，正在生成报告...")
            report_dir, report_error, gate_passed = generate_report_before_maintenance_gate(
                allure_paths,
                project_root=self.project_root,
                check_gate=lambda: self._check_maintenance_gate_after_execution(log_dir),
            )
            if not gate_passed:
                return
            zentao_environment = os.environ.copy()
            zentao_environment["ZENTAO_AUTO_SUBMIT"] = str(submit_zentao).lower()
            zentao_result = process_allure_failures(
                allure_paths.results,
                self.project_root / "artifacts" / "zentao",
                environment=zentao_environment,
            )
        except Exception as exc:
            self.after(0, self._finish_execution_error, str(exc))
            return
        self.after(
            0, self._finish_execution, commands, failures, environment_blocks,
            environment_warnings, log_dir,
            report_dir, report_error, allure_paths.results, zentao_result,
            *case_progress.snapshot(),
        )

    def _check_maintenance_gate_after_execution(self, log_dir: Path) -> bool:
        passed, output = check_maintenance_gate_once(
            lambda: run_skill_maintenance_gate_check(self.project_root)
        )
        if passed:
            return True

        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "maintenance-skill-gate.log"
        log_file.write_text(output or "维护Skill门禁检查失败。", encoding="utf-8")
        if gate_message := maintenance_gate_failure_message(output):
            message = (
                "本轮业务操作已经执行完成，后置维护 Skill 门禁校验未通过。\n\n"
                f"{gate_message}"
            )
            self.after(0, self._finish_maintenance_gate, message, log_file)
        else:
            self.after(
                0, self._finish_execution_error,
                f"维护Skill门禁检查异常。\n\n{output}\n\n完整日志：{log_file}",
            )
        return False

    def _set_execution_progress(
        self, completed: int, total: int | None, text: str = "",
    ) -> None:
        percent = execution_progress_percent(completed, total or 0)
        self.progress_value.set(percent)
        self.progress_text.set(
            text or case_progress_text(completed, total, running=True)
        )

    def _finish_execution_error(self, error: str) -> None:
        self.run_button.configure(state="normal", text=RUN_BUTTON_IDLE_TEXT)
        self.status.set("执行异常终止。")
        self.progress_text.set(f"异常终止 · {round(self.progress_value.get())}%")
        messagebox.showerror("执行失败", error)

    def _finish_maintenance_gate(self, message: str, log_file: Path) -> None:
        self.run_button.configure(state="normal", text=RUN_BUTTON_IDLE_TEXT)
        self.status.set("执行被维护Skill门禁阻断，业务测试尚未启动。")
        self.progress_value.set(0)
        self.progress_text.set("维护Skill门禁阻断 · 0%")
        messagebox.showerror(
            "维护Skill门禁阻断", f"{message}\n\n完整日志：{log_file}"
        )

    def _finish_execution(
        self, commands, failures, environment_blocks, environment_warnings,
        log_dir: Path, report_dir: Path | None,
        report_error: str, results_dir: Path, zentao_result: ZentaoRunResult,
        completed_cases: int, total_cases: int | None,
    ) -> None:
        self.run_button.configure(state="normal", text=RUN_BUTTON_IDLE_TEXT)
        self._set_execution_progress(
            completed_cases, total_cases,
            case_progress_text(completed_cases, total_cases, running=False),
        )
        if failures:
            self.status.set(f"执行完成：{len(failures)} 个目标失败。")
            messagebox.showerror(
                "执行失败",
                format_failure_message(failures, log_dir, report_error)
                + format_environment_block_message(environment_blocks, environment_warnings)
                + "\n\n" + zentao_result.summary(),
            )
        elif environment_blocks:
            passed_names = command_target_names(commands)
            self.status.set(
                f"执行完成：{len(passed_names)} 个通过，{len(environment_blocks)} 个因环境版本不匹配被阻塞。"
            )
            messagebox.showwarning(
                "环境版本不匹配",
                format_environment_block_message(environment_blocks, environment_warnings)
                + f"\n\nAllure：{report_dir or results_dir}{report_error}",
            )
        else:
            target_names = command_target_names(commands)
            self.status.set(f"执行完成：{len(target_names)} 个目标通过。")
            if len(target_names) == 1:
                target = commands[0][0]
                messagebox.showinfo("执行完成", f"模块：{target.name}\n测试类型：{self._test_type(target)}\n结果：通过\nAllure：{report_dir or results_dir}{report_error}\n\n{zentao_result.summary()}")
            else:
                target_list = "\n".join(
                    f"{index}. {name}" for index, name in enumerate(target_names, 1)
                )
                messagebox.showinfo(
                    "执行完成",
                    f"已完成 {len(target_names)} 个测试目标，全部通过。\n\n"
                    f"执行项：\n{target_list}\n\nAllure：{report_dir or results_dir}{report_error}\n\n{zentao_result.summary()}",
                )

def main() -> int:
    Launcher().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
