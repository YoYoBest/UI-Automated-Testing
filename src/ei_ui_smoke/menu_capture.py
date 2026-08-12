from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from .project_layout import discover_detail_prefixes, read_app_id


MENU_API_MARKER = "/funcPerm/getUserFuncPerm"
DETAIL_PREFIXES = ("ZGJJ_", "CGJJ_", "JJGL_")


def _join_route(parent: str, child: str) -> str:
    if not child:
        return parent
    if child.startswith(("http://", "https://", "/")):
        return child
    return "/" + "/".join(part.strip("/") for part in (parent, child) if part.strip("/"))


def _menu_leaves(nodes: list[object], parent_route: str = ""):
    for position, raw in enumerate(nodes):
        if not isinstance(raw, dict):
            continue
        meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
        if meta.get("hidden") is True:
            continue
        route = _join_route(parent_route, str(raw.get("path") or "").strip())
        children = raw.get("children") if isinstance(raw.get("children"), list) else []
        if children:
            yield from _menu_leaves(children, route)
        elif route:
            yield str(raw.get("funcCode") or raw.get("id") or route or f"menu-{position}"), route


def capture_menu(
    system_url: str,
    *,
    username: str = "",
    password: str = "",
    storage_state: str = "",
    headless: bool = False,
    timeout_ms: int = 60_000,
    source_root: Path | None = None,
) -> tuple[dict[str, Any], str]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context_args: dict[str, Any] = {"ignore_https_errors": True}
        if storage_state:
            context_args["storage_state"] = str(Path(storage_state))
        context = browser.new_context(**context_args)
        page = context.new_page()
        response = None
        try:
            with page.expect_response(lambda item: MENU_API_MARKER in item.url, timeout=timeout_ms) as response_info:
                page.goto(system_url, wait_until="domcontentloaded")
                login = page.locator('input[name="loginName"],input[placeholder*="账号"],input[placeholder*="用户名"]')
                if login.count():
                    if not username or not password:
                        raise ValueError("登录状态已失效，请输入用户名和密码")
                    login.first.fill(username)
                    page.locator('input[name="password"],input[type="password"]').first.fill(password)
                    page.locator('button[type="submit"],button.submit,.el-button--primary').first.click()
            response = response_info.value
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("菜单接口返回的不是 JSON 对象")
            origin = f"{urlsplit(system_url).scheme}://{urlsplit(system_url).netloc}"
            request_headers = response.request.headers
            auth_headers = {
                key: value for key, value in request_headers.items()
                if key.lower() in {"authorization", "x-access-token", "token", "x-tenant-id"}
            }
            detail_trees: dict[str, list[Any]] = {}
            app_id = read_app_id(source_root, "10015") if source_root else "10015"
            discovered_prefixes = discover_detail_prefixes(source_root) if source_root else ()
            prefixes = tuple(dict.fromkeys((*DETAIL_PREFIXES, *discovered_prefixes)))
            for prefix in prefixes:
                detail_response = context.request.get(
                    f"{origin}/ezgo/ezgo-uim/funcPerm/getUserFuncPermTreeByFuncCode",
                    params={"appId": app_id, "funcCode": prefix},
                    headers=auth_headers,
                    timeout=timeout_ms,
                )
                if not detail_response.ok:
                    detail_trees[prefix] = []
                    continue
                detail_payload = detail_response.json()
                if isinstance(detail_payload, dict) and isinstance(detail_payload.get("data"), list):
                    detail_trees[prefix] = detail_payload["data"]
                elif isinstance(detail_payload, list):
                    detail_trees[prefix] = detail_payload
                else:
                    detail_trees[prefix] = []
            payload["_detailTrees"] = detail_trees
            button_response = context.request.get(
                f"{origin}/ezgo/ei-service/proj/getUserButtonPermissions",
                params={"appId": app_id}, headers=auth_headers, timeout=timeout_ms,
            )
            if button_response.ok:
                button_payload = button_response.json()
                button_data = button_payload.get("data", button_payload) if isinstance(button_payload, dict) else {}
                button_items = button_data.get("funcPerm", []) if isinstance(button_data, dict) else []
                payload["_buttonCodes"] = [
                    str(item.get("code")) for item in button_items
                    if isinstance(item, dict) and item.get("code")
                ]
            state_path = str(Path("artifacts") / "auth-state.json")
            context.storage_state(path=state_path)
            return payload, state_path
        finally:
            context.close()
            browser.close()
