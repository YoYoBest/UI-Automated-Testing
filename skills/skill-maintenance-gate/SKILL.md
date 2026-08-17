---
name: skill-maintenance-gate
description: Enforce auditable Skill maintenance for repository changes. Use after changing implementation, tests, launchers, tooling, or project instructions; when pytest reports pending Skill maintenance; or when recording that a verified change does not produce reusable knowledge.
---

# Skill Maintenance Gate

Run all commands from the repository root.

## Required workflow

1. Make and verify the implementation change.
2. Run `python skills/skill-maintenance-gate/scripts/skill_gate.py check`.
3. Inspect every changed governed file and the owner reported by the command.
4. When reusable knowledge exists, update each applicable owner Skill and run:

   `python skills/skill-maintenance-gate/scripts/skill_gate.py record --skill <owner>`

   Repeat `--skill` when the change spans multiple ownership areas.
   During `record`, the gate automatically removes bullet methods matching
   `references/obsolete-methods.json` and exact duplicate bullet methods from each selected
   owner Skill. Confirmed bad methods must be added to that registry; semantic guessing is
   forbidden. Cleanup counts are written to the audit entry and printed as
   `SKILL_AUTO_CLEANUP`.
   Keep each obsolete-method pattern narrow enough to match the disproved method without
   deleting a corrected replacement that uses different evidence.
5. When the change is incident-specific and produces no reusable knowledge, record the decision with a concrete reason:

   `python skills/skill-maintenance-gate/scripts/skill_gate.py record --no-skill-reason "<reason>"`

6. Run pytest. The root `conftest.py` blocks collection when governed files differ from the recorded baseline.

Never use `bootstrap` to approve a normal change. It exists only to initialize a repository with no prior state. Never use a generic reason such as `not needed`; name why the result is one-off, already covered, environmental, or unverified.

## Enforcement contract

- File ownership and governed paths are defined in `references/ownership.json`; first matching rule wins.
- Map module-specific or personalized tests by the shared behavior they exercise, not by their filename scope: tests of add-form fields, controls, and persistence belong to `generic-module-crud-smoke`, while process orchestration remains in `ui-smoke-test`.
- A `--skill` decision is accepted only when that Skill belongs to every affected file and its `SKILL.md` changed since the preceding baseline.
- A no-Skill decision requires a meaningful reason and is appended to `.skill-maintenance-audit.jsonl` with old/new hashes.
- Every accepted decision refreshes `.skill-maintenance-state.json`; later changes cannot reuse an earlier Skill edit.
- `check` validates all Skill folders in UTF-8 mode before accepting the baseline.
- The pytest hook has no environment-variable bypass. Resolve every failure through an owner Skill update or an audited no-Skill decision.
- The root pytest session hook must enforce the normal gate before direct test collection. The desktop launcher may set `EI_DEFER_SKILL_MAINTENANCE_GATE=true` only for the subprocesses of one active operation batch; those subprocesses defer the session-start check so running operations are not interrupted, and the launcher must run the normal gate once after every operation finishes. Direct pytest runs and all other callers remain guarded at session start.
- The root pytest session hook must also enforce the shared business-source read-only guard before test collection. The launcher runs that same guard before starting its deferred subprocesses. The guard may use only `git status --porcelain=v1 --untracked-files=all`; a missing, inaccessible, or dirty business source repository blocks execution and must never be repaired by automated Git write operations.

When changing ownership rules or the gate implementation, update this Skill and cover the behavior in `tests/test_skill_maintenance_gate.py`.
