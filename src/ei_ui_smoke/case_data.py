from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SAVE_BUTTON = "button:has-text('保存'),button:has-text('提交')"
DEFAULT_SUCCESS_TEXTS = ("保存成功", "新增成功", "提交成功")


@dataclass(frozen=True, slots=True)
class PageSpec:
    form_url: str
    save_button: str = DEFAULT_SAVE_BUTTON
    save_api_pattern: str = ""
    detail_url_template: str = ""
    success_texts: tuple[str, ...] = DEFAULT_SUCCESS_TEXTS


@dataclass(frozen=True, slots=True)
class SmokeCase:
    form_code: str
    page: PageSpec
    values: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)
    runtime_expected: dict[str, Any] = field(default_factory=dict)
    upload_files: dict[str, Path] = field(default_factory=dict)


def load_smoke_case(project_root: Path, form_code: str = "") -> SmokeCase:
    pages_path = project_root / "data" / "pages.json"
    if not pages_path.exists():
        raise FileNotFoundError(f"Central page configuration not found: {pages_path}")
    pages = json.loads(pages_path.read_text(encoding="utf-8-sig"))
    page_config = ((pages.get("forms") or {}).get(form_code) or {})
    if not page_config:
        raise KeyError(f"No page configuration for formCode={form_code} in {pages_path}")
    overrides_path = project_root / "data" / "overrides.json"
    overrides = json.loads(overrides_path.read_text(encoding="utf-8-sig")) if overrides_path.exists() else {}
    form_override = ((overrides.get("forms") or {}).get(form_code) or {})
    raw = {
        "formCode": form_code,
        "page": page_config,
        "values": form_override.get("values") or {},
        "expected": form_override.get("expected") or {},
        "runtimeExpected": form_override.get("runtimeExpected") or {},
        "uploadFiles": form_override.get("uploadFiles") or {},
    }
    path = overrides_path
    code = str(raw.get("formCode") or form_code).strip()
    if not code:
        raise ValueError(f"formCode is required in {path}")
    page_raw = raw.get("page") or {}
    form_url = str(os.getenv("EI_FORM_URL") or page_raw.get("formUrl") or "").strip()
    if not form_url:
        raise ValueError(f"page.formUrl or EI_FORM_URL is required in {path}")
    page = PageSpec(
        form_url=form_url,
        save_button=str(page_raw.get("saveButton") or DEFAULT_SAVE_BUTTON),
        save_api_pattern=str(page_raw.get("saveApiPattern") or ""),
        detail_url_template=str(page_raw.get("detailUrlTemplate") or ""),
        success_texts=tuple(page_raw.get("successTexts") or DEFAULT_SUCCESS_TEXTS),
    )
    values = dict(raw.get("values") or {})
    expected = dict(raw.get("expected") or {})
    runtime_expected = dict(raw.get("runtimeExpected") or {})
    uploads = {
        str(code): (path.parent / str(file_path)).resolve()
        for code, file_path in (raw.get("uploadFiles") or {}).items()
    }
    return SmokeCase(code, page, values, expected, runtime_expected, uploads)
