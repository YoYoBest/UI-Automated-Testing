from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .project_layout import resolve_view_root


FORM_CODE_PATTERNS = (
    (re.compile(r"\bFORM_CODE(?:\s*:\s*[A-Za-z_$][\w.$<>\[\]| ]*)?\s*=\s*['\"]([^'\"]+)['\"]"), 8),
    (re.compile(r"\bformCode(?:\s*:\s*[A-Za-z_$][\w.$<>\[\]| ]*)?\s*=\s*['\"]([^'\"]+)['\"]"), 7),
    (re.compile(r"\bformCode\s*:\s*['\"]([^'\"]+)['\"]"), 5),
    (re.compile(r"['\"]formCode['\"]\s*:\s*['\"]([^'\"]+)['\"]"), 5),
    (re.compile(r"\bform-code\s*=\s*['\"]([^'\"]+)['\"]"), 3),
)


class ModuleResolutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FormCodeCandidate:
    form_code: str
    score: int
    files: tuple[str, ...]


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def discover_form_codes(source_root: Path, module_name: str) -> list[FormCodeCandidate]:
    try:
        view_root = resolve_view_root(source_root)
    except ValueError as exc:
        raise ModuleResolutionError(str(exc)) from exc
    target = _normalize(module_name)
    if not target:
        raise ModuleResolutionError("Module name is required")

    scores: dict[str, int] = defaultdict(int)
    files: dict[str, set[str]] = defaultdict(set)
    roots = [path for path in (view_root / "src", view_root / "srcEi") if path.exists()]
    for root in roots:
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".vue", ".ts", ".js", ".json"} or not path.is_file():
                continue
            if any(part in {"node_modules", "dist"} for part in path.parts):
                continue
            try:
                if path.stat().st_size > 2_000_000:
                    continue
                text = path.read_text(encoding="utf-8-sig", errors="ignore")
            except OSError:
                continue
            path_match = target in _normalize(path.stem) or target in _normalize(str(path.parent.name))
            text_match = target in _normalize(text)
            if not path_match and not text_match:
                continue
            location_bonus = 4 if path_match else 0
            text_bonus = 2 if text_match else 0
            for pattern, weight in FORM_CODE_PATTERNS:
                for match in pattern.findall(text):
                    code = str(match).strip()
                    if not code or "${" in code:
                        continue
                    scores[code] += weight + location_bonus + text_bonus
                    files[code].add(str(path.relative_to(view_root)))

    candidates = [
        FormCodeCandidate(code, score, tuple(sorted(files[code])))
        for code, score in scores.items()
    ]
    return sorted(candidates, key=lambda item: (-item.score, item.form_code))


def resolve_form_code(source_root: Path, module_name: str) -> str:
    candidates = discover_form_codes(source_root, module_name)
    if not candidates:
        raise ModuleResolutionError(
            f"No literal FORM_CODE/formCode found for module {module_name!r} under the selected *-view source"
        )
    exact = [item for item in candidates if item.form_code.lower() == module_name.strip().lower()]
    if len(exact) == 1:
        return exact[0].form_code
    if len(candidates) == 1:
        return candidates[0].form_code
    if candidates[0].score > candidates[1].score:
        return candidates[0].form_code
    details = "; ".join(
        f"{item.form_code} (score={item.score}, files={','.join(item.files[:2])})"
        for item in candidates[:10]
    )
    raise ModuleResolutionError(f"Ambiguous module {module_name!r}; candidates: {details}")
