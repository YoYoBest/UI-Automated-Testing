import os
from pathlib import Path

import pytest

from ei_ui_smoke.module_driver import ModuleSmokeDriver


@pytest.mark.smoke
def test_build_project_card_can_open_detail_without_table_row():
    if os.getenv("FI_BUILD_PROJECT_CARD_CHECK", "false").lower() != "true":
        pytest.skip("set FI_BUILD_PROJECT_CARD_CHECK=true to verify the deployed card list")

    from playwright.sync_api import sync_playwright

    url = os.getenv("EI_FORM_URL", "http://172.29.237.39:5443/fi-view/#/buildProject")
    storage = Path(os.getenv("EI_STORAGE_STATE", "artifacts/auth-state.json"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage), ignore_https_errors=True)
        page = context.new_page()
        responses = []
        page.on("response", lambda response: responses.append(response))
        page.goto(url, wait_until="domcontentloaded")
        card = page.locator(".mujijin-cardBox").first
        card.wait_for(state="visible", timeout=15_000)
        project_name = card.locator(".card-name").inner_text().strip()

        response_count_before_detail = len(responses)
        ModuleSmokeDriver(page, data_strategy=None)._open_detail([project_name], "")
        page.wait_for_timeout(5_000)

        assert "/buildProjects/detail" in page.url
        detail_response = ModuleSmokeDriver._find_detail_response(responses, None, "")
        network_summary = [
            (
                response.request.resource_type,
                response.status,
                response.headers.get("content-type", ""),
                response.url,
            )
            for response in responses[response_count_before_detail:]
            if response.request.resource_type in {"xhr", "fetch"}
        ]
        payload_summary = []
        for response in responses[response_count_before_detail:]:
            if response.request.resource_type not in {"xhr", "fetch"}:
                continue
            try:
                payload = response.json()
                nested_keys = sorted({key for item in ModuleSmokeDriver._collect_dicts(payload) for key in item})
                payload_summary.append((response.url, nested_keys))
            except Exception:
                pass
        assert detail_response is not None, (
            f"XHR/fetch responses after opening detail: {network_summary!r}; "
            f"JSON payload shapes: {payload_summary!r}"
        )
        assert isinstance(detail_response.json(), dict)
        context.close()
        browser.close()
