import json
from pathlib import Path

from ei_ui_smoke.execution_guard import (
    RuntimeVersion,
    capture_runtime_version,
    clear_launcher_process_record,
    launcher_process_file,
    runtime_version_change_reason,
    runtime_version_mismatch_message,
)


def test_capture_runtime_version_uses_commit_and_source_fingerprint(tmp_path):
    source = tmp_path / "src" / "ei_ui_smoke" / "launcher.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    git_dir = tmp_path / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text(
        "a" * 40 + "\n", encoding="utf-8"
    )

    version = capture_runtime_version(tmp_path)

    assert version.commit == "a" * 40
    assert len(version.worktree_fingerprint) == 64


def test_capture_runtime_version_detects_loaded_source_edits_without_git(tmp_path):
    source = tmp_path / "src" / "ei_ui_smoke" / "launcher.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")

    before = capture_runtime_version(tmp_path)
    source.write_text("VALUE = 2\n", encoding="utf-8")
    after = capture_runtime_version(tmp_path)

    assert before.commit == ""
    assert before.worktree_fingerprint != after.worktree_fingerprint


def test_version_message_explains_an_uncommitted_runtime_source_change():
    started = RuntimeVersion(commit="a" * 40, worktree_fingerprint="before" * 11)
    current = RuntimeVersion(commit="a" * 40, worktree_fingerprint="after" * 13)

    assert runtime_version_change_reason(started, current) == (
        "自动化运行源码内容已变化（Git 提交号未变化，通常是本地未提交修改）"
    )
    message = runtime_version_mismatch_message(started, current)
    assert "启动 Git 提交：aaaaaaaaaaaa" in message
    assert "当前 Git 提交：aaaaaaaaaaaa" in message
    assert "运行源码指纹：beforebefore -> afterafteraf" in message


def test_launcher_script_excludes_tortoisesvn_runtime_from_child_path():
    script = (Path(__file__).resolve().parents[1] / "run_test.vbs").read_text(
        encoding="utf-8"
    ).lower()

    assert 'processenv("path") = withoutpathentry(' in script
    assert '"\\tortoisesvn\\bin"' in script


def test_clear_launcher_record_does_not_remove_a_newer_launcher_record(tmp_path):
    record = launcher_process_file(tmp_path)
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pid": 200}), encoding="utf-8")

    clear_launcher_process_record(tmp_path, pid=100)

    assert record.is_file()
