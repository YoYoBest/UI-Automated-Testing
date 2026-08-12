from __future__ import annotations

import fnmatch
import json
from typing import Any, Iterable

from .contracts import parse_json_object, runtime_fields
from .models import FieldDefinition, ResolvedField


BUSINESS_ID_KEYS = (
    "id", "fundId", "mcId", "detailId", "businessId", "riskId", "pkId",
    "appId", "allId", "dataKey", "itemId",
)


def response_matches(url: str, pattern: str) -> bool:
    if not pattern:
        return False
    return fnmatch.fnmatch(url, pattern) or pattern in url


def extract_business_id(payload: Any, keys: Iterable[str] = BUSINESS_ID_KEYS) -> str:
    if payload in (None, ""):
        return ""
    if isinstance(payload, (str, int)):
        return str(payload)
    if isinstance(payload, list):
        for item in payload:
            found = extract_business_id(item, keys)
            if found:
                return found
        return ""
    if not isinstance(payload, dict):
        return ""
    lowered = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value not in (None, ""):
            return str(value)
    for envelope in ("data", "result", "body", "content"):
        found = extract_business_id(payload.get(envelope), keys)
        if found:
            return found
    return ""


def extract_runtime_data(response: Any) -> dict[str, Any]:
    current = response
    for _ in range(5):
        if not isinstance(current, dict):
            break
        if "dataJson" in current:
            value = current.get("dataJson")
            return parse_json_object(value) if isinstance(value, str) else (value or {})
        current = current.get("data")
    return {}


def assert_runtime_values(response: Any, definitions: Iterable[FieldDefinition], expected: dict[str, Any]) -> None:
    actual = extract_runtime_data(response)
    failures: list[str] = []
    for field in runtime_fields(definitions):
        if field.field_code not in expected:
            continue
        want = expected[field.field_code]
        got = actual.get(field.field_code)
        normalized_want = ",".join(map(str, want)) if isinstance(want, list) else want
        if str(got) != str(normalized_want):
            failures.append(f"{field.field_code}: expected={normalized_want!r}, actual={got!r}")
    if failures:
        raise AssertionError("Runtime detail mismatch:\n" + "\n".join(failures))


def assert_page_echo(page, fields: Iterable[ResolvedField], expected: dict[str, Any], interactor) -> None:
    failures: list[str] = []
    for field in fields:
        code = field.definition.field_code
        if code not in expected or field.dom is None:
            continue
        want = expected[code]
        try:
            locator = interactor.locate(field)
            try:
                got = locator.input_value()
            except Exception:
                got = (locator.inner_text() or "").strip()
            if isinstance(want, list):
                if not all(str(item) in str(got) for item in want):
                    failures.append(f"{code}: expected items={want!r}, actual={got!r}")
            elif str(want) not in str(got):
                failures.append(f"{code}: expected={want!r}, actual={got!r}")
        except Exception as exc:
            failures.append(f"{code}: cannot read echo ({exc})")
    if failures:
        raise AssertionError("Page echo mismatch:\n" + "\n".join(failures))
