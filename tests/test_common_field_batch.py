import json
import os
from pathlib import Path

import allure
import pytest

from ei_ui_smoke.allure_report import set_allure_common_case_metadata, set_allure_hidden_parameter
from ei_ui_smoke.common_field_cases import (
    build_common_case_coverage,
    common_case_coverage_path,
    load_bound_common_cases,
    plan_common_case_transactions,
    save_common_case_coverage,
)
from ei_ui_smoke.common_field_batch import run_common_field_transactions
from ei_ui_smoke.common_field_executor import CommonFieldExecutor
from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.detail_navigation import detail_context_preparer_from_env
from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs
from ei_ui_smoke.pytest_progress import (
    emit_logical_progress_finished,
    emit_logical_progress_total,
)
from ei_ui_smoke.source_form import discover_custom_form_fields


def _allowed_outcomes(case):
    if case.field_type.endswith("_command"):
        return {
            "save_blocked", "cancel_verified", "close_verified",
            "command_not_applicable", "confirmation_cancelled", "command_verified",
            "rapid_click_blocked_by_ui",
        }
    if case.field_type == "dialog_title":
        return {"dialog_title_verified"}
    if case.field_type == "required":
        return {"save_blocked", "validation_recovered", "required_default_value_skipped"}
    if case.field_type in {"select", "radio"}:
        return {
            "saved_verified_and_retained", "choice_options_verified",
            "choice_single_selection_verified",
        }
    if case.expected_type == "field_error":
        return {"save_blocked", "truncated_saved_verified_and_retained"}
    if case.expected_type == "safe_handling":
        return {"safe_content_saved_verified", "safe_content_rejected"}
    return {"saved_verified_and_retained", "form_probe_passed"}


@pytest.mark.smoke
def test_common_field_batch(browser_page, request):
    """Run one module/action's common Excel cases in one browser session."""
    workbook = request.config.getoption("--common-cases-excel") or os.getenv(
        "EI_COMMON_CASES_EXCEL", ""
    )
    manifest = request.config.getoption("--common-fields-manifest") or os.getenv(
        "EI_COMMON_FIELDS_MANIFEST", ""
    )
    sheet_name = request.config.getoption("--common-cases-sheet") or os.getenv(
        "EI_COMMON_CASES_SHEET", "新增"
    )
    raw_case_ids = request.config.getoption("--common-case-ids") or os.getenv(
        "EI_COMMON_CASE_IDS_JSON", "[]"
    )
    if not workbook or not manifest:
        pytest.skip("batch common-field run requires workbook and field manifest")
    try:
        case_ids = json.loads(raw_case_ids)
    except json.JSONDecodeError as exc:
        raise pytest.UsageError(f"通用用例编号配置无效：{exc}") from exc
    if not isinstance(case_ids, list) or not case_ids:
        raise pytest.UsageError("批处理通用字段验证必须提供至少一个用例编号")

    project_root = Path(__file__).resolve().parents[1]
    manifest_path = Path(manifest)
    settings = Settings.from_env()
    module_key = os.getenv("EI_MODULE_ID") or settings.form_code or "MODULE"
    form_code = os.getenv("EI_FORM_CODE", "") or settings.form_code or module_key
    pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = create_data_strategy("standard", pool, form_code)
    executor = CommonFieldExecutor(
        browser_page,
        strategy,
        source_fields=discover_custom_form_fields(
            settings.source_root, os.getenv("EI_COMPONENT", "")
        ),
        default_upload_file=pool.default_upload_file(),
        dynamic_collections=load_dynamic_collection_specs(
            project_root / "data",
            form_code=form_code,
            component=os.getenv("EI_COMPONENT", ""),
        ),
    )
    executor.prepare_form_context = detail_context_preparer_from_env(
        lambda: executor.driver.run(provision_only=True)
    )

    try:
        fields = executor.discover(manifest_path)
        assert fields, "当前表单没有发现可应用通用规则的字段"
        cases = load_bound_common_cases(
            Path(workbook), manifest_path, sheet_name=sheet_name, case_ids=case_ids
        )
        transactions = plan_common_case_transactions(cases)
        coverage = build_common_case_coverage(
            Path(workbook), manifest_path, sheet_name=sheet_name, case_ids=case_ids
        )
        coverage_path = common_case_coverage_path(
            manifest_path, sheet_name, case_ids
        )
        save_common_case_coverage(coverage_path, coverage)
        counts = coverage["counts"]
        print(
            "COMMON_CASE_COVERAGE "
            f"template={coverage['template_cases']} "
            f"executed_rules={counts['executed']} "
            f"bound_instances={coverage['bound_instances']} "
            f"transactions={coverage['transaction_count']} "
            f"not_applicable={counts['not_applicable']} "
            f"unsupported={counts['unsupported']} "
            f"report={coverage_path}",
            flush=True,
        )
        if not transactions:
            pytest.skip("Excel规则与字段清单没有可执行的匹配项")

        emit_logical_progress_total(
            len(cases), transaction_count=len(transactions),
        )

        set_allure_common_case_metadata(
            title=f"批量字段验证：{sheet_name}（{len(cases)}个字段绑定）",
            case_id=f"BATCH-{sheet_name}",
            display_case_id=", ".join(case_ids),
        )
        failures = []
        batch_result = run_common_field_transactions(
            executor,
            transactions,
            on_case_finished=lambda item: emit_logical_progress_finished(
                item.logical_id, outcome=item.result.outcome,
            ),
        )
        for item in batch_result.cases:
            transaction = item.transaction
            case = item.case
            result = item.result
            set_allure_hidden_parameter("transaction_id", transaction.transaction_id)
            observed = "<redacted>" if case.field_type == "password" else result.observed
            value = "<redacted>" if case.field_type == "password" else case.input_value
            with allure.step(f"{case.pytest_id} {case.field_label}：{case.scenario}"):
                allure.attach(
                    json.dumps(
                        {
                            "case_id": case.pytest_id,
                            "field_key": case.field_key,
                            "input": value,
                            "observed": observed,
                            "outcome": result.outcome,
                            "transaction_id": transaction.transaction_id,
                        },
                        ensure_ascii=False,
                        default=str,
                        indent=2,
                    ),
                    "字段执行结果",
                    allure.attachment_type.JSON,
                )
            allowed = _allowed_outcomes(case)
            if result.outcome not in allowed:
                failures.append(
                    f"{case.pytest_id}: expected={sorted(allowed)}, "
                    f"actual={result.outcome}, observed={observed}"
                )
        assert not failures, "通用字段批处理失败：" + "；".join(failures)
    finally:
        executor.close_form_session()
