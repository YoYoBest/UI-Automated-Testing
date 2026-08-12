from __future__ import annotations

import json
import os
from pathlib import Path


PROGRESS_FILE_ENV = "EI_PYTEST_PROGRESS_FILE"
PROGRESS_COMMAND_ENV = "EI_PYTEST_PROGRESS_COMMAND_ID"
LOGICAL_COLLECTED_EVENT = "logical_collected"
LOGICAL_FINISHED_EVENT = "logical_finished"


def _emit_progress_event(event: str, **values) -> None:
    progress_file = os.getenv(PROGRESS_FILE_ENV, "").strip()
    if not progress_file:
        return
    payload = {
        "event": event,
        "command_id": os.getenv(PROGRESS_COMMAND_ENV, "").strip(),
        **values,
    }
    try:
        path = Path(progress_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        # Progress reporting must never change the pytest result.
        return


def pytest_collection_finish(session) -> None:
    _emit_progress_event("collected", count=len(session.items))


def pytest_runtest_logfinish(nodeid: str, location) -> None:
    _emit_progress_event("finished", nodeid=nodeid)


def emit_logical_progress_total(count: int, *, transaction_count: int = 0) -> None:
    """Replace one physical pytest item with its Batch logical work total."""
    _emit_progress_event(
        LOGICAL_COLLECTED_EVENT,
        count=max(0, int(count)),
        transaction_count=max(0, int(transaction_count)),
    )


def emit_logical_progress_finished(nodeid: str, *, outcome: str = "") -> None:
    """Resolve one logical Batch item without creating another pytest process."""
    normalized = str(nodeid or "").strip()
    if not normalized:
        return
    _emit_progress_event(
        LOGICAL_FINISHED_EVENT,
        nodeid=normalized,
        outcome=str(outcome or "").strip(),
    )
