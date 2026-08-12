from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .common_field_cases import BoundCommonCase, BoundCommonTransaction
from .common_field_executor import (
    CommonFieldExecutionResult,
    CommonFieldExecutor,
    SharedFormPreconditionError,
)


@dataclass(frozen=True, slots=True)
class CommonFieldBatchCaseResult:
    transaction: BoundCommonTransaction
    case: BoundCommonCase
    result: CommonFieldExecutionResult
    logical_id: str


@dataclass(frozen=True, slots=True)
class CommonFieldBatchResult:
    cases: tuple[CommonFieldBatchCaseResult, ...]
    shared_precondition_error: str = ""


def _case_results_for_error(
    transaction: BoundCommonTransaction,
    *,
    outcome: str,
    error: str,
) -> tuple[CommonFieldExecutionResult, ...]:
    return tuple(
        CommonFieldExecutionResult(case.case_id, case.field_key, outcome, error)
        for case in transaction.cases
    )


def _logical_case_id(
    transaction_index: int,
    case_index: int,
    case: BoundCommonCase,
) -> str:
    return (
        "tests/test_common_field_batch.py::"
        f"transaction-{transaction_index:03d}::case-{case_index:03d}::{case.pytest_id}"
    )


def run_common_field_transactions(
    executor: CommonFieldExecutor,
    transactions: Iterable[BoundCommonTransaction],
    *,
    on_case_finished: Callable[[CommonFieldBatchCaseResult], None] | None = None,
) -> CommonFieldBatchResult:
    """Run a Batch once and preserve one result for every logical field binding."""
    resolved: list[CommonFieldBatchCaseResult] = []
    shared_error = ""
    for transaction_index, transaction in enumerate(transactions, start=1):
        if shared_error:
            results = _case_results_for_error(
                transaction,
                outcome="blocked_by_shared_precondition",
                error=shared_error,
            )
        else:
            try:
                results = tuple(executor.execute_transaction(transaction))
            except Exception as exc:
                executor.close_form_session()
                print(
                    "COMMON_TRANSACTION_FAILURE "
                    f"id={transaction.transaction_id} error={exc}",
                    flush=True,
                )
                results = _case_results_for_error(
                    transaction,
                    outcome="execution_failed",
                    error=str(exc),
                )
                if isinstance(exc, SharedFormPreconditionError):
                    shared_error = str(exc)
                    print(
                        "COMMON_BATCH_FAIL_FAST "
                        f"transaction={transaction.transaction_id} error={shared_error}",
                        flush=True,
                    )
            if len(results) != len(transaction.cases):
                shared_error = (
                    f"事务 {transaction.transaction_id} 结果数量不一致："
                    f"cases={len(transaction.cases)}, results={len(results)}"
                )
                executor.close_form_session()
                results = _case_results_for_error(
                    transaction,
                    outcome="execution_failed",
                    error=shared_error,
                )

        for case_index, (case, result) in enumerate(
            zip(transaction.cases, results), start=1,
        ):
            item = CommonFieldBatchCaseResult(
                transaction=transaction,
                case=case,
                result=result,
                logical_id=_logical_case_id(transaction_index, case_index, case),
            )
            resolved.append(item)
            if on_case_finished is not None:
                on_case_finished(item)
    return CommonFieldBatchResult(tuple(resolved), shared_error)
