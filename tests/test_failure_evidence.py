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
