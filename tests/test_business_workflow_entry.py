from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ENTRY_PATH = Path(__file__).with_name("test_business_workflow.py")
ENTRY_MODULE_NAME = "business_workflow_entry_under_test"
ENTRY_SPEC = importlib.util.spec_from_file_location(ENTRY_MODULE_NAME, ENTRY_PATH)
assert ENTRY_SPEC is not None and ENTRY_SPEC.loader is not None
entry = importlib.util.module_from_spec(ENTRY_SPEC)
sys.modules[ENTRY_MODULE_NAME] = entry
ENTRY_SPEC.loader.exec_module(entry)


class _Config:
    def __init__(self, *, data_mode=None):
        self.data_mode = data_mode

    def getoption(self, name):
        assert name == "--data-mode"
        return self.data_mode


class _Metafunc:
    fixturenames = ("workflow_case",)

    def __init__(self, *, data_mode=None):
        self.config = _Config(data_mode=data_mode)
        self.parametrize_calls = []

    def parametrize(self, *args, **kwargs):
        self.parametrize_calls.append((args, kwargs))


def _clear_workflow_environment(monkeypatch):
    for name in (
        "EI_WORKFLOW_ID",
        "EI_WORKFLOW_FACTORY",
        "EI_WORKFLOW_ROLE_STATES_JSON",
        "EI_ACTION",
        "EI_ACTIONS_JSON",
        "EI_ACTION_PATH",
        "EI_ACTION_PATHS_JSON",
    ):
        monkeypatch.delenv(name, raising=False)


def test_collection_parametrizes_an_explicit_skip_when_no_workflow_is_selected(
    monkeypatch,
):
    _clear_workflow_environment(monkeypatch)
    load_definition = Mock(side_effect=AssertionError("factory must not be loaded"))
    monkeypatch.setattr(entry, "load_workflow_definition", load_definition)
    metafunc = _Metafunc()

    entry.pytest_generate_tests(metafunc)

    load_definition.assert_not_called()
    assert len(metafunc.parametrize_calls) == 1
    args, kwargs = metafunc.parametrize_calls[0]
    assert kwargs == {}
    assert args[0] == "workflow_case"
    parameter = args[1][0]
    assert parameter.values == (None,)
    assert parameter.id == "workflow-not-selected"
    assert [mark.name for mark in parameter.marks] == ["skip"]


def test_selected_workflow_configuration_error_is_raised_during_collection(
    monkeypatch,
):
    _clear_workflow_environment(monkeypatch)
    monkeypatch.setenv("EI_WORKFLOW_ID", "project-approval")
    metafunc = _Metafunc(data_mode="standard")

    with pytest.raises(pytest.UsageError, match="EI_WORKFLOW_FACTORY"):
        entry.pytest_generate_tests(metafunc)

    assert metafunc.parametrize_calls == []


def test_selected_workflow_rejects_inherited_single_action_during_collection(
    monkeypatch,
):
    _clear_workflow_environment(monkeypatch)
    monkeypatch.setenv("EI_WORKFLOW_ID", "project-approval")
    monkeypatch.setenv(
        "EI_WORKFLOW_FACTORY", "tests.workflows.approval:build_workflow"
    )
    monkeypatch.setenv("EI_ACTION", "新增")
    metafunc = _Metafunc(data_mode="standard")

    with pytest.raises(pytest.UsageError, match="EI_ACTION"):
        entry.pytest_generate_tests(metafunc)

    assert metafunc.parametrize_calls == []


def test_failure_attachment_prefers_cached_pre_cleanup_evidence(monkeypatch):
    cached = SimpleNamespace(
        url=(
            "https://user:secret@ei.example:8443/app?token=hidden"
            "#/detail?businessId=secret"
        ),
        screenshot=b"cached-screenshot",
    )
    page = object()
    pool = SimpleNamespace(current_role="approver", current_page=page)
    context = SimpleNamespace(
        step_records=[SimpleNamespace(status="failed", role="approver")]
    )
    consume = Mock(return_value=cached)
    capture = Mock()
    attach = Mock()
    monkeypatch.setattr(entry, "consume_failure_evidence", consume)
    monkeypatch.setattr(entry, "capture_failure_evidence", capture)
    monkeypatch.setattr(entry.allure, "attach", attach)

    entry._attach_workflow_failure(pool, context)

    consume.assert_called_once_with(page)
    capture.assert_not_called()
    assert attach.call_args_list[0].args[:2] == (
        "https://ei.example:8443/app#/detail",
        "失败页面 URL",
    )
    assert attach.call_args_list[1].args[:2] == (
        b"cached-screenshot",
        "失败页面截图",
    )


def test_failure_attachment_does_not_use_the_previous_role_page(monkeypatch):
    page = object()
    pool = SimpleNamespace(current_role="maker", current_page=page)
    context = SimpleNamespace(
        step_records=[SimpleNamespace(status="failed", role="approver")]
    )
    consume = Mock()
    capture = Mock()
    attach = Mock()
    monkeypatch.setattr(entry, "consume_failure_evidence", consume)
    monkeypatch.setattr(entry, "capture_failure_evidence", capture)
    monkeypatch.setattr(entry.allure, "attach", attach)

    entry._attach_workflow_failure(pool, context)

    consume.assert_not_called()
    capture.assert_not_called()
    attach.assert_not_called()


def test_failure_attachment_captures_matching_step_page_only_without_cache(
    monkeypatch,
):
    captured = SimpleNamespace(
        url="https://ei.example/app#/detail",
        screenshot=b"new-screenshot",
    )
    page = object()
    pool = SimpleNamespace(current_role="approver", current_page=page)
    context = SimpleNamespace(
        step_records=[SimpleNamespace(status="failed", role="approver")]
    )
    consume = Mock(side_effect=(None, captured))
    capture = Mock()
    monkeypatch.setattr(entry, "consume_failure_evidence", consume)
    monkeypatch.setattr(entry, "capture_failure_evidence", capture)
    monkeypatch.setattr(entry.allure, "attach", Mock())

    entry._attach_workflow_failure(pool, context)

    assert consume.call_args_list == [
        ((page,), {}),
        ((page,), {}),
    ]
    capture.assert_called_once_with(page, "业务流程步骤失败")


def test_failure_attachment_accepts_a_dynamic_login_page_bound_to_the_step_role(
    monkeypatch,
):
    cached = SimpleNamespace(
        url="https://ei.example/app#/workflow/task?projId=hidden",
        screenshot=b"dynamic-actor-screenshot",
    )
    page = object()
    pool = SimpleNamespace(
        current_role="login-4c0b75d2b7d0",
        current_workflow_role="maker",
        current_page=page,
    )
    context = SimpleNamespace(
        step_records=[SimpleNamespace(status="failed", role="maker")]
    )
    consume = Mock(return_value=cached)
    attach = Mock()
    monkeypatch.setattr(entry, "consume_failure_evidence", consume)
    monkeypatch.setattr(entry.allure, "attach", attach)

    entry._attach_workflow_failure(pool, context)

    consume.assert_called_once_with(page)
    assert attach.call_args_list[1].args[:2] == (
        b"dynamic-actor-screenshot",
        "失败页面截图",
    )
