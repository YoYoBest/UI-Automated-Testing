"""Protect UI smoke runs from stale launcher processes and stale code."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


LAUNCHER_PROCESS_FILE = Path("artifacts") / "launcher-process.json"


@dataclass(frozen=True)
class RuntimeVersion:
    commit: str
    worktree_fingerprint: str


def launcher_process_file(project_root: Path) -> Path:
    return project_root / LAUNCHER_PROCESS_FILE


def capture_runtime_version(
    project_root: Path,
) -> RuntimeVersion:
    """Fingerprint the Python implementation loaded by the desktop launcher."""
    source_root = project_root / "src" / "ei_ui_smoke"
    try:
        source_files = sorted(
            path for path in source_root.rglob("*.py") if path.is_file()
        )
    except OSError as exc:
        raise RuntimeError(f"无法读取自动化运行代码：{exc}") from exc
    if not source_files:
        raise RuntimeError(f"无法读取自动化运行代码：{source_root} 中没有 Python 文件")

    digest = hashlib.sha256()
    try:
        for source_file in source_files:
            relative_path = source_file.relative_to(project_root).as_posix()
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(source_file.read_bytes())
            digest.update(b"\0")
    except OSError as exc:
        raise RuntimeError(f"无法读取自动化运行代码：{exc}") from exc

    commit = _read_repository_commit(project_root)
    fingerprint = digest.hexdigest()
    return RuntimeVersion(commit=commit, worktree_fingerprint=fingerprint)


def _read_repository_commit(project_root: Path) -> str:
    """Read Git HEAD without starting Git or loading user-level Git configuration."""
    git_entry = project_root / ".git"
    try:
        if git_entry.is_file():
            pointer = git_entry.read_text(encoding="utf-8", errors="replace").strip()
            if not pointer.lower().startswith("gitdir:"):
                return ""
            git_dir = Path(pointer.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = (project_root / git_dir).resolve()
        else:
            git_dir = git_entry
        head = (git_dir / "HEAD").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
        if not head.startswith("ref:"):
            return head
        reference = head.split(":", 1)[1].strip()
        loose_reference = git_dir / Path(reference)
        if loose_reference.is_file():
            return loose_reference.read_text(
                encoding="utf-8", errors="replace"
            ).strip()
        packed_refs = git_dir / "packed-refs"
        if packed_refs.is_file():
            for line in packed_refs.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line and not line.startswith(("#", "^")):
                    revision, _, name = line.partition(" ")
                    if name == reference:
                        return revision
    except OSError:
        pass
    return ""


def runtime_version_changed(started: RuntimeVersion, current: RuntimeVersion) -> bool:
    return started != current


def runtime_version_change_reason(
    started: RuntimeVersion, current: RuntimeVersion,
) -> str:
    """Describe which immutable runtime identity changed after launcher startup."""
    commit_changed = started.commit != current.commit
    source_changed = started.worktree_fingerprint != current.worktree_fingerprint
    if commit_changed and source_changed:
        return "Git 提交和自动化运行源码内容均已变化"
    if commit_changed:
        return "Git 提交已变化"
    if source_changed:
        return "自动化运行源码内容已变化（Git 提交号未变化，通常是本地未提交修改）"
    return "自动化运行代码版本未变化"


def runtime_version_mismatch_message(
    started: RuntimeVersion, current: RuntimeVersion,
) -> str:
    started_commit = started.commit[:12] or "不可用"
    current_commit = current.commit[:12] or "不可用"
    return (
        "自动化代码版本已在启动后发生变化，已阻止本次执行。\n\n"
        f"变化原因：{runtime_version_change_reason(started, current)}\n"
        f"启动 Git 提交：{started_commit}\n"
        f"当前 Git 提交：{current_commit}\n"
        "运行源码指纹："
        f"{started.worktree_fingerprint[:12]} -> "
        f"{current.worktree_fingerprint[:12]}\n\n"
        "请关闭启动器并重新运行 run_test.vbs，再重新执行测试。"
    )


def _windows_process_creation_time(pid: int) -> int | None:
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel_time = ctypes.c_ulonglong()
        user_time = ctypes.c_ulonglong()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel_time),
            ctypes.byref(user_time),
        ):
            return None
        return int(creation.value)
    finally:
        kernel32.CloseHandle(handle)


def register_launcher_process(project_root: Path, *, pid: int | None = None) -> Path:
    """Record only this launcher's PID so later starts can clean its process tree."""
    process_id = int(pid or os.getpid())
    record = {
        "pid": process_id,
        "creation_time": _windows_process_creation_time(process_id),
    }
    path = launcher_process_file(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=True), encoding="utf-8")
    return path


def clear_launcher_process_record(project_root: Path, *, pid: int | None = None) -> None:
    path = launcher_process_file(project_root)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if pid is not None and record.get("pid") != int(pid):
        return
    path.unlink(missing_ok=True)


def cleanup_registered_launcher(project_root: Path) -> bool:
    """End the previous launcher and only its child-process tree when it is still the recorded process."""
    path = launcher_process_file(project_root)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        pid = int(record["pid"])
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        path.unlink(missing_ok=True)
        return False

    recorded_time = record.get("creation_time")
    current_time = _windows_process_creation_time(pid)
    if current_time is None or (recorded_time is not None and current_time != recorded_time):
        path.unlink(missing_ok=True)
        return False
    try:
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode == 0:
        path.unlink(missing_ok=True)
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["cleanup"]:
        cleanup_registered_launcher(Path.cwd())
        return 0
    raise SystemExit("usage: python -m ei_ui_smoke.execution_guard cleanup")


if __name__ == "__main__":
    raise SystemExit(main())
