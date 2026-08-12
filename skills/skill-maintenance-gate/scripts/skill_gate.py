from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


STATE_FILE = ".skill-maintenance-state.json"
AUDIT_FILE = ".skill-maintenance-audit.jsonl"
CONFIG_FILE = "skills/skill-maintenance-gate/references/ownership.json"
CLEANUP_FILE = "skills/skill-maintenance-gate/references/obsolete-methods.json"


class GateError(RuntimeError):
    pass


def _root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_FILE).is_file():
            return candidate
    raise GateError(f"repository root not found from {current}")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _matches(path: str, pattern: str) -> bool:
    path = path.replace("\\", "/")
    return fnmatch.fnmatchcase(path, pattern) or (
        "**/" in pattern and fnmatch.fnmatchcase(path, pattern.replace("**/", ""))
    )


def _governed_files(root: Path, config: dict) -> list[Path]:
    files: set[Path] = set()
    for pattern in config["governed_patterns"]:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(
        path for path in files
        if not any(_matches(path.relative_to(root).as_posix(), item) for item in config["ignored_patterns"])
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root: Path, config: dict) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): _digest(path) for path in _governed_files(root, config)}


def _skill_hashes(root: Path) -> dict[str, str]:
    return {
        path.parent.name: _digest(path)
        for path in sorted((root / "skills").glob("*/SKILL.md"))
    }


def _owners(config: dict, path: str) -> list[str]:
    for rule in config["rules"]:
        if _matches(path, rule["pattern"]):
            return list(rule["owners"])
    return [config["default_owner"]]


def _changes(before: dict[str, str], after: dict[str, str]) -> dict[str, dict[str, str | None]]:
    return {
        path: {"before": before.get(path), "after": after.get(path)}
        for path in sorted(set(before) | set(after))
        if before.get(path) != after.get(path)
    }


def _validate_skills(root: Path) -> None:
    for skill_file in sorted((root / "skills").glob("*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            raise GateError(f"invalid Skill frontmatter: {skill_file.relative_to(root)}")
        end = text.find("\n---", 4)
        frontmatter = text[4:end] if end >= 0 else ""
        name = next((line.split(":", 1)[1].strip() for line in frontmatter.splitlines() if line.startswith("name:")), "")
        description = next((line.split(":", 1)[1].strip() for line in frontmatter.splitlines() if line.startswith("description:")), "")
        if name != skill_file.parent.name or not description:
            raise GateError(f"invalid Skill name/description: {skill_file.relative_to(root)}")


def _cleanup_skill(skill_file: Path, patterns: list[str]) -> dict[str, int]:
    lines = skill_file.read_text(encoding="utf-8").splitlines(keepends=True)
    compiled = [re.compile(pattern) for pattern in patterns]
    seen_bullets: set[str] = set()
    cleaned: list[str] = []
    removed_obsolete = 0
    removed_duplicates = 0
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        stripped = line.strip()
        if not in_fence and stripped.startswith(("- ", "* ")):
            if any(pattern.search(stripped) for pattern in compiled):
                removed_obsolete += 1
                continue
            normalized = re.sub(r"\s+", " ", stripped)
            if normalized in seen_bullets:
                removed_duplicates += 1
                continue
            seen_bullets.add(normalized)
        cleaned.append(line)
    if removed_obsolete or removed_duplicates:
        skill_file.write_text("".join(cleaned), encoding="utf-8")
    return {"obsolete": removed_obsolete, "duplicates": removed_duplicates}


def _auto_cleanup_skills(root: Path, selected: set[str]) -> dict[str, dict[str, int]]:
    cleanup_path = root / CLEANUP_FILE
    config = _load_json(cleanup_path) if cleanup_path.is_file() else {}
    patterns = list(config.get("obsolete_method_patterns", []))
    results = {}
    for name in sorted(selected):
        skill_file = root / "skills" / name / "SKILL.md"
        if skill_file.is_file():
            result = _cleanup_skill(skill_file, patterns)
            if result["obsolete"] or result["duplicates"]:
                results[name] = result
    return results


def _write_state(root: Path, files: dict[str, str], skills: dict[str, str]) -> None:
    payload = {
        "version": 1,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "skills": skills,
    }
    (root / STATE_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_audit(root: Path, payload: dict) -> None:
    with (root / AUDIT_FILE).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def pending(root: Path) -> tuple[dict, dict[str, str], dict[str, str], dict]:
    config = _load_json(root / CONFIG_FILE)
    state_path = root / STATE_FILE
    if not state_path.is_file():
        raise GateError(f"missing {STATE_FILE}; initialize with bootstrap")
    state = _load_json(state_path)
    files = _snapshot(root, config)
    skills = _skill_hashes(root)
    return state, files, skills, _changes(state.get("files", {}), files)


def check(root: Path) -> None:
    _validate_skills(root)
    _, _, _, changed = pending(root)
    if not changed:
        print("SKILL_MAINTENANCE_GATE=PASS")
        return
    lines = ["Skill maintenance decision required:"]
    config = _load_json(root / CONFIG_FILE)
    lines.extend(f"- {path} -> {', '.join(_owners(config, path))}" for path in changed)
    lines.append("Update the owner Skill and run record --skill <name>, or record --no-skill-reason <reason>.")
    raise GateError("\n".join(lines))


def record(root: Path, selected: Iterable[str], reason: str) -> None:
    selected_set = set(selected)
    cleanup = _auto_cleanup_skills(root, selected_set) if not reason else {}
    _validate_skills(root)
    state, files, skills, changed = pending(root)
    if not changed:
        raise GateError("no governed file changes to record")
    config = _load_json(root / CONFIG_FILE)
    if reason:
        if len(reason.strip()) < 12:
            raise GateError("--no-skill-reason must contain at least 12 meaningful characters")
        decision = {"type": "no_skill", "reason": reason.strip()}
    else:
        if not selected_set:
            raise GateError("provide --skill or --no-skill-reason")
        unknown = selected_set - set(skills)
        if unknown:
            raise GateError(f"unknown Skills: {', '.join(sorted(unknown))}")
        unchanged = {name for name in selected_set if state.get("skills", {}).get(name) == skills.get(name)}
        if unchanged:
            raise GateError(f"Skill files were not updated: {', '.join(sorted(unchanged))}")
        uncovered = {
            path: _owners(config, path)
            for path in changed
            if not selected_set.intersection(_owners(config, path))
        }
        if uncovered:
            details = "; ".join(f"{path} -> {','.join(owners)}" for path, owners in uncovered.items())
            raise GateError(f"selected Skills do not own all changed files: {details}")
        decision = {"type": "skill_update", "skills": sorted(selected_set)}
    _append_audit(root, {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "decision": decision,
        "changes": changed,
        "automatic_skill_cleanup": cleanup,
    })
    _write_state(root, files, skills)
    print(f"SKILL_MAINTENANCE_RECORDED={decision['type']}")
    for name, counts in cleanup.items():
        print(
            f"SKILL_AUTO_CLEANUP={name}:obsolete={counts['obsolete']},"
            f"duplicates={counts['duplicates']}"
        )


def bootstrap(root: Path) -> None:
    if (root / STATE_FILE).exists():
        raise GateError(f"{STATE_FILE} already exists")
    config = _load_json(root / CONFIG_FILE)
    _validate_skills(root)
    _write_state(root, _snapshot(root, config), _skill_hashes(root))
    _append_audit(root, {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "decision": {"type": "bootstrap"},
        "changes": {},
    })
    print("SKILL_MAINTENANCE_BOOTSTRAPPED=1")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce auditable Skill maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check")
    subparsers.add_parser("bootstrap")
    recorder = subparsers.add_parser("record")
    recorder.add_argument("--skill", action="append", default=[])
    recorder.add_argument("--no-skill-reason", default="")
    args = parser.parse_args(argv)
    try:
        root = _root()
        if args.command == "check":
            check(root)
        elif args.command == "bootstrap":
            bootstrap(root)
        else:
            record(root, args.skill, args.no_skill_reason)
    except (GateError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SKILL_MAINTENANCE_GATE=FAIL\n{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
