from __future__ import annotations

import json
import re
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from ei_ui_smoke.allure_report import set_allure_common_case_metadata
from ei_ui_smoke.common_field_cases import discover_common_fields, read_xlsx_records
from ei_ui_smoke.common_field_executor import CommonFieldExecutor
from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs
from ei_ui_smoke.models import DomField
from ei_ui_smoke.source_form import discover_custom_form_fields


DEFAULT_WORKBOOK = Path(__file__).parent / "Common_Test_Cases" / "建设项目_个性化用例.xlsx"
DEFAULT_SHEET_NAME = "新增项目"
def _load_project_cases(
    workbook: Path, sheet_name: str, case_ids: list[str] | None = None,
) -> list[Any]:
    filter_by_case_id = case_ids is not None
    selected_case_ids = {str(case_id).strip() for case_id in case_ids or [] if str(case_id).strip()}
    if filter_by_case_id and not selected_case_ids:
        raise pytest.UsageError(f"{sheet_name} 未选择任何用例编号")
    found_case_ids: set[str] = set()
    params = []
    for row_number, row in enumerate(read_xlsx_records(workbook, sheet_name), start=2):
        case_id = str(row.get("用例ID") or "").strip()
        if not case_id:
            raise pytest.UsageError(f"{sheet_name} 第 {row_number} 行缺少用例ID")
        if case_id in found_case_ids:
            raise pytest.UsageError(f"{sheet_name} 存在重复用例编号：{case_id}")
        found_case_ids.add(case_id)
        if filter_by_case_id and case_id not in selected_case_ids:
            continue
        marks = []
        if str(row.get("是否自动化") or "").strip() != "是":
            marks.append(pytest.mark.skip(reason="Excel 中‘是否自动化’不是‘是’"))
        params.append(pytest.param(row, id=case_id, marks=marks))
    missing = selected_case_ids - found_case_ids
    if missing:
        raise pytest.UsageError(f"{sheet_name} 未找到用例编号：{', '.join(sorted(missing))}")
    if not params:
        raise pytest.UsageError(f"{workbook} 的‘{sheet_name}’页签没有可执行用例")
    return params


def test_load_project_cases_filters_selected_case_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys.modules[__name__],
        "read_xlsx_records",
        lambda _workbook, _sheet: [
            {"用例ID": "LX-ADD-001", "是否自动化": "是"},
            {"用例ID": "LX-ADD-002", "是否自动化": "是"},
        ],
    )

    params = _load_project_cases(tmp_path / "cases.xlsx", "新增项目", ["LX-ADD-002"])

    assert [param.id for param in params] == ["LX-ADD-002"]


def test_load_project_cases_rejects_empty_selection(tmp_path):
    with pytest.raises(pytest.UsageError, match="未选择任何用例编号"):
        _load_project_cases(tmp_path / "cases.xlsx", "新增项目", [])


def test_load_project_cases_rejects_unknown_duplicate_and_missing_ids(monkeypatch, tmp_path):
    module = sys.modules[__name__]
    monkeypatch.setattr(
        module, "read_xlsx_records",
        lambda _workbook, _sheet: [{"用例ID": "LX-ADD-001", "是否自动化": "是"}],
    )
    with pytest.raises(pytest.UsageError, match="未找到用例编号"):
        _load_project_cases(tmp_path / "cases.xlsx", "新增项目", ["LX-ADD-999"])

    monkeypatch.setattr(
        module, "read_xlsx_records",
        lambda _workbook, _sheet: [
            {"用例ID": "LX-ADD-001", "是否自动化": "是"},
            {"用例ID": "LX-ADD-001", "是否自动化": "是"},
        ],
    )
    with pytest.raises(pytest.UsageError, match="重复用例编号"):
        _load_project_cases(tmp_path / "cases.xlsx", "新增项目", ["LX-ADD-001"])

    monkeypatch.setattr(
        module, "read_xlsx_records",
        lambda _workbook, _sheet: [{"用例ID": "", "是否自动化": "是"}],
    )
    with pytest.raises(pytest.UsageError, match="缺少用例ID"):
        _load_project_cases(tmp_path / "cases.xlsx", "新增项目", None)


def pytest_generate_tests(metafunc):
    if "project_case" not in metafunc.fixturenames:
        return
    workbook = (
        metafunc.config.getoption("--module-cases-excel")
        or os.getenv("EI_MODULE_CASES_EXCEL")
        or str(DEFAULT_WORKBOOK)
    )
    sheet_name = (
        metafunc.config.getoption("--module-cases-sheet")
        or os.getenv("EI_MODULE_CASES_SHEET")
        or DEFAULT_SHEET_NAME
    )
    raw_case_ids = (
        metafunc.config.getoption("--module-case-ids")
        or os.getenv("EI_MODULE_CASE_IDS_JSON")
    )
    selected_case_ids = None
    if raw_case_ids is not None:
        try:
            selected_case_ids = json.loads(raw_case_ids)
        except (TypeError, json.JSONDecodeError) as exc:
            raise pytest.UsageError(f"模块用例编号配置无效：{exc}") from exc
        if not isinstance(selected_case_ids, list):
            raise pytest.UsageError("模块用例编号配置必须是 JSON 数组")
    try:
        params = _load_project_cases(Path(workbook), sheet_name, selected_case_ids)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        raise pytest.UsageError(f"模块用例配置无效：{exc}") from exc
    metafunc.parametrize("project_case", params)


def _field_label(case: dict[str, Any]) -> str:
    label = str(case.get("字段/控件") or "").split("|", 1)[0].strip()
    if not label:
        raise AssertionError(f"{case.get('用例ID')} 的‘字段/控件’必须填写业务字段名称")
    return label


def _expected_options(case: dict[str, Any]) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[、,，]", str(case.get("测试数据") or ""))
        if item.strip()
    ]


def _runtime_field(executor, scope, label: str):
    wait_for_fields = getattr(executor, "_wait_for_fields_stable", None)
    dom_fields = (
        wait_for_fields(scope)
        if callable(wait_for_fields)
        else executor._scan_fields(scope)
    )
    fields = discover_common_fields(dom_fields)
    normalized = re.sub(r"[：:*\s]", "", label)
    source_match = next(
        (
            item
            for item in executor.driver.source_fields
            if re.sub(r"[：:*\s]", "", item[1]) == normalized
        ),
        None,
    )
    if source_match:
        by_code = [item for item in fields if item.field_key == source_match[0]]
        if len(by_code) == 1:
            return by_code[0]
    by_label = [
        item
        for item in fields
        if re.sub(r"[：:*\s]", "", item.label) == normalized
    ]
    if len(by_label) == 1:
        return by_label[0]
    if len(by_label) > 1:
        raise AssertionError(f"当前新增表单发现多个同名字段‘{label}’，无法确认唯一控件")
    source_detail = f"（源码字段编码：{source_match[0]}）" if source_match else ""
    raise AssertionError(f"当前新增表单未发现字段‘{label}’{source_detail}")


def _normalize_business_label(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^请(?:输入|选择|填写|上传|勾选|录入)", "", text)
    return re.sub(r"[：:*\s]", "", text)


def _loose_label_pattern(value: str) -> re.Pattern[str]:
    normalized = _normalize_business_label(value)
    if not normalized:
        return re.compile(r"a^")
    return re.compile(r"\s*".join(re.escape(char) for char in normalized))


def _first_visible(locator):
    try:
        count = locator.count()
    except Exception:
        return None
    for index in range(min(count, 20)):
        try:
            candidate = locator.nth(index)
        except Exception:
            candidate = locator.first if index == 0 else None
        if candidate is None:
            continue
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def _field_container(scope, field):
    literal = CommonFieldExecutor._css_attr_literal(field.field_key)
    owner_selectors = (
        f"[prop={literal}]",
        f"[field-code={literal}]",
        f"[data-field-code={literal}]",
    )
    for selector in owner_selectors:
        owner = _first_visible(scope.locator(selector))
        if owner is None:
            continue
        row = _first_visible(owner.locator(
            "xpath=ancestor-or-self::*["
            "contains(concat(' ',normalize-space(@class),' '),' el-form-item ') "
            "or contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ')"
            "][1]"
        ))
        return row or owner

    label_pattern = _loose_label_pattern(field.label)
    try:
        candidates = scope.locator(".el-form-item,.purvar_form_item").filter(
            has_text=label_pattern
        )
    except Exception:
        candidates = None
    if candidates is not None:
        expected = _normalize_business_label(field.label)
        for index in range(min(candidates.count(), 20)):
            row = candidates.nth(index)
            try:
                if row.is_visible() and expected in _normalize_business_label(row.inner_text()):
                    return row
            except Exception:
                continue

    control = _first_visible(scope.locator(field.selector))
    if control is not None:
        row = _first_visible(control.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ') "
            "or contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ')][1]"
        ))
        if row is not None:
            return row
    return None


def _field_control_from_container(row, field):
    if field.field_type == "select":
        selector = (
            ".el-select__wrapper,"
            "[role='combobox'],"
            "input[readonly][role='combobox'],"
            ".el-select"
        )
    elif field.field_type == "radio":
        selector = ".el-radio-group,.el-radio,input[type='radio'],[role='radio']"
    else:
        selector = field.selector
    return _first_visible(row.locator(selector))


def test_runtime_field_waits_for_stable_fields_before_matching():
    class Driver:
        source_fields = [("isGmoDecision", "是否需总经办决策", False)]

    class Executor:
        driver = Driver()

        def __init__(self):
            self.waited_scope = None

        def _wait_for_fields_stable(self, scope):
            self.waited_scope = scope
            return [
                DomField(
                    "isGmoDecision",
                    "是否需总经办决策",
                    "radio",
                    "#decision",
                    required=True,
                )
            ]

        def _scan_fields(self, _scope):
            raise AssertionError("个性化用例必须等待稳定字段快照后再匹配")

    scope = object()
    executor = Executor()

    field = _runtime_field(executor, scope, "是否需总经办决策")

    assert executor.waited_scope is scope
    assert field.field_key == "isGmoDecision"
    assert field.field_type == "radio"


def test_field_row_reacquires_generated_select_by_business_label():
    class Locator:
        def __init__(self, name, *, count=1, visible=True, text=""):
            self.name = name
            self._count = count
            self._visible = visible
            self._text = text

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        def nth(self, index):
            assert index == 0
            return self

        def is_visible(self):
            return self._visible

        def inner_text(self):
            return self._text

        def locator(self, selector):
            if self.name == "row" and ".el-select__wrapper" in selector:
                return Locator("current-select")
            if "ancestor" in selector and self.name in {"stable-owner", "stale-selector"}:
                return Locator("row", text="* 项目类型 请选择项目类型")
            return Locator("empty", count=0, visible=False)

        def filter(self, has_text=None):
            if self.name == "containers" and has_text.search("* 项目类型 请选择项目类型"):
                return Locator("row", text="* 项目类型 请选择项目类型")
            return Locator("empty", count=0, visible=False)

    class Scope:
        def locator(self, selector):
            if selector == ".el-form-item,.purvar_form_item":
                return Locator("containers")
            if selector == "#el-id-stale":
                return Locator("empty", count=0, visible=False)
            return Locator("empty", count=0, visible=False)

    field = type(
        "Field",
        (),
        {
            "field_key": "projClassify",
            "label": "项目类型",
            "field_type": "select",
            "selector": "#el-id-stale",
        },
    )()

    row, control = _field_row(Scope(), field)

    assert row.name == "row"
    assert control.name == "current-select"


def _field_row(scope, field):
    row = _field_container(scope, field)
    if row is None:
        raise AssertionError(f"字段‘{field.label}’（{field.field_key}）没有真实表单项容器")
    control = _field_control_from_container(row, field)
    if control is None:
        control = _first_visible(scope.locator(field.selector))
    if control is None:
        raise AssertionError(f"字段‘{field.label}’（{field.field_key}）没有可见控件")
    return row, control


def _option_texts(nodes) -> list[str]:
    try:
        raw_texts = nodes.evaluate_all(
            """els => els
            .filter(el => {
              const style = window.getComputedStyle(el);
              const rect = el.getBoundingClientRect();
              return style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 0 && rect.height > 0;
            })
            .map(el => el.innerText || el.textContent || '')
            .filter(Boolean)"""
        )
    except Exception:
        raw_texts = None
    if raw_texts is not None:
        values = []
        for text in raw_texts:
            normalized = re.sub(r"\s+", "", str(text or ""))
            if normalized and normalized not in values:
                values.append(normalized)
        return values
    values = []
    for index in range(nodes.count()):
        node = nodes.nth(index)
        try:
            if not node.is_visible():
                text = ""
            else:
                try:
                    raw_text = node.inner_text(timeout=500) or ""
                except TypeError:
                    raw_text = node.inner_text() or ""
                text = re.sub(r"\s+", "", raw_text)
        except Exception:
            text = ""
        if text and text not in values:
            values.append(text)
    return values


def test_option_texts_reads_current_visible_nodes_in_one_dom_pass():
    class Nodes:
        def evaluate_all(self, _script):
            return [" 四川板块 ", "其他未纳入\n业务板块管理的子公司", "四川板块"]

        def count(self):
            raise AssertionError("不应逐个 nth 读取易重绘的下拉节点")

    assert _option_texts(Nodes()) == [
        "四川板块",
        "其他未纳入业务板块管理的子公司",
    ]


def _first_visible_option(nodes, option_text: str):
    exact = re.compile(rf"^\s*{re.escape(option_text)}\s*$")
    matches = nodes.filter(has_text=exact)
    for index in range(matches.count()):
        option = matches.nth(index)
        if option.is_visible():
            return option
    return None


def _close_visible_select_poppers(executor) -> None:
    try:
        poppers = executor.page.locator(
            ".el-select-dropdown:visible,"
            ".el-popper:visible .el-select-dropdown,"
            "[role='listbox']:visible"
        )
        if not poppers.count():
            return
        executor.page.keyboard.press("Escape")
        executor.page.wait_for_timeout(100)
    except Exception:
        pass


def test_close_visible_select_poppers_does_not_close_form_when_no_dropdown():
    class Keyboard:
        def __init__(self):
            self.pressed = []

        def press(self, key):
            self.pressed.append(key)

    class Page:
        def __init__(self):
            self.keyboard = Keyboard()

        def locator(self, _selector):
            class Empty:
                def count(self):
                    return 0

            return Empty()

        def wait_for_timeout(self, _ms):
            raise AssertionError("没有可见下拉弹层时不应等待 Escape 清理")

    executor = type("Executor", (), {"page": Page()})()

    _close_visible_select_poppers(executor)

    assert executor.page.keyboard.pressed == []


def test_close_visible_select_poppers_presses_escape_for_existing_dropdown():
    class Keyboard:
        def __init__(self):
            self.pressed = []

        def press(self, key):
            self.pressed.append(key)

    class Page:
        def __init__(self):
            self.keyboard = Keyboard()
            self.waited = False

        def locator(self, _selector):
            class Visible:
                def count(self):
                    return 1

            return Visible()

        def wait_for_timeout(self, _ms):
            self.waited = True

    executor = type("Executor", (), {"page": Page()})()

    _close_visible_select_poppers(executor)

    assert executor.page.keyboard.pressed == ["Escape"]
    assert executor.page.waited


def _open_select_options(executor, scope, field):
    _close_visible_select_poppers(executor)
    _row, control = _field_row(scope, field)
    try:
        control.scroll_into_view_if_needed(timeout=1_000)
    except Exception:
        pass
    try:
        control.click(force=True, timeout=5_000)
    except TypeError:
        control.click(force=True)
    except Exception:
        _row, control = _field_row(scope, field)
        try:
            control.evaluate("el => el.click()", timeout=1_000)
        except TypeError:
            control.evaluate("el => el.click()")
    executor.page.wait_for_timeout(250)
    return _row, executor._owned_select_options(control)


def _click_option(option) -> None:
    try:
        option.scroll_into_view_if_needed(timeout=1_000)
    except Exception:
        pass
    try:
        option.click(force=True, timeout=3_000)
    except Exception:
        option.evaluate("el => el.click()")


def _assert_select_options(executor, scope, field, expected: list[str]) -> None:
    row, options = _open_select_options(executor, scope, field)
    actual = _option_texts(options)
    assert actual == expected, f"{field.label}码值不一致：期望 {expected}，实际 {actual}"
    _close_visible_select_poppers(executor)

    for option_text in expected:
        row, nodes = _open_select_options(executor, scope, field)
        option = _first_visible_option(nodes, option_text)
        assert option is not None, f"{field.label}的‘{option_text}’不可选择"
        _click_option(option)
        executor.page.wait_for_timeout(200)
        displayed = re.sub(r"\s+", "", row.inner_text() or "")
        assert option_text in displayed, f"{field.label}选择‘{option_text}’后未正确回显"


def _assert_radio_options(scope, field, expected: list[str]) -> None:
    row, _control = _field_row(scope, field)
    radios = row.locator(".el-radio,[role='radio']")
    actual = []
    for index in range(radios.count()):
        text = re.sub(r"\s+", "", radios.nth(index).inner_text() or "")
        if text and text not in actual:
            actual.append(text)
    assert actual == expected, f"{field.label}码值不一致：期望 {expected}，实际 {actual}"

    for option_text in expected:
        target = radios.filter(has_text=re.compile(rf"^\s*{re.escape(option_text)}\s*$")).first
        target.click(force=True)
        selected = 0
        for index in range(radios.count()):
            radio = radios.nth(index)
            is_checked = radio.evaluate(
                "el => el.getAttribute('aria-checked') === 'true' || "
                "el.classList.contains('is-checked') || "
                "Boolean(el.querySelector('input[type=radio]:checked'))"
            )
            selected += int(is_checked)
        assert selected == 1, f"{field.label}选择‘{option_text}’后未保持单选互斥"


@pytest.fixture(scope="module")
def module_case_executor():
    project_root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    module_key = os.getenv("EI_MODULE_ID") or settings.form_code or "MODULE"
    form_code = os.getenv("EI_FORM_CODE", "") or settings.form_code or module_key
    pool = GlobalDataPool.from_directory(project_root / "data")
    executor = CommonFieldExecutor(
        None,
        create_data_strategy("standard", pool, form_code),
        source_fields=discover_custom_form_fields(
            settings.source_root, os.getenv("EI_COMPONENT", "")
        ),
        default_upload_file=pool.default_upload_file(),
        dynamic_collections=load_dynamic_collection_specs(
            project_root / "data",
            form_code=form_code,
            component=os.getenv("EI_COMPONENT", ""),
        ),
        automation_record_registry=(
            project_root / "artifacts" / "automation-record-registry.json"
        ),
    )
    yield executor
    executor.close_form_session()


@pytest.mark.smoke
def test_build_project_add_personalized(
    browser_page, project_case, module_case_executor,
):
    module_case_executor.bind_page(browser_page)
    case_id = str(project_case["用例ID"])
    label = _field_label(project_case)
    expected = _expected_options(project_case)
    set_allure_common_case_metadata(
        title=f"{label}：{project_case['测试场景']}",
        case_id=case_id,
        parameter_name="project_case",
    )

    def check_field_options(scope):
        field = _runtime_field(module_case_executor, scope, label)
        control_type = field.field_type
        if control_type == "select":
            _assert_select_options(module_case_executor, scope, field, expected)
        elif control_type == "radio":
            _assert_radio_options(scope, field, expected)
        else:
            pytest.fail(f"{case_id} 暂不支持自动执行控件类型：{control_type}")

    module_case_executor.run_recoverable_form_check(
        case_id,
        label,
        check_field_options,
    )
