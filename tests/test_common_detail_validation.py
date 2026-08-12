import json
import os
from pathlib import Path

import allure
import pytest

from ei_ui_smoke.allure_report import set_allure_common_case_metadata
from ei_ui_smoke.common_field_cases import read_xlsx_records
from ei_ui_smoke.detail_navigation import enter_available_detail_module


DETAIL_DIALOG_SELECTOR = (
    ".el-dialog:visible,.el-drawer:visible,.ant-modal:visible,"
    ".ant-drawer:visible,[role='dialog']:visible"
)


def _selected_case_ids() -> set[str] | None:
    raw = os.getenv("EI_COMMON_CASE_IDS_JSON", "").strip()
    if not raw:
        return None
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise pytest.UsageError(f"通用用例 ID 配置无效：{exc}") from exc
    if not isinstance(values, list):
        raise pytest.UsageError("通用用例 ID 配置必须是 JSON 数组")
    return {str(value).strip() for value in values if str(value).strip()}


def _is_direct_detail_case(row: dict[str, object]) -> bool:
    feature = str(row.get("功能") or "").strip()
    control = str(row.get("字段/控件") or row.get("检查点") or "").strip()
    return feature in {"页面", "详情", "查看"} and control == "对话框名称"


def _has_visible_detail_dialog(page) -> bool:
    """Whether the selected detail is presented in an independent dialog or drawer."""
    try:
        return bool(page.locator(DETAIL_DIALOG_SELECTOR).count())
    except Exception:
        return False


def _detail_case_title(module_name: str) -> str:
    """Keep shared detail rules distinguishable across detail modules."""
    labels = [part.strip() for part in module_name.split("/") if part.strip()]
    return f"{' / '.join(labels) or '详情模块'}：详情页面名称检查"


def test_direct_detail_case_classification():
    assert _is_direct_detail_case({"功能": "页面", "字段/控件": "对话框名称"})
    assert not _is_direct_detail_case({"功能": "编辑", "字段/控件": "保存按钮"})


def test_detail_dialog_check_is_not_applicable_to_list_backed_detail():
    class Page:
        @staticmethod
        def locator(_selector):
            return type("Locator", (), {"count": staticmethod(lambda: 0)})()

    assert not _has_visible_detail_dialog(Page())


def test_detail_case_title_keeps_the_full_module_path():
    assert _detail_case_title("建设项目/详情/投前管理/项目立项") == (
        "建设项目 / 详情 / 投前管理 / 项目立项：详情页面名称检查"
    )


def pytest_generate_tests(metafunc):
    if "detail_case" not in metafunc.fixturenames:
        return
    workbook = metafunc.config.getoption("--common-cases-excel") or os.getenv(
        "EI_COMMON_CASES_EXCEL", ""
    )
    sheet = metafunc.config.getoption("--common-cases-sheet") or os.getenv(
        "EI_COMMON_CASES_SHEET", "详情"
    )
    if not workbook:
        metafunc.parametrize(
            "detail_case",
            [pytest.param(None, marks=pytest.mark.skip(
                reason="--common-cases-excel is required"
            ))],
        )
        return
    selected_ids = _selected_case_ids()
    rows = read_xlsx_records(Path(workbook), sheet)
    cases = []
    for row in rows:
        case_id = str(row.get("用例ID") or row.get("序号") or "").strip()
        if not case_id or (selected_ids is not None and case_id not in selected_ids):
            continue
        if _is_direct_detail_case(row):
            cases.append(pytest.param(row, id=case_id))
        else:
            cases.append(pytest.param(
                row,
                id=case_id,
                marks=pytest.mark.skip(
                    reason="该详情页签用例要求新增、编辑或提交入口，不能在只读详情模块伪造执行"
                ),
            ))
    if not cases:
        cases = [pytest.param(None, marks=pytest.mark.skip(
            reason="未选择可执行的详情页面用例"
        ))]
    metafunc.parametrize("detail_case", cases)


@pytest.mark.smoke
def test_selected_common_detail_case(browser_page, detail_case):
    if detail_case is None:
        pytest.skip("未选择可执行的详情页面用例")
    module_name = os.getenv("EI_MODULE_NAME", "").strip()
    detail_url = os.getenv("EI_FORM_URL", "").strip()
    assert module_name and detail_url, "详情通用用例缺少模块导航上下文"
    case_id = str(detail_case.get("用例ID") or detail_case.get("序号") or "详情")
    set_allure_common_case_metadata(
        title=_detail_case_title(module_name),
        case_id=case_id,
        display_case_id=case_id,
        parameter_name="detail_case",
    )
    enter_available_detail_module(browser_page, detail_url, module_name, "详情")

    if not _has_visible_detail_dialog(browser_page):
        pytest.skip("当前详情由列表承载，没有独立对话框，跳过对话框名称检查")

    module_label = module_name.split("/")[-1]
    content = browser_page.locator(
        ".component-box:visible,.detail-panel:visible,.base-info-page:visible"
    )
    assert content.count(), f"详情模块未渲染内容区域：{module_label}"
    visible_text = "\n".join(content.all_inner_texts())
    allure.attach(visible_text[:2_000], "详情页面可见内容", allure.attachment_type.TEXT)
    assert module_label in visible_text, (
        f"详情页面模块名不正确：expected={module_label!r}, "
        f"visible={visible_text[:500]!r}"
    )
