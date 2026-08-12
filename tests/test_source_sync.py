import subprocess

import pytest

from ei_ui_smoke.source_sync import (
    AUTO_PULL_SOURCE_ENV,
    AUTO_PULL_SOURCE_ROOT_ENV,
    SourceSyncError,
    discover_git_repositories,
    pull_latest_source,
)


def test_discovers_root_repository_before_child_repositories(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "child" / ".git").mkdir(parents=True)

    assert discover_git_repositories(tmp_path) == [tmp_path]


def test_discovers_direct_child_repositories_when_root_is_workspace(tmp_path):
    (tmp_path / "fi-parent" / ".git").mkdir(parents=True)
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)
    (tmp_path / "notes").mkdir()

    assert [path.name for path in discover_git_repositories(tmp_path)] == [
        "ei-parent",
        "fi-parent",
    ]


def test_pull_latest_source_runs_ff_only_pull_for_each_child_repo(tmp_path):
    (tmp_path / "fi-parent" / ".git").mkdir(parents=True)
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="Already up to date.\n")

    output = pull_latest_source(
        {AUTO_PULL_SOURCE_ROOT_ENV: str(tmp_path)},
        runner=runner,
    )

    assert [call[0] for call in calls] == [
        ["git", "-C", str(tmp_path / "ei-parent"), "pull", "--ff-only"],
        ["git", "-C", str(tmp_path / "fi-parent"), "pull", "--ff-only"],
    ]
    assert "SOURCE_SYNC_ROOT" in output
    assert output.count("SOURCE_SYNC_OK") == 2


def test_pull_latest_source_can_be_disabled(tmp_path):
    calls = []

    output = pull_latest_source(
        {
            AUTO_PULL_SOURCE_ENV: "false",
            AUTO_PULL_SOURCE_ROOT_ENV: str(tmp_path),
        },
        runner=lambda *_args, **_kwargs: calls.append("called"),
    )

    assert calls == []
    assert "SOURCE_SYNC_SKIPPED" in output


def test_pull_latest_source_failure_blocks_pytest_start(tmp_path):
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="fatal: conflict\n")

    with pytest.raises(SourceSyncError) as error:
        pull_latest_source(
            {AUTO_PULL_SOURCE_ROOT_ENV: str(tmp_path)},
            runner=runner,
        )

    message = str(error.value)
    assert "fatal: conflict" in message
    assert "SOURCE_SYNC_FAILED" in message


def test_pull_latest_source_reports_invalid_timeout_configuration(tmp_path):
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)

    with pytest.raises(SourceSyncError) as error:
        pull_latest_source(
            {
                AUTO_PULL_SOURCE_ROOT_ENV: str(tmp_path),
                "EI_AUTO_PULL_SOURCE_TIMEOUT_SECONDS": "abc",
            },
            runner=lambda *_args, **_kwargs: None,
        )

    assert "invalid-timeout" in str(error.value)
