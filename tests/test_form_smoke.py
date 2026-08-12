import os
from pathlib import Path

import pytest

from ei_ui_smoke.case_data import load_smoke_case
from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.orchestrator import FormSmokeOrchestrator


@pytest.mark.smoke
def test_configured_add_form_save_and_detail_echo(browser_page, request):
    settings = Settings.from_env()
    form_code = settings.form_code or os.getenv("EI_FORM_CODE", "")
    if not form_code:
        pytest.skip("EI_FORM_CODE is required")
    project_root = Path(__file__).resolve().parents[1]
    case = load_smoke_case(
        project_root,
        form_code=form_code,
    )
    data_dir = project_root / "data"
    pool = GlobalDataPool.from_directory(data_dir)
    mode = request.config.getoption("--data-mode") or settings.data_mode
    strategy = create_data_strategy(mode, pool, form_code)
    orchestrator = FormSmokeOrchestrator(
        browser_page,
        settings.source_root,
        form_code,
        data_strategy=strategy,
    )
    result = orchestrator.run_add_and_verify(case)
    assert result.business_id
