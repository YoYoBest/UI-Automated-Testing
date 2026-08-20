from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

import ei_ui_smoke.workflows.resource_pool_approval as resource_pool
from ei_ui_smoke.workflow import (
    WorkflowBuildContext,
    WorkflowCleanupDisposition,
    WorkflowCleanupRecord,
    WorkflowConfigurationError,
    WorkflowContext,
    WorkflowResultError,
    WorkflowStep,
    WorkflowStepExecution,
)


BUSINESS_ID = "resource-1"
PROJ_ID = "project-9"
MARKER = "AUTO_resource_pool_run"


def _config_payload(*, identity: str = "response") -> dict[str, Any]:
    approval_response: dict[str, Any] = {
        "method": "POST",
        "url_path": "/ezgo/bpm-service/task/complete",
        "business_code_path": "status",
        "success_values": ["0"],
    }
    identity_values = {
        "response": ("response_business_id_path", "data.projId"),
        "request": ("request_business_id_path", "payload.projId"),
        "query": ("request_business_id_query_key", "projId"),
    }
    key, value = identity_values[identity]
    approval_response[key] = value
    return {
        "resource_pool_url": "https://ei.example/ei-view/#/resourcePool",
        "todo_page_url": "https://ei.example/ei-view/#/todo",
        "todo_rows_path": "payload.rows",
        "todo_open_url_path": "payload.links.open",
        "todo_matcher": {
            "field_path": "businessKey",
            "operator": "equals",
            "value_from": "projId",
        },
        "process_preview": {
            "method": "GET",
            "url_path": "/ezgo/bpm-service/process/preview",
            "request_id_key": "projId",
            "request_id_from": "projId",
            "nodes_path": "data.nodes",
            "node_status_path": "status",
            "active_status_values": ["active"],
            "assignee_login_name_path": "assignee.loginName",
            "max_transitions": 3,
        },
        "approval_button": {"text": "同意"},
        "approval_response": approval_response,
        "approved_status": "2",
        "request_timeout_ms": 1_000,
        "mutation_quiet_ms": 50,
        "state_timeout_seconds": 1,
        "todo_timeout_seconds": 1,
    }


def _parse_config(*, identity: str = "response", confirmation: bool = False):
    payload = _config_payload(identity=identity)
    if confirmation:
        payload["approval_confirmation"] = {"text": "确认"}
    return resource_pool.parse_resource_pool_approval_config(
        json.dumps(payload, ensure_ascii=False)
    )


def test_process_preview_configuration_requires_read_only_unique_login_contract():
    payload = _config_payload()
    payload["process_preview"]["url_path"] = "/ezgo/bpm-service/task/complete"

    with pytest.raises(WorkflowConfigurationError, match="只读接口"):
        resource_pool.parse_resource_pool_approval_config(json.dumps(payload))

    payload = _config_payload()
    payload["process_preview"]["assignee_login_name_path"] = ""

    with pytest.raises(WorkflowConfigurationError, match="不能为空"):
        resource_pool.parse_resource_pool_approval_config(json.dumps(payload))


def test_process_preview_rejects_multiple_or_display_name_assignees():
    config = _parse_config()

    with pytest.raises(WorkflowResultError, match="多个候选登录名"):
        resource_pool._next_preview_login_name(
            [
                {"status": "active", "assignee": {"loginName": "approver01"}},
                {"status": "active", "assignee": {"loginName": "approver02"}},
            ],
            config,
        )

    with pytest.raises(WorkflowResultError, match="唯一登录名"):
        resource_pool._next_preview_login_name(
            [{"status": "active", "assignee": {"loginName": "张三"}}],
            config,
        )


class _Request:
    def __init__(self, method: str, url: str, body: Any = None):
        self.method = method
        self.url = url
        self.post_data_json = body


class _Response:
    def __init__(self, request: _Request, body: Any, *, status: int = 200):
        self.request = request
        self.status = status
        self._body = body

    def json(self):
        return self._body


class _Button:
    def __init__(self, name: str, events: list[str]):
        self.name = name
        self.events = events

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def click(self):
        self.events.append(f"click:{self.name}")


class _Locator:
    def __init__(self, items=()):
        self.items = tuple(items)

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _ApprovalPage:
    def __init__(self, button_names: tuple[str, ...], events: list[str]):
        self.url = "about:blank"
        self.events = events
        self.buttons = {
            name: _Button(name, self.events) for name in button_names
        }

    def goto(self, url, *, wait_until):
        assert wait_until == "domcontentloaded"
        self.url = url
        self.events.append(f"goto:{url}")

    def reload(self, *, wait_until):
        assert wait_until == "domcontentloaded"
        self.events.append(f"reload:{self.url}")

    def get_by_role(self, role, *, name, exact):
        assert role == "button"
        assert exact is True
        button = self.buttons.get(name)
        return _Locator(() if button is None else (button,))

    def locator(self, selector):
        button = self.buttons.get(selector)
        return _Locator(() if button is None else (button,))


def _mutation_observation(*, identity: str = "response"):
    request_body = None
    request_url = "https://ei.example/ezgo/bpm-service/task/complete"
    response_body: dict[str, Any] = {"status": 0}
    if identity == "response":
        response_body["data"] = {"projId": PROJ_ID}
    elif identity == "request":
        request_body = {"payload": {"projId": PROJ_ID}}
    else:
        request_url += f"?projId={PROJ_ID}"
    return resource_pool._MutationObservation(
        request_count=1,
        response_count=1,
        http_status=200,
        business_code="0",
        request_url=request_url,
        request_body=request_body,
        response_body=response_body,
    )


def _install_capture(monkeypatch, observation, events: list[str]):
    class _Capture:
        def __init__(self, _page, **kwargs):
            events.append(f"capture:{kwargs['method']}:{kwargs['path']}")
            assert kwargs["forbidden_paths"] == (
                resource_pool.FORBIDDEN_APPROVAL_CALLBACK_PATH,
            )

        def __enter__(self):
            return self

        def wait(self):
            events.append("capture:wait")

        def __exit__(self, _exc_type, _exc, _traceback):
            return None

        def observation(self, *, business_code_path, success_values):
            assert business_code_path == "status"
            assert tuple(success_values) == ("0",)
            return observation

    monkeypatch.setattr(resource_pool, "_MutationCapture", _Capture)


def _approval_execution(page, events: list[str], sessions=None):
    step = WorkflowStep(
        step_id="approve-storage-application",
        title="按流程预览动态审批通过",
        role="maker",
        handler=lambda _execution: None,
        requires_business_id=True,
        requires_status="1",
        expected_status="2",
        step_type="dynamic_mutation",
    )
    context = WorkflowContext(
        workflow_id=resource_pool.WORKFLOW_ID,
        run_id="run-20260819",
        data_mode="standard",
        business_id=BUSINESS_ID,
        page_scope="https://ei.example/ei-view/#/resourcePool",
        record_markers=(MARKER,),
        correlation_ids={"projId": PROJ_ID},
        actual_status="1",
        cleanup_records=[
            WorkflowCleanupRecord(
                business_id=BUSINESS_ID,
                page_scope="https://ei.example/ei-view/#/resourcePool",
                record_markers=(MARKER,),
                created_step_id="create-resource-pool",
                run_id="run-20260819",
            )
        ],
    )

    def checkpoint(disposition: WorkflowCleanupDisposition):
        events.append(f"checkpoint:{disposition.disposition}")
        context.checkpoint_cleanup_disposition(step, disposition)
        return disposition

    execution = WorkflowStepExecution(
        context=context,
        step=step,
        page=page,
        sessions=sessions,
        cleanup_checkpoint_writer=checkpoint,
    )
    return execution, context


class _DynamicSessions:
    def __init__(self, maker_page, actor_page, events: list[str]):
        self.maker_page = maker_page
        self.actor_page = actor_page
        self.events = events

    def page_for(self, role):
        assert role == "maker"
        self.events.append("preview:maker")
        return self.maker_page

    def page_for_login(self, login_name, *, entry_url, password, state_dir):
        assert login_name == "approver01"
        assert entry_url == "https://ei.example/ei-view/#/todo"
        assert password == "fixed-password"
        assert state_dir.name == "dynamic"
        self.events.append("login:approver01")
        return self.actor_page


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ("response", (PROJ_ID, "response", "projId", PROJ_ID)),
        ("request", (PROJ_ID, "request", "projId", "")),
        ("query", (PROJ_ID, "request", "projId", "")),
    ],
)
def test_config_parses_each_exclusive_approval_identity_source(identity, expected):
    config = _parse_config(identity=identity)

    actual = resource_pool._approval_identity(
        _mutation_observation(identity=identity),
        config.approval_response,
        business_id=BUSINESS_ID,
        proj_id=PROJ_ID,
    )

    assert actual == expected
    configured_sources = (
        config.approval_response.response_business_id_path,
        config.approval_response.request_business_id_path,
        config.approval_response.request_business_id_query_key,
    )
    assert sum(bool(value) for value in configured_sources) == 1


@pytest.mark.parametrize("source_count", [0, 2])
def test_config_requires_exactly_one_approval_identity_source(source_count):
    payload = _config_payload()
    response = payload["approval_response"]
    response.pop("response_business_id_path")
    if source_count == 2:
        response["request_business_id_path"] = "payload.projId"
        response["request_business_id_query_key"] = "projId"

    with pytest.raises(WorkflowConfigurationError, match="必须且只能声明一种 identity"):
        resource_pool.parse_resource_pool_approval_config(json.dumps(payload))


@pytest.mark.parametrize(
    ("business_code_path", "success_values"),
    [
        ("state", ["SUCCESS"]),
        ("code", ["000000"]),
        ("status", ["0"]),
    ],
)
def test_config_accepts_only_source_confirmed_approval_success_envelopes(
    business_code_path, success_values
):
    payload = _config_payload()
    payload["approval_response"]["business_code_path"] = business_code_path
    payload["approval_response"]["success_values"] = success_values

    config = resource_pool.parse_resource_pool_approval_config(json.dumps(payload))

    assert config.approval_response.business_code_path == business_code_path
    assert config.approval_response.success_values == tuple(success_values)


@pytest.mark.parametrize(
    ("business_code_path", "success_values"),
    [("foo", ["bar"]), ("status", ["success"]), ("status", ["0", "1"])],
)
def test_config_rejects_unconfirmed_approval_success_envelopes(
    business_code_path, success_values
):
    payload = _config_payload()
    payload["approval_response"]["business_code_path"] = business_code_path
    payload["approval_response"]["success_values"] = success_values

    with pytest.raises(WorkflowConfigurationError, match="源码确认的成功信封"):
        resource_pool.parse_resource_pool_approval_config(json.dumps(payload))


def test_config_and_runtime_reject_proj_storage_approval_callback():
    payload = _config_payload()
    payload["approval_response"]["url_path"] = (
        "/ezgo/ei-service/projStorage/approval"
    )

    with pytest.raises(WorkflowConfigurationError, match="BPM 回调接口"):
        resource_pool.parse_resource_pool_approval_config(json.dumps(payload))

    capture = resource_pool._MutationCapture(
        object(),
        method="POST",
        path="/ezgo/bpm-service/task/complete",
        timeout_ms=1_000,
        quiet_ms=10,
        forbidden_paths=(resource_pool.FORBIDDEN_APPROVAL_CALLBACK_PATH,),
    )
    capture._request_listener(
        _Request("POST", "https://ei.example/ezgo/ei-service/projStorage/approval")
    )
    with pytest.raises(WorkflowResultError, match="禁止的 /projStorage/approval"):
        capture.observation(business_code_path="status", success_values=("0",))


@pytest.mark.parametrize(
    "success_envelope",
    [
        {"state": "SUCCESS"},
        {"code": "000000"},
        {"status": 0},
    ],
)
def test_todo_read_uses_fixed_app_id_and_accepts_supported_success_envelopes(
    monkeypatch, success_envelope
):
    config = _parse_config()
    response_body = {
        **success_envelope,
        "payload": {"rows": [{"businessKey": PROJ_ID}]},
    }
    calls = []

    def request(_page, **kwargs):
        calls.append(kwargs)
        return resource_pool._HttpResult(
            status=200,
            url=resource_pool.TODO_PATH,
            body=response_body,
        )

    monkeypatch.setattr(resource_pool, "_browser_json_request", request)

    assert resource_pool._read_todos(object(), config) == [
        {"businessKey": PROJ_ID}
    ]
    assert len(calls) == 1
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == resource_pool.TODO_PATH
    assert calls[0]["body"]["query"]["app_id"] == resource_pool.FLOW_APP_ID


def test_mutation_capture_quiet_window_restarts_after_new_network_activity(
    monkeypatch,
):
    clock = [0.0]
    monkeypatch.setattr(resource_pool.time, "monotonic", lambda: clock[0])

    request = _Request(
        "POST", "https://ei.example/ezgo/bpm-service/task/complete"
    )
    response = _Response(request, {"status": 0})

    class _ClockPage:
        triggered = False

        def wait_for_timeout(self, milliseconds):
            before = clock[0]
            clock[0] += milliseconds / 1_000
            if not self.triggered and before < 0.3 <= clock[0]:
                self.triggered = True
                capture._response_listener(response)

    page = _ClockPage()
    capture = resource_pool._MutationCapture(
        page,
        method="POST",
        path="/ezgo/bpm-service/task/complete",
        timeout_ms=2_000,
        quiet_ms=500,
    )
    capture._response_listener(response)

    capture.wait()

    assert page.triggered is True
    assert len(capture.responses) == 2
    assert clock[0] >= 0.8


@pytest.mark.parametrize(
    ("with_confirmation", "expected_clicks"),
    [
        (False, ["click:同意"]),
        (True, ["click:同意", "click:确认"]),
    ],
)
def test_approve_handler_uses_nested_open_url_optional_confirmation_and_task_scope(
    monkeypatch, tmp_path, with_confirmation, expected_clicks
):
    config = _parse_config(confirmation=with_confirmation)
    events: list[str] = []
    button_names = ("同意", "确认") if with_confirmation else ("同意",)
    page = _ApprovalPage(button_names, events)
    sessions = _DynamicSessions(page, page, events)
    execution, context = _approval_execution(page, events, sessions)
    task_url = (
        "https://ei.example/ei-view/#/workflow/task"
        f"?projId={PROJ_ID}&credential=must-not-persist"
    )
    todo = {"payload": {"links": {"open": task_url}}}
    monkeypatch.setattr(
        resource_pool,
        "_wait_for_exact_todo",
        lambda _page, _config, **_kwargs: todo,
    )
    preview_nodes = iter((
        [{"status": "active", "assignee": {"loginName": "approver01"}}],
        [],
    ))
    monkeypatch.setattr(
        resource_pool,
        "_read_process_preview",
        lambda *_args, **_kwargs: next(preview_nodes),
    )
    _install_capture(monkeypatch, _mutation_observation(), events)

    monkeypatch.setattr(resource_pool, "_read_detail", lambda *_args, **_kwargs: "2")

    def poll(*_args, **_kwargs):
        events.append("poll:approved")
        return "2", 2

    monkeypatch.setattr(resource_pool, "_poll_detail_status", poll)

    monkeypatch.setenv(resource_pool.WORKFLOW_LOGIN_PASSWORD_ENV, "fixed-password")
    result = resource_pool._approve_handler(config, tmp_path)(execution)

    assert [event for event in events if event.startswith("click:")] == expected_clicks
    assert f"goto:{task_url}" in events
    assert events.index("checkpoint:retained") < events.index("poll:approved")
    assert "preview:maker" in events
    assert "login:approver01" in events
    assert result.page_scope == "https://ei.example/ei-view/#/workflow/task"
    assert result.page_scope != config.resource_pool_url
    assert result.actual_status == "2"
    assert context.cleanup_records[0].disposition == "retained"


def test_successful_approval_stays_retained_when_status_readback_fails(monkeypatch, tmp_path):
    config = _parse_config()
    events: list[str] = []
    page = _ApprovalPage(("同意",), events)
    sessions = _DynamicSessions(page, page, events)
    execution, context = _approval_execution(page, events, sessions)
    monkeypatch.setattr(
        resource_pool,
        "_wait_for_exact_todo",
        lambda _page, _config, **_kwargs: {
            "payload": {
                "links": {
                    "open": f"/ei-view/#/workflow/task?projId={PROJ_ID}"
                }
            }
        },
    )
    preview_nodes = iter((
        [{"status": "active", "assignee": {"loginName": "approver01"}}],
        [],
    ))
    monkeypatch.setattr(
        resource_pool,
        "_read_process_preview",
        lambda *_args, **_kwargs: next(preview_nodes),
    )
    _install_capture(monkeypatch, _mutation_observation(), events)
    monkeypatch.setattr(resource_pool, "_read_detail", lambda *_args, **_kwargs: "2")

    def failed_readback(*_args, **_kwargs):
        events.append("poll:failed")
        raise WorkflowResultError("approved status readback failed")

    monkeypatch.setattr(resource_pool, "_poll_detail_status", failed_readback)

    with pytest.raises(WorkflowResultError, match="status readback failed"):
        monkeypatch.setenv(resource_pool.WORKFLOW_LOGIN_PASSWORD_ENV, "fixed-password")
        resource_pool._approve_handler(config, tmp_path)(execution)

    cleanup = context.cleanup_records[0]
    assert events.index("checkpoint:retained") < events.index("poll:failed")
    assert cleanup.disposition == "retained"
    assert cleanup.retention_reason == "approved_record_not_safely_deletable"
    assert cleanup.disposition_step_id == "approve-storage-application"


def test_approve_handler_repeats_preview_and_switches_dynamic_actors(
    monkeypatch, tmp_path
):
    config = _parse_config()
    events: list[str] = []
    first_page = _ApprovalPage(("同意",), events)
    second_page = _ApprovalPage(("同意",), events)

    class _TwoActorSessions:
        def page_for(self, role):
            assert role == "maker"
            events.append("preview:maker")
            return first_page

        def page_for_login(self, login_name, **_kwargs):
            events.append(f"login:{login_name}")
            return {"approver01": first_page, "approver02": second_page}[login_name]

    execution, context = _approval_execution(
        first_page, events, _TwoActorSessions()
    )
    preview_nodes = iter((
        [{"status": "active", "assignee": {"loginName": "approver01"}}],
        [{"status": "active", "assignee": {"loginName": "approver02"}}],
        [],
    ))
    monkeypatch.setattr(
        resource_pool,
        "_read_process_preview",
        lambda *_args, **_kwargs: next(preview_nodes),
    )
    monkeypatch.setattr(
        resource_pool,
        "_wait_for_exact_todo",
        lambda *_args, **_kwargs: {
            "payload": {"links": {"open": f"/ei-view/#/workflow/task?projId={PROJ_ID}"}}
        },
    )
    _install_capture(monkeypatch, _mutation_observation(), events)
    statuses = iter(("1", "2"))
    monkeypatch.setattr(resource_pool, "_read_detail", lambda *_args, **_kwargs: next(statuses))
    monkeypatch.setattr(
        resource_pool,
        "_poll_detail_status",
        lambda *_args, **_kwargs: ("2", 3),
    )
    monkeypatch.setenv(resource_pool.WORKFLOW_LOGIN_PASSWORD_ENV, "fixed-password")

    result = resource_pool._approve_handler(config, tmp_path)(execution)

    assert events.count("preview:maker") == 3
    assert [event for event in events if event.startswith("login:")] == [
        "login:approver01",
        "login:approver02",
    ]
    assert [event for event in events if event == "click:同意"] == [
        "click:同意",
        "click:同意",
    ]
    assert result.observations["mutation_count"] == 2
    assert result.observations["request_count"] == 2
    assert result.observations["response_count"] == 2
    assert context.cleanup_records[0].disposition == "retained"


def test_factory_rejects_missing_dynamic_login_password_before_driver_creation(
    monkeypatch, tmp_path: Path
):
    config = _parse_config()
    monkeypatch.setattr(resource_pool, "_load_config", lambda: config)
    build_resources = Mock()
    monkeypatch.setattr(resource_pool, "_build_driver_resources", build_resources)
    monkeypatch.delenv(resource_pool.WORKFLOW_LOGIN_PASSWORD_ENV, raising=False)

    with pytest.raises(WorkflowConfigurationError, match="EI_WORKFLOW_LOGIN_PASSWORD"):
        resource_pool.build_workflow(
            WorkflowBuildContext(
                project_root=tmp_path,
                workflow_id=resource_pool.WORKFLOW_ID,
                run_id="run-20260819",
                data_mode="standard",
            )
        )

    build_resources.assert_not_called()


def test_stable_handler_executes_maker_read_only_api_check(monkeypatch):
    config = _parse_config()
    calls: list[tuple[str, Any]] = []

    class _StablePage:
        def __init__(self, url):
            self.url = url

        def goto(self, url, *, wait_until):
            assert wait_until == "domcontentloaded"
            self.url = url
            calls.append(("goto", url))

        def reload(self, *, wait_until):
            assert wait_until == "domcontentloaded"
            calls.append(("reload", self.url))

        def locator(self, selector):
            assert selector == "input[placeholder='请输入企业名称/企业简称']"
            return _Locator((_Button("keyword", []),))

    def browser_read(_page, **kwargs):
        calls.append(("resource-read", kwargs))
        return resource_pool._HttpResult(
            status=200,
            url=resource_pool.RESOURCE_LIST_PATH,
            body={"status": "0", "data": []},
        )

    monkeypatch.setattr(resource_pool, "_browser_json_request", browser_read)
    maker_step = WorkflowStep(
        "stable-resource-pool-health",
        "资源池健康检查",
        "maker",
        lambda _execution: None,
    )
    maker_page = _StablePage("about:blank")
    maker_result = resource_pool._stable_health_handler(config)(
        WorkflowStepExecution(
            context=WorkflowContext(
                workflow_id=resource_pool.WORKFLOW_ID,
                run_id="stable-maker",
                data_mode="stable",
            ),
            step=maker_step,
            page=maker_page,
        )
    )
    resource_call = next(value for kind, value in calls if kind == "resource-read")
    assert resource_call["method"] == "POST"
    assert resource_call["path"] == resource_pool.RESOURCE_LIST_PATH
    assert resource_call["body"]["formCode"] == resource_pool.FORM_CODE
    assert maker_result.observations["state_source"] == "resource_pool_list"
    assert maker_result.step_evidence is None


def test_factory_declares_create_next_action_and_dynamic_approval_step(
    monkeypatch, tmp_path: Path
):
    config = _parse_config()
    monkeypatch.setattr(resource_pool, "_load_config", lambda: config)
    monkeypatch.setattr(
        resource_pool,
        "_build_driver_resources",
        lambda _build: type("Resources", (), {"project_root": tmp_path})(),
    )
    monkeypatch.setenv(resource_pool.WORKFLOW_LOGIN_PASSWORD_ENV, "fixed-password")
    definition = resource_pool.build_workflow(
        WorkflowBuildContext(
            project_root=tmp_path,
            workflow_id=resource_pool.WORKFLOW_ID,
            run_id="run-20260819",
            data_mode="standard",
        )
    )

    create = next(
        step for step in definition.steps if step.step_id == "create-resource-pool"
    )
    dynamic_approval = next(
        step for step in definition.steps if step.step_id == "approve-storage-application"
    )
    stable_steps = definition.steps_for("stable")

    assert create.requires_next_action is True
    assert create.step_type == "mutation"
    assert dynamic_approval.role == "maker"
    assert dynamic_approval.step_type == "dynamic_mutation"
    assert [step.step_id for step in stable_steps] == ["stable-resource-pool-health"]
    assert [step.role for step in stable_steps] == ["maker"]
    assert all(step.step_type == "read_only" for step in stable_steps)
    assert definition.required_roles("stable") == ("maker",)
