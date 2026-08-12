from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


DEFER_GATE_ENV = "EI_DEFER_SKILL_MAINTENANCE_GATE"


def skill_maintenance_gate_deferred() -> bool:
    return os.getenv(DEFER_GATE_ENV, "").strip().lower() == "true"


def pytest_sessionstart(session: pytest.Session) -> None:
    if skill_maintenance_gate_deferred():
        return
    root = Path(__file__).resolve().parent
    script = root / "skills/skill-maintenance-gate/scripts/skill_gate.py"
    spec = importlib.util.spec_from_file_location("skill_maintenance_gate", script)
    if spec is None or spec.loader is None:
        raise pytest.UsageError(f"cannot load Skill maintenance gate: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        module.check(root)
    except module.GateError as exc:
        raise pytest.UsageError(str(exc)) from exc
