from ei_ui_smoke.failure_evidence import (
    capture_failure_evidence,
    consume_failure_evidence,
)


class _Page:
    url = "https://example.test/form?token=secret"

    def __init__(self):
        self.events = []

    def evaluate(self, script, argument):
        self.events.append(("evaluate", argument))

    def screenshot(self, **kwargs):
        self.events.append(("screenshot", kwargs))
        return b"png"


def test_failure_message_is_visible_during_screenshot_then_removed():
    page = _Page()

    capture_failure_evidence(page, "保存后没有捕获接口响应")
    evidence = consume_failure_evidence(page)

    assert evidence is not None
    assert evidence.url == page.url
    assert evidence.screenshot == b"png"
    assert page.events == [
        ("evaluate", ["__ei_ui_failure_message__", "保存后没有捕获接口响应"]),
        ("screenshot", {"full_page": False}),
        ("evaluate", "__ei_ui_failure_message__"),
    ]
    assert consume_failure_evidence(page) is None


def test_failure_evidence_keeps_structured_diagnostics_and_safe_dom_structure():
    class DiagnosticPage(_Page):
        def evaluate(self, script, argument=None):
            self.events.append(("evaluate", argument))
            if "document.querySelectorAll" in script:
                return [{"selector": ".el-upload:visible", "count": 1, "nodes": []}]

    page = DiagnosticPage()

    capture_failure_evidence(
        page,
        "附件超时待诊断",
        diagnostics={"attachmentLifecycle": {"classification": "network_request_timeout"}},
    )
    evidence = consume_failure_evidence(page)

    assert evidence is not None
    assert evidence.diagnostics == {
        "attachmentLifecycle": {"classification": "network_request_timeout"}
    }
    assert evidence.dom_snapshot == [{"selector": ".el-upload:visible", "count": 1, "nodes": []}]
    assert not any("outerHTML" in event[0] for event in page.events)


def test_safe_dom_structure_snapshot_cannot_collect_text_or_control_values():
    from ei_ui_smoke import failure_evidence

    source = failure_evidence._dom_structure_snapshot.__doc__ or ""
    assert "never retain HTML or field values" in source


def test_failure_evidence_keeps_explicit_diagnostics_without_exporting_dom():
    page = _Page()

    capture_failure_evidence(page, diagnostics={"field": "assetYear", "state": "missing"})
    evidence = consume_failure_evidence(page)

    assert evidence is not None
    assert evidence.diagnostics == {"field": "assetYear", "state": "missing"}
    assert evidence.dom_snapshot is None
