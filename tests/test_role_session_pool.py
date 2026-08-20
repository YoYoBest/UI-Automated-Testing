import base64
import json
from pathlib import Path

import pytest

from ei_ui_smoke.workflow import (
    RoleSessionPool,
    RoleSessionSpec,
    WorkflowConfigurationError,
    WorkflowSessionError,
    parse_role_session_specs,
)


def _jwt(
    *,
    expires: int | float | str | None,
    claims: dict | None = None,
) -> str:
    def segment(value):
        encoded = base64.urlsafe_b64encode(
            json.dumps(value, separators=(",", ":")).encode("utf-8")
        )
        return encoded.rstrip(b"=").decode("ascii")

    payload = dict(claims) if claims is not None else {"sub": "workflow-user"}
    if expires is not None:
        payload["exp"] = expires
    return f'{segment({"alg": "HS256", "typ": "JWT"})}.{segment(payload)}.signature'


def _state_with_local_storage(name: str, value: str):
    return {
        "cookies": [],
        "origins": [
            {
                "origin": "https://ei.example",
                "localStorage": [{"name": name, "value": value}],
            }
        ],
    }


class _Page:
    def __init__(self, role: str, events: list, *, fail_close: bool = False):
        self.role = role
        self.events = events
        self.closed = False
        self.fail_close = fail_close
        self.url = "about:blank"

    def is_closed(self):
        return self.closed

    def close(self):
        self.events.append(("page.close", self.role))
        self.closed = True
        if self.fail_close:
            raise RuntimeError("page close failed")


class _Context:
    def __init__(
        self,
        role: str,
        events: list,
        *,
        fail_new_page: bool = False,
        fail_close: bool = False,
        fail_page_close: bool = False,
    ):
        self.role = role
        self.events = events
        self.fail_new_page = fail_new_page
        self.fail_close = fail_close
        self.fail_page_close = fail_page_close
        self.page = None

    def new_page(self):
        self.events.append(("new_page", self.role))
        if self.fail_new_page:
            raise RuntimeError("new page failed with secret path")
        self.page = _Page(
            self.role, self.events, fail_close=self.fail_page_close
        )
        return self.page

    def close(self):
        self.events.append(("context.close", self.role))
        if self.fail_close:
            raise RuntimeError("context close failed")


class _Browser:
    def __init__(self, events: list):
        self.events = events
        self.contexts = []
        self.fail_new_context_for = set()
        self.fail_new_page_for = set()
        self.fail_context_close_for = set()
        self.fail_page_close_for = set()

    def new_context(self, *, storage_state, ignore_https_errors):
        role = Path(storage_state).stem
        self.events.append(("new_context", role, ignore_https_errors))
        if role in self.fail_new_context_for:
            raise RuntimeError("invalid storage state token=secret")
        context = _Context(
            role,
            self.events,
            fail_new_page=role in self.fail_new_page_for,
            fail_close=role in self.fail_context_close_for,
            fail_page_close=role in self.fail_page_close_for,
        )
        self.contexts.append(context)
        return context

    def close(self):
        self.events.append(("browser.close", "browser"))


def _specs(tmp_path):
    maker = tmp_path / "maker.json"
    approver = tmp_path / "approver.json"
    maker.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    approver.write_text(
        '{"cookies": [], "origins": [{"origin": "https://ei.example", '
        '"localStorage": [{"name": "workflowRole", "value": "approver"}]}]}',
        encoding="utf-8",
    )
    return (
        RoleSessionSpec("maker", maker),
        RoleSessionSpec("approver", approver),
    )


class _LoginControl:
    def __init__(self, context, kind: str):
        self.context = context
        self.kind = kind
        self.filled = []

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return not self.context.authenticated

    def fill(self, value):
        self.filled.append(value)

    def click(self):
        if self.kind == "submit":
            self.context.authenticated = True


class _LoginPage:
    def __init__(self, context):
        self.context = context
        self.url = "about:blank"
        self.closed = False
        self.login = _LoginControl(context, "login")
        self.password = _LoginControl(context, "password")
        self.submit = _LoginControl(context, "submit")

    def goto(self, url, *, wait_until):
        assert wait_until == "domcontentloaded"
        self.url = url

    def locator(self, selector):
        if "loginName" in selector:
            return self.login
        if "password" in selector:
            return self.password
        return self.submit

    def wait_for_timeout(self, _milliseconds):
        return None

    def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed


class _LoginContext:
    def __init__(self, *, authenticated: bool):
        self.authenticated = authenticated
        self.page = _LoginPage(self)
        self.saved_paths = []

    def new_page(self):
        return self.page

    def storage_state(self, *, path):
        Path(path).write_text('{"cookies": [], "origins": []}', encoding="utf-8")
        self.saved_paths.append(path)

    def close(self):
        return None


class _LoginBrowser:
    def __init__(self, *, cached_state_authenticates: bool = True):
        self.context_args = []
        self.contexts = []
        self.cached_state_authenticates = cached_state_authenticates

    def new_context(self, **kwargs):
        self.context_args.append(kwargs)
        context = _LoginContext(
            authenticated=(
                "storage_state" in kwargs and self.cached_state_authenticates
            )
        )
        self.contexts.append(context)
        return context


def test_dynamic_login_creates_and_then_reuses_only_its_own_cached_state(tmp_path):
    maker = tmp_path / "maker.json"
    maker.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    browser = _LoginBrowser()
    state_dir = tmp_path / "dynamic"
    pool = RoleSessionPool(browser, (RoleSessionSpec("maker", maker),))

    first = pool.page_for_login(
        "approver01",
        entry_url="https://ei.example/ei-view/#/todo",
        password="fixed-password",
        state_dir=state_dir,
    )

    assert first is pool.page_for_login(
        "approver01",
        entry_url="https://ei.example/ei-view/#/todo",
        password="fixed-password",
        state_dir=state_dir,
    )
    assert len(browser.context_args) == 1
    assert "storage_state" not in browser.context_args[0]
    assert browser.contexts[0].page.login.filled == ["approver01"]
    assert browser.contexts[0].page.password.filled == ["fixed-password"]
    assert len(list(state_dir.glob("login-*.json"))) == 1

    second_browser = _LoginBrowser()
    second_pool = RoleSessionPool(
        second_browser, (RoleSessionSpec("maker", maker),)
    )
    second_pool.page_for_login(
        "approver01",
        entry_url="https://ei.example/ei-view/#/todo",
        password="fixed-password",
        state_dir=state_dir,
    )

    assert "storage_state" in second_browser.context_args[0]
    assert second_browser.contexts[0].page.login.filled == []


def test_dynamic_login_rejects_display_name_and_never_creates_a_context(tmp_path):
    maker = tmp_path / "maker.json"
    maker.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    browser = _LoginBrowser()
    pool = RoleSessionPool(browser, (RoleSessionSpec("maker", maker),))

    with pytest.raises(WorkflowConfigurationError, match="唯一登录名"):
        pool.page_for_login(
            "审批人 张三",
            entry_url="https://ei.example/ei-view/#/todo",
            password="fixed-password",
            state_dir=tmp_path / "dynamic",
        )

    assert browser.context_args == []


def test_dynamic_login_discards_an_expired_cached_state_and_logs_in_again(tmp_path):
    maker = tmp_path / "maker.json"
    maker.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    state_dir = tmp_path / "dynamic"
    state_dir.mkdir()
    (state_dir / "login-497a0926312b7582a258c419.json").write_text(
        '{"cookies": [], "origins": []}', encoding="utf-8"
    )
    browser = _LoginBrowser(cached_state_authenticates=False)
    pool = RoleSessionPool(browser, (RoleSessionSpec("maker", maker),))

    pool.page_for_login(
        "approver01",
        entry_url="https://ei.example/ei-view/#/todo",
        password="fixed-password",
        state_dir=state_dir,
    )

    assert "storage_state" in browser.context_args[0]
    assert "storage_state" not in browser.context_args[1]
    assert browser.contexts[0].page.closed is True
    assert browser.contexts[1].page.login.filled == ["approver01"]


def test_roles_reuse_their_own_page_but_use_isolated_contexts(tmp_path):
    events = []
    browser = _Browser(events)
    initialized = []

    def initialize(context, role):
        initialized.append((context.role, role))
        events.append(("initialize", role))

    pool = RoleSessionPool(
        browser,
        _specs(tmp_path),
        context_initializer=initialize,
    )

    maker = pool.page_for("maker")
    assert pool.page_for("maker") is maker
    approver = pool.page_for("approver")

    assert maker is not approver
    assert pool.context_for("maker") is not pool.context_for("approver")
    assert initialized == [("maker", "maker"), ("approver", "approver")]
    assert events.index(("initialize", "maker")) < events.index(("new_page", "maker"))
    assert pool.current_role == "approver"
    assert pool.current_page is approver


def test_role_validation_happens_before_new_context(tmp_path):
    events = []
    browser = _Browser(events)
    maker = tmp_path / "maker.json"
    maker.write_text("{}", encoding="utf-8")
    pool = RoleSessionPool(browser, (RoleSessionSpec("maker", maker),))

    with pytest.raises(WorkflowConfigurationError, match="approver"):
        pool.ensure_roles(("maker", "approver"))

    assert events == []

    missing = RoleSessionPool(
        browser, (RoleSessionSpec("maker", tmp_path / "missing.json"),)
    )
    with pytest.raises(WorkflowConfigurationError, match="maker") as error:
        missing.ensure_roles(("maker",))
    assert "missing.json" not in str(error.value)
    assert events == []


def test_role_validation_rejects_invalid_storage_state_json_without_leaking_it(
    tmp_path,
):
    state = tmp_path / "token-and-cookie-state.json"
    state.write_text("token=top-secret", encoding="utf-8")

    with pytest.raises(WorkflowConfigurationError, match="maker") as error:
        RoleSessionPool(
            _Browser([]), (RoleSessionSpec("maker", state),)
        ).ensure_roles(("maker",))

    assert "top-secret" not in str(error.value)
    assert state.name not in str(error.value)


def test_role_validation_rejects_non_object_storage_state_json(tmp_path):
    state = tmp_path / "maker.json"
    state.write_text("[]", encoding="utf-8")

    with pytest.raises(WorkflowConfigurationError, match="JSON 无效"):
        RoleSessionPool(
            _Browser([]), (RoleSessionSpec("maker", state),)
        ).ensure_roles(("maker",))


@pytest.mark.parametrize("payload", ('{"cookies": {}}', '{"origins": {}}'))
def test_role_validation_rejects_non_list_storage_state_fields(tmp_path, payload):
    state = tmp_path / "maker.json"
    state.write_text(payload, encoding="utf-8")

    with pytest.raises(WorkflowConfigurationError, match="JSON 无效"):
        RoleSessionPool(
            _Browser([]), (RoleSessionSpec("maker", state),)
        ).ensure_roles(("maker",))


def test_role_validation_rejects_expired_jwt_before_new_context_without_leak(
    tmp_path,
):
    events = []
    token = _jwt(expires=1)
    state = tmp_path / "maker-token-and-cookie-state.json"
    state.write_text(
        json.dumps(_state_with_local_storage("accessToken", token)),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowConfigurationError, match="已过期.*maker") as error:
        RoleSessionPool(
            _Browser(events), (RoleSessionSpec("maker", state),)
        ).ensure_roles(("maker",))

    message = str(error.value)
    assert events == []
    assert token not in message
    assert token.split(".")[1] not in message
    assert state.name not in message


def test_role_validation_accepts_unexpired_jwt(tmp_path):
    events = []
    state = tmp_path / "maker.json"
    state.write_text(
        json.dumps(
            _state_with_local_storage(
                "accessToken", _jwt(expires=4_102_444_800)
            )
        ),
        encoding="utf-8",
    )

    RoleSessionPool(
        _Browser(events), (RoleSessionSpec("maker", state),)
    ).ensure_roles(("maker",))

    assert events[0] == ("new_context", "maker", True)


@pytest.mark.parametrize(
    "token",
    (
        "opaque-auth-token",
        "not-json.not-json.signature",
        _jwt(expires="1"),
    ),
)
def test_role_validation_keeps_unparseable_or_indeterminate_tokens_compatible(
    tmp_path, token
):
    events = []
    state = tmp_path / "maker.json"
    state.write_text(
        json.dumps(_state_with_local_storage("accessToken", token)),
        encoding="utf-8",
    )

    RoleSessionPool(
        _Browser(events), (RoleSessionSpec("maker", state),)
    ).ensure_roles(("maker",))

    assert events[0] == ("new_context", "maker", True)


def test_role_validation_rejects_jwt_whose_cookie_has_expired(tmp_path):
    events = []
    state = tmp_path / "maker.json"
    state.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "AUTH",
                        "value": _jwt(expires=4_102_444_800),
                        "expires": 1,
                    }
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkflowConfigurationError, match="已过期.*maker"):
        RoleSessionPool(
            _Browser(events), (RoleSessionSpec("maker", state),)
        ).ensure_roles(("maker",))

    assert events == []


def test_role_validation_does_not_treat_expired_opaque_cookie_as_expired_jwt(
    tmp_path,
):
    events = []
    state = tmp_path / "maker.json"
    state.write_text(
        json.dumps(
            {
                "cookies": [
                    {"name": "SESSION", "value": "opaque", "expires": 1}
                ],
                "origins": [],
            }
        ),
        encoding="utf-8",
    )

    RoleSessionPool(
        _Browser(events), (RoleSessionSpec("maker", state),)
    ).ensure_roles(("maker",))

    assert events[0] == ("new_context", "maker", True)


@pytest.mark.parametrize(
    ("maker_claims", "approver_claims"),
    (
        ({"sub": "shared-principal-secret"}, {"sub": "shared-principal-secret"}),
        (
            {"userId": "shared-principal-secret"},
            {"user_id": "shared-principal-secret"},
        ),
    ),
)
def test_role_validation_rejects_same_stable_jwt_principal_without_leak(
    tmp_path, maker_claims, approver_claims
):
    events = []
    tokens = {
        "maker": _jwt(expires=4_102_444_800, claims=maker_claims),
        "approver": _jwt(expires=4_102_444_800, claims=approver_claims),
    }
    specs = []
    for role, token in tokens.items():
        payload = _state_with_local_storage("accessToken", token)
        payload["origins"][0]["localStorage"].append(
            {"name": "workflowRole", "value": role}
        )
        state = tmp_path / f"{role}-token-and-cookie-state.json"
        state.write_text(json.dumps(payload), encoding="utf-8")
        specs.append(RoleSessionSpec(role, state))

    with pytest.raises(
        WorkflowConfigurationError, match="不同认证主体.*maker.*approver"
    ) as error:
        RoleSessionPool(_Browser(events), specs).ensure_roles(
            ("maker", "approver")
        )

    message = str(error.value)
    assert events == []
    assert "shared-principal-secret" not in message
    assert all(token not in message for token in tokens.values())
    assert all(Path(spec.storage_state).name not in message for spec in specs)


def test_role_validation_accepts_different_stable_jwt_principals(tmp_path):
    events = []
    specs = []
    for role in ("maker", "approver"):
        state = tmp_path / f"{role}.json"
        state.write_text(
            json.dumps(
                _state_with_local_storage(
                    "accessToken",
                    _jwt(
                        expires=4_102_444_800,
                        claims={"sub": f"{role}-principal"},
                    ),
                )
            ),
            encoding="utf-8",
        )
        specs.append(RoleSessionSpec(role, state))

    RoleSessionPool(_Browser(events), specs).ensure_roles(("maker", "approver"))

    assert [event[:2] for event in events if event[0] == "new_context"] == [
        ("new_context", "maker"),
        ("new_context", "approver"),
    ]


def test_role_validation_does_not_compare_unreliable_jwt_claims(tmp_path):
    events = []
    specs = []
    for role in ("maker", "approver"):
        payload = _state_with_local_storage(
            "accessToken",
            _jwt(
                expires=4_102_444_800,
                claims={"name": "Shared Display Name"},
            ),
        )
        payload["origins"][0]["localStorage"].append(
            {"name": "workflowRole", "value": role}
        )
        state = tmp_path / f"{role}.json"
        state.write_text(json.dumps(payload), encoding="utf-8")
        specs.append(RoleSessionSpec(role, state))

    RoleSessionPool(_Browser(events), specs).ensure_roles(("maker", "approver"))

    assert sum(event[0] == "new_context" for event in events) == 2


def test_role_validation_rejects_identical_states_before_browser_start(tmp_path):
    events = []
    maker = tmp_path / "maker.json"
    approver = tmp_path / "approver.json"
    state = '{"origins": [], "cookies": []}'
    maker.write_text(state, encoding="utf-8")
    approver.write_text('{ "cookies": [], "origins": [] }', encoding="utf-8")

    pool = RoleSessionPool(
        _Browser(events),
        (
            RoleSessionSpec("maker", maker),
            RoleSessionSpec("approver", approver),
        ),
    )

    with pytest.raises(WorkflowConfigurationError, match="独立登录态") as error:
        pool.ensure_roles(("maker", "approver"))

    assert events == []
    assert maker.name not in str(error.value)
    assert approver.name not in str(error.value)


def test_ensure_roles_prewarms_every_role_without_selecting_one(tmp_path):
    events = []
    browser = _Browser(events)
    pool = RoleSessionPool(browser, _specs(tmp_path))

    pool.ensure_roles(("maker", "approver"))

    assert [event[:2] for event in events if event[0] == "new_context"] == [
        ("new_context", "maker"),
        ("new_context", "approver"),
    ]
    assert [event for event in events if event[0] == "new_page"] == [
        ("new_page", "maker"),
        ("new_page", "approver"),
    ]
    assert pool.current_role == ""
    assert pool.current_page is None

    opened_contexts = len(browser.contexts)
    pool.page_for("maker")
    pool.page_for("approver")
    assert len(browser.contexts) == opened_contexts


def test_role_prewarm_failure_rolls_back_all_new_sessions(tmp_path):
    events = []
    browser = _Browser(events)
    browser.fail_new_context_for.add("approver")
    pool = RoleSessionPool(browser, _specs(tmp_path))

    with pytest.raises(WorkflowSessionError, match="approver") as error:
        pool.ensure_roles(("maker", "approver"))

    assert "secret" not in str(error.value)
    assert ("page.close", "maker") in events
    assert ("context.close", "maker") in events
    assert pool.current_role == ""
    assert pool.current_page is None
    assert "opened_roles=[]" in repr(pool)


def test_initializer_failure_closes_partial_context_without_caching_it(tmp_path):
    events = []
    browser = _Browser(events)

    def initialize(_context, _role):
        raise RuntimeError("token=secret")

    pool = RoleSessionPool(
        browser,
        _specs(tmp_path),
        context_initializer=initialize,
    )

    with pytest.raises(WorkflowSessionError, match="maker") as error:
        pool.page_for("maker")

    assert "secret" not in str(error.value)
    assert events[-1] == ("context.close", "maker")
    assert pool.current_page is None
    assert "maker" not in repr(pool).split("opened_roles=")[1]


def test_initializer_control_flow_exception_is_not_wrapped_and_is_cleaned_up(
    tmp_path,
):
    class StopWorkflow(BaseException):
        pass

    events = []

    def stop(_context, _role):
        raise StopWorkflow()

    pool = RoleSessionPool(
        _Browser(events),
        _specs(tmp_path),
        context_initializer=stop,
    )

    with pytest.raises(StopWorkflow):
        pool.page_for("maker")

    assert events[-1] == ("context.close", "maker")
    assert pool.current_role == ""
    assert pool.current_page is None
    assert "opened_roles=[]" in repr(pool)


def test_failed_role_switch_clears_current_page_without_discarding_prior_role(
    tmp_path,
):
    events = []
    browser = _Browser(events)
    pool = RoleSessionPool(browser, _specs(tmp_path))
    maker = pool.page_for("maker")
    browser.fail_new_context_for.add("approver")

    with pytest.raises(WorkflowSessionError, match="approver"):
        pool.page_for("approver")

    assert pool.current_role == ""
    assert pool.current_page is None
    assert maker.closed is False
    assert "opened_roles=['maker']" in repr(pool)


def test_new_page_failure_closes_partial_context_and_hides_original_error(tmp_path):
    events = []
    browser = _Browser(events)
    browser.fail_new_page_for.add("maker")
    pool = RoleSessionPool(browser, _specs(tmp_path))

    with pytest.raises(WorkflowSessionError, match="maker") as error:
        pool.page_for("maker")

    assert "secret path" not in str(error.value)
    assert events[-2:] == [("new_page", "maker"), ("context.close", "maker")]


def test_close_releases_pages_contexts_in_reverse_order_then_browser(tmp_path):
    events = []
    browser = _Browser(events)
    browser.fail_page_close_for.add("approver")
    browser.fail_context_close_for.add("maker")
    pool = RoleSessionPool(browser, _specs(tmp_path), owns_browser=True)
    pool.page_for("maker")
    pool.page_for("approver")
    events.clear()

    pool.close()
    pool.close()

    assert events == [
        ("page.close", "approver"),
        ("context.close", "approver"),
        ("page.close", "maker"),
        ("context.close", "maker"),
        ("browser.close", "browser"),
    ]
    assert pool.current_page is None


def test_closed_role_page_is_not_silently_recreated(tmp_path):
    events = []
    pool = RoleSessionPool(_Browser(events), _specs(tmp_path))
    page = pool.page_for("maker")
    page.closed = True

    with pytest.raises(WorkflowSessionError, match="禁止.*重建"):
        pool.page_for("maker")

    assert sum(event[0] == "new_context" for event in events) == 1


def test_pool_repr_and_errors_never_include_storage_state_paths(tmp_path):
    secret_path = tmp_path / "token-and-cookie-state.json"
    secret_path.write_text("{}", encoding="utf-8")
    pool = RoleSessionPool(
        _Browser([]), (RoleSessionSpec("maker", secret_path),)
    )

    assert "token-and-cookie" not in repr(pool)
    assert "storage_state=" not in repr(RoleSessionSpec("maker", secret_path))


def test_role_state_json_accepts_only_a_nonempty_role_to_path_object(tmp_path):
    state = tmp_path / "maker.json"
    state.write_text("{}", encoding="utf-8")

    specs = parse_role_session_specs(
        '{"maker": "maker.json"}', base_dir=tmp_path
    )
    assert specs == (RoleSessionSpec("maker", state.resolve()),)

    for invalid in ("[]", "{}", '{"maker": {"cookies": []}}'):
        with pytest.raises(WorkflowConfigurationError):
            parse_role_session_specs(invalid, base_dir=tmp_path)

    with pytest.raises(WorkflowConfigurationError, match="重复"):
        RoleSessionPool(
            _Browser([]),
            (
                RoleSessionSpec("maker", state),
                RoleSessionSpec("maker", state),
            ),
        )

    with pytest.raises(WorkflowConfigurationError, match="重复"):
        parse_role_session_specs(
            '{"maker": "maker.json", "maker": "maker.json"}',
            base_dir=tmp_path,
        )

    pool = RoleSessionPool(_Browser([]), specs)
    with pytest.raises(WorkflowConfigurationError, match="不能为空"):
        pool.page_for("")
