import json
import os
from pathlib import Path

import allure
import pytest

from ei_ui_smoke.allure_report import (
    set_allure_hidden_parameter,
    set_allure_common_case_metadata,
)
from ei_ui_smoke.common_field_executor import CommonFieldExecutor
from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.detail_navigation import detail_context_preparer_from_env
from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs
from ei_ui_smoke.source_form import discover_custom_form_fields


@pytest.fixture(scope="module")
def common_field_executor(browser_runtime):
    project_root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    module_key = os.getenv("EI_MODULE_ID") or settings.form_code or "MODULE"
    form_code = os.getenv("EI_FORM_CODE", "") or settings.form_code or module_key
    pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = create_data_strategy("standard", pool, form_code)
    source_fields = discover_custom_form_fields(
        settings.source_root, os.getenv("EI_COMPONENT", "")
    )
    executor = CommonFieldExecutor(
        browser_runtime(),
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
    yield executor
    executor.close_form_session()


@pytest.fixture(scope="module")
def common_field_transaction_cache():
    return {}


@pytest.mark.smoke
def test_common_field_validation(
    browser_page, common_field_case, common_field_executor,
    common_field_transaction_cache,
):
    common_field_executor.bind_page(browser_page)
    transaction = common_field_case.transaction
    cases = transaction.cases
    report_case = common_field_case.case
    title = f"{report_case.field_label}：{report_case.scenario}"
    display_case_id = report_case.case_id
    set_allure_common_case_metadata(
        title=title,
        case_id=common_field_case.pytest_id,
        display_case_id=display_case_id,
    )
    set_allure_hidden_parameter("transaction_id", transaction.transaction_id)
    results = common_field_executor.execute_transaction_once(
        transaction, common_field_transaction_cache
    )
    assert len(results) == len(cases), (
        f"事务 {transaction.transaction_id} 结果数量不一致："
        f"cases={len(cases)}, results={len(results)}"
    )
    command_allowed = {
        "save_blocked",
        "cancel_verified", "close_verified", "command_not_applicable", "confirmation_cancelled",
        "command_verified", "rapid_click_blocked_by_ui",
    }
    required_allowed = {
        "save_blocked", "validation_recovered",
        "required_default_value_skipped",
    }
    failures = []
    indexed_results = {
        index: result for index, result in enumerate(results)
    }
    case = common_field_case.case
    result = indexed_results[common_field_case.case_index]
    with allure.step(f"{case.pytest_id} {case.field_label}：{case.scenario}"):
        value = "<redacted>" if case.field_type == "password" else case.input_value
        observed = "<redacted>" if case.field_type == "password" else result.observed
        allure.attach(
            json.dumps(
                {
                    "case_id": case.pytest_id,
                    "field_key": case.field_key,
                    "input": value,
                    "observed": observed,
                    "outcome": result.outcome,
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            ),
            "字段执行结果",
            allure.attachment_type.JSON,
        )
        if result.outcome == "blocked_by_transaction_failure":
            pytest.skip(
                "事务被前序字段失败阻断：" + observed
            )
        if case.field_type.endswith("_command"):
            allowed = command_allowed
        elif case.field_type == "dialog_title":
            allowed = {"dialog_title_verified"}
        elif case.field_type == "required":
            allowed = required_allowed
        elif case.field_type in {"select", "radio"}:
            allowed = {
                "saved_verified_and_retained",
                "choice_options_verified",
                "choice_single_selection_verified",
            }
        elif case.expected_type == "field_error":
            allowed = {
                "save_blocked",
                "truncated_saved_verified_and_retained",
            }
        elif case.expected_type == "safe_handling":
            allowed = {
                "safe_content_saved_verified",
                "safe_content_rejected",
            }
        else:
            allowed = {"saved_verified_and_retained", "form_probe_passed"}
        if result.outcome not in allowed:
            failures.append(
                f"{case.pytest_id}: expected={sorted(allowed)}, "
                f"actual={result.outcome}, observed={observed}"
            )
    assert not failures, "通用字段校验失败：" + "；".join(failures)
