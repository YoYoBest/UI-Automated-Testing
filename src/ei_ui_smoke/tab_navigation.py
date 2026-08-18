from __future__ import annotations

import re


class PageTabUnavailableError(AssertionError):
    """The source-confirmed page tab is not rendered by the deployed page."""


def activate_page_tab(page, label: str, *, timeout: int = 15_000) -> None:
    """Select one page-level Element Plus tab and confirm it became active."""
    expected = re.compile(rf"^\s*{re.escape(label)}\s*$")
    tab = page.locator("[role='tab']:visible,.el-tabs__item:visible").filter(
        has_text=expected
    ).first
    try:
        tab.wait_for(state="visible", timeout=timeout)
    except Exception as exc:
        raise PageTabUnavailableError(f"页面未渲染源码已确认的页签：{label}") from exc
    if tab.get_attribute("aria-selected") == "true" or "is-active" in (
        tab.get_attribute("class") or ""
    ):
        return
    tab.click()
    try:
        tab.wait_for(state="visible", timeout=timeout)
        is_active = (
            tab.get_attribute("aria-selected") == "true"
            or "is-active" in (tab.get_attribute("class") or "")
        )
    except Exception as exc:
        raise PageTabUnavailableError(f"无法确认页签已切换：{label}") from exc
    if not is_active:
        raise PageTabUnavailableError(f"点击后页签未激活：{label}")
