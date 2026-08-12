from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from ei_ui_smoke.allure_report import (
    apply_allure_report_data_overrides,
    apply_allure_report_display_overrides,
    apply_allure_report_layout_overrides,
    format_allure_common_case_title,
    format_allure_case_ids,
    AllurePaths,
    create_allure_paths,
    generate_allure_report,
    open_allure_report,
    read_latest_paths,
    set_allure_common_case_metadata,
    set_allure_hidden_parameter,
    set_allure_module_metadata,
    write_environment,
    write_latest_paths,
)


def test_format_allure_case_ids_keeps_selected_ids_once_in_order() -> None:
    assert format_allure_case_ids(
        ["ADD-001", "ADD-001", "ADD-002", "ADD-002"]
    ) == "ADD-001 / ADD-002"
    assert format_allure_case_ids(
        ["ADD-019", "ADD-019", "ADD-019"]
    ) == "ADD-019"


def test_format_allure_common_case_title_splits_text_and_case_ids() -> None:
    assert format_allure_common_case_title(
        title="全部必填项：触发并逐项消除提示（18个检查点）",
        display_case_id="ADD-001 / ADD-002",
    ) == "全部必填项：触发并逐项消除提示（18个检查点）\n【ADD-001 / ADD-002】"


def test_allure_paths_and_latest_record(tmp_path: Path) -> None:
    paths = create_allure_paths(tmp_path, stamp="20260730_120000")
    assert paths.results.is_dir()
    assert paths.report.name == "allure-report-20260730_120000"

    write_latest_paths(tmp_path, paths)
    assert read_latest_paths(tmp_path) == AllurePaths(paths.results.resolve(), paths.report.resolve())


def test_write_environment_omits_empty_values(tmp_path: Path) -> None:
    write_environment(tmp_path, {"mode": "stable", "empty": "", "count": 2})
    assert (tmp_path / "environment.properties").read_text(encoding="utf-8") == "mode=stable\ncount=2\n"


def test_open_report_uses_allure_server(monkeypatch, tmp_path: Path) -> None:
    popen = Mock()
    monkeypatch.setattr("ei_ui_smoke.allure_report.shutil.which", lambda _name: "allure")
    monkeypatch.setattr("ei_ui_smoke.allure_report.subprocess.Popen", popen)

    open_allure_report(tmp_path)

    args, kwargs = popen.call_args
    assert args[0] == ["allure", "open", str(tmp_path)]
    assert kwargs["stdout"] is not None


def test_generate_report_hides_windows_console(monkeypatch, tmp_path: Path) -> None:
    run = Mock(return_value=subprocess.CompletedProcess([], 0, ""))
    monkeypatch.setattr("ei_ui_smoke.allure_report.shutil.which", lambda _name: "allure")
    monkeypatch.setattr("ei_ui_smoke.allure_report.os.name", "nt")
    monkeypatch.setattr("ei_ui_smoke.allure_report.subprocess.run", run)
    paths = create_allure_paths(tmp_path, stamp="20260804_000000")

    generate_allure_report(paths, project_root=tmp_path)

    assert run.call_args.kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_report_layout_override_preserves_allure_title_line_breaks(tmp_path: Path) -> None:
    styles = tmp_path / "styles.css"
    styles.write_text(".node__title{display:flex}", encoding="utf-8")

    apply_allure_report_layout_overrides(tmp_path)
    apply_allure_report_layout_overrides(tmp_path)

    text = styles.read_text(encoding="utf-8")
    assert text.count("preserve test title line breaks") == 1
    assert ".node__title .long-line,.test-result__name{white-space:pre-line;}" in text


def test_report_data_override_hides_two_line_case_display_parameters(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    case_dir = data_dir / "test-cases"
    widget_dir = tmp_path / "widgets"
    case_dir.mkdir(parents=True)
    widget_dir.mkdir()
    two_line_name = "全部必填项：触发并逐项消除提示（18个检查点）\n【ADD-001 / ADD-002】"
    (data_dir / "suites.json").write_text(
        (
            '{"children":[{"name":"'
            + two_line_name.replace("\n", "\\n")
            + '","parameters":["\\u0027TX-001\\u0027","\\u0027module\\u0027"]},'
            '{"name":"test_discover_common_fields","parameters":["\\u0027module\\u0027"]}]}'
        ),
        encoding="utf-8",
    )
    (case_dir / "case.json").write_text(
        (
            '{"name":"'
            + two_line_name.replace("\n", "\\n")
            + '","parameters":[{"name":"common_field_case","value":"\\u0027TX-001\\u0027"}],'
            '"parameterValues":["\\u0027TX-001\\u0027"]}'
        ),
        encoding="utf-8",
    )
    (widget_dir / "duration.json").write_text(
        '{"items":[{"name":"普通用例","parameters":["keep"]}]}',
        encoding="utf-8",
    )

    apply_allure_report_data_overrides(tmp_path)

    suites = (data_dir / "suites.json").read_text(encoding="utf-8")
    case = (case_dir / "case.json").read_text(encoding="utf-8")
    widget = (widget_dir / "duration.json").read_text(encoding="utf-8")
    assert '"parameters":[]' in suites
    assert '"test_discover_common_fields","parameters":["\'module\'"]' in suites
    assert '"parameters":[]' in case
    assert '"parameterValues":[]' in case
    assert '"parameters":["keep"]' in widget


def test_report_display_overrides_apply_layout_and_data(tmp_path: Path) -> None:
    (tmp_path / "styles.css").write_text(".node__title{display:flex}", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "suites.json").write_text(
        '{"name":"标题\\n【ADD-001】","parameters":["\\u0027TX-001\\u0027"]}',
        encoding="utf-8",
    )

    apply_allure_report_display_overrides(tmp_path)

    assert "white-space:pre-line" in (tmp_path / "styles.css").read_text(encoding="utf-8")
    assert '"parameters":[]' in (data_dir / "suites.json").read_text(encoding="utf-8")


def test_module_metadata_separates_allure_results(monkeypatch) -> None:
    dynamic = SimpleNamespace(
        title=Mock(), parameter=Mock(), parent_suite=Mock(), suite=Mock(),
        sub_suite=Mock(), feature=Mock(), story=Mock(),
    )
    monkeypatch.setitem(sys.modules, "allure", SimpleNamespace(dynamic=dynamic))

    set_allure_module_metadata(
        module_id="BUILD_PROJECT",
        module_name="建设项目/项目维护",
        form_code="PROJECT_FORM",
        test_title="自动新增及详情核对",
    )

    dynamic.title.assert_called_once_with("项目维护 - 自动新增及详情核对")
    dynamic.parameter.assert_any_call("module_id", "BUILD_PROJECT")
    dynamic.parameter.assert_any_call("form_code", "PROJECT_FORM", excluded=True)
    dynamic.parent_suite.assert_called_once_with("建设项目")
    dynamic.suite.assert_called_once_with("建设项目")
    dynamic.sub_suite.assert_called_once_with("项目维护")


def test_module_metadata_allows_common_case_suite_override(monkeypatch) -> None:
    dynamic = SimpleNamespace(
        title=Mock(), parameter=Mock(), parent_suite=Mock(), suite=Mock(),
        sub_suite=Mock(), feature=Mock(), story=Mock(),
    )
    monkeypatch.setitem(sys.modules, "allure", SimpleNamespace(dynamic=dynamic))

    set_allure_module_metadata(
        module_id="BUILD_PROJECT",
        module_name="建设项目/项目立项/编辑",
        sub_suite_override="详情",
    )

    dynamic.suite.assert_called_once_with("项目立项")
    dynamic.sub_suite.assert_called_once_with("详情")


def test_common_case_metadata_replaces_verbose_pytest_parameter(monkeypatch) -> None:
    dynamic = SimpleNamespace(title=Mock(), parameter=Mock())
    monkeypatch.setitem(
        sys.modules,
        "allure",
        SimpleNamespace(dynamic=dynamic, parameter_mode=SimpleNamespace(HIDDEN="hidden")),
    )

    set_allure_common_case_metadata(
        title="实施主体公司：只能选择一项",
        case_id="inveId-ADD-074",
        display_case_id="ADD-074",
    )

    dynamic.title.assert_called_once_with("实施主体公司：只能选择一项\n【ADD-074】")
    dynamic.parameter.assert_called_once_with(
        "common_field_case", "inveId-ADD-074", mode="hidden"
    )


def test_common_case_metadata_can_replace_custom_pytest_parameter(monkeypatch) -> None:
    dynamic = SimpleNamespace(title=Mock(), parameter=Mock())
    monkeypatch.setitem(
        sys.modules,
        "allure",
        SimpleNamespace(dynamic=dynamic, parameter_mode=SimpleNamespace(HIDDEN="hidden")),
    )

    set_allure_common_case_metadata(
        title="项目类型：码值选项完整性",
        case_id="BP-ADD-001",
        parameter_name="project_case",
    )

    dynamic.title.assert_called_once_with("项目类型：码值选项完整性\n【BP-ADD-001】")
    dynamic.parameter.assert_called_once_with("project_case", "BP-ADD-001", mode="hidden")


def test_common_case_metadata_hides_the_detail_pytest_parameter(monkeypatch) -> None:
    dynamic = SimpleNamespace(title=Mock(), parameter=Mock())
    monkeypatch.setitem(
        sys.modules,
        "allure",
        SimpleNamespace(dynamic=dynamic, parameter_mode=SimpleNamespace(HIDDEN="hidden")),
    )

    set_allure_common_case_metadata(
        title="建设项目 / 详情 / 投前管理 / 项目立项：详情页面名称检查",
        case_id="VIEW-001",
        parameter_name="detail_case",
    )

    dynamic.title.assert_called_once_with(
        "建设项目 / 详情 / 投前管理 / 项目立项：详情页面名称检查\n【VIEW-001】"
    )
    dynamic.parameter.assert_called_once_with(
        "detail_case", "VIEW-001", mode="hidden"
    )


def test_hidden_allure_parameter_falls_back_when_mode_is_unavailable(monkeypatch) -> None:
    dynamic = SimpleNamespace(parameter=Mock())
    monkeypatch.setitem(sys.modules, "allure", SimpleNamespace(dynamic=dynamic))

    set_allure_hidden_parameter("transaction_id", "TX-001")

    dynamic.parameter.assert_called_once_with("transaction_id", "TX-001")
