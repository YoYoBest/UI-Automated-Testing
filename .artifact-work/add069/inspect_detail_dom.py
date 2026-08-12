from pathlib import Path

from playwright.sync_api import sync_playwright


URL = "http://172.29.237.39:5443/fi-view/#/buildProjects/detail?id=2085289738192334850"
STATE = Path(__file__).resolve().parents[2] / "artifacts" / "auth-state.json"
LABELS = ["项目名称", "项目基本情况", "项目建设目的", "潜在风险分析"]


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(storage_state=str(STATE), ignore_https_errors=True)
    page = context.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_timeout(5_000)
    page.get_by_role("button", name="编辑", exact=True).click()
    page.wait_for_timeout(2_000)
    scopes = page.locator(
        ".detail-panel:visible form:visible,.detail-panel:visible .el-form:visible,"
        ".base-info-page:visible form:visible,.base-info-page:visible .el-form:visible"
    )
    print("SCOPES", scopes.count())
    for index in range(scopes.count()):
        scope = scopes.nth(index)
        controls = scope.locator(
            "input:not([type='hidden']):not([disabled]):visible,"
            "textarea:not([disabled]):visible,select:not([disabled]):visible,"
            "[role='combobox']:not([aria-disabled='true']):visible,"
            "[role='radio']:visible,[role='checkbox']:visible"
        )
        print("SCOPE", index, "CONTROLS", controls.count(), scope.evaluate("node => node.outerHTML.slice(0, 800)"))
    evidence = page.evaluate(
        """
        labels => labels.map(label => ({
          label,
          matches: Array.from(document.body.querySelectorAll('*'))
            .filter(node => String(node.innerText || '').trim() === label)
            .slice(0, 5)
            .map(node => ({
              tag: node.tagName,
              className: String(node.className || ''),
              outerHTML: node.outerHTML.slice(0, 1200),
              parentHTML: node.parentElement?.outerHTML.slice(0, 2000) || '',
            })),
        }))
        """,
        LABELS,
    )
    for item in evidence:
        print(item)
    context.close()
    browser.close()
