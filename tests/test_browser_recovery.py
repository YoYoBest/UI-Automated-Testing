import pytest

from ei_ui_smoke.browser_recovery import (
    BrowserNavigationCircuit,
    BrowserNavigationCircuitOpen,
    BrowserNavigationError,
    BrowserRecoveryPolicy,
    recover_fresh_browser_session,
)


def test_recovery_retries_with_a_new_session_and_resets_the_circuit():
    policy = BrowserRecoveryPolicy(fresh_session_attempts=2, circuit_failure_threshold=3)
    circuit = BrowserNavigationCircuit(policy)
    attempts = []
    closed = []

    def open_session():
        attempts.append("open")
        if len(attempts) == 1:
            raise RuntimeError("first navigation timed out")
        return "ready-session"

    session, attempt = recover_fresh_browser_session(
        url="https://example.test/module",
        policy=policy,
        circuit=circuit,
        open_session=open_session,
        close_session=closed.append,
    )

    assert session == "ready-session"
    assert attempt == 2
    assert circuit.consecutive_failures == 0
    assert closed == []


def test_recovery_fails_only_the_current_item_until_the_circuit_threshold():
    policy = BrowserRecoveryPolicy(fresh_session_attempts=2, circuit_failure_threshold=3)
    circuit = BrowserNavigationCircuit(policy)
    attempts = []

    def open_session():
        attempts.append("open")
        raise RuntimeError("navigation timed out")

    for expected_failures in (1, 2, 3):
        with pytest.raises(BrowserNavigationError) as error:
            recover_fresh_browser_session(
                url="https://example.test/module",
                policy=policy,
                circuit=circuit,
                open_session=open_session,
                close_session=lambda _session: None,
            )
        assert f"consecutive_item_failures={expected_failures}" in str(error.value)

    with pytest.raises(BrowserNavigationCircuitOpen, match="已熔断"):
        recover_fresh_browser_session(
            url="https://example.test/module",
            policy=policy,
            circuit=circuit,
            open_session=open_session,
            close_session=lambda _session: None,
        )

    assert len(attempts) == 6


def test_success_after_a_failed_item_resets_consecutive_failure_count():
    policy = BrowserRecoveryPolicy(fresh_session_attempts=1, circuit_failure_threshold=3)
    circuit = BrowserNavigationCircuit(policy)

    with pytest.raises(BrowserNavigationError):
        recover_fresh_browser_session(
            url="https://example.test/module",
            policy=policy,
            circuit=circuit,
            open_session=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            close_session=lambda _session: None,
        )

    session, attempt = recover_fresh_browser_session(
        url="https://example.test/module",
        policy=policy,
        circuit=circuit,
        open_session=lambda: "ready-session",
        close_session=lambda _session: None,
    )

    assert (session, attempt, circuit.consecutive_failures) == ("ready-session", 1, 0)
