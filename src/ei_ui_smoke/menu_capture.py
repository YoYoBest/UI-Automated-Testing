from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from .project_layout import (
    discover_detail_father_tree_requests,
    discover_detail_prefixes,
    read_app_base_api,
    read_app_id,
)


MENU_API_MARKER = "/funcPerm/getUserFuncPerm"
DETAIL_PREFIXES = ("ZGJJ_", "CGJJ_", "JJGL_")
LOGIN_NAME_SELECTOR = (
    'input[name="loginName"],input[placeholder*="账号"],'
    'input[placeholder*="用户名"]'
)
LOGIN_PASSWORD_SELECTOR = 'input[name="password"],input[type="password"]'
LOGIN_SUBMIT_SELECTOR = (
    'button[type="submit"],button.submit,.el-button--primary'
)


def _storage_state_output_path(save_path: str | Path = "") -> Path:
    """Resolve one output file and create only its exact parent directory."""
    path = Path(save_path).expanduser() if save_path else Path("artifacts") / "auth-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _login_form_visible(page: Any) -> bool:
    login = page.locator(LOGIN_NAME_SELECTOR)
    return bool(login.count() and login.first.is_visible())


def _prepare_login(
    page: Any,
    *,
    username: str,
    password: str,
    headless: bool,
) -> str:
    """Start automatic login or leave the visible page open for manual login."""
    login = page.locator(LOGIN_NAME_SELECTOR)
    if not login.count() or not login.first.is_visible():
        return "existing_session"
    if username and password:
        login.first.fill(username)
        page.locator(LOGIN_PASSWORD_SELECTOR).first.fill(password)
        page.locator(LOGIN_SUBMIT_SELECTOR).first.click()
        return "automatic"
    if headless:
        raise ValueError(
            "登录状态已失效；无头浏览器无法完成人工登录，请关闭无头模式或提供用户名和密码"
        )
    return "manual"


def _sanitized_page_url(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    fragment = parsed.fragment.split("?", 1)[0]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", fragment))


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


def _detail_nodes(response: Any) -> list[Any]:
    if not response.ok:
        return []
    payload = response.json()
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    return payload if isinstance(payload, list) else []


def _capture_detail_father_trees(
    context: Any,
    *,
    origin: str,
    app_id: str,
    auth_headers: dict[str, str],
    source_root: Path,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    """Fetch each statically declared father-ID detail tree without opening a page."""
    base_api = read_app_base_api(source_root, "/ezgo").rstrip("/")
    endpoint = f"{origin}{base_api}/ei-service/funcPerm/getUserFuncPermTree"
    trees: list[dict[str, Any]] = []
    for request in discover_detail_father_tree_requests(source_root):
        response = context.request.get(
            endpoint,
            params={"appId": app_id, "fatherId": request.father_id},
            headers=auth_headers,
            timeout=timeout_ms,
        )
        trees.append({
            "fatherId": request.father_id,
            "sourceComponent": request.source_component,
            "nodes": _detail_nodes(response),
        })
    return trees


def capture_menu(
    system_url: str,
    *,
    username: str = "",
    password: str = "",
    storage_state: str = "",
    headless: bool = False,
    timeout_ms: int = 60_000,
    source_root: Path | None = None,
    save_storage_state_path: str | Path = "",
) -> tuple[dict[str, Any], str]:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context_args: dict[str, Any] = {"ignore_https_errors": True}
        if storage_state:
            context_args["storage_state"] = str(Path(storage_state))
        context = browser.new_context(**context_args)
        page = context.new_page()
        response = None
        observed_candidates: list[str] = []

        def observe_candidate(item: Any) -> None:
            if "funcPerm" not in item.url:
                return
            marker = f"{urlsplit(item.url).path}:{item.status}"
            if marker not in observed_candidates:
                observed_candidates.append(marker)
                del observed_candidates[:-5]

        page.on("response", observe_candidate)
        try:
            try:
                with page.expect_response(
                    lambda item: MENU_API_MARKER in item.url and item.ok,
                    timeout=timeout_ms,
                ) as response_info:
                    page.goto(system_url, wait_until="domcontentloaded")
                    _prepare_login(
                        page,
                        username=username,
                        password=password,
                        headless=headless,
                    )
            except PlaywrightTimeoutError as exc:
                candidate_text = ", ".join(observed_candidates) or "未观察到权限菜单响应"
                login_state = "登录页仍可见" if _login_form_visible(page) else "登录页不可见"
                raise ValueError(
                    f"等待权限菜单响应超时（{timeout_ms}ms）；{login_state}；"
                    f"当前页面={_sanitized_page_url(page.url) or '未知'}；"
                    f"候选响应={candidate_text}。请确认登录、验证码/二次验证、应用地址和菜单接口。"
                ) from exc
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
                detail_trees[prefix] = _detail_nodes(detail_response)
            payload["_detailTrees"] = detail_trees
            payload["_detailFatherTrees"] = (
                _capture_detail_father_trees(
                    context,
                    origin=origin,
                    app_id=app_id,
                    auth_headers=auth_headers,
                    source_root=source_root,
                    timeout_ms=timeout_ms,
                )
                if source_root else []
            )
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
            state_path = _storage_state_output_path(save_storage_state_path)
            context.storage_state(path=str(state_path))
            return payload, str(state_path)
        finally:
            context.close()
            browser.close()
