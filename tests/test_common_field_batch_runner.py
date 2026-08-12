import pytest

from ei_ui_smoke.common_field_batch import run_common_field_transactions
from ei_ui_smoke.common_field_cases import BoundCommonCase, BoundCommonTransaction
from ei_ui_smoke.common_field_executor import (
    CommonFieldExecutionResult,
    CommonFieldExecutor,
    SharedFormPreconditionError,
)
from ei_ui_smoke.module_driver import DynamicFieldContractError


def _case(case_id: str, field_key: str) -> BoundCommonCase:
    return BoundCommonCase(
        case_id=case_id,
        field_key=field_key,
        field_label=field_key,
        field_type="text",
        selector=f"#{field_key}",
        scenario="长度边界",
        input_value="value",
        expected_type="accepted",
        expected_value="保存成功",
        priority="P1",
    )


class _Executor:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.executed = []
        self.close_calls = 0

    def execute_transaction(self, transaction):
        self.executed.append(transaction.transaction_id)
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return tuple(
            CommonFieldExecutionResult(
                case.case_id, case.field_key, outcome, outcome,
            )
            for case in transaction.cases
        )

    def close_form_session(self):
        self.close_calls += 1


def test_batch_resolves_every_merged_case_without_repeating_transaction():
    transaction = BoundCommonTransaction(
        "TX-001", (_case("ADD-019", "name"), _case("ADD-020", "description")),
    )
    finished = []
    executor = _Executor(["saved_verified_and_retained"])

    result = run_common_field_transactions(
        executor, [transaction], on_case_finished=finished.append,
    )

    assert executor.executed == ["TX-001"]
    assert [item.case.field_key for item in result.cases] == ["name", "description"]
    assert [item.case.field_key for item in finished] == ["name", "description"]
    assert len({item.logical_id for item in finished}) == 2


def test_shared_precondition_failure_blocks_remaining_transactions_once():
    first = BoundCommonTransaction("TX-001", (_case("ADD-019", "name"),))
    second = BoundCommonTransaction("TX-002", (_case("ADD-020", "description"),))
    executor = _Executor([SharedFormPreconditionError("adjustmentItems 契约缺失")])

    result = run_common_field_transactions(executor, [first, second])

    assert executor.executed == ["TX-001"]
    assert [item.result.outcome for item in result.cases] == [
        "execution_failed", "blocked_by_shared_precondition",
    ]
    assert result.shared_precondition_error == "adjustmentItems 契约缺失"


def test_ordinary_case_failure_does_not_skip_later_transaction(capsys):
    first = BoundCommonTransaction("TX-001", (_case("ADD-019", "name"),))
    second = BoundCommonTransaction("TX-002", (_case("ADD-020", "description"),))
    executor = _Executor([
        AssertionError("目标字段校验失败"),
        "saved_verified_and_retained",
    ])

    result = run_common_field_transactions(executor, [first, second])

    assert executor.executed == ["TX-001", "TX-002"]
    assert [item.result.outcome for item in result.cases] == [
        "execution_failed", "saved_verified_and_retained",
    ]
    assert "COMMON_TRANSACTION_FAILURE id=TX-001" in capsys.readouterr().out


def test_dynamic_collection_contract_failure_is_shared_batch_precondition():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver",
        (), {
            "_fill_dialog": lambda _self, **_kwargs: (_ for _ in ()).throw(
                DynamicFieldContractError("adjustmentItems 子字段配置不完整")
            ),
        },
    )()

    with pytest.raises(SharedFormPreconditionError, match="adjustmentItems"):
        executor._fill_valid_baseline(object())


def test_attachment_401_is_shared_batch_precondition():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver",
        (), {
            "_fill_dialog": staticmethod(lambda **_kwargs: {}),
            "_fill_failures": [],
            "_upload_default_attachments": staticmethod(
                lambda _scope: (_ for _ in ()).throw(
                    AssertionError("upload failed: POST /upload HTTP 401")
                )
            ),
        },
    )()

    with pytest.raises(SharedFormPreconditionError, match="登录态已失效"):
        executor._fill_valid_baseline(object())
