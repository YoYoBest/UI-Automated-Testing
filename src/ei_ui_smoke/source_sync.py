from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Mapping


SOURCE_READONLY_ROOT_ENV = "EI_SOURCE_READONLY_ROOT"
LEGACY_SOURCE_ROOT_ENV = "EI_AUTO_PULL_SOURCE_ROOT"
DEFAULT_SOURCE_READONLY_ROOT = Path(r"D:\Auto_Testing\Project_Purvar\SHZY")


class BusinessSourceReadOnlyError(RuntimeError):
    """Raised when a business source repository is not safe to treat as read-only."""


def configured_business_source_root(
    environment: Mapping[str, str] | None = None,
) -> Path:
    env = environment or os.environ
    configured = str(env.get(SOURCE_READONLY_ROOT_ENV, "")).strip()
    if not configured:
        configured = str(env.get(LEGACY_SOURCE_ROOT_ENV, "")).strip()
    return Path(configured) if configured else DEFAULT_SOURCE_READONLY_ROOT


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


def verify_business_sources_readonly(
    environment: Mapping[str, str] | None = None,
    *,
    runner=subprocess.run,
) -> str:
    """Block execution unless every configured business source repo is clean.

    This function intentionally invokes only the read-only Git status command. It
    must never fetch, pull, restore, reset, clean, or otherwise mutate source.
    """
    root = configured_business_source_root(environment)
    if not root.exists():
        raise BusinessSourceReadOnlyError(
            f"BUSINESS_SOURCE_READONLY_FAILED root={root} reason=path-not-found"
        )

    repositories = discover_git_repositories(root)
    if not repositories:
        raise BusinessSourceReadOnlyError(
            f"BUSINESS_SOURCE_READONLY_FAILED root={root} reason=no-git-repository"
        )

    outputs = [
        f"BUSINESS_SOURCE_READONLY_ROOT root={root} repositories={len(repositories)}"
    ]
    for repository in repositories:
        command = [
            "git",
            "-C",
            str(repository),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ]
        try:
            completed = runner(
                command,
                cwd=repository,
                text=True,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
            )
        except subprocess.TimeoutExpired as exc:
            raise BusinessSourceReadOnlyError(
                f"BUSINESS_SOURCE_READONLY_FAILED repo={repository} reason=timeout"
            ) from exc
        except OSError as exc:
            raise BusinessSourceReadOnlyError(
                f"BUSINESS_SOURCE_READONLY_FAILED repo={repository} reason={exc}"
            ) from exc

        if completed.returncode:
            error = _sanitize_git_output(completed.stderr or completed.stdout or "").strip()
            detail = f" detail={error}" if error else ""
            raise BusinessSourceReadOnlyError(
                f"BUSINESS_SOURCE_READONLY_FAILED repo={repository} "
                f"reason=git-status-returncode returncode={completed.returncode}{detail}"
            )
        if (completed.stdout or "").strip():
            raise BusinessSourceReadOnlyError(
                f"BUSINESS_SOURCE_READONLY_FAILED repo={repository} reason=dirty-worktree"
            )
        outputs.append(f"BUSINESS_SOURCE_READONLY_OK repo={repository}")
    return "\n".join(outputs)
