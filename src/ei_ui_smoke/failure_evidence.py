from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    url: str
    screenshot: bytes


_EVIDENCE_ATTRIBUTE = "_ei_ui_failure_evidence"


def clear_failure_evidence(page) -> None:
    try:
        delattr(page, _EVIDENCE_ATTRIBUTE)
    except AttributeError:
        pass


def capture_failure_evidence(page, error_message: str = "") -> None:
    """Capture the current viewport before cleanup destroys the failure state."""
    overlay_id = "__ei_ui_failure_message__"
    try:
        if error_message:
            page.evaluate(
                """([overlayId, message]) => {
                    document.getElementById(overlayId)?.remove();
                    const banner = document.createElement('div');
                    banner.id = overlayId;
                    banner.textContent = `断言失败：${message}`;
                    Object.assign(banner.style, {
                        position: 'fixed',
                        top: '0',
                        left: '0',
                        right: '0',
                        zIndex: '2147483647',
                        padding: '14px 18px',
                        boxSizing: 'border-box',
                        background: '#b42318',
                        color: '#ffffff',
                        font: '600 15px/1.5 sans-serif',
                        whiteSpace: 'pre-wrap',
                        overflowWrap: 'anywhere',
                        boxShadow: '0 2px 8px rgba(0, 0, 0, .28)',
                        pointerEvents: 'none'
                    });
                    document.documentElement.appendChild(banner);
                }""",
                [overlay_id, error_message],
            )
        evidence = FailureEvidence(
            url=str(page.url),
            screenshot=page.screenshot(full_page=False),
        )
        setattr(page, _EVIDENCE_ATTRIBUTE, evidence)
    except Exception:
        try:
            evidence = FailureEvidence(
                url=str(page.url),
                screenshot=page.screenshot(full_page=False),
            )
            setattr(page, _EVIDENCE_ATTRIBUTE, evidence)
        except Exception:
            pass
    finally:
        if error_message:
            try:
                page.evaluate(
                    "overlayId => document.getElementById(overlayId)?.remove()",
                    overlay_id,
                )
            except Exception:
                pass


def consume_failure_evidence(page) -> FailureEvidence | None:
    evidence = getattr(page, _EVIDENCE_ATTRIBUTE, None)
    clear_failure_evidence(page)
    return evidence if isinstance(evidence, FailureEvidence) else None
