import os
import json
from pathlib import Path

import pytest

from ei_ui_smoke.qcc_browser import install_qcc_route
from ei_ui_smoke.module_driver import ModuleSmokeDriver
from ei_ui_smoke.qcc_proxy import QccSearchService, QccSettings


@pytest.mark.smoke
def test_manage_platform_company_name_uses_qcc_dropdown():
    if os.getenv("EI_QCC_PAGE_CHECK", "false").lower() != "true":
        pytest.skip("set EI_QCC_PAGE_CHECK=true to verify the deployed management platform")

    from playwright.sync_api import sync_playwright

    url = os.getenv("EI_FORM_URL", "http://172.29.237.39:5443/ei-view/#/managePlatform")
    add_button_label = os.getenv("EI_QCC_ADD_BUTTON", "新增管理平台")
    company_field_label = os.getenv("EI_QCC_FIELD_LABEL", "公司全称")
    storage = Path(os.getenv("EI_STORAGE_STATE", "artifacts/auth-state.json"))
    backend_mode = os.getenv("QCC_BROWSER_MODE", "backend").lower() == "backend"
    service = QccSearchService(QccSettings(mode="mock"))
    captured = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage), ignore_https_errors=True)
        install_qcc_route(context, service, backend_mode=backend_mode)
        page = context.new_page()
        page.on("request", lambda request: captured.append(request.url))
        page.goto(url, wait_until="domcontentloaded")
        page.get_by_role("button", name=add_button_label, exact=True).click()
        dialog = page.locator('[role="dialog"]:visible,.el-dialog:visible').last
        dialog.wait_for(state="visible", timeout=15_000)
        with page.expect_response(lambda response: "/dataManager/entSearch" in response.url) as response_info:
            selected_company = ModuleSmokeDriver(page, data_strategy=None)._select_by_label(
                company_field_label
            )
        search_response = response_info.value
        search_status = search_response.status
        search_body = search_response.text()
        page.wait_for_timeout(2_000)

        search_requests = [url for url in captured if "/dataManager/entSearch" in url]
        search_data = json.loads(search_body).get("data", [])
        artifact = Path("artifacts/qcc-page-check.png")
        page.screenshot(path=artifact, full_page=True)
        context.close()
        browser.close()

    assert search_requests, "公司全称输入关键字后没有发出 dataManager/entSearch 请求"
    assert selected_company, "普通冒烟流程没有选中企查查企业"
    assert search_data, (
        "企查查请求已发出，但没有返回可选企业；"
        f"status={search_status}, body={search_body}, requests={search_requests}"
    )
