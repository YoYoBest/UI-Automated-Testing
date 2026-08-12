from __future__ import annotations

import json

from ei_ui_smoke import pytest_progress


class _Session:
    def __init__(self, item_count: int) -> None:
        self.items = [object()] * item_count


def test_plugin_emits_collected_and_finished_jsonl_events(tmp_path, monkeypatch):
    progress_file = tmp_path / "nested" / "progress.jsonl"
    monkeypatch.setenv(pytest_progress.PROGRESS_FILE_ENV, str(progress_file))
    monkeypatch.setenv(pytest_progress.PROGRESS_COMMAND_ENV, "command-7")

    pytest_progress.pytest_collection_finish(_Session(46))
    pytest_progress.pytest_runtest_logfinish(
        "tests/test_module.py::test_case[项目名称]", None,
    )

    events = [
        json.loads(line)
        for line in progress_file.read_text(encoding="utf-8").splitlines()
    ]
    assert events == [
        {"event": "collected", "command_id": "command-7", "count": 46},
        {
            "event": "finished",
            "command_id": "command-7",
            "nodeid": "tests/test_module.py::test_case[项目名称]",
        },
    ]


def test_batch_emits_logical_total_and_one_finished_event_per_binding(
    tmp_path, monkeypatch,
):
    progress_file = tmp_path / "progress.jsonl"
    monkeypatch.setenv(pytest_progress.PROGRESS_FILE_ENV, str(progress_file))
    monkeypatch.setenv(pytest_progress.PROGRESS_COMMAND_ENV, "batch-1")

    pytest_progress.emit_logical_progress_total(2, transaction_count=1)
    pytest_progress.emit_logical_progress_finished("logical-1", outcome="passed")
    pytest_progress.emit_logical_progress_finished("logical-2", outcome="execution_failed")

    events = [
        json.loads(line)
        for line in progress_file.read_text(encoding="utf-8").splitlines()
    ]
    assert events == [
        {
            "event": "logical_collected",
            "command_id": "batch-1",
            "count": 2,
            "transaction_count": 1,
        },
        {
            "event": "logical_finished",
            "command_id": "batch-1",
            "nodeid": "logical-1",
            "outcome": "passed",
        },
        {
            "event": "logical_finished",
            "command_id": "batch-1",
            "nodeid": "logical-2",
            "outcome": "execution_failed",
        },
    ]
def test_plugin_is_noop_without_progress_file_environment(monkeypatch):
    monkeypatch.delenv(pytest_progress.PROGRESS_FILE_ENV, raising=False)
    monkeypatch.delenv(pytest_progress.PROGRESS_COMMAND_ENV, raising=False)

    pytest_progress.pytest_collection_finish(_Session(2))
    pytest_progress.pytest_runtest_logfinish("tests/test_module.py::test_case", None)


def test_plugin_does_not_change_pytest_result_when_event_path_is_unwritable(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(pytest_progress.PROGRESS_FILE_ENV, str(tmp_path))
    monkeypatch.setenv(pytest_progress.PROGRESS_COMMAND_ENV, "command-1")

    pytest_progress.pytest_collection_finish(_Session(1))
    pytest_progress.pytest_runtest_logfinish("tests/test_module.py::test_case", None)
