import os
import re
import json
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
    detail_navigation_labels as _detail_navigation_labels,
    enter_available_detail_module as _enter_available_detail_module,
    enter_detail_record as _enter_detail_record,
    navigate_detail_module as _navigate_detail_module,
    visible_action as _visible_action,
)
from ei_ui_smoke.module_driver import ModuleSmokeDriver, RecordNotDeletableError
from ei_ui_smoke.project_progress_preconditions import project_progress_parent_provisioner
from ei_ui_smoke.module_resolver import resolve_form_code
from ei_ui_smoke.source_form import discover_custom_form_fields
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
}
_CREATED_RESULTS: dict[tuple[str, str], object] = {}
_DETAIL_PARENT_RESULTS: dict[tuple[str, str], object] = {}
_DELETE_PRECONDITION_FAILURES: dict[tuple[str, str], str] = {}


def _selected_actions() -> list[dict[str, object]]:
    raw = os.getenv("EI_ACTIONS_JSON", "").strip()
    if not raw:
        actions = [{
            "module_id": os.getenv("EI_MODULE_ID", ""),
            "module_name": os.getenv("EI_MODULE_NAME", ""),
            "action": os.getenv("EI_ACTION", "").strip(),
            "action_path": list(_action_path_steps()),
            "requires_business_id": os.getenv("EI_REQUIRES_BUSINESS_ID", "").lower() == "true",
        }]
    else:
        try:
            actions = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"EI_ACTIONS_JSON 不是有效 JSON：{exc}") from exc
        assert isinstance(actions, list) and actions, "EI_ACTIONS_JSON 必须是非空操作列表"
    actions = _coalesce_nested_actions(actions)
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


def _coalesce_nested_actions(actions: list[dict[str, object]]) -> list[dict[str, object]]:
    """Execute all actions inside one outer dialog during the same CRUD cycle."""
    cases: list[dict[str, object]] = []
    outer_cases: dict[tuple[str, str, str, str], dict[str, object]] = {}

    def key(item: dict[str, object], outer_action: str) -> tuple[str, str, str, str]:
        return (
            str(item.get("form_url") or ""), str(item.get("component") or ""),
            str(item.get("form_code") or ""), outer_action,
        )

    for raw_item in actions:
        item = dict(raw_item)
        path = item.get("action_path") or []
        if isinstance(path, list) and len(path) >= 3:
            outer_action = str(path[0])
            group_key = key(item, outer_action)
            parent = outer_cases.get(group_key)
            if parent is None:
                parent = dict(item)
                parent["action"] = outer_action
                parent["action_path"] = []
                parent["nested_action_paths"] = []
                cases.append(parent)
                outer_cases[group_key] = parent
            nested_paths = parent.setdefault("nested_action_paths", [])
            if path not in nested_paths:
                nested_paths.append(path)
            continue

        action = str(item.get("action") or "")
        group_key = key(item, action)
        existing = outer_cases.get(group_key)
        if existing is not None:
            item["nested_action_paths"] = existing.get("nested_action_paths", [])
            cases[cases.index(existing)] = item
            outer_cases[group_key] = item
        else:
            cases.append(item)
            outer_cases[group_key] = item
    return cases


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
    source_fields = discover_custom_form_fields(settings.source_root, component)
    dynamic_collections = load_dynamic_collection_specs(
        project_root / "data",
        form_code=form_code,
        component=component,
    )
    return ModuleSmokeDriver(
        browser_page,
        strategy,
        source_fields=source_fields,
        default_upload_file=data_pool.default_upload_file(),
        dynamic_collections=dynamic_collections,
        automation_record_registry=(
            project_root / "artifacts" / "automation-record-registry.json"
        ),
    )


def test_crud_driver_ignores_literal_none_form_code(monkeypatch):
    captured = {}

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
        f"{__name__}.discover_custom_form_fields", lambda *_args: []
    )
    monkeypatch.setattr(
        f"{__name__}.load_dynamic_collection_specs", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        f"{__name__}.ModuleSmokeDriver", lambda *_args, **_kwargs: object()
    )

    _crud_driver(object(), Request())

    assert captured == {"mode": "standard", "form_code": "BUILD_NETASSETS_MAINTAIL"}


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
    monkeypatch.setattr(f"{__name__}.discover_custom_form_fields", lambda *_args: [])
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
        provision_eligible_record=project_progress_parent_provisioner(
            browser_page, form_url, module_name,
        ),
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
    assert actions[1]["nested_action_paths"] == [["新增", "股权结构", "删除"]]


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


def test_common_delete_cases_share_a_failed_detail_precondition(monkeypatch):
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
        raise AssertionError("详情子模块无法建立新增前置数据")

    monkeypatch.setitem(globals(), "_prepare_detail_action_context", fail_precondition)

    with pytest.raises(AssertionError, match="无法建立新增前置数据"):
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


def test_destructive_action_requires_add_even_when_the_command_omits_it():
    assert _require_add_for_action({"action": "删除"}, "删除") is True
    assert _require_add_for_action({"require_add": ""}, "删除") is True
    assert _require_add_for_action({"action": "查询"}, "查询") is False


def test_nested_actions_share_one_outer_add_execution(monkeypatch):
    monkeypatch.setenv("EI_ACTIONS_JSON", json.dumps([
        {"module_id": "POOL::2", "action": "新增", "action_path": [], "form_url": "/pool"},
        {"module_id": "POOL::3", "action": "新增", "action_path": ["新增", "股权结构", "新增"], "form_url": "/pool"},
        {"module_id": "POOL::4", "action": "删除", "action_path": ["新增", "股权结构", "删除"], "form_url": "/pool"},
        {"module_id": "POOL::5", "action": "新增", "action_path": ["新增", "对外投资", "新增"], "form_url": "/pool"},
        {"module_id": "POOL::6", "action": "删除", "action_path": ["新增", "对外投资", "删除"], "form_url": "/pool"},
    ], ensure_ascii=False))

    actions = _selected_actions()

    assert len(actions) == 1
    assert actions[0]["action"] == "新增"
    assert actions[0]["nested_action_paths"] == [
        ["新增", "股权结构", "新增"], ["新增", "股权结构", "删除"],
        ["新增", "对外投资", "新增"], ["新增", "对外投资", "删除"],
    ]


def test_dialog_action_accepts_inline_edit_form(monkeypatch):
    page = _InlineEditPage()
    saved_actions = []

    monkeypatch.setitem(
        globals(),
        "_visible_action_for_created_record",
        lambda browser_page, action, created: page.edit_target,
    )
    monkeypatch.setitem(
        globals(),
        "_crud_driver",
        lambda browser_page, request: SimpleNamespace(
            save_open_dialog=lambda action: (
                saved_actions.append(action)
                or SimpleNamespace(mode="dialog_action_saved")
            )
        ),
    )

    result = _run_selected_action(page, request=None, action="编辑")

    assert result.mode == "dialog_action_saved"
    assert page.edit_target.clicked
    assert saved_actions == ["编辑"]


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
    if _require_add_for_action(action_case, action):
        os.environ["EI_REQUIRE_ADD"] = str(action_case.get("require_add") or "true")
    else:
        os.environ.pop("EI_REQUIRE_ADD", None)
    if action_path:
        os.environ["EI_ACTION_PATH"] = json.dumps(action_path, ensure_ascii=False)
    else:
        os.environ.pop("EI_ACTION_PATH", None)
    nested_action_paths = action_case.get("nested_action_paths") or []
    if nested_action_paths:
        os.environ["EI_ACTION_PATHS_JSON"] = json.dumps(
            nested_action_paths, ensure_ascii=False
        )
    else:
        os.environ.pop("EI_ACTION_PATHS_JSON", None)
    form_url = str(action_case.get("form_url") or os.getenv("EI_FORM_URL", ""))
    requires_business_id = bool(action_case.get("requires_business_id"))
    record_key = (form_url, os.getenv("EI_COMPONENT", ""))
    created = _CREATED_RESULTS.get(record_key)
    parent_record = _DETAIL_PARENT_RESULTS.get(record_key)
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
        prior_failure = _DELETE_PRECONDITION_FAILURES.get(record_key)
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
            except AssertionError as exc:
                if common_delete_case is not None:
                    _DELETE_PRECONDITION_FAILURES[record_key] = str(exc)
                raise
            if provisioned is not None:
                _DETAIL_PARENT_RESULTS[record_key] = provisioned
        elif browser_page.url == form_url:
            browser_page.reload(wait_until="domcontentloaded")
        else:
            browser_page.goto(form_url, wait_until="domcontentloaded")
    print(f"ACTION_START {action}", flush=True)
    if common_delete_case is not None:
        result = _run_common_delete_case(browser_page, request, common_delete_case)
    else:
        result = _run_selected_action(browser_page, request, action, created)
    if result and result.mode in ADD_VERIFIED_MODES:
        _CREATED_RESULTS[record_key] = result
    elif action.startswith("删除") and result:
        _CREATED_RESULTS.pop(record_key, None)
    print(f"ACTION_PASSED {action}", flush=True)


def _run_common_delete_case(browser_page, request, case):
    driver = _crud_driver(browser_page, request)
    created = driver.find_available_delete_record()
    if created is None:
        driver.run(provision_only=True)
        created = driver.find_available_delete_record()
    if created is None:
        raise RecordNotDeletableError("页面没有可用的删除记录")
    try:
        return _run_delete_case_behavior(driver, created, case)
    except RecordNotDeletableError:
        created = driver.run(provision_only=True)
        assert created.mode == "add_provisioned"
        return _run_delete_case_behavior(driver, created, case)


def _run_delete_case_behavior(driver, record, case):
    if case.behavior == "confirm":
        result = driver.delete_created_record(record)
        assert result.mode == "add_and_delete_verified"
        return result
    result = driver.cancel_delete_created_record(record)
    assert result.mode == "delete_confirmation_cancelled"
    return result


def test_common_delete_uses_available_record_without_creating(monkeypatch):
    reusable = SimpleNamespace(mode="delete_any_available", business_id="")
    calls = []

    class Driver:
        def find_available_delete_record(self):
            return reusable

        def run(self, *, provision_only=False):
            calls.append(provision_only)
            return SimpleNamespace(mode="add_provisioned")

        def delete_created_record(self, record):
            assert record is reusable
            return SimpleNamespace(mode="add_and_delete_verified")

    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())

    result = _run_common_delete_case(
        object(), object(), CommonDeleteCase("DELETE-004", "删除成功", "删除成功")
    )

    assert result.mode == "add_and_delete_verified"
    assert calls == []


def test_common_delete_provisions_only_when_no_available_record_exists(monkeypatch):
    calls = []

    class Driver:
        def find_available_delete_record(self):
            return None if not calls else SimpleNamespace(mode="delete_any_available")

        def run(self, *, provision_only=False):
            calls.append(provision_only)
            return SimpleNamespace(mode="add_provisioned")

        def delete_created_record(self, record):
            assert record.mode == "delete_any_available"
            return SimpleNamespace(mode="add_and_delete_verified")

    monkeypatch.setitem(globals(), "_crud_driver", lambda _page, _request: Driver())

    result = _run_common_delete_case(
        object(), object(), CommonDeleteCase("DELETE-004", "删除成功", "删除成功")
    )

    assert result.mode == "add_and_delete_verified"
    assert calls == [True]


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
        if created is None:
            created = driver.find_reusable_automation_delete_record()
            if created is None:
                created = driver.run(provision_only=True)
                assert created.mode == "add_provisioned"
        try:
            deleted = driver.delete_created_record(created)
        except RecordNotDeletableError:
            created = driver.run(provision_only=True)
            assert created.mode == "add_provisioned"
            deleted = driver.delete_created_record(created)
        assert deleted.mode == "add_and_delete_verified"
        return deleted

    if action.startswith(ADD_ACTIONS):
        result = _crud_driver(browser_page, request).run()
        assert result.mode in ADD_VERIFIED_MODES, f"页面操作“{action}”未完成新增及保存后数据核对"
        return result

    if _is_cancel_edit_action(action):
        return _run_cancel_edit_action(browser_page, action, created)

    if action.startswith(DIALOG_ACTIONS):
        target = _visible_action_for_created_record(browser_page, action, created)
        assert target is not None, f"页面上未找到可见操作按钮：{action}"
        before_dialogs = browser_page.locator(ACTION_FORM_SELECTOR).count()
        target.click()
        _wait_for_action_form_effect(browser_page, before_dialogs, action)
        result = _crud_driver(browser_page, request).save_open_dialog(action)
        assert result.mode == "dialog_action_saved"
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
