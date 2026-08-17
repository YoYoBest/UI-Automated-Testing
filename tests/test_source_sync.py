import subprocess

import pytest

from ei_ui_smoke.source_sync import (
    BusinessSourceReadOnlyError,
    SOURCE_READONLY_ROOT_ENV,
    discover_git_repositories,
    verify_business_sources_readonly,
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


def test_readonly_guard_runs_only_git_status_for_each_child_repo(tmp_path):
    (tmp_path / "fi-parent" / ".git").mkdir(parents=True)
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="")

    output = verify_business_sources_readonly(
        {SOURCE_READONLY_ROOT_ENV: str(tmp_path)},
        runner=runner,
    )

    assert [call[0] for call in calls] == [
        [
            "git", "-C", str(tmp_path / "ei-parent"), "status", "--porcelain=v1",
            "--untracked-files=all",
        ],
        [
            "git", "-C", str(tmp_path / "fi-parent"), "status", "--porcelain=v1",
            "--untracked-files=all",
        ],
    ]
    assert "BUSINESS_SOURCE_READONLY_ROOT" in output
    assert output.count("BUSINESS_SOURCE_READONLY_OK") == 2


def test_readonly_guard_blocks_dirty_worktree(tmp_path):
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=" M source.java\n")

    with pytest.raises(BusinessSourceReadOnlyError) as error:
        verify_business_sources_readonly(
            {SOURCE_READONLY_ROOT_ENV: str(tmp_path)},
            runner=runner,
        )

    message = str(error.value)
    assert "dirty-worktree" in message
    assert "ei-parent" in message


def test_readonly_guard_blocks_a_failed_git_status(tmp_path):
    (tmp_path / "ei-parent" / ".git").mkdir(parents=True)

    def runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="fatal: not a repository\n"
        )

    with pytest.raises(BusinessSourceReadOnlyError, match="git-status-returncode"):
        verify_business_sources_readonly(
            {SOURCE_READONLY_ROOT_ENV: str(tmp_path)},
            runner=runner,
        )
