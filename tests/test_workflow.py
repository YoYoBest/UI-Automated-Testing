import json
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from ei_ui_smoke.module_driver import ModuleSmokeResult
from ei_ui_smoke.workflow import (
    AllureWorkflowReporter,
    WorkflowBuildContext,
    WorkflowCleanupDisposition,
    WorkflowCleanupRecord,
    WorkflowConfigurationError,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowPreconditionError,
    WorkflowResultError,
    WorkflowRunner,
    WorkflowStateTimeout,
    WorkflowStep,
    WorkflowStepEvidence,
    WorkflowStepExecution,
    WorkflowStepResult,
    load_workflow_definition,
    workflow_snapshot_path,
)


class _Page:
    def __init__(self, url: str):
        self.url = url


class _Sessions:
    def __init__(self, roles=("maker", "approver")):
        self.roles = set(roles)
        self.pages = {
            role: _Page(f"https://ei.example/{role}?token=hidden#/work?id=record-1")
            for role in roles
        }
        self.ensure_calls = []
        self.page_calls = []

    def ensure_roles(self, roles):
        roles = tuple(roles)
        self.ensure_calls.append(roles)
        missing = [role for role in roles if role not in self.roles]
        if missing:
            raise WorkflowConfigurationError("missing roles: " + ", ".join(missing))

    def page_for(self, role):
        self.page_calls.append(role)
        return self.pages[role]

    def bind_workflow_role(self, role):
        self.bound_role = role


class _Reporter:
    def __init__(self):
        self.started = []
        self.steps = []

    def start(self, definition, context):
        self.started.append((definition.workflow_id, context.run_id))

    @contextmanager
    def step(self, step, *, index):
        self.steps.append((index, step.step_id, step.role))
        yield


def _definition(*steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="project-approval",
        title="项目提交审批",
        steps=tuple(steps),
    )


def _context(mode: str = "standard") -> WorkflowContext:
    return WorkflowContext(
        workflow_id="project-approval",
        run_id="run-20260818",
        data_mode=mode,
    )


def _mutation_evidence(business_id: str = "record-1", **overrides):
    values = {
        "record_visible": True,
        "action_available": True,
        "next_action_available": True,
        "mutation_count": 1,
        "request_count": 1,
        "response_count": 1,
        "http_status": 200,
        "business_success": True,
        "business_code": 200,
        "response_business_id": business_id,
        "mutation_business_id": business_id,
        "mutation_id_source": "response",
        "readback_business_id": business_id,
        "state_source": "detail_api",
        "poll_attempts": 1,
    }
    values.update(overrides)
    return WorkflowStepEvidence(**values)


def _readback_evidence(business_id: str = "record-1", **overrides):
    values = {
        "record_visible": True,
        "action_available": False,
        "readback_business_id": business_id,
        "state_source": "detail_api",
        "poll_attempts": 1,
    }
    values.update(overrides)
    return WorkflowStepEvidence(**values)


def test_runner_passes_module_business_id_across_roles_and_records_cleanup(tmp_path):
    calls = []

    class UnsafeObservation:
        def __str__(self):
            return "password=leaked-by-str"

    def create(execution):
        calls.append((execution.step.step_id, execution.business_id))
        module_result = ModuleSmokeResult(
            mode="add_and_detail_verified",
            business_id="record-1",
            submitted={"password": "must-not-persist", "name": "AUTO_project"},
            record_markers=("AUTO_project", "ordinary business value"),
            record_identity_payload={"token": "must-not-persist"},
        )
        execution.wait_for_status(
            lambda: "draft",
            expected="draft",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult.from_module_result(
            module_result,
            page_scope=execution.page.url,
            actual_status="draft",
            observations={
                "business_code": 200,
                "runtime_object": UnsafeObservation(),
                "storage_shape": {
                    "name": "accessToken",
                    "value": "top-secret-from-storage",
                },
                "json_dump": '{"accessToken":"top-secret-from-json"}',
                "request_count": {"unsafe": 1},
            },
            created_by_workflow=True,
            cleanup_allowed=True,
            step_evidence=_mutation_evidence(),
            cleanup_disposition=WorkflowCleanupDisposition("pending"),
            correlation_ids={"resource_pool_id": "rk-1"},
        )

    def approve(execution):
        calls.append((execution.step.step_id, execution.require_business_id()))
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id=execution.business_id,
            state_source="approval_detail_api",
        )
        return WorkflowStepResult(
            business_id=execution.business_id,
            actual_status="approved",
            observations={"api_token": "secret", "business_code": 200},
            step_evidence=_mutation_evidence(
                state_source="approval_detail_api",
                next_action_available=None,
                response_business_id="",
                mutation_business_id="rk-1",
                mutation_id_source="request",
                mutation_correlation_key="resource_pool_id",
            ),
            cleanup_disposition=WorkflowCleanupDisposition(
                "retained", "approved_record_not_deletable"
            ),
            correlation_ids={
                "resource_pool_id": "rk-1",
                "bpm_instance_id": "bpm-9",
            },
        )

    definition = _definition(
        WorkflowStep(
            "create",
            "经办人创建项目",
            "maker",
            create,
            produces_business_id=True,
            expected_status="draft",
            created_record=True,
            cleanup_allowed=True,
            step_type="mutation",
            requires_next_action=True,
        ),
        WorkflowStep(
            "approve",
            "审批人审批通过",
            "approver",
            approve,
            depends_on=("create",),
            requires_business_id=True,
            requires_status="draft",
            expected_status="approved",
            step_type="mutation",
        ),
    )
    reporter = _Reporter()
    snapshot = tmp_path / "workflow.json"

    result = WorkflowRunner(
        _Sessions(), snapshot_path=snapshot, reporter=reporter
    ).run(definition, _context())

    assert calls == [("create", ""), ("approve", "record-1")]
    assert result.status == "passed"
    assert result.business_id == "record-1"
    assert result.actual_status == "approved"
    assert result.correlation_ids == {
        "resource_pool_id": "rk-1",
        "bpm_instance_id": "bpm-9",
    }
    assert [record.step_id for record in result.step_records] == ["create", "approve"]
    assert result.cleanup_records[0].business_id == "record-1"
    assert result.cleanup_records[0].page_scope == "https://ei.example/maker#/work"
    assert result.cleanup_records[0].disposition == "retained"
    assert (
        result.cleanup_records[0].retention_reason
        == "approved_record_not_deletable"
    )
    assert reporter.steps == [(1, "create", "maker"), (2, "approve", "approver")]

    payload_text = snapshot.read_text(encoding="utf-8")
    payload = json.loads(payload_text)
    assert payload["status"] == "passed"
    assert payload["steps"][0]["observations"] == {
        "module_result_mode": "add_and_detail_verified",
        "business_code": 200,
    }
    assert payload["steps"][1]["observations"] == {"business_code": 200}
    assert payload["steps"][0]["step_type"] == "mutation"
    assert payload["steps"][0]["step_evidence"]["request_count"] == 1
    assert payload["steps"][1]["step_evidence"]["mutation_id_source"] == "request"
    assert (
        payload["steps"][1]["step_evidence"]["mutation_correlation_key"]
        == "resource_pool_id"
    )
    assert payload["correlation_ids"] == result.correlation_ids
    assert payload["cleanup_records"][0]["disposition"] == "retained"
    assert (
        payload["cleanup_records"][0]["retention_reason"]
        == "approved_record_not_deletable"
    )
    assert "must-not-persist" not in payload_text
    assert "top-secret" not in payload_text
    assert "?token=" not in payload_text
    assert not snapshot.with_suffix(".json.tmp").exists()


def test_first_step_failure_is_snapshotted_and_stops_dependents(tmp_path):
    calls = []

    def fail(_execution):
        calls.append("create")
        raise RuntimeError("backend unavailable")

    def should_not_run(_execution):
        calls.append("approve")

    definition = _definition(
        WorkflowStep(
            "create",
            "创建",
            "maker",
            fail,
            produces_business_id=True,
            expected_status="draft",
            step_type="mutation",
        ),
        WorkflowStep(
            "approve", "审批", "approver", should_not_run, depends_on=("create",)
        ),
    )
    context = _context()
    snapshot = tmp_path / "failed.json"

    with pytest.raises(RuntimeError, match="backend unavailable"):
        WorkflowRunner(
            _Sessions(), snapshot_path=snapshot, reporter=_Reporter()
        ).run(definition, context)

    assert calls == ["create"]
    assert context.status == "failed"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["steps"][-1]["status"] == "failed"
    assert payload["steps"][-1]["error_type"] == "RuntimeError"
    assert "backend unavailable" not in json.dumps(payload)


def test_all_roles_are_validated_before_any_handler_runs():
    called = []
    definition = _definition(
        WorkflowStep("create", "创建", "maker", lambda _execution: called.append(True)),
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            lambda _execution: called.append(True),
            depends_on=("create",),
        ),
    )
    context = _context()

    with pytest.raises(WorkflowConfigurationError, match="approver"):
        WorkflowRunner(_Sessions(("maker",)), reporter=_Reporter()).run(
            definition, context
        )

    assert called == []
    assert context.status == "pending"


def test_all_role_contexts_are_opened_before_the_first_handler_runs():
    called = []

    class Sessions(_Sessions):
        def page_for(self, role):
            if role == "approver":
                raise RuntimeError("invalid approver state")
            return super().page_for(role)

    definition = _definition(
        WorkflowStep("create", "创建", "maker", lambda _execution: called.append(True)),
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            lambda _execution: called.append(True),
            depends_on=("create",),
        ),
    )
    context = _context()

    with pytest.raises(RuntimeError, match="invalid approver state"):
        WorkflowRunner(Sessions(), reporter=_Reporter()).run(definition, context)

    assert called == []
    assert context.status == "pending"


def test_missing_business_id_output_fails_and_does_not_run_next_step():
    calls = []
    definition = _definition(
        WorkflowStep(
            "create",
            "创建",
            "maker",
            lambda _execution: None,
            produces_business_id=True,
            expected_status="draft",
            step_type="mutation",
        ),
        WorkflowStep(
            "review",
            "回查",
            "maker",
            lambda _execution: calls.append("review"),
            depends_on=("create",),
            requires_business_id=True,
        ),
    )

    with pytest.raises(WorkflowResultError, match="不能返回 None"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, _context())

    assert calls == []


def test_business_id_dependent_step_must_return_the_same_id_as_evidence():
    context = _context()
    context.business_id = "record-1"
    definition = _definition(
        WorkflowStep(
            "readback",
            "最终回查",
            "maker",
            lambda _execution: WorkflowStepResult(actual_status="approved"),
            requires_business_id=True,
            expected_status="approved",
        )
    )

    with pytest.raises(WorkflowResultError, match="未返回已核对的 business_id"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


def test_created_record_checkpoint_survives_a_later_handler_failure(tmp_path):
    context = _context()
    snapshot = tmp_path / "checkpoint.json"

    def create_then_fail(execution):
        execution.checkpoint(
            WorkflowStepResult(
                business_id="record-1",
                page_scope=execution.page.url,
                record_markers=("AUTO_checkpoint",),
                created_by_workflow=True,
                cleanup_allowed=True,
                cleanup_disposition=WorkflowCleanupDisposition("pending"),
            )
        )
        raise RuntimeError("detail readback failed")

    definition = _definition(
        WorkflowStep(
            "create",
            "创建并回读",
            "maker",
            create_then_fail,
            produces_business_id=True,
            expected_status="draft",
            created_record=True,
            cleanup_allowed=True,
            step_type="mutation",
        )
    )

    with pytest.raises(RuntimeError, match="detail readback failed"):
        WorkflowRunner(
            _Sessions(), snapshot_path=snapshot, reporter=_Reporter()
        ).run(definition, context)

    assert context.business_id == "record-1"
    assert context.cleanup_records[0].business_id == "record-1"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["business_id"] == "record-1"
    assert payload["cleanup_records"][0]["created_step_id"] == "create"


def test_cleanup_checkpoint_survives_post_approval_readback_failure(tmp_path):
    context = _context()
    snapshot = tmp_path / "approval-checkpoint.json"

    def create(execution):
        execution.wait_for_status(
            lambda: "draft",
            expected="draft",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            page_scope=execution.page.url,
            record_markers=("AUTO_checkpoint",),
            actual_status="draft",
            created_by_workflow=True,
            cleanup_allowed=True,
            step_evidence=_mutation_evidence(),
            cleanup_disposition=WorkflowCleanupDisposition("pending"),
        )

    def approve_then_fail(execution):
        execution.checkpoint_cleanup(
            WorkflowCleanupDisposition(
                "retained", "approved_record_not_safely_deletable"
            )
        )
        raise RuntimeError("approved status readback failed")

    definition = _definition(
        WorkflowStep(
            "create",
            "创建",
            "maker",
            create,
            produces_business_id=True,
            expected_status="draft",
            created_record=True,
            cleanup_allowed=True,
            step_type="mutation",
        ),
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            approve_then_fail,
            depends_on=("create",),
            requires_business_id=True,
            requires_status="draft",
            expected_status="approved",
            step_type="mutation",
        ),
    )

    with pytest.raises(RuntimeError, match="approved status readback failed"):
        WorkflowRunner(
            _Sessions(), snapshot_path=snapshot, reporter=_Reporter()
        ).run(definition, context)

    cleanup = context.cleanup_records[0]
    assert cleanup.business_id == "record-1"
    assert cleanup.disposition == "retained"
    assert cleanup.retention_reason == "approved_record_not_safely_deletable"
    assert cleanup.disposition_step_id == "approve"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["cleanup_records"][0]["disposition"] == "retained"
    assert payload["cleanup_records"][0]["disposition_step_id"] == "approve"


def test_checkpoint_cannot_register_a_different_business_record():
    context = _context()
    context.business_id = "record-1"
    context.page_scope = "https://ei.example/original"

    def checkpoint_another_record(execution):
        execution.checkpoint(
            WorkflowStepResult(
                business_id="record-2",
                page_scope=execution.page.url,
                record_markers=("AUTO_other",),
                created_by_workflow=True,
                cleanup_allowed=True,
                cleanup_disposition=WorkflowCleanupDisposition("pending"),
            )
        )

    definition = _definition(
        WorkflowStep(
            "create-another",
            "创建另一记录",
            "maker",
            checkpoint_another_record,
            produces_business_id=True,
            expected_status="draft",
            created_record=True,
            cleanup_allowed=True,
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="不同 business_id"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)

    assert context.business_id == "record-1"
    assert context.page_scope == "https://ei.example/original"
    assert context.record_markers == ()
    assert context.cleanup_records == []


def test_wrong_prior_state_fails_before_the_transition_handler():
    called = []
    reporter = _Reporter()
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "draft"
    definition = _definition(
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            lambda _execution: called.append(True),
            requires_business_id=True,
            requires_status="submitted",
            expected_status="approved",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowPreconditionError, match="前态不匹配"):
        WorkflowRunner(_Sessions(), reporter=reporter).run(definition, context)

    assert called == []
    assert reporter.steps == [(1, "approve", "approver")]


def test_wrong_result_state_and_business_id_are_rejected():
    wrong_status_context = _context()
    wrong_status = _definition(
        WorkflowStep(
            "create",
            "创建",
            "maker",
            lambda _execution: WorkflowStepResult(
                business_id="record-1",
                actual_status="submitted",
                created_by_workflow=True,
                cleanup_allowed=True,
                step_evidence=_mutation_evidence(),
                cleanup_disposition=WorkflowCleanupDisposition("pending"),
            ),
            produces_business_id=True,
            expected_status="draft",
            created_record=True,
            cleanup_allowed=True,
            step_type="mutation",
        )
    )
    with pytest.raises(WorkflowResultError, match="后态不匹配"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(
            wrong_status, wrong_status_context
        )
    assert wrong_status_context.business_id == "record-1"
    assert wrong_status_context.cleanup_records[0].business_id == "record-1"
    assert wrong_status_context.step_records[-1].business_id == "record-1"
    assert wrong_status_context.step_records[-1].actual_status == "submitted"

    context = _context()
    context.business_id = "record-1"
    context.page_scope = "https://ei.example/original"
    different_record = _definition(
        WorkflowStep(
            "review",
            "回查",
            "maker",
            lambda _execution: WorkflowStepResult(
                business_id="record-2",
                page_scope="https://ei.example/maker#/work",
                record_markers=("AUTO_other",),
            ),
        )
    )
    with pytest.raises(WorkflowResultError, match="不同 business_id"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(
            different_record, context
        )
    assert context.business_id == "record-1"
    assert context.page_scope == "https://ei.example/original"
    assert context.record_markers == ()
    assert context.cleanup_records == []


def test_handler_cannot_escalate_cleanup_permission_beyond_step_definition():
    definition = _definition(
        WorkflowStep(
            "read",
            "只读回查",
            "maker",
            lambda _execution: WorkflowStepResult(
                business_id="record-1",
                created_by_workflow=True,
                cleanup_allowed=True,
            ),
        )
    )

    context = _context()
    with pytest.raises(WorkflowResultError, match="创建/清理结果.*不一致"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)

    assert context.cleanup_records == []


def test_created_module_result_must_prove_the_driver_create_readback():
    definition = _definition(
        WorkflowStep(
            "create",
            "创建",
            "maker",
            lambda _execution: ModuleSmokeResult(
                mode="page_access", business_id="record-1"
            ),
            produces_business_id=True,
            expected_status="draft",
            created_record=True,
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="未完成新增回读"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, _context())


def test_read_only_no_op_must_return_an_explicit_result():
    definition = _definition(
        WorkflowStep("read", "只读回查", "maker", lambda _execution: None)
    )

    with pytest.raises(WorkflowResultError, match="不能返回 None"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, _context())


def test_mutation_evidence_requires_a_real_bounded_readback_call():
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "draft"
    definition = _definition(
        WorkflowStep(
            "submit",
            "提交",
            "maker",
            lambda _execution: WorkflowStepResult(
                business_id="record-1",
                actual_status="submitted",
                step_evidence=_mutation_evidence(),
            ),
            requires_business_id=True,
            requires_status="draft",
            expected_status="submitted",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="wait_for_status"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


def test_mutation_step_rejects_missing_structured_evidence():
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "draft"

    def submit(execution):
        execution.wait_for_status(
            lambda: "submitted",
            expected="submitted",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1", actual_status="submitted"
        )

    definition = _definition(
        WorkflowStep(
            "submit",
            "提交",
            "maker",
            submit,
            requires_business_id=True,
            requires_status="draft",
            expected_status="submitted",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="缺少 WorkflowStepEvidence"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


@pytest.mark.parametrize(
    ("overrides", "match"),
    (
        ({"action_available": False}, "可执行动作"),
        ({"record_visible": False}, "记录.*可见"),
        ({"mutation_count": 0}, "只能发生一次"),
        ({"request_count": 2}, "只能发生一次"),
        ({"response_count": 0}, "只能发生一次"),
        ({"http_status": 500}, "HTTP 状态不是 2xx"),
        ({"business_success": False}, "业务状态未成功"),
        ({"business_code": ""}, "业务成功码"),
        ({"response_business_id": "record-2"}, "business_id 与流程不一致"),
        ({"readback_business_id": "record-2"}, "回读事件 business_id"),
        ({"state_source": "list_api"}, "state_source 与证据不一致"),
        ({"poll_attempts": 2}, "回读次数与证据不一致"),
        ({"readback_bounded": False}, "没有有界超时证据"),
        ({"next_action_available": False}, "下一业务操作可用"),
    ),
)
def test_mutation_evidence_contract_rejects_incomplete_proof(overrides, match):
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "draft"

    def submit(execution):
        execution.wait_for_status(
            lambda: "submitted",
            expected="submitted",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="submitted",
            step_evidence=_mutation_evidence(**overrides),
        )

    definition = _definition(
        WorkflowStep(
            "submit",
            "提交",
            "maker",
            submit,
            requires_business_id=True,
            requires_status="draft",
            expected_status="submitted",
            step_type="mutation",
            requires_next_action=True,
        )
    )

    with pytest.raises(WorkflowResultError, match=match):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


@pytest.mark.parametrize(
    ("overrides", "match"),
    (
        (
            {
                "response_business_id": "",
                "mutation_business_id": "record-2",
                "mutation_id_source": "request",
            },
            "请求 business_id 与流程不一致",
        ),
        (
            {
                "response_business_id": "",
                "mutation_business_id": "rk-1",
                "mutation_id_source": "request",
                "mutation_correlation_key": "missing_key",
            },
            "未登记的 correlation key",
        ),
        (
            {
                "response_business_id": "",
                "mutation_business_id": "rk-2",
                "mutation_id_source": "request",
                "mutation_correlation_key": "resource_pool_id",
            },
            "请求 correlation ID 与流程不一致",
        ),
        (
            {
                "response_business_id": "",
                "mutation_business_id": "record-1",
                "mutation_id_source": "",
            },
            "缺少响应 ID 或请求 identity",
        ),
    ),
)
def test_request_mutation_identity_must_match_main_or_registered_correlation(
    overrides, match
):
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "draft"
    context.correlation_ids = {"resource_pool_id": "rk-1"}

    def submit(execution):
        execution.wait_for_status(
            lambda: "submitted",
            expected="submitted",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="submitted",
            step_evidence=_mutation_evidence(**overrides),
        )

    definition = _definition(
        WorkflowStep(
            "submit",
            "提交",
            "maker",
            submit,
            requires_business_id=True,
            requires_status="draft",
            expected_status="submitted",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match=match):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


@pytest.mark.parametrize(
    ("identity_source", "response_business_id"),
    (("request", ""), ("response", "bpm-1")),
)
def test_mutation_identity_accepts_request_or_response_registered_correlation(
    identity_source, response_business_id
):
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "submitted"
    context.correlation_ids = {"projId": "bpm-1"}

    def approve(execution):
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="approved",
            step_evidence=_mutation_evidence(
                response_business_id=response_business_id,
                mutation_business_id="bpm-1",
                mutation_id_source=identity_source,
                mutation_correlation_key="projId",
            ),
        )

    definition = _definition(
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            approve,
            requires_business_id=True,
            requires_status="submitted",
            expected_status="approved",
            step_type="mutation",
        )
    )

    result = WorkflowRunner(_Sessions(), reporter=_Reporter()).run(
        definition, context
    )

    assert result.status == "passed"
    assert result.business_id == "record-1"
    assert result.correlation_ids == {"projId": "bpm-1"}


@pytest.mark.parametrize(
    ("overrides", "match"),
    (
        (
            {
                "response_business_id": "bpm-2",
                "mutation_business_id": "bpm-2",
                "mutation_id_source": "response",
                "mutation_correlation_key": "projId",
            },
            "响应 correlation ID 与流程不一致",
        ),
        (
            {
                "response_business_id": "bpm-2",
                "mutation_business_id": "bpm-1",
                "mutation_id_source": "response",
                "mutation_correlation_key": "projId",
            },
            "响应 correlation ID 与流程不一致",
        ),
        (
            {
                "response_business_id": "bpm-1",
                "mutation_business_id": "bpm-1",
                "mutation_id_source": "request",
                "mutation_correlation_key": "projId",
            },
            "请求 identity 证据不能同时声明响应 ID",
        ),
    ),
)
def test_mutation_correlation_evidence_rejects_mismatch_or_mixed_sources(
    overrides, match
):
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "submitted"
    context.correlation_ids = {"projId": "bpm-1"}

    def approve(execution):
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="approved",
            step_evidence=_mutation_evidence(**overrides),
        )

    definition = _definition(
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            approve,
            requires_business_id=True,
            requires_status="submitted",
            expected_status="approved",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match=match):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


def test_current_step_correlation_result_cannot_self_authorize_a_mutation():
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "submitted"

    def approve(execution):
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="approved",
            correlation_ids={"projId": "bpm-1"},
            step_evidence=_mutation_evidence(
                response_business_id="bpm-1",
                mutation_business_id="bpm-1",
                mutation_id_source="response",
                mutation_correlation_key="projId",
            ),
        )

    definition = _definition(
        WorkflowStep(
            "approve",
            "审批",
            "approver",
            approve,
            requires_business_id=True,
            requires_status="submitted",
            expected_status="approved",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="未登记的 correlation key"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)

    assert context.status == "failed"


@pytest.mark.parametrize(
    "identity_field",
    ("response_business_id", "mutation_business_id", "readback_business_id"),
)
def test_step_evidence_identity_fields_reject_response_objects(identity_field):
    values = {
        "record_visible": True,
        "action_available": True,
        "readback_business_id": "record-1",
        "state_source": "detail_api",
        "poll_attempts": 1,
    }
    values[identity_field] = {"data": {"id": "secret-response"}}

    with pytest.raises(WorkflowResultError, match="必须是安全的短标量"):
        WorkflowStepEvidence(**values)


def test_created_business_id_must_be_response_originated():
    def create(execution):
        execution.wait_for_status(
            lambda: "draft",
            expected="draft",
            business_id="record-1",
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="draft",
            step_evidence=_mutation_evidence(
                response_business_id="",
                mutation_business_id="record-1",
                mutation_id_source="request",
            ),
        )

    definition = _definition(
        WorkflowStep(
            "create",
            "创建",
            "maker",
            create,
            produces_business_id=True,
            expected_status="draft",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="业务 ID 必须来自响应"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, _context())


def test_final_read_only_step_must_relocate_and_read_back_the_same_id():
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "approved"

    def final_readback(execution):
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id=execution.require_business_id(),
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="approved",
            step_evidence=_readback_evidence(),
        )

    definition = _definition(
        WorkflowStep(
            "final-readback",
            "最终回查",
            "maker",
            final_readback,
            requires_business_id=True,
            requires_status="approved",
            expected_status="approved",
            step_type="read_only",
        )
    )

    result = WorkflowRunner(_Sessions(), reporter=_Reporter()).run(
        definition, context
    )

    assert result.status == "passed"
    assert result.step_records[0].step_evidence.record_visible is True


@pytest.mark.parametrize(
    ("values", "match"),
    (
        ({"accessToken": "secret"}, "敏感 key"),
        ({"resource_pool_id": {"id": "rk-1"}}, "必须是字符串"),
        ({"resource_pool_id": "https://ei.example/rk-1"}, "安全的短标量"),
        ({"resource pool id": "rk-1"}, "key 必须是稳定 ID"),
    ),
)
def test_correlation_ids_reject_sensitive_or_complex_values(values, match):
    with pytest.raises(WorkflowResultError, match=match):
        WorkflowStepResult(correlation_ids=values)


def test_correlation_ids_cannot_change_an_existing_value():
    context = _context()
    context.correlation_ids = {"resource_pool_id": "rk-1"}
    definition = _definition(
        WorkflowStep(
            "correlate",
            "关联待办",
            "maker",
            lambda _execution: WorkflowStepResult(
                correlation_ids={"resource_pool_id": "rk-2"}
            ),
        )
    )

    with pytest.raises(WorkflowResultError, match="试图改写 correlation_ids"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)

    assert context.correlation_ids == {"resource_pool_id": "rk-1"}
    assert context.step_records[-1].correlation_ids == {}


@pytest.mark.parametrize(
    ("disposition", "reason", "match"),
    (
        ("retained", "", "必须声明 retention_reason"),
        ("retained", "https://ei.example/record", "安全的简短文本"),
        ("pending", "still needed", "不能声明 retention_reason"),
        ("unknown", "", "不支持的 cleanup disposition"),
    ),
)
def test_cleanup_disposition_requires_a_safe_consistent_reason(
    disposition, reason, match
):
    with pytest.raises(WorkflowResultError, match=match):
        WorkflowCleanupDisposition(disposition, reason)


def test_stable_mode_rejects_mutation_steps_before_execution():
    definition = _definition(
        WorkflowStep(
            "submit",
            "提交",
            "maker",
            lambda _execution: WorkflowStepResult(),
            modes=frozenset({"stable"}),
            produces_business_id=True,
            expected_status="submitted",
            step_type="mutation",
        )
    )

    with pytest.raises(WorkflowConfigurationError, match="stable 模式只能运行只读"):
        definition.steps_for("stable")


def test_dynamic_mutation_accepts_multiple_matched_writes_and_checkpoints_cleanup():
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "submitted"
    context.cleanup_records.append(
        WorkflowCleanupRecord(
            business_id="record-1",
            page_scope="https://ei.example/maker#/work",
            record_markers=("AUTO_record",),
            created_step_id="create",
            run_id=context.run_id,
        )
    )

    def approve(execution):
        execution.checkpoint_cleanup(
            WorkflowCleanupDisposition("retained", "approval_completed")
        )
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id=execution.require_business_id(),
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="approved",
            step_evidence=_mutation_evidence(
                mutation_count=2,
                request_count=2,
                response_count=2,
            ),
        )

    definition = _definition(
        WorkflowStep(
            "approve",
            "动态审批",
            "maker",
            approve,
            requires_business_id=True,
            requires_status="submitted",
            expected_status="approved",
            step_type="dynamic_mutation",
        )
    )

    WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)

    assert context.cleanup_records[0].disposition == "retained"


def test_dynamic_mutation_rejects_unmatched_request_response_counts():
    context = _context()
    context.business_id = "record-1"
    context.actual_status = "submitted"

    def approve(execution):
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id=execution.require_business_id(),
            state_source="detail_api",
        )
        return WorkflowStepResult(
            business_id="record-1",
            actual_status="approved",
            step_evidence=_mutation_evidence(
                mutation_count=2,
                request_count=2,
                response_count=1,
            ),
        )

    definition = _definition(
        WorkflowStep(
            "approve",
            "动态审批",
            "maker",
            approve,
            requires_business_id=True,
            requires_status="submitted",
            expected_status="approved",
            step_type="dynamic_mutation",
        )
    )

    with pytest.raises(WorkflowResultError, match="次数一致"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, context)


def test_result_page_scope_must_match_the_current_role_page():
    definition = _definition(
        WorkflowStep(
            "read",
            "回查",
            "maker",
            lambda _execution: WorkflowStepResult(
                page_scope="https://ei.example/a-different-page"
            ),
        )
    )

    with pytest.raises(WorkflowResultError, match="page_scope 与当前角色页面不一致"):
        WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, _context())


def test_probe_and_standard_profiles_share_core_steps_but_filter_deep_checks():
    calls = []

    def record(name):
        def handler(_execution):
            calls.append(name)
            return WorkflowStepResult()

        return handler

    definition = _definition(
        WorkflowStep(
            "core",
            "核心闭环",
            "maker",
            record("core"),
        ),
        WorkflowStep(
            "deep",
            "深度回读",
            "maker",
            record("deep"),
            depends_on=("core",),
            modes=frozenset({"standard"}),
        ),
    )

    WorkflowRunner(_Sessions(), reporter=_Reporter()).run(definition, _context("probe"))
    assert calls == ["core"]

    calls.clear()
    WorkflowRunner(_Sessions(), reporter=_Reporter()).run(
        definition, _context("standard")
    )
    assert calls == ["core", "deep"]

    with pytest.raises(WorkflowConfigurationError, match="没有声明 stable"):
        definition.steps_for("stable")


def test_mode_filter_must_preserve_dependency_closure():
    definition = _definition(
        WorkflowStep(
            "standard-create",
            "标准创建",
            "maker",
            lambda _execution: None,
            modes=frozenset({"standard"}),
        ),
        WorkflowStep(
            "readback",
            "回查",
            "maker",
            lambda _execution: None,
            depends_on=("standard-create",),
        ),
    )

    with pytest.raises(WorkflowConfigurationError, match="缺少步骤依赖"):
        definition.steps_for("probe")


def test_status_polling_is_bounded_and_returns_the_expected_value(monkeypatch):
    values = iter(("draft", "submitted", "approved"))
    execution = WorkflowStepExecution(
        context=_context(),
        step=WorkflowStep("poll", "轮询", "maker", lambda _execution: None),
        page=_Page("https://ei.example/list"),
    )

    assert execution.wait_for_status(
        lambda: next(values),
        expected="approved",
        business_id="record-1",
        state_source="detail_api",
        timeout_seconds=0.1,
        interval_seconds=0.001,
    ) == "approved"

    with pytest.raises(WorkflowStateTimeout, match="状态轮询超时") as timeout_error:
        execution.wait_for_status(
            lambda: "submitted",
            expected="approved",
            business_id="record-1",
            state_source="detail_api",
            timeout_seconds=0.003,
            interval_seconds=0.001,
        )
    assert "submitted" not in str(timeout_error.value)
    assert "actual=" not in str(timeout_error.value)

    ticks = iter((10.0, 10.002))
    monkeypatch.setattr(
        "ei_ui_smoke.workflow.time.monotonic", lambda: next(ticks)
    )
    with pytest.raises(WorkflowStateTimeout, match="状态轮询超时"):
        execution.wait_for_status(
            lambda: "approved",
            expected="approved",
            business_id="record-1",
            state_source="detail_api",
            timeout_seconds=0.001,
            interval_seconds=0.001,
        )


def test_python_factory_loader_requires_the_selected_workflow_id(monkeypatch, tmp_path):
    module = ModuleType("example_workflows")
    module.build = lambda build_context: _definition(
        WorkflowStep("read", "只读回查", "viewer", lambda _execution: None)
    )
    monkeypatch.setitem(sys.modules, module.__name__, module)
    build_context = WorkflowBuildContext(
        project_root=tmp_path,
        workflow_id="project-approval",
        run_id="run-1",
        data_mode="probe",
    )

    definition = load_workflow_definition("example_workflows:build", build_context)
    assert definition.workflow_id == "project-approval"

    module.build = lambda _build_context: WorkflowDefinition(
        "another-flow",
        "其他流程",
        (WorkflowStep("read", "回查", "viewer", lambda _execution: None),),
    )
    with pytest.raises(WorkflowConfigurationError, match="workflow_id"):
        load_workflow_definition("example_workflows:build", build_context)


def test_python_factory_loader_does_not_wrap_control_flow_exceptions(
    monkeypatch, tmp_path
):
    class StopWorkflow(BaseException):
        pass

    module = ModuleType("stopped_workflows")

    def stop(_build_context):
        raise StopWorkflow()

    module.build = stop
    monkeypatch.setitem(sys.modules, module.__name__, module)
    build_context = WorkflowBuildContext(
        project_root=tmp_path,
        workflow_id="project-approval",
        run_id="run-1",
        data_mode="probe",
    )

    with pytest.raises(StopWorkflow):
        load_workflow_definition("stopped_workflows:build", build_context)


def test_snapshot_path_is_unique_and_sanitizes_run_path_segments(tmp_path):
    path = workflow_snapshot_path(
        tmp_path, run_id="run/with unsafe spaces", workflow_id="project-approval"
    )

    assert path.parent.parent.name == "workflows"
    assert path.name == "project-approval.json"
    assert "unsafe_spaces-" in path.parent.name
    assert "/with" not in str(path)


def test_page_scope_removes_basic_auth_and_query_credentials():
    result = WorkflowStepResult(
        page_scope=(
            "https://user:password@ei.example:8443/"
            "projects?token=secret#/detail?id=1"
        )
    )

    assert result.page_scope == "https://ei.example:8443/projects#/detail"


def test_snapshot_failure_does_not_rewrite_a_completed_business_step(
    monkeypatch, tmp_path
):
    context = _context()
    snapshot = tmp_path / "snapshot-failure.json"
    original = WorkflowContext.write_snapshot
    calls = 0

    def fail_after_step(self, path):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("disk full")
        return original(self, path)

    monkeypatch.setattr(WorkflowContext, "write_snapshot", fail_after_step)
    definition = _definition(
        WorkflowStep(
            "read",
            "只读检查",
            "maker",
            lambda _execution: WorkflowStepResult(actual_status="verified"),
            expected_status="verified",
        )
    )

    with pytest.raises(OSError, match="disk full"):
        WorkflowRunner(
            _Sessions(), snapshot_path=snapshot, reporter=_Reporter()
        ).run(definition, context)

    assert context.status == "failed"
    assert context.failure_type == ""
    assert context.snapshot_error_type == "OSError"
    assert context.actual_status == "verified"
    assert [(record.step_id, record.status) for record in context.step_records] == [
        ("read", "passed")
    ]
    assert context.step_records[0].actual_status == "verified"


def test_allure_reporter_sets_stable_workflow_identity(monkeypatch):
    module_metadata = Mock()
    hidden_parameter = Mock()
    dynamic = SimpleNamespace(parameter=Mock())
    allure = SimpleNamespace(dynamic=dynamic, step=Mock())
    monkeypatch.setattr(
        "ei_ui_smoke.workflow.set_allure_module_metadata", module_metadata
    )
    monkeypatch.setattr(
        "ei_ui_smoke.workflow.set_allure_hidden_parameter", hidden_parameter
    )
    monkeypatch.setitem(sys.modules, "allure", allure)
    definition = _definition(
        WorkflowStep("read", "回查", "viewer", lambda _execution: None)
    )

    AllureWorkflowReporter().start(definition, _context())

    module_metadata.assert_called_once_with(
        module_id="workflow::project-approval",
        module_name="业务流程/项目提交审批",
        test_title="项目提交审批 [project-approval]",
    )
    dynamic.parameter.assert_any_call("workflow_id", "project-approval")
    dynamic.parameter.assert_any_call("data_mode", "standard", excluded=True)
    hidden_parameter.assert_called_once_with("workflow_run_id", "run-20260818")


def test_allure_reporter_emits_only_a_safe_structured_evidence_summary(
    monkeypatch,
):
    titles = []

    @contextmanager
    def record_step(title):
        titles.append(title)
        yield

    monkeypatch.setitem(sys.modules, "allure", SimpleNamespace(step=record_step))
    step = WorkflowStep(
        "approve",
        "审批",
        "approver",
        lambda _execution: WorkflowStepResult(),
        requires_business_id=True,
        expected_status="approved",
        step_type="mutation",
    )
    result = WorkflowStepResult(
        business_id="record-1",
        actual_status="approved",
        step_evidence=_mutation_evidence(
            state_source="approval_detail_api", next_action_available=None
        ),
        cleanup_disposition=WorkflowCleanupDisposition(
            "retained", "approved_record_not_deletable"
        ),
        correlation_ids={"bpm_instance_id": "bpm-secret-value"},
    )

    AllureWorkflowReporter().evidence(step, result)

    assert len(titles) == 1
    assert "http_status=200" in titles[0]
    assert "cleanup=retained" in titles[0]
    assert "retention_reason=approved_record_not_deletable" in titles[0]
    assert "correlations=bpm_instance_id" in titles[0]
    assert "record-1" not in titles[0]
    assert "bpm-secret-value" not in titles[0]
