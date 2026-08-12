import pytest
from types import SimpleNamespace

import ei_ui_smoke.detail_navigation as detail_navigation


class _StateLocator:
    def __init__(self, page, *, loading=False):
        self.page = page
        self.loading = loading

    def count(self):
        state = self.page.states[self.page.index]
        return int(state[0]) if self.loading else len(state[1])

    def all_inner_texts(self):
        return list(self.page.states[self.page.index][1])


class _StatePage:
    url = "https://example.test/list"

    def __init__(self, states):
        self.states = states
        self.index = 0
        self.waits = 0

    def locator(self, selector):
        return _StateLocator(
            self, loading=selector == detail_navigation._PARENT_LOADING_SELECTOR
        )

    def wait_for_timeout(self, _milliseconds):
        self.waits += 1
        self.index = min(self.index + 1, len(self.states) - 1)


class _EmptyDetailAction:
    @property
    def first(self):
        return self

    def filter(self, **_kwargs):
        return self

    def count(self):
        return 0

    def is_visible(self):
        return False


class _UnstableRecord:
    def __init__(self):
        self.click_timeout = None

    def locator(self, _selector):
        return _EmptyDetailAction()

    def click(self, *, timeout):
        self.click_timeout = timeout
        raise RuntimeError("row replaced during refresh")


class _ReadyCandidates:
    def __init__(self, record):
        self.record = record

    def count(self):
        return 1

    def nth(self, _index):
        return self.record


class _ClickPage:
    url = "https://example.test/list"

    def goto(self, url, *, wait_until):
        self.url = url
        assert wait_until == "domcontentloaded"

    def locator(self, _selector):
        return _StateLocator(_StatePage([(False, [])]), loading=True)


class _DisabledAction:
    def __init__(self):
        self.waits = 0

    def count(self):
        return 1

    def nth(self, _index):
        return self

    def inner_text(self):
        return "新增"

    def is_visible(self):
        return True

    def is_enabled(self):
        return False


class _DisabledActionPage:
    def __init__(self):
        self.action = _DisabledAction()

    def locator(self, _selector):
        return self.action

    def wait_for_timeout(self, _milliseconds):
        self.action.waits += 1


class _NameLink:
    clicked = False

    def count(self):
        return 1

    def nth(self, index):
        assert index == 0
        return self

    def is_visible(self):
        return True

    def is_enabled(self):
        return True

    def inner_text(self):
        return "AUTO_项目名称"

    def get_attribute(self, _name):
        return ""

    def click(self, *, timeout):
        self.clicked = True
        self.timeout = timeout


class _RecordWithNameLink:
    def __init__(self):
        self.link = _NameLink()

    def locator(self, selector):
        if "has-text(\"详情\")" in selector or "has-text(\"查看\")" in selector:
            return _EmptyDetailAction()
        if "a:visible" in selector:
            return self.link
        return _EmptyDetailAction()


def test_parent_list_readiness_ignores_stale_rows_behind_loading_mask():
    page = _StatePage([
        (True, ["stale row"]),
        (True, ["stale row"]),
        (False, ["current row"]),
        (False, ["current row"]),
        (False, ["current row"]),
    ])

    candidates = detail_navigation._wait_for_parent_records_ready(
        page, timeout=1_000, poll_ms=1, stable_polls=3
    )

    assert candidates.all_inner_texts() == ["current row"]
    assert page.waits == 4


def test_detail_record_uses_business_name_link_when_no_detail_button():
    record = _RecordWithNameLink()

    target = detail_navigation._record_detail_entry(record)

    assert target is record.link


def test_detail_record_click_timeout_becomes_bounded_readiness_failure(monkeypatch):
    page = _ClickPage()
    record = _UnstableRecord()
    monkeypatch.setattr(
        detail_navigation,
        "_wait_for_parent_records_ready",
        lambda _page: _ReadyCandidates(record),
    )

    with pytest.raises(
        detail_navigation.ParentListNotReadyError,
        match="持续刷新或被加载遮罩阻挡",
    ):
        detail_navigation.enter_detail_record(
            page, "https://example.test/list/detail", record_index=0
        )

    assert record.click_timeout == 10_000


def test_detail_navigation_accepts_list_as_detail_when_action_is_visible(monkeypatch):
    class MissingTab:
        @property
        def first(self):
            return self

        def filter(self, **_kwargs):
            return self

        def wait_for(self, **_kwargs):
            raise TimeoutError("no detail tab")

    class Page:
        def locator(self, _selector):
            return MissingTab()

    monkeypatch.setattr(
        detail_navigation,
        "visible_action",
        lambda _page, action, timeout=15_000: object() if action == "编辑" else None,
    )

    detail_navigation.navigate_detail_module(
        Page(),
        "建设项目/详情/投前管理/项目立项/编辑",
        "编辑",
        navigation_labels=["投前管理", "项目立项"],
        timeout=1,
    )


def test_detail_entry_falls_back_to_current_page_when_url_does_not_change(monkeypatch):
    attempts = []
    navigations = []

    def no_url_change(_page, _url, record_index=0):
        attempts.append(record_index)
        raise AssertionError("已点击父列表记录，但未进入有效详情页；当前地址：/buildProject")

    def navigate(_page, _module_name, _action, **kwargs):
        navigations.append(kwargs["timeout"])

    monkeypatch.setattr(detail_navigation, "enter_detail_record", no_url_change)
    monkeypatch.setattr(detail_navigation, "navigate_detail_module", navigate)

    detail_navigation.enter_available_detail_module(
        object(),
        "/buildProject/detail",
        "建设项目/详情/投前管理/项目立项/编辑",
        "编辑",
    )

    assert attempts == [0]
    assert navigations == [2_000]


def test_parent_list_readiness_failure_does_not_retry_every_record(monkeypatch):
    attempts = []

    def fail_once(_page, _url, record_index=0):
        attempts.append(record_index)
        raise detail_navigation.ParentListNotReadyError("详情父列表未就绪")

    monkeypatch.setattr(detail_navigation, "enter_detail_record", fail_once)

    with pytest.raises(AssertionError, match="详情父列表未就绪"):
        detail_navigation.enter_available_detail_module(
            object(), "/list/detail", "建设项目/详情/项目决策/新增", "新增"
        )

    assert attempts == [0]


def test_visible_disabled_action_returns_after_short_grace_period():
    page = _DisabledActionPage()

    result = detail_navigation.visible_action(
        page, "新增", timeout=20_000, disabled_grace_ms=0
    )

    assert result is None
    assert page.action.waits == 1


def test_detail_action_stops_after_the_rendered_target_is_stably_missing(monkeypatch):
    calls = []
    clock = type("Clock", (), {"value": 0.0})()

    class Target:
        @property
        def last(self):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def inner_text(self):
            return "项目运行信息 暂无数据"

    class Page:
        def locator(self, selector):
            if selector == detail_navigation._DETAIL_TARGET_LOADING_SELECTOR:
                return type("Loading", (), {"count": staticmethod(lambda: 0)})()
            return Target()

        def wait_for_timeout(self, _milliseconds):
            clock.value += _milliseconds / 1_000

    monkeypatch.setattr(
        detail_navigation,
        "visible_action",
        lambda _page, action, timeout=15_000: calls.append((action, timeout)) or None,
    )
    monkeypatch.setattr(detail_navigation.time, "monotonic", lambda: clock.value)

    assert detail_navigation._wait_for_detail_action_or_stable_absence(
        Page(), "新增", timeout=20_000, stable_grace_ms=0
    ) is None
    assert calls == [("新增", 1_000), ("新增", 1_000)]


def test_detail_action_keeps_waiting_when_target_content_is_not_rendered(monkeypatch):
    calls = []
    clock = type("Clock", (), {"value": 0.0})()

    class EmptyTarget:
        def count(self):
            return 0

    class Page:
        def locator(self, _selector):
            return EmptyTarget()

        def wait_for_timeout(self, _milliseconds):
            clock.value += _milliseconds / 1_000

    monkeypatch.setattr(detail_navigation.time, "monotonic", lambda: clock.value)
    monkeypatch.setattr(
        detail_navigation,
        "visible_action",
        lambda _page, action, timeout=15_000: calls.append((action, timeout)) or None,
    )

    assert detail_navigation._wait_for_detail_action_or_stable_absence(
        Page(), "新增", timeout=1_000, stable_grace_ms=0
    ) is None
    assert calls == [("新增", 1_000), ("新增", 750), ("新增", 500), ("新增", 250)]


def test_detail_record_absolute_index_moves_to_later_parent_page(monkeypatch):
    class Candidates:
        def count(self):
            return 5

        def all_inner_texts(self):
            return [f"record-{index}" for index in range(5)]

    class Page:
        url = "https://example.test/list"

        def goto(self, url, *, wait_until):
            self.url = url
            assert wait_until == "domcontentloaded"

    page = Page()
    candidates = Candidates()
    monkeypatch.setattr(
        detail_navigation,
        "_wait_for_parent_records_ready",
        lambda _page: candidates,
    )
    monkeypatch.setattr(
        detail_navigation, "_parent_total_record_count", lambda _page: 20
    )

    def goto_page(_page, page_number, previous_snapshot):
        assert page_number == 3
        assert previous_snapshot == tuple(
            f"record-{index}" for index in range(5)
        )
        raise RuntimeError("pagination reached")

    monkeypatch.setattr(
        detail_navigation, "_goto_parent_record_page", goto_page
    )

    with pytest.raises(RuntimeError, match="pagination reached"):
        detail_navigation.enter_detail_record(
            page, "https://example.test/list/detail", record_index=12
        )


def test_empty_parent_list_provisions_and_opens_exact_created_record(monkeypatch):
    attempts = []
    navigations = []
    provisioned = SimpleNamespace(
        business_id="9001", record_markers=("AUTO_detail_parent",),
    )

    def enter(_page, _url, record_index=0, *, record_identity=None):
        attempts.append((record_index, record_identity))
        if record_identity is None:
            raise detail_navigation.ParentListEmptyError("parent list is empty")
        assert record_identity is provisioned

    monkeypatch.setattr(detail_navigation, "enter_detail_record", enter)
    monkeypatch.setattr(
        detail_navigation,
        "navigate_detail_module",
        lambda _page, _module_name, action, **_kwargs: navigations.append(action),
    )

    result = detail_navigation.enter_available_detail_module(
        object(),
        "/buildProject/detail",
        "建设项目/详情/项目决策/新增",
        "新增",
        provision_record=lambda: provisioned,
    )

    assert result is provisioned
    assert attempts == [(0, None), (0, provisioned)]
    assert navigations == ["新增"]


def test_exhausted_parent_records_provisions_an_isolated_parent(monkeypatch):
    provisioned = SimpleNamespace(
        business_id="9001", record_markers=("AUTO_detail_parent",),
    )
    attempts = []

    def enter(_page, _url, record_index=0, *, record_identity=None):
        attempts.append((record_index, record_identity))
        if record_identity is None:
            raise AssertionError("目标子模块暂无数据")

    monkeypatch.setattr(detail_navigation, "enter_detail_record", enter)
    monkeypatch.setattr(detail_navigation, "navigate_detail_module", lambda *_args, **_kwargs: None)

    result = detail_navigation.enter_available_detail_module(
        object(),
        "/buildProject/detail",
        "建设项目/详情/项目决策/新增",
        "新增",
        max_records=1,
        provision_record=lambda: provisioned,
    )

    assert result is provisioned
    assert attempts == [(0, None), (0, provisioned)]


def test_exhausted_non_data_failure_does_not_provision_parent(monkeypatch):
    calls = []

    monkeypatch.setattr(
        detail_navigation,
        "enter_detail_record",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("目标操作没有权限")
        ),
    )

    with pytest.raises(AssertionError, match="目标操作没有权限"):
        detail_navigation.enter_available_detail_module(
            object(),
            "/buildProject/detail",
            "建设项目/详情/项目决策/编辑",
            "编辑",
            max_records=1,
            provision_record=lambda: calls.append("provision"),
        )

    assert calls == []


def test_provisioned_parent_seeds_child_before_edit_retry(monkeypatch):
    parent = SimpleNamespace(
        business_id="9001", record_markers=("AUTO_detail_parent",),
    )
    child = SimpleNamespace(
        business_id="9002", record_markers=("AUTO_detail_child",),
    )
    events = []

    def enter(_page, _url, record_index=0, *, record_identity=None):
        events.append(("enter", record_index, record_identity))
        if record_identity is None:
            raise AssertionError("编辑操作没有可用子记录；目标子表暂无数据")

    def navigate(_page, _module_name, action, **_kwargs):
        events.append(("navigate", action))
        if action == "编辑" and events.count(("navigate", "编辑")) == 1:
            raise AssertionError("编辑操作没有可用子记录")

    monkeypatch.setattr(detail_navigation, "enter_detail_record", enter)
    monkeypatch.setattr(detail_navigation, "navigate_detail_module", navigate)

    result = detail_navigation.enter_available_detail_module(
        object(),
        "/buildProject/detail",
        "建设项目/详情/项目决策/编辑",
        "编辑",
        max_records=1,
        provision_record=lambda: parent,
        provision_child_record=lambda: events.append(("seed-child",)) or child,
    )

    assert result is parent
    assert events == [
        ("enter", 0, None),
        ("enter", 0, parent),
        ("navigate", "编辑"),
        ("navigate", "新增"),
        ("seed-child",),
        ("enter", 0, parent),
        ("navigate", "编辑"),
    ]


def test_detail_context_preparer_reuses_first_successful_parent_identity(
    monkeypatch,
):
    monkeypatch.setenv("EI_REQUIRES_BUSINESS_ID", "true")
    monkeypatch.setenv("EI_FORM_URL", "/buildProject/detail")
    monkeypatch.setenv("EI_MODULE_NAME", "建设项目/详情/项目决策/新增")
    monkeypatch.setenv("EI_ACTION", "新增")
    calls = []

    def enter(*_args, **kwargs):
        calls.append(kwargs.get("record_identity"))
        return ("AUTO_parent_001",)

    monkeypatch.setattr(detail_navigation, "enter_available_detail_module", enter)
    prepare = detail_navigation.detail_context_preparer_from_env()

    assert prepare is not None
    prepare(object())
    prepare(object())

    assert calls == [None, ("AUTO_parent_001",)]


def test_detail_context_preparer_rescans_only_after_cached_identity_disappears(
    monkeypatch,
):
    monkeypatch.setenv("EI_REQUIRES_BUSINESS_ID", "true")
    monkeypatch.setenv("EI_FORM_URL", "/buildProject/detail")
    monkeypatch.setenv("EI_MODULE_NAME", "建设项目/详情/项目决策/新增")
    monkeypatch.setenv("EI_ACTION", "新增")
    first = ("AUTO_parent_001",)
    replacement = ("AUTO_parent_002",)
    calls = []

    def enter(*_args, **kwargs):
        identity = kwargs.get("record_identity")
        calls.append(identity)
        if identity == first:
            raise detail_navigation.ParentRecordIdentityUnavailableError("missing")
        return first if len(calls) == 1 else replacement

    monkeypatch.setattr(detail_navigation, "enter_available_detail_module", enter)
    prepare = detail_navigation.detail_context_preparer_from_env()

    prepare(object())
    prepare(object())

    assert calls == [None, first, None]
