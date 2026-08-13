"""Bounded browser-session recovery for parameterized deployed UI tests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class BrowserNavigationError(RuntimeError):
    """Every fresh-session navigation attempt for one pytest item failed."""

    def __init__(self, *, url: str, attempts: int, consecutive_failures: int, errors: list[Exception]):
        summary = " | ".join(str(error) or type(error).__name__ for error in errors)
        super().__init__(
            "浏览器/环境前置失败："
            f"url={url}; fresh_session_attempts={attempts}; "
            f"consecutive_item_failures={consecutive_failures}; errors={summary}"
        )


class BrowserNavigationCircuitOpen(RuntimeError):
    """Repeated independent session failures make later attempts non-actionable."""

    def __init__(self, *, failures: int):
        super().__init__(
            "浏览器/环境前置持续失败，已熔断本模块后续用例："
            f"consecutive_item_failures={failures}"
        )


@dataclass(frozen=True)
class BrowserRecoveryPolicy:
    fresh_session_attempts: int = 2
    circuit_failure_threshold: int = 3

    def __post_init__(self) -> None:
        if self.fresh_session_attempts < 1:
            raise ValueError("fresh_session_attempts must be at least 1")
        if self.circuit_failure_threshold < 1:
            raise ValueError("circuit_failure_threshold must be at least 1")


class BrowserNavigationCircuit:
    """Count failed pytest items, never individual retries within one item."""

    def __init__(self, policy: BrowserRecoveryPolicy):
        self.policy = policy
        self.consecutive_failures = 0

    @property
    def is_open(self) -> bool:
        return self.consecutive_failures >= self.policy.circuit_failure_threshold

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_item_failure(self) -> int:
        self.consecutive_failures += 1
        return self.consecutive_failures


def recover_fresh_browser_session(
    *,
    url: str,
    policy: BrowserRecoveryPolicy,
    circuit: BrowserNavigationCircuit,
    open_session: Callable[[], Any],
    close_session: Callable[[Any], None],
) -> tuple[Any, int]:
    """Return a ready fresh session or raise a per-item failure/circuit error."""
    if circuit.is_open:
        raise BrowserNavigationCircuitOpen(failures=circuit.consecutive_failures)

    errors: list[Exception] = []
    for attempt in range(1, policy.fresh_session_attempts + 1):
        session = None
        try:
            session = open_session()
        except Exception as exc:
            errors.append(exc)
            if session is not None:
                close_session(session)
            continue
        circuit.record_success()
        return session, attempt

    failures = circuit.record_item_failure()
    raise BrowserNavigationError(
        url=url,
        attempts=policy.fresh_session_attempts,
        consecutive_failures=failures,
        errors=errors,
    )
