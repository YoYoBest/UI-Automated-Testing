# Project Instructions

## Skill Usage

- Before starting a task, inspect `skills/` and identify all skills relevant to the request.
- Read each relevant `SKILL.md` completely before acting, then follow its workflow and referenced resources.
- Prefer established project skills, scripts, conventions, and reusable helpers over creating parallel implementations.
- If no skill applies, proceed using the repository's existing patterns. Do not force unrelated guidance into a skill.

## Automatic Skill Maintenance

This policy is enforced by `skills/skill-maintenance-gate/scripts/skill_gate.py` at pytest session start. A governed code or tooling change must be recorded with an updated owner Skill or a meaningful audited no-Skill reason before tests can run. Never bypass or bootstrap the gate to approve normal work.

After completing and verifying a task, evaluate whether the work produced reusable knowledge. Reusable knowledge includes a confirmed workflow, troubleshooting procedure, business rule, stable implementation pattern, command sequence, or helper that will materially improve similar future tasks.

When reusable knowledge was produced:

1. Update the single most relevant `skills/<skill-name>/SKILL.md` without waiting for a separate user request.
2. If the details are lengthy, place them in that skill's `references/` directory and add a direct, clearly routed reference from `SKILL.md`.
3. If deterministic code would otherwise be rewritten, place it in that skill's `scripts/` directory and verify it by running a representative test.
4. Search the target skill first. Merge with or correct existing guidance instead of appending duplicate or conflicting instructions.
5. Record only methods confirmed by code inspection, execution, tests, or other concrete evidence. Do not record guesses, temporary symptoms, one-off task data, credentials, environment-specific secrets, or conversation history.
6. Keep `SKILL.md` concise and procedural. Preserve valid YAML frontmatter and existing ownership boundaries.
7. Validate the affected skill after updating it. Fix validation failures before considering the task complete.
8. Summarize any automatic skill update in the final response, including which skill changed and what reusable knowledge was added.

Do not update a skill when the result is specific to one incident and has no likely reuse. If ownership is genuinely ambiguous or the change would alter a skill's scope materially, explain the ambiguity and ask the user before writing it.
