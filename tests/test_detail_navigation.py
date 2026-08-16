import pytest
from types import SimpleNamespace

import ei_ui_smoke.detail_navigation as detail_navigation
from ei_ui_smoke.project_progress_preconditions import project_decision_add_module


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


class _IdentityCells:
    def __init__(self, values):
        self.values = values

    def count(self):
        return len(self.values)

    def all_inner_texts(self):
        return self.values


class _IdentityRecord:
    def __init__(self, business_id, *values):
        self.business_id = business_id
        self.values = values

    def get_attribute(self, name):
        return self.business_id if name == "data-row-key" else ""

    def locator(self, selector):
        if selector == "td,[role='cell']":
            return _IdentityCells(self.values)
        return _EmptyDetailAction()

    def inner_text(self):
        return "\n".join(self.values)


class _IdentityRecords:
    def __init__(self, records):
        self.records = records

    def count(self):
        return len(self.records)

    def nth(self, index):
        return self.records[index]

    def all_inner_texts(self):
        return [record.inner_text() for record in self.records]


class _PreAddResponse:
    def __init__(self, payload, *, ok=True, status=200):
        self.payload = payload
        self.ok = ok
        self.status = status

    def json(self):
        return self.payload


class _PreAddResponseRequest:
    method = "GET"


class _EmptyLocator:
    @property
    def first(self):
        return self

    @property
    def last(self):
        return self

    @staticmethod
    def count():
        return 0

    @staticmethod
    def is_visible():
        return False


class _PreAddExpectation:
    def __init__(self, response):
        self.value = response

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _PreAddButton:
    def __init__(self):
        self.clicked = False

    def click(self):
        self.clicked = True


class _PreAddPage:
    url = "https://example.test/fi-view/#/buildProjects/detail?id=project-23"

    def __init__(self, response):
        self.response = response
        self.button = _PreAddButton()

    def expect_response(self, predicate, *, timeout):
        assert timeout == 15_000
        assert predicate(self.response)
        return _PreAddExpectation(self.response)

    @staticmethod
    def locator(_selector):
        return _EmptyLocator()


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


def test_persisted_parent_business_id_beats_a_duplicate_automation_marker():
    target = _IdentityRecord("parent-2", "UI自动化_20260812210539_1")
    duplicate = _IdentityRecord("parent-1", "UI自动化_20260812210539_1")
    identity = SimpleNamespace(
        business_id="parent-2", record_markers=("UI自动化_20260812210539_1",)
    )

    assert detail_navigation._normalized_record_identity_values(identity) == (
        "parent-2",
    )
    assert detail_navigation._record_for_identity(
        _IdentityRecords([duplicate, target]),
        detail_navigation._normalized_record_identity_values(identity),
    ) is target


def test_persisted_parent_business_id_scans_later_parent_page(monkeypatch):
    target = _IdentityRecord("parent-2", "自动化父记录")
    first_page = _IdentityRecords([_IdentityRecord("parent-1", "其他记录")])
    second_page = _IdentityRecords([target])
    page = object()

    monkeypatch.setattr(detail_navigation, "_parent_total_record_count", lambda _page: 2)
    monkeypatch.setattr(
        detail_navigation,
        "_goto_parent_record_page",
        lambda _page, page_number, _snapshot: (
            second_page if page_number == 2 else pytest.fail("unexpected page")
        ),
    )
    monkeypatch.setattr(
        detail_navigation, "_record_snapshot", lambda candidates: tuple(candidates.all_inner_texts())
    )

    assert detail_navigation._find_parent_record_by_identity(
        page, first_page, ("parent-2",)
    ) is target


def test_project_progress_decision_filter_uses_list_query_before_opening_record(monkeypatch):
    calls = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **_kwargs):
            return None

    class Page:
        url = "https://example.test/fi-view/#/buildProjects"

        def goto(self, url, *, wait_until):
            calls.append(("goto", url, wait_until))

        def locator(self, selector):
            return Locator()

    candidates = type("Candidates", (), {"nth": staticmethod(lambda _index: "first-row")})()
    monkeypatch.setattr(detail_navigation, "_locator_visible", lambda _locator: False)
    monkeypatch.setattr(
        detail_navigation,
        "_click_exact_text",
        lambda _locator, text, **_kwargs: calls.append(("click", text)),
    )
    monkeypatch.setattr(
        detail_navigation,
        "_click_project_list_query",
        lambda _page, **kwargs: calls.append(("query", kwargs.get("status"))),
    )
    monkeypatch.setattr(
        detail_navigation,
        "_wait_for_project_decision_results",
        lambda _page, **_kwargs: candidates,
    )
    monkeypatch.setattr(
        detail_navigation,
        "_open_parent_list_record",
        lambda _page, record, _candidates: ("opened", record),
    )

    result = detail_navigation._enter_project_progress_decision_parent(
        Page(), "https://example.test/fi-view/#/buildProjects/detail"
    )

    assert result == ("opened", "first-row")
    assert calls == [
        ("goto", "https://example.test/fi-view/#/buildProjects", "domcontentloaded"),
        ("click", "···"),
        ("click", "项目决策"),
        ("query", "项目决策"),
    ]


def test_project_progress_filter_uses_implementation_when_decision_has_no_records(monkeypatch):
    calls = []

    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **_kwargs):
            return None

    class Page:
        url = "https://example.test/fi-view/#/buildProjects"

        def goto(self, *_args, **_kwargs):
            return None

        def locator(self, _selector):
            return Locator()

    candidates = type("Candidates", (), {"nth": staticmethod(lambda _index: "implementation-row")})()
    monkeypatch.setattr(detail_navigation, "_locator_visible", lambda _locator: True)
    monkeypatch.setattr(
        detail_navigation,
        "_click_exact_text",
        lambda _locator, text, **_kwargs: calls.append(("filter", text)),
    )
    monkeypatch.setattr(
        detail_navigation,
        "_click_project_list_query",
        lambda _page, **kwargs: calls.append(("query", kwargs["status"])),
    )

    def wait_for_results(_page, *, status, **_kwargs):
        if status == "项目决策":
            raise detail_navigation.ParentListEmptyError("empty")
        assert status == "项目实施"
        return candidates

    monkeypatch.setattr(
        detail_navigation, "_wait_for_project_decision_results", wait_for_results,
    )
    monkeypatch.setattr(
        detail_navigation,
        "_open_parent_list_record",
        lambda _page, record, _candidates: ("opened", record),
    )

    assert detail_navigation._enter_project_progress_decision_parent(
        Page(), "https://example.test/fi-view/#/buildProjects/detail"
    ) == ("opened", "implementation-row")
    assert calls == [
        ("filter", "项目决策"),
        ("query", "项目决策"),
        ("filter", "项目实施"),
        ("query", "项目实施"),
    ]


def test_project_progress_prerequisite_keeps_the_current_detail_root():
    assert project_decision_add_module(
        "建设项目/建设项目/详情/投中管理/项目进度/新增"
    ) == "建设项目/建设项目/详情/投前管理/项目决策/新增"


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


def test_project_progress_uses_decision_filter_before_opening_detail(monkeypatch):
    calls = []

    monkeypatch.setattr(
        detail_navigation,
        "_enter_project_progress_decision_parent",
        lambda _page, detail_url: calls.append(detail_url) or ("decision-parent",),
    )
    monkeypatch.setattr(
        detail_navigation, "navigate_detail_module", lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        detail_navigation,
        "enter_detail_record",
        lambda *_args, **_kwargs: pytest.fail("项目进度不应逐条进入未筛选详情"),
    )

    result = detail_navigation.enter_available_detail_module(
        object(),
        "/buildProject/detail",
        "建设项目/详情/投中管理/项目进度/新增",
        "新增",
        max_records=25,
    )

    assert result == ("decision-parent",)
    assert calls == ["/buildProject/detail"]


def test_project_progress_reuses_cached_parent_without_requery(monkeypatch):
    calls = []

    class Page:
        url = ""

        def goto(self, url, *, wait_until):
            self.url = url

    context = detail_navigation.ProjectProgressParentContext(
        "cached-parent", "https://example.test/fi-view/#/buildProjects/detail?id=cached-parent",
    )
    monkeypatch.setattr(
        detail_navigation,
        "_enter_project_progress_decision_parent",
        lambda *_args: pytest.fail("cached project-progress parent must not be re-queried"),
    )
    monkeypatch.setattr(detail_navigation, "navigate_detail_module", lambda *_args, **_kwargs: None)

    result = detail_navigation.enter_available_detail_module(
        Page(),
        "/buildProject/detail",
        "建设项目/详情/投中管理/项目进度/新增",
        "新增",
        max_records=3,
        record_identity=context,
    )

    assert result is context
    assert calls == []


def test_project_progress_cached_parent_seeds_child_before_edit(monkeypatch):
    events = []

    class Page:
        url = ""

        def goto(self, url, *, wait_until):
            assert wait_until == "domcontentloaded"
            self.url = url
            events.append(("goto", url))

    context = detail_navigation.ProjectProgressParentContext(
        "parent-001", "https://example.test/fi-view/#/buildProjects/detail?id=parent-001",
    )

    def navigate(_page, _module_name, action, **_kwargs):
        events.append(("navigate", action))
        if action == "编辑" and events.count(("navigate", "编辑")) == 1:
            raise detail_navigation.DetailActionUnavailableError("no editable row")

    monkeypatch.setattr(detail_navigation, "navigate_detail_module", navigate)

    result = detail_navigation.enter_available_detail_module(
        Page(),
        "/buildProject/detail",
        "建设项目/详情/投中管理/项目进度/编辑",
        "编辑",
        record_identity=context,
        provision_child_record=lambda: events.append(("seed-child",)) or SimpleNamespace(
            business_id="child-001",
        ),
    )

    assert result is context
    assert events == [
        ("goto", context.detail_url),
        ("navigate", "编辑"),
        ("navigate", "新增"),
        ("seed-child",),
        ("goto", context.detail_url),
        ("navigate", "编辑"),
    ]


def test_project_progress_parent_context_is_reused_by_later_command(monkeypatch, tmp_path):
    monkeypatch.setenv("EI_REQUIRES_BUSINESS_ID", "true")
    monkeypatch.setenv("EI_FORM_URL", "/buildProject/detail")
    monkeypatch.setenv("EI_MODULE_NAME", "建设项目/详情/投中管理/项目进度/新增")
    monkeypatch.setenv("EI_ACTION", "新增")
    monkeypatch.setenv("EI_AUTOMATION_RUN_ID", "run-001")
    monkeypatch.setenv(
        "EI_PROJECT_PROGRESS_PARENT_CONTEXT_PATH", str(tmp_path / "parents.json"),
    )
    selected = detail_navigation.ProjectProgressParentContext(
        "parent-001", "https://example.test/fi-view/#/buildProjects/detail?id=parent-001",
    )
    calls = []

    def first_enter(*_args, **kwargs):
        calls.append(kwargs.get("record_identity"))
        return selected

    monkeypatch.setattr(detail_navigation, "enter_available_detail_module", first_enter)
    detail_navigation.detail_context_preparer_from_env()(object())

    monkeypatch.setenv("EI_MODULE_NAME", "建设项目/详情/投中管理/项目进度/编辑")
    monkeypatch.setenv("EI_ACTION", "编辑")

    def later_enter(*_args, **kwargs):
        calls.append(kwargs.get("record_identity"))
        return selected

    monkeypatch.setattr(detail_navigation, "enter_available_detail_module", later_enter)
    detail_navigation.detail_context_preparer_from_env()(object())

    assert calls == [None, selected]


def test_project_progress_provisions_only_when_decision_query_is_empty(monkeypatch):
    parent = SimpleNamespace(business_id="new-parent")
    monkeypatch.setattr(
        detail_navigation,
        "_enter_project_progress_decision_parent",
        lambda *_args: (_ for _ in ()).throw(
            detail_navigation.ParentListEmptyError("项目状态“项目决策”查询结果为空")
        ),
    )
    created = []
    opened = []
    monkeypatch.setattr(
        detail_navigation,
        "_open_provisioned_detail_module",
        lambda *_args, **kwargs: opened.append(kwargs) or parent,
    )

    result = detail_navigation.enter_available_detail_module(
        object(),
        "/buildProject/detail",
        "建设项目/详情/投中管理/项目进度/新增",
        "新增",
        max_records=2,
        provision_eligible_record=lambda: created.append("created") or parent,
    )

    assert result is parent
    assert created == ["created"]
    assert opened == [{
        "navigation_labels": ["投中管理", "项目进度"],
        "provision_child_record": None,
    }]


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
