from pathlib import Path
from types import SimpleNamespace

import ei_ui_smoke.launcher as launcher_module

from ei_ui_smoke.launcher import (
    CaseProgressTracker,
    EXECUTION_MODES,
    DEFAULT_EXECUTION_MODE,
    add_standard_common_field_commands,
    add_standard_module_case_commands,
    build_pytest_command,
    command_log_name,
    command_failure_names,
    common_cases_dialog_defaults,
    common_case_sheet_family,
    common_case_sheet_matches_target,
    common_field_discovery_identity,
    common_field_batch_timeout_seconds,
    default_common_cases_workbook,
    default_module_cases_workbook,
    default_storage_state,
    console_python_executable,
    format_failure_message,
    group_action_commands,
    is_detail_page_target,
    is_executable_target,
    command_target_names,
    case_progress_text,
    default_submit_zentao,
    exclude_delete_targets,
    environment_flag,
    execution_progress_percent,
    execution_stage_label,
    generate_report_before_maintenance_gate,
    resolve_selected_targets,
    requires_add_cycle,
    run_button_running_text,
    should_open_tree_branch,
    storage_dialog_defaults,
    suppress_base_commands_covered_by_excel_cases,
    suppress_pages_covered_by_actions,
    load_url_history,
    maintenance_gate_failure_message,
    linked_case_id_options,
    missing_zentao_settings,
    save_url_history,
    safe_run_log_name,
    target_preflight_error,
    preferred_common_cases_sheet,
    parse_sheet_selection,
    preferred_case_sheets,
    effective_case_sheets,
    preferred_case_ids,
    prioritize_progress_discovery_commands,
    read_pytest_progress_events,
    register_discovered_validation_counts,
    omit_discovered_validation_commands,
    check_maintenance_gate_once,
    DEFER_SKILL_GATE_ENV,
    Launcher,
)


def test_common_cases_sheet_prefers_current_then_add_then_first():
    assert preferred_common_cases_sheet(["说明", "新增"], "说明") == "说明"
    assert preferred_common_cases_sheet(["说明", "新增"]) == "新增"
    assert preferred_common_cases_sheet(["说明", "编辑"]) == "说明"
    assert preferred_common_cases_sheet([]) == ""


def test_multiple_case_sheets_keep_valid_selections_in_workbook_order():
    assert parse_sheet_selection("编辑、新增, 删除") == ["编辑", "新增", "删除"]
    assert preferred_case_sheets(
        ["说明", "新增", "编辑"], ["编辑", "新增"], preferred="新增"
    ) == ["新增", "编辑"]
    assert preferred_case_sheets(["说明", "新增"], [], preferred="新增") == []
    assert effective_case_sheets(["说明", "新增"], []) == ["说明", "新增"]
    assert effective_case_sheets(["说明", "新增", "编辑"], ["编辑", "新增"]) == ["新增", "编辑"]


def test_case_id_selection_keeps_only_explicit_valid_ids():
    assert preferred_case_ids(["ADD-001", "ADD-002"], ["ADD-002"]) == ["ADD-002"]
    assert preferred_case_ids(["ADD-001", "ADD-002"], ["不存在"]) == []


def test_case_id_options_follow_selected_sheets_and_keep_sheet_identity():
    references = [
        ("新增", "CASE-001"),
        ("新增", "ADD-002"),
        ("编辑", "CASE-001"),
        ("编辑", "EDIT-002"),
    ]

    assert linked_case_id_options(references, ["编辑"]) == [
        ("CASE-001（编辑）", "CASE-001"),
        ("EDIT-002（编辑）", "EDIT-002"),
    ]
    assert linked_case_id_options(references, ["新增", "编辑"]) == [
        ("CASE-001（新增）", "CASE-001（新增）"),
        ("ADD-002（新增）", "ADD-002"),
        ("CASE-001（编辑）", "CASE-001（编辑）"),
        ("EDIT-002（编辑）", "EDIT-002"),
    ]
    assert linked_case_id_options(references, []) == [
        ("CASE-001（新增）", "CASE-001（新增）"),
        ("ADD-002（新增）", "ADD-002"),
        ("CASE-001（编辑）", "CASE-001（编辑）"),
        ("EDIT-002（编辑）", "EDIT-002"),
    ]


def test_pytest_commands_forward_selected_case_ids_and_target_only_business_test(tmp_path):
    common_env = {
        "EI_COMMON_CASES_EXCEL": "common.xlsx",
        "EI_COMMON_CASES_SHEET": "新增",
        "EI_COMMON_CASE_IDS_JSON": '["ADD-002"]',
        "EI_COMMON_FIELDS_MANIFEST": "fields.json",
    }
    common = build_pytest_command(
        "python.exe", "tests/test_common_field_validation.py", common_env,
        "standard", tmp_path,
    )
    assert common[common.index("--common-case-ids") + 1] == '["ADD-002"]'

    detail_env = {
        "EI_COMMON_CASES_EXCEL": "common.xlsx",
        "EI_COMMON_CASES_SHEET": "详情",
        "EI_COMMON_CASE_IDS_JSON": '["VIEW-001"]',
    }
    detail = build_pytest_command(
        "python.exe", "tests/test_common_detail_validation.py", detail_env,
        "standard", tmp_path,
    )
    assert detail[detail.index("--common-case-ids") + 1] == '["VIEW-001"]'
    assert "--common-fields-manifest" not in detail

    module_env = {
        "EI_MODULE_CASES_EXCEL": "module.xlsx",
        "EI_MODULE_CASES_SHEET": "新增项目",
        "EI_MODULE_CASE_IDS_JSON": '["LX-ADD-002"]',
    }
    module = build_pytest_command(
        "python.exe", "tests/test_build_project_add_personalized.py", module_env,
        "standard", tmp_path,
    )
    assert any(item.endswith(
        "test_build_project_add_personalized.py::test_build_project_add_personalized"
    ) for item in module)
    assert module[module.index("--module-case-ids") + 1] == '["LX-ADD-002"]'
from ei_ui_smoke.module_index import ModuleItem


def test_console_python_is_used_when_launcher_runs_under_pythonw(tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    python = tmp_path / "python.exe"
    pythonw.write_text("", encoding="utf-8")
    python.write_text("", encoding="utf-8")

    assert console_python_executable(str(pythonw)) == str(python)


def test_common_cases_dialog_defaults_to_project_case_directory(tmp_path):
    case_dir = tmp_path / "tests" / "Common_Test_Cases"
    case_dir.mkdir(parents=True)

    assert common_cases_dialog_defaults(tmp_path, "") == (case_dir, "")


def test_default_common_cases_workbook_is_specific_project_file(tmp_path):
    workbook = tmp_path / "tests" / "Common_Test_Cases" / "公共用例_UI自动化.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"xlsx")

    assert default_common_cases_workbook(tmp_path) == workbook.as_posix()


def test_default_module_cases_workbook_is_specific_project_file(tmp_path):
    workbook = tmp_path / "tests" / "Common_Test_Cases" / "建设项目_个性化用例.xlsx"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"xlsx")

    assert default_module_cases_workbook(tmp_path) == workbook.as_posix()


def test_default_storage_state_prefers_environment_then_saved_state(tmp_path, monkeypatch):
    saved = tmp_path / "artifacts" / "auth-state.json"
    saved.parent.mkdir(parents=True)
    saved.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("EI_STORAGE_STATE", raising=False)
    assert default_storage_state(tmp_path) == str(saved.resolve())
    monkeypatch.setenv("EI_STORAGE_STATE", "configured-state.json")
    assert default_storage_state(tmp_path) == "configured-state.json"


def test_common_cases_dialog_reopens_current_file_directory(tmp_path):
    case_dir = tmp_path / "tests" / "Common_Test_Cases"
    case_dir.mkdir(parents=True)
    workbook = case_dir / "rules.xlsx"

    assert common_cases_dialog_defaults(tmp_path, str(workbook)) == (
        case_dir, "rules.xlsx"
    )


def test_prefixed_crud_actions_require_the_add_cycle():
    assert requires_add_cycle("新增项目")
    assert requires_add_cycle("添加成员")
    assert requires_add_cycle("新建任务")
    assert requires_add_cycle("删除项目")
    assert requires_add_cycle("任意嵌套动作", ("编辑", "联系人", "新增"))
    assert not requires_add_cycle("查询")


def test_execution_modes_include_standard_and_remove_qcc_verification():
    assert EXECUTION_MODES == (
        ("标准自动化", "standard"),
        ("快速探测", "probe"),
        ("稳定冒烟", "stable"),
    )
    assert DEFAULT_EXECUTION_MODE == "standard"


def test_run_button_gives_clear_headless_feedback():
    assert run_button_running_text(True) == "测试中，请稍后…"
    assert run_button_running_text(False) == "执行中…"


def test_execution_progress_is_clamped_and_stage_is_readable():
    assert execution_progress_percent(0, 4) == 0
    assert execution_progress_percent(1, 3) == 33
    assert execution_progress_percent(2, 4) == 50
    assert execution_progress_percent(5, 4) == 100
    assert execution_progress_percent(0, 0) == 0
    assert execution_stage_label(
        "tests/test_common_field_validation.py", "编辑"
    ) == "通用字段验证（编辑）"


def test_case_progress_uses_collected_pytest_items_as_total():
    tracker = CaseProgressTracker((
        "page-action",
        "personalized",
        "field-discovery",
        "common-validation",
    ))

    assert tracker.set_collected("page-action", 1)
    assert tracker.set_collected("personalized", 4)
    assert tracker.set_collected("field-discovery", 1)
    assert tracker.set_collected("common-validation", 40)

    assert tracker.snapshot() == (0, 46)
    assert case_progress_text(0, 46, running=True) == "0/46 用例执行中 · 0%"
    assert case_progress_text(46, 46, running=False) == "46/46 用例执行完成 · 100%"


def test_case_progress_advances_only_for_unique_finished_pytest_items():
    tracker = CaseProgressTracker(("actions",))
    tracker.set_collected("actions", 4)

    assert tracker.snapshot() == (0, 4)
    assert tracker.mark_finished("actions", "tests/test_action.py::test_action[01]")
    assert not tracker.mark_finished("actions", "tests/test_action.py::test_action[01]")
    assert not tracker.mark_finished("actions", "")

    assert tracker.snapshot() == (1, 4)


def test_batch_logical_progress_replaces_outer_pytest_item_and_ignores_outer_finish():
    tracker = CaseProgressTracker(("batch",))
    tracker.set_collected("batch", 1)

    assert tracker.set_collected("batch", 3, logical=True)
    assert not tracker.set_collected("batch", 1)
    assert tracker.mark_finished("batch", "logical-1", logical=True)
    assert not tracker.mark_finished("batch", "outer-pytest-item")
    assert tracker.snapshot() == (1, 3)


def test_batch_logical_progress_resolves_remaining_items_after_timeout():
    tracker = CaseProgressTracker(("batch",))
    tracker.set_collected("batch", 4, logical=True)
    tracker.mark_finished("batch", "logical-1", logical=True)

    assert tracker.resolve_remaining("batch", reason="timeout")
    assert tracker.snapshot() == (4, 4)
    assert not tracker.resolve_remaining("batch", reason="timeout")


def test_batch_timeout_scales_with_physical_transaction_count(monkeypatch):
    monkeypatch.delenv("EI_COMMON_BATCH_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("EI_COMMON_BATCH_TIMEOUT_BASE_SECONDS", "180")
    monkeypatch.setenv("EI_COMMON_BATCH_TIMEOUT_PER_TRANSACTION_SECONDS", "120")
    monkeypatch.setenv("EI_COMMON_BATCH_TIMEOUT_MAX_SECONDS", "3600")
    monkeypatch.setattr(
        launcher_module,
        "common_field_batch_plan_counts",
        lambda _env: (28, 4),
    )

    assert common_field_batch_timeout_seconds({}, 300) == 660


def test_case_progress_keeps_total_unknown_until_every_active_command_is_collected():
    tracker = CaseProgressTracker(("discovery", "validation"))

    tracker.set_collected("discovery", 1)

    assert tracker.snapshot() == (0, None)
    assert tracker.registered_total == 1
    assert case_progress_text(0, None, running=True) == (
        "已完成 0 个用例 · 等待后置字段校验登记总数"
    )
    assert case_progress_text(
        0,
        None,
        running=True,
        discovery_completed=2,
        discovery_total=5,
        registered_total=12,
    ) == (
        "已完成 0 个用例 · 字段发现进度 2/5 · "
        "已登记 12 个用例 · 剩余 3 组待发现"
    )

    tracker.set_collected("validation", 40)
    assert tracker.snapshot() == (0, 41)


def test_discovery_registers_all_validation_totals_for_the_same_form(monkeypatch):
    discovery_env = {
        "EI_COMMON_CASES_EXCEL": "cases.xlsx",
        "EI_COMMON_FIELDS_MANIFEST": "fresh.json",
        "EI_COMMON_CASES_SHEET": "编辑",
        "EI_COMMON_CASE_IDS_JSON": '["EDIT-002"]',
        "EI_MODULE_ID": "PROJECT::edit",
        "EI_FORM_URL": "https://example.test/project/detail",
        "EI_COMPONENT": "project/detail/index",
        "EI_ACTION": "编辑",
        "EI_COMMON_FORM_ACTION": "编辑",
    }
    matching_validation_env = discovery_env.copy()
    shared_detail_validation_env = {
        **discovery_env,
        "EI_COMMON_CASES_SHEET": "详情",
        "EI_COMMON_CASE_IDS_JSON": '["VIEW-003"]',
    }
    unrelated_validation_env = {
        **discovery_env,
        "EI_COMMON_FIELDS_MANIFEST": "other.json",
    }
    tracker = CaseProgressTracker((
        "discovery", "validation", "detail-validation", "unrelated",
    ))
    assert tracker.set_collected("discovery", 1)
    command_entries = [
        ("discovery", SimpleNamespace(), discovery_env, "tests/test_common_field_discovery.py"),
        ("validation", SimpleNamespace(), matching_validation_env, "tests/test_common_field_validation.py"),
        (
            "detail-validation", SimpleNamespace(), shared_detail_validation_env,
            "tests/test_common_field_validation.py",
        ),
        ("unrelated", SimpleNamespace(), unrelated_validation_env, "tests/test_common_field_validation.py"),
    ]
    monkeypatch.setattr(
        launcher_module,
        "common_field_validation_item_count",
        lambda env: 4 if env["EI_COMMON_CASES_SHEET"] == "编辑" else 2,
    )

    assert register_discovered_validation_counts(
        command_entries, discovery_env, tracker,
    ) == 2
    assert tracker.snapshot() == (0, None)
    assert tracker.registered_total == 7
    assert tracker.set_collected("unrelated", 3)
    assert tracker.snapshot() == (0, 10)


def test_failed_discovery_omits_every_validation_for_the_same_form():
    discovery_env = {
        "EI_COMMON_CASES_EXCEL": "cases.xlsx",
        "EI_COMMON_FIELDS_MANIFEST": "fresh.json",
        "EI_COMMON_CASES_SHEET": "编辑",
        "EI_COMMON_CASE_IDS_JSON": '["EDIT-002"]',
        "EI_MODULE_ID": "PROJECT::edit",
        "EI_FORM_URL": "https://example.test/project/detail",
        "EI_ACTION": "编辑",
    }
    detail_env = {
        **discovery_env,
        "EI_COMMON_CASES_SHEET": "详情",
        "EI_COMMON_CASE_IDS_JSON": '["VIEW-003"]',
    }
    tracker = CaseProgressTracker(("discovery", "edit", "detail"))
    tracker.set_collected("discovery", 1)
    command_entries = [
        ("discovery", SimpleNamespace(), discovery_env, "tests/test_common_field_discovery.py"),
        ("edit", SimpleNamespace(), discovery_env.copy(), "tests/test_common_field_validation.py"),
        ("detail", SimpleNamespace(), detail_env, "tests/test_common_field_validation.py"),
    ]

    assert omit_discovered_validation_commands(
        command_entries, discovery_env, tracker,
    ) == 2
    assert tracker.snapshot() == (0, 1)


def test_manifest_discovery_runs_before_unrelated_page_actions():
    action = (SimpleNamespace(name="搜索"), {}, "tests/test_module_action.py")
    discovery = (
        SimpleNamespace(name="新增"), {}, "tests/test_common_field_discovery.py",
    )
    validation = (
        SimpleNamespace(name="新增"), {}, "tests/test_common_field_validation.py",
    )
    detail = (
        SimpleNamespace(name="详情"), {}, "tests/test_common_detail_validation.py",
    )

    planned = prioritize_progress_discovery_commands([
        detail, action, validation, discovery,
    ])

    assert planned == [discovery, detail, action, validation]


def test_manifest_discovery_is_deduplicated_per_physical_form():
    shared_context = {
        "EI_COMMON_FIELDS_MANIFEST": "fresh.json",
        "EI_MODULE_ID": "PROJECT::edit",
        "EI_FORM_URL": "https://example.test/project/detail",
        "EI_COMPONENT": "project/detail/index",
        "EI_ACTION": "编辑",
        "EI_COMMON_FORM_ACTION": "编辑",
    }
    edit_env = {
        **shared_context,
        "EI_COMMON_CASES_SHEET": "编辑",
        "EI_COMMON_CASE_IDS_JSON": '["EDIT-001"]',
    }
    detail_env = {
        **shared_context,
        "EI_COMMON_CASES_SHEET": "详情",
        "EI_COMMON_CASE_IDS_JSON": '["VIEW-003"]',
    }
    other_action_env = {
        **shared_context,
        "EI_MODULE_ID": "PROJECT::add",
        "EI_ACTION": "新增",
        "EI_COMMON_FORM_ACTION": "新增",
    }
    first_discovery = (
        SimpleNamespace(name="编辑"), edit_env,
        "tests/test_common_field_discovery.py",
    )
    duplicate_discovery = (
        SimpleNamespace(name="编辑"), detail_env,
        "tests/test_common_field_discovery.py",
    )
    other_discovery = (
        SimpleNamespace(name="新增"), other_action_env,
        "tests/test_common_field_discovery.py",
    )
    edit_validation = (
        SimpleNamespace(name="编辑"), edit_env.copy(),
        "tests/test_common_field_validation.py",
    )
    detail_validation = (
        SimpleNamespace(name="编辑"), detail_env.copy(),
        "tests/test_common_field_validation.py",
    )

    assert common_field_discovery_identity(edit_env) == (
        common_field_discovery_identity(detail_env)
    )
    assert common_field_discovery_identity(edit_env) != (
        common_field_discovery_identity(other_action_env)
    )
    planned = prioritize_progress_discovery_commands([
        edit_validation,
        duplicate_discovery,
        detail_validation,
        first_discovery,
        other_discovery,
    ])

    assert planned == [
        duplicate_discovery,
        other_discovery,
        edit_validation,
        detail_validation,
    ]


def test_omitted_dependent_command_is_removed_from_case_total():
    tracker = CaseProgressTracker(("page", "personalized", "discovery", "validation"))
    tracker.set_collected("page", 1)
    tracker.set_collected("personalized", 4)
    tracker.set_collected("discovery", 1)
    assert tracker.total is None
    assert tracker.omit("validation")
    assert tracker.snapshot() == (0, 6)
    assert not tracker.mark_finished("validation", "test_validation.py::test_case[01]")


def test_incomplete_execution_does_not_display_one_hundred_percent():
    assert case_progress_text(3, 4, running=False) == "3/4 用例执行结束 · 75%"
    assert case_progress_text(3, None, running=False) == "执行结束 · 已完成 3 个用例"


def test_progress_event_reader_ignores_malformed_and_unknown_json(tmp_path):
    progress_file = tmp_path / "progress.jsonl"
    assert read_pytest_progress_events(tmp_path / "missing.jsonl") == ([], 0)
    progress_file.write_text(
        "\n".join((
            '{"event":"collected","command_id":"1","count":4}',
            "not-json",
            '{"event":"diagnostic","message":"ignored"}',
            '{"event":"finished","command_id":"1","nodeid":"case-1"}',
        )) + "\n",
        encoding="utf-8",
    )

    events, offset = read_pytest_progress_events(progress_file)

    assert events == [
        {"event": "collected", "command_id": "1", "count": 4},
        {"event": "finished", "command_id": "1", "nodeid": "case-1"},
    ]
    assert offset > 0
    assert read_pytest_progress_events(progress_file, offset) == ([], offset)


def test_progress_event_reader_waits_for_a_complete_jsonl_record(tmp_path):
    progress_file = tmp_path / "progress.jsonl"
    complete = b'{"event":"collected","command_id":"1","count":2}\n'
    partial = b'{"event":"finished","command_id":"1"'
    progress_file.write_bytes(complete + partial)

    events, offset = read_pytest_progress_events(progress_file)

    assert events == [
        {"event": "collected", "command_id": "1", "count": 2},
    ]
    assert offset == len(complete)

    with progress_file.open("ab") as stream:
        stream.write(b',"nodeid":"case-1"}\n')

    assert read_pytest_progress_events(progress_file, offset) == (
        [{"event": "finished", "command_id": "1", "nodeid": "case-1"}],
        progress_file.stat().st_size,
    )


def test_skill_maintenance_gate_failure_is_reported_as_infrastructure_blocker():
    output = """SKILL_MAINTENANCE_GATE=FAIL
Skill maintenance decision required:
- src/ei_ui_smoke/source_form.py -> generic-module-crud-smoke
- tests/test_source_form.py -> ui-smoke-test
Update the owner Skill and run record --skill <name>.
"""

    message = maintenance_gate_failure_message(output)

    assert "尚未执行任何业务测试" in message
    assert "source_form.py -> generic-module-crud-smoke" in message
    assert "test_source_form.py -> ui-smoke-test" in message
    assert maintenance_gate_failure_message("1 failed") == ""


def test_skill_gate_does_not_wait_for_sync_during_an_active_run():
    gate_output = "SKILL_MAINTENANCE_GATE=FAIL\nSkill maintenance decision required:"
    calls = []

    passed, output = check_maintenance_gate_once(
        lambda: calls.append("check") or (1, gate_output)
    )

    assert not passed
    assert output == gate_output
    assert calls == ["check"]


def test_completed_run_generates_report_before_post_run_skill_gate(tmp_path):
    events = []
    results_dir = tmp_path / "allure-results"
    report_dir = tmp_path / "allure-report"
    allure_paths = SimpleNamespace(results=results_dir, report=report_dir)

    actual_report, error, gate_passed = generate_report_before_maintenance_gate(
        allure_paths,
        project_root=tmp_path,
        generate_report=lambda *_args, **_kwargs: events.append("generate") or report_dir,
        open_report=lambda _report: events.append("open"),
        check_gate=lambda: events.append("gate") or False,
    )

    assert events == ["generate", "open", "gate"]
    assert actual_report == report_dir
    assert error == ""
    assert not gate_passed


def test_launcher_uses_a_scoped_gate_deferral_environment_name():
    assert DEFER_SKILL_GATE_ENV == "EI_DEFER_SKILL_MAINTENANCE_GATE"


def test_zentao_checkbox_default_and_required_settings(monkeypatch):
    monkeypatch.setenv("ZENTAO_AUTO_SUBMIT", "true")
    assert default_submit_zentao() is False
    assert environment_flag("true") is True
    assert environment_flag(" TRUE ") is True
    assert environment_flag("false") is False
    assert missing_zentao_settings({"ZENTAO_URL": "url"}) == [
        "ZENTAO_USERNAME", "ZENTAO_PASSWORD"
    ]
    assert missing_zentao_settings({
        "ZENTAO_URL": "url", "ZENTAO_USERNAME": "user", "ZENTAO_PASSWORD": "secret",
    }) == []


def test_common_field_checks_are_only_added_to_standard_add_pages(tmp_path, monkeypatch):
    add_page = ModuleItem(
        "POOL", "资源池", ("资源池",), route="/pool", supports_add=True,
    )
    plain_page = ModuleItem("HOME", "首页", ("首页",), route="/home")
    base = [
        (add_page, {"EI_MODULE_ID": "POOL"}, "tests/test_module_smoke.py"),
        (plain_page, {"EI_MODULE_ID": "HOME"}, "tests/test_module_smoke.py"),
    ]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("字段校验", "ADD-001")],
    )

    for mode in ("probe", "stable"):
        assert add_standard_common_field_commands(
            base, mode=mode, workbook="rules.xlsx", case_ids=["ADD-001（字段校验）"],
            project_root=tmp_path,
        ) == base

    expanded = add_standard_common_field_commands(
        base, mode="standard", workbook="rules.xlsx", case_ids=["ADD-001（字段校验）"],
        project_root=tmp_path,
    )
    common_commands = [item for item in expanded if "common_field" in item[2]]
    assert common_commands
    assert all(item[1]["EI_COMMON_CASES_SHEET"] == "字段校验" for item in common_commands)
    assert all(item[1]["EI_COMMON_CASE_IDS_JSON"] == '["ADD-001"]' for item in common_commands)
    assert [item[2] for item in expanded] == [
        "tests/test_module_smoke.py",
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
        "tests/test_module_smoke.py",
    ]
    assert expanded[1][1]["EI_COMMON_CASES_EXCEL"] == str(tmp_path / "rules.xlsx")
    assert expanded[1][1]["EI_COMMON_FIELDS_MANIFEST"].endswith("POOL.json")


def test_common_field_checks_group_selected_ids_by_sheet(tmp_path, monkeypatch):
    add_page = ModuleItem(
        "POOL", "资源池", ("资源池",), route="/pool", supports_add=True,
    )
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [
            ("新增", "ADD-001"), ("新增", "ADD-002"), ("编辑", "EDIT-001"),
        ],
    )
    expanded = add_standard_common_field_commands(
        [(add_page, {}, "tests/test_module_smoke.py")],
        mode="standard", workbook="rules.xlsx",
        case_ids=["ADD-002（新增）", "EDIT-001（编辑）"],
        project_root=tmp_path,
    )

    common_commands = [item for item in expanded if "common_field" in item[2]]
    assert [item[2] for item in common_commands] == [
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
    ]
    assert [item[1]["EI_COMMON_CASES_SHEET"] for item in common_commands] == [
        "新增", "新增",
    ]
    assert [item[1]["EI_COMMON_CASE_IDS_JSON"] for item in common_commands] == [
        '["ADD-002"]', '["ADD-002"]',
    ]


def test_common_case_sheet_matching_filters_operation_specific_sheets():
    add = ModuleItem("add", "新增", ("资源池", "新增"), operation="新增")
    edit = ModuleItem("edit", "编辑", ("资源池", "编辑"), operation="编辑")
    delete = ModuleItem("delete", "删除", ("资源池", "删除"), operation="删除")
    detail = ModuleItem(
        "detail", "项目立项", ("建设项目", "详情", "项目立项"),
        requires_business_id=True,
    )
    detail_edit = ModuleItem(
        "detail-edit", "编辑", ("建设项目", "详情", "项目立项", "编辑"),
        operation="编辑", requires_business_id=True,
    )
    detail_cancel = ModuleItem(
        "detail-cancel", "取消编辑", ("建设项目", "详情", "项目立项", "取消编辑"),
        operation="取消编辑", requires_business_id=True,
    )
    nested = ModuleItem(
        "nested", "新增", ("建设项目", "详情", "项目决策", "新增", "分区", "新增"),
        operation="新增", operation_path=("新增", "分区", "新增"),
        requires_business_id=True,
    )
    add_page = ModuleItem("pool", "资源池", ("资源池",), supports_add=True)

    assert common_case_sheet_family("新增") == "add"
    assert common_case_sheet_family("编辑") == "edit"
    assert common_case_sheet_family("详情") == "detail"
    assert common_case_sheet_family("删除") == "delete"
    assert common_case_sheet_family("字段校验") == ""
    assert common_case_sheet_matches_target(add, "新增")
    assert common_case_sheet_matches_target(add_page, "新增")
    assert not common_case_sheet_matches_target(add, "编辑")
    assert common_case_sheet_matches_target(edit, "编辑")
    assert common_case_sheet_matches_target(delete, "删除")
    assert common_case_sheet_matches_target(add, "字段校验")
    assert not common_case_sheet_matches_target(edit, "字段校验")
    assert common_case_sheet_matches_target(detail, "详情")
    assert common_case_sheet_matches_target(detail_edit, "详情")
    assert not common_case_sheet_matches_target(detail_cancel, "详情")
    assert not common_case_sheet_matches_target(delete, "详情")
    assert not common_case_sheet_matches_target(nested, "详情")


def test_module_cases_are_added_once_to_matching_standard_add_target(tmp_path, monkeypatch):
    build_add = ModuleItem(
        "BUILD::add", "新增项目", ("建设项目", "新增项目"),
        component="buildProject/index", operation="新增项目", runnable=True,
    )
    build_edit = ModuleItem(
        "BUILD::edit", "编辑", ("建设项目", "编辑"),
        operation="编辑", runnable=True,
    )
    other_add = ModuleItem(
        "OTHER::add", "新增", ("投资管理", "新增"),
        operation="新增", runnable=True,
    )
    base = [
        (item, {"EI_MODULE_ID": item.id}, "tests/test_module_action.py")
        for item in (build_add, build_edit, other_add)
    ]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("新增项目", "LX-ADD-001")],
    )

    expanded = add_standard_module_case_commands(
        base, mode="standard", workbook="module.xlsx",
        case_ids=["LX-ADD-001（新增项目）"], project_root=tmp_path,
    )

    module_commands = [
        command for command in expanded
        if command[2] == "tests/test_build_project_add_personalized.py"
    ]
    assert len(module_commands) == 1
    assert module_commands[0][0] == build_add
    assert module_commands[0][1]["EI_MODULE_CASES_EXCEL"] == str(tmp_path / "module.xlsx")
    assert module_commands[0][1]["EI_MODULE_CASES_SHEET"] == "新增项目"
    assert module_commands[0][1]["EI_MODULE_CASE_IDS_JSON"] == '["LX-ADD-001"]'


def test_module_cases_group_selected_ids_by_sheet(tmp_path, monkeypatch):
    build_add = ModuleItem(
        "BUILD::add", "新增项目", ("建设项目", "新增项目"),
        component="buildProject/index", operation="新增项目", runnable=True,
    )
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [
            ("新增项目", "LX-ADD-001"), ("字段校验", "LX-CHECK-001"),
        ],
    )
    expanded = add_standard_module_case_commands(
        [(build_add, {}, "tests/test_module_action.py")], mode="standard",
        workbook="module.xlsx",
        case_ids=["LX-ADD-001（新增项目）", "LX-CHECK-001（字段校验）"],
        project_root=tmp_path,
    )

    module_commands = [
        item for item in expanded
        if item[2] == "tests/test_build_project_add_personalized.py"
    ]
    assert [item[1]["EI_MODULE_CASES_SHEET"] for item in module_commands] == [
        "新增项目", "字段校验",
    ]


def test_excel_cases_suppress_only_the_covered_target_base_command():
    add = ModuleItem(
        "BUILD::add", "新增项目", ("建设项目", "新增项目"), operation="新增项目"
    )
    query = ModuleItem(
        "BUILD::query", "查询", ("建设项目", "查询"), operation="查询"
    )
    commands = [
        (add, {}, "tests/test_module_action.py"),
        (add, {}, "tests/test_common_field_discovery.py"),
        (add, {}, "tests/test_common_field_validation.py"),
        (add, {}, "tests/test_build_project_add_personalized.py"),
        (query, {}, "tests/test_module_action.py"),
    ]

    filtered = suppress_base_commands_covered_by_excel_cases(commands)

    assert [(target.id, test_file) for target, _env, test_file in filtered] == [
        ("BUILD::add", "tests/test_common_field_discovery.py"),
        ("BUILD::add", "tests/test_common_field_validation.py"),
        ("BUILD::add", "tests/test_build_project_add_personalized.py"),
        ("BUILD::query", "tests/test_module_action.py"),
    ]


def test_standard_add_action_gets_common_field_checks(tmp_path, monkeypatch):
    add = ModuleItem(
        "BUILD::action::2", "新增项目", ("建设项目", "新增项目"),
        route="/buildProject", component="buildProject/index", operation="新增项目",
    )
    base = [(add, {"EI_REQUIRE_ADD": "true"}, "tests/test_module_action.py")]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("新增", "ADD-001")],
    )

    expanded = add_standard_common_field_commands(
        base, mode="standard", workbook="rules.xlsx", case_ids=["ADD-001（新增）"],
        project_root=tmp_path,
    )

    assert [item[2] for item in expanded] == [
        "tests/test_module_action.py",
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
    ]
    assert expanded[1][1]["EI_COMMON_FIELDS_MANIFEST"].endswith(
        "BUILD_action_2.json"
    )
    assert expanded[1][1] == expanded[2][1]


def test_delete_sheet_uses_delete_execution_not_field_discovery(tmp_path, monkeypatch):
    delete = ModuleItem(
        "DECISION::action::6", "删除", ("建设项目", "删除"),
        route="/buildProject/detail", component="buildProject/before/projectDecision/index",
        operation="删除", requires_business_id=True,
    )
    base = [(delete, {"EI_REQUIRE_ADD": "true"}, "tests/test_module_action.py")]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids", lambda _path: [("删除", "DELETE-002")]
    )

    expanded = add_standard_common_field_commands(
        base, mode="standard", workbook="rules.xlsx",
        case_ids=["DELETE-002（删除）"], project_root=tmp_path,
    )
    filtered = suppress_base_commands_covered_by_excel_cases(expanded)

    assert len(filtered) == 1
    _target, environment, test_file = filtered[0]
    assert test_file == "tests/test_module_action.py"
    assert environment["EI_COMMON_DELETE_CASES_SHEET"] == "删除"
    assert environment["EI_COMMON_DELETE_CASE_IDS_JSON"] == '["DELETE-002"]'
    assert "EI_COMMON_FIELDS_MANIFEST" not in environment


def test_nested_add_action_does_not_get_outer_form_common_field_checks(
    tmp_path, monkeypatch,
):
    nested = ModuleItem(
        "DECISION::action::1",
        "新增",
        ("项目决策", "新增", "新增项目决策", "预算及资金来源明细", "新增"),
        route="/buildProject/detail",
        component="buildProject/before/projectDecision/index",
        operation="新增",
        operation_path=("新增", "预算及资金来源明细", "新增"),
        requires_business_id=True,
    )
    base = [(nested, {"EI_REQUIRE_ADD": "true"}, "tests/test_module_action.py")]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("新增", "ADD-001")],
    )

    expanded = add_standard_common_field_commands(
        base,
        mode="standard",
        workbook="rules.xlsx",
        case_ids=["ADD-001（新增）"],
        project_root=tmp_path,
    )

    assert expanded == base


def test_detail_module_outer_add_gets_contextual_common_field_checks(
    tmp_path, monkeypatch,
):
    decision_add = ModuleItem(
        "DECISION::action::0",
        "新增",
        ("建设项目", "详情", "投前管理", "项目决策", "新增"),
        route="/buildProject/detail",
        component="buildProject/before/projectDecision/index",
        operation="新增",
        requires_business_id=True,
    )
    base = [
        (decision_add, {"EI_REQUIRE_ADD": "true"}, "tests/test_module_action.py")
    ]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("新增", "ADD-001")],
    )

    expanded = add_standard_common_field_commands(
        base,
        mode="standard",
        workbook="rules.xlsx",
        case_ids=["ADD-001（新增）"],
        project_root=tmp_path,
    )

    assert [test_file for _target, _env, test_file in expanded] == [
        "tests/test_module_action.py",
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
    ]
    for target, command_env, _test_file in expanded[1:]:
        assert target is decision_add
        assert command_env["EI_REQUIRE_ADD"] == "true"
        assert command_env["EI_COMMON_CASES_SHEET"] == "新增"
        assert command_env["EI_COMMON_CASE_IDS_JSON"] == '["ADD-001"]'
        assert command_env["EI_COMMON_FORM_ACTION"] == "新增"


def test_detail_module_gets_selected_detail_common_case_checks(
    tmp_path, monkeypatch,
):
    establishment_detail = ModuleItem(
        "ESTABLISH",
        "项目立项",
        ("建设项目", "详情", "投前管理", "项目立项"),
        route="/buildProject/detail",
        component="buildProject/establishment/index",
        requires_business_id=True,
    )
    base = [
        (
            establishment_detail,
            {
                "EI_REQUIRES_BUSINESS_ID": "true",
                "EI_MODULE_NAME": "建设项目/详情/投前管理/项目立项",
            },
            "tests/test_module_action.py",
        )
    ]
    monkeypatch.setattr(
        launcher_module,
        "list_xlsx_case_ids",
        lambda _path: [("详情", "VIEW-001"), ("新增", "ADD-001")],
    )
    monkeypatch.setattr(
        launcher_module,
        "read_xlsx_records",
        lambda _path, _sheet: [{"用例ID": "VIEW-001", "功能": "页面"}],
    )

    expanded = add_standard_common_field_commands(
        base,
        mode="standard",
        workbook="rules.xlsx",
        case_ids=["VIEW-001（详情）", "ADD-001（新增）"],
        project_root=tmp_path,
    )

    assert [test_file for _target, _env, test_file in expanded] == [
        "tests/test_module_action.py",
        "tests/test_common_detail_validation.py",
    ]
    for target, command_env, _test_file in expanded[1:]:
        assert target is establishment_detail
        assert command_env["EI_COMMON_CASES_SHEET"] == "详情"
        assert command_env["EI_COMMON_CASE_IDS_JSON"] == '["VIEW-001"]'
        assert command_env["EI_ACTION"] == "详情"
        assert "EI_COMMON_FORM_ACTION" not in command_env
        assert command_env["EI_REQUIRES_BUSINESS_ID"] == "true"


def test_detail_edit_target_gets_its_selected_detail_common_case_checks(
    tmp_path, monkeypatch,
):
    establishment_edit = ModuleItem(
        "ESTABLISH::action::0",
        "编辑",
        ("建设项目", "详情", "投前管理", "项目立项", "编辑"),
        route="/buildProject/detail",
        component="buildProject/establishment/index",
        operation="编辑",
        requires_business_id=True,
    )
    base = [(establishment_edit, {"EI_REQUIRES_BUSINESS_ID": "true"}, "tests/test_module_action.py")]
    monkeypatch.setattr(
        launcher_module,
        "list_xlsx_case_ids",
        lambda _path: [("详情", "VIEW-001"), ("详情", "VIEW-003")],
    )
    monkeypatch.setattr(
        launcher_module,
        "read_xlsx_records",
        lambda _path, _sheet: [
            {"用例ID": "VIEW-001", "功能": "页面"},
            {"用例ID": "VIEW-003", "功能": "编辑"},
        ],
    )

    expanded = add_standard_common_field_commands(
        base,
        mode="standard",
        workbook="rules.xlsx",
        case_ids=["VIEW-001（详情）", "VIEW-003（详情）"],
        project_root=tmp_path,
    )

    assert [test_file for _target, _env, test_file in expanded] == [
        "tests/test_module_action.py",
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
    ]
    for target, command_env, _test_file in expanded[1:]:
        assert target is establishment_edit
        assert command_env["EI_COMMON_CASE_IDS_JSON"] == '["VIEW-003"]'
        assert command_env["EI_COMMON_FORM_ACTION"] == "编辑"
        assert command_env["EI_ALLURE_SUB_SUITE"] == "详情"


def test_build_project_personalized_cases_ignore_project_decision_add(
    tmp_path, monkeypatch,
):
    decision_add = ModuleItem(
        "DECISION::action::0",
        "新增",
        ("建设项目", "详情", "投前管理", "项目决策", "新增"),
        route="/buildProject/detail",
        component="buildProject/before/projectDecision/index",
        operation="新增",
        requires_business_id=True,
    )
    base = [
        (decision_add, {"EI_REQUIRE_ADD": "true"}, "tests/test_module_action.py")
    ]
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("新增项目", "LX-ADD-001")],
    )

    expanded = add_standard_module_case_commands(
        base,
        mode="standard",
        workbook="project.xlsx",
        case_ids=["LX-ADD-001（新增项目）"],
        project_root=tmp_path,
    )

    assert expanded == base


def test_selected_actions_do_not_apply_crud_name_priority():
    delete = ModuleItem("delete", "删除", ("资源池", "删除"), runnable=True, operation="删除")
    edit = ModuleItem("edit", "编辑", ("资源池", "编辑"), runnable=True, operation="编辑")
    add = ModuleItem("add", "新增", ("资源池", "新增"), runnable=True, operation="新增")

    targets = resolve_selected_targets([delete, edit, add], [delete, edit, add])

    assert [item.operation for item in targets] == ["删除", "编辑", "新增"]


def test_action_module_id_produces_windows_safe_log_name():
    assert safe_run_log_name("POOL::action::0") == "POOL_action_0.log"
    assert safe_run_log_name("base/path\\child") == "base_path_child.log"
    assert command_log_name("POOL", "tests/test_common_field_validation.py") == (
        "POOL_common_field_validation.log"
    )


def test_all_actions_are_grouped_into_one_browser_command():
    query = ModuleItem(
        id="POOL::action::0", name="查询", path=("资源池", "查询"),
        component="pool/index", route="/pool", form_code="POOL", operation="查询",
    )
    nested = ModuleItem(
        id="POOL::action::1", name="新增", path=("资源池", "股权结构", "新增"),
        component="pool/index", route="/pool", form_code="POOL", operation="新增",
        operation_path=("新增", "股权结构", "新增"),
    )
    other = ModuleItem(
        id="OTHER::action::0", name="查询", path=("其他", "查询"),
        component="other/index", route="/other", form_code="OTHER", operation="查询",
    )
    commands = [
        (query, {"EI_ACTION": "查询"}, "tests/test_module_action.py"),
        (nested, {"EI_ACTION": "新增", "EI_REQUIRE_ADD": "true"}, "tests/test_module_action.py"),
        (other, {"EI_ACTION": "查询"}, "tests/test_module_action.py"),
    ]

    grouped = group_action_commands(commands)

    assert len(grouped) == 1
    import json
    pool_actions = json.loads(grouped[0][1]["EI_ACTIONS_JSON"])
    assert [item["action"] for item in pool_actions] == ["查询", "新增", "查询"]
    assert pool_actions[1]["action_path"] == ["新增", "股权结构", "新增"]
    assert pool_actions[1]["require_add"] == "true"
    assert pool_actions[2]["component"] == "other/index"


def test_delete_excel_case_command_stays_outside_action_batch():
    query = ModuleItem(
        id="POOL::action::0", name="查询", path=("资源池", "查询"),
        component="pool/index", route="/pool", form_code="POOL", operation="查询",
    )
    delete = ModuleItem(
        id="POOL::action::1", name="删除", path=("资源池", "删除"),
        component="pool/index", route="/pool", form_code="POOL", operation="删除",
    )

    grouped = group_action_commands([
        (query, {"EI_ACTION": "查询"}, "tests/test_module_action.py"),
        (
            delete,
            {
                "EI_ACTION": "删除",
                "EI_COMMON_DELETE_CASES_EXCEL": "rules.xlsx",
                "EI_COMMON_DELETE_CASES_SHEET": "删除",
                "EI_COMMON_DELETE_CASE_IDS_JSON": '["DELETE-002"]',
            },
            "tests/test_module_action.py",
        ),
    ])

    assert len(grouped) == 2
    assert grouped[0][1]["EI_ACTIONS_JSON"]
    _target, delete_env, test_file = grouped[1]
    assert test_file == "tests/test_module_action.py"
    assert "EI_ACTIONS_JSON" not in delete_env
    assert delete_env["EI_COMMON_DELETE_CASE_IDS_JSON"] == '["DELETE-002"]'


def test_page_smoke_is_suppressed_when_selected_actions_cover_same_page():
    page = ModuleItem("POOL", "资源池", ("资源池",), component="pool/index", route="/pool")
    add = ModuleItem(
        "POOL::add", "新增", ("资源池", "新增"), component="pool/index",
        route="/pool", operation="新增",
    )
    other = ModuleItem("OTHER", "其他", ("其他",), component="other/index", route="/other")

    commands = suppress_pages_covered_by_actions([
        (page, {}, "tests/test_module_smoke.py"),
        (add, {}, "tests/test_module_action.py"),
        (other, {}, "tests/test_module_smoke.py"),
    ])

    assert [command[0].id for command in commands] == ["POOL::add", "OTHER"]


def test_action_coverage_keeps_no_button_detail_page_for_its_worksheet_cases():
    detail = ModuleItem(
        "ESTABLISH",
        "项目立项",
        ("建设项目", "详情", "投前管理", "项目立项"),
        component="buildProject/establishment/index",
        route="/buildProject/detail",
        requires_business_id=True,
    )
    edit = ModuleItem(
        "ESTABLISH::action::0",
        "编辑",
        detail.path + ("编辑",),
        component=detail.component,
        route=detail.route,
        operation="编辑",
        requires_business_id=True,
    )

    commands = suppress_pages_covered_by_actions([
        (detail, {}, "tests/test_module_smoke.py"),
        (edit, {}, "tests/test_module_action.py"),
    ])

    assert is_detail_page_target(detail)
    assert [command[0].id for command in commands] == ["ESTABLISH", "ESTABLISH::action::0"]


def test_no_button_detail_page_and_edit_expand_their_own_detail_rows(tmp_path, monkeypatch):
    detail = ModuleItem(
        "ESTABLISH",
        "项目立项",
        ("建设项目", "详情", "投前管理", "项目立项"),
        component="buildProject/establishment/index",
        route="/buildProject/detail",
        requires_business_id=True,
    )
    edit = ModuleItem(
        "ESTABLISH::action::0",
        "编辑",
        detail.path + ("编辑",),
        component=detail.component,
        route=detail.route,
        operation="编辑",
        requires_business_id=True,
    )
    monkeypatch.setattr(
        launcher_module,
        "list_xlsx_case_ids",
        lambda _path: [("详情", "VIEW-001"), ("详情", "VIEW-003")],
    )
    monkeypatch.setattr(
        launcher_module,
        "read_xlsx_records",
        lambda _path, _sheet: [
            {"用例ID": "VIEW-001", "功能": "页面"},
            {"用例ID": "VIEW-003", "功能": "编辑"},
        ],
    )

    commands = suppress_pages_covered_by_actions([
        (detail, {}, "tests/test_module_smoke.py"),
        (edit, {}, "tests/test_module_action.py"),
    ])
    commands = add_standard_common_field_commands(
        commands,
        mode="standard",
        workbook="rules.xlsx",
        case_ids=["VIEW-001（详情）", "VIEW-003（详情）"],
        project_root=tmp_path,
    )
    commands = suppress_base_commands_covered_by_excel_cases(commands)

    assert [test_file for _target, _env, test_file in commands] == [
        "tests/test_common_detail_validation.py",
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
    ]
    assert commands[0][1]["EI_COMMON_CASE_IDS_JSON"] == '["VIEW-001"]'
    assert all(command[1]["EI_COMMON_CASE_IDS_JSON"] == '["VIEW-003"]' for command in commands[1:])


def test_grouped_command_reports_each_logical_action_target():
    query = ModuleItem(
        id="POOL::action::0", name="查询", path=("资源池", "查询"),
        component="pool/index", route="/pool", form_code="POOL", operation="查询",
    )
    edit = ModuleItem(
        id="POOL::action::1", name="编辑", path=("资源池", "编辑"),
        component="pool/index", route="/pool", form_code="POOL", operation="编辑",
    )
    grouped = group_action_commands([
        (query, {"EI_ACTION": "查询"}, "tests/test_module_action.py"),
        (edit, {"EI_ACTION": "编辑"}, "tests/test_module_action.py"),
    ])

    assert len(grouped) == 1
    assert command_target_names(grouped) == ["资源池/查询", "资源池/编辑"]


def test_normal_command_reports_its_full_tree_path():
    module = ModuleItem(id="POOL", name="资源池", path=("项目管理", "资源池"))

    names = command_target_names([(module, {}, "tests/test_module_smoke.py")])

    assert names == ["项目管理 / 资源池"]


def test_url_history_keeps_most_recent_unique_links(tmp_path):
    cache = tmp_path / "artifacts" / "launcher-history.json"

    assert load_url_history(cache) == []
    save_url_history(cache, "https://first.example/ei-view/")
    save_url_history(cache, "https://second.example/ei-view/")
    history = save_url_history(cache, "https://first.example/ei-view/")

    assert history == [
        "https://first.example/ei-view/",
        "https://second.example/ei-view/",
    ]
    assert load_url_history(cache) == history


def test_url_history_is_limited_and_ignores_invalid_cache(tmp_path):
    cache = tmp_path / "launcher-history.json"
    cache.write_text("not-json", encoding="utf-8")

    assert load_url_history(cache) == []
    for index in range(12):
        save_url_history(cache, f"https://host-{index}.example/ei-view/")

    history = load_url_history(cache)
    assert len(history) == 10
    assert history[0] == "https://host-11.example/ei-view/"
    assert history[-1] == "https://host-2.example/ei-view/"


class _StubValue:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _StubWidget:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)


class _StubMultiSelect:
    def __init__(self, options=(), selected=()):
        self.options = list(options)
        self.selected = list(selected)
        self.display_labels = {}

    def option_values(self):
        return list(self.options)

    def selected_values(self):
        return list(self.selected)

    def set_options(self, values, selected, *, display_labels=None):
        self.options = list(values)
        self.selected = list(selected)
        self.display_labels = display_labels or {}


def test_refresh_common_case_ids_removes_ids_from_unselected_sheets(tmp_path):
    launcher = SimpleNamespace(
        common_cases_excel=_StubValue(str(tmp_path / "cases.xlsx")),
        project_root=tmp_path,
        common_cases_sheet_input=_StubMultiSelect(
            ["新增", "编辑"], ["编辑"]
        ),
        common_case_ids_input=_StubMultiSelect(
            ["ADD-001（新增）", "EDIT-001（编辑）"],
            ["ADD-001（新增）", "EDIT-001（编辑）"],
        ),
    )

    values = Launcher.refresh_common_case_ids(
        launcher,
        references=[("新增", "ADD-001"), ("编辑", "EDIT-001")],
    )

    assert values == ["EDIT-001（编辑）"]
    assert launcher.common_case_ids_input.selected_values() == ["EDIT-001（编辑）"]
    assert launcher.common_case_ids_input.display_labels == {
        "EDIT-001（编辑）": "EDIT-001"
    }


def test_runtime_menu_capture_uses_url_aligned_to_source_view(monkeypatch, tmp_path):
    source_root = tmp_path / "source"
    (source_root / "fi-view" / "src" / "views").mkdir(parents=True)
    original_url = (
        "https://host/uim-view/#/login?"
        "redirect=%2Fei-view%2F%23%2FbuildProject"
    )
    captured = {}
    finished = []

    def fake_capture_menu(url, **kwargs):
        captured["url"] = url
        captured["source_root"] = kwargs["source_root"]
        return {"data": {}}, "artifacts/auth-state.json"

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(launcher_module, "capture_menu", fake_capture_menu)
    monkeypatch.setattr(launcher_module, "modules_from_menu", lambda _payload, _root: [])
    monkeypatch.setattr(launcher_module.threading, "Thread", ImmediateThread)
    launcher = SimpleNamespace(
        system_url=_StubValue(original_url),
        source=_StubValue(str(source_root)),
        username=_StubValue(""),
        password=_StubValue(""),
        storage=_StubValue("auth-state.json"),
        headless=_StubValue(True),
        fetch_menu_button=_StubWidget(),
        status=_StubValue(""),
        after=lambda _delay, callback, *args: callback(*args),
        _finish_runtime_menu_error=lambda error: finished.append(("error", error)),
        _finish_runtime_menu=lambda items, state, used_url="": finished.append(
            (items, state, used_url)
        ),
    )

    Launcher.fetch_runtime_menu(launcher)

    assert captured["url"] == (
        "https://host/uim-view/#/login?"
        "redirect=%2Ffi-view%2F%23%2FbuildProject"
    )
    assert captured["source_root"] == source_root
    assert finished == [([], "artifacts/auth-state.json", original_url)]


def test_run_selected_builds_environment_and_form_url_from_aligned_url(
    monkeypatch, tmp_path
):
    source_root = tmp_path / "source"
    (source_root / "fi-view" / "src" / "views").mkdir(parents=True)
    original_url = (
        "https://host/uim-view/#/login?"
        "redirect=%2Fei-view%2F%23%2FbuildProject"
    )
    target = launcher_module.ModuleItem(
        id="BUILD::query",
        name="查询",
        path=("建设项目", "查询"),
        source_file="src/views/buildProject/index.vue",
        component="buildProject/index",
        route="/buildProject",
        runnable=True,
        operation="查询",
    )
    started = []

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            started.append(self)

    monkeypatch.setattr(launcher_module.threading, "Thread", DeferredThread)
    launcher = SimpleNamespace(
        _selected_targets=lambda: [target],
        storage=_StubValue("auth-state.json"),
        username=_StubValue(""),
        password=_StubValue(""),
        mode=_StubValue("probe"),
        submit_zentao=_StubValue(False),
        common_cases_excel=_StubValue(""),
        module_cases_excel=_StubValue(""),
        source=_StubValue(str(source_root)),
        system_url=_StubValue(original_url),
        headless=_StubValue(True),
        common_cases_sheet_input=SimpleNamespace(selected_values=lambda: []),
        module_cases_sheet_input=SimpleNamespace(selected_values=lambda: []),
        common_case_ids_input=SimpleNamespace(selected_values=lambda: []),
        module_case_ids_input=SimpleNamespace(selected_values=lambda: []),
        project_root=tmp_path,
        run_button=_StubWidget(),
        status=_StubValue(""),
        _set_execution_progress=lambda *_args: None,
        _execute_commands_worker=lambda *_args: None,
    )

    Launcher.run_selected(launcher)

    assert len(started) == 1
    commands, _preflight, _mode, base_url, _headless, _submit = started[0].args
    aligned_url = (
        "https://host/uim-view/#/login?"
        "redirect=%2Ffi-view%2F%23%2FbuildProject"
    )
    assert base_url == aligned_url
    assert commands[0][1]["EI_BASE_URL"] == aligned_url
    assert commands[0][1]["EI_FORM_URL"] == "https://host/fi-view/#/buildProject"


def test_run_selected_uses_all_sheets_and_ids_when_sheet_and_id_selection_are_empty(
    monkeypatch, tmp_path
):
    source_root = tmp_path / "source"
    (source_root / "fi-view" / "src" / "views").mkdir(parents=True)
    workbook = tmp_path / "common.xlsx"
    workbook.write_bytes(b"placeholder")
    target = launcher_module.ModuleItem(
        id="BUILD::add", name="新增项目", path=("建设项目", "新增项目"),
        route="/buildProject", runnable=True, operation="新增项目",
    )
    warnings = []
    started = []

    class DeferredThread:
        def __init__(self, *, target, args, **_kwargs):
            self.target = target
            self.args = args

        def start(self):
            started.append(self)

    monkeypatch.setattr(
        launcher_module.messagebox, "showwarning",
        lambda title, message: warnings.append((title, message)),
    )
    monkeypatch.setattr(launcher_module.threading, "Thread", DeferredThread)
    monkeypatch.setattr(
        launcher_module, "list_xlsx_case_ids",
        lambda _path: [("新增", "ADD-001"), ("新增", "ADD-002")],
    )
    launcher = SimpleNamespace(
        _selected_targets=lambda: [target],
        storage=_StubValue("auth-state.json"), username=_StubValue(""),
        password=_StubValue(""), mode=_StubValue("standard"),
        submit_zentao=_StubValue(False),
        common_cases_excel=_StubValue(str(workbook)), module_cases_excel=_StubValue(""),
        source=_StubValue(str(source_root)), system_url=_StubValue("https://host/fi-view/"),
        headless=_StubValue(True), project_root=tmp_path,
        common_cases_sheet_input=SimpleNamespace(selected_values=lambda: []),
        common_case_ids_input=SimpleNamespace(
            option_values=lambda: ["ADD-001（新增）", "ADD-002（新增）"],
            selected_values=lambda: [],
        ),
        module_cases_sheet_input=SimpleNamespace(selected_values=lambda: []),
        module_case_ids_input=SimpleNamespace(selected_values=lambda: []),
        refresh_common_case_sheets=lambda **_kwargs: ["新增"],
        run_button=_StubWidget(), status=_StubValue(""),
        _set_execution_progress=lambda *_args: None,
        _execute_commands_worker=lambda *_args: None,
    )

    Launcher.run_selected(launcher)

    assert warnings == []
    assert len(started) == 1
    commands = started[0].args[0]
    common_commands = [
        command for command in commands
        if command[2] in {
            "tests/test_common_field_discovery.py",
            "tests/test_common_field_validation.py",
        }
    ]
    assert len(common_commands) == 2
    assert all(
        command[1]["EI_COMMON_CASE_IDS_JSON"] == '["ADD-001", "ADD-002"]'
        for command in common_commands
    )
    assert [command[2] for command in commands] == [
        "tests/test_common_field_discovery.py",
        "tests/test_common_field_validation.py",
    ]


def test_first_two_menu_levels_are_open_and_deeper_levels_are_collapsed():
    detail = ModuleItem(
        id="detail:buildProject:decision",
        name="项目决策",
        path=("建设项目", "详情", "投前管理", "项目决策"),
        requires_business_id=True,
    )

    assert should_open_tree_branch(detail, depth=0, query="")
    assert should_open_tree_branch(detail, depth=1, query="")
    assert not should_open_tree_branch(detail, depth=2, query="")
    assert not should_open_tree_branch(detail, depth=3, query="")
    assert should_open_tree_branch(detail, depth=3, query="项目决策")


def test_detail_page_actions_follow_the_current_tree_selection():
    parent = ModuleItem(
        "detail", "项目立项", ("建设项目", "详情", "投前管理", "项目立项"),
        runnable=False, requires_business_id=True,
    )
    edit = ModuleItem(
        "edit", "编辑", parent.path + ("编辑",), runnable=False,
        requires_business_id=True, operation="编辑",
    )
    cancel = ModuleItem(
        "cancel", "取消编辑", parent.path + ("取消编辑",), runnable=False,
        requires_business_id=True, operation="取消编辑",
    )

    assert is_executable_target(parent)
    assert is_executable_target(edit)
    assert is_executable_target(cancel)
    assert resolve_selected_targets([parent, edit, cancel], [parent]) == [parent]
    assert resolve_selected_targets([parent, edit, cancel], [parent, edit, cancel]) == [
        parent, edit, cancel,
    ]


def test_deep_normal_menu_stays_collapsed_without_search():
    normal = ModuleItem(id="normal", name="普通模块", path=("一级", "二级", "三级"))

    assert not should_open_tree_branch(normal, depth=2, query="")
    assert should_open_tree_branch(normal, depth=2, query="三级")


def test_storage_dialog_opens_current_file_directory(tmp_path: Path):
    storage = tmp_path / "artifacts" / "auth-state.json"
    storage.parent.mkdir()
    storage.write_text("{}", encoding="utf-8")

    initial_dir, initial_file = storage_dialog_defaults(tmp_path, str(storage))

    assert initial_dir == storage.parent
    assert initial_file == "auth-state.json"


def test_storage_dialog_resolves_relative_file_from_project(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    initial_dir, initial_file = storage_dialog_defaults(tmp_path, "artifacts/auth-state.json")

    assert initial_dir == artifacts
    assert initial_file == "auth-state.json"


def test_storage_dialog_defaults_to_artifacts(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    assert storage_dialog_defaults(tmp_path, "") == (artifacts, "")


def test_runnable_parent_selection_uses_the_cascaded_tree_selection():
    parent = ModuleItem(id="BASIC", name="基础管理", path=("基础管理",), runnable=True)
    child = ModuleItem(id="PLATFORM", name="管理平台", path=("基础管理", "管理平台"), runnable=True)
    grandchild = ModuleItem(id="TEAM", name="基金管理团队", path=("基础管理", "投资人库", "基金管理团队"), runnable=True)
    unrelated = ModuleItem(id="REPORT", name="报表", path=("统计报表",), runnable=True)

    targets = resolve_selected_targets(
        [parent, child, grandchild, unrelated], [parent, child, grandchild]
    )

    assert [item.id for item in targets] == ["BASIC", "PLATFORM", "TEAM"]


def test_directory_selection_requires_the_cascaded_runnable_descendants():
    directory = ModuleItem(id="BASIC", name="基础管理", path=("基础管理",), runnable=False)
    child = ModuleItem(id="PLATFORM", name="管理平台", path=("基础管理", "管理平台"), runnable=True)

    assert resolve_selected_targets([directory, child], [directory]) == []
    assert resolve_selected_targets([directory, child], [directory, child]) == [child]


def test_overlapping_parent_and_child_selection_does_not_duplicate_execution():
    parent = ModuleItem(id="BASIC", name="基础管理", path=("基础管理",), runnable=True)
    child = ModuleItem(id="PLATFORM", name="管理平台", path=("基础管理", "管理平台"), runnable=True)

    targets = resolve_selected_targets([parent, child], [parent, child])

    assert [item.id for item in targets] == ["BASIC", "PLATFORM"]


def test_manual_child_deselection_overrides_a_parent_cascade():
    parent = ModuleItem("PRE", "投前管理", ("建设项目", "详情", "投前管理"), runnable=False)
    decision = ModuleItem(
        "DECISION", "项目决策", parent.path + ("项目决策",), runnable=True,
    )
    adjustment = ModuleItem(
        "CHANGE", "重大调整", parent.path + ("重大调整",), runnable=True,
    )
    adjustment_add = ModuleItem(
        "CHANGE::add", "新增", adjustment.path + ("新增",),
        runnable=True, operation="新增",
    )

    # Clicking the parent selects all descendants.  After the user clears the
    # adjustment branch, only the still-selected decision branch may run.
    assert resolve_selected_targets(
        [parent, decision, adjustment, adjustment_add], [parent, decision]
    ) == [decision]


def test_selected_targets_follow_tree_order_not_click_order():
    first = ModuleItem("first", "查询", ("资源池", "查询"), runnable=True, operation="查询")
    second = ModuleItem("second", "重置", ("资源池", "重置"), runnable=True, operation="重置")
    third = ModuleItem("third", "编辑", ("资源池", "编辑"), runnable=True, operation="编辑")

    targets = resolve_selected_targets(
        [first, second, third], [third, first, second]
    )

    assert targets == [first, second, third]


def test_cancel_delete_removes_delete_actions_from_cascaded_parent_selection():
    parent = ModuleItem("POOL", "资源池", ("资源池",), runnable=False)
    query = ModuleItem(
        "query", "查询", ("资源池", "查询"), runnable=True, operation="查询",
    )
    delete = ModuleItem(
        "delete", "删除", ("资源池", "删除"), runnable=True, operation="删除",
    )
    nested_delete = ModuleItem(
        "nested-delete", "删除", ("资源池", "股权结构", "删除"),
        runnable=True, operation="批量删除",
    )

    expanded = resolve_selected_targets(
        [parent, query, delete, nested_delete], [parent, query, delete, nested_delete]
    )

    assert exclude_delete_targets(expanded) == [query]


def test_cancel_delete_keeps_normal_page_whose_name_contains_delete():
    page = ModuleItem("history", "删除记录", ("审计", "删除记录"), runnable=True)

    assert exclude_delete_targets([page]) == [page]


def test_selected_actions_preserve_exact_ui_order_without_name_priority():
    actions = [
        ModuleItem(str(index), name, ("资源池", name), runnable=True, operation=name)
        for index, name in enumerate(("查询", "重置", "新增", "立项准备", "编辑", "删除"))
    ]

    targets = resolve_selected_targets(actions, list(reversed(actions)))

    assert [item.operation for item in targets] == [
        "查询", "重置", "新增", "立项准备", "编辑", "删除",
    ]


def test_unmatched_runtime_component_is_blocked_before_page_access_false_pass():
    item = ModuleItem(
        id="resourcePool",
        name="资源池",
        path=("项目管理", "资源池"),
        component="/srcEi/views/projectResourcePool/index",
        runnable=True,
    )

    error = target_preflight_error(item)

    assert "未匹配到所选源码" in error
    assert "页面访问假通过" in error


def test_matched_runtime_component_passes_preflight():
    item = ModuleItem(
        id="resourcePool::action::0",
        name="新增",
        path=("项目管理", "资源池", "新增"),
        source_file="src/views/projectResourcePool/index.vue",
        component="projectResourcePool/index",
        runnable=True,
        operation="新增",
    )

    assert target_preflight_error(item) == ""


def test_action_without_source_match_fails_preflight():
    item = ModuleItem(
        id="resourcePool::action::prepare",
        name="立项准备",
        path=("项目管理", "资源池", "立项准备"),
        component="/srcEi/views/projectResourcePool/index",
        route="/resourcePool",
        runnable=True,
        operation="立项准备",
    )

    assert "未匹配到所选源码" in target_preflight_error(item)


def test_batch_failure_message_lists_every_failed_module(tmp_path: Path):
    failures = [
        ("基础管理 / 管理平台", "first error", tmp_path / "platform.log"),
        ("基础管理 / 投资人库", "second error", tmp_path / "investor.log"),
        ("基础管理 / 企业库", "last error", tmp_path / "enterprise.log"),
    ]

    message = format_failure_message(failures, tmp_path)

    assert "共 3 个模块执行失败" in message
    assert "1. 基础管理 / 管理平台" in message
    assert "2. 基础管理 / 投资人库" in message
    assert "3. 基础管理 / 企业库" in message
    assert "last error" not in message
    assert f"完整日志目录：{tmp_path}" in message


def test_grouped_action_failure_names_show_each_failed_button():
    target = ModuleItem("add", "新增", ("项目实施", "新增"), operation="新增")
    actions = [
        {"module_name": f"项目实施/{name}", "action": name}
        for name in ("新增", "刷新", "编辑", "删除")
    ]
    output = "\n".join([
        "FAILED tests/test_module_action.py::test_selected_page_action[01-add]",
        "FAILED tests/test_module_action.py::test_selected_page_action[03-edit]",
        "FAILED tests/test_module_action.py::test_selected_page_action[04-delete]",
    ])

    names = command_failure_names(
        target,
        {"EI_ACTIONS_JSON": __import__("json").dumps(actions, ensure_ascii=False)},
        "tests/test_module_action.py",
        output,
    )

    assert names == ["项目实施/新增", "项目实施/编辑", "项目实施/删除"]


def test_common_field_failure_name_includes_stage():
    target = ModuleItem("add", "新增", ("项目实施", "新增"), operation="新增")

    assert command_failure_names(
        target, {}, "tests/test_common_field_discovery.py"
    ) == ["项目实施 / 新增（通用字段发现）"]
