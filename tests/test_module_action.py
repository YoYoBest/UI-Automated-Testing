import json
import hashlib
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import allure
from types import SimpleNamespace

from ei_ui_smoke.config import Settings
from ei_ui_smoke.data_pool import GlobalDataPool
from ei_ui_smoke.data_strategy import create_data_strategy
from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs
from ei_ui_smoke.detail_navigation import (
    DetailModulePreconditionError,
    activate_detail_parent_tab as _activate_detail_parent_tab,
    detail_navigation_labels as _detail_navigation_labels,
    enter_available_detail_module as _enter_available_detail_module,
    enter_detail_record as _enter_detail_record,
    navigate_detail_module as _navigate_detail_module,
    visible_action as _visible_action,
)
from ei_ui_smoke.module_driver import (
    CrudValidationPolicy,
    ModuleSmokeDriver,
    RecordNotDeletableError,
)
from ei_ui_smoke.module_resolver import resolve_form_code
from ei_ui_smoke.source_form import discover_form_contract
from ei_ui_smoke.common_delete_cases import CommonDeleteCase, load_common_delete_cases
from ei_ui_smoke.urls import detail_parent_url


ADD_ACTIONS = ("新增", "添加", "新建")
DESTRUCTIVE_ACTIONS = ("删除", "移除", "清空")
REQUEST_ACTIONS = ("查询", "搜索", "重置")
DIALOG_ACTIONS = ("立项准备", "编辑", "入库申请", "跟进")
CANCEL_EDIT_ACTIONS = ("取消编辑", "取消修改")
ACTION_FORM_SELECTOR = '[role="dialog"]:visible,.el-dialog:visible,.el-drawer:visible'
INLINE_EDIT_FORM_SELECTOR = (
    ".detail-panel:visible form:visible,.detail-panel:visible .el-form:visible,"
    ".base-info-page:visible form:visible,.base-info-page:visible .el-form:visible,"
    ".component-box:visible form:visible,.component-box:visible .el-form:visible"
)
INLINE_EDIT_COMMAND_SELECTOR = (
    "button:has-text('保存'),button:has-text('确定'),"
    "button:has-text('取消编辑'),button:has-text('取消修改')"
)
INLINE_EDIT_CONTAINER_COMMAND_SELECTOR = (
    ".detail-panel:visible button:has-text('保存'),"
    ".detail-panel:visible button:has-text('确定'),"
    ".detail-panel:visible button:has-text('取消编辑'),"
    ".detail-panel:visible button:has-text('取消修改'),"
    ".base-info-page:visible button:has-text('保存'),"
    ".base-info-page:visible button:has-text('确定'),"
    ".base-info-page:visible button:has-text('取消编辑'),"
    ".base-info-page:visible button:has-text('取消修改'),"
    ".component-box:visible button:has-text('保存'),"
    ".component-box:visible button:has-text('确定'),"
    ".component-box:visible button:has-text('取消编辑'),"
    ".component-box:visible button:has-text('取消修改')"
)
EDITABLE_CONTROL_SELECTOR = (
    "input:not([type='hidden']):not([disabled]):visible,"
    "textarea:not([disabled]):visible,select:not([disabled]):visible,"
    "[role='combobox']:not([aria-disabled='true']):visible,"
    "[role='radio']:visible,[role='checkbox']:visible"
)
ADD_VERIFIED_MODES = {
    "add_and_detail_verified",
    "add_and_edit_form_verified",
    "add_and_list_verified",
    "add_and_runtime_verified",
}
_CREATED_RESULTS: dict[tuple[str, str], object] = {}
_DETAIL_PARENT_RESULTS: dict[tuple[str, str], object] = {}
_DETAIL_MODULE_PRECONDITION_FAILURES: dict[tuple[str, str], str] = {}
_DELETE_PRECONDITION_FAILURES: dict[tuple[str, str, str], str] = {}


class _DeleteSharedPreconditionError(RuntimeError):
    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original


class _DeleteCandidatePreconditionError(RuntimeError):
    def __init__(self, original: Exception):
        super().__init__(str(original))
        self.original = original


def _selected_actions() -> list[dict[str, object]]:
    raw = os.getenv("EI_ACTIONS_JSON", "").strip()
    if not raw:
        actions = [{
            "module_id": os.getenv("EI_MODULE_ID", ""),
            "module_name": os.getenv("EI_MODULE_NAME", ""),
            "action": os.getenv("EI_ACTION", "").strip(),
            "action_path": list(_action_path_steps()),
            "requires_business_id": os.getenv("EI_REQUIRES_BUSINESS_ID", "").lower() == "true",
            "page_tab": os.getenv("EI_PAGE_TAB", "").strip(),
        }]
    else:
        try:
            actions = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"EI_ACTIONS_JSON 不是有效 JSON：{exc}") from exc
        assert isinstance(actions, list) and actions, "EI_ACTIONS_JSON 必须是非空操作列表"
    actions = _schedule_actions_independently(actions)
    workbook = os.getenv("EI_COMMON_DELETE_CASES_EXCEL", "").strip()
    if not workbook:
        return actions
    sheet = os.getenv("EI_COMMON_DELETE_CASES_SHEET", "删除").strip()
    raw_ids = os.getenv("EI_COMMON_DELETE_CASE_IDS_JSON", "[]")
    try:
        case_ids = json.loads(raw_ids)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"EI_COMMON_DELETE_CASE_IDS_JSON 不是有效 JSON：{exc}") from exc
    if not isinstance(case_ids, list):
        raise AssertionError("EI_COMMON_DELETE_CASE_IDS_JSON 必须是数组")
    cases = load_common_delete_cases(Path(workbook), sheet, [str(item) for item in case_ids])
    return [
        {**action, "common_delete_case": case}
        for action in actions
        for case in cases
    ]


def _schedule_actions_independently(actions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Keep each selected nested operation in its own outer-form lifecycle."""

    scheduled: list[dict[str, object]] = []
    for raw_item in actions:
        item = dict(raw_item)
        path = item.get("action_path") or []
        if isinstance(path, list) and len(path) >= 3:
            item["action"] = str(path[0])
            item["action_path"] = list(path)
        item.pop("nested_action_paths", None)
        scheduled.append(item)
    return scheduled


def _action_data_scope(action_case: dict[str, object]) -> str:
    """Return a stable generated-data discriminator for one logical action."""
    identity = {
        "module_id": str(action_case.get("module_id") or ""),
        "action_path": list(action_case.get("action_path") or ()),
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:12]


def pytest_generate_tests(metafunc):
    if "action_case" not in metafunc.fixturenames:
        return
    actions = _selected_actions()
    metafunc.parametrize(
        "action_case",
        actions,
        ids=[
            f"{index:02d}-"
            f"{getattr(item.get('common_delete_case'), 'case_id', '') or str(item.get('action') or 'operation')}"
            for index, item in enumerate(actions, 1)
        ],
    )


def _action_path_steps() -> tuple[str, ...]:
    raw = os.getenv("EI_ACTION_PATH", "").strip()
    if not raw:
        return ()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"EI_ACTION_PATH 不是有效 JSON：{exc}") from exc
    if not isinstance(value, list) or len(value) < 3:
        return ()
    return tuple(str(step) for step in value)


def _crud_driver(browser_page, request):
    project_root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    mode = request.config.getoption("--data-mode") or settings.data_mode
    module_key = os.getenv("EI_MODULE_ID") or settings.module_name or "MODULE"
    # The launcher can serialize an absent optional form code as the literal
    # string "None".  It must not shadow the configured form code because that
    # disables declared data constraints for the real form.
    configured_form_code = os.getenv("EI_FORM_CODE", "").strip()
    if configured_form_code.lower() in {"", "none", "null", "undefined"}:
        configured_form_code = ""
    component = os.getenv("EI_COMPONENT", "")
    settings_form_code = str(settings.form_code or "").strip()
    if settings_form_code.lower() in {"", "none", "null", "undefined"}:
        settings_form_code = ""
    form_code = configured_form_code or settings_form_code
    if not form_code and component:
        try:
            form_code = resolve_form_code(
                settings.source_root, component.split("/", 1)[0]
            )
        except (OSError, ValueError):
            pass
    form_code = form_code or module_key
    data_pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = create_data_strategy(mode, data_pool, form_code)
    source_contract = discover_form_contract(settings.source_root, component)
    dynamic_collections = load_dynamic_collection_specs(
        project_root / "data",
        form_code=form_code,
        component=component,
    )
    return ModuleSmokeDriver(
        browser_page,
        strategy,
        source_fields=list(source_contract.fields),
        source_branch_candidates=source_contract.branch_candidates,
        source_detail_endpoints=source_contract.detail_endpoints,
        default_upload_file=data_pool.default_upload_file(),
        dynamic_collections=dynamic_collections,
        form_code=form_code,
        component=component,
        automation_record_registry=(
            project_root / "artifacts" / "automation-record-registry.json"
        ),
    )


def test_crud_driver_ignores_literal_none_form_code(monkeypatch):
    captured = {}
    branch_candidate = object()

    class RequestConfig:
        @staticmethod
        def getoption(_name):
            return "standard"

    class Request:
        config = RequestConfig()

    monkeypatch.setenv("EI_FORM_CODE", "None")
    monkeypatch.setenv("EI_MODULE_ID", "netAssets::action::2")
    monkeypatch.setenv("EI_COMPONENT", "")
    monkeypatch.setattr(
        f"{__name__}.Settings.from_env",
        lambda: SimpleNamespace(
            data_mode="standard", form_code="BUILD_NETASSETS_MAINTAIL",
            module_name="净资产维护", source_root=Path("."),
        ),
    )
    monkeypatch.setattr(
        f"{__name__}.GlobalDataPool.from_directory",
        lambda _path: SimpleNamespace(default_upload_file=lambda: None),
    )
    monkeypatch.setattr(
        f"{__name__}.create_data_strategy",
        lambda mode, pool, form_code: captured.update(
            {"mode": mode, "form_code": form_code}
        ) or object(),
    )
    monkeypatch.setattr(
        f"{__name__}.discover_form_contract",
        lambda *_args: SimpleNamespace(
            fields=(),
            branch_candidates=(branch_candidate,),
            detail_endpoints=(),
        ),
    )
    monkeypatch.setattr(
        f"{__name__}.load_dynamic_collection_specs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        f"{__name__}.ModuleSmokeDriver",
        lambda *_args, **kwargs: captured.update(
            {"branch_candidates": kwargs["source_branch_candidates"]}
        ) or object(),
    )

    _crud_driver(object(), Request())

    assert captured == {
        "mode": "standard",
        "form_code": "BUILD_NETASSETS_MAINTAIL",
        "branch_candidates": (branch_candidate,),
    }


def test_crud_driver_resolves_form_code_when_action_environment_omits_it(monkeypatch):
    captured = {}

    class RequestConfig:
        @staticmethod
        def getoption(_name):
            return "standard"

    class Request:
        config = RequestConfig()

    monkeypatch.setenv("EI_FORM_CODE", "None")
    monkeypatch.setenv("EI_MODULE_ID", "netAssets::action::2")
    monkeypatch.setenv("EI_COMPONENT", "netAssets/index")
    monkeypatch.setattr(
        f"{__name__}.Settings.from_env",
        lambda: SimpleNamespace(
            data_mode="standard", form_code="", module_name="净资产维护",
            source_root=Path("."),
        ),
    )
    monkeypatch.setattr(f"{__name__}.resolve_form_code", lambda *_args: "BUILD_NETASSETS_MAINTAIL")
    monkeypatch.setattr(
        f"{__name__}.GlobalDataPool.from_directory",
        lambda _path: SimpleNamespace(default_upload_file=lambda: None),
    )
    monkeypatch.setattr(
        f"{__name__}.create_data_strategy",
        lambda _mode, _pool, form_code: captured.update({"form_code": form_code}) or object(),
    )
    monkeypatch.setattr(
        f"{__name__}.discover_form_contract",
        lambda *_args: SimpleNamespace(
            fields=(), branch_candidates=(), detail_endpoints=()
        ),
    )
    monkeypatch.setattr(f"{__name__}.load_dynamic_collection_specs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(f"{__name__}.ModuleSmokeDriver", lambda *_args, **_kwargs: object())

    _crud_driver(object(), Request())

    assert captured == {"form_code": "BUILD_NETASSETS_MAINTAIL"}


def _prepare_detail_action_context(
    browser_page, request, *, form_url: str, module_name: str, action: str, created=None,
):
    """Enter the requested detail module with isolated parent/child preconditions."""
    navigation_module_name, navigation_action = _navigation_context_for_action(
        module_name, action
    )
    provisioned = created

    def provision_parent_record():
        nonlocal provisioned
        if provisioned is None:
            goto = getattr(browser_page, "goto", None)
            if callable(goto):
                goto(_parent_list_url(form_url), wait_until="domcontentloaded")
                _activate_detail_parent_tab(browser_page)
            provisioned = _crud_driver(browser_page, request).run(provision_only=True)
            assert provisioned.mode == "add_provisioned"
        return provisioned

    def provision_child_record():
        child = _crud_driver(browser_page, request).run(provision_only=True)
        assert child.mode == "add_provisioned"
        return child

    return _enter_available_detail_module(
        browser_page,
        form_url,
        navigation_module_name,
        navigation_action,
        record_identity=created,
        provision_record=provision_parent_record,
        provision_child_record=provision_child_record,
    )


def _visible_action_for_created_record(page, action: str, created):
    submitted = getattr(created, "submitted", None) or {}
    markers = list(getattr(created, "record_markers", None) or ())
    markers.extend(
        str(value) for value in submitted.values()
        if isinstance(value, str) and (value.startswith("AUTO_") or value.startswith("UI自动化_"))
    )
    markers = list(dict.fromkeys(markers))
    exact = re.compile(rf"^\s*{re.escape(action)}\s*$")
    for marker in markers:
        containers = page.locator(
            ".el-table__row,.fund-card,.project-card,.list-item", has_text=marker
        )
        for index in range(containers.count()):
            target = containers.nth(index).locator(
                "button:visible,a:visible,[role='button']:visible"
            ).filter(has_text=exact).first
            if target.count() and target.is_visible() and target.is_enabled():
                return target
    return _visible_action(page, action)


def _parent_list_url(detail_url: str) -> str:
    return detail_parent_url(detail_url)


def _is_cancel_edit_action(action: str) -> bool:
    return action.startswith(CANCEL_EDIT_ACTIONS)


def _is_destructive_action(action: str) -> bool:
    return action.startswith(DESTRUCTIVE_ACTIONS)


def _require_add_for_action(action_case: dict[str, object], action: str) -> bool:
    """Deletion cases always need an automation-owned record to operate safely."""
    return bool(action_case.get("require_add")) or _is_destructive_action(action)


def _navigation_context_for_action(module_name: str, action: str) -> tuple[str, str]:
    """Use a visible precondition entry for actions whose button appears later."""
    if _is_cancel_edit_action(action):
        navigation_action = "编辑"
    elif _is_destructive_action(action):
        navigation_action = "新增"
    else:
        return module_name, action

    parts = [part.strip() for part in module_name.split("/") if part.strip()]
    if parts and parts[-1] == action:
        parts[-1] = navigation_action
        return "/".join(parts), navigation_action
    return module_name, navigation_action


def _locator_visible(locator) -> bool:
    try:
        return bool(locator.count() and locator.is_visible())
    except Exception:
        return False


def _has_inline_edit_form(page) -> bool:
    try:
        forms = page.locator(INLINE_EDIT_FORM_SELECTOR)
        for index in range(forms.count()):
            form = forms.nth(index)
            if not form.is_visible():
                continue
            controls = form.locator(EDITABLE_CONTROL_SELECTOR)
            commands = form.locator(INLINE_EDIT_COMMAND_SELECTOR)
            container_commands = page.locator(INLINE_EDIT_CONTAINER_COMMAND_SELECTOR)
            if _locator_visible(controls.first) and (
                _locator_visible(commands.first)
                or _locator_visible(container_commands.first)
            ):
                return True
    except Exception:
        return False
    return False


def _wait_for_action_form_effect(
    page, before_dialogs: int, action: str, timeout: int = 10_000
) -> str:
    """Wait until a business action opens a dialog or switches the detail page inline."""
    deadline = time.monotonic() + timeout / 1000
    while True:
        dialogs = page.locator(ACTION_FORM_SELECTOR)
        try:
            if dialogs.count() > before_dialogs:
                dialogs.last.wait_for(state="visible", timeout=1_000)
                return "dialog"
        except Exception:
            pass
        if _has_inline_edit_form(page):
            return "inline_edit"
        if time.monotonic() >= deadline or not hasattr(page, "wait_for_timeout"):
            break
        page.wait_for_timeout(200)
    raise AssertionError(
        f"页面操作“{action}”已点击，但没有打开业务弹窗，也没有进入页面内联编辑态"
    )


def _confirm_leave_if_prompt(page) -> bool:
    confirm = page.locator('.el-message-box:visible,[role="alertdialog"]:visible').last
    try:
        confirm.wait_for(state="visible", timeout=1_000)
    except Exception:
        return False
    button = confirm.locator(
        "button:has-text('确定'),button:has-text('确认'),"
        "button:has-text('离开'),button:has-text('不保存')"
    ).last
    assert button.count() and button.is_visible(), "取消编辑确认框没有可确认离开的按钮"
    button.click()
    try:
        confirm.wait_for(state="hidden", timeout=5_000)
    except Exception:
        pass
    return True


def _run_cancel_edit_action(browser_page, action: str, created=None):
    edit = _visible_action_for_created_record(browser_page, "编辑", created)
    assert edit is not None, f"页面上未找到可见操作按钮：编辑，无法执行“{action}”"
    before_dialogs = browser_page.locator(ACTION_FORM_SELECTOR).count()
    edit.click()
    effect = _wait_for_action_form_effect(browser_page, before_dialogs, "编辑")
    target = _visible_action(browser_page, action, timeout=10_000)
    assert target is not None, f"进入编辑态后未找到可见操作按钮：{action}"
    target.click()
    _confirm_leave_if_prompt(browser_page)
    if effect == "dialog":
        dialogs = browser_page.locator(ACTION_FORM_SELECTOR)
        deadline = time.monotonic() + 10
        while hasattr(browser_page, "wait_for_timeout") and time.monotonic() < deadline:
            if dialogs.count() <= before_dialogs:
                return None
            browser_page.wait_for_timeout(200)
        assert dialogs.count() <= before_dialogs, f"点击“{action}”后业务弹窗未关闭"
        return None
    edit_again = _visible_action(browser_page, "编辑", timeout=10_000)
    assert edit_again is not None, f"点击“{action}”后页面未返回只读态"
    return None


def _execute_and_verify_action(page, target, action: str) -> str:
    responses = []
    requests = []
    response_listener = lambda response: responses.append(response)
    request_listener = lambda request: requests.append(request)
    page.on("response", response_listener)
    page.on("request", request_listener)
    try:
        before_url = page.url
        before_dialogs = page.locator(ACTION_FORM_SELECTOR).count()
        target.click()

        if action.startswith(DESTRUCTIVE_ACTIONS):
            confirm = page.locator(
                '.el-message-box:visible,[role="alertdialog"]:visible,'
                '.el-dialog:visible:has-text("确定删除")'
            ).last
            confirm.wait_for(state="visible", timeout=10_000)
            cancel = confirm.locator(
                'button:has-text("取消"),button:has-text("关闭"),button[aria-label="Close"]'
            ).last
            assert cancel.count() and cancel.is_visible(), f"页面操作“{action}”已点击，但确认框没有取消按钮"
            cancel.click()
            return "confirmation_opened_and_cancelled"

        if action.startswith(REQUEST_ACTIONS):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(
                getattr(request, "resource_type", "") in {"xhr", "fetch"}
                for request in requests
            ):
                page.wait_for_timeout(100)
            business_requests = [
                request for request in requests
                if getattr(request, "resource_type", "") in {"xhr", "fetch"}
            ]
            assert business_requests, f"页面操作“{action}”已点击，但没有触发列表请求"
            return "request_dispatched"

        page.wait_for_timeout(1_000)
        business_responses = [
            response for response in responses
            if getattr(response.request, "resource_type", "") in {"xhr", "fetch"}
        ]
        after_dialogs = page.locator(ACTION_FORM_SELECTOR).count()
        if action.startswith(DIALOG_ACTIONS):
            if after_dialogs <= before_dialogs and not _has_inline_edit_form(page):
                raise AssertionError(
                    f"页面操作“{action}”已点击，但没有打开业务弹窗，也没有进入页面内联编辑态"
                )
            return "dialog_opened"
        assert (
            after_dialogs > before_dialogs or page.url != before_url or business_responses
        ), f"页面操作“{action}”已点击，但没有出现弹窗、路由变化或业务请求"
        return "effect_observed"
    finally:
        if hasattr(page, "remove_listener"):
            page.remove_listener("response", response_listener)
            page.remove_listener("request", request_listener)


class _ActionCandidates:
    def __init__(self, wait_error=None):
        self.first = self
        self.wait_error = wait_error
        self.wait_calls = []

    def filter(self, **kwargs):
        return self

    def wait_for(self, **kwargs):
        self.wait_calls.append(kwargs)
        if self.wait_error:
            raise self.wait_error

    def count(self):
        return 1

    def nth(self, index):
        return self

    def is_visible(self):
        return True


class _ActionPage:
    def __init__(self, candidates):
        self.candidates = candidates

    def locator(self, selector):
        return self.candidates


class _FakeLocator:
    def __init__(self, count=0, *, visible=True, enabled=True):
        self._count = count
        self._visible = visible
        self._enabled = enabled
        self.first = self
        self.last = self

    def count(self):
        return self._count

    def nth(self, index):
        return self

    def is_visible(self):
        return self._visible and self._count > 0

    def is_enabled(self):
        return self._enabled

    def wait_for(self, **kwargs):
        if not self.is_visible():
            raise TimeoutError("locator is not visible")

    def locator(self, selector):
        return _FakeLocator(0)


class _InlineEditFormLocator(_FakeLocator):
    def __init__(self):
        super().__init__(1)

    def locator(self, selector):
        if selector == EDITABLE_CONTROL_SELECTOR:
            return _FakeLocator(1)
        return _FakeLocator(0)


class _ClickTarget(_FakeLocator):
    def __init__(self):
        super().__init__(1)
        self.clicked = False

    def click(self):
        self.clicked = True


class _InlineEditPage:
    url = "http://example.test/detail?id=1"

    def __init__(self):
        self.edit_target = _ClickTarget()
        self.cancel_target = _ClickTarget()
        self.edit_again_target = _ClickTarget()

    def locator(self, selector):
        if selector == ACTION_FORM_SELECTOR:
            return _FakeLocator(0)
        if selector == INLINE_EDIT_FORM_SELECTOR:
            return _InlineEditFormLocator()
        if selector == INLINE_EDIT_CONTAINER_COMMAND_SELECTOR:
            return _FakeLocator(1)
        return _FakeLocator(0)

    def wait_for_timeout(self, timeout):
        return None

    def is_closed(self):
        return False


class _RequestActionPage:
    url = "http://example.test/projects"

    def __init__(self, *, dispatch=True):
        self.dispatch = dispatch
        self.listeners = {}
        self.removed = []

    def on(self, event, listener):
        self.listeners[event] = listener

    def remove_listener(self, event, listener):
        assert self.listeners.pop(event) is listener
        self.removed.append(event)

    @staticmethod
    def locator(_selector):
        return _FakeLocator(0)

    def wait_for_timeout(self, _timeout):
        return None


class _RequestActionTarget:
    def __init__(self, page):
        self.page = page

    def click(self):
        if self.page.dispatch:
            request = SimpleNamespace(resource_type="xhr")
            self.page.listeners["request"](request)


def test_visible_action_waits_for_async_page_rendering():
    candidates = _ActionCandidates()

    result = _visible_action(_ActionPage(candidates), "查询")

    assert result is candidates
    assert candidates.wait_calls == [{"state": "visible", "timeout": 15_000}]


def test_request_action_passes_on_dispatch_without_waiting_for_slow_response():
    page = _RequestActionPage()

    result = _execute_and_verify_action(page, _RequestActionTarget(page), "搜索")

    assert result == "request_dispatched"
    assert page.removed == ["response", "request"]


def test_request_action_still_fails_when_no_xhr_or_fetch_is_dispatched(monkeypatch):
    page = _RequestActionPage(dispatch=False)
    ticks = iter([0, 0, 6])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 6))

    with pytest.raises(AssertionError, match="没有触发列表请求"):
        _execute_and_verify_action(page, _RequestActionTarget(page), "搜索")

    assert page.removed == ["response", "request"]


def test_visible_action_returns_none_after_readiness_timeout():
    candidates = _ActionCandidates(wait_error=TimeoutError())

    assert _visible_action(_ActionPage(candidates), "查询") is None


def test_empty_serialized_action_path_is_not_nested(monkeypatch):
    monkeypatch.setenv("EI_ACTION_PATH", "[]")

    assert _action_path_steps() == ()


def test_real_action_path_requires_three_steps(monkeypatch):
    monkeypatch.setenv("EI_ACTION_PATH", '["新增", "股权结构", "新增"]')

    assert _action_path_steps() == ("新增", "股权结构", "新增")


def test_parent_list_url_removes_only_detail_suffix():
    assert _parent_list_url(
        "http://host/fi-view/#/buildProject/detail"
    ) == "http://host/fi-view/#/buildProject"


def test_parent_list_url_preserves_hash_query():
    assert _parent_list_url(
        "http://host/fi-view/#/buildProject/detail?tab=execution"
    ) == "http://host/fi-view/#/buildProject?tab=execution"


def test_detail_navigation_labels_excludes_roots_and_action():
    assert _detail_navigation_labels(
        "建设项目/建设项目/详情/信息管理与备案/项目实施/刷新", "刷新"
    ) == ["信息管理与备案", "项目实施"]


def test_detail_navigation_stops_before_outer_dialog_and_nested_operations():
    assert _detail_navigation_labels(
        "建设项目/建设项目/详情/投前管理/项目决策/新增/新增项目决策/"
        "预算及资金来源明细/新增",
        "新增",
    ) == ["投前管理", "项目决策"]


def test_cancel_edit_navigation_uses_visible_edit_entry():
    module_name, action = _navigation_context_for_action(
        "建设项目/建设项目/详情/投前管理/项目立项/取消编辑",
        "取消编辑",
    )

    assert module_name == "建设项目/建设项目/详情/投前管理/项目立项/编辑"
    assert action == "编辑"
    assert _detail_navigation_labels(module_name, action) == ["投前管理", "项目立项"]


def test_destructive_detail_navigation_uses_visible_add_entry():
    module_name, action = _navigation_context_for_action(
        "建设项目/建设项目/详情/投前管理/项目决策/删除",
        "删除",
    )

    assert module_name == "建设项目/建设项目/详情/投前管理/项目决策/新增"
    assert action == "新增"
    assert _detail_navigation_labels(module_name, action) == ["投前管理", "项目决策"]


def test_selected_actions_reads_page_batch(monkeypatch):
    monkeypatch.setenv("EI_ACTIONS_JSON", json.dumps([
        {"module_id": "POOL::action::0", "action": "查询", "action_path": []},
        {
            "module_id": "POOL::action::1", "action": "删除",
            "action_path": ["新增", "股权结构", "删除"],
        },
    ], ensure_ascii=False))

    actions = _selected_actions()

    assert [item["action"] for item in actions] == ["查询", "新增"]
    assert actions[1]["action_path"] == ["新增", "股权结构", "删除"]
    assert "nested_action_paths" not in actions[1]


def test_selected_actions_keeps_direct_page_tab(monkeypatch):
    monkeypatch.delenv("EI_ACTIONS_JSON", raising=False)
    monkeypatch.setenv("EI_MODULE_ID", "AFTER::personnel")
    monkeypatch.setenv("EI_MODULE_NAME", "对外投资项目/项目投后管理/详情/投后管理/人员委派/新增")
    monkeypatch.setenv("EI_PAGE_TAB", "项目投后管理")
    monkeypatch.setenv("EI_ACTION", "新增")
    monkeypatch.setenv("EI_REQUIRES_BUSINESS_ID", "true")

    actions = _selected_actions()

    assert actions == [{
        "module_id": "AFTER::personnel",
        "module_name": "对外投资项目/项目投后管理/详情/投后管理/人员委派/新增",
        "action": "新增", "action_path": [], "requires_business_id": True,
        "page_tab": "项目投后管理",
    }]


def test_selected_actions_expands_delete_cases_for_direct_cli(monkeypatch):
    monkeypatch.delenv("EI_ACTIONS_JSON", raising=False)
    monkeypatch.setenv("EI_MODULE_ID", "POOL::action::delete")
    monkeypatch.setenv("EI_ACTION", "删除")
    monkeypatch.setenv("EI_COMMON_DELETE_CASES_EXCEL", "rules.xlsx")
    monkeypatch.setenv("EI_COMMON_DELETE_CASE_IDS_JSON", '["DELETE-002", "DELETE-003"]')
    monkeypatch.setitem(
        globals(),
        "load_common_delete_cases",
        lambda _workbook, _sheet, _ids: [
            CommonDeleteCase("DELETE-002", "删除确认提示", "确认删除成功"),
            CommonDeleteCase("DELETE-003", "取消删除有效性", "删除不成功"),
        ],
    )

    actions = _selected_actions()

    assert [item["common_delete_case"].case_id for item in actions] == [
        "DELETE-002", "DELETE-003",
    ]


@pytest.mark.parametrize("failure_type", [AssertionError, RuntimeError])
def test_common_delete_cases_share_a_failed_detail_precondition(
    monkeypatch, failure_type,
):
    _DELETE_PRECONDITION_FAILURES.clear()
    calls = []
    case = {
        "module_id": "DETAIL::delete",
        "module_name": "建设项目/详情/项目运行信息/删除",
        "action": "删除",
        "form_url": "https://example.test/detail",
        "component": "buildProject/projectOperation",
        "requires_business_id": True,
        "common_delete_case": CommonDeleteCase(
            "DELETE-001", "删除关联数据检查", "存在关联不能删除",
        ),
    }

    def fail_precondition(*_args, **_kwargs):
        calls.append("prepare")
        raise failure_type("详情子模块无法建立新增前置数据")

    monkeypatch.setitem(globals(), "_prepare_detail_action_context", fail_precondition)

    with pytest.raises(failure_type, match="无法建立新增前置数据"):
        test_selected_page_action(object(), object(), case)
    with pytest.raises(pytest.skip.Exception, match="删除共享前置条件未满足"):
        test_selected_page_action(
            object(),
            object(),
            {**case, "common_delete_case": CommonDeleteCase(
                "DELETE-002", "删除确认提示信息检查", "是否确认删除",
            )},
        )

    assert calls == ["prepare"]
    _DELETE_PRECONDITION_FAILURES.clear()


def test_detail_actions_share_a_missing_target_module_precondition(monkeypatch):
    _DETAIL_MODULE_PRECONDITION_FAILURES.clear()
    calls = []
    add_case = {
        "module_id": "DETAIL::staff-assignment::add",
        "module_name": "对外投资项目/详情/投后管理/人员委派/新增",
        "action": "新增",
        "form_url": "https://example.test/projectManage/detail",
        "component": "projectManage/after/staffAssignment/index",
        "requires_business_id": True,
    }

    def unavailable(*_args, **_kwargs):
        calls.append("prepare")
        raise DetailModulePreconditionError(
            "详情模块前置条件未满足：已扫描 10 条父记录，均无法进入“投后管理”"
        )

    monkeypatch.setitem(globals(), "_prepare_detail_action_context", unavailable)

    with pytest.raises(DetailModulePreconditionError, match="投后管理"):
        test_selected_page_action(SimpleNamespace(url=""), object(), add_case)
    with pytest.raises(pytest.skip.Exception, match="详情模块共享前置条件未满足"):
        test_selected_page_action(
            SimpleNamespace(url=""),
            object(),
            {**add_case, "module_id": "DETAIL::staff-assignment::edit", "action": "编辑"},
        )

    assert calls == ["prepare"]
    _DETAIL_MODULE_PRECONDITION_FAILURES.clear()


def test_common_delete_cases_share_a_failed_record_provision_precondition(monkeypatch):
    _DELETE_PRECONDITION_FAILURES.clear()
    calls = []
    action_case = {
        "module_id": "DETAIL::delete",
        "module_name": "建设项目/详情/信息管理与备案/项目进度/删除",
        "action": "删除",
        "form_url": "https://example.test/buildProjects/detail",
        "component": "buildProject/information/projectExecution/index",
        "requires_business_id": True,
        "common_delete_case": CommonDeleteCase(
            "DELETE-001", "删除关联数据检查", "删除成功",
        ),
    }

    class Driver:
        def find_reusable_automation_delete_record(self):
            calls.append("registry")
            return None

        def find_available_delete_record(self):
            calls.append("find")
            return None

        def run(self, *, provision_only=False):
            assert provision_only is True
            calls.append("provision")
            raise RuntimeError("点击新增后没有出现对话框或页面内嵌表单")

    monkeypatch.setitem(globals(), "_crud_driver", lambda *_args: Driver())
    monkeypatch.setitem(
        globals(), "_prepare_detail_action_context", lambda *_args, **_kwargs: None,
    )

    with pytest.raises(RuntimeError, match="点击新增后没有出现"):
        test_selected_page_action(
            SimpleNamespace(url=""), object(), action_case,
        )
    with pytest.raises(pytest.skip.Exception, match="删除共享前置条件未满足"):
        test_selected_page_action(
            SimpleNamespace(url=""),
            object(),
            {**action_case, "common_delete_case": CommonDeleteCase(
                "DELETE-002", "删除确认提示信息检查", "删除成功",
            )},
        )

    assert calls == ["registry", "find", "provision"]
    _DELETE_PRECONDITION_FAILURES.clear()


def test_common_delete_precondition_is_scoped_to_one_logical_action(monkeypatch):
    _DELETE_PRECONDITION_FAILURES.clear()
    calls = []
    base_case = {
        "module_name": "建设项目/详情/信息管理与备案/项目进度/删除",
        "action": "删除",
        "form_url": "https://example.test/buildProjects/detail",
        "component": "buildProject/information/projectExecution/index",
        "requires_business_id": True,
        "common_delete_case": CommonDeleteCase(
            "DELETE-001", "删除关联数据检查", "删除成功",
        ),
    }

    class Driver:
        @staticmethod
        def find_reusable_automation_delete_record():
            return None

        @staticmethod
        def find_available_delete_record():
            return None

        @staticmethod
        def run(*, provision_only=False):
            assert provision_only is True
            calls.append("provision")
            raise RuntimeError("删除前置不可用")

    monkeypatch.setitem(globals(), "_crud_driver", lambda *_args: Driver())
    monkeypatch.setitem(
        globals(), "_prepare_detail_action_context", lambda *_args, **_kwargs: None,
    )

    for module_id in ("DETAIL::delete-a", "DETAIL::delete-b"):
        with pytest.raises(RuntimeError, match="删除前置不可用"):
            test_selected_page_action(
                SimpleNamespace(url=""),
                object(),
                {**base_case, "module_id": module_id},
            )

    assert calls == ["provision", "provision"]
    _DELETE_PRECONDITION_FAILURES.clear()


def test_common_delete_behavior_failure_does_not_poison_shared_precondition(monkeypatch):
    _DELETE_PRECONDITION_FAILURES.clear()
    calls = []
    action_case = {
        "module_id": "DETAIL::delete",
        "module_name": "建设项目/详情/信息管理与备案/项目进度/删除",
        "action": "删除",
        "form_url": "https://example.test/buildProjects/detail",
        "component": "buildProject/information/projectExecution/index",
        "requires_business_id": True,
        "common_delete_case": CommonDeleteCase(
            "DELETE-004", "删除成功检查", "删除成功",
        ),
    }
    reusable = SimpleNamespace(mode="delete_any_available")

    class Driver:
        @staticmethod
        def find_reusable_automation_delete_record():
            return None

        @staticmethod
        def find_available_delete_record():
            return reusable

        @staticmethod
        def delete_created_record(_record):
            calls.append("delete")
            raise AssertionError("删除响应业务状态不正确")

    monkeypatch.setitem(globals(), "_crud_driver", lambda *_args: Driver())
    monkeypatch.setitem(
        globals(), "_prepare_detail_action_context", lambda *_args, **_kwargs: None,
    )

    for case_id in ("DELETE-004", "DELETE-006"):
        with pytest.raises(AssertionError, match="删除响应业务状态不正确"):
            test_selected_page_action(
                SimpleNamespace(url=""),
                object(),
                {**action_case, "common_delete_case": CommonDeleteCase(
                    case_id, "删除成功检查", "删除成功",
                )},
            )

    assert calls == ["delete", "delete"]
    assert _DELETE_PRECONDITION_FAILURES == {}


def test_destructive_action_requires_add_even_when_the_command_omits_it():
    assert _require_add_for_action({"action": "删除"}, "删除") is True
    assert _require_add_for_action({"require_add": ""}, "删除") is True
    assert _require_add_for_action({"action": "查询"}, "查询") is False


def test_nested_actions_have_independent_outer_add_executions(monkeypatch):
    monkeypatch.setenv("EI_ACTIONS_JSON", json.dumps([
        {"module_id": "POOL::2", "action": "新增", "action_path": [], "form_url": "/pool"},
        {"module_id": "POOL::3", "action": "新增", "action_path": ["新增", "股权结构", "新增"], "form_url": "/pool"},
        {"module_id": "POOL::4", "action": "删除", "action_path": ["新增", "股权结构", "删除"], "form_url": "/pool"},
        {"module_id": "POOL::5", "action": "新增", "action_path": ["新增", "对外投资", "新增"], "form_url": "/pool"},
        {"module_id": "POOL::6", "action": "删除", "action_path": ["新增", "对外投资", "删除"], "form_url": "/pool"},
    ], ensure_ascii=False))

    actions = _selected_actions()

    assert len(actions) == 5
    assert [item["action"] for item in actions] == ["新增"] * 5
    assert [item["action_path"] for item in actions] == [
        [],
        ["新增", "股权结构", "新增"],
        ["新增", "股权结构", "删除"],
        ["新增", "对外投资", "新增"],
        ["新增", "对外投资", "删除"],
    ]
    assert all("nested_action_paths" not in item for item in actions)


def test_independent_actions_have_distinct_stable_data_scopes():
    outer = {"module_id": "POOL::2", "action_path": []}
    nested = {
        "module_id": "POOL::3",
        "action_path": ["add", "ownership", "add"],
    }

    assert _action_data_scope(outer) == _action_data_scope(dict(outer))
    assert _action_data_scope(outer) != _action_data_scope(nested)


def test_dialog_action_accepts_inline_edit_form(monkeypatch):
    page = _InlineEditPage()
    saved_actions = []
    created = SimpleNamespace(business_id="9001")

    monkeypatch.setitem(
        globals(),
        "_visible_action_for_created_record",
        lambda browser_page, action, created: page.edit_target,
    )
    monkeypatch.setitem(
        globals(),
        "_crud_driver",
        lambda browser_page, request: SimpleNamespace(
            save_open_dialog=lambda action, *, established_business_id="": (
                saved_actions.append((action, established_business_id))
                or SimpleNamespace(mode="dialog_action_detail_verified")
            )
        ),
    )

    result = _run_selected_action(
        page, request=None, action="编辑", created=created
    )

    assert result.mode == "dialog_action_detail_verified"
    assert page.edit_target.clicked
    assert saved_actions == [("编辑", "9001")]


def test_probe_dialog_action_accepts_response_only_result(monkeypatch):
    page = _InlineEditPage()
    driver = SimpleNamespace(
        data_strategy=SimpleNamespace(data_mode="probe"),
        save_open_dialog=lambda _action, *, established_business_id="": (
            SimpleNamespace(mode="dialog_action_saved")
        ),
    )

    monkeypatch.setitem(
        globals(),
        "_visible_action_for_created_record",
        lambda _browser_page, _action, _created: page.edit_target,
    )
    monkeypatch.setitem(globals(), "_crud_driver", lambda *_args: driver)

    result = _run_selected_action(
        page,
        request=None,
        action="编辑",
        created=SimpleNamespace(business_id="9001"),
    )

    assert result.mode == "dialog_action_saved"


def test_add_action_verifies_every_runtime_branch(monkeypatch):
    verified = SimpleNamespace(mode="add_and_detail_verified", business_id="1")
    final = SimpleNamespace(mode="add_and_list_verified", business_id="2")
    calls = []

    monkeypatch.delenv("EI_ACTION_PATH", raising=False)
    monkeypatch.setitem(
        globals(),
        "_crud_driver",
        lambda _page, _request: SimpleNamespace(
            run_all_branches=lambda: calls.append("all") or (verified, final)
        ),
    )

    result = _run_selected_action(object(), request=None, action="新增")

    assert result is final
    assert calls == ["all"]


def test_cancel_edit_enters_inline_edit_before_cancelling(monkeypatch):
    page = _InlineEditPage()
    visible_actions = []

    monkeypatch.setitem(
        globals(),
        "_visible_action_for_created_record",
        lambda browser_page, action, created: page.edit_target,
    )

    def visible_action(browser_page, action, timeout=15_000):
        visible_actions.append(action)
        if action == "取消编辑":
            return page.cancel_target
        if action == "编辑":
            return page.edit_again_target
        return None

    monkeypatch.setitem(globals(), "_visible_action", visible_action)

    assert _run_cancel_edit_action(page, "取消编辑") is None
    assert page.edit_target.clicked
    assert page.cancel_target.clicked
    assert visible_actions == ["取消编辑", "编辑"]


@pytest.mark.smoke
def test_selected_page_action(browser_page, request, action_case):
    action = str(action_case.get("action") or "").strip()
    module_name = str(action_case.get("module_name") or "")
    action_path = action_case.get("action_path") or []
    os.environ["EI_MODULE_ID"] = str(action_case.get("module_id") or "")
    os.environ["EI_MODULE_NAME"] = str(action_case.get("module_name") or "")
    os.environ["EI_ACTION"] = action
    os.environ["EI_COMPONENT"] = str(
        action_case.get("component") or os.getenv("EI_COMPONENT", "")
    )
    os.environ["EI_FORM_CODE"] = str(
        action_case.get("form_code") or os.getenv("EI_FORM_CODE", "")
    )
    if page_tab := str(action_case.get("page_tab") or "").strip():
        os.environ["EI_PAGE_TAB"] = page_tab
    else:
        os.environ.pop("EI_PAGE_TAB", None)
    action_scope = _action_data_scope(action_case)
    os.environ["EI_AUTOMATION_ACTION_SCOPE"] = action_scope
    if _require_add_for_action(action_case, action):
        os.environ["EI_REQUIRE_ADD"] = str(action_case.get("require_add") or "true")
    else:
        os.environ.pop("EI_REQUIRE_ADD", None)
    if action_path:
        os.environ["EI_ACTION_PATH"] = json.dumps(action_path, ensure_ascii=False)
    else:
        os.environ.pop("EI_ACTION_PATH", None)
    os.environ.pop("EI_ACTION_PATHS_JSON", None)
    form_url = str(action_case.get("form_url") or os.getenv("EI_FORM_URL", ""))
    requires_business_id = bool(action_case.get("requires_business_id"))
    record_key = (form_url, os.getenv("EI_COMPONENT", ""))
    delete_precondition_key = (*record_key, action_scope)
    created = _CREATED_RESULTS.get(record_key)
    parent_record = _DETAIL_PARENT_RESULTS.get(record_key)
    prior_detail_precondition = _DETAIL_MODULE_PRECONDITION_FAILURES.get(record_key)
    if requires_business_id and prior_detail_precondition:
        pytest.skip(f"详情模块共享前置条件未满足：{prior_detail_precondition}")
    common_delete_case = action_case.get("common_delete_case")
    if common_delete_case is not None:
        allure.dynamic.title(
            f"删除：{common_delete_case.scenario}\n【{common_delete_case.case_id}】"
        )
        allure.dynamic.parameter(
            "common_delete_case",
            common_delete_case.case_id,
            mode=allure.parameter_mode.HIDDEN,
        )
        prior_failure = _DELETE_PRECONDITION_FAILURES.get(delete_precondition_key)
        if prior_failure:
            pytest.skip(f"删除共享前置条件未满足：{prior_failure}")
    if form_url:
        os.environ["EI_FORM_URL"] = form_url
        if requires_business_id:
            try:
                provisioned = _prepare_detail_action_context(
                    browser_page,
                    request,
                    form_url=form_url,
                    module_name=module_name,
                    action=action,
                    created=parent_record,
                )
            except DetailModulePreconditionError as exc:
                _DETAIL_MODULE_PRECONDITION_FAILURES.setdefault(record_key, str(exc))
                raise
            except Exception as exc:
                if common_delete_case is not None:
                    _DELETE_PRECONDITION_FAILURES[delete_precondition_key] = str(exc)
                raise
            if provisioned is not None:
                _DETAIL_PARENT_RESULTS[record_key] = provisioned
        elif browser_page.url == form_url:
            browser_page.reload(wait_until="domcontentloaded")
        else:
            browser_page.goto(form_url, wait_until="domcontentloaded")
    print(f"ACTION_START {action}", flush=True)
    if common_delete_case is not None:
        try:
            result = _run_common_delete_case(
                browser_page, request, common_delete_case, created=created,
            )
        except _DeleteSharedPreconditionError as exc:
            _DELETE_PRECONDITION_FAILURES[delete_precondition_key] = str(exc.original)
            raise exc.original from exc
    else:
        result = _run_selected_action(browser_page, request, action, created)
    _update_created_result_cache(record_key, action, result, created)
    print(f"ACTION_PASSED {action}", flush=True)


def _update_created_result_cache(record_key, action, result, created):
    if result and result.mode in ADD_VERIFIED_MODES:
        _CREATED_RESULTS[record_key] = result
        return
    if (
        action.startswith("删除")
        and getattr(result, "mode", "") == "add_and_delete_verified"
        and created is not None
        and str(getattr(result, "business_id", ""))
        == str(getattr(created, "business_id", ""))
    ):
        _CREATED_RESULTS.pop(record_key, None)


def _delete_candidate_key(record):
    business_id = str(getattr(record, "business_id", "") or "").strip()
    if business_id:
        return ("business_id", business_id)
    markers = tuple(
        str(marker).strip()
        for marker in (getattr(record, "record_markers", ()) or ())
        if str(marker).strip()
    )
    if markers:
        return ("markers", markers)
    return ("object", id(record))


def _run_delete_candidate_chain(
    driver, execute, *, created=None, allow_arbitrary_fallback: bool,
):
    seen = set()
    last_not_deletable = None

    def attempt(record):
        nonlocal last_not_deletable
        if record is None:
            return False, None
        key = _delete_candidate_key(record)
        if key in seen:
            return False, None
        seen.add(key)
        try:
            return True, execute(record)
        except RecordNotDeletableError as exc:
            last_not_deletable = exc
            return False, None

    providers = [
        ("current-created", lambda: created),
        ("current-run-registry", driver.find_reusable_automation_delete_record),
    ]
    if allow_arbitrary_fallback:
        providers.append(("first-available-row", driver.find_available_delete_record))

    for source, provider in providers:
        try:
            record = provider()
        except Exception as exc:
            raise _DeleteCandidatePreconditionError(exc) from exc
        completed, result = attempt(record)
        if completed:
            return result

    try:
        provisioned = driver.run(provision_only=True)
        if getattr(provisioned, "mode", "") != "add_provisioned":
            raise AssertionError(
                "删除前置创建返回了无效结果："
                f"{getattr(provisioned, 'mode', '') or 'unknown'}"
            )
    except Exception as exc:
        raise _DeleteCandidatePreconditionError(exc) from exc

    completed, result = attempt(provisioned)
    if completed:
        return result
    error = last_not_deletable or RecordNotDeletableError(
        "删除前置创建后仍没有可用的删除记录"
    )
    raise _DeleteCandidatePreconditionError(error) from error


def _run_common_delete_case(browser_page, request, case, *, created=None):
    driver = _crud_driver(browser_page, request)
    try:
        return _run_delete_candidate_chain(
            driver,
            lambda record: _run_delete_case_behavior(driver, record, case),
            created=created,
            allow_arbitrary_fallback=True,
        )
    except _DeleteCandidatePreconditionError as exc:
        raise _DeleteSharedPreconditionError(exc.original) from exc


def _run_delete_case_behavior(driver, record, case):
    if case.behavior == "confirm":
        result = driver.delete_created_record(record)
        assert result.mode == "add_and_delete_verified"
        return result
    result = driver.cancel_delete_created_record(record)
    assert result.mode == "delete_confirmation_cancelled"
    return result


def test_common_delete_prefers_current_created_record(monkeypatch):
    created = SimpleNamespace(mode="add_provisioned", business_id="created-1")
    calls = []

    class Driver:
        def find_reusable_automation_delete_record(self):
            pytest.fail("current created record must win before registry lookup")

        def find_available_delete_record(self):
            pytest.fail("current created record must win before arbitrary lookup")

        def run(self, *, provision_only=False):
            pytest.fail("current created record must win before provisioning")

        def delete_created_record(self, record):
            calls.append(("delete", record.business_id))
            return SimpleNamespace(
                mode="add_and_delete_verified", business_id=record.business_id,
            )

    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())

    result = _run_common_delete_case(
        object(), object(), CommonDeleteCase("DELETE-004", "删除成功", "删除成功"),
        created=created,
    )

    assert result.mode == "add_and_delete_verified"
    assert calls == [("delete", "created-1")]


def test_common_delete_falls_back_in_declared_candidate_order(monkeypatch):
    created = SimpleNamespace(mode="add_provisioned", business_id="created-1")
    registered = SimpleNamespace(
        mode="delete_reusable_record", business_id="registered-2",
    )
    arbitrary = SimpleNamespace(
        mode="delete_any_available", business_id="",
        record_markers=("__arbitrary_delete_row__",),
    )
    calls = []

    class Driver:
        def find_reusable_automation_delete_record(self):
            calls.append("registry")
            return registered

        def find_available_delete_record(self):
            calls.append("available")
            return arbitrary

        def run(self, *, provision_only=False):
            pytest.fail("an available row must win before provisioning")

        def delete_created_record(self, record):
            calls.append(("delete", record.mode))
            if record is not arbitrary:
                raise RecordNotDeletableError("candidate cannot be deleted")
            return SimpleNamespace(mode="add_and_delete_verified", business_id="3")

    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())

    result = _run_common_delete_case(
        object(), object(), CommonDeleteCase("DELETE-004", "删除成功", "删除成功"),
        created=created,
    )

    assert result.mode == "add_and_delete_verified"
    assert calls == [
        ("delete", "add_provisioned"),
        "registry",
        ("delete", "delete_reusable_record"),
        "available",
        ("delete", "delete_any_available"),
    ]


def test_common_delete_provisions_only_after_all_existing_candidates_are_absent(
    monkeypatch,
):
    provisioned = SimpleNamespace(mode="add_provisioned", business_id="created-4")
    calls = []

    class Driver:
        def find_reusable_automation_delete_record(self):
            calls.append("registry")
            return None

        def find_available_delete_record(self):
            calls.append("available")
            return None

        def run(self, *, provision_only=False):
            assert provision_only is True
            calls.append("provision")
            return provisioned

        def delete_created_record(self, record):
            assert record is provisioned
            calls.append(("delete", record.business_id))
            return SimpleNamespace(
                mode="add_and_delete_verified", business_id=record.business_id,
            )

    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())

    result = _run_common_delete_case(
        object(), object(), CommonDeleteCase("DELETE-004", "删除成功", "删除成功")
    )

    assert result.business_id == "created-4"
    assert calls == [
        "registry", "available", "provision", ("delete", "created-4"),
    ]


def test_delete_result_cache_is_removed_only_when_the_created_id_was_deleted():
    key = ("https://example.test/list", "component/index")
    created = SimpleNamespace(mode="add_and_detail_verified", business_id="created-1")
    _CREATED_RESULTS[key] = created

    _update_created_result_cache(
        key,
        "删除",
        SimpleNamespace(mode="delete_confirmation_cancelled", business_id="created-1"),
        created,
    )
    assert _CREATED_RESULTS[key] is created

    _update_created_result_cache(
        key,
        "删除",
        SimpleNamespace(mode="add_and_delete_verified", business_id="arbitrary-2"),
        created,
    )
    assert _CREATED_RESULTS[key] is created

    _update_created_result_cache(
        key,
        "删除",
        SimpleNamespace(mode="add_and_delete_verified", business_id="created-1"),
        created,
    )
    assert key not in _CREATED_RESULTS


def _run_selected_action(browser_page, request, action: str, created=None):
    assert action, "未提供 EI_ACTION，无法执行页面操作测试"

    if _action_path_steps():
        result = _crud_driver(browser_page, request).run()
        assert result.mode in ADD_VERIFIED_MODES, (
            f"嵌套操作“{' / '.join(_action_path_steps())}”未完成父表单保存及数据回读"
        )
        return result

    if action.startswith("删除"):
        driver = _crud_driver(browser_page, request)
        try:
            deleted = _run_delete_candidate_chain(
                driver,
                driver.delete_created_record,
                created=created,
                allow_arbitrary_fallback=True,
            )
        except _DeleteCandidatePreconditionError as exc:
            raise exc.original from exc
        assert deleted.mode == "add_and_delete_verified"
        return deleted

    if action.startswith(ADD_ACTIONS):
        branch_results = _crud_driver(browser_page, request).run_all_branches()
        assert branch_results, f"页面操作“{action}”未产生任何可验证的新增分支结果"
        for result in branch_results:
            assert result.mode in ADD_VERIFIED_MODES, (
                f"页面操作“{action}”存在未完成新增及保存后数据核对的分支"
            )
        return branch_results[-1]

    if _is_cancel_edit_action(action):
        return _run_cancel_edit_action(browser_page, action, created)

    if action.startswith(DIALOG_ACTIONS):
        target = _visible_action_for_created_record(browser_page, action, created)
        assert target is not None, f"页面上未找到可见操作按钮：{action}"
        before_dialogs = browser_page.locator(ACTION_FORM_SELECTOR).count()
        target.click()
        _wait_for_action_form_effect(browser_page, before_dialogs, action)
        driver = _crud_driver(browser_page, request)
        result = driver.save_open_dialog(
            action,
            established_business_id=getattr(created, "business_id", ""),
        )
        policy = CrudValidationPolicy.for_mode(
            getattr(getattr(driver, "data_strategy", None), "data_mode", "")
        )
        expected_modes = (
            {"dialog_action_detail_verified", "dialog_action_runtime_verified"}
            if policy.require_dialog_persistence_readback
            else {"dialog_action_saved"}
        )
        assert result.mode in expected_modes
        return result

    target = _visible_action(browser_page, action)
    assert target is not None, f"页面上未找到可见操作按钮：{action}"
    _execute_and_verify_action(browser_page, target, action)
    assert not browser_page.is_closed(), f"点击页面操作“{action}”后页面异常关闭"
    return None


def test_detail_action_context_provisions_parent_record_only_when_navigation_requests_it(monkeypatch):
    provisioned = SimpleNamespace(
        mode="add_provisioned",
        business_id="9001",
        record_markers=("AUTO_parent",),
    )
    calls = []

    class Driver:
        def run(self, *, provision_only=False):
            calls.append(provision_only)
            return provisioned

    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())

    def enter(_page, _url, _module, _action, **kwargs):
        assert kwargs["record_identity"] is None
        return kwargs["provision_record"]()

    monkeypatch.setitem(globals(), "_enter_available_detail_module", enter)

    result = _prepare_detail_action_context(
        object(),
        object(),
        form_url="/buildProject/detail",
        module_name="建设项目/详情/项目决策/新增",
        action="新增",
    )

    assert result is provisioned
    assert calls == [True]


def test_detail_parent_provision_restores_selected_page_tab(monkeypatch):
    calls = []
    provisioned = SimpleNamespace(mode="add_provisioned", business_id="9001")

    class Page:
        def goto(self, url, *, wait_until):
            calls.append(("goto", url, wait_until))

    class Driver:
        def run(self, *, provision_only=False):
            calls.append(("provision", provision_only))
            return provisioned

    monkeypatch.setenv("EI_PAGE_TAB", "项目投后管理")
    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())
    monkeypatch.setitem(
        globals(), "_activate_detail_parent_tab", lambda _page: calls.append(("tab",)),
    )
    monkeypatch.setitem(
        globals(),
        "_enter_available_detail_module",
        lambda *_args, **kwargs: kwargs["provision_record"](),
    )

    result = _prepare_detail_action_context(
        Page(),
        object(),
        form_url="https://example.test/ei-view/#/projectManage/detail",
        module_name="对外投资项目/项目投后管理/详情/投后管理/人员委派/新增",
        action="新增",
    )

    assert result is provisioned
    assert calls == [
        ("goto", "https://example.test/ei-view/#/projectManage", "domcontentloaded"),
        ("tab",),
        ("provision", True),
    ]
