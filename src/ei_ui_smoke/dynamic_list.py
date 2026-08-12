from __future__ import annotations

from typing import Any, Iterable


def enabled_queries(queries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result = [q for q in queries if str(q.get("enabled", 1)) == "1" and q.get("conditionCode")]
    return sorted(result, key=lambda q: (int(q.get("sortOrder") or 0), str(q.get("conditionCode"))))


def create_query_model(queries: Iterable[dict[str, Any]]) -> dict[str, Any]:
    return {str(q["conditionCode"]): q.get("defaultValue", [] if q.get("valueMode") == "MULTI" else "") for q in enabled_queries(queries)}


def build_query_values(queries: Iterable[dict[str, Any]], model: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for query in enabled_queries(queries):
        code = str(query["conditionCode"])
        value = model.get(code)
        if query.get("valueMode") == "NONE":
            if value:
                result.append({"conditionCode": code, "value": True})
            continue
        if value not in (None, "", []):
            result.append({"conditionCode": code, "value": value})
    return result


def flatten_columns(columns: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for column in columns:
        children = column.get("children")
        if isinstance(children, list) and children:
            result.extend(flatten_columns(children))
        elif str(column.get("enabled", 1)) == "1" and column.get("fieldCode"):
            result.append(column)
    return result


def display_value(row: dict[str, Any], column: dict[str, Any]) -> Any:
    code = str(column.get("fieldCode") or "")
    display_code = str(column.get("displayFieldCode") or "")
    if display_code and row.get(display_code) not in (None, ""):
        return row[display_code]
    labels = row.get("dynamicFieldLabels")
    if isinstance(labels, dict) and labels.get(code) not in (None, ""):
        value = labels[code]
        if isinstance(value, list):
            return ",".join(str(item.get("name") or item.get("value") or "") if isinstance(item, dict) else str(item) for item in value)
        return value
    if row.get(code) not in (None, ""):
        return row[code]
    dynamic = row.get("dynamicFields")
    return dynamic.get(code, "") if isinstance(dynamic, dict) else ""

