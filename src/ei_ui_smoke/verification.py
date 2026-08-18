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

BUSINESS_ID_ENVELOPES = ("data", "result", "body", "content")


def response_matches(url: str, pattern: str) -> bool:
    if not pattern:
        return False
    return fnmatch.fnmatch(url, pattern) or pattern in url


def extract_business_id(payload: Any, keys: Iterable[str] = BUSINESS_ID_KEYS) -> str:
    """Return one unambiguous primary-record ID from a save response.

    Save responses may contain child-table rows, attachments, or audit records,
    all of which commonly expose a generic ``id``.  Only direct response fields
    and direct records reached through known response envelopes are eligible.
    Arbitrary recursive traversal is deliberately forbidden.
    """
    if payload in (None, ""):
        return ""
    if isinstance(payload, (str, int)) and not isinstance(payload, bool):
        return str(payload).strip()
    if not isinstance(payload, dict):
        return ""

    normalized_keys = tuple(str(key).lower() for key in keys)
    candidates: list[str] = []
    current: Any = payload
    for _depth in range(6):
        if isinstance(current, dict):
            lowered = {str(key).lower(): value for key, value in current.items()}
            direct = {
                str(lowered[key]).strip()
                for key in normalized_keys
                if key in lowered and lowered[key] not in (None, "")
            }
            if len(direct) > 1:
                return ""
            if direct:
                candidates.extend(direct)

            envelopes = [
                current.get(name)
                for name in BUSINESS_ID_ENVELOPES
                if current.get(name) not in (None, "")
            ]
            if not envelopes:
                break
            if len(envelopes) != 1:
                envelope_ids = {
                    identifier
                    for envelope in envelopes
                    if (identifier := _direct_envelope_business_id(
                        envelope, normalized_keys
                    ))
                }
                if len(envelope_ids) != 1:
                    return ""
                candidates.extend(envelope_ids)
                break
            current = envelopes[0]
            continue

        if isinstance(current, list):
            if len(current) != 1:
                return ""
            current = current[0]
            continue

        if isinstance(current, (str, int)) and not isinstance(current, bool):
            value = str(current).strip()
            if value:
                candidates.append(value)
        break

    unique = set(candidates)
    return candidates[0] if len(unique) == 1 else ""


def _direct_envelope_business_id(
    payload: Any,
    normalized_keys: tuple[str, ...],
) -> str:
    """Read an ID from one envelope level without traversing child objects."""
    if isinstance(payload, (str, int)) and not isinstance(payload, bool):
        return str(payload).strip()
    if isinstance(payload, list):
        if len(payload) != 1:
            return ""
        payload = payload[0]
    if not isinstance(payload, dict):
        return ""
    lowered = {str(key).lower(): value for key, value in payload.items()}
    values = {
        str(lowered[key]).strip()
        for key in normalized_keys
        if key in lowered and lowered[key] not in (None, "")
    }
    return next(iter(values)) if len(values) == 1 else ""


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
