from types import SimpleNamespace

import pytest

from ei_ui_smoke.menu_capture import (
    MENU_API_MARKER,
    _capture_detail_father_trees,
    _menu_leaves,
    _prepare_login,
    _storage_state_output_path,
    capture_menu,
)


class _Response:
    ok = True

    def json(self):
        return {"data": [{"funcCode": "OVERVIEW"}]}


class _RequestClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _Context:
    def __init__(self):
        self.request = _RequestClient()


def test_menu_leaves_yield_only_visible_leaf_routes():
    nodes = [{"funcCode": "PROJECT", "path": "/project", "children": [
        {"funcCode": "POOL", "path": "pool", "children": []},
        {"funcCode": "HIDDEN", "path": "hidden", "meta": {"hidden": True}, "children": []},
    ]}]

    assert list(_menu_leaves(nodes)) == [("POOL", "/project/pool")]


def test_father_detail_tree_capture_reuses_menu_auth_and_source_proxy_path(tmp_path):
    view = tmp_path / "ei-view"
    views = view / "src" / "views" / "projectManage" / "before"
    views.mkdir(parents=True)
    (view / ".env").write_text("VITE_APP_BASE_API=/ezgo\n", encoding="utf-8")
    (views / "detail.vue").write_text(
        'CommonAPI.getUserFuncPermTree({ fatherId: "30021" })', encoding="utf-8"
    )
    after = view / "src" / "views" / "projectManage" / "after" / "afterManage.vue"
    after.parent.mkdir(parents=True)
    after.write_text(
        '''const fatherId = computed(() => String(route.query.fatherId || "30022"));
        CommonAPI.getUserFuncPermTree({ fatherId: fatherId.value })''',
        encoding="utf-8",
    )
    context = _Context()

    trees = _capture_detail_father_trees(
        context,
        origin="https://example.test",
        app_id="10015",
        auth_headers={"authorization": "masked", "x-tenant-id": "tenant"},
        source_root=tmp_path,
        timeout_ms=1234,
    )

    assert trees == [{
        "fatherId": "30022",
        "sourceComponent": "projectManage/after/afterManage",
        "nodes": [{"funcCode": "OVERVIEW"}],
    }, {
        "fatherId": "30021",
        "sourceComponent": "projectManage/before/detail",
        "nodes": [{"funcCode": "OVERVIEW"}],
    }]
    assert context.request.calls == [(
        "https://example.test/ezgo/ei-service/funcPerm/getUserFuncPermTree",
        {
            "params": {"appId": "10015", "fatherId": "30022"},
            "headers": {"authorization": "masked", "x-tenant-id": "tenant"},
            "timeout": 1234,
        },
    ), (
        "https://example.test/ezgo/ei-service/funcPerm/getUserFuncPermTree",
        {
            "params": {"appId": "10015", "fatherId": "30021"},
            "headers": {"authorization": "masked", "x-tenant-id": "tenant"},
            "timeout": 1234,
        },
    )]


class _LoginControl:
    def __init__(self, *, count=1, visible=True):
        self._count = count
        self._visible = visible
        self.filled = []
        self.clicks = 0

    @property
    def first(self):
        return self

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def fill(self, value):
        self.filled.append(value)

    def click(self):
        self.clicks += 1


class _LoginPage:
    def __init__(self):
        self.login = _LoginControl()
        self.password = _LoginControl()
        self.submit = _LoginControl()

    def locator(self, selector):
        if "loginName" in selector:
            return self.login
        if "password" in selector:
            return self.password
        return self.submit


def test_visible_menu_capture_allows_manual_login_but_keeps_automatic_login():
    manual_page = _LoginPage()

    assert _prepare_login(
        manual_page, username="", password="", headless=False
    ) == "manual"
    assert manual_page.login.filled == []
    assert manual_page.password.filled == []
    assert manual_page.submit.clicks == 0

    automatic_page = _LoginPage()
    assert _prepare_login(
        automatic_page,
        username="maker",
        password="secret",
        headless=False,
    ) == "automatic"
    assert automatic_page.login.filled == ["maker"]
    assert automatic_page.password.filled == ["secret"]
    assert automatic_page.submit.clicks == 1

    with pytest.raises(ValueError, match="无头浏览器无法完成人工登录"):
        _prepare_login(
            _LoginPage(), username="", password="", headless=True
        )


def test_storage_state_output_creates_only_the_explicit_parent(tmp_path):
    explicit = tmp_path / "workflow-auth" / "approval-maker.json"

    resolved = _storage_state_output_path(explicit)

    assert resolved == explicit
    assert explicit.parent.is_dir()
    assert not explicit.exists()


class _MenuResponse:
    ok = True
    status = 200
    url = f"https://example.test{MENU_API_MARKER}"
    request = SimpleNamespace(headers={"authorization": "masked"})

    def json(self):
        return {"data": []}


class _ExpectedResponse:
    def __init__(self, response, predicate):
        assert predicate(response)
        self.value = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _CapturePage:
    url = "https://example.test/ei-view/#/home"

    def __init__(self, response):
        self.response = response
        self.listeners = {}

    def on(self, name, callback):
        self.listeners[name] = callback

    def expect_response(self, predicate, *, timeout):
        assert timeout == 1234
        return _ExpectedResponse(self.response, predicate)

    def goto(self, url, *, wait_until):
        assert url == "https://example.test/ei-view/#/login"
        assert wait_until == "domcontentloaded"

    def locator(self, _selector):
        return _LoginControl(count=0, visible=False)


class _CaptureContext(_Context):
    def __init__(self, response):
        super().__init__()
        self.page = _CapturePage(response)
        self.saved_paths = []

    def new_page(self):
        return self.page

    def storage_state(self, *, path):
        self.saved_paths.append(path)

    def close(self):
        return None


class _CaptureBrowser:
    def __init__(self, context):
        self.context = context

    def new_context(self, **kwargs):
        assert kwargs == {"ignore_https_errors": True}
        return self.context

    def close(self):
        return None


class _PlaywrightManager:
    def __init__(self, browser):
        self.playwright = SimpleNamespace(
            chromium=SimpleNamespace(
                launch=lambda **kwargs: (
                    browser if kwargs == {"headless": False} else None
                )
            )
        )

    def __enter__(self):
        return self.playwright

    def __exit__(self, *_args):
        return False


def test_capture_menu_writes_to_explicit_storage_state_path(
    monkeypatch, tmp_path
):
    import playwright.sync_api as playwright_sync

    response = _MenuResponse()
    context = _CaptureContext(response)
    browser = _CaptureBrowser(context)
    monkeypatch.setattr(
        playwright_sync,
        "sync_playwright",
        lambda: _PlaywrightManager(browser),
    )
    output = tmp_path / "roles" / "maker.json"

    _payload, saved_path = capture_menu(
        "https://example.test/ei-view/#/login",
        headless=False,
        timeout_ms=1234,
        save_storage_state_path=output,
    )

    assert output.parent.is_dir()
    assert saved_path == str(output)
    assert context.saved_paths == [str(output)]
