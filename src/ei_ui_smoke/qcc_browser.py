from __future__ import annotations

import argparse
import json
import os
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .qcc_proxy import QccProxyError, QccSearchService, QccSettings, clean_companies, normalize_keyword


SEARCH_MARKERS = ("/dataManager/entSearch", "/api/qcc/companies")


def extract_search_keyword(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    return str((query.get("params") or query.get("keyword") or [""])[0])


def frontend_search_payload(companies: list[dict[str, Any]]) -> dict[str, Any]:
    return {"status": "0", "code": "0", "data": companies}


def backend_qcc_url(search_url: str, keyword: str) -> str:
    parsed = urlparse(search_url)
    path = parsed.path
    marker = "/dataManager/entSearch"
    index = path.lower().find(marker.lower())
    prefix = path[:index] if index >= 0 else ""
    if prefix.lower().endswith("/common-service"):
        prefix = f"{prefix[:-len('/common-service')]}/ei-service"
    return f"{parsed.scheme}://{parsed.netloc}{prefix}/BPI/FUND/QCCSearchData?keyword={quote(keyword)}"


def extract_backend_qcc_payload(response: Any) -> Any:
    payload = response.json()
    value = payload.get("data", {}).get("value") if isinstance(payload, dict) else None
    if not value:
        raise QccProxyError("deployed QCC endpoint returned no data")
    try:
        return json.loads(value) if isinstance(value, str) else value
    except json.JSONDecodeError as exc:
        raise QccProxyError("deployed QCC endpoint returned invalid JSON") from exc


def install_qcc_route(context: Any, service: QccSearchService, *, backend_mode: bool = False) -> None:
    def handle(route: Any) -> None:
        if not any(marker.lower() in route.request.url.lower() for marker in SEARCH_MARKERS):
            route.continue_()
            return
        try:
            keyword = normalize_keyword(extract_search_keyword(route.request.url))
            if backend_mode:
                response = context.request.get(
                    backend_qcc_url(route.request.url, keyword),
                    headers={
                        key: value for key, value in route.request.headers.items()
                        if key.lower() in {"authorization", "x-tenant-id", "accept", "user-agent"}
                    },
                    timeout=15_000,
                )
                if not response.ok:
                    raise QccProxyError(f"deployed QCC endpoint failed: HTTP {response.status}")
                companies = clean_companies(extract_backend_qcc_payload(response), service.settings.max_results)
            else:
                companies, _source = service.search(keyword)
            body = frontend_search_payload(companies)
            route.fulfill(
                status=200,
                content_type="application/json; charset=utf-8",
                body=json.dumps(body, ensure_ascii=False),
            )
        except ValueError as exc:
            route.fulfill(
                status=400,
                content_type="application/json; charset=utf-8",
                body=json.dumps({"status": "400", "code": "INVALID_ARGUMENT", "message": str(exc)}),
            )
        except QccProxyError as exc:
            route.fulfill(
                status=502,
                content_type="application/json; charset=utf-8",
                body=json.dumps({"status": "502", "code": "UPSTREAM_ERROR", "message": str(exc)}),
            )

    context.route("**/*", handle)


def open_verification_browser(
    url: str,
    *,
    storage_state: str = "",
    username: str = "",
    password: str = "",
) -> None:
    from playwright.sync_api import Error, sync_playwright

    browser_mode = os.getenv("QCC_BROWSER_MODE", "backend").strip().lower()
    if browser_mode not in {"backend", "mock", "real"}:
        raise ValueError("QCC_BROWSER_MODE must be 'backend', 'mock' or 'real'")
    settings = QccSettings.from_env() if browser_mode == "real" else QccSettings(
        mode=browser_mode if browser_mode == "mock" else "mock"
    )
    service = QccSearchService(settings)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context_args: dict[str, Any] = {"ignore_https_errors": True}
        if storage_state:
            context_args["storage_state"] = storage_state
        context = browser.new_context(**context_args)
        install_qcc_route(context, service, backend_mode=browser_mode == "backend")
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            login = page.locator('input[name="loginName"],input[placeholder*="账号"],input[placeholder*="用户名"]')
            if login.count() and username and password:
                login.first.fill(username)
                page.locator('input[name="password"],input[type="password"]').first.fill(password)
                page.locator('button[type="submit"],button.submit,.el-button--primary').first.click()
                page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(1_000)
            page.evaluate(
                """
                () => {
                  const badge = document.createElement('div');
                  badge.id = 'qcc-verification-badge';
                  badge.textContent = '企查查拦截已启用';
                  Object.assign(badge.style, {
                    position: 'fixed', top: '8px', right: '12px', zIndex: '2147483647',
                    background: '#b42318', color: '#fff', padding: '8px 12px',
                    borderRadius: '4px', fontSize: '14px', fontWeight: '600'
                  });
                  document.body.appendChild(badge);
                }
                """
            )
            add_button = page.locator(
                "button:has-text('新增'),button:has-text('添加'),button:has-text('新建')"
            )
            if add_button.count() and add_button.first.is_visible():
                add_button.first.click()
                dialog = page.locator('[role="dialog"]:visible,.el-dialog:visible,.el-drawer:visible')
                if dialog.count():
                    dialog.last.wait_for(state="visible", timeout=15_000)
            while not page.is_closed():
                page.wait_for_timeout(500)
        except Error as exc:
            if not page.is_closed() and browser.is_connected():
                raise RuntimeError(f"企查查验证浏览器运行失败：{exc}") from exc
        finally:
            if browser.is_connected():
                context.close()
                browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Open an EI page with QCC search interception")
    parser.add_argument("--url", required=True)
    parser.add_argument("--storage-state", default=os.getenv("EI_STORAGE_STATE", ""))
    parser.add_argument("--username", default=os.getenv("EI_USERNAME", ""))
    args = parser.parse_args()
    open_verification_browser(
        args.url,
        storage_state=args.storage_state,
        username=args.username,
        password=os.getenv("EI_PASSWORD", ""),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
