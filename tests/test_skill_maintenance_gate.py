from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/skill-maintenance-gate/scripts/skill_gate.py"
SPEC = importlib.util.spec_from_file_location("gate_under_test", SCRIPT)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

ROOT_CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"
CONFTEST_SPEC = importlib.util.spec_from_file_location("root_conftest_under_test", ROOT_CONFTEST)
assert CONFTEST_SPEC and CONFTEST_SPEC.loader
root_conftest = importlib.util.module_from_spec(CONFTEST_SPEC)
CONFTEST_SPEC.loader.exec_module(root_conftest)


def make_repo(tmp_path: Path) -> Path:
    root = tmp_path
    (root / "skills/skill-maintenance-gate/references").mkdir(parents=True)
    (root / "skills/skill-maintenance-gate/SKILL.md").write_text(
        "---\nname: skill-maintenance-gate\ndescription: Test gate.\n---\n", encoding="utf-8"
    )
    (root / "skills/ui-smoke-test").mkdir(parents=True)
    (root / "skills/ui-smoke-test/SKILL.md").write_text(
        "---\nname: ui-smoke-test\ndescription: Test owner.\n---\n", encoding="utf-8"
    )
    config = {
        "version": 1,
        "default_owner": "ui-smoke-test",
        "rules": [{"pattern": "src/**/*.py", "owners": ["ui-smoke-test"]}],
        "governed_patterns": ["src/**/*.py"],
        "ignored_patterns": [],
    }
    (root / gate.CONFIG_FILE).write_text(json.dumps(config), encoding="utf-8")
    (root / gate.CLEANUP_FILE).write_text(
        json.dumps({"obsolete_method_patterns": ["obsolete method"]}), encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src/example.py").write_text("value = 1\n", encoding="utf-8")
    gate.bootstrap(root)
    return root


def test_check_blocks_changed_code_until_owner_skill_is_updated(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "src/example.py").write_text("value = 2\n", encoding="utf-8")
    with pytest.raises(gate.GateError, match="ui-smoke-test"):
        gate.check(root)
    with pytest.raises(gate.GateError, match="were not updated"):
        gate.record(root, ["ui-smoke-test"], "")
    skill = root / "skills/ui-smoke-test/SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nUpdated rule.\n", encoding="utf-8")
    gate.record(root, ["ui-smoke-test"], "")
    gate.check(root)


def test_launcher_can_defer_session_gate_until_operations_finish(monkeypatch) -> None:
    monkeypatch.setenv(root_conftest.DEFER_GATE_ENV, "true")
    assert root_conftest.skill_maintenance_gate_deferred()
    monkeypatch.delenv(root_conftest.DEFER_GATE_ENV)
    assert not root_conftest.skill_maintenance_gate_deferred()


def test_no_skill_decision_requires_meaningful_audited_reason(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "src/example.py").write_text("value = 3\n", encoding="utf-8")
    with pytest.raises(gate.GateError, match="at least 12"):
        gate.record(root, [], "not needed")
    gate.record(root, [], "One-off fixture value; no reusable behavior changed.")
    gate.check(root)
    audit = (root / gate.AUDIT_FILE).read_text(encoding="utf-8").splitlines()
    assert json.loads(audit[-1])["decision"]["type"] == "no_skill"


def test_record_rejects_skill_that_does_not_own_changed_file(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    (root / "skills/other").mkdir()
    (root / "skills/other/SKILL.md").write_text(
        "---\nname: other\ndescription: Another Skill.\n---\nChanged.\n", encoding="utf-8"
    )
    (root / "src/example.py").write_text("value = 4\n", encoding="utf-8")
    with pytest.raises(gate.GateError, match="do not own"):
        gate.record(root, ["other"], "")


def test_record_automatically_removes_obsolete_and_duplicate_skill_methods(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    skill = root / "skills/ui-smoke-test/SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8")
        + "\n- Keep valid method.\n- Keep valid method.\n- Remove obsolete method now.\n",
        encoding="utf-8",
    )
    (root / "src/example.py").write_text("value = 5\n", encoding="utf-8")

    gate.record(root, ["ui-smoke-test"], "")

    text = skill.read_text(encoding="utf-8")
    assert text.count("- Keep valid method.") == 1
    assert "obsolete method" not in text
    audit = json.loads((root / gate.AUDIT_FILE).read_text(encoding="utf-8").splitlines()[-1])
    assert audit["automatic_skill_cleanup"]["ui-smoke-test"] == {
        "obsolete": 1,
        "duplicates": 1,
    }
