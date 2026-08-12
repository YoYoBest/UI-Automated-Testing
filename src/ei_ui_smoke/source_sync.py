from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Mapping


AUTO_PULL_SOURCE_ENV = "EI_AUTO_PULL_SOURCE"
AUTO_PULL_SOURCE_ROOT_ENV = "EI_AUTO_PULL_SOURCE_ROOT"
AUTO_PULL_SOURCE_TIMEOUT_ENV = "EI_AUTO_PULL_SOURCE_TIMEOUT_SECONDS"
DEFAULT_AUTO_PULL_SOURCE_ROOT = Path(r"D:\Auto_Testing\Project_Purvar\SHZY")
_DISABLED_VALUES = {"0", "false", "no", "off", "disable", "disabled"}


class SourceSyncError(RuntimeError):
    """Raised when the configured business source repositories cannot be synced."""


def auto_pull_source_enabled(environment: Mapping[str, str] | None = None) -> bool:
    env = environment or os.environ
    value = str(env.get(AUTO_PULL_SOURCE_ENV, "")).strip().lower()
    return value not in _DISABLED_VALUES


def configured_source_sync_root(environment: Mapping[str, str] | None = None) -> Path:
    env = environment or os.environ
    configured = str(env.get(AUTO_PULL_SOURCE_ROOT_ENV, "")).strip()
    return Path(configured) if configured else DEFAULT_AUTO_PULL_SOURCE_ROOT


def discover_git_repositories(root: Path) -> list[Path]:
    """Return root itself when it is a repo, otherwise direct child repos."""
    if (root / ".git").exists():
        return [root]
    try:
        children = list(root.iterdir())
    except OSError:
        return []
    return sorted(
        (child for child in children if child.is_dir() and (child / ".git").exists()),
        key=lambda path: path.name.lower(),
    )


def _sanitize_git_output(output: str) -> str:
    return re.sub(r"(https?://)([^/\s:@]+):([^@\s/]+)@", r"\1***:***@", output)


def pull_latest_source(
    environment: Mapping[str, str] | None = None,
    *,
    runner=subprocess.run,
) -> str:
    """Pull the configured deployed-source repositories before a UI pytest run."""
    env = environment or os.environ
    if not auto_pull_source_enabled(env):
        root = configured_source_sync_root(env)
        return f"SOURCE_SYNC_SKIPPED root={root} reason=disabled"

    root = configured_source_sync_root(env)
    if not root.exists():
        raise SourceSyncError(f"SOURCE_SYNC_FAILED root={root} reason=path-not-found")

    repositories = discover_git_repositories(root)
    if not repositories:
        raise SourceSyncError(f"SOURCE_SYNC_FAILED root={root} reason=no-git-repository")

    raw_timeout = str(env.get(AUTO_PULL_SOURCE_TIMEOUT_ENV, "120")).strip() or "120"
    try:
        timeout_seconds = int(raw_timeout)
    except ValueError as exc:
        raise SourceSyncError(
            f"SOURCE_SYNC_FAILED root={root} reason=invalid-timeout value={raw_timeout}"
        ) from exc
    outputs = [f"SOURCE_SYNC_ROOT root={root} repositories={len(repositories)}"]
    for repository in repositories:
        command = ["git", "-C", str(repository), "pull", "--ff-only"]
        outputs.append(f"SOURCE_SYNC repo={repository} command=git pull --ff-only")
        try:
            completed = runner(
                command,
                cwd=repository,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout_seconds,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise SourceSyncError(
                f"SOURCE_SYNC_FAILED repo={repository} reason=timeout seconds={timeout_seconds}"
            ) from exc
        except OSError as exc:
            raise SourceSyncError(
                f"SOURCE_SYNC_FAILED repo={repository} reason={exc}"
            ) from exc

        output = _sanitize_git_output(completed.stdout or "").strip()
        if output:
            outputs.append(output)
        if completed.returncode:
            outputs.append(
                f"SOURCE_SYNC_FAILED repo={repository} returncode={completed.returncode}"
            )
            raise SourceSyncError("\n".join(outputs))
        outputs.append(f"SOURCE_SYNC_OK repo={repository}")
    return "\n".join(outputs)
