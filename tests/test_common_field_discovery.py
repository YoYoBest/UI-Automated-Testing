import os
from pathlib import Path

import pytest

from ei_ui_smoke.common_field_executor import CommonFieldExecutor
from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs
from ei_ui_smoke.detail_navigation import detail_context_preparer_from_env
from ei_ui_smoke.source_form import discover_custom_form_fields


@pytest.mark.smoke
def test_discover_common_fields(browser_page, request):
    if not request.config.getoption("--discover-common-fields"):
        pytest.skip("use --discover-common-fields to create the runtime field manifest")
    project_root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    module_key = os.getenv("EI_MODULE_ID") or settings.form_code or "MODULE"
    form_code = os.getenv("EI_FORM_CODE", "") or settings.form_code or module_key
    manifest = request.config.getoption("--common-fields-manifest")
    manifest_path = Path(manifest) if manifest else (
        project_root / "artifacts" / "common-fields" / f"{module_key}.json"
    )
    pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = create_data_strategy("standard", pool, form_code)
    source_fields = discover_custom_form_fields(
        settings.source_root, os.getenv("EI_COMPONENT", "")
    )
    executor = CommonFieldExecutor(
        browser_page,
        strategy,
        source_fields=source_fields,
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
    executor.prepare_form_context = detail_context_preparer_from_env(
        lambda: executor.driver.run(provision_only=True)
    )
    fields = executor.discover(manifest_path)
    assert fields
