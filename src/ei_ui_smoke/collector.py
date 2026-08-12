from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .data_pool import atomic_write_json, load_json


def get_path(data: Any, path: str, default: Any = None) -> Any:
    current = data
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return default
    return default if current is None else current


def _first_value(record: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def browser_request(page, method: str, url: str, *, body: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
    return page.evaluate(
        """async ({method, url, body, params}) => {
          const headers = {'Content-Type': 'application/json'};
          const token = localStorage.getItem('accessToken');
          const tenant = localStorage.getItem('tenantId') || localStorage.getItem('tenant-id');
          if (token) headers.Authorization = token;
          if (tenant) { headers.tenantId = tenant; headers['x-tenant-id'] = tenant; headers['X-Tenant-Id'] = tenant; }
          headers['X-Language'] = localStorage.getItem('i18n-language') || 'zh_CN';
          const query = params && Object.keys(params).length ? '?' + new URLSearchParams(params).toString() : '';
          const response = await fetch(url + query, {method, headers, body: method === 'GET' ? undefined : JSON.stringify(body || {})});
          const text = await response.text();
          let result; try { result = JSON.parse(text); } catch { result = text; }
          if (!response.ok) throw new Error(`${response.status} ${text}`);
          return result;
        }""",
        {"method": method.upper(), "url": url, "body": body or {}, "params": params or {}},
    )


def collect_form_data(page, form_code: str, source: dict[str, Any], output_path: Path, limit: int = 20) -> dict[str, Any]:
    list_spec = source.get("list") or {}
    detail_spec = source.get("detail") or {}
    allowed = [str(item) for item in source.get("allowedFields") or []]
    if not list_spec.get("url") or not detail_spec.get("url") or not allowed:
        raise ValueError(f"Collector for {form_code} requires list.url, detail.url and allowedFields")

    list_response = browser_request(
        page,
        str(list_spec.get("method") or "POST"),
        str(list_spec["url"]),
        body=dict(list_spec.get("body") or {}),
        params=dict(list_spec.get("params") or {}),
    )
    records = get_path(list_response, str(list_spec.get("recordsPath") or "data.records"), [])
    if not isinstance(records, list):
        raise ValueError(f"recordsPath did not resolve to a list for {form_code}")

    values: dict[str, list[Any]] = {field: [] for field in allowed}
    source_ids: list[str] = []
    for record in records[:limit]:
        if not isinstance(record, dict):
            continue
        business_id = _first_value(record, [str(k) for k in detail_spec.get("idFields") or ["id", "businessId"]])
        if business_id in (None, ""):
            continue
        source_ids.append(str(business_id))
        params = dict(detail_spec.get("params") or {})
        body = dict(detail_spec.get("body") or {})
        id_param = str(detail_spec.get("idParam") or "id")
        if str(detail_spec.get("method") or "GET").upper() == "GET":
            params[id_param] = business_id
        else:
            body[id_param] = business_id
        response = browser_request(
            page,
            str(detail_spec.get("method") or "GET"),
            str(detail_spec["url"]),
            body=body,
            params=params,
        )
        detail = get_path(response, str(detail_spec.get("dataPath") or "data"), {})
        if not isinstance(detail, dict):
            continue
        for field in allowed:
            value = detail.get(field)
            if value in (None, "", [], {}):
                continue
            if value not in values[field]:
                values[field].append(value)

    store = load_json(output_path, {"version": 1, "forms": {}})
    forms = store.setdefault("forms", {})
    forms[form_code] = {
        "collectedAt": datetime.now(timezone.utc).isoformat(),
        "sourceRecordIds": source_ids,
        "fields": {
            field: {"values": field_values, "source": "detail-api"}
            for field, field_values in values.items()
            if field_values
        },
    }
    atomic_write_json(output_path, store)
    return forms[form_code]


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect stable smoke data from existing EI detail records")
    parser.add_argument("--form-code", required=True)
    parser.add_argument("--base-url", default=os.getenv("EI_BASE_URL", ""))
    parser.add_argument("--storage-state", default=os.getenv("EI_STORAGE_STATE", ""))
    parser.add_argument("--headless", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not args.base_url:
        parser.error("--base-url or EI_BASE_URL is required")

    project_root = Path(__file__).resolve().parents[2]
    sources = load_json(project_root / "data" / "collection_sources.json")
    source = ((sources.get("forms") or {}).get(args.form_code) or {})
    if not source:
        parser.error(f"No collection source configured for {args.form_code}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        context = browser.new_context(storage_state=args.storage_state or None, ignore_https_errors=True)
        page = context.new_page()
        page.goto(args.base_url, wait_until="domcontentloaded")
        result = collect_form_data(
            page,
            args.form_code,
            source,
            project_root / "data" / "collected_data.json",
            args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        context.close()
        browser.close()


if __name__ == "__main__":
    main()

