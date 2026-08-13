from __future__ import annotations

import atexit
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import date, timedelta
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Callable
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .common_field_cases import (
    BoundCommonCase,
    BoundCommonTransaction,
    DiscoveredCommonField,
    FieldConstraints,
    REQUIRED_ERRORS_RECOVER,
    REQUIRED_ERRORS_TRIGGER,
    branch_condition_key,
    discover_common_fields,
    required_validation_scenario,
    save_field_manifest,
)
from .failure_evidence import capture_failure_evidence, clear_failure_evidence
from .dom import scan_dom_fields
from .detail_navigation import visible_action
from .models import DomField, FieldDefinition, FixedType, ResolvedField
from .module_driver import (
    AUTOMATION_RECORD_PREFIXES,
    DIALOG,
    DynamicFieldContractError,
    EDITABLE_FORM_CONTROL,
    ModuleSmokeDriver,
)
from .dynamic_collections import DynamicCollectionSpec


FIELD_TYPE_TO_COMPONENT = {
    "amount": "ElInputNumber-NUMBER",
    "percentage": "ElInputNumber-NUMBER",
    "number": "ElInputNumber-NUMBER",
    "text": "ElInput-TEXT",
    "textarea": "PurvarTextarea-TEXTAREA",
    "phone": "ElInput-TEXT",
    "contact": "ElInput-TEXT",
    "email": "ElInput-TEXT",
    "password": "ElInput-TEXT",
    "id_card": "ElInput-TEXT",
    "date": "ElDatePicker-DATE",
    "year": "ElDatePicker-DATE",
    "datetime": "ElDatePicker-DATETIME",
    "select": "ElSelect-SELECT",
    "multi_select": "ElSelect-MULTIPLE",
    "radio": "ElRadioGroup-RADIO",
    "checkbox": "ElCheckboxGroup-CHECKBOX",
    "file": "ElUpload-FILE",
}
COMMON_ADD_ACTION_PREFIXES = ("新增", "添加", "新建", "创建")
COMMON_EDIT_ACTION_PREFIXES = ("编辑", "修改")

FORM_VALIDATION_SELECTOR = ".el-form-item__error:visible"
PAGE_ERROR_SELECTOR = (
    ".el-message--error:visible,.el-message--warning:visible,"
    ".el-notification--error:visible,.el-notification--warning:visible"
)
GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX = "附件为空且保存被全局必填提示阻止："
FORM_CONFIRM_SELECTOR = ".el-message-box:visible,[role='alertdialog']:visible"
FORM_POPPER_SELECTOR = (
    ".el-popper:visible,.el-select-dropdown:visible,[role=listbox]:visible"
)
FORM_CLOSE_SELECTOR = (
    ":scope > .el-dialog__header button.el-dialog__headerbtn:visible,"
    ":scope > .el-drawer__header button.el-drawer__close-btn:visible"
)
FORM_CLOSE_FALLBACK_SELECTOR = (
    ":scope > .el-dialog__header button[aria-label='Close']:visible,"
    ":scope > .el-dialog__header button[aria-label='Close this dialog']:visible,"
    ":scope > .el-dialog__header button[aria-label='关闭']:visible,"
    ":scope > .el-dialog__header button[aria-label='关闭此对话框']:visible,"
    ":scope > .el-drawer__header button[aria-label='Close']:visible,"
    ":scope > .el-drawer__header button[aria-label='Close this dialog']:visible,"
    ":scope > .el-drawer__header button[aria-label='关闭']:visible,"
    ":scope > .el-drawer__header button[aria-label='关闭此对话框']:visible"
)
FORM_TITLE_SELECTOR = (
    ":scope > .el-dialog__header .el-dialog__title:visible,"
    ":scope > .el-drawer__header .el-drawer__title:visible,"
    ":scope > .ant-modal-content .ant-modal-title:visible,"
    ":scope > .ant-drawer-content .ant-drawer-title:visible"
)
FORM_LEAVE_CONFIRM_PATTERN = re.compile(
    r"^\s*(?:确认离开|确定离开|不保存|不保存并离开|放弃|离开)\s*$"
)
FORM_GENERIC_CONFIRM_PATTERN = re.compile(r"^\s*(?:确定|确认)\s*$")
FORM_LEAVE_CONFIRM_CONTEXT_PATTERN = re.compile(
    r"(?:离开|未保存|放弃(?:修改|更改)|关闭.*(?:编辑|表单)|是否.*关闭)"
)
FORM_COMMAND_PATTERN = re.compile(
    r"^\s*(?:保存|确定|提交|提交审批|取消|取消编辑|关闭)\s*$"
)
INLINE_COMMAND_HOST_XPATH = (
    "xpath=ancestor::*["
    "contains(concat(' ',normalize-space(@class),' '),' detail-panel ') or "
    "contains(concat(' ',normalize-space(@class),' '),' base-info-page ')"
    "][1]"
)


@dataclass(frozen=True, slots=True)
class CommonFieldExecutionResult:
    case_id: str
    field_key: str
    outcome: str
    observed: str = ""


class SharedFormPreconditionError(AssertionError):
    """A stable form/context contract blocks every remaining Batch transaction."""


@dataclass(slots=True)
class _ActiveCommonFieldForm:
    scope: Any
    handle: Any
    url: str


class CommonFieldFormSession:
    """Keep one recoverable form instance until an operation terminates it."""

    _REUSABLE_OUTCOMES = {
        "save_blocked",
        "validation_recovered",
        "form_check_passed",
        "form_probe_passed",
        "choice_options_verified",
        "choice_single_selection_verified",
        "confirmation_cancelled",
        "command_not_applicable",
        "required_default_value_skipped",
        "dialog_title_verified",
    }
    _TERMINAL_OUTCOMES = {
        "cancel_verified",
        "close_verified",
        "command_verified",
        "rapid_click_blocked_by_ui",
    }

    def __init__(self, owner: "CommonFieldExecutor") -> None:
        self.owner = owner
        self.active: _ActiveCommonFieldForm | None = None

    def acquire(self):
        if self.active is not None and self._is_current(self.active):
            active = self.active
            self.owner._set_driver_form_scope(active.scope)
            try:
                self.owner._stabilize_reusable_form(active.scope)
                if not self._is_current(active):
                    raise AssertionError("复用前原表单实例已隐藏、替换或发生跳转")
            except Exception as exc:
                print(
                    f"COMMON_FORM_SESSION reuse_rejected error={exc}",
                    flush=True,
                )
                self.invalidate(close=True)
            else:
                print("COMMON_FORM_SESSION mode=reuse", flush=True)
                return active.scope
        if self.active is not None:
            self.invalidate(close=True)
        scope = self.owner._pin_form_scope(self.owner.open_fresh_add_form())
        self.owner._reset_driver_nested_evidence()
        try:
            handle = scope.element_handle()
        except Exception:
            handle = None
        self.active = _ActiveCommonFieldForm(
            scope=scope,
            handle=handle,
            url=str(getattr(self.owner.page, "url", "")),
        )
        self.owner._set_driver_form_scope(scope)
        print("COMMON_FORM_SESSION mode=new", flush=True)
        return scope

    def finish(
        self,
        results: Iterable[CommonFieldExecutionResult],
        *,
        recover: Callable[[], None] | None = None,
    ) -> None:
        results = tuple(results)
        active = self.active
        if active is None:
            return
        if self._has_terminal_effect(results):
            self.active = None
            self.owner._set_driver_form_scope(None)
            self.owner._close_session_scope(active.scope)
            print("COMMON_FORM_SESSION disposition=terminal", flush=True)
            return
        if not results or any(
            result.outcome not in self._REUSABLE_OUTCOMES for result in results
        ):
            self.invalidate(close=True)
            print("COMMON_FORM_SESSION disposition=discarded outcome=unsafe", flush=True)
            return
        if not self._is_current(active):
            self.invalidate(close=True)
            print("COMMON_FORM_SESSION disposition=discarded outcome=replaced", flush=True)
            return
        try:
            if recover is not None:
                recover()
            self.owner._stabilize_reusable_form(active.scope)
            if not self._is_current(active):
                raise AssertionError("恢复后原表单实例已隐藏、替换或发生跳转")
        except Exception as exc:
            print(
                f"COMMON_FORM_SESSION_RESET_FAILED error={exc}",
                flush=True,
            )
            self.invalidate(close=True)
            return
        print("COMMON_FORM_SESSION disposition=retained", flush=True)

    def invalidate(self, *, close: bool) -> None:
        active = self.active
        self.active = None
        self.owner._set_driver_form_scope(None)
        if close and active is not None:
            self.owner._close_session_scope(active.scope)

    def close(self) -> None:
        self.invalidate(close=True)

    def _is_current(self, active: _ActiveCommonFieldForm) -> bool:
        return (
            str(getattr(self.owner.page, "url", "")) == active.url
            and self._scope_visible(active)
        )

    @staticmethod
    def _scope_visible(active: _ActiveCommonFieldForm) -> bool:
        return CommonFieldExecutor._original_scope_is_visible(
            active.scope, active.handle
        )

    @classmethod
    def _has_terminal_effect(
        cls, results: Iterable[CommonFieldExecutionResult],
    ) -> bool:
        for result in results:
            outcome = result.outcome
            if (
                outcome in cls._TERMINAL_OUTCOMES
                or outcome.startswith("saved")
                or outcome.startswith("truncated_saved")
                or (
                    outcome == "save_blocked"
                    and result.field_key == "__submit_command"
                )
                or (
                    outcome == "command_not_applicable"
                    and "direct save" in result.observed.lower()
                )
            ):
                return True
        return False


class CommonFieldExecutor:
    """Execute bound common-field transactions without unnecessary business saves."""

    def __init__(
        self,
        page,
        data_strategy,
        *,
        source_fields: list[tuple[str, str, bool]] | None = None,
        default_upload_file: Path | None = None,
        dynamic_collections: list[DynamicCollectionSpec] | None = None,
        prepare_form_context: Callable[[object], None] | None = None,
        automation_record_registry: Path | None = None,
    ):
        self.page = page
        self.entry_url = (
            os.getenv("EI_ENTRY_URL") or os.getenv("EI_FORM_URL") or ""
        ).strip()
        self.prepare_form_context = prepare_form_context
        self.driver = ModuleSmokeDriver(
            page,
            data_strategy,
            source_fields=source_fields,
            default_upload_file=default_upload_file,
            dynamic_collections=dynamic_collections,
            automation_record_registry=automation_record_registry,
        )
        self._form_session = CommonFieldFormSession(self)
        self._record_identity_sequence = 0

    def _session(self) -> CommonFieldFormSession:
        session = getattr(self, "_form_session", None)
        if session is None:
            session = CommonFieldFormSession(self)
            self._form_session = session
        return session

    def close_form_session(self) -> None:
        self._session().close()

    def bind_page(self, page) -> None:
        """Rebind a shared executor after the browser fixture recovers its Page."""
        if page is self.page:
            return
        try:
            self.close_form_session()
        except Exception:
            pass
        self.page = page
        self.driver.bind_page(page)
        self._reset_driver_nested_evidence()
        self._form_session = CommonFieldFormSession(self)

    def _set_driver_form_scope(self, scope) -> None:
        if hasattr(self, "driver"):
            self.driver._common_form_scope = scope

    def _reset_driver_nested_evidence(self) -> None:
        reset = getattr(getattr(self, "driver", None), "_reset_nested_evidence", None)
        if callable(reset):
            reset()

    def discover(
        self,
        manifest_path: Path,
        definitions: Iterable[FieldDefinition] = (),
    ) -> list[DiscoveredCommonField]:
        self._reset_driver_nested_evidence()
        scope = self.open_fresh_add_form()
        try:
            getattr(
                self.driver,
                "_prepare_implicit_required_nested_baselines",
                lambda _scope: {},
            )(scope)
            fields = discover_common_fields(
                self._wait_for_fields_stable(scope), definitions
            )
            fields = self._merge_discovered_fields(
                fields,
                self._discover_baseline_branch_fields(scope, fields, definitions),
            )
            fields.extend(self._discover_form_commands(scope))
            if not fields:
                raise AssertionError("新增表单中没有发现可应用通用规则的字段")
            save_field_manifest(manifest_path, fields)
            return fields
        finally:
            self.close_form()

    @staticmethod
    def _merge_discovered_fields(
        base: Iterable[DiscoveredCommonField],
        extra: Iterable[DiscoveredCommonField],
    ) -> list[DiscoveredCommonField]:
        merged: dict[tuple[str, tuple[tuple[str, str], ...]], DiscoveredCommonField] = {}
        for field in [*base, *extra]:
            key = (
                field.field_key,
                branch_condition_key(field.branch_conditions),
            )
            if not field.field_key or key in merged:
                continue
            merged[key] = field
        return list(merged.values())

    def _discover_baseline_branch_fields(
        self,
        scope,
        fields: Iterable[DiscoveredCommonField],
        definitions: Iterable[FieldDefinition],
    ) -> list[DiscoveredCommonField]:
        """Reveal conditional controls gated by the initial baseline select value.

        Some add forms render only a driver field at first, then show the real
        date/amount/text controls after that driver select gets a valid value.
        Discovery must mirror the baseline branch used by execution; otherwise
        the manifest marks those controls as not_applicable while later command
        cases still fill them through generated DOM ids.
        """

        if os.getenv("EI_COMMON_FIELD_BRANCH_DISCOVERY", "true").lower() in {
            "0",
            "false",
            "no",
        }:
            return []
        base_fields = list(fields)
        base_keys = {field.field_key for field in base_fields if field.field_key}
        discovered: list[DiscoveredCommonField] = []
        for field in base_fields:
            if field.readonly or field.kind not in {"select", "radio"}:
                continue
            try:
                option_labels = self._branch_option_labels(field, scope)
                if not option_labels:
                    continue
                max_options = max(
                    1,
                    int(os.getenv("EI_COMMON_FIELD_BRANCH_MAX_OPTIONS", "8")),
                )
                if len(option_labels) > max_options:
                    print(
                        "COMMON_FIELD_BRANCH_DISCOVERY_SKIPPED "
                        f"field={field.field_key} reason=too_many_options "
                        f"count={len(option_labels)} max={max_options}",
                        flush=True,
                    )
                    continue
                for option_index, option_label in enumerate(option_labels):
                    self._select_branch_option(
                        field,
                        scope,
                        option_label,
                        option_index=option_index,
                    )
                    self.page.wait_for_timeout(500)
                    branch_fields = discover_common_fields(
                        self._wait_for_fields_stable(
                            scope,
                            timeout_ms=int(
                                os.getenv(
                                    "EI_COMMON_FIELD_BRANCH_STABLE_TIMEOUT_MS", "6000"
                                )
                            ),
                            stable_ms=int(
                                os.getenv("EI_COMMON_FIELD_BRANCH_STABLE_MS", "700")
                            ),
                        ),
                        definitions,
                    )
                    condition = ((field.field_key, option_label),)
                    branch_added = 0
                    for branch_field in branch_fields:
                        if branch_field.field_key in base_keys:
                            continue
                        discovered.append(
                            replace(
                                branch_field,
                                branch_conditions=condition,
                            )
                        )
                        branch_added += 1
                    print(
                        "COMMON_FIELD_BRANCH_DISCOVERED "
                        f"driver={field.field_key} option={option_label!r} "
                        f"fields={branch_added}",
                        flush=True,
                    )
            except Exception as exc:
                print(
                    "COMMON_FIELD_BRANCH_DISCOVERY_SKIPPED "
                    f"field={field.field_key} reason={exc}",
                    flush=True,
                )
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
        return discovered

    def _branch_option_labels(
        self, field: DiscoveredCommonField, scope,
    ) -> list[str]:
        locator = self._choice_locator_for_branch_driver(field, scope)
        if locator is None or not locator.count() or not locator.is_visible():
            return []
        if field.kind == "radio":
            option_nodes = self._radio_branch_options(locator)
            return self._filter_branch_option_labels(
                self._unique_visible_texts(option_nodes)
            )
        locator.click(force=True)
        self.page.wait_for_timeout(150)
        try:
            options = self._owned_select_options(locator)
            return self._filter_branch_option_labels(self._visible_texts(options))
        finally:
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass

    def _choice_locator_for_branch_driver(
        self, field: DiscoveredCommonField, scope,
    ):
        locator = self._choice_locator_by_field_label(field, scope)
        if locator is not None:
            return locator
        if field.kind in {"select", "radio"}:
            runtime = self._runtime_choice_locator(field.field_key, field.kind, scope)
            if runtime is not None and runtime.count():
                return runtime.first
        current_dom = self._dom_for_discovered_field(field, scope)
        if current_dom.selector:
            return scope.locator(current_dom.selector).first
        return None

    @classmethod
    def _filter_branch_option_labels(cls, labels: Iterable[str]) -> list[str]:
        result = []
        for raw in labels:
            label = str(raw or "").strip()
            normalized = re.sub(r"\s+", "", label)
            if not normalized:
                continue
            if normalized in {"请选择", "请先选择", "暂无数据", "无数据", "全部"}:
                continue
            result.append(label)
        return list(dict.fromkeys(result))

    def _select_branch_option(
        self,
        field: DiscoveredCommonField,
        scope,
        option_label: str,
        *,
        option_index: int | None = None,
    ) -> None:
        locator = self._choice_locator_for_branch_driver(field, scope)
        if locator is None or not locator.count() or not locator.is_visible():
            raise AssertionError(
                f"无法定位联动分支驱动字段：{field.field_key} ({field.label})"
            )
        if self._branch_option_already_selected(locator, option_label):
            return
        if field.kind == "radio":
            self._select_radio_branch_option(locator, option_label)
            return
        locator.click(force=True)
        self.page.wait_for_timeout(150)
        options = self._owned_select_options(locator)
        labels = self._visible_texts(options)
        target_index = self._matching_option_index(labels, option_label)
        if target_index is None and option_index is not None and option_index < options.count():
            target_index = option_index
        if target_index is None:
            raise AssertionError(
                f"联动分支字段 {field.field_key} 没有选项：{option_label}"
            )
        options.nth(target_index).click(force=True)

    def _branch_option_already_selected(self, locator, option_label: str) -> bool:
        wanted = self._normalize_choice_label(option_label)
        if not wanted:
            return False
        candidates: list[str] = []
        for method_name in ("input_value", "text_content"):
            method = getattr(locator, method_name, None)
            if not callable(method):
                continue
            try:
                value = method()
            except Exception:
                value = ""
            if value:
                candidates.append(str(value))
        try:
            owner = locator.locator(
                "xpath=ancestor-or-self::*["
                "contains(concat(' ',normalize-space(@class),' '),' el-select ') or "
                "contains(concat(' ',normalize-space(@class),' '),' el-radio-group ') or "
                "contains(concat(' ',normalize-space(@class),' '),' el-form-item ') or "
                "@prop][1]"
            )
            if owner.count():
                selected = owner.locator(
                    ".el-select__selected-item:not(.is-transparent),"
                    ".el-select__tags-text,.el-tag__content,"
                    ".el-radio.is-checked,[role=radio][aria-checked=true]"
                )
                candidates.extend(self._visible_texts(selected))
        except Exception:
            pass
        return any(
            self._normalize_choice_label(candidate) == wanted
            for candidate in candidates
        )

    def _radio_branch_options(self, locator):
        item = locator.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ') "
            "or contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ') "
            "or @prop][1]"
        )
        scope = item if item.count() else locator.locator("xpath=..")
        return scope.locator(
            ".el-radio:not(.is-disabled),label:has(input[type=radio]:not(:disabled)),"
            "[role=radio]:not([aria-disabled=true])"
        )

    def _select_radio_branch_option(self, locator, option_label: str) -> None:
        options = self._radio_branch_options(locator)
        labels = self._visible_texts(options)
        target_index = self._matching_option_index(labels, option_label)
        if target_index is None:
            raise AssertionError(f"单选联动分支没有选项：{option_label}")
        options.nth(target_index).click(force=True)

    def _matching_option_index(
        self, labels: Iterable[str], expected: str,
    ) -> int | None:
        wanted = self._normalize_choice_label(expected)
        if not wanted:
            return None
        normalized = [self._normalize_choice_label(label) for label in labels]
        for index, label in enumerate(normalized):
            if label == wanted:
                return index
        for index, label in enumerate(normalized):
            if wanted in label or label in wanted:
                return index
        return None

    def _run_in_form_session(self, operation):
        session = self._session()
        scope = session.acquire()
        try:
            results, recover = operation(scope)
        except BaseException as exc:
            if not isinstance(exc, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                try:
                    self._capture_failure_once(str(exc))
                except Exception as evidence_error:
                    print(
                        "COMMON_FORM_FAILURE_EVIDENCE_CAPTURE_FAILED "
                        f"error={evidence_error}",
                        flush=True,
                    )
            try:
                session.invalidate(close=True)
            except Exception as close_error:
                print(
                    "COMMON_FORM_CLEANUP_FAILED_AFTER_PRIMARY_ERROR "
                    f"error={close_error}",
                    flush=True,
                )
            raise
        session.finish(results, recover=recover)
        return tuple(results)

    def run_recoverable_form_check(
        self,
        case_id: str,
        field_key: str,
        operation: Callable[[Any], Any],
    ) -> Any:
        """Run a non-submitting assertion in the shared recoverable form."""
        operation_result = None

        def session_operation(scope):
            nonlocal operation_result
            operation_result = operation(scope)
            result = CommonFieldExecutionResult(
                case_id=case_id,
                field_key=field_key,
                outcome="form_check_passed",
            )
            return (result,), None

        self._run_in_form_session(session_operation)
        return operation_result

    def _apply_case_branch_conditions(
        self, case: BoundCommonCase, scope,
    ) -> bool:
        return self._apply_branch_conditions(case.branch_conditions, scope)

    def _apply_branch_conditions(
        self,
        branch_conditions: Iterable[tuple[Any, Any]] | None,
        scope,
    ) -> bool:
        conditions = branch_condition_key(branch_conditions)
        if not conditions:
            return False
        for field_key, option_label in conditions:
            driver = self._branch_driver_field(field_key, scope)
            self._select_branch_option(driver, scope, option_label)
        self._wait_for_fields_stable(
            scope,
            timeout_ms=int(os.getenv("EI_COMMON_FIELD_BRANCH_STABLE_TIMEOUT_MS", "6000")),
            stable_ms=int(os.getenv("EI_COMMON_FIELD_BRANCH_STABLE_MS", "700")),
        )
        print(
            "COMMON_BRANCH_APPLIED "
            f"conditions={conditions!r}",
            flush=True,
        )
        return True

    def _branch_driver_field(
        self, field_key: str, scope,
    ) -> DiscoveredCommonField:
        fields = discover_common_fields(self._scan_fields(scope))
        for field in fields:
            if field.field_key == field_key and field.kind in {"select", "radio"}:
                return field
        for kind in ("select", "radio"):
            selector = self._choice_control_selector(field_key, kind)
            if not selector:
                continue
            try:
                locator = scope.locator(selector)
                if locator.count():
                    return DiscoveredCommonField(
                        field_key,
                        field_key,
                        kind,
                        kind,
                        selector,
                        FieldConstraints(),
                    )
            except Exception:
                continue
        raise AssertionError(f"无法定位联动分支驱动字段：{field_key}")

    def execute(
        self,
        case: BoundCommonCase,
        *,
        clear_evidence: bool = True,
    ) -> CommonFieldExecutionResult:
        if clear_evidence:
            clear_failure_evidence(self.page)
        print(
            f"COMMON_CASE_START id={case.pytest_id} scenario={case.scenario!r} "
            f"expected={case.expected_type}",
            flush=True,
        )

        def operation(scope):
            # A framework rerender can replace the pinned dialog between form-open
            # and the first case. Refresh before any baseline fill or save lookup.
            if callable(getattr(scope, "evaluate", None)):
                self._scan_fields(scope)
                scope = self._active_form_scope() or scope
            recover = None
            if case.field_type == "dialog_title":
                result = self._execute_dialog_title_case(case, scope)
            elif case.field_type.endswith("_command"):
                result = self._execute_command_case(case, scope)
                if result.outcome == "save_blocked":
                    recover = lambda: self._restore_valid_form(scope)
            elif case.field_type == "required":
                self._apply_case_branch_conditions(case, scope)
                result = self._execute_required_case(case, scope)
                if result.outcome == "save_blocked":
                    recover = lambda: self._restore_required_case(case, scope)
            elif case.field_type in {"select", "radio"}:
                self._apply_case_branch_conditions(case, scope)
                current = self._current_field(case, scope)
                result = self._execute_choice_case(case, current, scope)
            elif case.field_type == "file":
                self._apply_case_branch_conditions(case, scope)
                current = self._wait_for_current_field(case, scope)
                result = self._execute_file_case(case, current, scope)
            else:
                # Fill a valid baseline first so Save exercises the target case rather
                # than unrelated required-field validation.
                self._apply_case_branch_conditions(case, scope)
                submitted = self._fill_valid_baseline(scope)
                current = self._wait_for_current_field(case, scope)
                scope = self._active_form_scope() or scope
                locator = self._locator(current)
                before = self._input_value(locator)
                requested = self._case_input_value(case, current, before)
                self._replace_value(current, requested)
                actual = self._input_value(locator)
                submitted[case.field_key] = actual
                result = self._submit_case(
                    case, scope, current, submitted, requested, actual, before
                )
                if result.outcome == "save_blocked":
                    recover = lambda: self._restore_target_after_validation(
                        case, scope, before
                    )
            return (result,), recover

        result = self._run_in_form_session(operation)[0]
        self._log_result(case, result)
        return result

    def _execute_file_case(
        self,
        case: BoundCommonCase,
        field: DiscoveredCommonField,
        scope,
    ) -> CommonFieldExecutionResult:
        """Replace one edit attachment and prove it survives the saved-record readback."""
        if field.kind != "file":
            raise AssertionError(f"附件用例未绑定到文件控件：{field.field_key}/{field.kind}")
        # A normal baseline deliberately preserves populated attachments. EDIT-004
        # adds one uniquely named fixture to only its target attachment control.
        submitted = self._fill_valid_baseline(scope, upload_attachments=False)
        before = self._attachment_names(field, scope)
        tracker = self.driver._start_attachment_lifecycle_tracking()
        try:
            file_name = self._upload_edit_attachment(field, scope, tracker)
            result = self._submit_case(
                case,
                scope,
                field,
                submitted,
                file_name,
                file_name,
                ", ".join(before),
                required_codes=set(),
                require_edit_and_detail=True,
                attachment_lifecycle_tracker=tracker,
            )
            self._assert_attachment_persisted(field, file_name, before)
            return replace(
                result,
                observed=f"uploaded={file_name}; existing={', '.join(before) or 'none'}",
            )
        finally:
            self.driver._stop_attachment_lifecycle_tracking(tracker)

    def _file_input(self, field: DiscoveredCommonField, scope):
        locator = scope.locator(field.selector).first
        if not locator.count():
            raise AssertionError(f"无法定位附件控件：{field.field_key}")
        return locator

    def _attachment_names(self, field: DiscoveredCommonField, scope) -> list[str]:
        file_input = self._file_input(field, scope)
        try:
            names = file_input.evaluate(
                """el => {
                    const owner = el.closest('.el-form-item,.purvar_form_item,.ant-form-item') || el.parentElement;
                    if (!owner) return [];
                    const nodes = owner.querySelectorAll(
                        '.el-upload-list__item-name,.el-upload-list__item a,.ant-upload-list-item-name,' +
                        '[data-upload-name],[class*="file-name"],[class*="filename"]'
                    );
                    const values = [
                        ...(el.files ? Array.from(el.files, file => file.name) : []),
                        ...Array.from(nodes, node => node.getAttribute('title') || node.textContent || '')
                    ];
                    return [...new Set(values.map(value => String(value).trim()).filter(Boolean))];
                }"""
            )
        except Exception as exc:
            raise AssertionError(f"无法读取附件显示值：{field.field_key}") from exc
        return [str(name).strip() for name in names if str(name).strip()]

    def _upload_edit_attachment(
        self, field: DiscoveredCommonField, scope, lifecycle_tracker,
    ) -> str:
        source = Path(self.driver.default_upload_file or "")
        if not source.is_file():
            raise AssertionError(f"EDIT-004 缺少可上传的测试附件：{source}")
        file_input = self._file_input(field, scope)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="EDIT-004_", suffix=source.suffix,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copy2(source, temporary)
            file_input.set_input_files(str(temporary))
            self.driver._wait_for_file_upload(
                file_input, temporary.name, tracker=lifecycle_tracker,
            )
            self.driver._wait_for_attachment_lifecycle(
                lifecycle_tracker, phase="EDIT-004 附件上传"
            )
            if self.driver._file_input_has_failure(file_input):
                raise AssertionError(f"EDIT-004 附件上传失败：{temporary.name}")
            names = self._attachment_names(field, scope)
            if temporary.name not in names:
                raise AssertionError(
                    "EDIT-004 上传后未显示目标附件："
                    f"expected={temporary.name!r}, actual={names!r}"
                )
            return temporary.name
        finally:
            self._cleanup_temporary_upload(temporary)

    @classmethod
    def _cleanup_temporary_upload(cls, temporary: Path) -> None:
        """Remove a browser upload fixture without masking the test outcome."""
        for attempt in range(5):
            if cls._try_unlink_temporary_upload(temporary):
                return
            if attempt < 4:
                time.sleep(0.1)
        atexit.register(cls._try_unlink_temporary_upload, temporary)

    @staticmethod
    def _try_unlink_temporary_upload(temporary: Path) -> bool:
        try:
            temporary.unlink(missing_ok=True)
            return True
        except OSError:
            return False

    @staticmethod
    def _attachment_readback_matches(
        expected_name: str,
        existing_names: list[str],
        observed_names: list[str],
    ) -> bool:
        if expected_name in observed_names:
            return True
        existing = {name.strip() for name in existing_names if name.strip()}
        server_added = [name for name in observed_names if name not in existing]
        suffix = Path(expected_name).suffix.lower()
        return bool(suffix and any(Path(name).suffix.lower() == suffix for name in server_added))

    def _assert_attachment_persisted(
        self,
        original: DiscoveredCommonField,
        expected_name: str,
        existing_names: list[str],
    ) -> None:
        deadline = time.monotonic() + 10
        observed: list[str] = []
        while time.monotonic() < deadline:
            scope = self.driver._wait_for_readback_form_scope()
            fields = discover_common_fields(self._scan_fields(scope))
            target = next(
                (
                    field for field in fields
                    if field.kind == "file"
                    and (
                        field.field_key == original.field_key
                        or field.label.strip() == original.label.strip()
                    )
                ),
                None,
            )
            if target is not None:
                observed = self._attachment_names(target, scope)
                if self._attachment_readback_matches(
                    expected_name, existing_names, observed
                ):
                    return
            self.page.wait_for_timeout(200)
        raise AssertionError(
            f"EDIT-004 保存后编辑页未回显替换附件："
            f"field={original.field_key}, expected={expected_name!r}, "
            f"existing={existing_names!r}, actual={observed!r}"
        )

    def execute_transaction(
        self, transaction: BoundCommonTransaction,
    ) -> tuple[CommonFieldExecutionResult, ...]:
        if transaction.execution_mode == "probe_persistence":
            return self._execute_probe_persistence_transaction(transaction)
        if transaction.execution_mode == "attachment_persistence":
            return self._execute_attachment_persistence_transaction(transaction)
        if self._is_page_required_transaction(transaction):
            return self._execute_page_required_transaction(transaction)
        if len(transaction.cases) == 1:
            return (self.execute(transaction.cases[0]),)
        if all(
            case.expected_type == "field_error"
            and case.field_type in {"text", "textarea", "number"}
            for case in transaction.cases
        ):
            return self._execute_field_error_transaction(transaction)
        if not all(
            case.expected_type in {"accepted", "safe_handling"}
            and case.field_type in {"text", "textarea", "number"}
            for case in transaction.cases
        ):
            return self._execute_sequential_transaction(transaction)
        clear_failure_evidence(self.page)
        print(
            f"COMMON_TRANSACTION_START id={transaction.transaction_id} "
            f"cases={[case.pytest_id for case in transaction.cases]!r}",
            flush=True,
        )

        def operation(scope):
            self._apply_case_branch_conditions(transaction.cases[0], scope)
            submitted = self._fill_valid_baseline(scope, upload_attachments=False)
            prepared = []
            for case in transaction.cases:
                current = self._current_field(case, scope)
                locator = self._locator(current)
                before = self._input_value(locator)
                requested = self._case_input_value(case, current, before)
                self._replace_value(current, requested)
                actual = self._input_value(locator)
                if (
                    case.expected_type == "accepted"
                    and str(actual) != str(requested)
                ):
                    raise AssertionError(
                        f"事务 {transaction.transaction_id} 中合法值未被控件完整接受："
                        f"field={case.field_key}, requested={requested!r}, actual={actual!r}"
                    )
                submitted[case.field_key] = actual
                prepared.append((case, current, before, requested, actual))

            first_case, first_field, first_before, first_requested, first_actual = prepared[0]
            rendered_text_expectations = {
                case.field_key: (case.field_label, str(actual))
                for case, _field, _before, _requested, actual in prepared
                if self._requires_rendered_whitespace_check(case, actual)
            }
            saved = self._submit_case(
                first_case,
                scope,
                first_field,
                submitted,
                first_requested,
                first_actual,
                first_before,
                required_codes={case.field_key for case in transaction.cases},
                rendered_text_expectations=rendered_text_expectations,
            )
            results = tuple(
                CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    saved.outcome,
                    self._transaction_result_observed(
                        saved, case, actual, transaction.transaction_id
                    ),
                )
                for case, _field, _before, _requested, actual in prepared
            )
            for case, result in zip(transaction.cases, results):
                self._log_result(case, result)
            print(
                f"COMMON_TRANSACTION_RESULT id={transaction.transaction_id} "
                f"outcome={saved.outcome} fields={len(results)}",
                flush=True,
            )
            return results, None

        return self._run_in_form_session(operation)

    def _execute_attachment_persistence_transaction(
        self, transaction: BoundCommonTransaction,
    ) -> tuple[CommonFieldExecutionResult, ...]:
        """Persist compatible attachment fields through one physical Save."""
        clear_failure_evidence(self.page)
        print(
            f"COMMON_TRANSACTION_START id={transaction.transaction_id} "
            f"mode=attachment_persistence "
            f"cases={[case.pytest_id for case in transaction.cases]!r}",
            flush=True,
        )

        def operation(scope):
            self._apply_case_branch_conditions(transaction.cases[0], scope)
            submitted = self._fill_valid_baseline(scope, upload_attachments=False)
            tracker = self.driver._start_attachment_lifecycle_tracking()
            prepared = []
            try:
                for case in transaction.cases:
                    field = self._wait_for_current_field(case, scope)
                    if field.kind != "file":
                        raise AssertionError(
                            f"附件事务未绑定到文件控件：{case.field_key}/{field.kind}"
                        )
                    before = self._attachment_names(field, scope)
                    file_name = self._upload_edit_attachment(field, scope, tracker)
                    prepared.append((case, field, before, file_name))

                first_case, first_field, first_before, first_name = prepared[0]
                saved = self._submit_case(
                    first_case,
                    scope,
                    first_field,
                    submitted,
                    first_name,
                    first_name,
                    ", ".join(first_before),
                    required_codes=set(),
                    require_edit_and_detail=True,
                    attachment_lifecycle_tracker=tracker,
                )
                results = []
                for case, field, before, file_name in prepared:
                    self._assert_attachment_persisted(field, file_name, before)
                    results.append(
                        CommonFieldExecutionResult(
                            case.case_id,
                            case.field_key,
                            saved.outcome,
                            "uploaded=" + file_name
                            + "; existing=" + (", ".join(before) or "none")
                            + f"; transaction={transaction.transaction_id}; shared_save=true",
                        )
                    )
                result_tuple = tuple(results)
                for case, result in zip(transaction.cases, result_tuple):
                    self._log_result(case, result)
                print(
                    f"COMMON_TRANSACTION_RESULT id={transaction.transaction_id} "
                    f"outcome={saved.outcome} fields={len(result_tuple)}",
                    flush=True,
                )
                return result_tuple, None
            finally:
                self.driver._stop_attachment_lifecycle_tracking(tracker)

        return self._run_in_form_session(operation)

    def _execute_probe_persistence_transaction(
        self, transaction: BoundCommonTransaction,
    ) -> tuple[CommonFieldExecutionResult, ...]:
        """Probe reversible values in one form, then persist one value per field."""
        clear_failure_evidence(self.page)
        print(
            f"COMMON_TRANSACTION_START id={transaction.transaction_id} "
            f"mode=probe_persistence "
            f"cases={[case.pytest_id for case in transaction.cases]!r}",
            flush=True,
        )

        def operation(scope):
            self._apply_case_branch_conditions(transaction.cases[0], scope)
            submitted = self._fill_valid_baseline(
                scope, upload_attachments=False
            )
            prepared = []
            representatives: dict[str, tuple[int, BoundCommonCase, Any, Any]] = {}
            mutation_requests = []

            def request_started(request) -> None:
                method = str(getattr(request, "method", "")).upper()
                url = str(getattr(request, "url", ""))
                if method not in {"POST", "PUT", "PATCH"}:
                    return
                if not ModuleSmokeDriver._is_business_mutation_url(url):
                    return
                try:
                    payload = self.driver._request_payload(request)
                    if ModuleSmokeDriver._is_non_business_mutation_endpoint(
                        url, payload
                    ):
                        return
                except Exception:
                    pass
                mutation_requests.append(request)

            self.page.on("request", request_started)
            representative_indexes: set[int] = set()
            representative_fields = []
            try:
                for case_index, case in enumerate(transaction.cases):
                    try:
                        current = self._current_field(case, scope)
                        locator = self._locator(current)
                        before = self._input_value(locator)
                        requested = self._case_input_value(case, current, before)
                        self._replace_value(current, requested)
                        actual = self._input_value(locator)
                        if str(actual) != str(requested):
                            raise AssertionError(
                                f"事务 {transaction.transaction_id} 中合法值未被控件完整接受："
                                f"field={case.field_key}, requested={requested!r}, "
                                f"actual={actual!r}"
                            )
                        self.page.wait_for_timeout(
                            max(0, min(int(os.getenv("EI_COMMON_PROBE_SETTLE_MS", "100")), 1_000))
                        )
                        error_text = self._visible_error_text(scope, current)
                        if error_text:
                            raise AssertionError(
                                f"事务 {transaction.transaction_id} 的合法探测值触发校验："
                                f"field={case.field_key}, error={error_text}"
                            )
                        prepared.append((case, before, requested, actual))
                        representatives[case.field_key] = (
                            case_index, case, current, actual
                        )
                        self._replace_value(current, before)
                        restored = self._input_value(self._locator(current))
                        if str(restored) != str(before):
                            raise AssertionError(
                                f"探测后字段未恢复：field={case.field_key}, "
                                f"expected={before!r}, actual={restored!r}"
                            )
                    except Exception as exc:
                        # A shared browser session is an optimization, not shared
                        # failure attribution.  The triggering binding failed;
                        # untouched later bindings are explicitly blocked.
                        failed = CommonFieldExecutionResult(
                            case.case_id, case.field_key, "execution_failed", str(exc)
                        )
                        results = [
                            CommonFieldExecutionResult(
                                item.case_id,
                                item.field_key,
                                "form_probe_passed",
                                f"transaction={transaction.transaction_id}; persistence_skipped=true",
                            )
                            for item, _before, _requested, _actual in prepared
                        ]
                        results.append(failed)
                        results.extend(
                            CommonFieldExecutionResult(
                                item.case_id,
                                item.field_key,
                                "blocked_by_transaction_failure",
                                f"transaction={transaction.transaction_id}; root_case={case.pytest_id}; error={exc}",
                            )
                            for item in transaction.cases[case_index + 1:]
                        )
                        for item, result in zip(transaction.cases, results):
                            self._log_result(item, result)
                        print(
                            f"COMMON_TRANSACTION_FAILURE id={transaction.transaction_id} "
                            f"root_case={case.pytest_id} error={exc}", flush=True,
                        )
                        return tuple(results), None
                self.page.wait_for_timeout(
                    max(
                        100,
                        min(
                            int(
                                os.getenv(
                                    "EI_COMMON_PROBE_MUTATION_QUIET_MS", "750"
                                )
                            ),
                            3_000,
                        ),
                    )
                )
                if mutation_requests:
                    raise AssertionError(
                        "表单探测期间产生业务写请求，禁止继续复用："
                        f"{self._mutation_request_summary(mutation_requests)}"
                    )

                representative_indexes = {
                    item[0] for item in representatives.values()
                }
                for _index, case, _previous_field, actual in representatives.values():
                    current = self._current_field(case, scope)
                    self._replace_value(current, actual)
                    persisted_actual = self._input_value(self._locator(current))
                    if str(persisted_actual) != str(actual):
                        raise AssertionError(
                            f"代表值写入失败：field={case.field_key}, "
                            f"expected={actual!r}, actual={persisted_actual!r}"
                        )
                    submitted[case.field_key] = persisted_actual
                    representative_fields.append((case, current, persisted_actual))
                self.page.wait_for_timeout(
                    max(
                        100,
                        min(
                            int(
                                os.getenv(
                                    "EI_COMMON_PROBE_MUTATION_QUIET_MS", "750"
                                )
                            ),
                            3_000,
                        ),
                    )
                )
                if mutation_requests:
                    raise AssertionError(
                        "代表值写入期间产生业务写请求，禁止再次保存："
                        f"{self._mutation_request_summary(mutation_requests)}"
                    )
            finally:
                if hasattr(self.page, "remove_listener"):
                    self.page.remove_listener("request", request_started)

            first_case, first_field, first_actual = representative_fields[0]
            saved = self._submit_case(
                first_case,
                scope,
                first_field,
                submitted,
                first_actual,
                first_actual,
                "",
                required_codes=set(representatives),
            )
            results = []
            for case_index, (case, before, requested, actual) in enumerate(prepared):
                if case_index in representative_indexes:
                    result = CommonFieldExecutionResult(
                        case.case_id,
                        case.field_key,
                        saved.outcome,
                        f"persisted={actual!r}; transaction={transaction.transaction_id}; "
                        "representative=true",
                    )
                else:
                    result = CommonFieldExecutionResult(
                        case.case_id,
                        case.field_key,
                        "form_probe_passed",
                        f"requested={requested!r}; actual={actual!r}; "
                        f"restored={before!r}; transaction={transaction.transaction_id}",
                    )
                results.append(result)
                self._log_result(case, result)
            print(
                f"COMMON_TRANSACTION_RESULT id={transaction.transaction_id} "
                f"mode=probe_persistence outcome={saved.outcome} "
                f"probes={len(results) - len(representative_indexes)} "
                f"persisted_fields={len(representative_indexes)}",
                flush=True,
            )
            return tuple(results), None

        return self._run_in_form_session(operation)

    @staticmethod
    def _transaction_result_observed(
        saved: CommonFieldExecutionResult,
        case: BoundCommonCase,
        actual: Any,
        transaction_id: str,
    ) -> str:
        if saved.outcome == "safe_content_rejected":
            return (
                f"{saved.observed}; field={case.field_key}; "
                f"transaction={transaction_id}; retained_probe_record=true"
            )
        return f"persisted={actual!r}; transaction={transaction_id}; retained=true"

    def _execute_field_error_transaction(
        self, transaction: BoundCommonTransaction,
    ) -> tuple[CommonFieldExecutionResult, ...]:
        """Submit compatible invalid field cases in one physical form transaction."""
        clear_failure_evidence(self.page)
        print(
            f"COMMON_TRANSACTION_START id={transaction.transaction_id} "
            f"mode=field_validation cases={[case.pytest_id for case in transaction.cases]!r}",
            flush=True,
        )

        def operation(scope):
            self._apply_case_branch_conditions(transaction.cases[0], scope)
            submitted = self._fill_valid_baseline(scope, upload_attachments=False)
            prepared = []
            for case in transaction.cases:
                current = self._current_field(case, scope)
                locator = self._locator(current)
                before = self._input_value(locator)
                requested = self._case_input_value(case, current, before)
                self._replace_value(current, requested)
                actual = self._input_value(locator)
                submitted[case.field_key] = actual
                prepared.append((case, current, before, requested, actual))

            first_case, first_field, first_before, first_requested, first_actual = prepared[0]
            saved = self._submit_case(
                first_case,
                scope,
                first_field,
                submitted,
                first_requested,
                first_actual,
                first_before,
                required_codes={case.field_key for case in transaction.cases},
            )
            if saved.outcome == "truncated_saved_verified_and_retained":
                results = tuple(
                    CommonFieldExecutionResult(
                        case.case_id,
                        case.field_key,
                        saved.outcome,
                        (
                            f"requested={requested!r}, saved={actual!r}, "
                            f"before={before!r}; transaction={transaction.transaction_id}; "
                            "retained=true"
                        ),
                    )
                    for case, _field, before, requested, actual in prepared
                )
                for case, result in zip(transaction.cases, results):
                    self._log_result(case, result)
                print(
                    f"COMMON_TRANSACTION_RESULT id={transaction.transaction_id} "
                    f"mode=field_validation outcome={saved.outcome} fields={len(results)}",
                    flush=True,
                )
                return results, None
            if saved.outcome == "save_blocked":
                results = tuple(
                    CommonFieldExecutionResult(
                        case.case_id,
                        case.field_key,
                        "save_blocked",
                        self._visible_error_text(scope, field) or saved.observed,
                    )
                    for case, field, _before, _requested, _actual in prepared
                )

                def recover_all():
                    for case, _field, before, _requested, _actual in prepared:
                        self._restore_target_after_validation(case, scope, before)

                for case, result in zip(transaction.cases, results):
                    self._log_result(case, result)
                print(
                    f"COMMON_TRANSACTION_RESULT id={transaction.transaction_id} "
                    f"mode=field_validation outcome=save_blocked fields={len(results)}",
                    flush=True,
                )
                return results, recover_all
            raise AssertionError(
                f"事务 {transaction.transaction_id} 返回了不支持的字段校验结果："
                f"{saved.outcome}"
            )

        return self._run_in_form_session(operation)

    def execute_transaction_once(
        self,
        transaction: BoundCommonTransaction,
        cache: dict[str, tuple[tuple[CommonFieldExecutionResult, ...] | None, Exception | None]],
    ) -> tuple[CommonFieldExecutionResult, ...]:
        """Execute one physical transaction once across separate report items."""
        key = transaction.transaction_id
        if key not in cache:
            try:
                cache[key] = (self.execute_transaction(transaction), None)
            except Exception as exc:
                cache[key] = (None, exc)
        results, error = cache[key]
        if error is not None:
            raise error
        if results is None:
            raise AssertionError(f"事务 {transaction.transaction_id} 没有生成执行结果")
        return results

    def _execute_sequential_transaction(
        self, transaction: BoundCommonTransaction,
    ) -> tuple[CommonFieldExecutionResult, ...]:
        """Run compatible logical cases in one recoverable form session."""
        clear_failure_evidence(self.page)
        print(
            f"COMMON_TRANSACTION_START id={transaction.transaction_id} "
            f"mode=sequential cases={[case.pytest_id for case in transaction.cases]!r}",
            flush=True,
        )
        results = []
        for case in transaction.cases:
            try:
                result = self.execute(case, clear_evidence=False)
            except Exception as exc:
                result = CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "execution_failed",
                    str(exc),
                )
                self._log_result(case, result)
            results.append(result)
        print(
            f"COMMON_TRANSACTION_RESULT id={transaction.transaction_id} "
            f"mode=sequential fields={len(results)}",
            flush=True,
        )
        return tuple(results)

    @staticmethod
    def _is_page_required_transaction(
        transaction: BoundCommonTransaction,
    ) -> bool:
        return bool(transaction.cases) and all(
            required_validation_scenario(case) for case in transaction.cases
        )

    def _execute_page_required_transaction(
        self, transaction: BoundCommonTransaction,
    ) -> tuple[CommonFieldExecutionResult, ...]:
        clear_failure_evidence(self.page)
        print(
            f"COMMON_REQUIRED_BATCH_START id={transaction.transaction_id} "
            f"fields={[case.field_key for case in transaction.cases]!r}",
            flush=True,
        )
        session = self._session()
        if getattr(session, "active", None) is not None:
            # Page-level required checks intentionally mutate the whole form.
            # A previous select/radio assertion may have moved the form onto a
            # different linkage branch, so start from a clean add form.
            session.invalidate(close=True)
        scope = session.acquire()
        try:
            if transaction.cases:
                self._apply_case_branch_conditions(transaction.cases[0], scope)
            requested_keys = list(dict.fromkeys(
                case.field_key for case in transaction.cases
            ))
            trigger_cases = [
                case for case in transaction.cases
                if required_validation_scenario(case) == REQUIRED_ERRORS_TRIGGER
            ]
            recovery_cases = [
                case for case in transaction.cases
                if required_validation_scenario(case) == REQUIRED_ERRORS_RECOVER
            ]

            def scan_current_required_fields(current_scope):
                getattr(
                    self.driver,
                    "_prepare_implicit_required_nested_baselines",
                    lambda _scope: {},
                )(current_scope)
                runtime_fields = discover_common_fields(
                    self._wait_for_fields_stable(current_scope)
                )
                return {
                    field.field_key: field for field in runtime_fields
                }

            fields_by_key = scan_current_required_fields(scope)
            missing = [
                field_key for field_key in requested_keys
                if field_key not in fields_by_key
            ]
            if missing:
                print(
                    "COMMON_REQUIRED_BATCH_REOPEN_FOR_BRANCH "
                    f"missing={missing!r}",
                    flush=True,
                )
                session.invalidate(close=True)
                scope = session.acquire()
                if transaction.cases:
                    self._apply_case_branch_conditions(transaction.cases[0], scope)
                fields_by_key = scan_current_required_fields(scope)
                missing = [
                    field_key for field_key in requested_keys
                    if field_key not in fields_by_key
                ]
            if missing:
                raise AssertionError(
                    "当前表单无法定位本轮字段发现中的必填控件："
                    + ", ".join(missing)
                )

            skipped_fields: dict[str, str] = {}
            target_fields: dict[str, DiscoveredCommonField] = {}
            for case in transaction.cases:
                field = fields_by_key[case.field_key]
                if field.field_key in target_fields or field.field_key in skipped_fields:
                    continue
                if self._required_control_has_value(field):
                    try:
                        self._clear_required_control(field)
                    except AssertionError:
                        skipped_fields[case.field_key] = (
                            "控件存在非空默认值且页面没有可用清空操作"
                        )
                        continue
                if self._required_control_has_value(field):
                    skipped_fields[case.field_key] = "控件清空后仍保留非空默认值"
                    continue
                target_fields[field.field_key] = field

            if not target_fields:
                raise AssertionError("新增表单没有可执行空值校验的必填控件")

            submitted = {field_key: "" for field_key in target_fields}
            first_case = next(
                case for case in transaction.cases
                if case.field_key in target_fields
            )
            first_field = target_fields[first_case.field_key]
            blocked = self._submit_case(
                first_case,
                scope,
                first_field,
                submitted,
                "",
                "",
                "",
                required_codes=set(target_fields),
            )

            error_snapshot = self._wait_for_required_error_snapshot(target_fields)
            current_fields = {
                field.field_key: field
                for field in discover_common_fields(
                    self._wait_for_fields_stable(scope)
                )
            }
            conditional_fields = self._conditionally_inactive_required_keys(
                target_fields,
                error_snapshot,
                current_fields,
            )
            initial_errors: dict[str, tuple[str, str]] = {}
            for field_key, field in target_fields.items():
                error_text = error_snapshot[field_key]
                if field_key in conditional_fields:
                    initial_errors[field_key] = (
                        "required_condition_inactive",
                        "批量清空其他字段后当前字段的运行时必填条件不再成立",
                    )
                elif not error_text and self._required_file_global_block_is_valid(
                    field, blocked
                ):
                    initial_errors[field_key] = (
                        "save_blocked",
                        GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX + blocked.observed,
                    )
                elif not error_text:
                    initial_errors[field_key] = (
                        "required_error_missing",
                        f"未发现字段级必填提示；global={blocked.observed or 'none'}",
                    )
                elif not self._required_message_is_correct(field, error_text):
                    initial_errors[field_key] = ("required_message_incorrect", error_text)
                else:
                    initial_errors[field_key] = ("save_blocked", error_text)

            # A page-level submit can stop at an earlier required radio/select,
            # leaving a required attachment without its own field message.  Once
            # the ordinary required controls are restored, submit again with just
            # that attachment empty so its validation is evidenced independently.
            for field_key, field in target_fields.items():
                outcome, _observed = initial_errors[field_key]
                if field.kind != "file" or outcome != "required_error_missing":
                    continue
                matching_cases = [
                    case for case in trigger_cases if case.field_key == field_key
                ]
                if not matching_cases:
                    continue
                initial_errors[field_key] = self._isolate_required_file_validation(
                    scope,
                    matching_cases[0],
                    field,
                    target_fields,
                )

            results_by_case: dict[
                tuple[str, str, int], CommonFieldExecutionResult
            ] = {}
            for case in trigger_cases:
                if case.field_key in skipped_fields:
                    outcome = "required_default_value_skipped"
                    observed = skipped_fields[case.field_key]
                elif case.field_key in conditional_fields:
                    continue
                else:
                    outcome, observed = initial_errors[case.field_key]
                results_by_case[(case.case_id, case.field_key, case.source_row)] = (
                    CommonFieldExecutionResult(
                        case.case_id, case.field_key, outcome, observed
                    )
                )

            if recovery_cases:
                self._recover_required_fields(
                    scope,
                    [
                        case for case in recovery_cases
                        if case.field_key not in conditional_fields
                    ],
                    target_fields,
                    skipped_fields,
                    initial_errors,
                    results_by_case,
                )

            if conditional_fields:
                self._execute_conditional_required_fields(
                    scope,
                    conditional_fields,
                    trigger_cases,
                    recovery_cases,
                    results_by_case,
                )

            results = tuple(
                results_by_case[(case.case_id, case.field_key, case.source_row)]
                for case in transaction.cases
            )
            for case, result in zip(transaction.cases, results):
                self._log_result(case, result)
            print(
                f"COMMON_REQUIRED_BATCH_RESULT id={transaction.transaction_id} "
                f"checked={len(target_fields)} skipped={len(skipped_fields)} "
                f"missing={sum(result.outcome == 'required_error_missing' for result in results)} "
                f"incorrect={sum(result.outcome == 'required_message_incorrect' for result in results)}",
                flush=True,
            )
            session.finish(
                results,
                recover=lambda: self._restore_valid_form(scope),
            )
            return results
        except Exception as exc:
            self._capture_failure_once(str(exc))
            try:
                session.invalidate(close=True)
            except Exception as close_error:
                print(
                    "COMMON_FORM_CLEANUP_FAILED_AFTER_PRIMARY_ERROR "
                    f"error={close_error}",
                    flush=True,
                )
            raise

    @staticmethod
    def _conditionally_inactive_required_keys(
        target_fields: dict[str, DiscoveredCommonField],
        error_snapshot: dict[str, str],
        current_fields: dict[str, DiscoveredCommonField],
    ) -> set[str]:
        """Find required fields whose rule was disabled by the batch clear."""
        return {
            field_key
            for field_key in target_fields
            if not error_snapshot.get(field_key)
            and field_key in current_fields
            and not current_fields[field_key].constraints.required
        }

    def _execute_conditional_required_fields(
        self,
        scope,
        field_keys: set[str],
        trigger_cases: list[BoundCommonCase],
        recovery_cases: list[BoundCommonCase],
        results_by_case: dict[
            tuple[str, str, int], CommonFieldExecutionResult
        ],
    ) -> None:
        """Validate conditional required fields without clearing their drivers."""
        ordered_keys = list(dict.fromkeys(
            case.field_key
            for case in (*trigger_cases, *recovery_cases)
            if case.field_key in field_keys
        ))
        for field_key in ordered_keys:
            matching_triggers = [
                case for case in trigger_cases if case.field_key == field_key
            ]
            matching_recoveries = [
                case for case in recovery_cases if case.field_key == field_key
            ]
            if not matching_triggers:
                raise AssertionError(
                    f"条件必填字段缺少触发用例，无法验证：{field_key}"
                )

            self._restore_valid_form(scope)
            trigger_case = matching_triggers[0]
            field = self._current_field(trigger_case, scope)
            if not field.constraints.required:
                outcome = "required_error_missing"
                observed = "恢复合法基线后字段的运行时必填条件仍未成立"
            else:
                if self._required_control_has_value(field):
                    self._clear_required_control(field)
                if self._required_control_has_value(field):
                    outcome = "required_error_missing"
                    observed = "条件必填字段清空后仍有值"
                else:
                    blocked = self._submit_case(
                        trigger_case,
                        scope,
                        field,
                        {field_key: ""},
                        "",
                        "",
                        "",
                        required_codes={field_key},
                    )
                    snapshot = self._wait_for_required_error_snapshot({
                        field_key: field,
                    })
                    error_text = snapshot.get(field_key, "")
                    if not error_text and self._required_file_global_block_is_valid(
                        field, blocked
                    ):
                        outcome = "save_blocked"
                        observed = (
                            GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX + blocked.observed
                        )
                    elif not error_text:
                        outcome = "required_error_missing"
                        observed = (
                            "条件必填字段单独清空后未发现字段级必填提示；"
                            f"global={blocked.observed or 'none'}"
                        )
                    elif not self._required_message_is_correct(field, error_text):
                        outcome = "required_message_incorrect"
                        observed = error_text
                    else:
                        outcome = "save_blocked"
                        observed = error_text

            for case in matching_triggers:
                results_by_case[(case.case_id, case.field_key, case.source_row)] = (
                    CommonFieldExecutionResult(
                        case.case_id, case.field_key, outcome, observed
                    )
                )

            conditional_initial = {field_key: (outcome, observed)}
            if matching_recoveries:
                self._recover_required_fields(
                    scope,
                    matching_recoveries,
                    {field_key: field},
                    {},
                    conditional_initial,
                    results_by_case,
                )
            else:
                self._restore_valid_form(scope)

        self._restore_valid_form(scope)

    def _recover_required_fields(
        self,
        scope,
        cases: list[BoundCommonCase],
        target_fields: dict[str, DiscoveredCommonField],
        skipped_fields: dict[str, str],
        initial_errors: dict[str, tuple[str, str]],
        results_by_case: dict[tuple[str, str, int], CommonFieldExecutionResult],
    ) -> None:
        start_url = self.page.url
        try:
            scope_handle = scope.element_handle()
        except Exception:
            scope_handle = None
        requests = []

        def listener(request) -> None:
            requests.append(request)

        if hasattr(self.page, "on"):
            self.page.on("request", listener)
        filled_fields: list[DiscoveredCommonField] = []
        semantic_filled_fields: set[str] = set()
        pending = [case for case in cases if case.field_key not in skipped_fields]
        global_only_file_keys = {
            case.field_key
            for case in pending
            if case.field_key in target_fields
            and target_fields[case.field_key].kind == "file"
            and initial_errors.get(case.field_key, ("", ""))[1].startswith(
                GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX
            )
        }
        try:
            for case in cases:
                key = (case.case_id, case.field_key, case.source_row)
                if case.field_key in skipped_fields:
                    results_by_case[key] = CommonFieldExecutionResult(
                        case.case_id,
                        case.field_key,
                        "required_default_value_skipped",
                        skipped_fields[case.field_key],
                    )
                    continue
                outcome, observed = initial_errors[case.field_key]
                if outcome != "save_blocked":
                    results_by_case[key] = CommonFieldExecutionResult(
                        case.case_id, case.field_key, outcome, observed
                    )
                    continue
                field = target_fields[case.field_key]
                submitted = self.driver._fill_dialog(
                    only_codes={case.field_key.lower()}
                )
                if field.kind == "file" and not self._required_control_has_value(field):
                    self._upload_required_attachment(scope, field)
                self._blur_required_control(field)
                deadline = time.monotonic() + 3
                while (
                    self._target_form_errors(field).count()
                    and time.monotonic() < deadline
                ):
                    self.page.wait_for_timeout(100)
                if self._target_form_errors(field).count():
                    raise AssertionError(
                        f"输入合法值并失焦后必填提示未消失：{case.field_key}"
                    )
                semantic_value = self._submitted_choice_has_value(field, submitted)
                if not self._required_control_has_value(field) and not semantic_value:
                    raise AssertionError(
                        f"必填提示消失后字段值未保留：{case.field_key}"
                    )
                if semantic_value:
                    semantic_filled_fields.add(field.field_key)
                filled_fields.append(field)
                for filled in filled_fields:
                    if (
                        not self._required_control_has_value(filled)
                        and filled.field_key not in semantic_filled_fields
                    ):
                        raise AssertionError(
                            f"后续字段操作导致已填写值丢失：{filled.field_key}"
                        )
                if (
                    self.page.url != start_url
                    or not self._original_scope_is_visible(scope, scope_handle)
                ):
                    raise AssertionError(
                        "必填提示恢复期间页面发生刷新、跳转或原表单被替换"
                    )
                results_by_case[key] = CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "validation_recovered",
                    "required error cleared; "
                    f"value={submitted.get(case.field_key, '<filled>')!r}",
                )
            remaining_errors = [
                case.field_key
                for case in cases
                if case.field_key not in skipped_fields
                and initial_errors.get(case.field_key, ("", ""))[0] == "save_blocked"
                and case.field_key not in global_only_file_keys
                and case.field_key in target_fields
                and self._target_form_errors(target_fields[case.field_key]).count()
            ]
            if remaining_errors:
                raise AssertionError(
                    "全部必填字段恢复后仍存在字段级提示："
                    + ", ".join(sorted(set(remaining_errors)))
                )
            if global_only_file_keys:
                missing_files = [
                    field_key for field_key in global_only_file_keys
                    if not self._required_control_has_value(target_fields[field_key])
                ]
                if missing_files:
                    raise AssertionError(
                        "必填附件恢复后仍为空：" + ", ".join(missing_files)
                    )
                self._wait_for_global_required_warning_to_clear()
            business_requests = [
                request
                for request in requests
                if str(getattr(request, "method", "GET")).upper()
                in {"POST", "PUT", "PATCH"}
                and any(
                    token in str(getattr(request, "url", "")).lower()
                    for token in ("/add", "/save", "/create", "/insert")
                )
            ]
            if business_requests:
                raise AssertionError("必填提示恢复期间意外产生业务保存请求")
        finally:
            if hasattr(self.page, "remove_listener"):
                self.page.remove_listener("request", listener)

    def _isolate_required_file_validation(
        self,
        scope,
        case: BoundCommonCase,
        target: DiscoveredCommonField,
        target_fields: dict[str, DiscoveredCommonField],
    ) -> tuple[str, str]:
        """Re-submit one required attachment after unrelated blockers are repaired."""
        submitted: dict[str, Any] = {}
        for field_key, field in target_fields.items():
            if field_key == target.field_key:
                continue
            submitted.update(
                self.driver._fill_dialog(only_codes={field.field_key.lower()})
            )
            if field.kind == "file" and not self._required_control_has_value(field):
                self._upload_required_attachment(scope, field)
            self._blur_required_control(field)

        if self._required_control_has_value(target):
            return (
                "required_default_value_skipped",
                "附件存在不可清空的默认值，无法建立空值必填前置条件",
            )
        submitted[target.field_key] = ""
        blocked = self._submit_case(
            case,
            scope,
            target,
            submitted,
            "",
            "",
            "",
            required_codes={target.field_key},
        )
        error_text = self._target_required_error_text(target)
        if error_text and self._required_message_is_correct(target, error_text):
            return "save_blocked", error_text
        if self._required_file_global_block_is_valid(target, blocked):
            return (
                "save_blocked",
                GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX + blocked.observed,
            )
        return (
            "required_error_missing",
            "恢复其他必填字段后附件单独为空提交，仍未发现附件级必填提示；"
            f"global={blocked.observed or 'none'}",
        )

    def _wait_for_required_error_snapshot(
        self,
        target_fields: dict[str, DiscoveredCommonField],
        *,
        timeout_seconds: float = 3,
        minimum_observation_seconds: float = 1,
        quiet_seconds: float = 0.5,
    ) -> dict[str, str]:
        started_at = time.monotonic()
        changed_at = started_at
        snapshot = {
            field_key: self._target_required_error_text(field)
            for field_key, field in target_fields.items()
        }
        while time.monotonic() - started_at < timeout_seconds:
            self.page.wait_for_timeout(100)
            current = {
                field_key: self._target_required_error_text(field)
                for field_key, field in target_fields.items()
            }
            now = time.monotonic()
            if current != snapshot:
                snapshot = current
                changed_at = now
            if (
                now - started_at >= minimum_observation_seconds
                and now - changed_at >= quiet_seconds
            ):
                break
        return snapshot

    def _blur_required_control(self, field: DiscoveredCommonField) -> None:
        if field.kind == "file":
            return
        locator = self._locator(field)
        try:
            locator.evaluate("element => element.blur()")
        except Exception:
            if hasattr(self.page, "keyboard"):
                self.page.keyboard.press("Tab")

    def _upload_required_attachment(
        self, scope, field: DiscoveredCommonField,
    ) -> None:
        file_input = scope.locator(field.selector).first
        if not file_input.count():
            raise AssertionError(f"无法定位必填附件控件：{field.field_key}")
        owner = file_input.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ')][1]"
        ).first
        if not owner.count():
            raise AssertionError(f"无法定位必填附件所属表单项：{field.field_key}")
        uploaded = self.driver._upload_default_attachments(owner)
        if not uploaded and not self._required_control_has_value(field):
            raise AssertionError(f"必填附件上传未生效：{field.field_key}")

    def _discover_form_commands(self, scope) -> list[DiscoveredCommonField]:
        definitions = (
            ("save_command", "保存", re.compile(r"^\s*(?:保存|确定)\s*$")),
            ("submit_command", "提交", re.compile(r"^\s*(?:提交|提交审批)\s*$")),
            ("cancel_command", "取消", re.compile(r"^\s*(?:取消|取消编辑|关闭)\s*$")),
        )
        result = []
        command_scope = self._command_scope(scope)
        buttons = command_scope.locator("button:visible")
        for field_type, label, pattern in definitions:
            matched = buttons.filter(has_text=pattern)
            if not matched.count():
                continue
            result.append(DiscoveredCommonField(
                field_key=f"__{field_type}", label=label, field_type=field_type,
                kind="button", selector="", constraints=FieldConstraints(),
                source="dom-command",
            ))
        close = self._form_close_button(scope)
        if close is not None and close.is_visible():
            result.append(DiscoveredCommonField(
                field_key="__close_command", label="关闭图标 X",
                field_type="close_command", kind="button", selector="",
                constraints=FieldConstraints(), source="dom-command",
            ))
        title = self._form_title(scope)
        if title is not None and title.count() and title.is_visible():
            result.append(DiscoveredCommonField(
                field_key="__dialog_title", label="对话框名称",
                field_type="dialog_title", kind="text", selector="",
                constraints=FieldConstraints(), source="dom-dialog-title",
            ))
        return result

    @staticmethod
    def _form_title(scope):
        try:
            direct = scope.locator(FORM_TITLE_SELECTOR).first
            if direct.count():
                return direct
            host = scope.locator(
                "xpath=ancestor::*[@role='dialog' or "
                "contains(concat(' ',normalize-space(@class),' '),' el-dialog ') or "
                "contains(concat(' ',normalize-space(@class),' '),' el-drawer ') or "
                "contains(concat(' ',normalize-space(@class),' '),' ant-modal ') or "
                "contains(concat(' ',normalize-space(@class),' '),' ant-drawer ')][1]"
            ).first
            if host.count():
                title = host.locator(FORM_TITLE_SELECTOR).first
                if title.count():
                    return title
        except Exception:
            pass
        return None

    def _execute_dialog_title_case(self, case: BoundCommonCase, scope):
        title = self._form_title(scope)
        if title is None or not title.count() or not title.is_visible():
            raise AssertionError("当前编辑表单不是对话框，无法检查对话框名称")
        text = (title.inner_text() or title.text_content() or "").strip()
        if not text:
            raise AssertionError("编辑对话框标题为空")
        return CommonFieldExecutionResult(
            case.case_id, case.field_key, "dialog_title_verified", text
        )

    @staticmethod
    def _form_command_buttons(scope):
        return scope.locator("button:visible").filter(
            has_text=FORM_COMMAND_PATTERN
        )

    def _command_scope(self, scope):
        """Keep dialog commands scoped, with a bounded host fallback for inline edit."""
        try:
            if self._form_command_buttons(scope).count():
                return scope
            inline_host = scope.locator(INLINE_COMMAND_HOST_XPATH).first
            if (
                inline_host.count()
                and inline_host.is_visible()
                and self._form_command_buttons(inline_host).count()
            ):
                return inline_host
        except Exception:
            pass
        return scope

    @staticmethod
    def _form_close_button(scope, *, strict: bool = False):
        close = scope.locator(FORM_CLOSE_SELECTOR)
        if close.count():
            return close.last
        fallback = scope.locator(FORM_CLOSE_FALLBACK_SELECTOR)
        fallback_count = fallback.count()
        if fallback_count == 1:
            return fallback.first
        if fallback_count > 1 and strict:
            raise AssertionError(
                "新增表单头部存在多个关闭图标候选，无法唯一定位右上角 X"
            )
        return None

    def _execute_command_case(self, case: BoundCommonCase, scope):
        if case.field_type == "close_command":
            button = self._form_close_button(scope, strict=True)
        else:
            pattern = {
                "save_command": re.compile(r"^\s*(?:保存|确定)\s*$"),
                "submit_command": re.compile(r"^\s*(?:提交|提交审批)\s*$"),
                "cancel_command": re.compile(r"^\s*(?:取消|取消编辑|关闭)\s*$"),
            }[case.field_type]
            button = self._command_scope(scope).locator(
                "button:visible"
            ).filter(has_text=pattern).last
        if button is None or not button.count() or not button.is_visible():
            raise AssertionError(f"新增表单缺少{case.field_label}按钮")
        if case.field_type in {"cancel_command", "close_command"}:
            scope_handle = scope.element_handle()
            requests = []
            listener = lambda request: requests.append(request)
            self.page.on("request", listener)
            try:
                modified = self._modify_form_for_abandonment(scope)
                existing_confirmation_handles = self._visible_confirmation_handles()
                button.click(timeout=3_000)
                self._confirm_form_cancellation_if_present(
                    scope,
                    scope_handle,
                    ignored_confirmation_handles=existing_confirmation_handles,
                )
                try:
                    scope_handle.wait_for_element_state("hidden", timeout=3000)
                except Exception:
                    if (
                        scope_handle.is_visible()
                        and not self._inline_edit_has_exited(scope)
                    ):
                        action = "关闭图标 X" if case.field_type == "close_command" else "取消"
                        raise AssertionError(f"点击{action}后表单未关闭或退出编辑态")
                # Request events fire when a request is issued, so this bounded quiet
                # window also catches saves whose response is delayed.
                self.page.wait_for_timeout(500)
                mutations = self._business_mutation_requests(requests, modified)
                if mutations:
                    raise AssertionError(
                        f"点击{case.field_label}关闭表单时产生业务保存请求："
                        f"{mutations[0].method} {mutations[0].url}"
                    )
                outcome = (
                    "close_verified"
                    if case.field_type == "close_command"
                    else "cancel_verified"
                )
                return CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    outcome,
                    f"modified={','.join(modified)}; form closed without save",
                )
            finally:
                if hasattr(self.page, "remove_listener"):
                    self.page.remove_listener("request", listener)

        if case.field_type == "submit_command" and "必填校验" in case.scenario:
            return self._execute_submit_required_validation(
                case, scope, button
            )

        optional_clear_case = (
            case.field_type in {"save_command", "submit_command"}
            and "非必填" in case.scenario
            and "清空" in case.scenario
        )
        submitted = (
            self._fill_valid_baseline(scope, upload_attachments=False)
            if optional_clear_case
            else self._fill_valid_baseline(scope)
        )
        self._ensure_command_required_baseline(scope, submitted)
        cleared_optional_codes: set[str] = set()
        if optional_clear_case:
            cleared_optional_codes = self._clear_optional_field_values(
                scope, submitted
            )
            if not cleared_optional_codes:
                return CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "command_not_applicable",
                    "no populated optional field exposes a user clear action",
                )
        elif not submitted:
            # An already-populated edit form may not emit an update for a no-op
            # Save click. Feedback and duplicate-click cases need a real mutation.
            submitted.update(self._modify_form_for_command_save(scope))
        if any(value not in (None, "", []) for value in submitted.values()):
            self._ensure_unique_record_identity(scope, submitted)
        form_action = os.getenv("EI_COMMON_FORM_ACTION", "").strip()
        is_create = (
            not form_action or form_action.startswith(COMMON_ADD_ACTION_PREFIXES)
        )
        if is_create:
            submitted.update(self._prepare_declared_unique_values(scope, submitted))
        self._capture_submitted_display_values(submitted, scope)
        responses = []
        requests = []
        request_failures = []
        record_markers = self.driver._collect_record_identity_markers(
            submitted, scope=scope
        )
        listener = lambda response: responses.append(response)
        request_listener = lambda request: requests.append(request)
        request_failed_listener = lambda request: request_failures.append(request)
        self.page.on("response", listener)
        self.page.on("request", request_listener)
        self.page.on("requestfailed", request_failed_listener)
        unique_reservation_committed = False

        def commit_unique_reservation() -> None:
            nonlocal unique_reservation_committed
            if is_create and not unique_reservation_committed:
                commit = getattr(
                    self.driver, "commit_pending_unique_reservations", None
                )
                if callable(commit):
                    commit()
                unique_reservation_committed = True

        try:
            try:
                scope_handle = scope.element_handle(timeout=1_000)
            except TypeError:
                scope_handle = scope.element_handle()
            except Exception:
                scope_handle = None
            max_attempts = max(1, int(os.getenv("EI_VALIDATION_SAVE_ATTEMPTS", "3")))
            for attempt in range(1, max_attempts + 1):
                response_start = len(responses)
                pre_click_error = self._visible_command_error_text(scope)
                button.click()
                attempt_responses = lambda: responses[response_start:]
                confirm = self.page.locator(
                    ".el-message-box:visible,[role='alertdialog']:visible"
                ).last
                expects_confirmation = "二次确认" in case.scenario
                if expects_confirmation:
                    confirmation_deadline = time.monotonic() + 3
                    while (
                        not (confirm.count() and confirm.is_visible())
                        and not self._matching_business_responses(attempt_responses(), submitted)
                        and scope.is_visible()
                        and time.monotonic() < confirmation_deadline
                    ):
                        self.page.wait_for_timeout(100)
                if expects_confirmation and not (confirm.count() and confirm.is_visible()):
                    direct_matches = self._matching_business_responses(
                        attempt_responses(), submitted
                    )
                    if direct_matches:
                        commit_unique_reservation()
                        form_hidden = self._wait_for_command_form_completion(
                            scope, scope_handle
                        )
                        if not form_hidden:
                            self._close_retained_form_before_readback(
                                scope, scope_handle
                            )
                        self._verify_saved_record(
                            responses,
                            direct_matches[0],
                            submitted,
                            record_markers,
                            terminal_operation=(
                                case.field_type == "submit_command"
                            ),
                        )
                        if "取消" in case.scenario:
                            self._fail_saved_case(
                                f"{case.field_label}期望二次确认取消，但页面未出现确认框且已直接保存："
                                f"{direct_matches[0].url}"
                            )
                    return CommonFieldExecutionResult(
                        case.case_id, case.field_key, "command_not_applicable",
                        (
                            "no confirmation dialog; direct save was verified and retained"
                            if direct_matches else "no confirmation dialog"
                        ),
                    )
                if confirm.count() and confirm.is_visible():
                    if "取消" in case.scenario:
                        cancel = confirm.locator("button:has-text('取消')").last
                        cancel.click()
                        return CommonFieldExecutionResult(
                            case.case_id, case.field_key, "confirmation_cancelled",
                            "business form remains open",
                        )
                    confirm.locator("button:has-text('确定')").last.click()
                rapid_click = "快速重复点击" in case.scenario
                second_click_blocked = False
                if rapid_click:
                    try:
                        # A real user cannot click through a loading mask. Keep Playwright's
                        # actionability checks enabled so the test observes that protection.
                        button.click(timeout=350)
                    except PlaywrightTimeoutError:
                        second_click_blocked = True
                deadline = time.monotonic() + 30
                matches = []
                first_match_at = None
                blocked_message = ""
                hidden_response_deadline = None
                while time.monotonic() < deadline:
                    matches = self._matching_business_responses(
                        attempt_responses(), submitted
                    )
                    if matches and first_match_at is None:
                        first_match_at = time.monotonic()
                    if matches and (not rapid_click or time.monotonic() - first_match_at >= 1):
                        break
                    current_error = self._visible_command_error_text(scope)
                    if current_error and current_error != pre_click_error:
                        blocked_message = current_error
                        break
                    if (
                        not rapid_click
                        and not self._original_scope_is_visible(scope, scope_handle)
                    ):
                        if hidden_response_deadline is None:
                            settle_ms = int(os.getenv(
                                "EI_COMMON_COMMAND_FORM_SETTLE_MS",
                                os.getenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "3000"),
                            ))
                            hidden_response_deadline = (
                                time.monotonic() + max(0, settle_ms) / 1000
                            )
                        elif time.monotonic() >= hidden_response_deadline:
                            break
                    self.page.wait_for_timeout(200)
                if not matches:
                    if not blocked_message:
                        blocked_message = self._visible_command_error_text(scope)
                    if blocked_message:
                        raise AssertionError(f"点击{case.field_label}后被页面校验阻止：{blocked_message}")
                    raise AssertionError(
                        f"点击{case.field_label}后没有捕获业务响应；"
                        f"{self._command_network_diagnostics(requests, responses, request_failures)}"
                    )
                if rapid_click and len(matches) > 1:
                    raise AssertionError(
                        f"快速重复点击产生 {len(matches)} 次业务保存响应"
                    )
                response = matches[0]
                if not response.ok:
                    raise AssertionError(f"业务接口失败：HTTP {response.status} {response.url}")
                body = None
                try:
                    body = response.json()
                    self.driver._assert_business_success(body)
                except ValueError:
                    self.driver._assert_business_success(response.text())
                except AssertionError:
                    message = (
                        str(body.get("message") or body.get("msg") or body)
                        if isinstance(body, dict) else ""
                    )
                    if not is_create:
                        raise
                    repaired = self.driver._repair_business_validation_message(
                        message,
                        submitted,
                        attempt,
                        protected_codes=(
                            set()
                            if case.field_type.endswith("_command")
                            else {case.field_key}
                        ),
                        allow_unique_repair=is_create,
                    )
                    can_retry = (
                        repaired
                        and attempt < max_attempts
                        and self._original_scope_is_visible(scope, scope_handle)
                    )
                    if can_retry:
                        submitted.update(repaired)
                        record_markers = self.driver._collect_record_identity_markers(
                            submitted, scope=scope
                        )
                        continue
                    raise
                commit_unique_reservation()
                form_hidden = self._wait_for_command_form_completion(scope, scope_handle)
                if not form_hidden:
                    self._close_retained_form_before_readback(scope, scope_handle)
                require_edit_and_detail = (
                    bool(cleared_optional_codes)
                    or self._requires_edit_and_detail_readback(case)
                )
                required_codes = None
                if cleared_optional_codes:
                    identity_codes = {
                        code for code in submitted
                        if code.lower() in ModuleSmokeDriver.RECORD_IDENTITY_CODES
                    }
                    required_codes = cleared_optional_codes | identity_codes
                elif require_edit_and_detail:
                    required_codes = set(submitted)
                elif case.field_type in {"save_command", "submit_command"}:
                    required_codes = self._command_readback_required_codes(
                        submitted, record_markers
                    )
                self._verify_saved_record(
                    responses,
                    response,
                    submitted,
                    record_markers,
                    required_codes=required_codes,
                    require_edit_and_detail=require_edit_and_detail,
                    terminal_operation=(case.field_type == "submit_command"),
                )
                return CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "rapid_click_blocked_by_ui" if rapid_click and second_click_blocked else "command_verified",
                    (
                        f"second click blocked by UI; one business response: {response.url}"
                        if rapid_click and second_click_blocked
                        else response.url
                    ),
                )
        finally:
            if is_create and not unique_reservation_committed:
                release = getattr(
                    self.driver, "release_pending_unique_reservations", None
                )
                if callable(release):
                    release()
            if hasattr(self.page, "remove_listener"):
                self.page.remove_listener("response", listener)
                self.page.remove_listener("request", request_listener)
                self.page.remove_listener("requestfailed", request_failed_listener)

    @staticmethod
    def _command_network_diagnostics(requests, responses, request_failures) -> str:
        """Describe mutation traffic without exposing request bodies or credentials."""
        def endpoint(request, *, status: Any = None) -> str:
            method = str(getattr(request, "method", "?")).upper()
            parsed = urlsplit(str(getattr(request, "url", "")))
            target = parsed.path or "<unknown-path>"
            return f"{method} {target}" + (f" HTTP {status}" if status is not None else "")

        mutation_requests = [
            request for request in requests
            if str(getattr(request, "method", "")).upper() in {"POST", "PUT", "PATCH"}
        ]
        response_items = [
            endpoint(getattr(response, "request", None), status=getattr(response, "status", "?"))
            for response in responses
            if str(getattr(getattr(response, "request", None), "method", "")).upper()
            in {"POST", "PUT", "PATCH"}
        ]
        failed_items = [endpoint(request) for request in request_failures]
        parts = [
            "mutation-requests=" + (", ".join(endpoint(request) for request in mutation_requests[-5:]) or "none"),
            "mutation-responses=" + (", ".join(response_items[-5:]) or "none"),
        ]
        if failed_items:
            parts.append("requestfailed=" + ", ".join(failed_items[-5:]))
        return "; ".join(parts)

    def _inline_edit_has_exited(self, scope) -> bool:
        """An inline editor stays mounted after cancel but loses writable controls."""
        try:
            controls = scope.locator(EDITABLE_FORM_CONTROL)
            return not (
                controls.count()
                and controls.first.is_visible()
            ) and not self._form_command_buttons(self._command_scope(scope)).count()
        except Exception:
            return False

    def _clear_optional_field_values(
        self, scope, submitted: dict[str, Any]
    ) -> set[str]:
        """Clear every populated optional control, including user-deletable attachments."""
        fields = discover_common_fields(self._wait_for_fields_stable(scope))
        cleared: set[str] = set()
        unsupported: list[str] = []
        for field in fields:
            if field.constraints.required or field.readonly:
                continue
            dom = self._dom_for_discovered_field(field, scope)
            if not self.driver._dom_field_has_value(dom, root=scope):
                continue
            try:
                if field.kind == "file":
                    self._clear_optional_attachment(field, scope)
                else:
                    self._clear_required_control(field)
            except AssertionError:
                unsupported.append(f"{field.field_key}/{field.kind}")
                continue
            self.page.wait_for_timeout(100)
            refreshed = self._dom_for_discovered_field(field, scope)
            if self.driver._dom_field_has_value(refreshed, root=scope):
                raise AssertionError(
                    f"非必填字段清空后仍有值：{field.field_key}"
                )
            submitted[field.field_key] = ""
            cleared.add(field.field_key)
        if unsupported:
            raise AssertionError(
                "以下已填非必填字段没有可用清空操作：" + ", ".join(unsupported)
            )
        return cleared

    def _execute_submit_required_validation(self, case, scope, button):
        """Prove edit submission is blocked after clearing editable required fields."""
        fields = discover_common_fields(self._wait_for_fields_stable(scope))
        required_fields = [
            field for field in fields
            if field.constraints.required and not field.readonly
        ]
        if not required_fields:
            raise AssertionError("当前编辑表单没有可见且可编辑的必填字段")

        start_url = self.page.url
        try:
            scope_handle = scope.element_handle()
        except Exception:
            scope_handle = None
        requests = []
        def listener(request):
            requests.append(request)

        listener_registered = False
        if hasattr(self.page, "on"):
            self.page.on("request", listener)
            listener_registered = True
        try:
            targets: dict[str, DiscoveredCommonField] = {}
            unsupported: list[str] = []
            for field in required_fields:
                if self._required_control_has_value(field):
                    try:
                        self._clear_required_control(field)
                    except AssertionError:
                        unsupported.append(f"{field.field_key}/{field.kind}")
                        continue
                    self.page.wait_for_timeout(100)
                if not self._required_control_has_value(field):
                    targets[field.field_key] = field

            if not targets:
                detail = ", ".join(unsupported) or "none"
                raise AssertionError(
                    "当前编辑表单没有可安全清空的必填字段；unsupported=" + detail
                )

            mutations = self._business_mutation_requests(requests, {})
            if mutations:
                request = mutations[0]
                raise AssertionError(
                    "清空必填字段时触发了业务提交请求："
                    f"{request.method} {request.url}"
                )

            existing_confirmations = self._visible_confirmation_handles()
            button.click()
            self._confirm_submission_if_present(existing_confirmations)
            mutations = self._business_mutation_requests(requests, {})
            if mutations:
                request = mutations[0]
                raise AssertionError(
                    "必填校验未阻止业务提交请求："
                    f"{request.method} {request.url}"
                )
            if self.page.url != start_url or not self._original_scope_is_visible(
                scope, scope_handle
            ):
                raise AssertionError("必填校验后编辑表单已关闭、刷新或跳转")

            current_fields = discover_common_fields(
                self._wait_for_fields_stable(scope)
            )
            current_by_exact: dict[str, list[DiscoveredCommonField]] = {}
            current_by_normalized: dict[str, list[DiscoveredCommonField]] = {}
            for field in current_fields:
                current_by_exact.setdefault(field.field_key, []).append(field)
                normalized = self._normalized_runtime_field_key(field.field_key)
                current_by_normalized.setdefault(normalized, []).append(field)

            active_targets: dict[str, DiscoveredCommonField] = {}
            invalid_states: list[str] = []
            for field_key in targets:
                normalized = self._normalized_runtime_field_key(field_key)
                candidates = current_by_exact.get(field_key)
                if not candidates:
                    candidates = current_by_normalized.get(normalized, [])
                if not candidates:
                    invalid_states.append(f"{field_key}:提交后字段已从运行时扫描中消失")
                    continue
                active = [
                    field for field in candidates
                    if field.constraints.required and not field.readonly
                ]
                inactive = [
                    field for field in candidates
                    if not field.constraints.required
                ]
                if active:
                    for active_field in active:
                        active_key = active_field.field_key
                        if active_key in active_targets:
                            invalid_states.append(
                                f"{field_key}:同一运行时必填身份匹配到多个控件"
                            )
                            continue
                        active_targets[active_key] = active_field
                    continue
                if len(inactive) == len(candidates):
                    continue
                invalid_states.append(
                    f"{field_key}:提交后字段仍为必填但不可编辑"
                )

            if invalid_states:
                raise AssertionError(
                    "无法安全判定条件必填状态：" + "; ".join(invalid_states)
                )
            if not active_targets:
                raise AssertionError(
                    "清空字段并提交后，没有仍处于运行时必填状态的可编辑字段，"
                    "无法证明必填校验已阻止提交"
                )

            error_snapshot = self._wait_for_required_error_snapshot(active_targets)
            mutations = self._business_mutation_requests(requests, {})
            if mutations:
                request = mutations[0]
                raise AssertionError(
                    "必填校验未阻止业务提交请求："
                    f"{request.method} {request.url}"
                )

            invalid = []
            observed = []
            for field_key, field in active_targets.items():
                message = error_snapshot.get(field_key, "")
                if not message:
                    invalid.append(f"{field_key}:未出现字段级必填提示")
                elif not self._required_message_is_correct(field, message):
                    invalid.append(f"{field_key}:提示不正确({message})")
                else:
                    observed.append(f"{field.label or field_key}: {message}")
            if invalid:
                raise AssertionError(
                    "编辑必填校验未准确提示所有已清空字段：" + "; ".join(invalid)
                )
            return CommonFieldExecutionResult(
                case.case_id,
                case.field_key,
                "save_blocked",
                "; ".join(observed),
            )
        finally:
            if listener_registered and hasattr(self.page, "remove_listener"):
                self.page.remove_listener("request", listener)

    def _confirm_submission_if_present(
        self, ignored_confirmation_handles: Iterable[Any] = (),
    ) -> bool:
        """Confirm only a newly opened submit confirmation, if validation allows it."""
        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            confirmations = self.page.locator(FORM_CONFIRM_SELECTOR)
            for index in range(confirmations.count() - 1, -1, -1):
                confirm = (
                    confirmations.nth(index)
                    if hasattr(confirmations, "nth")
                    else confirmations.last
                )
                if not confirm.is_visible() or self._confirmation_matches_handle(
                    confirm, ignored_confirmation_handles
                ):
                    continue
                accept = confirm.locator("button:visible").filter(
                    has_text=FORM_GENERIC_CONFIRM_PATTERN
                ).last
                if accept.count() and accept.is_visible():
                    accept.click()
                    return True
            self.page.wait_for_timeout(100)
        return False

    def _clear_optional_attachment(
        self, field: DiscoveredCommonField, scope,
    ) -> None:
        """Delete only the files owned by one optional attachment form item."""
        file_input = self._file_input(field, scope)
        owner = file_input.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ') or "
            "contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ') or "
            "contains(concat(' ',normalize-space(@class),' '),' ant-form-item ')][1]"
        ).first
        deleted = 0
        while deleted < 20:
            before_names = tuple(self._attachment_names(field, scope))
            rows = owner.locator(
                ".el-upload-list__item:visible,.ant-upload-list-item:visible"
            )
            before_row_count = rows.count()
            remove = self._attachment_remove_control(owner, rows)
            if remove is None:
                if deleted and not before_names and not before_row_count:
                    return
                raise AssertionError(
                    f"非必填附件没有可用删除操作：{field.field_key}"
                )
            try:
                remove.click(timeout=2_000)
            except PlaywrightTimeoutError:
                if not remove.count():
                    continue
                # The target is already constrained to this attachment's file row.
                try:
                    remove.click(force=True, timeout=2_000)
                except Exception as exc:
                    raise AssertionError(
                        f"非必填附件删除操作不可点击：{field.field_key}"
                    ) from exc
            except Exception as exc:
                if not remove.count():
                    continue
                raise AssertionError(
                    f"非必填附件删除操作不可点击：{field.field_key}"
                ) from exc
            self._confirm_attachment_removal()
            deleted += 1
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current_names = tuple(self._attachment_names(field, scope))
                current_row_count = owner.locator(
                    ".el-upload-list__item:visible,.ant-upload-list-item:visible"
                ).count()
                if current_names != before_names or current_row_count < before_row_count:
                    break
                self.page.wait_for_timeout(100)
            else:
                raise AssertionError(
                    f"非必填附件点击删除后页面值未变化：{field.field_key}"
                )
        raise AssertionError(f"非必填附件超过可安全删除数量：{field.field_key}")

    @staticmethod
    def _attachment_remove_control(owner, rows):
        """Return one row-scoped delete control, revealing hover-only actions first."""
        visible = owner.locator(
            ".el-upload-list__item-delete:visible,.el-upload-list__item .el-icon-delete:visible,"
            ".el-upload-list__item .el-icon--close:visible,"
            ".ant-upload-list-item-actions .anticon-delete:visible,"
            "button[aria-label*='删除']:visible,button[title*='删除']:visible,"
            "[data-action='remove']:visible,[data-action='delete']:visible"
        )
        if visible.count():
            return visible.first
        for index in range(rows.count()):
            row = rows.nth(index)
            try:
                row.hover(timeout=2_000)
            except Exception:
                continue
            remove = row.locator(
                ".el-upload-list__item-delete,.el-icon-delete,.el-icon--close,"
                ".ant-upload-list-item-actions .anticon-delete,"
                "button[aria-label*='删除'],button[title*='删除'],"
                "[data-action='remove'],[data-action='delete']"
            )
            if remove.count():
                return remove.first
        return None

    def _confirm_attachment_removal(self) -> None:
        dialog = self.page.locator(".el-message-box:visible,[role='alertdialog']:visible").last
        if not dialog.count() or not dialog.is_visible():
            return
        confirm = dialog.locator("button:has-text('确定'),button:has-text('确认')").last
        if not confirm.count() or not confirm.is_visible():
            raise AssertionError("附件删除确认框没有可用确认按钮")
        confirm.click()

    def _wait_for_command_form_completion(self, scope, scope_handle) -> bool:
        """Wait briefly for close/refresh, but allow verified forms that remain open."""
        timeout_ms = int(
            os.getenv(
                "EI_COMMON_COMMAND_FORM_SETTLE_MS",
                os.getenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "3000"),
            )
        )
        deadline = time.monotonic() + timeout_ms / 1000
        while (
            self._original_scope_is_visible(scope, scope_handle)
            and time.monotonic() < deadline
        ):
            self.page.wait_for_timeout(100)
        form_hidden = not self._original_scope_is_visible(scope, scope_handle)

        loading = self.page.locator(
            ".el-loading-mask:visible,.ant-spin-spinning:visible,[aria-busy='true']:visible"
        ).first
        if loading.count() and loading.is_visible():
            try:
                loading.wait_for(state="hidden", timeout=20_000)
            except Exception as exc:
                raise AssertionError("新增表单关闭后页面在 20 秒内仍未完成刷新") from exc
        self.page.wait_for_timeout(500)
        return form_hidden

    def _close_retained_form_before_readback(self, scope, scope_handle) -> None:
        """Close a successfully saved form that the app keeps visible before readback."""
        if not self._original_scope_is_visible(scope, scope_handle):
            self._set_driver_form_scope(None)
            return
        self._close_session_scope(scope)
        self._set_driver_form_scope(None)
        self.page.wait_for_timeout(500)

    def _matching_business_responses(self, responses, submitted: dict[str, Any]):
        expected_values = {
            str(value).strip()
            for value in submitted.values()
            if value not in (None, "", [])
        }
        expected_keys = {
            parts[-1].lower()
            for code in submitted
            if (
                parts := [
                    part for part in re.split(r"[.\[\]/\\]+", str(code))
                    if part and part != "*"
                ]
            )
        }
        candidates = []
        scored = []
        for index, response in enumerate(responses):
            if response.request.method not in {"POST", "PUT", "PATCH"}:
                continue
            payload = self.driver._request_payload(response.request)
            if ModuleSmokeDriver._is_non_business_mutation_endpoint(
                response.url, payload
            ):
                continue
            if not ModuleSmokeDriver._is_business_mutation_url(response.url):
                continue
            payload_values = self.driver._payload_scalar_values(
                payload
            )
            candidates.append((index, response))
            overlap = len(expected_values & payload_values)
            key_overlap = len(
                expected_keys & ModuleSmokeDriver._payload_key_names(payload)
            )
            scored.append((overlap, key_overlap, index, response))
        value_scored = [item for item in scored if item[0] > 0]
        key_scored = [item for item in scored if item[1] > 0]
        if not value_scored and not key_scored:
            # An edit form may submit an API-normalized payload rather than the
            # visible baseline values.  Its single non-attachment mutation
            # endpoint is still a reliable response candidate; multiple
            # endpoints without value overlap remain ambiguous and must fail.
            candidate_urls = {
                urlunsplit((*urlsplit(response.url)[:3], "", ""))
                for _, response in candidates
            }
            if len(candidate_urls) == 1:
                return [response for _, response in candidates]
            return []
        active_scores = value_scored or key_scored
        score_index = 0 if value_scored else 1
        best_overlap = max(item[score_index] for item in active_scores)
        primary = max(
            (item for item in active_scores if item[score_index] == best_overlap),
            key=lambda item: item[2],
        )[3]
        primary_url = urlunsplit((*urlsplit(primary.url)[:3], "", ""))
        return [
            response
            for value_overlap, key_overlap, _, response in active_scores
            if (value_overlap if value_scored else key_overlap) == best_overlap
            and response.request.method == primary.request.method
            and urlunsplit((*urlsplit(response.url)[:3], "", "")) == primary_url
        ]

    @staticmethod
    def _command_readback_required_codes(
        submitted: dict[str, Any],
        record_markers: tuple[str, ...],
    ) -> set[str] | None:
        """For command-only Save/Submit cases, verify the record identity only."""
        marker_values = {
            str(value).strip() for value in record_markers if str(value).strip()
        }
        required = {
            code for code, value in submitted.items()
            if value not in (None, "", [])
            and code.lower() in ModuleSmokeDriver.RECORD_IDENTITY_CODES
        }
        if required:
            return required
        required = {
            code
            for code, value in submitted.items()
            if str(value).strip() in marker_values
        }
        return required or None

    @staticmethod
    def _business_mutation_requests(requests, modified: dict[str, Any]):
        expected_values = {
            str(value).strip()
            for value in modified.values()
            if value not in (None, "", [])
        }
        result = []
        for request in requests:
            if request.method not in {"POST", "PUT", "PATCH"}:
                continue
            url = request.url.lower()
            payload = ModuleSmokeDriver._request_payload(request)
            if ModuleSmokeDriver._is_non_business_mutation_endpoint(
                request.url, payload
            ):
                continue
            payload_values = ModuleSmokeDriver._payload_scalar_values(payload)
            payload_text = str(payload)
            contains_modified_value = bool(expected_values & payload_values) or any(
                value in payload_text for value in expected_values
            )
            known_save_endpoint = ModuleSmokeDriver._is_business_mutation_url(url)
            if contains_modified_value or (not expected_values and known_save_endpoint):
                result.append(request)
        return result

    def _confirm_form_cancellation_if_present(
        self,
        scope,
        scope_handle,
        *,
        timeout_ms: int = 3_000,
        ignored_confirmation_handles: Iterable[Any] = (),
    ) -> bool:
        ignored_confirmation_handles = tuple(ignored_confirmation_handles)
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            if not self._original_scope_is_visible(scope, scope_handle):
                return False
            confirmations = self.page.locator(FORM_CONFIRM_SELECTOR)
            found_unrecognized = False
            for index in range(confirmations.count() - 1, -1, -1):
                confirm = (
                    confirmations.nth(index)
                    if hasattr(confirmations, "nth")
                    else confirmations.last
                )
                if not confirm.is_visible() or self._confirmation_matches_handle(
                    confirm, ignored_confirmation_handles
                ):
                    continue
                buttons = confirm.locator("button:visible")
                accept = buttons.filter(has_text=FORM_LEAVE_CONFIRM_PATTERN).last
                if not accept.count() or not accept.is_visible():
                    try:
                        context = confirm.inner_text()
                    except (AttributeError, TypeError):
                        context = ""
                    if FORM_LEAVE_CONFIRM_CONTEXT_PATTERN.search(context):
                        accept = buttons.filter(
                            has_text=FORM_GENERIC_CONFIRM_PATTERN
                        ).last
                if accept.count() and accept.is_visible():
                    accept.click()
                    return True
                found_unrecognized = True
            if found_unrecognized:
                raise AssertionError(
                    "关闭动作后出现的确认框不是可识别的离开确认框，禁止点击其确认按钮"
                )
            self.page.wait_for_timeout(100)
        return False

    def _visible_confirmation_handles(self) -> tuple[Any, ...]:
        try:
            confirmations = self.page.locator(FORM_CONFIRM_SELECTOR)
        except AttributeError:
            return ()
        handles = []
        for index in range(confirmations.count()):
            confirm = (
                confirmations.nth(index)
                if hasattr(confirmations, "nth")
                else confirmations.last
            )
            if not confirm.is_visible():
                continue
            handle = confirm.element_handle()
            if handle is not None:
                handles.append(handle)
        return tuple(handles)

    @staticmethod
    def _confirmation_matches_handle(confirm, handles: Iterable[Any]) -> bool:
        handles = tuple(handles)
        if not handles:
            return False
        try:
            current_handle = confirm.element_handle()
        except (AttributeError, TypeError):
            current_handle = None
        for handle in handles:
            if current_handle is handle:
                return True
            try:
                if confirm.evaluate("(node, other) => node === other", handle):
                    return True
            except (AttributeError, TypeError):
                continue
        return False

    def _modify_form_for_abandonment(self, scope) -> dict[str, Any]:
        return self._modify_writable_text_field(scope, marker="AUTO_UNSAVED")

    def _modify_form_for_command_save(self, scope) -> dict[str, Any]:
        """Change one editable field so a Save/Submit command exercises persistence."""
        return self._modify_writable_text_field(scope, marker="AUTO_SAVE")

    def _modify_writable_text_field(self, scope, *, marker: str) -> dict[str, Any]:
        """Create a real dirty form without depending on a text control existing."""
        fields = [
            field
            for field in discover_common_fields(self._scan_fields(scope))
            if not field.readonly
        ]
        for field in fields:
            if field.field_type not in {"text", "textarea"}:
                continue
            locator = self._locator(field)
            before = self._input_value(locator)
            maximum = field.constraints.max_length
            candidates = (
                f"{marker}_{uuid.uuid4().hex[:8]}",
                "未保存测试内容",
                "测",
            )
            for candidate in candidates:
                value = candidate[:maximum] if maximum is not None else candidate
                if not value or value == before:
                    continue
                self._replace_value(field, value)
                actual = self._input_value(locator)
                if actual != before:
                    return {field.field_key: actual}

        for field in fields:
            if field.kind not in {"year", "date", "datetime"}:
                continue
            locator = self._locator(field)
            before = self._input_value(locator)
            for candidate in self._alternate_temporal_values(field, before):
                try:
                    self._replace_value(field, candidate)
                except AssertionError:
                    continue
                actual = self._input_value(locator)
                if actual != before:
                    return {field.field_key: actual}

        for field in fields:
            if field.kind not in {"select", "radio"}:
                continue
            if self._modify_writable_choice_field(field, scope):
                return {field.field_key: "changed"}

        submitted = self._fill_valid_baseline(scope)
        if submitted:
            return submitted
        raise AssertionError("表单没有可修改字段，无法建立真实保存或未保存修改前置条件")

    @staticmethod
    def _alternate_temporal_values(
        field: DiscoveredCommonField, before: str,
    ) -> tuple[str, ...]:
        if field.kind == "year":
            match = re.search(r"(?<!\d)(\d{4})(?!\d)", before or "")
            year = int(match.group(1)) if match else date.today().year
            return tuple(str(value) for value in (year + 1, year - 1))
        match = re.search(r"(\d{4}-\d{2}-\d{2})", before or "")
        try:
            current = date.fromisoformat(match.group(1)) if match else date.today()
        except ValueError:
            current = date.today()
        suffix = (before[match.end():] if match else "")
        return tuple(
            f"{candidate.isoformat()}{suffix}"
            for candidate in (current + timedelta(days=1), current - timedelta(days=1))
        )

    def _modify_writable_choice_field(
        self, field: DiscoveredCommonField, scope) -> bool:
        locator = self._choice_locator_for_branch_driver(field, scope)
        if locator is None or not locator.count() or not locator.is_visible():
            return False
        if field.kind == "radio":
            labels = self._filter_branch_option_labels(
                self._visible_texts(self._radio_branch_options(locator))
            )
        else:
            locator.click(force=True)
            self.page.wait_for_timeout(150)
            labels = self._filter_branch_option_labels(
                self._visible_texts(self._owned_select_options(locator))
            )
        for label in labels:
            if self._branch_option_already_selected(locator, label):
                continue
            self._select_branch_option(field, scope, label)
            if self._branch_option_already_selected(locator, label):
                return True
        return False

    def _execute_required_case(self, case: BoundCommonCase, scope):
        self._apply_case_branch_conditions(case, scope)
        fields = discover_common_fields(self._scan_fields(scope))
        target = next(
            (field for field in fields if field.field_key == case.field_key), None
        )
        if target is None:
            raise AssertionError(f"当前表单无法定位必填字段：{case.field_key}")
        other_codes = {
            field.field_key.lower()
            for field in fields
            if field.field_key != case.field_key and not field.readonly
        }
        submitted = self.driver._fill_dialog(only_codes=other_codes)
        self.driver._upload_default_attachments(scope)

        if "仅输入空格" in case.scenario:
            self._replace_value(target, " \u3000")
        elif "输入后清空" in case.scenario:
            self.driver._fill_dialog(only_codes={case.field_key.lower()})
            self._clear_required_control(target)

        actual = self._required_control_value(target)
        submitted[case.field_key] = actual
        blocked = self._submit_case(
            case, scope, target, submitted, case.input_value, actual, ""
        )
        target_error = self._target_required_error_text(target)
        if not target_error:
            if self._required_file_global_block_is_valid(target, blocked):
                target_error = GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX + blocked.observed
            else:
                raise AssertionError(
                    f"保存虽被阻止，但目标必填字段没有出现可关联的校验提示："
                    f"{case.field_key} ({case.field_label}); "
                    f"global={blocked.observed or 'none'}"
                )
        blocked = replace(blocked, observed=target_error)
        if "提示恢复" not in case.scenario:
            return blocked

        if target.kind == "file" and not self._required_control_has_value(target):
            self._upload_required_attachment(scope, target)
        else:
            self.driver._fill_dialog(only_codes={case.field_key.lower()})
        deadline = time.monotonic() + 3
        while self._target_form_errors(target).count() and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        if self._target_form_errors(target).count():
            raise AssertionError(f"输入合法值后必填提示未消失：{case.field_key}")
        if not self._required_control_has_value(target):
            raise AssertionError(f"必填字段恢复后仍为空：{case.field_key}")
        if target_error.startswith(GLOBAL_REQUIRED_FILE_EVIDENCE_PREFIX):
            self._wait_for_global_required_warning_to_clear()
        return CommonFieldExecutionResult(
            case.case_id, case.field_key, "validation_recovered", "required error cleared"
        )

    def _clear_required_control(self, field: DiscoveredCommonField) -> None:
        if field.kind in {"text", "textarea", "number"}:
            self._replace_value(field, "")
            return
        if field.kind in {"select", "multi_select"}:
            locator = self._locator(field)
            wrapper = locator.locator(
                "xpath=self::*[contains(concat(' ',normalize-space(@class),' '),' el-select ')]"
                "|ancestor::div[contains(concat(' ',normalize-space(@class),' '),' el-select ')][1]"
            )
            wrapper.hover()
            clear = wrapper.locator(
                ".el-select__clear,.el-icon-circle-close,[aria-label=clear]"
            ).first
            if not clear.count() or not clear.is_visible():
                raise AssertionError(f"下拉框没有可用清空操作：{field.field_key}")
            clear.click(force=True)
            return
        if field.kind in {"year", "date", "datetime"}:
            definition = self._definition(field)
            dom = self._dom_for_discovered_field(field)
            resolved = ResolvedField(definition, dom)
            root = self._active_form_scope()
            if root is not None:
                self.driver.interactor.clear(resolved, root=root)
            else:
                self.driver.interactor.clear(resolved)
            return
        raise AssertionError(f"必填控件不支持清空：{field.field_key}/{field.kind}")

    def _target_form_errors(self, field: DiscoveredCommonField):
        if field.kind == "file" and field.selector:
            root = self._active_form_scope()
            locator = (
                root.locator(field.selector).first
                if root is not None
                else self.page.locator(field.selector).first
            )
        else:
            locator = self._locator(field)
        item = locator.locator(
            "xpath=ancestor::*["
            "contains(concat(' ',normalize-space(@class),' '),' el-form-item ') or "
            "contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ') or "
            "contains(concat(' ',normalize-space(@class),' '),' ant-form-item ')][1]"
        )
        return item.locator(FORM_VALIDATION_SELECTOR)

    def _target_required_error_text(self, field: DiscoveredCommonField) -> str:
        errors = self._target_form_errors(field)
        return "; ".join(
            dict.fromkeys(
                text.strip() for text in errors.all_inner_texts() if text.strip()
            )
        )

    @staticmethod
    def _normalized_runtime_field_key(field_key: str) -> str:
        """Map dynamic collection row indexes to their manifest wildcard key."""
        return re.sub(r"(?<=\.)\d+(?=\.|$)", "*", str(field_key or ""))

    def _required_control_value(self, field: DiscoveredCommonField) -> str:
        if field.kind in {"text", "textarea", "number", "year"}:
            return self._input_value(self._locator(field))
        return "present" if self._required_control_has_value(field) else ""

    def _required_control_has_value(self, field: DiscoveredCommonField) -> bool:
        dom = next(
            (
                item
                for item in self._scan_fields()
                if self._same_runtime_field(item, field)
            ),
            None,
        )
        if dom is None or not dom.selector:
            return False
        root = self._active_form_scope()
        locator = (
            root.locator(dom.selector).first
            if root is not None
            else self.page.locator(dom.selector).first
        )
        if field.kind == "file":
            if not locator.count():
                return False
            return self.driver._file_input_has_value(locator)
        return (
            self.driver._dom_field_has_value(dom, root=root)
            if root is not None
            else self.driver._dom_field_has_value(dom)
        )

    @staticmethod
    def _submitted_choice_has_value(
        field: DiscoveredCommonField, submitted: dict[str, Any]
    ) -> bool:
        if field.kind not in {"select", "multi_select", "radio", "checkbox"}:
            return False
        value = submitted.get(field.field_key)
        return value not in (None, "", [])

    @staticmethod
    def _required_message_is_correct(
        field: DiscoveredCommonField, message: str,
    ) -> bool:
        normalized = re.sub(r"\s+", "", message or "")
        if not normalized:
            return False
        patterns = {
            "file": r"请上传|上传|附件|文件|必填|不能为空",
            "select": r"请选择|必填|不能为空",
            "multi_select": r"请选择|必填|不能为空",
            "radio": r"请选择|必填|不能为空",
            "checkbox": r"请选择|请勾选|必填|不能为空",
            "year": r"请选择|请输入|必填|不能为空",
            "date": r"请选择|请输入|必填|不能为空",
            "datetime": r"请选择|请输入|必填|不能为空",
        }
        pattern = patterns.get(field.kind, r"请输入|请填写|必填|不能为空")
        return bool(re.search(pattern, normalized))

    def _required_file_global_block_is_valid(
        self,
        field: DiscoveredCommonField,
        blocked: CommonFieldExecutionResult,
    ) -> bool:
        """Accept the framework's global-only required warning for an empty file."""
        if (
            field.kind != "file"
            or not field.constraints.required
            or blocked.outcome != "save_blocked"
            or self._required_control_has_value(field)
        ):
            return False
        message = re.sub(r"\s+", "", str(blocked.observed or ""))
        return bool(re.search(r"必填|未填写|不能为空|请上传", message))

    def _wait_for_global_required_warning_to_clear(self) -> None:
        """Ensure a global-only attachment warning is no longer visible after recovery."""
        deadline = time.monotonic() + 3
        while True:
            try:
                messages = [
                    re.sub(r"\s+", "", text)
                    for text in self.page.locator(PAGE_ERROR_SELECTOR).all_inner_texts()
                    if str(text).strip()
                ]
            except Exception:
                return
            required_messages = [
                text for text in messages
                if re.search(r"必填|未填写|不能为空|请上传", text)
            ]
            if not required_messages:
                return
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "必填附件恢复后全局必填提示仍未消失："
                    + "; ".join(required_messages)
                )
            self.page.wait_for_timeout(100)

    def _fill_valid_baseline(
        self, scope, *, upload_attachments: bool = True
    ) -> dict[str, Any]:
        submitted: dict[str, Any] = {}
        attempts = max(1, int(os.getenv("EI_FIELD_FILL_ATTEMPTS", "3")))
        retry_codes = None
        report = None
        for attempt in range(1, attempts + 1):
            try:
                submitted.update(self.driver._fill_dialog(only_codes=retry_codes))
                fill_failures = list(self.driver._fill_failures)
                if upload_attachments:
                    self.driver._upload_default_attachments(scope)
                else:
                    self._upload_required_baseline_attachments(scope)
                nested_baseline = getattr(
                    self.driver, "_prepare_implicit_required_nested_baselines",
                    lambda _scope: {},
                )(scope)
                submitted.update(nested_baseline)
            except DynamicFieldContractError as exc:
                raise SharedFormPreconditionError(
                    f"通用用例共享动态字段前置契约失败：{exc}"
                ) from exc
            except AssertionError as exc:
                if self._is_authentication_expired_error(exc):
                    raise SharedFormPreconditionError(
                        "浏览器登录态已失效：" + str(exc)
                    ) from exc
                raise
            report = self.driver.check_field_completion(
                submitted, fill_failures
            )
            if not upload_attachments:
                report = self._without_optional_file_completion_gaps(
                    report, scope
                )
            if self.driver._field_report_ok(report):
                self._ensure_unique_record_identity(scope, submitted)
                return submitted
            retry_codes = self.driver._retry_field_codes(submitted)
            if not retry_codes or attempt == attempts:
                break
            self.page.wait_for_timeout(500)
        detail = report.message() if report is not None else "未生成字段完成度报告"
        raise AssertionError(f"通用用例合法基线填充失败：{detail}")

    @staticmethod
    def _is_authentication_expired_error(error: BaseException) -> bool:
        """Treat an unauthenticated backend response as a Batch-wide precondition."""
        return bool(re.search(r"\bHTTP\s*401\b", str(error), re.IGNORECASE))

    def _upload_required_baseline_attachments(self, scope) -> None:
        """Satisfy required files while leaving optional files unchanged."""
        fields = discover_common_fields(self._wait_for_fields_stable(scope))
        for field in fields:
            if field.kind != "file" or not field.constraints.required:
                continue
            if not self._required_control_has_value(field):
                self._upload_required_attachment(scope, field)

    def _without_optional_file_completion_gaps(self, report, scope):
        """Keep optional files untouched for clear-value cases without relaxing other fields."""
        fields = discover_common_fields(self._wait_for_fields_stable(scope))
        file_displays = {
            self.driver._field_display(field.field_key, field.label)
            for field in fields
            if field.kind == "file" and not field.constraints.required
        }

        def belongs_to_optional_file(message: str) -> bool:
            return any(
                message == display or message.startswith(display + ":")
                for display in file_displays
            )

        return replace(
            report,
            optional_not_filled=[
                message for message in report.optional_not_filled
                if not belongs_to_optional_file(message)
            ],
            optional_fill_failed=[
                message for message in report.optional_fill_failed
                if not belongs_to_optional_file(message)
            ],
        )

    def _record_identity_candidate_fields(
        self, fields: Iterable[Any], submitted: dict[str, Any],
    ) -> list[Any]:
        """Prefer business identity fields; use an automation-owned text fallback only."""
        text_fields = [
            field for field in fields
            if field.kind in {"text", "textarea"}
        ]
        identity_fields = [
            field for field in text_fields
            if self.driver._is_record_identity_field(field)
        ]
        if identity_fields:
            return identity_fields
        return [
            field for field in text_fields
            if str(submitted.get(field.field_code, "") or "").strip().startswith(
                AUTOMATION_RECORD_PREFIXES
            )
        ]

    def _ensure_unique_record_identity(self, scope, submitted: dict[str, Any]) -> None:
        """Make every physical save traceable to one unique automation-owned record."""
        if not submitted:
            return
        try:
            fields = self._scan_fields(scope)
        except Exception:
            return
        token = self._record_identity_token(scope)
        for field in self._record_identity_candidate_fields(fields, submitted):
            try:
                definition = FieldDefinition(
                    field_code=field.field_code,
                    field_name=field.label,
                    field_type=FIELD_TYPE_TO_COMPONENT.get(field.kind, "ElInput-TEXT"),
                    required=field.required,
                    readonly=field.readonly,
                    props={"maxlength": field.maxlength},
                    source="runtime-dom",
                )
                resolved = ResolvedField(definition, field)
                locator = self.driver.interactor.locate(resolved, root=scope)
                current = self._input_value(locator).strip()
                unique = self._unique_record_identity_value(
                    current or str(submitted.get(field.field_code, "") or ""),
                    token,
                    field.maxlength,
                )
                if not unique or unique == current:
                    submitted[field.field_code] = unique or current
                    return
                locator.fill(unique)
                try:
                    locator.press("Tab")
                except Exception:
                    pass
                submitted[field.field_code] = unique
                return
            except Exception:
                continue

    def _prepare_declared_unique_values(
        self,
        scope,
        submitted: dict[str, Any],
        *,
        exclude_codes: set[str] | None = None,
    ) -> dict[str, Any]:
        # Common-field cases begin from an already-filled legal baseline. Keep
        # those user-visible values intact; an explicit duplicate response can
        # later authorize a retry of only the current target field.
        if any(value not in (None, "", []) for value in submitted.values()):
            return {}
        prepare = getattr(
            self.driver, "_prepare_declared_unique_values", None
        )
        if not callable(prepare):
            return {}
        action = os.getenv("EI_COMMON_FORM_ACTION", "").strip()
        if action.startswith(COMMON_EDIT_ACTION_PREFIXES):
            return {}
        return prepare(
            scope,
            submitted,
            exclude_codes=exclude_codes,
        )

    def _record_identity_token(self, scope) -> str:
        attribute = "data-ei-common-record-token"
        try:
            existing = scope.get_attribute(attribute)
        except Exception:
            existing = ""
        if existing:
            return existing
        self._record_identity_sequence = getattr(
            self, "_record_identity_sequence", 0
        ) + 1
        # A per-executor S001 token is reused by later runs and therefore cannot
        # safely identify a newly saved row among retained automation data.
        # Milliseconds keep the marker compact enough for business-name fields;
        # the sequence still distinguishes two forms opened in the same tick.
        token = (
            f"S{time.time_ns() // 1_000_000:013d}"
            f"{self._record_identity_sequence:03d}"
        )
        try:
            scope.evaluate(
                "(node, value) => node.setAttribute('data-ei-common-record-token', value)",
                token,
            )
        except Exception:
            pass
        return token

    @staticmethod
    def _unique_record_identity_value(
        value: str,
        token: str,
        maximum: int | None = None,
    ) -> str:
        base = str(value or "").strip()
        if not base:
            return ""
        if not base.startswith(AUTOMATION_RECORD_PREFIXES):
            base = f"AUTO_{base}"
        suffix = f"_{token}"
        if suffix in base:
            return base
        if maximum is not None and len(base) + len(suffix) > maximum:
            keep = maximum - len(suffix)
            if keep < min(map(len, AUTOMATION_RECORD_PREFIXES)):
                raise AssertionError(
                    f"名称字段最大长度 {maximum} 无法容纳唯一自动化标记，禁止保存"
                )
            base = base[:keep]
        return f"{base}{suffix}"

    def _restore_required_case(self, case: BoundCommonCase, scope) -> None:
        self._apply_case_branch_conditions(case, scope)
        field = self._current_field(case, scope)
        if field.kind == "file":
            self._upload_required_attachment(scope, field)
        else:
            try:
                self._clear_required_control(field)
            except AssertionError:
                pass
            self.driver._fill_dialog(only_codes={case.field_key.lower()})
        field = self._current_field(case, scope)
        self._blur_required_control(field)
        self._restore_valid_form(scope)

    def _restore_target_after_validation(
        self,
        case: BoundCommonCase,
        scope,
        valid_value: Any,
    ) -> None:
        self._apply_case_branch_conditions(case, scope)
        field = self._current_field(case, scope)
        self._replace_value(field, valid_value)
        actual = self._input_value(self._locator(field))
        if str(actual) != str(valid_value):
            raise AssertionError(
                f"校验后字段未恢复为合法值：{case.field_key}, "
                f"expected={valid_value!r}, actual={actual!r}"
            )
        deadline = time.monotonic() + 3
        while self._target_form_errors(field).count() and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        if self._target_form_errors(field).count():
            raise AssertionError(f"校验后字段提示未清除：{case.field_key}")
        self._wait_for_page_errors_to_clear()

    def _wait_for_page_errors_to_clear(self) -> None:
        """Do not let a previous save's transient message satisfy the next attempt."""
        timeout_ms = int(os.getenv("EI_COMMON_ERROR_TIMEOUT_MS", "5000"))
        deadline = time.monotonic() + timeout_ms / 1000
        while True:
            message = self._visible_page_error_text()
            if not message:
                return
            if time.monotonic() >= deadline:
                raise AssertionError(f"校验恢复后页面错误提示未消失：{message}")
            self.page.wait_for_timeout(100)

    def _restore_valid_form(self, scope) -> None:
        self._fill_valid_baseline(scope)
        deadline = time.monotonic() + 3
        errors = scope.locator(FORM_VALIDATION_SELECTOR)
        while errors.count() and time.monotonic() < deadline:
            self.page.wait_for_timeout(100)
        if errors.count():
            raise AssertionError("合法值恢复后表单仍存在字段校验提示")

    def _dismiss_form_transients(self) -> None:
        try:
            poppers = self.page.locator(FORM_POPPER_SELECTOR)
            if poppers.count():
                self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _stabilize_reusable_form(self, scope) -> None:
        self._dismiss_form_transients()
        try:
            confirmations = self.page.locator(FORM_CONFIRM_SELECTOR)
            poppers = self.page.locator(FORM_POPPER_SELECTOR)
        except AttributeError:
            self._wait_for_fields_stable(scope, timeout_ms=3_000, stable_ms=300)
            return
        if confirmations.count():
            raise AssertionError("表单上仍有未处理的确认框")
        deadline = time.monotonic() + 1
        while poppers.count() and time.monotonic() < deadline:
            try:
                self.page.wait_for_timeout(100)
            except AttributeError:
                break
        if poppers.count():
            raise AssertionError("表单上仍有未关闭的选择浮层")
        if confirmations.count():
            raise AssertionError("表单稳定等待期间出现了未处理的确认框")
        self._wait_for_fields_stable(scope, timeout_ms=3_000, stable_ms=300)
        if confirmations.count():
            raise AssertionError("字段稳定等待期间出现了未处理的确认框")
        if poppers.count():
            raise AssertionError("字段稳定等待期间重新出现了选择浮层")

    def _submit_case(
        self,
        case,
        scope,
        current,
        submitted,
        requested,
        actual,
        before,
        *,
        required_codes: set[str] | None = None,
        rendered_text_expectations: dict[str, tuple[str, str]] | None = None,
        require_edit_and_detail: bool = False,
        attachment_lifecycle_tracker=None,
    ) -> CommonFieldExecutionResult:
        if required_codes is None and not case.field_type.endswith("_command"):
            required_codes = {case.field_key}
        if rendered_text_expectations is None and self._requires_rendered_whitespace_check(
            case, actual
        ):
            rendered_text_expectations = {
                case.field_key: (case.field_label, str(actual))
            }
        save = self.driver._save_button(scope)
        if not save.count() or not save.is_visible() or not save.is_enabled():
            raise AssertionError("新增表单没有可用的保存按钮")
        responses = []
        if any(value not in (None, "", []) for value in submitted.values()):
            self._ensure_unique_record_identity(scope, submitted)
        form_action = os.getenv("EI_COMMON_FORM_ACTION", "").strip()
        is_create = (
            not form_action or form_action.startswith(COMMON_ADD_ACTION_PREFIXES)
        )
        if is_create:
            submitted.update(self._prepare_declared_unique_values(
                scope,
                submitted,
                exclude_codes=(
                    set()
                    if case.field_type.endswith("_command")
                    else (required_codes or {case.field_key})
                ),
            ))
        record_markers = self.driver._collect_record_identity_markers(
            submitted, scope=scope
        )
        observed_requests = []
        failed_requests: list[str] = []

        def response_received(response) -> None:
            if not any(existing is response for existing in responses):
                responses.append(response)

        def request_started(request) -> None:
            method = str(getattr(request, "method", "")).upper()
            if method not in {"POST", "PUT", "PATCH"}:
                return
            url = str(getattr(request, "url", ""))
            if not ModuleSmokeDriver._is_business_mutation_url(url):
                return
            try:
                payload = self.driver._request_payload(request)
                if ModuleSmokeDriver._is_non_business_mutation_endpoint(url, payload):
                    return
            except Exception:
                pass
            observed_requests.append(request)

        def request_finished(request) -> None:
            try:
                response = request.response
                response = response() if callable(response) else response
            except Exception:
                return
            if response is not None:
                response_received(response)

        def request_failed(request) -> None:
            method = str(getattr(request, "method", "")).upper()
            if method not in {"POST", "PUT", "PATCH"}:
                return
            url = str(getattr(request, "url", ""))
            try:
                payload = self.driver._request_payload(request)
                if ModuleSmokeDriver._is_non_business_mutation_endpoint(url, payload):
                    return
            except Exception:
                pass
            failure = getattr(request, "failure", "") or "<unknown>"
            if callable(failure):
                try:
                    failure = failure()
                except Exception:
                    failure = "<unreadable>"
            failed_requests.append(f"{method} {url}: {failure}")

        self.page.on("response", response_received)
        self.page.on("request", request_started)
        self.page.on("requestfinished", request_finished)
        self.page.on("requestfailed", request_failed)
        unique_reservation_committed = False

        def commit_unique_reservation() -> None:
            nonlocal unique_reservation_committed
            if is_create and not unique_reservation_committed:
                commit = getattr(
                    self.driver, "commit_pending_unique_reservations", None
                )
                if callable(commit):
                    commit()
                unique_reservation_committed = True

        try:
            try:
                scope_handle = scope.element_handle(timeout=1_000)
            except TypeError:
                scope_handle = scope.element_handle()
            except Exception:
                scope_handle = None
            save_response, scope_visible, message = (
                self._click_case_save_with_business_repairs(
                    scope,
                    save,
                    responses,
                    submitted,
                    current,
                    scope_handle,
                    expected_type=case.expected_type,
                    business_request_started=lambda: bool(observed_requests),
                    protected_codes=(required_codes or {case.field_key}),
                    retryable_unique_codes=(
                        {case.field_key}
                        if is_create and not case.field_type.endswith("_command")
                        else set()
                    ),
                    allow_unique_repair=is_create,
                )
            )
            if save_response is not None and attachment_lifecycle_tracker is not None:
                self.driver._wait_for_attachment_lifecycle(
                    attachment_lifecycle_tracker,
                    phase="EDIT-004 附件保存",
                )
            record_markers = self.driver._collect_record_identity_markers(
                submitted, scope=scope
            )

            control_rejected = str(actual) != str(requested)
            if case.expected_type == "field_error":
                if control_rejected and save_response is not None:
                    commit_unique_reservation()
                    if scope_visible:
                        self._close_retained_form_before_readback(
                            scope, scope_handle
                        )
                    self._verify_saved_record(
                        responses,
                        save_response,
                        submitted,
                        record_markers,
                        required_codes=required_codes,
                        rendered_text_expectations=rendered_text_expectations,
                        require_edit_and_detail=require_edit_and_detail,
                    )
                    return CommonFieldExecutionResult(
                        case.case_id,
                        case.field_key,
                        "truncated_saved_verified_and_retained",
                        f"requested={requested!r}, saved={actual!r}, before={before!r}",
                    )
                if message and save_response is None and scope_visible:
                    return CommonFieldExecutionResult(
                        case.case_id, case.field_key, "save_blocked", message
                    )
                if save_response is not None:
                    self._fail_saved_case(
                        f"异常值被保存接口接受：{case.pytest_id}, "
                        f"requested={requested!r}, actual={actual!r}",
                    )
                raise AssertionError(
                    f"点击保存后既无校验提示也无保存接口响应：{case.pytest_id}"
                )

            if case.expected_type == "safe_handling":
                if save_response is None:
                    if (
                        scope_visible
                        and message
                        and self._is_explicit_safe_content_rejection(message)
                    ):
                        return CommonFieldExecutionResult(
                            case.case_id,
                            case.field_key,
                            "safe_content_rejected",
                            message,
                        )
                    probed = self._probe_safe_content_rejection(
                        case,
                        scope,
                        save,
                        responses,
                        submitted,
                        current,
                        scope_handle,
                        failed_requests,
                        required_codes=required_codes,
                        rendered_text_expectations=rendered_text_expectations,
                        require_edit_and_detail=require_edit_and_detail,
                    )
                    if probed is not None:
                        return probed
                    request_failure = self._failed_request_summary(failed_requests)
                    raise AssertionError(
                        f"脚本安全用例既未安全保存也无可证明的内容级拒绝："
                        f"{case.pytest_id}; validation={message or 'none'}; "
                        f"request={self._mutation_request_summary(observed_requests)}; "
                        f"requestfailed={request_failure}"
                    )
                if scope_visible:
                    self._close_retained_form_before_readback(scope, scope_handle)
                commit_unique_reservation()
                self._verify_saved_record(
                    responses,
                    save_response,
                    submitted,
                    record_markers,
                    required_codes=required_codes,
                    rendered_text_expectations=rendered_text_expectations,
                    require_edit_and_detail=require_edit_and_detail,
                )
                return CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "safe_content_saved_verified",
                    f"requested={requested!r}, persisted={actual!r}",
                )

            if control_rejected:
                raise AssertionError(
                    f"合法值未被控件完整接受：field={case.field_key}, "
                    f"requested={requested!r}, actual={actual!r}"
                )
            if save_response is None:
                request_failure = self._failed_request_summary(failed_requests)
                raise AssertionError(
                    f"点击保存后没有捕获保存接口响应：{case.pytest_id}; "
                    f"validation={message or 'none'}; "
                    f"request={self._mutation_request_summary(observed_requests)}; "
                    f"requestfailed={request_failure}"
                )
            if scope_visible:
                self._close_retained_form_before_readback(scope, scope_handle)
            commit_unique_reservation()
            self._verify_saved_record(
                responses,
                save_response,
                submitted,
                record_markers,
                required_codes=required_codes,
                rendered_text_expectations=rendered_text_expectations,
                require_edit_and_detail=require_edit_and_detail,
            )
            return CommonFieldExecutionResult(
                case.case_id,
                case.field_key,
                "saved_verified_and_retained",
                str(actual),
            )
        finally:
            if is_create and not unique_reservation_committed:
                release = getattr(
                    self.driver, "release_pending_unique_reservations", None
                )
                if callable(release):
                    release()
            if hasattr(self.page, "remove_listener"):
                self.page.remove_listener("response", response_received)
                self.page.remove_listener("request", request_started)
                self.page.remove_listener("requestfinished", request_finished)
                self.page.remove_listener("requestfailed", request_failed)

    @staticmethod
    def _failed_request_summary(requests: list[str]) -> str:
        if not requests:
            return "none"
        return " | ".join(requests[-3:])[:1_000]

    @staticmethod
    def _mutation_request_summary(requests) -> str:
        if not requests:
            return "none"
        items = []
        for request in requests[-3:]:
            method = str(getattr(request, "method", "?")).upper()
            path = urlsplit(str(getattr(request, "url", ""))).path or "<unknown-path>"
            status = None
            try:
                response = request.response
                response = response() if callable(response) else response
                status = getattr(response, "status", None) if response is not None else None
            except Exception:
                pass
            items.append(
                f"{method} {path}" + (f" HTTP {status}" if status is not None else "")
            )
        return " | ".join(items)[:1_000]

    @staticmethod
    def _contains_executable_content(value: Any) -> bool:
        return bool(re.search(
            r"<\s*/?\s*(?:script|img|svg|iframe|object|embed|style)\b|"
            r"\bon\w+\s*=|javascript\s*:",
            str(value or ""),
            re.IGNORECASE,
        ))

    @staticmethod
    def _safe_content_probe_value(value: Any) -> str:
        return re.sub(r"[<>&\"']", "_", str(value or ""))

    @staticmethod
    def _is_explicit_safe_content_rejection(message: str) -> bool:
        text = re.sub(r"\s+", "", str(message or ""))
        return bool(
            text
            and re.search(
                r"脚本|XSS|跨站|危险(?:字符|内容)|非法(?:字符|内容)|"
                r"敏感(?:字符|内容)|不允许.*(?:HTML|标签|字符|内容)|安全策略",
                text,
                re.IGNORECASE,
            )
        )

    def _probe_safe_content_rejection(
        self,
        case: BoundCommonCase,
        scope,
        save,
        responses,
        submitted: dict[str, Any],
        current: DiscoveredCommonField,
        scope_handle,
        failed_requests: list[str],
        *,
        required_codes: set[str] | None,
        rendered_text_expectations: dict[str, tuple[str, str]] | None,
        require_edit_and_detail: bool,
    ) -> CommonFieldExecutionResult | None:
        if not any("ERR_CONNECTION_RESET" in item for item in failed_requests):
            return None
        if not self._original_scope_is_visible(scope, scope_handle):
            return None

        runtime_fields = {
            field.field_key: field
            for field in discover_common_fields(self._scan_fields(scope))
        }
        replacements: dict[str, tuple[str, str]] = {}
        for field_key, raw_value in list(submitted.items()):
            if not self._contains_executable_content(raw_value):
                continue
            field = runtime_fields.get(field_key)
            if field is None:
                return None
            safe_value = self._safe_content_probe_value(raw_value)
            self._replace_value(field, safe_value)
            actual = str(self._input_value(self._locator(field)))
            if actual != safe_value:
                raise AssertionError(
                    f"脚本安全用例健康探测值未被控件完整接受："
                    f"field={field_key}, requested={safe_value!r}, actual={actual!r}"
                )
            submitted[field_key] = actual
            replacements[field_key] = (str(raw_value), actual)
        if not replacements:
            return None

        probe_current = runtime_fields.get(current.field_key, current)
        probe_response, probe_scope_visible, probe_message = (
            self._click_case_save_with_business_repairs(
                scope,
                save,
                responses,
                submitted,
                probe_current,
                scope_handle,
                expected_type="accepted",
                protected_codes=set(replacements),
            )
        )
        if probe_response is None:
            raise AssertionError(
                "脚本内容请求断连后，无害内容健康探测也未保存成功："
                f"validation={probe_message or 'none'}"
            )
        record_markers = self.driver._collect_record_identity_markers(
            submitted, scope=scope
        )
        if probe_scope_visible:
            self._close_retained_form_before_readback(scope, scope_handle)
        self._verify_saved_record(
            responses,
            probe_response,
            submitted,
            record_markers,
            required_codes=required_codes,
            rendered_text_expectations=rendered_text_expectations,
            require_edit_and_detail=require_edit_and_detail,
        )
        return CommonFieldExecutionResult(
            case.case_id,
            case.field_key,
            "safe_content_rejected",
            "危险内容请求被连接级拒绝，且同表单无害内容健康探测保存成功；"
            f"fields={sorted(replacements)}",
        )

    def _click_case_save_with_business_repairs(
        self,
        scope,
        save,
        responses,
        submitted: dict[str, Any],
        current: DiscoveredCommonField,
        scope_handle,
        *,
        expected_type: str,
        business_request_started: Callable[[], bool] | None = None,
        protected_codes: set[str] | None = None,
        retryable_unique_codes: set[str] | None = None,
        allow_unique_repair: bool = True,
    ):
        """Save with bounded validation repairs and one no-dispatch self-repair."""
        max_attempts = max(1, int(os.getenv("EI_VALIDATION_SAVE_ATTEMPTS", "3")))
        dispatch_timeout_s = max(
            0,
            int(os.getenv("EI_COMMON_SAVE_DISPATCH_TIMEOUT_MS", "5000")),
        ) / 1000
        last_message = ""
        for attempt in range(1, max_attempts + 1):
            self._capture_submitted_display_values(submitted, scope)
            response_start = len(responses)
            save.click()
            save_response = None
            deadline = time.monotonic() + 30
            dispatch_deadline = time.monotonic() + dispatch_timeout_s
            dispatch_repaired = False
            hidden_response_deadline = None
            while time.monotonic() < deadline:
                attempt_responses = responses[response_start:]
                save_response = self.driver._find_save_response(
                    attempt_responses, submitted
                )
                if save_response is not None:
                    break
                if not self._original_scope_is_visible(scope, scope_handle):
                    if hidden_response_deadline is None:
                        settle_ms = int(os.getenv(
                            "EI_COMMON_COMMAND_FORM_SETTLE_MS",
                            os.getenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "3000"),
                        ))
                        hidden_response_deadline = (
                            time.monotonic() + max(0, settle_ms) / 1000
                        )
                    elif time.monotonic() >= hidden_response_deadline:
                        break
                else:
                    last_message = self._visible_error_text(scope, current)
                    if last_message:
                        self.page.wait_for_timeout(300)
                        save_response = self.driver._find_save_response(
                            responses[response_start:], submitted
                        )
                        break
                if (
                    business_request_started is not None
                    and not business_request_started()
                    and time.monotonic() >= dispatch_deadline
                ):
                    if not dispatch_repaired and self._original_scope_is_visible(
                        scope, scope_handle
                    ):
                        repaired_save = self.driver._save_button(scope)
                        if (
                            repaired_save.count()
                            and repaired_save.is_visible()
                            and repaired_save.is_enabled()
                        ):
                            dispatch_repaired = True
                            save = repaired_save
                            response_start = len(responses)
                            dispatch_deadline = time.monotonic() + dispatch_timeout_s
                            print(
                                "COMMON_SAVE_DISPATCH_REPAIR retry=1 "
                                f"field={current.field_key}",
                                flush=True,
                            )
                            save.click()
                            continue
                    return (
                        None,
                        self._original_scope_is_visible(scope, scope_handle),
                        "点击保存后未发起业务请求（已重新定位并重试一次）",
                    )
                self.page.wait_for_timeout(200)

            scope_visible = self._original_scope_is_visible(scope, scope_handle)
            if save_response is None:
                if not last_message and scope_visible:
                    last_message = self._visible_error_text(scope, current)
                return None, scope_visible, last_message
            if not save_response.ok:
                try:
                    response_detail = save_response.text()[:1000]
                except Exception:
                    response_detail = "<响应正文不可读>"
                if (
                    expected_type == "safe_handling"
                    and 400 <= save_response.status < 500
                    and self._is_explicit_safe_content_rejection(response_detail)
                ):
                    return (
                        None,
                        self._original_scope_is_visible(scope, scope_handle),
                        response_detail,
                    )
                raise AssertionError(
                    f"保存接口失败：HTTP {save_response.status} {save_response.url}; "
                    f"response={response_detail}"
                )
            body = None
            try:
                body = save_response.json()
                self.driver._assert_business_success(body)
            except ValueError:
                try:
                    self.driver._assert_business_success(save_response.text())
                except AssertionError as exc:
                    last_message = str(exc)
                    if expected_type == "field_error" or (
                        expected_type == "safe_handling"
                        and self._is_explicit_safe_content_rejection(last_message)
                    ):
                        return None, scope_visible, last_message
                    raise
            except AssertionError:
                last_message = (
                    str(body.get("message") or body.get("msg") or body)
                    if isinstance(body, dict)
                    else str(body)
                )
                if expected_type == "field_error" or (
                    expected_type == "safe_handling"
                    and self._is_explicit_safe_content_rejection(last_message)
                ):
                    return None, scope_visible, last_message
                if not allow_unique_repair:
                    raise
                repaired = self.driver._repair_business_validation_message(
                    last_message,
                    submitted,
                    attempt,
                    protected_codes=protected_codes,
                    retryable_unique_codes=retryable_unique_codes,
                    allow_unique_repair=allow_unique_repair,
                )
                can_retry = (
                    repaired
                    and attempt < max_attempts
                    and self._original_scope_is_visible(scope, scope_handle)
                )
                if can_retry:
                    submitted.update(repaired)
                    continue
                raise

            form_hidden = self._wait_for_command_form_completion(scope, scope_handle)
            return save_response, not form_hidden, ""
        return None, self._original_scope_is_visible(scope, scope_handle), last_message

    def _verify_saved_record(
        self,
        responses,
        save_response,
        submitted: dict[str, Any],
        record_markers: tuple[str, ...],
        *,
        required_codes: set[str] | None = None,
        rendered_text_expectations: dict[str, tuple[str, str]] | None = None,
        require_edit_and_detail: bool = False,
        terminal_operation: bool = False,
    ):
        self._wait_for_saved_ui_settle()
        # An explicit empty set delegates ordinary-field readback to a
        # specialized verifier (currently EDIT-004 attachment persistence).
        # Preserve that contract instead of treating it as an omitted option.
        verify_options = (
            {"required_codes": set(required_codes)}
            if required_codes is not None
            else {}
        )
        if rendered_text_expectations:
            verify_options["rendered_text_expectations"] = dict(
                rendered_text_expectations
            )
        if require_edit_and_detail:
            verify_options["require_edit_and_detail"] = True
        form_action = os.getenv("EI_COMMON_FORM_ACTION", "").strip()
        created_by_automation = (
            not form_action or form_action.startswith(COMMON_ADD_ACTION_PREFIXES)
        )
        if created_by_automation:
            verify_options.update(
                created_by_automation=True,
                automation_registry_scope=getattr(self, "entry_url", ""),
            )
        if (
            not terminal_operation
            and form_action.startswith(COMMON_EDIT_ACTION_PREFIXES)
        ):
            verify_options["saved_from_current_detail_edit"] = True
        try:
            return self.driver.verify_saved_record(
                responses,
                save_response,
                submitted,
                record_markers,
                **verify_options,
            )
        except Exception as exc:
            self._capture_failure_once(str(exc))
            raise

    def _wait_for_saved_ui_settle(self) -> None:
        """Wait for post-save rendering without always idling for one second."""
        loading = None
        try:
            loading = self.page.locator(
                ".el-loading-mask:visible,.ant-spin-spinning:visible,"
                "[aria-busy='true']:visible,[data-loading='true']:visible"
            ).first
        except (AttributeError, TypeError):
            pass
        if loading is not None:
            try:
                if loading.count() and loading.is_visible():
                    loading.wait_for(state="hidden", timeout=20_000)
            except Exception as exc:
                raise AssertionError("保存成功后页面在 20 秒内仍未完成刷新") from exc
        settle_ms = max(
            0,
            int(os.getenv("EI_COMMON_POST_SAVE_SETTLE_MS", "300")),
        )
        if settle_ms:
            self.page.wait_for_timeout(settle_ms)

    def _capture_submitted_display_values(self, submitted, scope) -> dict[str, str]:
        capture = getattr(self.driver, "_capture_submitted_display_values", None)
        if not callable(capture):
            return {}
        return capture(submitted, scope)

    @staticmethod
    def _requires_edit_and_detail_readback(case: BoundCommonCase) -> bool:
        return bool(
            case.field_type == "save_command"
            and "输入值正确性检查" in case.scenario
            and "编辑" in case.expected_value
            and "详情" in case.expected_value
        )

    @staticmethod
    def _requires_rendered_whitespace_check(case, value: Any) -> bool:
        if case.expected_type != "accepted" or case.field_type != "textarea":
            return False
        text = str(value or "")
        return bool(
            "\n" in text
            or "\r" in text
            or "\t" in text
            or re.search(r" {2,}", text)
        )

    def _fail_saved_case(self, failure: str) -> None:
        self._capture_failure_once(failure)
        raise AssertionError(failure)

    def _capture_failure_once(self, message: str) -> None:
        if getattr(self.page, "_ei_ui_failure_evidence", None) is None:
            capture_failure_evidence(self.page, message)

    @staticmethod
    def _original_scope_is_visible(scope, scope_handle) -> bool:
        """Check the submitted form element, not a dynamically re-resolved locator."""
        if scope_handle is not None:
            try:
                return scope_handle.is_visible()
            except Exception:
                return False
        try:
            return scope.is_visible()
        except Exception:
            return False

    @staticmethod
    def _case_input_value(case, field, baseline):
        """Keep rule semantics while making business-name saves rerunnable."""
        requested = case.input_value
        if requested == "__REPLACE_LAST_WITH_9__":
            current = str(baseline or "")
            return f"{current[:-1]}9" if current else "9"
        if not isinstance(requested, str):
            return requested
        maximum = field.constraints.max_length
        identity_field = field.field_key.lower() in ModuleSmokeDriver.RECORD_IDENTITY_CODES
        if not identity_field:
            return CommonFieldExecutor._bounded_positive_text_value(
                case, requested, maximum
            )
        marker = str(baseline).strip()
        if not marker:
            return CommonFieldExecutor._bounded_positive_text_value(
                case, requested, maximum
            )
        if not marker.startswith(AUTOMATION_RECORD_PREFIXES):
            marker = f"AUTO_{marker}"
        if maximum and "长度" in case.scenario and requested:
            target_length = len(requested)
            if target_length < min(map(len, AUTOMATION_RECORD_PREFIXES)):
                raise AssertionError(
                    f"名称字段长度 {target_length} 无法容纳安全自动化标记，禁止保存"
                )
            effective_length = min(target_length, maximum)
            marker = CommonFieldExecutor._compact_record_identity_marker(
                marker, effective_length
            )
            padding_length = max(0, target_length - len(marker))
            return (marker + requested * max(1, padding_length))[:target_length]
        leading = requested[: len(requested) - len(requested.lstrip())]
        trailing = requested[len(requested.rstrip()) :]
        core = requested.strip()
        owned_value = marker if not core else f"{marker}_{core}"
        if maximum is not None:
            available = maximum - len(leading) - len(trailing)
            if available < min(map(len, AUTOMATION_RECORD_PREFIXES)):
                raise AssertionError(
                    f"名称字段最大长度 {maximum} 无法容纳安全自动化标记，禁止保存"
                )
            owned_value = owned_value[:available]
        return f"{leading}{owned_value}{trailing}"

    @staticmethod
    def _bounded_positive_text_value(
        case: BoundCommonCase,
        requested: str,
        maximum: int | None,
    ) -> str:
        if (
            case.expected_type == "accepted"
            and maximum
            and maximum > 0
            and len(requested) > maximum
            and "长度" not in case.scenario
        ):
            return requested[:maximum]
        return requested

    @staticmethod
    def _compact_record_identity_marker(marker: str, maximum: int) -> str:
        if len(marker) <= maximum:
            return marker
        prefix = next(
            (item for item in AUTOMATION_RECORD_PREFIXES if marker.startswith(item)),
            "",
        )
        token_match = None
        for token_match in re.finditer(r"_S\d{3,}", marker):
            pass
        token = token_match.group(0) if token_match else ""
        if prefix and token and len(prefix) + len(token) <= maximum:
            middle = marker[len(prefix):]
            token_index = middle.rfind(token)
            if token_index >= 0:
                middle = middle[:token_index]
            middle_length = maximum - len(prefix) - len(token)
            return f"{prefix}{middle[:middle_length]}{token}"
        return marker[:maximum]

    @staticmethod
    def _log_result(case: BoundCommonCase, result: CommonFieldExecutionResult) -> None:
        observed = result.observed
        if len(observed) > 240:
            observed = f"{observed[:240]}... [length={len(result.observed)}]"
        print(
            f"COMMON_CASE_RESULT id={case.pytest_id} outcome={result.outcome} "
            f"observed={observed!r}",
            flush=True,
        )

    def open_add_form(self, *, require_new: bool = False):
        action = os.getenv("EI_COMMON_FORM_ACTION", "").strip()
        if action and not action.startswith(COMMON_ADD_ACTION_PREFIXES):
            return self.open_action_form(action)
        dialog = self.page.locator(DIALOG).last
        if not require_new and dialog.count() and dialog.is_visible():
            popper = self.page.locator(
                ".el-popper:visible,.el-select-dropdown:visible,[role=listbox]:visible"
            )
            if popper.count():
                self.page.keyboard.press("Escape")
            print("COMMON_FORM_OPEN mode=reuse", flush=True)
            return dialog
        generation = f"ei-before-add-{uuid.uuid4().hex}"
        existing = self.page.locator(DIALOG)
        for index in range(existing.count()):
            candidate = existing.nth(index)
            if candidate.is_visible():
                candidate.evaluate(
                    "(node, marker) => node.setAttribute('data-ei-form-generation', marker)",
                    generation,
                )
        add = self.driver._wait_for_add_button()
        add.click()
        scope = self._wait_for_new_form_scope(generation)
        self.driver._wait_for_form_ready(scope)
        print("COMMON_FORM_OPEN mode=new", flush=True)
        return scope

    def open_action_form(self, action: str):
        target = visible_action(self.page, action, timeout=15_000)
        if target is None:
            raise AssertionError(f"页面要求执行{action}，但没有找到可见且可用的操作入口")
        target.click()
        scope = self.driver._wait_for_form_scope()
        self.driver._wait_for_form_ready(scope)
        print(f"COMMON_FORM_OPEN mode=action action={action}", flush=True)
        return scope

    def _pin_form_scope(self, scope):
        """Replace a positional Dialog locator with a stable instance selector."""
        if not hasattr(scope, "evaluate"):
            return scope
        marker = f"ei-common-form-{uuid.uuid4().hex}"
        try:
            scope.evaluate(
                "(node, value) => node.setAttribute('data-ei-common-form-session', value)",
                marker,
            )
            pinned = self.page.locator(
                f'[data-ei-common-form-session="{marker}"]'
            ).first
            if pinned.count() == 1 and pinned.is_visible():
                return pinned
        except Exception as exc:
            raise AssertionError("无法为新增表单建立稳定会话定位") from exc
        raise AssertionError("新增表单会话标识未唯一命中原表单实例")

    def open_fresh_add_form(self):
        try:
            self.close_form()
            driver = getattr(self, "driver", None)
            if driver is not None:
                driver._submitted_display_values = {}
            prepare_form_context = getattr(self, "prepare_form_context", None)
            if prepare_form_context is not None:
                prepare_form_context(self.page)
            else:
                entry_url = getattr(self, "entry_url", "")
                if entry_url and self.page.url != entry_url:
                    self.page.goto(entry_url, wait_until="domcontentloaded")
            if not os.getenv("EI_COMMON_FORM_ACTION", "").strip().startswith(
                COMMON_EDIT_ACTION_PREFIXES
            ):
                prepare_unique = getattr(
                    getattr(self, "driver", None),
                    "prepare_unique_constraint_evidence",
                    None,
                )
                if callable(prepare_unique):
                    prepare_unique()
            return self.open_add_form(require_new=True)
        except SharedFormPreconditionError:
            raise
        except Exception as exc:
            raise SharedFormPreconditionError(
                f"无法建立通用用例共享表单前置条件：{exc}"
            ) from exc

    def _close_session_scope(self, scope) -> None:
        self._form_scope_to_close = scope
        try:
            self.close_form()
        finally:
            self._form_scope_to_close = None

    def _close_form_after_execution(self, primary_error: Exception | None) -> None:
        try:
            self.close_form()
        except Exception as close_error:
            if primary_error is None:
                raise
            print(
                f"COMMON_FORM_CLEANUP_FAILED_AFTER_PRIMARY_ERROR error={close_error}",
                flush=True,
            )

    def _wait_for_new_form_scope(self, previous_generation: str, timeout: int = 15_000):
        deadline = time.monotonic() + timeout / 1000
        while time.monotonic() < deadline:
            dialogs = self.page.locator(DIALOG)
            for index in range(dialogs.count() - 1, -1, -1):
                candidate = dialogs.nth(index)
                if not candidate.is_visible():
                    continue
                if candidate.get_attribute("data-ei-form-generation") == previous_generation:
                    continue
                controls = candidate.locator(EDITABLE_FORM_CONTROL)
                if controls.count() and controls.first.is_visible():
                    return candidate
            self.page.wait_for_timeout(100)
        raise AssertionError(
            "点击新增后没有出现包含可编辑控件的新表单实例；"
            "旧弹窗可能仍在关闭过渡状态"
        )

    def close_form(self) -> None:
        dialog = getattr(self, "_form_scope_to_close", None)
        if dialog is not None:
            try:
                if not dialog.count() or not dialog.is_visible():
                    return
            except Exception:
                return
        else:
            dialog = self.page.locator(DIALOG).last
        if not dialog.count() or not dialog.is_visible():
            return
        dialog_handle = dialog.element_handle()
        existing_confirmation_handles = self._visible_confirmation_handles()
        close = dialog.locator(
            "button:has-text('关闭'),button:has-text('取消')"
        ).last
        if not close.count() or not close.is_visible():
            close = self._form_close_button(dialog, strict=True)
        if close is not None and close.count() and close.is_visible():
            close.click(timeout=3_000)
            self._confirm_form_cancellation_if_present(
                dialog,
                dialog_handle,
                ignored_confirmation_handles=existing_confirmation_handles,
            )
            try:
                dialog_handle.wait_for_element_state("hidden", timeout=5_000)
            except Exception as exc:
                if self._original_scope_is_visible(dialog, dialog_handle):
                    raise AssertionError(
                        "上一条用例的表单在 5 秒内未关闭，禁止复用旧弹窗执行下一条用例"
                    ) from exc

    def _current_field(self, case: BoundCommonCase, scope) -> DiscoveredCommonField:
        self._apply_case_branch_conditions(case, scope)
        fields = discover_common_fields(self._scan_fields(scope))
        candidates = [field for field in fields if field.field_key == case.field_key]
        compatible = [
            field for field in candidates if self._field_matches_case_type(case, field)
        ]
        if compatible:
            # Radio/checkbox groups legitimately expose multiple native controls for
            # one business field. The group identity is the stable field key, not
            # its count.
            return compatible[0]
        label_candidates = [
            field
            for field in fields
            if field.label.strip() == case.field_label.strip()
            and self._field_matches_case_type(case, field)
        ]
        if label_candidates:
            return label_candidates[0]
        runtime_choice = self._runtime_choice_field(case, scope)
        if runtime_choice is not None:
            return runtime_choice
        rebound_generated = self._rebind_generated_id_field(case, scope)
        if rebound_generated is not None:
            return rebound_generated
        if candidates:
            observed = ", ".join(
                f"{field.label or field.field_key}/{field.field_type}/{field.kind}"
                for field in candidates
            )
            raise AssertionError(
                f"当前表单字段身份错配：{case.field_key} ({case.field_label}) "
                f"期望 {case.field_type}，实际 {observed}；"
                "禁止按旧动态 ID 或源码顺序兜底执行"
            )
        if not label_candidates:
            # Choice groups often expose one native control per option. Their stable
            # selector from discovery is a stronger identity than positional remapping.
            if case.field_type in {"select", "radio"} and case.selector:
                return DiscoveredCommonField(
                    case.field_key,
                    case.field_label,
                    case.field_type,
                    case.field_type,
                    case.selector,
                    FieldConstraints(),
                )
            raise AssertionError(
                f"当前表单无法定位字段：{case.field_key} ({case.field_label})；"
                f"实际字段={self._runtime_field_inventory(fields)}"
            )

    def _rebind_generated_id_field(self, case: BoundCommonCase, scope):
        """Rebind a manifest-only Element Plus ID when its stable suffix is unique."""
        selector = str(case.selector or "").strip()
        match = re.fullmatch(r"#(el-id-[A-Za-z0-9_-]*?-(\d+))", selector)
        if match is None:
            return None
        suffix = match.group(2)
        try:
            candidates = scope.locator(f'input[id$="-{suffix}"]')
            if candidates.count() != 1 or not candidates.first.is_visible():
                return None
            current_id = candidates.first.get_attribute("id") or ""
        except Exception:
            return None
        if not re.fullmatch(rf"el-id-[A-Za-z0-9_-]*-{re.escape(suffix)}", current_id):
            return None
        print(
            "COMMON_FIELD_REBOUND "
            f"field={case.field_key} generated_id_suffix=-{suffix}",
            flush=True,
        )
        return DiscoveredCommonField(
            case.field_key,
            case.field_label,
            case.field_type,
            case.field_type,
            f"#{current_id}",
            FieldConstraints(),
        )

    def _runtime_field_inventory(self, fields: Iterable[DiscoveredCommonField]) -> str:
        """Keep locator failures actionable without exposing form values."""
        inventory = ", ".join(
            f"{field.field_key}|{field.label}|{field.field_type}|{field.selector}"
            for field in fields
        )
        return inventory or f"<none>; context={self._form_context_inventory()}"

    def _form_context_inventory(self) -> str:
        """Describe visible form containers without reading user-entered values."""
        try:
            dialogs = self.page.locator(DIALOG)
            dialog_counts = []
            for index in range(dialogs.count()):
                dialog = dialogs.nth(index)
                dialog_counts.append(
                    str(dialog.locator(EDITABLE_FORM_CONTROL).count())
                )
            active = getattr(getattr(self, "_form_session", None), "active", None)
            scope_count = (
                active.scope.locator(EDITABLE_FORM_CONTROL).count()
                if active is not None else 0
            )
            scoped_controls = self._editable_control_inventory(
                active.scope if active is not None else None
            )
            return (
                f"dialogs={dialogs.count()} editable={dialog_counts}; "
                f"scopeEditable={scope_count}; "
                f"pageEditable={self.page.locator(EDITABLE_FORM_CONTROL).count()}; "
                f"visibleForms={self.page.locator('.el-form:visible,form:visible').count()}; "
                f"scopeControls={scoped_controls}"
            )
        except Exception as exc:
            return f"<unavailable:{type(exc).__name__}:{str(exc)[:180]}>"

    @staticmethod
    def _editable_control_inventory(scope, *, limit: int = 8) -> str:
        """Report control identity only when field scanning unexpectedly returns none."""
        if scope is None:
            return "<none>"
        controls = scope.locator(EDITABLE_FORM_CONTROL)
        items = []
        for index in range(min(controls.count(), limit)):
            control = controls.nth(index)
            try:
                items.append(
                    control.evaluate(
                        """node => {
                            const style = getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            const hiddenAncestor = node.closest('[hidden]');
                            const ariaHidden = node.closest('[aria-hidden="true"]');
                            return [
                                node.tagName.toLowerCase(), node.getAttribute('type') || '',
                                node.id || '', node.getAttribute('name') || '',
                                node.closest('.el-form-item,.purvar_form_item')?.getAttribute('prop') || '',
                                `display=${style.display};visibility=${style.visibility};rect=${Math.round(rect.width)}x${Math.round(rect.height)}`,
                                `hidden=${hiddenAncestor?.tagName.toLowerCase() || ''}`,
                                `ariaHidden=${ariaHidden?.tagName.toLowerCase() || ''}`,
                                `readonly=${node.readOnly ? 'true' : 'false'}`,
                            ].join('|');
                        }"""
                    )
                )
            except Exception as exc:
                items.append(f"<unavailable:{type(exc).__name__}>")
        suffix = "+" if controls.count() > limit else ""
        return ", ".join(items) + suffix

    def _wait_for_current_field(
        self,
        case: BoundCommonCase,
        scope,
        *,
        timeout_ms: int | None = None,
    ) -> DiscoveredCommonField:
        """Wait for a target that appears after edit-form data hydration."""
        timeout_ms = timeout_ms or int(
            os.getenv("EI_COMMON_FIELD_HYDRATION_TIMEOUT_MS", "10000")
        )
        deadline = time.monotonic() + timeout_ms / 1000
        last_error: AssertionError | None = None
        while True:
            try:
                return self._current_field(case, scope)
            except AssertionError as exc:
                if not str(exc).startswith("当前表单无法定位字段："):
                    raise
                last_error = exc
            if time.monotonic() >= deadline:
                raise AssertionError(
                    f"当前表单在 {timeout_ms / 1000:g} 秒内未完成字段水合："
                    f"{case.field_key} ({case.field_label})；"
                    f"last={last_error or '<none>'}"
                ) from last_error
            self.page.wait_for_timeout(100)

    @staticmethod
    def _field_matches_case_type(
        case: BoundCommonCase, field: DiscoveredCommonField
    ) -> bool:
        if case.field_type == "required":
            return True
        if field.field_type == case.field_type or field.kind == case.field_type:
            return True
        text_types = {"text", "phone", "contact", "email", "password", "id_card"}
        return case.field_type in text_types and field.field_type in text_types

    def _runtime_choice_field(self, case: BoundCommonCase, scope):
        if case.field_type not in {"select", "radio"}:
            return None
        locator = self._runtime_choice_locator(case.field_key, case.field_type, scope)
        if locator is None:
            return None
        return DiscoveredCommonField(
            case.field_key,
            case.field_label,
            case.field_type,
            case.field_type,
            self._choice_control_selector(case.field_key, case.field_type) or case.selector,
            FieldConstraints(),
        )

    def _runtime_choice_locator(self, field_key: str, field_type: str, scope=None):
        selector = self._choice_control_selector(field_key, field_type)
        if not selector:
            return None
        root = scope if scope is not None else self.page
        try:
            locator = root.locator(selector)
            return locator if locator.count() else None
        except Exception:
            return None

    @classmethod
    def _choice_control_selector(cls, field_key: str, field_type: str) -> str:
        if not field_key:
            return ""
        literal = cls._css_attr_literal(field_key)
        owners = (
            f'[prop={literal}]',
            f'[field-code={literal}]',
            f'[data-field-code={literal}]',
        )
        direct_attrs = (
            f'[name={literal}]',
            f'[data-field-code={literal}]',
            f'[field-code={literal}]',
        )
        if field_type == "radio":
            selectors = [
                *(f'{owner} .el-radio-group' for owner in owners),
                *(f'{owner} .el-radio' for owner in owners),
                *(f'{owner} input[type="radio"]' for owner in owners),
                *(f'{owner} [role="radio"]' for owner in owners),
                *(f'{attr}.el-radio-group' for attr in direct_attrs),
                *(f'{attr}.el-radio' for attr in direct_attrs),
                *(f'input[type="radio"]{attr}' for attr in direct_attrs),
                *(f'[role="radio"]{attr}' for attr in direct_attrs),
            ]
            return ",".join(selectors)
        if field_type == "select":
            selectors = [
                *(f"{owner} .el-select" for owner in owners),
                *(f'{owner} [role="combobox"]' for owner in owners),
                *(f'{owner} input[readonly][role="combobox"]' for owner in owners),
                *(f"{attr}.el-select" for attr in direct_attrs),
                *(f'{attr}[role="combobox"]' for attr in direct_attrs),
            ]
            return ",".join(selectors)
        return ""

    @staticmethod
    def _css_attr_literal(value: str) -> str:
        escaped = (value or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'

    def _active_form_scope(self):
        session = getattr(self, "_form_session", None)
        active = getattr(session, "active", None)
        return active.scope if active is not None else None

    def _same_runtime_field(
        self, item: DomField, field: DiscoveredCommonField
    ) -> bool:
        if item.field_code == field.field_key:
            return True
        item_label = (item.label or "").strip()
        field_label = (field.label or "").strip()
        if not item_label or not field_label:
            return False
        if item_label == field_label:
            return True
        normalize = getattr(self.driver, "_normalize_label", lambda value: value)
        return normalize(item_label) == normalize(field_label)

    def _dom_for_discovered_field(
        self, field: DiscoveredCommonField, scope=None
    ) -> DomField:
        fields = self._scan_fields(scope)
        match = next(
            (item for item in fields if self._same_runtime_field(item, field)),
            None,
        )
        if match is not None:
            return match
        if field.selector:
            return DomField(
                field.field_key,
                field.label,
                field.kind,
                field.selector,
                required=bool(getattr(field, "required", False)),
                readonly=bool(getattr(field, "readonly", False)),
                maxlength=getattr(field.constraints, "max_length", None),
                minimum=getattr(field.constraints, "minimum", None),
                maximum=getattr(field.constraints, "maximum", None),
            )
        raise AssertionError(
            f"当前表单无法定位字段：{field.field_key} ({field.label})"
        )

    def _locator(self, field: DiscoveredCommonField):
        definition = self._definition(field)
        dom = self._dom_for_discovered_field(field)
        resolved = ResolvedField(definition, dom)
        root = self._active_form_scope()
        return (
            self.driver.interactor.locate(resolved, root=root)
            if root is not None
            else self.driver.interactor.locate(resolved)
        )

    def _replace_value(self, field: DiscoveredCommonField, value: Any) -> None:
        definition = self._definition(field)
        dom = self._dom_for_discovered_field(field)
        resolved = ResolvedField(definition, dom)
        root = self._active_form_scope()
        locator = (
            self.driver.interactor.locate(resolved, root=root)
            if root is not None
            else self.driver.interactor.locate(resolved)
        )
        if field.kind in {"year", "date", "datetime"}:
            if root is not None:
                self.driver.interactor.fill(resolved, value, root=root)
            else:
                self.driver.interactor.fill(resolved, value)
            return
        if field.kind not in {"text", "textarea", "number"}:
            raise AssertionError(
                f"当前通用执行器仅支持文本、文本域和数字输入：{field.field_key}/{field.kind}"
            )
        locator.fill("" if value is None else str(value))
        locator.press("Tab")

    @staticmethod
    def _definition(field: DiscoveredCommonField) -> FieldDefinition:
        return FieldDefinition(
            field_code=field.field_key,
            field_name=field.label,
            field_type=FIELD_TYPE_TO_COMPONENT.get(field.field_type, "ElInput-TEXT"),
            fixed_type=FixedType.FIXED,
            required=field.constraints.required,
            readonly=field.readonly,
            props={
                "maxlength": field.constraints.max_length,
                "min": str(field.constraints.minimum) if field.constraints.minimum is not None else None,
                "max": str(field.constraints.maximum) if field.constraints.maximum is not None else None,
                "precision": field.constraints.precision,
            },
            source=field.source,
        )

    @staticmethod
    def _input_value(locator) -> str:
        try:
            return locator.input_value()
        except Exception:
            return (locator.text_content() or "").strip()

    def _execute_choice_case(
        self, case: BoundCommonCase, field: DiscoveredCommonField, form_scope=None
    ) -> CommonFieldExecutionResult:
        # Dependent selects need their upstream fields populated before their real
        # option set exists. The target control is exercised again below.
        if form_scope is None:
            form_scope = self.page.locator(DIALOG).last
        self._apply_case_branch_conditions(case, form_scope)
        submitted = self._fill_valid_baseline(form_scope)
        locator = self._choice_locator_for_current_field(case, field, form_scope)
        item = locator.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ')][1]"
        )
        scope = item if item.count() else locator.locator("xpath=..")
        if case.field_type == "radio":
            choices = scope.locator(
                ".el-radio:not(.is-disabled),label:has(input[type=radio]:not(:disabled)),"
                "[role=radio]:not([aria-disabled=true])"
            )
            labels = self._unique_visible_texts(choices)
            if not labels:
                raise AssertionError(f"单选框没有可用选项：{field.field_key}")
            if "码值" in case.scenario and "互斥" not in case.scenario:
                return CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "choice_options_verified",
                    "; ".join(labels),
                )
            if "互斥" in case.scenario:
                choices.first.click(force=True)
                choices.last.click(force=True)
                native_checked = scope.locator("input[type=radio]:checked")
                checked_count = native_checked.count()
                if not checked_count:
                    checked_count = scope.locator(
                        "[role=radio][aria-checked=true],.el-radio.is-checked"
                    ).count()
                if checked_count != 1:
                    raise AssertionError(
                        f"单选框未保持互斥：{field.field_key}, checked={checked_count}"
                    )
                return CommonFieldExecutionResult(
                    case.case_id,
                    case.field_key,
                    "choice_single_selection_verified",
                    f"options={labels}; checked=1",
                )
            actual = submitted.get(case.field_key, labels[-1] if labels else "selected")
            submitted[case.field_key] = actual
            return self._submit_case(
                case, form_scope, field, submitted, actual, actual, ""
            )

        selected_before = self._unique_visible_texts(
            scope.locator(
                ".el-select__selected-item:not(.is-transparent),"
                ".el-select__selection .el-tag"
            )
        )
        locator.click(force=True)
        self.page.wait_for_timeout(250)
        options = self._owned_select_options(locator)
        visible_labels = self._visible_texts(options)
        labels = list(dict.fromkeys(visible_labels))
        if not labels:
            raise AssertionError(f"下拉框没有可用选项：{field.field_key}")
        if "修改选择框码值正确性检查" in case.scenario:
            if len(labels) < 2:
                raise AssertionError(
                    f"选择框少于两个可用码值：{field.field_key}, options={labels}"
                )
            target_index = next(
                (
                    index for index, label in enumerate(visible_labels)
                    if label not in selected_before
                ),
                len(visible_labels) - 1,
            )
            options.nth(target_index).click(force=True)
            selected_after = self._unique_visible_texts(
                scope.locator(
                    ".el-select__selected-item:not(.is-transparent),"
                    ".el-select__selection .el-tag"
                )
            )
            if not selected_after or selected_after == selected_before:
                raise AssertionError(
                    f"选择框码值没有发生变化：{field.field_key}, "
                    f"before={selected_before}, after={selected_after}"
                )
            actual = "; ".join(selected_after)
            submitted[case.field_key] = actual
            self._refill_required_baseline_after_target_mutation(
                form_scope, submitted, exclude_codes={case.field_key},
            )
            return self._submit_case(
                case, form_scope, field, submitted, actual, actual,
                "; ".join(selected_before),
            )
        if "固定码值" in case.scenario:
            if len(visible_labels) != len(labels):
                raise AssertionError(
                    f"下拉框存在重复选项：{field.field_key}, options={visible_labels}"
                )
            self.page.keyboard.press("Escape")
            return CommonFieldExecutionResult(
                case.case_id,
                case.field_key,
                "choice_options_verified",
                "; ".join(labels),
            )
        if "清空非必填值" in case.scenario:
            target_dom = self._dom_for_discovered_field(field, form_scope)
            if not self.driver._dom_field_has_value(target_dom, root=form_scope):
                options.first.click(force=True)
                self.page.wait_for_timeout(250)
            wrapper = locator.locator(
                "xpath=self::*[contains(concat(' ',normalize-space(@class),' '),' el-select ')]"
                "|ancestor::div[contains(concat(' ',normalize-space(@class),' '),' el-select ')][1]"
            )
            wrapper.hover()
            clear = wrapper.locator(
                ".el-select__clear,.el-icon-circle-close,[aria-label=clear]"
            ).first
            if not clear.count() or not clear.is_visible():
                raise AssertionError(f"非必填下拉框没有可用清空操作：{field.field_key}")
            clear.click(force=True)
            selected = self._unique_visible_texts(
                scope.locator(
                    ".el-select__selected-item:not(.is-transparent),.el-select__selection .el-tag"
                )
            )
            target_still_has_value = self.driver._dom_field_has_value(
                target_dom, root=form_scope,
            )
            if selected or target_still_has_value:
                raise AssertionError(
                    f"非必填下拉框清空后仍有选中值：{field.field_key}, values={selected}"
                )
        elif "只能选择一项" in case.scenario and options.count() > 1:
            options.first.click(force=True)
            locator.click(force=True)
            self.page.wait_for_timeout(150)
            refreshed = self._owned_select_options(locator)
            refreshed.last.click(force=True)
            selected = scope.locator(
                ".el-select__selected-item:not(.is-transparent),.el-select__selection .el-tag"
            )
            selected_texts = self._unique_visible_texts(selected)
            if len(selected_texts) != 1:
                raise AssertionError(
                    f"单选下拉框保留了多个值：{field.field_key}, values={selected_texts}"
                )
            self.page.keyboard.press("Escape")
            return CommonFieldExecutionResult(
                case.case_id,
                case.field_key,
                "choice_single_selection_verified",
                selected_texts[0],
            )
        else:
            options.first.click(force=True)
        selected = self._unique_visible_texts(
            scope.locator(
                ".el-select__selected-item:not(.is-transparent),"
                ".el-select__selection .el-tag"
            )
        )
        actual = "; ".join(selected)
        submitted[case.field_key] = actual
        self._refill_required_baseline_after_target_mutation(
            form_scope, submitted, exclude_codes={case.field_key},
        )
        return self._submit_case(
            case, form_scope, field, submitted, actual, actual, ""
        )

    def _choice_locator_for_current_field(
        self,
        case: BoundCommonCase,
        field: DiscoveredCommonField,
        form_scope,
    ):
        stable = form_scope.locator(field.selector).first if field.selector else None
        if (
            stable is not None
            and stable.count()
            and stable.is_visible()
            and self._choice_control_matches_field_label(stable, field)
        ):
            return stable
        label_scoped = self._choice_locator_by_field_label(field, form_scope)
        if label_scoped is not None:
            return label_scoped
        choice_group = self._runtime_choice_locator(
            case.field_key, case.field_type, form_scope
        )
        if choice_group is not None and choice_group.count():
            return choice_group.first
        return self._locator(field)

    def _choice_control_matches_field_label(
        self,
        control,
        field: DiscoveredCommonField,
    ) -> bool:
        wanted = self._normalize_choice_label(field.label or field.field_key)
        if not wanted:
            return True
        try:
            item = control.locator(
                "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ') "
                "or contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ') "
                "or @prop][1]"
            )
            if not item.count():
                return True
            actual = self._normalize_choice_label(item.inner_text() or "")
            return wanted in actual
        except Exception:
            return True

    def _choice_locator_by_field_label(
        self,
        field: DiscoveredCommonField,
        form_scope,
    ):
        wanted = self._normalize_choice_label(field.label or field.field_key)
        if not wanted:
            return None
        for selector in (
            ".el-form-item:visible,.purvar_form_item:visible,.ant-form-item:visible",
            ".el-col:visible",
        ):
            items = form_scope.locator(selector)
            for index in range(items.count()):
                item = items.nth(index)
                try:
                    actual = self._normalize_choice_label(item.inner_text() or "")
                    if wanted not in actual:
                        continue
                    controls = item.locator(
                        ".el-select:visible,[role='combobox']:visible,"
                        "input[readonly][role='combobox']:visible,"
                        ".el-radio-group:visible,.el-radio:visible,"
                        ".el-checkbox-group:visible,.el-checkbox:visible"
                    )
                    for control_index in range(controls.count()):
                        control = controls.nth(control_index)
                        if control.is_visible():
                            return control
                except Exception:
                    continue
        return None

    def _normalize_choice_label(self, value: str) -> str:
        driver = getattr(self, "driver", None)
        normalize = getattr(driver, "_normalize_label", None)
        if callable(normalize):
            return normalize(value)
        normalized = re.sub(r"[：:*\s]", "", value or "")
        for prefix in ("请输入", "请填写", "请录入", "请选择", "请上传", "请勾选"):
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                return normalized[len(prefix):]
        return normalized

    def _refill_required_baseline_after_target_mutation(
        self,
        scope,
        submitted: dict[str, Any],
        *,
        exclude_codes: set[str] | None = None,
    ) -> None:
        """Refill required controls that a target choice mutation cleared."""
        excluded = {str(code).lower() for code in (exclude_codes or set())}
        attempts = max(1, int(os.getenv("EI_FIELD_FILL_ATTEMPTS", "3")))
        missing: set[str] = set()
        for attempt in range(1, attempts + 1):
            missing = self._missing_required_dom_codes(
                scope, submitted, exclude_codes=excluded,
            )
            if not missing:
                return
            submitted.update(self.driver._fill_dialog(only_codes=missing))
            self.driver._upload_default_attachments(scope)
            nested_baseline = getattr(
                self.driver, "_prepare_implicit_required_nested_baselines",
                lambda _scope: {},
            )(scope)
            submitted.update(nested_baseline)
            if attempt < attempts:
                self.page.wait_for_timeout(500)
        if missing:
            raise AssertionError(
                "目标选择控件操作后仍有其他必填字段为空："
                + ", ".join(sorted(missing))
            )

    def _ensure_command_required_baseline(
        self, scope, submitted: dict[str, Any]
    ) -> None:
        """Recheck required controls after baseline linkage and upload rerenders."""
        required_driver_methods = (
            "_dom_field_has_value",
            "_fill_dialog",
            "_upload_default_attachments",
        )
        if not all(
            callable(getattr(self.driver, method, None))
            for method in required_driver_methods
        ):
            return
        attempts = max(1, int(os.getenv("EI_FIELD_FILL_ATTEMPTS", "3")))
        missing: set[str] = set()
        for attempt in range(1, attempts + 1):
            missing = self._missing_required_dom_codes(
                scope,
                submitted,
                trust_submitted_choices=False,
            )
            if not missing:
                return
            submitted.update(self.driver._fill_dialog(only_codes=missing))
            self.driver._upload_default_attachments(scope)
            nested_baseline = getattr(
                self.driver,
                "_prepare_implicit_required_nested_baselines",
                lambda _scope: {},
            )(scope)
            submitted.update(nested_baseline)
            if attempt < attempts:
                self.page.wait_for_timeout(500)
        if missing:
            raise AssertionError(
                "保存命令执行前仍有必填字段为空："
                + ", ".join(sorted(missing))
            )

    def _missing_required_dom_codes(
        self,
        scope,
        submitted: dict[str, Any],
        *,
        exclude_codes: set[str] | None = None,
        trust_submitted_choices: bool = True,
    ) -> set[str]:
        excluded = {str(code).lower() for code in (exclude_codes or set())}
        submitted_choice_codes = {
            str(code).lower()
            for code, value in submitted.items()
            if value not in (None, "", [])
        } if trust_submitted_choices else set()
        missing: set[str] = set()
        for field in self._scan_fields(scope):
            code = str(field.field_code or "").lower()
            if not code or code in excluded or field.readonly or not field.required:
                continue
            if not self.driver._dom_field_has_value(field, root=scope):
                if field.kind in {"radio", "checkbox"} and code in submitted_choice_codes:
                    continue
                missing.add(code)
        return missing

    @staticmethod
    def _unique_visible_texts(locator) -> list[str]:
        return list(dict.fromkeys(CommonFieldExecutor._visible_texts(locator)))

    @staticmethod
    def _visible_texts(locator) -> list[str]:
        try:
            texts = locator.evaluate_all(
                """els => els
                .filter(el => {
                  const style = window.getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  return style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    rect.width > 0 && rect.height > 0;
                })
                .map(el => (el.innerText || el.textContent || '').trim())
                .filter(Boolean)"""
            )
            if texts:
                return [str(text).strip() for text in texts if str(text).strip()]
        except Exception:
            pass
        result = []
        for index in range(locator.count()):
            item = locator.nth(index)
            try:
                if not item.is_visible():
                    text = ""
                else:
                    try:
                        text = (item.inner_text(timeout=500) or "").strip()
                    except TypeError:
                        text = (item.inner_text() or "").strip()
            except Exception:
                text = ""
            if text:
                result.append(text)
        return result

    def _owned_select_options(self, locator):
        controls_id = locator.get_attribute("aria-controls") or ""
        if not controls_id:
            try:
                owner_control = locator.locator("[aria-controls]").first
                if owner_control.count():
                    controls_id = owner_control.get_attribute("aria-controls") or ""
            except Exception:
                controls_id = ""
        if not controls_id:
            try:
                owner = locator.locator(
                    "xpath=ancestor-or-self::*[contains(concat(' ',normalize-space(@class),' '),' el-select ')][1]"
                )
                if owner.count():
                    owner_control = owner.locator("[aria-controls]").first
                    if owner_control.count():
                        controls_id = owner_control.get_attribute("aria-controls") or ""
            except Exception:
                controls_id = ""
        if controls_id:
            literal = self._css_attr_literal(controls_id)
            owned = self.page.locator(
                f"[id={literal}]:visible .el-select-dropdown__item:visible:not(.is-disabled),"
                f"[id={literal}]:visible [role=option]:visible:not([aria-disabled=true])"
            )
            if owned.count():
                return owned
        return self.page.locator(
            ".el-popper:visible .el-select-dropdown__item:not(.is-disabled),"
            ".el-select-dropdown:visible [role=option]:not([aria-disabled=true]),"
            "[role=listbox]:visible [role=option]:not([aria-disabled=true])"
        )

    def _wait_for_error(self, scope, field: DiscoveredCommonField) -> str:
        deadline_ms = int(os.getenv("EI_COMMON_ERROR_TIMEOUT_MS", "5000"))
        try:
            self.page.wait_for_timeout(200)
            self.page.locator(
                f"{FORM_VALIDATION_SELECTOR},{PAGE_ERROR_SELECTOR}"
            ).first.wait_for(state="visible", timeout=deadline_ms)
        except Exception:
            return ""
        return self._visible_error_text(scope, field)

    def _visible_error_text(self, scope, field: DiscoveredCommonField) -> str:
        if hasattr(scope, "is_visible"):
            try:
                if not scope.is_visible():
                    return self._visible_page_error_text()
            except Exception:
                return self._visible_page_error_text()
        try:
            current_dom = next(
                (
                    item
                    for item in self._scan_fields(scope)
                    if self._same_runtime_field(item, field)
                ),
                None,
            )
        except Exception:
            return self._visible_page_error_text()
        if current_dom and current_dom.selector:
            control = scope.locator(current_dom.selector).first
            item = control.locator(
                "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-form-item ')][1]"
            )
            if item.count():
                errors = item.locator(FORM_VALIDATION_SELECTOR)
                if errors.count():
                    return "; ".join(text.strip() for text in errors.all_inner_texts() if text.strip())
        scoped_errors = scope.locator(FORM_VALIDATION_SELECTOR)
        texts = [text.strip() for text in scoped_errors.all_inner_texts() if text.strip()]
        page_error = self._visible_page_error_text()
        if page_error:
            texts.extend(page_error.split("; "))
        return "; ".join(dict.fromkeys(texts))

    def _visible_page_error_text(self) -> str:
        try:
            global_errors = self.page.locator(PAGE_ERROR_SELECTOR)
            texts = [
                text.strip()
                for text in global_errors.all_inner_texts()
                if text.strip()
            ]
        except Exception:
            return ""
        return "; ".join(dict.fromkeys(texts))

    def _visible_command_error_text(self, scope) -> str:
        texts = []
        try:
            errors = scope.locator(FORM_VALIDATION_SELECTOR)
            if errors.count():
                texts.extend(errors.all_inner_texts())
        except Exception:
            pass
        try:
            messages = self.page.locator(PAGE_ERROR_SELECTOR)
            if messages.count():
                texts.extend(messages.all_inner_texts())
        except Exception:
            pass
        return "; ".join(dict.fromkeys(text.strip() for text in texts if text.strip()))

    def _scan_fields(self, scope=None):
        """Apply the same runtime/source identity mapping as the form driver."""
        if scope is None:
            session = getattr(self, "_form_session", None)
            active = getattr(session, "active", None)
            if active is not None:
                scope = active.scope
        fields = scan_dom_fields(self.page, scope)
        if not fields and scope is not None:
            recovered_scope = self._recover_replaced_active_form_scope(scope)
            if recovered_scope is not None:
                fields = scan_dom_fields(self.page, recovered_scope)
        mapped = []
        for index, dom in enumerate(fields, 1):
            field_code, field_label, *_ = self.driver._runtime_identity_for_dom(
                dom, index
            )
            mapped.append(replace(dom, field_code=field_code, label=field_label))
        return mapped

    def _recover_replaced_active_form_scope(self, scope):
        """Rebind only when a live form uniquely replaces an empty pinned scope."""
        session = getattr(self, "_form_session", None)
        active = getattr(session, "active", None)
        if active is None or active.scope is not scope:
            return None
        candidates = []
        try:
            for selector in (DIALOG,):
                containers = self.page.locator(selector)
                for index in range(containers.count()):
                    candidate = containers.nth(index)
                    controls = candidate.locator(EDITABLE_FORM_CONTROL)
                    if (
                        candidate.is_visible()
                        and controls.count()
                        and controls.first.is_visible()
                    ):
                        candidates.append(candidate)
        except Exception:
            return None
        if not candidates:
            try:
                containers = self.page.locator(".el-form:visible,form:visible")
                for index in range(containers.count()):
                    candidate = containers.nth(index)
                    controls = candidate.locator(EDITABLE_FORM_CONTROL)
                    if (
                        candidate.is_visible()
                        and controls.count()
                        and controls.first.is_visible()
                    ):
                        candidates.append(candidate)
            except Exception:
                return None
        unique_candidates = []
        seen = set()
        for candidate in candidates:
            try:
                handle = candidate.element_handle()
                identity = id(handle) if handle is not None else id(candidate)
            except Exception:
                identity = id(candidate)
            if identity not in seen:
                seen.add(identity)
                unique_candidates.append(candidate)
        if len(unique_candidates) != 1:
            return None
        rebound = self._pin_form_scope(unique_candidates[0])
        try:
            handle = rebound.element_handle()
        except Exception:
            handle = None
        session.active = _ActiveCommonFieldForm(
            scope=rebound,
            handle=handle,
            url=str(getattr(self.page, "url", "")),
        )
        self._set_driver_form_scope(rebound)
        print("COMMON_FORM_SESSION context_rebound=framework_rerender", flush=True)
        return rebound

    def _source_identity_safe_for_dom(
        self, dom, source_code: str, source_label: str
    ) -> bool:
        return self.driver._source_identity_safe_for_dom(
            dom, source_code, source_label
        )

    def _wait_for_fields_stable(
        self,
        scope,
        *,
        timeout_ms: int | None = None,
        stable_ms: int | None = None,
    ):
        """Wait for late-rendered dynamic controls before freezing a field snapshot."""
        timeout_ms = timeout_ms or int(
            os.getenv("EI_COMMON_FIELD_STABLE_TIMEOUT_MS", "10000")
        )
        stable_ms = stable_ms or int(
            os.getenv("EI_COMMON_FIELD_STABLE_MS", "1500")
        )
        deadline = time.monotonic() + timeout_ms / 1000
        stable_since = None
        last_signature = None
        latest = []
        while time.monotonic() < deadline:
            current = self._scan_fields(scope)
            signature = tuple(
                sorted(
                    (
                        field.field_code,
                        field.label,
                        field.kind,
                        field.required,
                        field.readonly,
                    )
                    for field in current
                )
            )
            now = time.monotonic()
            if current and signature == last_signature:
                if stable_since is not None and now - stable_since >= stable_ms / 1000:
                    return current
            else:
                latest = current
                last_signature = signature
                stable_since = now if current else None
            self.page.wait_for_timeout(100)
        observed = len(latest)
        raise AssertionError(
            f"新增表单字段集合在 {timeout_ms / 1000:g} 秒内未稳定；"
            f"最后发现 {observed} 个控件"
        )
