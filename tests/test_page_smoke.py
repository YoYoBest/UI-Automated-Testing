from pathlib import Path

import pytest


@pytest.mark.smoke
def test_selected_module_opens_without_page_error(browser_page):
    assert browser_page.url
    body = browser_page.locator("body")
    body.wait_for(state="visible")
    try:
        browser_page.wait_for_function(
            "() => (document.body?.innerText || '').trim().length > 0",
            timeout=20_000,
        )
    except Exception:
        artifact_dir = Path(__file__).resolve().parents[1] / "artifacts" / "failures"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        browser_page.screenshot(path=artifact_dir / "page-empty.png", full_page=True)
        (artifact_dir / "page-empty.html").write_text(browser_page.content(), encoding="utf-8")
        raise AssertionError(f"页面在 20 秒后仍为空白：{browser_page.url}")
    text = body.inner_text().strip()
    assert text, "页面内容为空"
    assert "404" not in page_title_and_text(browser_page, text)


def page_title_and_text(page, text):
    return f"{page.title()} {text[:1000]}"
