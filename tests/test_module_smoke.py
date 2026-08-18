import os
from pathlib import Path

import pytest

from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs
from ei_ui_smoke.module_driver import ModuleSmokeDriver
from ei_ui_smoke.source_form import discover_custom_form_fields
from ei_ui_smoke.tab_navigation import activate_page_tab


@pytest.mark.smoke
def test_selected_module_auto_add_or_page_access(browser_page, request):
    project_root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    mode = request.config.getoption("--data-mode") or settings.data_mode
    module_key = os.getenv("EI_MODULE_ID") or settings.module_name or "MODULE"
    form_code = os.getenv("EI_FORM_CODE", "") or settings.form_code or module_key
    data_pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = create_data_strategy(mode, data_pool, form_code)
    component = os.getenv("EI_COMPONENT", "")
    if tab_label := os.getenv("EI_PAGE_TAB", "").strip():
        activate_page_tab(browser_page, tab_label)
    source_fields = discover_custom_form_fields(settings.source_root, component)
    dynamic_collections = load_dynamic_collection_specs(
        project_root / "data",
        form_code=os.getenv("EI_FORM_CODE", "") or settings.form_code,
        component=component,
    )
    result = ModuleSmokeDriver(
        browser_page,
        strategy,
        source_fields=source_fields,
        default_upload_file=data_pool.default_upload_file(),
        dynamic_collections=dynamic_collections,
        automation_record_registry=(
            project_root / "artifacts" / "automation-record-registry.json"
        ),
    ).run()
    if os.getenv("EI_REQUIRE_ADD", "false").lower() == "true":
        assert result.mode in {
            "add_and_detail_verified",
            "add_and_edit_form_verified",
            "add_and_list_verified",
        }, (
            "所选模块未完成新增及保存后数据核对"
        )
