from __future__ import annotations

import os
import json
import re
import time
import uuid
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .dom import is_semantic_numeric_field, scan_dom_fields
from .dynamic_collections import DynamicCollectionSpec
from .failure_evidence import capture_failure_evidence
from .interactions import FieldInteractor
from .models import DomField, FieldDefinition, ResolvedField
from .verification import BUSINESS_ID_KEYS, extract_business_id


ADD_BUTTON = (
    "button:visible:enabled:not([aria-disabled='true']):not(.is-disabled):has-text('新增'),"
    "button:visible:enabled:not([aria-disabled='true']):not(.is-disabled):has-text('添加'),"
    "button:visible:enabled:not([aria-disabled='true']):not(.is-disabled):has-text('新建'),"
    "button:visible:enabled:not([aria-disabled='true']):not(.is-disabled):has-text('创建')"
)
SAVE_BUTTON = "[role=dialog] button:has-text('保存'),[role=dialog] button:has-text('确定'),.el-dialog button:has-text('保存'),.el-dialog button:has-text('确定')"
DIALOG = '[role="dialog"]:visible,.el-dialog:visible,.el-drawer:visible'
INLINE_FORM = (
    ".detail-panel:visible form:visible,.detail-panel:visible .el-form:visible,"
    ".base-info-page:visible form:visible,.base-info-page:visible .el-form:visible"
)
EDITABLE_FORM_CONTROL = (
    "input:not([type='hidden']):not([disabled]):visible,"
    "textarea:not([disabled]):visible,select:not([disabled]):visible,"
    "[role='combobox']:not([aria-disabled='true']):visible,"
    "[role='radio']:visible,[role='checkbox']:visible"
)
NESTED_ADD_ACTIONS = ("新增", "添加", "新建", "创建")
NESTED_DESTRUCTIVE_ACTIONS = ("删除", "移除", "清空")
IMPLICIT_REQUIRED_NESTED_SECTION_RE = re.compile(
    r"(?:(?:预算|资金来源|费用|金额|款项).{0,12}明细|明细.{0,12}(?:预算|资金来源|费用|金额|款项))"
)
AUTOMATION_RECORD_PREFIXES = ("AUTO_", "UI自动化")
ARBITRARY_DELETE_ROW_MARKER = "__arbitrary_delete_row__"

DYNAMIC_COLLECTION_CONTRACT_SCRIPT = r"""
root => [...root.querySelectorAll('[data-ei-collection-field]')].map((node) => ({
  fieldCode: (node.getAttribute('data-ei-collection-field') || '').trim(),
  path: (node.getAttribute('data-ei-collection-path') || '').trim(),
  mode: (node.getAttribute('data-ei-collection-mode') || '').trim(),
  minRows: Number(node.getAttribute('data-ei-collection-min-rows') || 1),
  childFields: (node.getAttribute('data-ei-collection-child-fields') || '')
    .split(',').map((value) => value.trim()).filter(Boolean),
}));
"""

TYPE_BY_KIND = {
    "text": "TEXT", "textarea": "TEXTAREA", "number": "NUMBER",
    "date": "DATE", "year": "DATE", "select": "SELECT", "multi_select": "MULTI_SELECT",
    "radio": "RADIO", "checkbox": "CHECKBOX", "file": "FILE",
}

DETAIL_DISPLAY_ALIASES = {
    "projClassify": ("projClassifyName",),
    "belongSection": ("belongSectionName",),
    "inveId": ("inveName",),
    "investorType": ("investorTypeName",),
    "company": ("companyName",),
    "registrationStatus": ("registrationStatusName",),
    "orgId": ("orgName",),
    "investmentAttributes": ("investmentAttributesName",),
    "platform": ("platformName",),
    "relationId": ("relationName",),
    "parentId": ("parentName",),
    "projName": ("shortName",),
    "progressType": ("progressTypeName",),
    "createBy": ("createByName",),
    "financeSources.*.sourceFrom": ("financeSources.*.sourceFromName",),
}


@dataclass(slots=True)
class ModuleSmokeResult:
    mode: str
    business_id: str = ""
    save_url: str = ""
    detail_url: str = ""
    submitted: dict[str, Any] | None = None
    record_markers: tuple[str, ...] = ()
    record_identity_payload: Any = None


class RecordNotDeletableError(AssertionError):
    """The automation-owned record exists but its delete action is disabled."""


class DynamicFieldContractError(AssertionError):
    """A rendered dynamic collection cannot be safely created or filled."""


class _RequestContextDetailResponse:
    """Expose an APIRequestContext response through the captured-response contract."""

    def __init__(self, response, request_url: str, payload: Any, headers: dict[str, Any]):
        self._response = response
        self._payload = payload
        self.ok = bool(getattr(response, "ok", False))
        self.status = getattr(response, "status", 0)
        self.url = str(getattr(response, "url", "") or request_url)
        self.headers = headers
        self.request = type(
            "AuthenticatedDetailRequest",
            (),
            {
                "method": "GET",
                "resource_type": "fetch",
                "url": request_url,
                "post_data_json": None,
                "post_data": "",
            },
        )()

    def json(self) -> Any:
        return self._payload

    def text(self) -> str:
        text = getattr(self._response, "text", None)
        return str(text() if callable(text) else text or "")


class _UniqueListReplayResponse:
    """Expose a browser-context fetch result through the response contract."""

    def __init__(self, result: dict[str, Any], request_url: str):
        self.ok = bool(result.get("ok"))
        self.status = int(result.get("status") or 0)
        self.url = str(result.get("url") or request_url)
        self._body = result.get("body")

    def json(self) -> Any:
        if isinstance(self._body, (dict, list)):
            return self._body
        raise ValueError("response body is not JSON")


@dataclass(slots=True)
class FieldCompletionReport:
    not_located: list[str]
    not_filled: list[str]
    fill_failed: list[str]
    optional_not_filled: list[str] = dataclass_field(default_factory=list)
    optional_fill_failed: list[str] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.not_located or self.not_filled or self.fill_failed)

    def message(self) -> str:
        parts = []
        if self.not_located:
            parts.append("未定位到字段: " + ", ".join(self.not_located))
        if self.not_filled:
            parts.append("字段未成功输入: " + ", ".join(self.not_filled))
        if self.fill_failed:
            parts.append("字段填写异常: " + "; ".join(self.fill_failed))
        if self.optional_not_filled:
            parts.append("可选字段未成功输入: " + ", ".join(self.optional_not_filled))
        if self.optional_fill_failed:
            parts.append("可选字段填写异常: " + "; ".join(self.optional_fill_failed))
        return "；".join(parts) if parts else "全部字段均已定位并成功输入"


@dataclass(slots=True)
class AttachmentCompletionReport:
    status: str = "not_configured"
    uploaded: int = 0
    existing: int = 0
    pending: int = 0
    requests_observed: int = 0
    errors: list[str] = dataclass_field(default_factory=list)
    classification: str = ""
    lifecycle: list[dict[str, Any]] = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.status not in {"failed", "pending"}


@dataclass(slots=True)
class AttachmentLifecycleTracker:
    requests: list[Any] = dataclass_field(default_factory=list)
    pending: dict[int, Any] = dataclass_field(default_factory=dict)
    responses: list[Any] = dataclass_field(default_factory=list)
    validated_responses: set[int] = dataclass_field(default_factory=set)
    failures: list[str] = dataclass_field(default_factory=list)
    events: list[dict[str, Any]] = dataclass_field(default_factory=list)
    backend_pending: list[dict[str, Any]] = dataclass_field(default_factory=list)
    final_classification: str = ""
    callbacks: dict[str, Callable] = dataclass_field(default_factory=dict)
    last_activity: float = dataclass_field(default_factory=time.monotonic)
    enabled: bool = False


class ModuleSmokeDriver:
    # The same successful CRUD action can be exposed as add or update depending
    # on whether the UI is a standalone edit page or an add dialog.
    BUSINESS_MUTATION_URL_TOKENS = (
        "/add", "/save", "/create", "/insert", "/submit", "/commit",
        "/update", "/edit", "/modify",
    )
    OPTIONAL_RELATIONSHIP_FIELDS = {"parentid", "relationid"}
    RECORD_IDENTITY_CODES = {
        "regname", "fundname", "name", "title", "projname", "projectname",
        "mcname", "entname", "enterprisename", "companyname", "investorname",
        "projobjectname", "shortname", "itemname", "mattername",
    }
    RECORD_IDENTITY_LABEL_SUFFIXES = ("名称", "全称", "简称", "标题")
    RECORD_IDENTITY_PROMPT_PREFIXES = ("请输入", "请填写", "请录入", "请选择", "请上传")
    SEMANTIC_LABEL_CODES = {
        "项目名称": "projName",
        "企业全称": "projObjectName",
        "投资人名称": "investorName",
        "基金名称": "fundName",
        "事项名称": "matterName",
    }

    def __init__(
        self,
        page,
        data_strategy,
        source_fields: list[tuple[str, str, bool]] | None = None,
        default_upload_file=None,
        dynamic_collections: list[DynamicCollectionSpec] | None = None,
        automation_record_registry: Path | None = None,
    ):
        self.page = page
        self.data_strategy = data_strategy
        self.interactor = FieldInteractor(page)
        self.source_fields = source_fields or []
        self.default_upload_file = default_upload_file
        self.dynamic_collections = dynamic_collections or []
        self.last_field_report = FieldCompletionReport([], [], [])
        self.last_attachment_report = AttachmentCompletionReport()
        self._fill_failures: list[str] = []
        self._optional_fill_failures: list[str] = []
        self._validation_repairs: list[dict[str, Any]] = []
        self._nested_evidence: list[dict[str, str]] = []
        self._submitted_display_values: dict[str, tuple[Any, str]] = {}
        self._collection_submission_codes: set[str] = set()
        self._unique_list_responses: list[Any] = []
        self._unique_list_listener = None
        self._unique_occupied_snapshot: dict[tuple[Any, ...], list[Any]] = {}
        self._pending_unique_reservations: list[tuple[Any, tuple[Any, ...], str]] = []
        self.automation_record_registry = automation_record_registry

    def prepare_unique_constraint_evidence(self) -> None:
        """Capture a complete, authenticated occupied-key snapshot before Add."""
        specs = self._declared_unique_constraints()
        if not any(spec.list_url_includes for spec in specs):
            return
        self._stop_unique_list_capture()
        self._unique_list_responses = []
        self._unique_occupied_snapshot = {}
        self._start_unique_list_capture()
        reload_page = getattr(self.page, "reload", None)
        if not callable(reload_page):
            self._stop_unique_list_capture()
            raise AssertionError("组合唯一约束缺少列表证据：当前页面不能刷新列表")
        try:
            try:
                reload_page(wait_until="domcontentloaded")
            except TypeError:
                reload_page()
            self._wait_for_unique_list_response(specs)
        finally:
            self._stop_unique_list_capture()
        for spec in specs:
            if not spec.list_url_includes:
                continue
            matching = [
                response for response in self._unique_list_responses
                if self._response_matches_unique_list(response, spec)
            ]
            if not matching:
                raise AssertionError(
                    f"组合唯一约束没有捕获本轮列表响应：{spec.form_code}"
                )
            self._unique_occupied_snapshot[
                self._unique_spec_key(spec)
            ] = self._occupied_unique_keys_from_response(matching[-1], spec)

    def bind_page(self, page) -> None:
        """Move the driver to a recovered Page without retaining stale evidence."""
        old_page = getattr(self, "page", None)
        if page is old_page:
            return
        self.release_pending_unique_reservations()
        self._stop_unique_list_capture(page=old_page)
        self._unique_list_responses = []
        self._unique_occupied_snapshot = {}
        self.page = page
        interactor = getattr(self, "interactor", None)
        if interactor is not None:
            interactor.page = page
        self._common_form_scope = None

    def run(
        self, *, provision_only: bool = False, submit: bool = False,
    ) -> ModuleSmokeResult:
        try:
            result = self._run_create(
                provision_only=provision_only, submit=submit,
            )
        except Exception:
            self.release_pending_unique_reservations()
            raise
        self.commit_pending_unique_reservations()
        return result

    def _run_create(
        self, *, provision_only: bool = False, submit: bool = False,
    ) -> ModuleSmokeResult:
        # Detail actions such as Edit do not themselves require an Add button,
        # but their isolated parent-data provisioner always does.  Do not let
        # the action's EI_REQUIRE_ADD setting turn that provisioner into a
        # page-access no-op.
        if (
            not provision_only
            and os.getenv("EI_REQUIRE_ADD", "false").lower() != "true"
        ):
            return ModuleSmokeResult(mode="page_access")
        self._reset_nested_evidence()
        self._submitted_display_values = {}
        self._collection_submission_codes = set()
        automation_registry_scope = self._automation_registry_scope()
        self.prepare_unique_constraint_evidence()
        add = self._wait_for_add_button()
        add.click()
        scope = self._wait_for_form_scope()
        self._wait_for_form_ready(scope)
        self._form_scope_for_collections = scope
        submitted: dict[str, Any] = {}
        attempts = max(1, int(os.getenv("EI_FIELD_FILL_ATTEMPTS", "2")))
        all_failures: list[str] = []
        retry_codes: set[str] | None = None
        for attempt in range(1, attempts + 1):
            submitted.update(self._fill_dialog(only_codes=retry_codes))
            all_failures = list(self._fill_failures)
            attachment_error = ""
            try:
                self._upload_default_attachments(scope)
            except AssertionError as exc:
                attachment_error = str(exc)
                all_failures.append("附件: " + attachment_error)
            self.last_field_report = self.check_field_completion(submitted, all_failures)
            if self._field_report_ok(self.last_field_report) and not attachment_error:
                break
            if attempt < attempts:
                retry_codes = self._retry_field_codes(submitted)
                if not retry_codes and not attachment_error:
                    break
                self.page.wait_for_timeout(1_000)
        submitted.update(self._prepare_nested_operation(scope))
        submitted.update(self._prepare_implicit_required_nested_baselines(scope))
        self._assert_configured_dynamic_collection_controls(scope)
        self.last_field_report = self.check_field_completion(submitted, all_failures)
        repaired_values = self._repair_visible_validation_errors(scope, submitted)
        submitted.update(repaired_values)
        if repaired_values:
            self.last_field_report = self.check_field_completion(submitted, all_failures)
        submitted.update(self._prepare_declared_unique_values(scope, submitted))
        record_markers = self._collect_record_identity_markers(submitted, scope=scope)
        self._capture_submitted_display_values(submitted, scope)
        self.write_field_diagnostics(self.last_field_report, submitted, attempts)
        if not self._field_report_ok(self.last_field_report):
            raise AssertionError("保存前字段检查失败：" + self.last_field_report.message())
        save = self._save_button(scope, operation="提交" if submit else "")
        if not save.count() or not save.is_visible():
            expected = "提交" if submit else "保存/确定"
            raise AssertionError(f"新增表单已打开，但没有找到{expected}按钮")
        responses = []
        self.page.on("response", lambda response: responses.append(response))
        self._save_with_validation_repairs(scope, save, responses, submitted)
        self.page.wait_for_timeout(1000)
        save_response = self._find_save_response(responses, submitted)
        if save_response is None:
            raise AssertionError("保存后没有捕获到新增/保存接口响应")
        self._assert_successful_save_response(save_response)
        self.commit_pending_unique_reservations()
        return self.verify_saved_record(
            responses,
            save_response,
            submitted,
            record_markers,
            provision_only=provision_only,
            created_by_automation=True,
            automation_registry_scope=automation_registry_scope,
        )

    def _assert_successful_save_response(self, response) -> None:
        if not bool(getattr(response, "ok", False)):
            raise AssertionError(
                f"保存接口失败：HTTP {getattr(response, 'status', 0)} "
                f"{getattr(response, 'url', '')}; "
                f"{self._failed_save_response_detail(response)}"
            )
        try:
            body = response.json()
        except ValueError:
            return
        except Exception:
            body = response.text()
        self._assert_business_success(body)

    def verify_saved_record(
        self,
        responses,
        save_response,
        submitted: dict[str, Any],
        record_markers: tuple[str, ...],
        *,
        provision_only: bool = False,
        required_codes: set[str] | None = None,
        rendered_text_expectations: dict[str, tuple[str, str]] | None = None,
        require_edit_and_detail: bool = False,
        saved_from_current_detail_edit: bool = False,
        created_by_automation: bool = False,
        automation_registry_scope: str = "",
    ) -> ModuleSmokeResult:
        """Complete the shared save, identity, and persisted-value verification."""
        requested_codes = (
            self._default_readback_required_codes(submitted)
            if required_codes is None
            else set(required_codes)
        )
        explicit_empty_readback = required_codes is not None and not requested_codes
        if required_codes is None:
            required_codes = requested_codes
        else:
            required_codes = requested_codes
        required_codes = self._stable_readback_required_codes(
            submitted, required_codes
        )
        filtered_all_requested_codes = bool(requested_codes) and not required_codes
        if (
            (require_edit_and_detail or saved_from_current_detail_edit)
            and filtered_all_requested_codes
        ):
            raise AssertionError(
                "详情与编辑双回读要求的字段全部是运行时生成 ID，"
                "没有稳定业务字段可核对"
            )
        if not save_response.ok:
            raise AssertionError(
                f"保存接口失败：HTTP {save_response.status} {save_response.url}; "
                f"{self._failed_save_response_detail(save_response)}"
            )
        try:
            body = save_response.json()
        except Exception:
            body = save_response.text()
        self._assert_business_success(body)
        business_id = extract_business_id(body)
        record_identity_payload = self._saved_record_identity_payload(
            responses, body, business_id
        )
        if created_by_automation and business_id:
            self._remember_automation_owned_record(
                ModuleSmokeResult(
                    mode="automation_create_succeeded",
                    business_id=business_id,
                    save_url=save_response.url,
                    submitted=submitted,
                    record_markers=record_markers,
                    record_identity_payload=record_identity_payload,
                ),
                page_scope=automation_registry_scope,
            )
        save_payload = self._request_payload(save_response.request)
        self._assert_nested_values_in_payload(
            save_payload,
            submitted=submitted,
            stage=f"保存请求 {save_response.url}",
            synchronize_support=True,
        )
        echo_values = list(record_markers)
        display_identity_values = (
            self._delete_display_identity_values(submitted, [])
            if business_id and not echo_values
            else []
        )
        # An edit starts from an already-associated detail record.  Its update
        # response may deliberately return no record data, so use the detail
        # and reopened-edit readback below as the identity proof instead.
        if not business_id and not echo_values and not saved_from_current_detail_edit:
            raise AssertionError(f"保存接口未返回业务主键，且没有可用于定位记录的名称字段：{body!r}")
        if (
            not business_id and echo_values
            and not any(self.page.get_by_text(value, exact=False).count() for value in echo_values)
        ):
            raise AssertionError(f"保存成功但列表未回显本次数据：{echo_values}")
        if provision_only:
            return ModuleSmokeResult(
                mode="add_provisioned",
                business_id=business_id,
                save_url=save_response.url,
                submitted=submitted,
                record_markers=record_markers,
                record_identity_payload=record_identity_payload,
            )
        if require_edit_and_detail or saved_from_current_detail_edit:
            api_verified = self._verify_saved_record_by_business_id_detail(
                save_response,
                submitted,
                business_id=business_id,
                required_codes=(
                    set(required_codes)
                    if required_codes is not None
                    else set(submitted)
                ),
                record_markers=record_markers,
                save_payload=save_payload,
            )
            if api_verified is not None:
                return api_verified
            return self._verify_saved_record_in_edit_and_detail(
                responses,
                save_response,
                submitted,
                record_markers,
                business_id,
                required_codes=(
                    set(required_codes)
                    if required_codes is not None
                    else set(submitted)
                ),
            )
        # A successful save with an exact business ID is verified through the
        # same resource's detail API before inspecting the rendered list.  A
        # table can omit the ID, truncate values, or contain duplicate text;
        # none of those conditions should invalidate an ID-addressed JSON
        # readback.
        detail_response = None
        detail_body = None
        associated_detail_body = None
        if business_id:
            requested_detail = self._request_same_resource_detail_response(
                save_response, business_id
            )
            if requested_detail is not None:
                detail_response = requested_detail
                detail_body = self._detail_response_readback_or_fallback(
                    requested_detail,
                    submitted,
                    required_codes=required_codes,
                    business_id=business_id,
                    record_markers=record_markers,
                    save_payload=save_payload,
                )
                associated_detail_body = requested_detail.json()
                if detail_body is not None and not rendered_text_expectations:
                    return ModuleSmokeResult(
                        mode="add_and_detail_verified",
                        business_id=business_id,
                        save_url=save_response.url,
                        detail_url=requested_detail.url,
                        submitted=submitted,
                        record_markers=record_markers,
                        record_identity_payload=associated_detail_body,
                    )
                if filtered_all_requested_codes and not explicit_empty_readback:
                    return ModuleSmokeResult(
                        mode="add_and_detail_verified",
                        business_id=business_id,
                        save_url=save_response.url,
                        detail_url=requested_detail.url,
                        submitted=submitted,
                        record_markers=record_markers,
                        record_identity_payload=associated_detail_body,
                    )
        if detail_body is None and self._try_current_page_list_readback(
            submitted,
            record_markers,
            business_id,
            required_codes=required_codes,
            rendered_text_expectations=rendered_text_expectations,
        ):
            return ModuleSmokeResult(
                mode="add_and_list_verified",
                business_id=business_id,
                save_url=save_response.url,
                detail_url=self.page.url,
                submitted=submitted,
                record_markers=record_markers,
                record_identity_payload=record_identity_payload,
            )
        if detail_body is None:
            detail_response = self._find_associated_detail_response(
                responses, save_response, business_id
            )
        if detail_response is not None and detail_body is None:
            detail_body = self._detail_response_readback_or_fallback(
                detail_response,
                submitted,
                required_codes=required_codes,
                business_id=business_id,
                record_markers=record_markers,
                save_payload=save_payload,
            )
            if detail_body is not None:
                associated_detail_body = detail_body
            else:
                # A child list response can identify the saved record while
                # omitting fields that must still be checked in detail/edit UI.
                # Retain that identity evidence for exact row association.
                associated_detail_body = detail_response.json()
            if detail_body is not None and not rendered_text_expectations:
                return ModuleSmokeResult(
                    mode="add_and_detail_verified",
                    business_id=business_id,
                    save_url=save_response.url,
                    detail_url=detail_response.url,
                    submitted=submitted,
                    record_markers=record_markers,
                    record_identity_payload=associated_detail_body,
                )
        if filtered_all_requested_codes and not explicit_empty_readback:
            raise AssertionError(
                "原回读字段全部是运行时生成 ID，且没有取得按本次业务 ID "
                "请求的同资源详情响应，无法证明保存结果"
            )
        if detail_body is None:
            if associated_detail_body is None:
                self._open_detail(
                    echo_values,
                    business_id,
                    **(
                        {"display_identity_values": display_identity_values}
                        if display_identity_values else {}
                    ),
                )
            else:
                self._open_detail(
                    echo_values,
                    business_id,
                    response_payload=associated_detail_body,
                    **(
                        {"display_identity_values": display_identity_values}
                        if display_identity_values else {}
                    ),
                )
        else:
            self._open_detail(
                echo_values,
                business_id,
                response_payload=detail_body,
                **(
                    {"display_identity_values": display_identity_values}
                    if display_identity_values else {}
                ),
            )
        if detail_body is None:
            detail_response = self._find_detail_response(
                responses, save_response, business_id
            )
            if detail_response is not None:
                detail_body = self._detail_response_readback_or_fallback(
                    detail_response,
                    submitted,
                    required_codes=required_codes,
                    business_id=business_id,
                    record_markers=record_markers,
                    save_payload=save_payload,
                )
        if detail_body is None:
            self._open_current_detail_edit_for_readback(echo_values)
            display_values = {
                code: display_value
                for code in submitted
                if (
                    display_value := self._remembered_display_value(
                        code, submitted[code]
                    )
                )
            }
            readback_options: dict[str, Any] = {"required_codes": required_codes}
            if display_values:
                readback_options["display_values"] = display_values
            self._assert_open_form_values(submitted, **readback_options)
            self._assert_nested_values_in_open_form()
            self._assert_rendered_detail_text(rendered_text_expectations)
            return ModuleSmokeResult(
                mode="add_and_edit_form_verified",
                business_id=business_id,
                save_url=save_response.url,
                submitted=submitted,
                record_markers=record_markers,
                record_identity_payload=(
                    associated_detail_body or record_identity_payload
                ),
            )
        self._assert_rendered_detail_text(rendered_text_expectations)
        return ModuleSmokeResult(
            mode="add_and_detail_verified",
            business_id=business_id,
            save_url=save_response.url,
            detail_url=detail_response.url,
            submitted=submitted,
            record_markers=record_markers,
            record_identity_payload=(
                detail_body or associated_detail_body or record_identity_payload
            ),
        )

    def _verify_saved_record_by_business_id_detail(
        self,
        save_response,
        submitted: dict[str, Any],
        *,
        business_id: str,
        required_codes: set[str],
        record_markers: tuple[str, ...],
        save_payload: Any,
    ) -> ModuleSmokeResult | None:
        """Return exact-ID JSON evidence before any rendered-record fallback."""
        if not business_id:
            return None
        detail_response = self._request_same_resource_detail_response(
            save_response, business_id
        )
        if detail_response is None:
            return None
        detail_body = self._detail_response_readback_or_fallback(
            detail_response,
            submitted,
            required_codes=required_codes,
            business_id=business_id,
            record_markers=record_markers,
            save_payload=save_payload,
        )
        if detail_body is None:
            return None
        return ModuleSmokeResult(
            mode="add_and_detail_verified",
            business_id=business_id,
            save_url=save_response.url,
            detail_url=detail_response.url,
            submitted=submitted,
            record_markers=record_markers,
            record_identity_payload=detail_response.json(),
        )

    def _default_readback_required_codes(self, submitted: dict[str, Any]) -> set[str]:
        """Use collection child paths for readback, not the collection's UI wrapper code."""
        return set(submitted) - set(
            getattr(self, "_collection_submission_codes", set())
        )

    def _open_current_detail_edit_for_readback(self, markers: list[str]) -> bool:
        """Enter edit mode when ordinary readback currently sits on readonly detail."""
        try:
            edit = self._current_detail_edit_button(markers)
        except (AttributeError, KeyError, TypeError):
            return False
        if edit is None:
            return False
        edit.click()
        wait_for_timeout = getattr(self.page, "wait_for_timeout", None)
        if callable(wait_for_timeout):
            wait_for_timeout(1_500)
        return True

    def _assert_detail_response_readback(
        self,
        detail_response,
        submitted: dict[str, Any],
        *,
        required_codes: set[str] | None,
        business_id: str,
        record_markers: tuple[str, ...],
        save_payload: Any,
    ) -> Any:
        """Validate one associated JSON response as persisted-record evidence."""
        try:
            detail_body = detail_response.json()
        except Exception as exc:
            raise AssertionError(
                f"详情接口响应不是有效 JSON：{detail_response.url}"
            ) from exc
        self._assert_business_success(detail_body, operation="详情")
        self._assert_detail_values(
            detail_body,
            submitted,
            required_codes=required_codes,
            business_id=business_id,
            record_markers=record_markers,
            detail_request=detail_response.request,
            submitted_payload=save_payload,
        )
        self._assert_nested_values_in_payload(
            detail_body,
            stage="详情响应",
            submitted=submitted,
            required_codes=required_codes,
        )
        return detail_body

    def _verify_saved_record_in_edit_and_detail(
        self,
        responses,
        save_response,
        submitted: dict[str, Any],
        record_markers: tuple[str, ...],
        business_id: str,
        *,
        required_codes: set[str],
    ) -> ModuleSmokeResult:
        """Require every submitted field to match in both detail and edit views."""
        list_url = self.page.url
        echo_values = list(record_markers)
        readback_required_codes = self._stable_readback_required_codes(
            submitted, required_codes
        )
        record_identity_payload = self._saved_record_identity_payload(
            responses, save_response.json(), business_id
        )
        save_payload = self._request_payload(save_response.request)
        detail_response = self._find_associated_detail_response(
            responses, save_response, business_id
        )
        detail_body = None
        associated_detail_body = None
        if detail_response is not None:
            detail_body = self._detail_response_readback_or_fallback(
                detail_response,
                submitted,
                required_codes=readback_required_codes,
                business_id=business_id,
                record_markers=record_markers,
                save_payload=save_payload,
            )
            if detail_body is not None:
                associated_detail_body = detail_body
            else:
                # Keep an incomplete but ID-associated list payload available
                # to locate the exact embedded child row before UI readback.
                associated_detail_body = detail_response.json()
        direct_edit_opened = False
        verified_detail_row_marker = ""
        requested_detail = None
        detail_edit = self._current_detail_edit_button(echo_values)
        has_record_identity = bool(business_id or echo_values)
        if (
            detail_edit is None
            and has_record_identity
            and self._url_is_detail_page(self.page.url)
        ):
            deadline = time.monotonic() + 15
            while (
                detail_edit is None
                and not self._detail_route_has_current_record(business_id, echo_values)
                and time.monotonic() < deadline
            ):
                self.page.wait_for_timeout(200)
                detail_edit = self._current_detail_edit_button(echo_values)
        if detail_edit is None:
            if not has_record_identity:
                raise AssertionError(
                    "编辑保存接口未返回业务主键，且没有记录标识；"
                    "当前页面也没有可验证的详情级编辑入口，无法证明回读的是同一条记录"
                )
            try:
                self._open_record_action(
                    echo_values,
                    business_id,
                    action_names=("查看", "详情"),
                    allow_row_click=True,
                )
            except AssertionError as exc:
                association_payload = (
                    detail_body
                    if detail_body is not None
                    else associated_detail_body
                )
                if association_payload is None and business_id:
                    requested_detail = self._request_same_resource_detail_response(
                        save_response, business_id
                    )
                    if requested_detail is not None:
                        detail_response = requested_detail
                        detail_body = self._detail_response_readback_or_fallback(
                            requested_detail,
                            submitted,
                            required_codes=readback_required_codes,
                            business_id=business_id,
                            record_markers=record_markers,
                            save_payload=save_payload,
                        )
                        association_payload = (
                            detail_body
                            if detail_body is not None
                            else requested_detail.json()
                        )
                if association_payload is not None:
                    try:
                        container, identity = self._find_response_associated_record_container(
                            association_payload,
                            business_id,
                            display_field_codes=self._source_response_association_codes(),
                        )
                    except AssertionError as association_exc:
                        probe = self._probe_response_associated_detail_row(
                            association_payload,
                            business_id,
                            display_field_codes=self._source_response_association_codes(),
                        )
                        if probe is None:
                            if requested_detail is not None:
                                raise AssertionError(
                                    "同资源详情接口已按业务 ID 返回记录，"
                                    "但当前页面仍无法唯一定位对应记录，不能跳过 UI 双回读"
                                ) from association_exc
                            raise
                        identity, verified_detail_row_marker, detail_response = probe
                        detail_body = self._detail_response_readback_or_fallback(
                            detail_response,
                            submitted,
                            required_codes=readback_required_codes,
                            business_id=business_id,
                            record_markers=record_markers,
                            save_payload=save_payload,
                        )
                        associated_detail_body = (
                            detail_body if detail_body is not None else detail_response.json()
                        )
                    else:
                        self._open_record_container_action(
                            container,
                            identity,
                            echo_values,
                            action_names=("编辑", "修改"),
                            allow_row_click=False,
                        )
                        direct_edit_opened = True
                elif self._url_is_detail_page(list_url):
                    raise AssertionError(
                        "保存后已经进入详情路由，但既没有可用页面级编辑按钮，"
                        "也无法从当前详情页内嵌列表按本次记录打开查看/详情入口"
                    ) from exc
                else:
                    raise
            if not direct_edit_opened:
                detail_edit = self._current_detail_edit_button(echo_values)
        if detail_response is None:
            detail_response = self._find_detail_response(
                responses, save_response, business_id
            )
        if detail_response is not None:
            if detail_body is None:
                detail_body = self._detail_response_readback_or_fallback(
                    detail_response,
                    submitted,
                    required_codes=readback_required_codes,
                    business_id=business_id,
                    record_markers=record_markers,
                    save_payload=save_payload,
                )
        detail_expectations = self._submitted_detail_expectations(
            submitted,
            readback_required_codes,
            detail_payload=detail_body,
        )
        if not direct_edit_opened:
            self._assert_rendered_detail_text(
                detail_expectations,
                all_fields=True,
            )
        detail_url = detail_response.url if detail_response is not None else self.page.url

        if direct_edit_opened:
            pass
        elif detail_edit is not None:
            detail_edit.click()
            self.page.wait_for_timeout(1_500)
        elif verified_detail_row_marker:
            self._return_to_record_list(list_url)
            container = self._runtime_marked_record_container(
                verified_detail_row_marker
            )
            self._open_record_container_action(
                container,
                "详情响应已验证的记录",
                echo_values,
                action_names=("编辑", "修改"),
                allow_row_click=False,
            )
        else:
            self._return_to_record_list(list_url)
            self._open_record_action(
                echo_values,
                business_id,
                action_names=("编辑",),
                allow_row_click=False,
            )
        self._assert_open_form_values(
            submitted,
            required_codes=readback_required_codes,
            display_values={
                code: expected for code, (_label, expected) in detail_expectations.items()
            },
        )
        self._assert_nested_values_in_open_form()
        return ModuleSmokeResult(
            mode="add_edit_and_detail_verified",
            business_id=business_id,
            save_url=save_response.url,
            detail_url=detail_url,
            submitted=submitted,
            record_markers=record_markers,
            record_identity_payload=(
                detail_body or associated_detail_body or record_identity_payload
            ),
        )

    def _detail_response_readback_or_fallback(
        self,
        detail_response,
        submitted: dict[str, Any],
        *,
        required_codes: set[str] | None,
        business_id: str,
        record_markers: tuple[str, ...],
        save_payload: Any,
    ) -> Any | None:
        """Use complete target evidence, otherwise continue rendered UI readback."""
        try:
            detail_body = self._assert_detail_response_readback(
                detail_response,
                submitted,
                required_codes=required_codes,
                business_id=business_id,
                record_markers=record_markers,
                save_payload=save_payload,
            )
        except AssertionError as exc:
            message = str(exc)
            if not any(
                marker in message
                for marker in (
                    "详情接口未返回任何本次提交字段",
                    "详情响应没有持久化嵌套行字段",
                )
            ):
                raise
            return None
        missing = self._missing_detail_required_codes(
            detail_body,
            required_codes,
            business_id=business_id,
            record_markers=record_markers,
            detail_request=detail_response.request,
        )
        if missing:
            return None
        return detail_body

    @classmethod
    def _missing_detail_required_codes(
        cls,
        payload: Any,
        required_codes: set[str] | None,
        *,
        business_id: str,
        record_markers: tuple[str, ...],
        detail_request,
    ) -> set[str]:
        required = set(required_codes or ())
        if not required:
            return set()
        records = cls._strict_detail_record_dicts(
            payload,
            required,
            DETAIL_DISPLAY_ALIASES,
            business_id=business_id,
            record_markers=record_markers,
            detail_request=detail_request,
        )
        return {
            code
            for code in required
            if not any(
                cls._detail_field_values(record, code, DETAIL_DISPLAY_ALIASES)
                for record in records
            )
        }

    def _stable_readback_required_codes(
        self, submitted: dict[str, Any], required_codes: set[str],
    ) -> set[str]:
        """Return required codes that can be re-located after detail/edit rerender."""
        stable: set[str] = set()
        for code in required_codes:
            text = str(code or "")
            if text not in submitted:
                continue
            parts = re.split(r"[.。/\\]", text)
            has_generated_part = any(
                self._is_generated_identifier(part) for part in parts
            )
            if has_generated_part:
                continue
            stable.add(text)
        return stable

    def _submitted_detail_expectations(
        self,
        submitted: dict[str, Any],
        required_codes: set[str],
        *,
        detail_payload: Any = None,
    ) -> dict[str, tuple[str, str]]:
        labels = {
            str(field[0]): str(field[1])
            for field in getattr(self, "source_fields", [])
            if len(field) >= 2 and str(field[0]) and str(field[1])
        }
        missing = sorted(required_codes - submitted.keys())
        if missing:
            raise AssertionError(
                "指定回读字段未出现在本次提交值：" + ", ".join(missing)
            )

        def display(value: Any) -> str:
            if isinstance(value, (list, tuple, set)):
                return ",".join(str(item) for item in value)
            return str(value)

        detail_records = self._collect_dicts(detail_payload) if detail_payload is not None else []

        def rendered_value(code: str) -> str:
            for alias in DETAIL_DISPLAY_ALIASES.get(code, ()):
                for record in detail_records:
                    values = self._field_values_from_record(record, alias)
                    for value in values:
                        if value not in (None, ""):
                            return display(value)
            remembered = self._remembered_display_value(code, submitted[code])
            if remembered is not None:
                return remembered
            return display(submitted[code])

        return {
            code: (labels.get(code, code), rendered_value(code))
            for code in required_codes
        }

    def _capture_submitted_display_values(
        self, submitted: dict[str, Any], scope,
    ) -> dict[str, str]:
        """Bind a stored choice value to text rendered by that same form control."""
        relevant = {
            code for code in submitted
            if code in DETAIL_DISPLAY_ALIASES
        }
        if not relevant:
            return {}
        try:
            fields = scan_dom_fields(self.page, scope)
        except TypeError:
            fields = scan_dom_fields(self.page)
        except Exception:
            return {}

        remembered = getattr(self, "_submitted_display_values", None)
        if not isinstance(remembered, dict):
            remembered = {}
            self._submitted_display_values = remembered
        captured: dict[str, str] = {}
        for index, dom in enumerate(fields, start=1):
            if dom.kind not in {"select", "multi_select", "radio", "checkbox"}:
                continue
            source_code, _source_label, *_ = self._source_for_dom(dom, index)
            candidates = self._deduplicate([
                str(dom.field_code or ""),
                str(source_code or ""),
            ])
            code = next((item for item in candidates if item in relevant), "")
            if not code:
                continue
            actual_values = self._open_form_field_values(dom, scope)
            if not actual_values:
                continue
            stored_value = submitted[code]
            display_value = next(
                (
                    value for value in reversed(actual_values)
                    if not self._readback_values_match(
                        stored_value, [value], field_code=code
                    )
                ),
                actual_values[-1],
            )
            display_text = str(display_value or "").strip()
            if not display_text:
                continue
            remembered[code] = (stored_value, display_text)
            captured[code] = display_text
        return captured

    def _remembered_display_value(self, code: str, submitted_value: Any) -> str | None:
        remembered = getattr(self, "_submitted_display_values", {})
        entry = remembered.get(code) if isinstance(remembered, dict) else None
        if not isinstance(entry, tuple) or len(entry) != 2:
            return None
        stored_value, display_value = entry
        if not self._readback_values_match(
            stored_value, [submitted_value], field_code=code
        ):
            return None
        text = str(display_value or "").strip()
        return text or None

    def _assert_rendered_detail_text(
        self,
        expectations: dict[str, tuple[str, str]] | None,
        *,
        all_fields: bool = False,
    ) -> None:
        """Compare formatted text with what the detail page actually renders."""
        if not expectations:
            return
        items = [
            {"code": code, "label": self._readback_label(label), "expected": str(expected)}
            for code, (label, expected) in expectations.items()
        ]
        evidence_script = r"""
        expectations => {
          const visible = element => {
            if (!(element instanceof HTMLElement)) return false;
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && rect.width > 0 && rect.height > 0;
          };
          const normalizeLabel = value => String(value || '')
            .replace(/^\s*\*\s*/, '').replace(/[\uFF1A:]\s*$/, '')
            .replace(/\s+/g, '').trim()
            .replace(/^(请输入|请填写|请录入|请选择|请上传)/, '');
          const ownText = element => Array.from(element.childNodes)
            .filter(node => node.nodeType === Node.TEXT_NODE)
            .map(node => node.textContent || '').join('').trim();
          const rendered = element => {
            if (element instanceof HTMLTextAreaElement || element instanceof HTMLInputElement) {
              return String(element.value || '');
            }
            const clone = element.cloneNode(true);
            if (clone instanceof HTMLElement) {
              clone.querySelectorAll(
                '.el-input__count,.el-textarea__count,.ant-input-show-count-suffix,' +
                '.el-form-item__error,[class*="input-count"],[class*="textarea-count"]'
              ).forEach(node => node.remove());
              return String(clone.innerText || '');
            }
            return String(element.innerText || '');
          };
          const add = (result, element) => {
            if (!element || !visible(element)) return;
            if (element.matches('script,style,label')) return;
            if (!result.includes(element)) result.push(element);
          };
          const tableColumnCells = labelCell => {
            const result = [];
            if (!labelCell || labelCell.tagName !== 'TH') return result;
            const table = labelCell.closest('.el-table,.ant-table')
              || labelCell.closest('table');
            if (!table) return result;
            const addCell = cell => {
              if (!cell) return;
              cell.querySelectorAll(
                'textarea,input:not([type="hidden"]),.purvar-col__content,' +
                '.purvar_form_item_content,.el-form-item__content'
              ).forEach(element => add(result, element));
              add(result, cell);
            };
            const columnClass = Array.from(labelCell.classList || [])
              .find(name => /(?:^|_)column[_-]\d+/.test(name));
            if (columnClass) {
              table.querySelectorAll('td').forEach(cell => {
                if (cell.classList.contains(columnClass)) addCell(cell);
              });
            }
            const index = labelCell.cellIndex;
            if (Number.isInteger(index) && index >= 0) {
              table.querySelectorAll('tbody tr').forEach(row => {
                addCell(row.children[index]);
              });
            }
            return result;
          };
          const valueCandidates = labelNode => {
            const result = [];
            const descriptionLabel = labelNode.closest(
              '.el-descriptions__label,.ant-descriptions-item-label,dt'
            );
            if (descriptionLabel) {
              add(result, descriptionLabel.nextElementSibling);
              if (descriptionLabel.tagName === 'DT') add(result, descriptionLabel.nextElementSibling);
            }
            const rowCell = labelNode.closest('th,td');
            if (rowCell?.tagName === 'TH') {
              tableColumnCells(rowCell).forEach(element => add(result, element));
            } else if (rowCell) {
              add(result, rowCell.nextElementSibling);
            }
            const owner = labelNode.closest(
              '.el-form-item,.purvar_form_item,.el-descriptions__cell,' +
              '.ant-descriptions-item,.detail-item,.info-item,[class*="detail-item"],[class*="info-item"]'
            );
            if (owner) {
              for (const selector of [
                'textarea[readonly]','textarea:disabled','textarea',
                'input[readonly]','input:disabled',
                '.purvar-col__content','.purvar_form_item_content',
                '.el-descriptions__content','.ant-descriptions-item-content',
                '.detail-value','.info-value','[class*="detail-value"]','[class*="info-value"]',
                '.el-form-item__content'
              ]) {
                owner.querySelectorAll(selector).forEach(element => add(result, element));
              }
              Array.from(owner.children).forEach(element => {
                if (element !== labelNode && !element.contains(labelNode)) add(result, element);
              });
            }
            add(result, labelNode.nextElementSibling);
            add(result, labelNode.parentElement?.nextElementSibling);
            return result;
          };
          const nodes = Array.from(document.body.querySelectorAll(
            'label,dt,th,td,.el-form-item__label,.purvar_form_item_title,' +
            '.el-descriptions__label,.ant-descriptions-item-label,' +
            '[class*="detail-label"],[class*="info-label"],span[field-code],' +
            'span[data-field-code],span[class*="detail"],span[class*="info"],span.box-2,' +
            '.purvar_form_item > .el-col:first-child span'
          )).filter(visible);
          return expectations.map(item => {
            const wanted = normalizeLabel(item.label);
            const labels = nodes.filter(node => {
              const direct = ownText(node);
              const full = String(node.innerText || '').trim();
              return normalizeLabel(direct) === wanted || normalizeLabel(full) === wanted;
            });
            const inlineValues = nodes.flatMap(node => {
              const full = rendered(node).trim();
              for (const delimiter of ['\uFF1A', ':']) {
                const index = full.indexOf(delimiter);
                if (index >= 0 && normalizeLabel(full.slice(0, index)) === wanted) {
                  return [full.slice(index + delimiter.length).trim()];
                }
              }
              return [];
            });
            const candidates = labels.flatMap(valueCandidates);
            const expected = String(item.expected);
            const counterOnly = value => /^\s*\d+\s*\/\s*\d+\s*$/.test(String(value || ''));
            const operationOnly = value => /^(操作|序号)$/.test(normalizeLabel(value));
            const emptyDisplay = value => ['', '-', '--'].includes(String(value || '').trim());
            const values = [
              ...candidates.map(rendered),
              ...inlineValues,
            ].filter(value => !counterOnly(value) && !operationOnly(value)
              && normalizeLabel(value) !== wanted);
            const labelFound = labels.length > 0 || inlineValues.length > 0;
            const matched = expected === ''
              ? labelFound && values.every(emptyDisplay)
              : values.includes(expected);
            return {
              code: item.code,
              label: item.label,
              matched,
              labelFound,
              actual: matched ? expected : (values[0] ?? ''),
              values,
            };
          });
        }
        """
        deadline = time.monotonic() + 8
        while True:
            evidence = self.page.evaluate(evidence_script, items)
            for item in evidence or []:
                if item.get("matched"):
                    continue
                code = str(item.get("code", ""))
                expectation = expectations.get(code)
                if expectation and self._semantic_numeric_readback_values_match(
                    code,
                    expectation[1],
                    item.get("values") or [],
                    field_label=str(item.get("label", "")),
                ):
                    item["matched"] = True
                    item["actual"] = str(expectation[1])
            if evidence and all(item.get("matched") for item in evidence):
                break
            if time.monotonic() >= deadline or not hasattr(self.page, "wait_for_timeout"):
                break
            self.page.wait_for_timeout(200)
        failures = []
        for item in evidence:
            if item.get("matched"):
                continue
            if not item.get("labelFound"):
                failures.append(
                    f"{item['code']}({item['label']}): 详情页面未找到字段标签"
                )
            else:
                failures.append(
                    f"{item['code']}({item['label']}): "
                    f"expected={expectations[item['code']][1]!r}, "
                    f"rendered={item.get('actual', '')!r}"
                )
        if failures:
            prefix = (
                "详情页面字段值与新增页面输入不一致：\n"
                if all_fields
                else "详情页面没有按原格式显示换行、空格或缩进：\n"
            )
            raise AssertionError(prefix + "\n".join(failures))

    def _try_current_page_list_readback(
        self,
        submitted: dict[str, Any],
        record_markers: tuple[str, ...],
        business_id: str,
        *,
        required_codes: set[str] | None = None,
        rendered_text_expectations: dict[str, tuple[str, str]] | None = None,
    ) -> bool:
        """Accept pages where the current list/read-only page is the final readback."""
        required = set(required_codes) if required_codes is not None else set(submitted)
        if not required or any(code not in submitted for code in required):
            return False
        try:
            expectations = rendered_text_expectations or self._submitted_detail_expectations(
                submitted,
                required,
            )
        except Exception:
            return False
        if not expectations:
            return False
        if self._current_record_container_matches_readback(
            expectations,
            record_markers,
            business_id,
        ):
            return True
        if not self._current_page_has_record_identity(record_markers, business_id):
            return False
        try:
            self._assert_rendered_detail_text(expectations, all_fields=True)
        except AssertionError:
            return False
        return True

    def _can_uniquely_locate_current_record(
        self, record_markers: list[str], business_id: str,
    ) -> bool:
        """Check exact current-page identity without changing page state."""
        try:
            self._find_unique_record_container(
                business_id, record_markers, allow_search=False
            )
            return True
        except Exception:
            return False

    def _current_record_container_matches_readback(
        self,
        expectations: dict[str, tuple[str, str]],
        record_markers: tuple[str, ...],
        business_id: str,
    ) -> bool:
        try:
            container, _identity = self._find_unique_record_container(
                business_id,
                list(record_markers),
            )
            text = container.inner_text()
        except Exception:
            return False
        for _code, (_label, expected) in expectations.items():
            actual_values = self._record_container_cell_texts(container) or [text]
            if not self._readback_values_match(
                expected, actual_values, field_code=_code
            ):
                return False
            if self._requires_exact_rendered_text(expected) and not any(
                str(expected) in actual for actual in actual_values
            ):
                return False
        return True

    @staticmethod
    def _record_container_cell_texts(container) -> list[str]:
        try:
            cells = container.locator("td,[role='cell']")
            if not cells.count():
                return []
            return [text for text in cells.all_inner_texts() if str(text).strip()]
        except Exception:
            return []

    def _current_page_has_record_identity(
        self,
        record_markers: tuple[str, ...],
        business_id: str,
    ) -> bool:
        try:
            self._find_unique_record_container(business_id, list(record_markers))
            return True
        except Exception:
            pass
        markers = [
            marker for marker in (*record_markers, business_id)
            if self._normalize_record_text(marker)
        ]
        for marker in markers:
            try:
                nodes = self.page.get_by_text(str(marker), exact=True)
                for index in range(nodes.count()):
                    node = nodes.nth(index)
                    if not node.is_visible():
                        continue
                    if node.evaluate(
                        "el => !el.closest('.el-table__row,.ant-table-row,' +"
                        "'.mujijin-cardBox,.platform-card,.fund-card,.category-item')"
                    ):
                        return True
            except Exception:
                continue
        return False

    @staticmethod
    def _requires_exact_rendered_text(value: Any) -> bool:
        text = str(value or "")
        return bool(re.search(r"\n|\t| {2,}|(^|\n)\s", text))

    def _open_delete_confirmation(self, result: ModuleSmokeResult, responses=None):
        submitted = result.submitted or {}
        allow_arbitrary_row = (
            result.mode == "delete_any_available"
            or ARBITRARY_DELETE_ROW_MARKER in result.record_markers
        )
        markers = list(result.record_markers) or self._submitted_identity_values(submitted)
        markers = self._automation_owned_markers(markers)
        # Dynamic fields can be discovered with a generated Element Plus key.
        # Their value is still safe delete evidence when it is this run's explicit
        # automation marker and can be matched to exactly one rendered row.
        if not markers:
            markers = self._automation_owned_markers(
                [value for value in submitted.values() if value not in (None, "", [])]
            )
        fallback_values = self._delete_display_identity_values(submitted, markers)
        response_payload = result.record_identity_payload
        if not allow_arbitrary_row and not markers and not (
            result.business_id
            and (len(fallback_values) >= 2 or response_payload is not None)
        ):
            raise AssertionError(
                "本次新增记录缺少自动化标识，且没有至少两个可用于唯一定位的保存展示字段，禁止执行删除"
            )

        dialog = self.page.locator(DIALOG).last
        if dialog.count() and dialog.is_visible():
            close = dialog.locator(
                'button:has-text("关闭"),button:has-text("取消"),button[aria-label="Close"]'
            ).last
            if not close.count() or not close.is_visible():
                raise AssertionError("新增回读弹窗无法安全关闭，禁止继续删除")
            close.click()
            dialog.wait_for(state="hidden", timeout=10_000)

        loading_matches = self.page.locator(
            ".el-loading-mask:visible,.ant-spin-spinning:visible,[aria-busy='true']:visible"
        )
        try:
            if loading_matches.count():
                loading = loading_matches.first
                if loading.is_visible():
                    loading.wait_for(state="hidden", timeout=20_000)
        except Exception as exc:
            raise AssertionError("删除前列表在 20 秒内未加载完成") from exc
        # A record created in this process still has a known business ID.  Use
        # the same fresh-JSON-to-rendered-row proof as Edit when possible, but
        # never substitute a newer record with a different ID.
        if allow_arbitrary_row:
            rows = self.page.locator(".el-table__row:visible")
            try:
                rows.first.wait_for(state="visible", timeout=15_000)
            except Exception:
                pass
            row, marker = self._find_unique_delete_row(
                "",
                [],
                rows=rows,
                allow_arbitrary_row=True,
            )
        else:
            candidate = self._latest_automation_delete_candidate(
                allowed_business_ids={self._normalize_record_text(result.business_id)},
            )
            if candidate is not None:
                _business_id, row, marker, candidate_payload = candidate
                response_payload = candidate_payload
            else:
                rows = self.page.locator(".el-table__row:visible")
                try:
                    rows.first.wait_for(state="visible", timeout=15_000)
                except Exception:
                    pass
                row, marker = self._find_unique_delete_row(
                    result.business_id,
                    markers,
                    fallback_values=fallback_values,
                    response_payload=response_payload,
                    rows=rows,
                    allow_arbitrary_row=True,
                )

        arbitrary_row = marker == ARBITRARY_DELETE_ROW_MARKER
        if arbitrary_row:
            markers = [*markers, ARBITRARY_DELETE_ROW_MARKER]

        delete = self._pin_delete_row(
            row,
            "" if arbitrary_row else result.business_id,
            allow_missing_id=(
                arbitrary_row
                or marker.startswith("响应关联字段=")
                or marker == "保存字段组合"
            ),
        ).get_by_role("button", name="删除", exact=True).first
        if not delete.count() or not delete.is_visible() or not delete.is_enabled():
            raise RecordNotDeletableError(
                f"本次自动化新增记录因当前业务状态没有可用删除按钮：{marker}"
            )
        if responses is not None:
            self.page.on("response", lambda response: responses.append(response))
        delete.click()
        confirm = self.page.locator('.el-message-box:visible,[role="alertdialog"]:visible').last
        confirm.wait_for(state="visible", timeout=10_000)
        return submitted, markers, fallback_values, confirm

    def find_available_delete_record(self) -> ModuleSmokeResult | None:
        """Return one rendered record with an enabled row-local Delete action."""
        rows = self.page.locator(".el-table__row:visible")
        try:
            rows.first.wait_for(state="visible", timeout=15_000)
        except Exception:
            pass
        try:
            self._find_unique_delete_row(
                "", [], rows=rows, allow_arbitrary_row=True,
            )
        except AssertionError:
            return None
        return ModuleSmokeResult(
            mode="delete_any_available",
            record_markers=(ARBITRARY_DELETE_ROW_MARKER,),
        )

    def find_reusable_automation_delete_record(self) -> ModuleSmokeResult | None:
        """Return one registry-owned record that remains uniquely provable on screen."""
        loading_matches = self.page.locator(
            ".el-loading-mask:visible,.ant-spin-spinning:visible,[aria-busy='true']:visible"
        )
        try:
            if loading_matches.count():
                loading = loading_matches.first
                if loading.is_visible():
                    loading.wait_for(state="hidden", timeout=20_000)
        except Exception as exc:
            raise AssertionError("复用删除记录前列表在 20 秒内未加载完成") from exc

        registered = self._registered_automation_records()
        entries_by_id = {
            self._normalize_record_text(entry.get("business_id")): entry
            for entry in registered
            if self._normalize_record_text(entry.get("business_id"))
        }
        candidate = self._latest_automation_delete_candidate(
            allowed_business_ids=set(entries_by_id),
        )
        if candidate is not None:
            business_id, row, identity, response_payload = candidate
            entry = entries_by_id[business_id]
            submitted = entry.get("submitted") if isinstance(entry.get("submitted"), dict) else {}
            markers = self._automation_owned_markers(entry.get("record_markers") or [])
            self._pin_delete_row(
                row,
                business_id,
                allow_missing_id=identity.startswith("响应关联字段="),
            )
            return ModuleSmokeResult(
                mode="delete_reusable_record",
                business_id=business_id,
                submitted=submitted,
                record_markers=tuple(markers),
                record_identity_payload=response_payload,
            )

        rows = self.page.locator(".el-table__row:visible")
        for entry in registered:
            business_id = self._normalize_record_text(entry.get("business_id"))
            markers = self._automation_owned_markers(entry.get("record_markers") or [])
            submitted = entry.get("submitted") if isinstance(entry.get("submitted"), dict) else {}
            response_payload = entry.get("record_identity_payload")
            if not business_id:
                continue
            try:
                fallback_values = self._delete_display_identity_values(
                    submitted, markers
                )
                row, identity = self._find_unique_delete_row(
                    business_id,
                    markers,
                    fallback_values=fallback_values,
                    response_payload=response_payload,
                    rows=rows,
                )
                row_id = self._row_business_id(row)
                if not row_id and not (
                    identity.startswith("响应关联字段=")
                    or identity == "保存字段组合"
                ):
                    row, identity = self._find_unique_delete_row(
                        business_id,
                        [],
                        fallback_values=fallback_values,
                        response_payload=response_payload,
                        rows=rows,
                    )
                self._pin_delete_row(
                    row,
                    business_id,
                    allow_missing_id=(
                        identity.startswith("响应关联字段=")
                        or identity == "保存字段组合"
                    ),
                )
            except (AssertionError, RecordNotDeletableError):
                continue
            return ModuleSmokeResult(
                mode="delete_reusable_record",
                business_id=business_id,
                submitted=submitted,
                record_markers=tuple(markers),
                record_identity_payload=response_payload,
            )
        return None

    def _latest_automation_delete_candidate(
        self,
        *,
        allowed_business_ids: set[str],
    ) -> tuple[str, Any, str, dict[str, Any]] | None:
        """Find the newest owned record whose JSON data proves one deletable row.

        Deletion must never use the general Edit fallback: the allowed IDs are
        supplied by the caller and are either this transaction's returned ID
        or current-scope registry ownership entries.
        """
        allowed_ids = {
            self._normalize_record_text(business_id)
            for business_id in allowed_business_ids
            if self._normalize_record_text(business_id)
        }
        if not allowed_ids:
            return None
        responses: list[Any] = []
        listener = lambda response: responses.append(response)
        listening = hasattr(self.page, "on") and hasattr(self.page, "remove_listener")
        if listening:
            self.page.on("response", listener)
        try:
            refreshed = self._refresh_list_after_delete()
            if refreshed and hasattr(self.page, "wait_for_timeout"):
                self.page.wait_for_timeout(500)
        finally:
            if listening:
                self.page.remove_listener("response", listener)

        candidates = self._latest_automation_delete_response_candidates(
            responses,
            allowed_business_ids=allowed_ids,
        )
        if not candidates:
            return None
        _key, business_id, row, identity, payload = candidates[0]
        print(
            "DELETE_RECORD_CANDIDATE_SELECTED "
            f"business_id={business_id} evidence={identity}",
            flush=True,
        )
        return business_id, row, identity, payload

    def _latest_automation_delete_response_candidates(
        self,
        responses: Iterable[Any],
        *,
        allowed_business_ids: set[str],
    ) -> list[tuple[tuple[int, int, str], str, Any, str, dict[str, Any]]]:
        """Rank only registry-owned JSON records that prove one deletable row."""
        allowed_ids = {
            self._normalize_record_text(business_id)
            for business_id in allowed_business_ids
            if self._normalize_record_text(business_id)
        }
        records_by_id: dict[str, dict[str, Any]] = {}
        for response in responses:
            if not self._is_json_collection_response(response):
                continue
            try:
                payload = response.json()
            except Exception:
                continue
            for record in self._collect_dicts(payload):
                business_id = self._direct_record_business_id(record)
                if (
                    business_id not in allowed_ids
                    or not self._record_create_time_key(record)
                ):
                    continue
                previous = records_by_id.get(business_id)
                if previous is None or self._record_recency_key(record) > self._record_recency_key(previous):
                    records_by_id[business_id] = record

        candidates: list[tuple[tuple[int, int, str], str, Any, str, dict[str, Any]]] = []
        for business_id, record in records_by_id.items():
            payload = {"data": {"records": [record]}}
            try:
                row, identity = self._find_response_associated_record_container(
                    payload,
                    business_id,
                    display_field_codes=self._visible_response_field_codes(record),
                )
            except AssertionError:
                continue
            if not any(
                self._record_container_has_enabled_action(row, action)
                for action in ("删除", "移除")
            ):
                continue
            candidates.append((
                self._record_recency_key(record), business_id, row, identity, payload,
            ))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates

    def _registered_automation_records(self) -> list[dict[str, Any]]:
        """Read registry records whose authoritative scope/ID key is intact."""
        path = getattr(self, "automation_record_registry", None)
        if path is None or not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        records = payload.get("records") if isinstance(payload, dict) else []
        if not isinstance(records, list):
            return []
        scope = self._automation_registry_scope()
        seen_keys: set[tuple[str, str]] = set()
        valid: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict) or record.get("page_scope") != scope:
                continue
            business_id = self._normalize_record_text(record.get("business_id"))
            key = (scope, business_id)
            if not business_id or key in seen_keys:
                continue
            seen_keys.add(key)
            valid.append(record)
        return valid

    def _automation_registry_scope(self, page_url: str = "") -> str:
        page_url = str(page_url or getattr(self.page, "url", "") or "")
        parts = urlsplit(page_url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))

    def _automation_registry_display_values(
        self, submitted: dict[str, Any]
    ) -> dict[str, Any]:
        """Retain only stable scalar values that can identify the created row."""
        display_values: dict[str, Any] = {}
        for code, value in submitted.items():
            if self._is_generated_identifier(str(code)) or value in (None, "", []):
                continue
            display_value = self._remembered_display_value(str(code), value)
            candidate = display_value if display_value is not None else value
            if isinstance(candidate, bool) or isinstance(candidate, (dict, list, tuple, set)):
                continue
            if isinstance(candidate, Decimal):
                candidate = str(candidate)
            if not isinstance(candidate, (str, int, float)):
                candidate = str(candidate)
            if self._normalize_record_text(candidate):
                display_values[str(code)] = candidate
        return display_values

    @classmethod
    def _minimal_record_identity_payload(
        cls,
        payload: Any,
        business_id: str,
        *,
        submitted: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Keep only the created ID and submitted fields that may identify its row."""
        normalized_id = cls._normalize_record_text(business_id)
        records = [
            record
            for record in cls._collect_dicts(payload)
            if cls._direct_record_business_id(record) == normalized_id
        ]
        record = records[-1] if records else {}
        minimal: dict[str, Any] = {"id": str(business_id)}
        submitted_codes = tuple(
            str(code) for code in (submitted or {}) if str(code)
        )
        for code in submitted_codes:
            wildcard_code = re.sub(r"(?<=\.)\d+(?=\.|$)", "*", code)
            candidates = cls._deduplicate([
                *DETAIL_DISPLAY_ALIASES.get(code, ()),
                *DETAIL_DISPLAY_ALIASES.get(wildcard_code, ()),
                code,
                wildcard_code,
            ])
            for candidate in candidates:
                values = [
                    value
                    for value in cls._field_values_from_record(record, candidate)
                    if value not in (None, "")
                    and not isinstance(value, (bool, dict, list))
                ]
                normalized_values = {
                    cls._normalize_record_text(value): value for value in values
                    if cls._normalize_record_text(value)
                }
                if len(normalized_values) != 1:
                    continue
                value = next(iter(normalized_values.values()))
                if isinstance(value, Decimal):
                    value = str(value)
                elif not isinstance(value, (str, int, float)):
                    value = str(value)
                minimal[candidate] = value
                break
        return minimal

    def _remember_automation_owned_record(
        self,
        result: ModuleSmokeResult,
        *,
        page_scope: str = "",
    ) -> None:
        """Persist minimal identity evidence only after a successful automation create."""
        path = getattr(self, "automation_record_registry", None)
        if path is None or not result.business_id:
            return
        normalized_scope = self._automation_registry_scope(page_scope)
        if not normalized_scope:
            return
        markers = self._automation_owned_markers(list(result.record_markers))
        run_id = self._automation_registry_run_id(markers)
        entry = {
            "schema_version": 2,
            "registry_key": f"{normalized_scope}::{result.business_id}",
            "business_id": str(result.business_id),
            "page_scope": normalized_scope,
            "run_id": run_id,
            "sequence": self._automation_registry_sequence(markers),
            "record_markers": markers,
            "submitted": self._automation_registry_display_values(
                result.submitted or {}
            ),
            "record_identity_payload": self._minimal_record_identity_payload(
                result.record_identity_payload,
                str(result.business_id),
                submitted=result.submitted or {},
            ),
        }
        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            raw_payload = {}
        existing_records = (
            raw_payload.get("records", []) if isinstance(raw_payload, dict) else []
        )
        if not isinstance(existing_records, list):
            existing_records = []
        conflicting_marker_ids = sorted({
            self._normalize_record_text(record.get("business_id"))
            for record in existing_records
            if isinstance(record, dict)
            and record.get("page_scope") == normalized_scope
            and markers
            and set(markers).intersection(
                self._automation_owned_markers(record.get("record_markers") or [])
            )
            and self._normalize_record_text(record.get("business_id"))
            != entry["business_id"]
        })
        if conflicting_marker_ids:
            entry["marker_conflict_business_ids"] = conflicting_marker_ids
        records = [
            record for record in existing_records
            if not isinstance(record, dict)
            or (
                self._normalize_record_text(record.get("business_id"))
                != entry["business_id"]
                or record.get("page_scope") != normalized_scope
            )
        ]
        records.insert(0, entry)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"records": records[:200]}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            # Failure to maintain local cleanup evidence must only disable reuse.
            return

    @staticmethod
    def _automation_registry_run_id(markers: Iterable[str]) -> str:
        """Derive a readable run identity from this run's generated markers."""
        for marker in markers:
            match = re.search(r"(?:AUTO_|UI自动化_)(\d{14,})(?:_|$)", str(marker))
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _automation_registry_sequence(markers: Iterable[str]) -> int | None:
        for marker in markers:
            match = re.search(r"_(\d+)(?:_S\d+)?$", str(marker))
            if match:
                return int(match.group(1))
        return None

    def _forget_automation_owned_record(
        self,
        business_id: str,
        *,
        page_scope: str = "",
    ) -> None:
        path = getattr(self, "automation_record_registry", None)
        normalized_id = self._normalize_record_text(business_id)
        normalized_scope = self._automation_registry_scope(page_scope)
        if path is None or not normalized_id:
            return
        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            raw_payload = {}
        existing_records = (
            raw_payload.get("records", []) if isinstance(raw_payload, dict) else []
        )
        records = [
            record for record in existing_records
            if not isinstance(record, dict)
            or self._normalize_record_text(record.get("business_id")) != normalized_id
            or record.get("page_scope") != normalized_scope
        ]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            return

    def _pin_delete_row(self, row, business_id: str, *, allow_missing_id: bool = False):
        """Pin one physical table row so a rerender cannot move a row locator."""
        expected_id = self._normalize_record_text(business_id)
        row_id = self._row_business_id(row)
        if expected_id and row_id and row_id != expected_id:
            raise AssertionError("删除前目标行的业务 ID 已变化，已停止删除")
        if expected_id and not row_id and not allow_missing_id:
            raise AssertionError("删除前目标行未暴露业务 ID，已停止删除")
        token = f"ei-delete-target-{uuid.uuid4().hex}"
        try:
            row.evaluate(
                "(node, token) => node.setAttribute('data-ei-delete-target', token)",
                token,
            )
            pinned = self.page.locator(
                f'.el-table__row[data-ei-delete-target="{token}"]:visible'
            )
            if pinned.count() != 1 or not pinned.is_visible():
                raise AssertionError("target row was replaced before delete click")
            pinned_id = self._row_business_id(pinned)
            if expected_id and pinned_id and pinned_id != expected_id:
                raise AssertionError("删除前固定行的业务 ID 与目标不一致，已停止删除")
            if expected_id and not pinned_id and not allow_missing_id:
                raise AssertionError("删除前固定行未暴露业务 ID，已停止删除")
            return pinned
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError("删除前无法锁定本次自动化记录所在行，已停止删除") from exc

    @staticmethod
    def _delete_request_business_id(request) -> str:
        path = urlsplit(str(getattr(request, "url", "") or "")).path
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) >= 2 and segments[-2].lower() in {"delete", "remove"}:
            return segments[-1]
        return ""

    @staticmethod
    def _sanitized_delete_url(url: str) -> str:
        parts = urlsplit(str(url or ""))
        return parts.path or str(url or "")

    def _install_delete_request_guard(self, business_id: str) -> list[str]:
        """Abort a rerender-drift delete request before it removes another row."""
        expected_id = self._normalize_record_text(business_id)
        route = getattr(self.page, "route", None)
        unroute = getattr(self.page, "unroute", None)
        if not expected_id or not callable(route) or not callable(unroute):
            return []
        blocked_urls: list[str] = []

        def guard(intercept, request):
            method = str(getattr(request, "method", "")).upper()
            url = str(getattr(request, "url", "") or "")
            path = urlsplit(url).path.lower()
            if method in {"POST", "PUT", "PATCH", "DELETE"} and any(
                token in path for token in ("/delete", "/remove")
            ):
                actual_id = self._delete_request_business_id(request)
                if actual_id != expected_id:
                    blocked_urls.append(self._sanitized_delete_url(url))
                    intercept.abort()
                    unroute("**/*", guard)
                    return
                intercept.continue_()
                unroute("**/*", guard)
                return
            intercept.continue_()

        route("**/*", guard)
        return blocked_urls

    def cancel_delete_created_record(self, result: ModuleSmokeResult) -> ModuleSmokeResult:
        """Prove a delete confirmation can be cancelled without deleting owned data."""
        submitted, markers, fallback_values, confirm = self._open_delete_confirmation(result)
        cancel = confirm.locator(
            'button:has-text("取消"),button:has-text("关闭"),button[aria-label="Close"]'
        ).last
        if not cancel.count() or not cancel.is_visible():
            raise AssertionError("删除确认框没有取消按钮")
        cancel.click()
        confirm.wait_for(state="hidden", timeout=10_000)
        if ARBITRARY_DELETE_ROW_MARKER not in markers:
            self._find_unique_delete_row(
                result.business_id,
                markers,
                fallback_values=fallback_values,
                response_payload=result.record_identity_payload,
            )
        return ModuleSmokeResult(
            mode="delete_confirmation_cancelled", business_id=result.business_id,
            save_url=result.save_url, submitted=submitted, record_markers=tuple(markers),
        )

    def delete_created_record(self, result: ModuleSmokeResult) -> ModuleSmokeResult:
        responses = []
        submitted, markers, fallback_values, confirm = self._open_delete_confirmation(
            result, responses
        )
        confirm_button = confirm.get_by_role("button", name="确定", exact=True).last
        if not confirm_button.count() or not confirm_button.is_visible():
            raise AssertionError("删除确认框没有确定按钮")
        arbitrary_row = ARBITRARY_DELETE_ROW_MARKER in markers
        blocked_delete_urls = (
            [] if arbitrary_row else self._install_delete_request_guard(result.business_id)
        )
        confirm_button.click()
        confirm.wait_for(state="hidden", timeout=10_000)
        if blocked_delete_urls:
            raise AssertionError(
                "删除前已阻止错误记录删除：目标业务 ID="
                f"{result.business_id}; 实际请求={blocked_delete_urls[-1]}"
            )
        self.page.wait_for_timeout(1_000)

        delete_responses = [
            response for response in responses
            if response.request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and any(token in response.url.lower() for token in ("delete", "remove"))
        ]
        if not delete_responses:
            raise AssertionError("确认删除后没有捕获到删除接口响应")
        delete_response = delete_responses[-1]
        actual_delete_id = self._delete_request_business_id(delete_response.request)
        if (
            not arbitrary_row
            and result.business_id
            and actual_delete_id != str(result.business_id)
        ):
            raise AssertionError(
                "删除接口目标与本次记录不一致："
                f"expected={result.business_id}; actual={actual_delete_id or 'unknown'}; "
                f"url={self._sanitized_delete_url(delete_response.url)}"
            )
        if not delete_response.ok:
            raise AssertionError(f"删除接口失败：HTTP {delete_response.status} {delete_response.url}")
        try:
            self._assert_business_success(delete_response.json(), operation="删除")
        except ValueError:
            pass
        self._refresh_list_after_delete()
        if not arbitrary_row:
            self._wait_for_deleted_record_absent(
                result.business_id,
                markers,
                fallback_values=fallback_values,
                response_payload=result.record_identity_payload,
                responses=responses,
                after_response=delete_response,
            )
            self._forget_automation_owned_record(result.business_id)
        return ModuleSmokeResult(
            mode="add_and_delete_verified",
            business_id=actual_delete_id if arbitrary_row else result.business_id,
            save_url=result.save_url, detail_url=delete_response.url, submitted=submitted,
            record_markers=tuple(
                marker for marker in markers if marker != ARBITRARY_DELETE_ROW_MARKER
            ),
        )

    def verify_nested_operation(self) -> ModuleSmokeResult:
        """Verify a dialog-local action without saving a duplicate parent record."""
        add = self._wait_for_add_button()
        add.click()
        scope = self._wait_for_form_scope()
        self._wait_for_form_ready(scope)
        self._prepare_nested_operation(scope)
        return ModuleSmokeResult(mode="nested_action_verified")

    def save_open_dialog(self, operation: str) -> ModuleSmokeResult:
        """Fill and save a dialog opened by edit or another business action."""
        scope = self._wait_for_form_scope()
        self._wait_for_form_ready(scope)
        submitted = self._fill_dialog()
        self._upload_default_attachments(scope)
        fields = scan_dom_fields(self.page, scope)
        if not fields:
            raise AssertionError(f"页面操作“{operation}”打开了弹窗，但没有发现可编辑表单字段")
        empty_required = [
            field.label or field.field_code
            for field in fields
            if field.required and not self._dom_field_has_value(field)
        ]
        if self._fill_failures or empty_required:
            details = [*self._fill_failures, *empty_required]
            raise AssertionError(
                f"页面操作“{operation}”保存前表单未填写完整：" + "; ".join(details)
            )
        save = self._save_button(scope, operation)
        if not save.count() or not save.is_visible() or not save.is_enabled():
            raise AssertionError(f"页面操作“{operation}”弹窗没有可用的保存/确定按钮")
        responses = []
        self.page.on("response", lambda response: responses.append(response))
        self._save_with_validation_repairs(scope, save, responses, submitted)
        self.page.wait_for_timeout(1_000)
        save_response = self._find_save_response(responses, submitted)
        if save_response is None:
            raise AssertionError(f"页面操作“{operation}”点击保存后没有捕获到业务保存接口")
        if not save_response.ok:
            raise AssertionError(
                f"页面操作“{operation}”保存接口失败：HTTP {save_response.status} "
                f"{save_response.url}; {self._failed_save_response_detail(save_response)}"
            )
        try:
            body = save_response.json()
        except Exception:
            body = save_response.text()
        self._assert_business_success(body, operation=operation)
        return ModuleSmokeResult(
            mode="dialog_action_saved", save_url=save_response.url, submitted=submitted
        )

    def _field_report_ok(self, report: FieldCompletionReport) -> bool:
        if not report.ok:
            return False
        if not getattr(self.data_strategy, "strict_field_validation", False):
            return True
        return not (report.optional_not_filled or report.optional_fill_failed)

    def _assert_open_form_values(
        self,
        submitted: dict[str, Any],
        *,
        required_codes: set[str] | None = None,
        display_values: dict[str, Any] | None = None,
    ) -> None:
        """Verify CRUD pages whose edit action reuses list data without a detail request."""
        required = set(required_codes) if required_codes is not None else None
        if required is not None:
            missing_submitted = sorted(required - submitted.keys())
            if missing_submitted:
                raise AssertionError(
                    "指定回读字段未出现在本次提交值："
                    + ", ".join(missing_submitted)
                )
        scope = self._wait_for_readback_form_scope()
        self._wait_for_form_ready(scope)
        if required == set():
            # Some specialized checks, such as EDIT-004 attachment persistence,
            # delegate their field assertion after this method.  They still need
            # proof that the saved record's edit form reopened successfully.
            return
        comparable: dict[str, tuple[Any, list[str]]] = {}
        try:
            dom_fields = scan_dom_fields(self.page, scope)
        except TypeError:
            # Keep compatibility with injected scanners used by local contract tests.
            dom_fields = scan_dom_fields(self.page)
        for index, dom in enumerate(dom_fields, start=1):
            source_code, _source_label, *_ = self._source_for_dom(dom, index)
            field_code = source_code if self._is_generated_identifier(dom.field_code) else dom.field_code
            if (
                field_code not in submitted
                or (required is not None and field_code not in required)
                or dom.kind not in {
                "text", "textarea", "number", "date", "datetime", "year",
                "select", "multi_select", "radio", "checkbox", "file",
                }
            ):
                continue
            actual_values = self._open_form_field_values(dom, scope)
            expected = (display_values or {}).get(field_code, submitted[field_code])
            if actual_values or (
                required is not None
                and field_code in required
                and not self._normalize_record_text(expected)
            ):
                comparable[field_code] = (expected, actual_values)
        if required is not None:
            missing_comparable = sorted(required - comparable.keys())
            if missing_comparable:
                raise AssertionError(
                    "编辑表单缺少或无法比较指定回读字段："
                    + ", ".join(missing_comparable)
                )
        if not comparable:
            raise AssertionError(
                "新增后未捕获到详情接口响应，编辑表单也没有可核对的提交字段"
            )
        failures = [
            f"{code}: expected={expected!r}, actual={actual_values!r}"
            for code, (expected, actual_values) in comparable.items()
            if not self._readback_values_match(
                expected, actual_values, field_code=code
            )
        ]
        if failures:
            raise AssertionError("编辑表单回显与本次提交不一致：\n" + "\n".join(failures))

    def _open_form_field_values(self, dom, scope) -> list[str]:
        """Return raw and rendered values for one visible edit-form control."""
        root = scope if hasattr(scope, "locator") else self.page
        control = root.locator(dom.selector).first
        if not control.count() or not control.is_visible():
            return []
        try:
            values = control.evaluate(r"""
            (el, domKind) => {
              const clean = value => String(value ?? '').trim();
              const unique = values => [...new Set(values.map(clean).filter(Boolean))];
              const kind = (el.type || '').toLowerCase();
              const role = (el.getAttribute('role') || '').toLowerCase();
              const cls = `${el.className || ''}`.toLowerCase();
              const owner = el.closest(
                '.el-form-item,.purvar_form_item,.ant-form-item,.el-select,' +
                '.el-cascader,.ant-select,[role="radiogroup"],[role="group"]'
              ) || el.parentElement;
              if (domKind === 'radio' || domKind === 'checkbox' ||
                  kind === 'radio' || kind === 'checkbox' ||
                  ['radio', 'checkbox', 'switch', 'radiogroup', 'group'].includes(role) ||
                  cls.includes('radio-group') || cls.includes('checkbox-group')) {
                const checked = owner?.querySelectorAll(
                  'input:checked,[aria-checked="true"]'
                ) || [];
                return unique(Array.from(checked).flatMap(item => {
                  const wrapper = item.closest(
                    'label,.el-radio,.el-checkbox,.ant-radio-wrapper,.ant-checkbox-wrapper'
                  );
                  return [item.value, item.getAttribute('label'), wrapper?.innerText];
                }));
              }
              if ((el.tagName || '').toLowerCase() === 'select') {
                return unique(Array.from(el.selectedOptions || []).flatMap(option => [
                  option.value, option.textContent,
                ]));
              }
              const select = el.closest('.el-select,.el-cascader,.ant-select') ||
                owner?.querySelector('.el-select,.el-cascader,.ant-select');
              if (select) {
                const selected = select.querySelectorAll(
                  '.el-select__selected-item:not(.is-transparent),.el-select__tags-text,' +
                  '.el-cascader__tags-text,.ant-select-selection-item'
                );
                return unique([
                  el.value,
                  el.getAttribute('value'),
                  ...Array.from(selected).flatMap(item => [
                    item.textContent, item.getAttribute('title'), item.getAttribute('data-value'),
                  ]),
                ]);
              }
              if (el.isContentEditable) return unique([el.textContent]);
              if (domKind === 'file') {
                const files = owner?.querySelectorAll(
                  '.el-upload-list__item-name,.el-upload-list__item a,' +
                  '.ant-upload-list-item-name,[data-upload-name],' +
                  '[class*="file-name"],[class*="filename"]'
                ) || [];
                return unique([
                  ...(el.files ? Array.from(el.files, file => file.name) : []),
                  ...Array.from(files).map(item => item.getAttribute('title') || item.textContent),
                ]);
              }
              return unique([el.value, el.getAttribute('value')]);
            }
            """, dom.kind)
        except Exception:
            try:
                values = [control.input_value()]
            except Exception:
                return []
        return self._deduplicate([str(value).strip() for value in values if str(value).strip()])

    @staticmethod
    def _decimal_readback_value(value: Any) -> Decimal | None:
        text = str(value or "").strip().replace(",", "")
        if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
            return None
        try:
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        return number if number.is_finite() else None

    @classmethod
    def _semantic_numeric_readback_values_match(
        cls,
        field_code: str,
        expected: Any,
        actual_values: list[Any],
        *,
        field_label: str = "",
    ) -> bool:
        if not field_code or isinstance(expected, (list, tuple, set)):
            return False
        if not is_semantic_numeric_field(str(field_code), field_label):
            return False
        expected_number = cls._decimal_readback_value(expected)
        if expected_number is None:
            return False
        return any(
            cls._decimal_readback_value(actual) == expected_number
            for actual in actual_values
        )

    @classmethod
    def _readback_values_match(
        cls,
        expected: Any,
        actual_values: list[str],
        *,
        field_code: str = "",
    ) -> bool:
        if cls._semantic_numeric_readback_values_match(
            field_code, expected, actual_values
        ):
            return True
        if isinstance(expected, (list, tuple, set)):
            expected_values = {
                cls._normalize_record_text(value) for value in expected
                if cls._normalize_record_text(value)
            }
            actual = {
                cls._normalize_record_text(value) for value in actual_values
                if cls._normalize_record_text(value)
            }
            return bool(expected_values) and expected_values.issubset(actual)
        normalized = cls._normalize_record_text(expected)
        if not normalized:
            return not any(
                cls._normalize_record_text(value) for value in actual_values
            )
        return normalized in {
            cls._normalize_record_text(value) for value in actual_values
        }

    def _save_with_validation_repairs(
        self,
        scope,
        save,
        responses,
        submitted: dict[str, Any],
        *,
        protected_codes: set[str] | None = None,
        allow_unique_repair: bool = True,
    ) -> None:
        self._save_with_validation_repairs_inner(
            scope,
            save,
            responses,
            submitted,
            protected_codes=protected_codes,
            allow_unique_repair=allow_unique_repair,
        )

    def _save_with_validation_repairs_inner(
        self,
        scope,
        save,
        responses,
        submitted: dict[str, Any],
        *,
        protected_codes: set[str] | None = None,
        allow_unique_repair: bool = True,
    ) -> None:
        max_attempts = max(1, int(os.getenv("EI_VALIDATION_SAVE_ATTEMPTS", "3")))
        for attempt in range(1, max_attempts + 1):
            self._capture_submitted_display_values(submitted, scope)
            response_start = len(responses)
            save.click()
            try:
                timeout = 5_000 if attempt < max_attempts else 30_000
                scope.wait_for(state="hidden", timeout=timeout)
                return
            except Exception:
                new_responses = responses[response_start:]
                save_response = self._find_save_response(new_responses, submitted)
                if save_response is not None:
                    if not save_response.ok:
                        raise AssertionError(
                            f"保存接口失败：HTTP {save_response.status} {save_response.url}; "
                            f"{self._failed_save_response_detail(save_response)}"
                        )
                    try:
                        body = save_response.json()
                        self._assert_business_success(body)
                    except ValueError:
                        pass
                    except AssertionError:
                        message = str(
                            body.get("message") or body.get("msg") or body
                        ) if isinstance(body, dict) else str(body)
                        repaired = self._repair_business_validation_message(
                            message,
                            submitted,
                            attempt,
                            protected_codes=protected_codes,
                            allow_unique_repair=allow_unique_repair,
                        )
                        if repaired and attempt < max_attempts:
                            submitted.update(repaired)
                            continue
                        raise
                    return
                repaired = self._repair_visible_validation_errors(
                    scope, submitted, trigger_blur=False
                )
                if repaired and attempt < max_attempts:
                    submitted.update(repaired)
                    self.last_field_report = self.check_field_completion(submitted, self._fill_failures)
                    self.write_field_diagnostics(self.last_field_report, submitted, attempt)
                    continue
                errors = self.page.locator(
                    '.el-form-item__error:visible,.el-message:visible,[role="alert"]:visible'
                )
                detail = "; ".join(errors.all_inner_texts()) if errors.count() else "表单未关闭"
                raise AssertionError(f"新增保存未完成：{detail}")

    def _repair_business_validation_message(
        self,
        message: str,
        submitted: dict[str, Any],
        attempt: int,
        *,
        protected_codes: set[str] | None = None,
        retryable_unique_codes: set[str] | None = None,
        allow_unique_repair: bool = True,
    ) -> dict[str, Any]:
        if not hasattr(self.data_strategy, "repair_value"):
            return {}
        if not allow_unique_repair or self._is_edit_operation():
            return {}
        protected = {str(code).lower() for code in (protected_codes or set())}
        retryable_unique = {
            str(code).lower() for code in (retryable_unique_codes or set())
        }
        normalized = self._normalize_label(message)
        preferred_code = ""
        preferred_resolver = getattr(
            self.data_strategy, "unique_repair_field", None
        )
        if callable(preferred_resolver):
            preferred_code = preferred_resolver(message, submitted)
        spec = self._declared_unique_constraint_for_message(message, submitted)
        print(
            "COMMON_UNIQUE_REPAIR_DECISION "
            f"form={getattr(self.data_strategy, 'form_code', '')} "
            f"declared_match={spec is not None} "
            f"submitted_codes={sorted(str(code) for code in submitted)} "
            f"submitted_key_fields={tuple(getattr(spec, 'field_codes', ())) if spec else ()} "
            f"protected={sorted(protected)} "
            f"retryable={sorted(retryable_unique)}",
            flush=True,
        )
        if spec is not None:
            self._release_pending_unique_reservations_for_spec(spec)
            # A duplicate response is explicit evidence that the current Add
            # target is invalid. It may be retried only when this transaction
            # opted that code in and the constraint declares it repairable.
            unique_protected = protected - retryable_unique
            candidates = self._constraint_repair_candidates(spec, unique_protected)
            if retryable_unique:
                candidates = tuple(
                    code for code in candidates
                    if str(code).lower() in retryable_unique
                )
            return self._repair_declared_unique_constraint(
                spec,
                candidates,
                submitted,
                message=message,
                source="saveResponse",
                protected_codes=unique_protected,
            )
        candidates = (
            [
                field for field in self.source_fields
                if field[0] == preferred_code
                and str(field[0]).lower() not in protected
            ]
            if preferred_code
            else [
                field for field in self.source_fields
                if field[0].lower() in message.lower()
                or self._normalize_label(field[1]) in normalized
                if str(field[0]).lower() not in protected
            ]
        )
        if preferred_code and not candidates:
            candidates = [
                (dom.field_code, dom.label, dom.qcc_remote)
                for dom in scan_dom_fields(self.page)
                if dom.field_code == preferred_code
                and str(dom.field_code).lower() not in protected
            ]
        if not candidates:
            candidates = [
                (dom.field_code, dom.label, dom.qcc_remote)
                for dom in scan_dom_fields(self.page)
                if dom.label and self._normalize_label(dom.label) in normalized
                and str(dom.field_code).lower() not in protected
            ]
        if not candidates and any(token in message for token in ("已存在", "重复", "不能重复")):
            candidates = self._duplicate_field_candidates(self.source_fields, submitted)
        candidates = [
            field for field in candidates
            if str(field[0]).lower() not in protected
        ]
        for code, label, *metadata in candidates:
            definition, dom = self._definition_for_validation_issue({
                "message": message, "code": code, "label": label, "selector": "",
            })
            if definition is None or dom is None:
                continue
            old_value = submitted.get(definition.field_code)
            result = self.data_strategy.repair_value(
                definition, old_value, message, attempt
            )
            if result is None:
                continue
            new_value, constraint = result
            if dom.kind in {"select", "multi_select"} and constraint.get("kind") == "unique":
                new_value = self._select_by_label(
                    label,
                    option_index=attempt,
                    field_code=code,
                    selector=dom.selector,
                    qcc_remote=bool(metadata[0]) if metadata else False,
                )
            elif dom.kind in {"select", "multi_select", "radio", "checkbox"}:
                continue
            else:
                self.interactor.fill(ResolvedField(definition, dom), new_value)
            self._validation_repairs.append({
                "fieldCode": definition.field_code,
                "label": definition.field_name,
                "oldValue": old_value,
                "validationMessage": message,
                "constraint": constraint,
                "newValue": new_value,
                "attempt": attempt,
                "source": "saveResponse",
                "repairResult": "applied",
            })
            return {definition.field_code: new_value}
        return {}

    def _prepare_declared_unique_values(
        self,
        scope,
        submitted: dict[str, Any],
        *,
        exclude_codes: set[str] | None = None,
    ) -> dict[str, Any]:
        """Allocate an unoccupied composite key before a physical create."""
        if self._is_edit_operation():
            return {}
        specs = self._declared_unique_constraints(submitted)
        if not specs:
            return {}
        prepared: dict[str, Any] = {}
        protected = {str(code).lower() for code in (exclude_codes or set())}
        for spec in specs:
            prepared.update(self._repair_declared_unique_constraint(
                spec,
                self._constraint_repair_candidates(spec, protected),
                {**submitted, **prepared},
                source="declaredUniqueConstraint",
                protected_codes=protected,
                scope=scope,
            ))
        return prepared

    def _declared_unique_constraints(
        self, submitted: dict[str, Any] | None = None,
    ) -> tuple[Any, ...]:
        strategy = getattr(self, "data_strategy", None)
        resolver = getattr(
            strategy, "declared_unique_constraints", None
        )
        if not callable(resolver):
            return ()
        return tuple(resolver(submitted))

    def _declared_unique_constraint_for_message(
        self, message: str, submitted: dict[str, Any],
    ):
        resolver = getattr(
            self.data_strategy, "declared_unique_constraint_for_message", None
        )
        return resolver(message, submitted) if callable(resolver) else None

    @staticmethod
    def _is_edit_operation() -> bool:
        action = os.getenv("EI_COMMON_FORM_ACTION", "").strip()
        return action.startswith(("编辑", "修改"))

    def _track_unique_reservation(
        self, spec, key: tuple[Any, ...], page_scope: str,
    ) -> None:
        pending = getattr(self, "_pending_unique_reservations", None)
        if not isinstance(pending, list):
            pending = self._pending_unique_reservations = []
        pending.append((spec, key, page_scope))

    def commit_pending_unique_reservations(self) -> None:
        pending = getattr(self, "_pending_unique_reservations", None)
        if isinstance(pending, list):
            pending.clear()

    def _release_pending_unique_reservations_for_spec(self, target_spec) -> None:
        release = getattr(
            getattr(self, "data_strategy", None), "release_unique_key", None
        )
        pending = getattr(self, "_pending_unique_reservations", None)
        if not isinstance(pending, list):
            return
        retained = []
        for spec, key, page_scope in pending:
            if spec == target_spec:
                if callable(release):
                    release(spec, key, page_scope=page_scope)
            else:
                retained.append((spec, key, page_scope))
        pending[:] = retained

    def release_pending_unique_reservations(self) -> None:
        release = getattr(
            getattr(self, "data_strategy", None), "release_unique_key", None
        )
        if callable(release):
            for spec, key, page_scope in reversed(
                getattr(self, "_pending_unique_reservations", [])
            ):
                release(spec, key, page_scope=page_scope)
        pending = getattr(self, "_pending_unique_reservations", None)
        if isinstance(pending, list):
            pending.clear()

    @staticmethod
    def _constraint_repair_candidates(
        spec, protected_codes: set[str],
    ) -> tuple[str, ...]:
        return tuple(
            code for code in (spec.repair_field, *spec.alternate_repair_fields)
            if str(code).lower() not in protected_codes
        )

    def _start_unique_list_capture(self) -> None:
        if getattr(self, "_unique_list_listener", None) is not None:
            return
        if not isinstance(getattr(self, "_unique_list_responses", None), list):
            self._unique_list_responses = []

        def capture(response) -> None:
            self._unique_list_responses.append(response)

        self._unique_list_listener = capture
        on = getattr(self.page, "on", None)
        if callable(on):
            on("response", capture)

    def _stop_unique_list_capture(self, *, page=None) -> None:
        listener = getattr(self, "_unique_list_listener", None)
        if listener is None:
            return
        target_page = page if page is not None else getattr(self, "page", None)
        try:
            remove = getattr(target_page, "remove_listener", None)
            if callable(remove):
                remove("response", listener)
            else:
                off = getattr(target_page, "off", None)
                if callable(off):
                    off("response", listener)
        finally:
            self._unique_list_listener = None

    @staticmethod
    def _unique_spec_key(spec) -> tuple[Any, ...]:
        return (
            str(spec.form_code or "").strip().lower(),
            tuple(str(code or "").strip().lower() for code in spec.field_codes),
        )

    def _wait_for_unique_list_response(
        self, specs: tuple[Any, ...], *, response_start: int = 0,
    ) -> None:
        deadline = time.monotonic() + max(
            1, int(os.getenv("EI_UNIQUE_LIST_TIMEOUT_MS", "10000"))
        ) / 1000
        while time.monotonic() < deadline:
            if all(
                any(self._response_matches_unique_list(response, spec)
                    for response in self._unique_list_responses[response_start:])
                for spec in specs if spec.list_url_includes
            ):
                return
            wait = getattr(self.page, "wait_for_timeout", None)
            if callable(wait):
                wait(100)
            else:
                break
        missing = [
            spec.form_code for spec in specs
            if spec.list_url_includes and not any(
                self._response_matches_unique_list(response, spec)
                for response in self._unique_list_responses[response_start:]
            )
        ]
        if missing:
            raise AssertionError(
                "组合唯一约束缺少真实列表响应，无法证明可用组合："
                + ", ".join(missing)
            )

    @staticmethod
    def _response_matches_unique_list(response, spec) -> bool:
        url = str(getattr(response, "url", "") or "").lower()
        return bool(url) and any(
            token.lower() in url for token in spec.list_url_includes
        )

    @staticmethod
    def _payload_path(payload: Any, path: str) -> Any:
        current = payload
        for part in str(path or "").split("."):
            if not part:
                continue
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @classmethod
    def _unique_record_envelope(cls, payload: Any, spec) -> tuple[list[dict[str, Any]], Any]:
        for path in spec.record_paths:
            value = cls._payload_path(payload, path)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                parent_path = path.rsplit(".", 1)[0] if "." in path else ""
                envelope = cls._payload_path(payload, parent_path) if parent_path else payload
                return list(value), envelope
        return [], None

    @classmethod
    def _records_from_unique_payload(cls, payload: Any, spec) -> list[dict[str, Any]]:
        return cls._unique_record_envelope(payload, spec)[0]

    @staticmethod
    def _request_payload_dict(request) -> dict[str, Any]:
        payload = getattr(request, "post_data_json", None)
        try:
            payload = payload() if callable(payload) else payload
        except Exception:
            payload = None
        return dict(payload) if isinstance(payload, dict) else {}

    @classmethod
    def _request_pagination_value(
        cls, request, keys: tuple[str, ...],
    ) -> int | None:
        payload = cls._request_payload_dict(request)
        query = dict(parse_qsl(urlsplit(str(getattr(request, "url", ""))).query))
        for values in (payload, query):
            for key in keys:
                value = values.get(key)
                if str(value or "").isdigit() and int(value) > 0:
                    return int(value)
        return None

    @staticmethod
    def _pagination_total(envelope: Any) -> int | None:
        if not isinstance(envelope, dict):
            return None
        for key in ("total", "totalCount", "recordsTotal"):
            value = envelope.get(key)
            if str(value or "").isdigit():
                return int(value)
        return None

    @classmethod
    def _unique_record_identity(cls, record: dict[str, Any], spec) -> tuple[Any, ...]:
        business_id = cls._direct_record_business_id(record)
        if business_id:
            return ("id", business_id)
        key = tuple(
            frozenset(cls._record_alias_values(record, spec.aliases_for(code)))
            for code in spec.field_codes
        )
        if not all(key):
            raise AssertionError(
                "组合唯一约束列表记录缺少业务 ID 或完整键字段，"
                "无法证明分页数据完整"
            )
        return ("composite", *key)

    @classmethod
    def _deduplicate_unique_records(
        cls, records: list[dict[str, Any]], spec, *, total: int,
    ) -> list[dict[str, Any]]:
        deduplicated: list[dict[str, Any]] = []
        identities = set()
        for record in records:
            identity = cls._unique_record_identity(record, spec)
            if identity in identities:
                raise AssertionError(
                    "组合唯一约束分页存在重复业务记录，无法证明占用快照完整"
                )
            identities.add(identity)
            deduplicated.append(record)
        if len(deduplicated) != total:
            raise AssertionError(
                f"组合唯一约束分页读取不完整：{len(deduplicated)}/{total}"
            )
        return deduplicated

    def _complete_unique_list_records(self, response, spec) -> list[dict[str, Any]]:
        if not bool(getattr(response, "ok", False)):
            raise AssertionError(
                f"组合唯一约束列表接口失败：{getattr(response, 'url', '')}"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise AssertionError("组合唯一约束列表响应不是有效 JSON") from exc
        records, envelope = self._unique_record_envelope(payload, spec)
        total = self._pagination_total(envelope)
        request = getattr(response, "request", None)
        if total is None:
            raise AssertionError(
                "组合唯一约束列表响应缺少总数，无法证明分页数据完整"
            )
        if len(records) >= total:
            return self._deduplicate_unique_records(records, spec, total=total)
        page_size = self._request_pagination_value(
            request, ("pageSize", "size", "limit", "rows")
        )
        if page_size is None:
            raise AssertionError(
                "组合唯一约束列表存在分页但请求缺少 pageSize，无法完整读取"
            )
        required_pages = (total + page_size - 1) // page_size
        max_pages = max(1, int(os.getenv("EI_UNIQUE_LIST_MAX_PAGES", "100")))
        if required_pages > max_pages:
            raise AssertionError(
                f"组合唯一约束列表需要读取 {required_pages} 页，"
                f"超过安全上限 {max_pages}"
            )
        all_records = []
        for page_number in range(1, required_pages + 1):
            replay = self._replay_unique_list_request(response, page_number)
            if replay is None:
                raise AssertionError("组合唯一约束分页请求未返回响应")
            if not bool(getattr(replay, "ok", False)):
                raise AssertionError(
                    f"组合唯一约束分页接口失败：HTTP {getattr(replay, 'status', 0)} "
                    f"{getattr(replay, 'url', '')}"
                )
            try:
                replay_payload = replay.json()
            except Exception as exc:
                raise AssertionError("组合唯一约束分页响应不是有效 JSON") from exc
            page_records, replay_envelope = self._unique_record_envelope(
                replay_payload, spec
            )
            replay_total = self._pagination_total(replay_envelope)
            if replay_total != total:
                raise AssertionError(
                    "组合唯一约束分页期间总数变化，无法证明占用快照完整"
                )
            if not page_records:
                raise AssertionError("组合唯一约束分页返回空记录，无法证明占用快照完整")
            all_records.extend(page_records)
        return self._deduplicate_unique_records(all_records, spec, total=total)

    def _replay_unique_list_request(self, response, page_number: int):
        request = getattr(response, "request", None)
        if request is None:
            return None
        request_context = getattr(self.page, "request", None)
        if request_context is None:
            return None
        url = str(getattr(request, "url", "") or getattr(response, "url", ""))
        payload = None
        try:
            payload = getattr(request, "post_data_json", None)
            payload = payload() if callable(payload) else payload
        except Exception:
            payload = None
        if isinstance(payload, dict):
            body = dict(payload)
            page_key = next(
                (key for key in ("pageNum", "currPage", "page", "current") if key in body),
                "pageNum",
            )
            body[page_key] = page_number
            fetch = getattr(request_context, "fetch", None)
            if callable(fetch):
                return fetch(request, data=body)
            return None
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        page_key = next(
            (key for key in ("pageNum", "currPage", "page", "current") if key in query),
            "pageNum",
        )
        query[page_key] = str(page_number)
        fetch = getattr(request_context, "fetch", None)
        return fetch(request, params=urlencode(query)) if callable(fetch) else None

    @staticmethod
    def _record_alias_values(record: dict[str, Any], aliases: tuple[str, ...]) -> set[str]:
        result = set()
        for alias in aliases:
            value = record.get(alias)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                normalized = ModuleSmokeDriver._normalize_record_text(value)
                if normalized:
                    result.add(normalized)
        return result

    def _occupied_unique_keys(self, spec) -> list[tuple[frozenset[str], ...]]:
        snapshot = getattr(self, "_unique_occupied_snapshot", {})
        if isinstance(snapshot, dict) and self._unique_spec_key(spec) in snapshot:
            return list(snapshot[self._unique_spec_key(spec)])
        matching = [
            response for response in self._unique_list_responses
            if self._response_matches_unique_list(response, spec)
        ]
        if not matching:
            raise AssertionError(
                "组合唯一约束没有捕获真实列表响应，禁止盲目创建"
            )
        return self._occupied_unique_keys_from_response(matching[-1], spec)

    def _occupied_unique_keys_from_response(
        self, response, spec,
    ) -> list[tuple[frozenset[str], ...]]:
        records = self._complete_unique_list_records(response, spec)
        occupied = []
        for record in records:
            key = tuple(
                frozenset(self._record_alias_values(record, spec.aliases_for(code)))
                for code in spec.field_codes
            )
            if all(key):
                occupied.append(key)
        if len(occupied) != len(records):
            raise AssertionError(
                "组合唯一约束列表记录缺少完整键字段，无法证明可用组合"
            )
        return occupied

    def _dom_fields_by_code(self, scope) -> dict[str, tuple[DomField, str]]:
        try:
            dom_fields = scan_dom_fields(self.page, scope)
        except TypeError:
            dom_fields = scan_dom_fields(self.page)
        result = {}
        for index, dom in enumerate(dom_fields, start=1):
            source_code, source_label, *_ = self._source_for_dom(dom, index)
            stable_code = (
                source_code if self._is_generated_identifier(dom.field_code)
                else dom.field_code
            )
            for code in (stable_code, source_code):
                if code and code not in result:
                    result[code] = (dom, dom.label or source_label or code)
        return result

    def _candidate_alias_values(
        self, spec, values: dict[str, Any], candidate_code: str, candidate_value: Any,
    ) -> tuple[frozenset[str], ...]:
        key = []
        for code in spec.field_codes:
            value = candidate_value if code == candidate_code else values.get(code)
            aliases = {self._normalize_record_text(value)}
            display = self._remembered_display_value(code, value)
            if display:
                aliases.add(self._normalize_record_text(display))
            key.append(frozenset(item for item in aliases if item))
        return tuple(key)

    @staticmethod
    def _unique_key_conflicts(
        candidate: tuple[frozenset[str], ...],
        occupied: list[tuple[frozenset[str], ...]],
    ) -> bool:
        return any(
            all(left & right for left, right in zip(candidate, existing))
            for existing in occupied
        )

    def _repair_declared_unique_constraint(
        self,
        spec,
        candidates: tuple[str, ...],
        submitted: dict[str, Any],
        *,
        source: str,
        protected_codes: set[str],
        message: str = "",
        scope=None,
    ) -> dict[str, Any]:
        if not candidates:
            raise AssertionError(
                "组合唯一约束的所有可修复字段均为 Excel 目标字段，禁止覆盖"
            )
        scope = scope or getattr(self, "_common_form_scope", None)
        if scope is None:
            scope = self.page.locator(DIALOG).last
        self._capture_submitted_display_values(submitted, scope)
        occupied = self._occupied_unique_keys(spec)
        dom_by_code = self._dom_fields_by_code(scope)
        allocator = getattr(self.data_strategy, "allocate_unique_value", None)
        reserve = getattr(
            self.data_strategy, "reserve_unique_key_if_available", None
        )
        release = getattr(self.data_strategy, "release_unique_key", None)
        reserved = getattr(self.data_strategy, "unique_key_is_reserved", None)
        page_scope = self._automation_registry_scope()
        max_candidates = max(
            1, int(os.getenv("EI_UNIQUE_ALLOCATION_ATTEMPTS", "100"))
        )
        failures = []
        current_key = self._candidate_alias_values(spec, submitted, "", None)
        # A declared duplicate save response is authoritative: the list snapshot
        # may be stale or omit a just-created concurrent record, so it cannot
        # justify resubmitting the same key.
        if (
            source != "saveResponse"
            and all(current_key)
            and not self._unique_key_conflicts(current_key, occupied)
        ):
            if not callable(reserve) or reserve(
                spec, current_key, page_scope=page_scope
            ):
                if callable(reserve):
                    self._track_unique_reservation(spec, current_key, page_scope)
                return {}
        for target_code in candidates:
            match = dom_by_code.get(target_code)
            if match is None:
                failures.append(f"{target_code}:字段未渲染")
                continue
            dom, label = match
            definition = FieldDefinition(
                field_code=target_code,
                field_name=label,
                field_type=TYPE_BY_KIND.get(dom.kind, "ElInput-TEXT"),
                required=dom.required,
                readonly=dom.readonly,
                source="declared-unique-constraint",
                props={
                    "domKind": dom.kind,
                    "maxlength": dom.maxlength,
                    "min": dom.minimum,
                    "max": dom.maximum,
                    "step": dom.step,
                },
            )
            old_value = submitted.get(target_code)
            for offset in range(1, max_candidates + 1):
                if dom.kind in {"select", "multi_select", "radio", "checkbox"}:
                    try:
                        options = self._available_choice_values(
                            dom,
                            label,
                            target_code,
                            scope,
                        )
                    except AssertionError:
                        break
                    if offset > len(options):
                        break
                    new_value, option_index = options[offset - 1]
                    constraint = {"kind": "unique", "sequence": offset}
                elif callable(allocator):
                    new_value, constraint = allocator(definition, old_value)
                else:
                    break
                candidate_key = self._candidate_alias_values(
                    spec, submitted, target_code, new_value
                )
                is_reserved = bool(
                    callable(reserved)
                    and reserved(spec, candidate_key, page_scope=page_scope)
                )
                if self._unique_key_conflicts(candidate_key, occupied) or is_reserved:
                    continue
                if callable(reserve) and not reserve(
                    spec, candidate_key, page_scope=page_scope
                ):
                    continue
                if callable(reserve):
                    self._track_unique_reservation(
                        spec, candidate_key, page_scope
                    )
                try:
                    if dom.kind in {"select", "multi_select", "radio", "checkbox"}:
                        actual = self._select_by_label(
                            label,
                            option_index=option_index,
                            field_code=target_code,
                            selector=dom.selector,
                            dom_scope=scope,
                        )
                    else:
                        actual = self.interactor.fill(
                            ResolvedField(definition, dom), new_value, root=scope
                        )
                except Exception:
                    if callable(release):
                        release(spec, candidate_key, page_scope=page_scope)
                    self._pending_unique_reservations = [
                        item for item in self._pending_unique_reservations
                        if item != (spec, candidate_key, page_scope)
                    ]
                    raise
                if actual in (None, "", []) or str(actual) == str(old_value):
                    if callable(release):
                        release(spec, candidate_key, page_scope=page_scope)
                    self._pending_unique_reservations = [
                        item for item in self._pending_unique_reservations
                        if item != (spec, candidate_key, page_scope)
                    ]
                    failures.append(f"{target_code}:没有产生新值")
                    break
                actual_key = self._candidate_alias_values(
                    spec, submitted, target_code, actual
                )
                if actual_key != candidate_key:
                    if callable(release):
                        release(spec, candidate_key, page_scope=page_scope)
                    self._pending_unique_reservations = [
                        item for item in self._pending_unique_reservations
                        if item != (spec, candidate_key, page_scope)
                    ]
                    if self._unique_key_conflicts(actual_key, occupied) or (
                        callable(reserved)
                        and reserved(spec, actual_key, page_scope=page_scope)
                    ) or (
                        callable(reserve)
                        and not reserve(spec, actual_key, page_scope=page_scope)
                    ):
                        failures.append(f"{target_code}:实际选择值占用或无法预留")
                        continue
                    if callable(reserve):
                        self._track_unique_reservation(
                            spec, actual_key, page_scope
                        )
                self._validation_repairs.append({
                    "fieldCode": target_code,
                    "label": definition.field_name,
                    "oldValue": old_value,
                    "validationMessage": message,
                    "constraint": constraint,
                    "newValue": actual,
                    "source": source,
                    "repairResult": "applied",
                })
                return {target_code: actual}
            failures.append(f"{target_code}:没有真实可用组合")
        raise AssertionError(
            "组合唯一约束无法分配未占用值：" + "; ".join(failures)
        )

    def _available_choice_values(
        self, dom: DomField, label: str, field_code: str, scope,
    ) -> list[tuple[str, int]]:
        """Read real options without selecting or changing the current value."""
        dialog = scope
        label_node = dialog.get_by_text(label, exact=True).first
        row = label_node.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),"
            "' purvar_form_item ') or contains(concat(' ',normalize-space(@class),' '),"
            "' el-form-item ')][1]"
        )
        if not row.count():
            raise AssertionError(f"未找到 {label} 的选择控件")
        wrapper = row.locator(
            ".el-select__wrapper,.el-cascader .el-input__wrapper"
        ).first
        if dom.selector:
            scanned = dialog.locator(dom.selector).first
            if scanned.count():
                nested = scanned.locator(
                    ".el-select__wrapper,.el-cascader .el-input__wrapper"
                ).first
                wrapper = nested if nested.count() else wrapper
        if not wrapper.count() or not wrapper.is_visible():
            raise AssertionError(f"未找到 {label} 的选择控件")
        wrapper.click(force=True)
        controls_id = self._select_controls_id(None, wrapper)
        popper = (
            self.page.locator(f"#{controls_id}")
            if controls_id else self.page.locator(".el-popper:visible,.el-popover:visible").last
        )
        options = popper.locator(
            ".el-select-dropdown__item:not(.is-disabled),"
            ".el-cascader-node:not(.is-disabled),.el-tree-node__content"
        )
        result = []
        try:
            for index in range(options.count()):
                option = options.nth(index)
                text = (option.inner_text() or "").strip()
                if option.is_visible() and text and not any(
                    token in text for token in ("请选择", "全部", "暂无", "无数据", "加载")
                ):
                    keyed = option.locator("xpath=ancestor-or-self::*[@data-key][1]")
                    value = keyed.get_attribute("data-key") if keyed.count() else text
                    result.append((str(value or text), index))
        finally:
            try:
                self.page.keyboard.press("Escape")
            except Exception:
                pass
        if not result:
            raise AssertionError(f"{field_code or label} 没有真实可用选项")
        return result

    @staticmethod
    def _duplicate_field_candidates(source_fields, submitted):
        remote_candidates = [
            field for field in source_fields
            if field[0] in submitted and len(field) > 2 and bool(field[2])
        ]
        if len(remote_candidates) == 1:
            return remote_candidates
        name_candidates = [
            field for field in source_fields
            if field[0] in submitted and any(
                token in field[0].lower() or token in field[1]
                for token in ("name", "名称", "账号", "编码")
            )
        ]
        full_name_candidates = [
            field for field in name_candidates
            if "short" not in field[0].lower() and "简称" not in field[1]
        ]
        if len(full_name_candidates) == 1:
            name_candidates = full_name_candidates
        return name_candidates if len(name_candidates) == 1 else []

    def _wait_for_form_scope(self, timeout: int = 15_000):
        """Return only the new form instance that exposes editable controls."""
        deadline = time.monotonic() + timeout / 1000
        saw_dialog = False
        saw_inline = False
        while True:
            try:
                dialogs = self.page.locator(DIALOG)
                for index in range(dialogs.count()):
                    candidate = dialogs.nth(index)
                    if not candidate.is_visible():
                        continue
                    saw_dialog = True
                    controls = candidate.locator(EDITABLE_FORM_CONTROL)
                    if controls.count() and controls.first.is_visible():
                        return self._pin_form_scope(candidate)
            except (AttributeError, KeyError, TypeError):
                # Minimal locator doubles in local contract tests may not expose
                # a collection API. The inline branch below remains compatible.
                pass
            except Exception:
                pass

            try:
                candidates = self.page.locator(INLINE_FORM)
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    if not candidate.is_visible():
                        continue
                    saw_inline = True
                    controls = candidate.locator(EDITABLE_FORM_CONTROL)
                    if controls.count() and controls.first.is_visible():
                        return self._pin_form_scope(candidate)
            except (AttributeError, KeyError, TypeError):
                inline = self.page.locator(INLINE_FORM).last
                try:
                    inline.wait_for(state="visible", timeout=timeout)
                    controls = inline.locator(EDITABLE_FORM_CONTROL)
                    if controls.count() and controls.first.is_visible():
                        return self._pin_form_scope(inline)
                except Exception:
                    pass
            except Exception:
                pass

            if time.monotonic() >= deadline or not hasattr(self.page, "wait_for_timeout"):
                if saw_dialog:
                    raise AssertionError(
                        f"新增弹窗已出现，但在 {timeout // 1000} 秒内未出现可编辑控件"
                    )
                if saw_inline:
                    raise AssertionError(
                        f"页面内嵌表单已出现，但在 {timeout // 1000} 秒内未出现可编辑控件"
                    )
                raise AssertionError("点击新增后没有出现对话框或页面内嵌表单")
            self.page.wait_for_timeout(200)

    def _wait_for_readback_form_scope(self):
        """Prefer the visible edit form that actually exposes editable controls."""
        initial = self._wait_for_form_scope()
        deadline = time.monotonic() + 15
        try:
            while True:
                candidates = self.page.locator(f"{DIALOG},{INLINE_FORM}")
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    controls = candidate.locator(EDITABLE_FORM_CONTROL)
                    if controls.count() and controls.first.is_visible():
                        pinned = self._pin_form_scope(candidate)
                        pinned_controls = pinned.locator(EDITABLE_FORM_CONTROL)
                        if pinned_controls.count() and pinned_controls.first.is_visible():
                            return pinned
                if time.monotonic() >= deadline or not hasattr(self.page, "wait_for_timeout"):
                    return initial
                self.page.wait_for_timeout(200)
        except (AttributeError, KeyError, TypeError):
            # Local contract tests may inject minimal locator doubles.
            return initial

    def _pin_form_scope(self, scope):
        """Return a selector bound to the concrete form DOM node, not its position."""
        if not hasattr(scope, "evaluate"):
            return scope
        marker = f"ei-module-form-{uuid.uuid4().hex}"
        attribute = "data-ei-module-form-scope"
        try:
            scope.evaluate(
                "(node, data) => node.setAttribute(data.attribute, data.marker)",
                {"attribute": attribute, "marker": marker},
            )
            pinned = self.page.locator(f'[{attribute}="{marker}"]').first
            if pinned.count() == 1 and pinned.is_visible():
                return pinned
        except Exception as exc:
            raise AssertionError("无法为当前表单建立稳定 DOM 实例定位") from exc
        raise AssertionError("当前表单 DOM 实例标识未唯一命中")

    def _wait_for_add_button(self, timeout: int = 15_000):
        add = self.page.locator(ADD_BUTTON).first
        try:
            add.wait_for(state="visible", timeout=timeout)
            if not add.count() or not add.is_visible() or not add.is_enabled():
                raise RuntimeError("add entry is not actionable")
        except Exception as exc:
            raise AssertionError(
                "页面要求执行新增，但没有找到可见且可用的新增/添加/新建/创建入口"
            ) from exc
        return add

    def _wait_for_form_ready(self, scope, timeout: int = 15_000) -> None:
        if hasattr(scope, "is_visible") and not scope.is_visible():
            raise AssertionError("新增表单在出现可编辑控件前已关闭或被替换")
        loading = scope.locator(
            ".el-loading-mask:visible,.ant-spin-spinning:visible,"
            "[aria-busy='true']:visible,[data-loading='true']:visible"
        ).first
        try:
            if loading.count() and loading.is_visible():
                loading.wait_for(state="hidden", timeout=timeout)
            controls = scope.locator(EDITABLE_FORM_CONTROL).first
            controls.wait_for(state="visible", timeout=timeout)
        except Exception as exc:
            if hasattr(scope, "is_visible") and not scope.is_visible():
                raise AssertionError("新增表单在出现可编辑控件前已关闭或被替换") from exc
            raise AssertionError(
                f"新增表单已打开，但表单控件在 {timeout // 1000} 秒内未加载完成"
            ) from exc

    def _save_button(self, scope, operation: str = ""):
        if operation:
            exact = re.compile(rf"^\s*{re.escape(operation)}\s*$")
            command = scope.locator(
                ".el-dialog__footer button:visible,.dialog-footer button:visible,"
                "footer button:visible"
            ).filter(has_text=exact).last
            if command.count() and command.is_visible():
                return command
            return command
        scoped = scope.locator("button:has-text('保存'),button:has-text('确定')").last
        if scoped.count() and scoped.is_visible():
            return scoped
        return self.page.locator(SAVE_BUTTON + ",button:has-text('保存')").last

    def _upload_default_attachments(self, dialog) -> int:
        path = self.default_upload_file
        if not path:
            return 0
        path = Path(path)
        if not path.is_file():
            raise AssertionError(f"默认附件不存在：{path}")
        inputs = dialog.locator('input[type="file"]:not([disabled])')
        uploaded = 0
        existing = 0
        tracker = self._start_attachment_lifecycle_tracking()
        try:
            for index in range(inputs.count()):
                file_input = inputs.nth(index)
                if self._file_input_has_failure(file_input):
                    raise AssertionError(f"附件控件报告上传失败：{path.name}")
                if self._file_input_has_value(file_input):
                    existing += 1
                    continue
                file_input.set_input_files(str(path))
                uploaded += 1
                self._wait_for_file_upload(file_input, path.name, tracker=tracker)
            if uploaded:
                self._wait_for_attachment_lifecycle(
                    tracker, phase="默认附件上传"
                )
            self.last_attachment_report = AttachmentCompletionReport(
                status="completed" if uploaded else "existing",
                uploaded=uploaded,
                existing=existing,
                pending=len(tracker.pending),
                requests_observed=len(tracker.requests),
                classification="completed" if uploaded else "existing",
                lifecycle=tracker.events[-20:],
            )
        except Exception as exc:
            classification = (
                getattr(tracker, "final_classification", "")
                or self._attachment_timeout_classification(tracker)
            )
            if not tracker.failures:
                self._capture_attachment_failure(
                    tracker,
                    classification=classification,
                    message=f"附件超时待诊断（前端渲染等待超时）：{exc}",
                )
            self.last_attachment_report = AttachmentCompletionReport(
                status="failed",
                uploaded=uploaded,
                existing=existing,
                pending=len(tracker.pending),
                requests_observed=len(tracker.requests),
                errors=[str(exc)],
                classification=classification,
                lifecycle=tracker.events[-20:],
            )
            if classification in {
                "network_request_timeout",
                "backend_task_processing_timeout",
                "frontend_render_timeout",
            }:
                raise AssertionError(
                    f"附件超时待诊断（{classification}）：{path.name}: {exc}"
                ) from exc
            raise AssertionError(f"附件/存储服务明确失败：{path.name}: {exc}") from exc
        finally:
            self._stop_attachment_lifecycle_tracking(tracker)
        return uploaded

    @classmethod
    def _is_attachment_lifecycle_request(cls, request) -> bool:
        method = str(getattr(request, "method", "GET")).upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return False
        path = urlsplit(str(getattr(request, "url", "") or "")).path.lower()
        if any(
            token in path
            for token in (
                "/foundation/oss/",
                "/foundation/commfile/",
                "/attachment/",
                "/attachments/",
                "/tenantform/saveformdata",
                "/tenantform/updateformdata",
            )
        ):
            return True
        compact = re.sub(r"[^a-z0-9]+", "", path)
        return any(
            token in compact
            for token in (
                "upload",
                "putfileattach",
                "mergegroupfiles",
                "savefilebatch",
                "selectcommfilelist",
                "filelists",
            )
        )

    @staticmethod
    def _safe_attachment_url(value: Any) -> str:
        parts = urlsplit(str(value or ""))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    @staticmethod
    def _safe_attachment_response(response) -> Any:
        """Capture a bounded response summary without retaining credentials or payloads."""
        if response is None:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        if isinstance(payload, dict):
            return {
                key: str(payload[key])[:500]
                for key in ("code", "status", "message", "msg", "success", "error")
                if key in payload
            }
        return str(payload)[:500]

    def _attachment_lifecycle_snapshot(
        self, tracker: AttachmentLifecycleTracker, *, classification: str,
    ) -> dict[str, Any]:
        return {
            "classification": classification,
            "observedRequests": len(tracker.requests),
            "pendingRequests": len(tracker.pending),
            "backendPending": tracker.backend_pending[-10:],
            "events": tracker.events[-20:],
        }

    def _capture_attachment_failure(
        self, tracker: AttachmentLifecycleTracker, *, classification: str,
        message: str,
    ) -> None:
        tracker.final_classification = classification
        if getattr(self.page, "_ei_ui_failure_evidence", None) is not None:
            return
        capture_failure_evidence(
            self.page,
            message,
            diagnostics={"attachmentLifecycle": self._attachment_lifecycle_snapshot(
                tracker, classification=classification,
            )},
        )

    @staticmethod
    def _attachment_response_is_pending(response) -> bool:
        if not isinstance(response, dict):
            return False
        values = [
            response.get(key)
            for key in ("status", "state", "taskStatus", "jobStatus")
        ]
        return any(
            str(value or "").strip().lower() in {
                "pending", "processing", "queued", "running", "in_progress",
            }
            for value in values
        )

    @staticmethod
    def _attachment_timeout_classification(
        tracker: AttachmentLifecycleTracker,
    ) -> str:
        if tracker.failures:
            return "attachment_request_failed"
        if tracker.backend_pending:
            return "backend_task_processing_timeout"
        return "frontend_render_timeout"

    def _start_attachment_lifecycle_tracking(self) -> AttachmentLifecycleTracker:
        tracker = AttachmentLifecycleTracker()
        if not hasattr(self.page, "on") or not hasattr(self.page, "remove_listener"):
            return tracker

        def request_started(request) -> None:
            if not self._is_attachment_lifecycle_request(request):
                return
            tracker.requests.append(request)
            tracker.pending[id(request)] = request
            tracker.last_activity = time.monotonic()
            tracker.events.append({
                "event": "request_started",
                "method": str(getattr(request, "method", "?")),
                "url": self._safe_attachment_url(getattr(request, "url", "")),
                "startedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            })

        def request_finished(request) -> None:
            if id(request) not in tracker.pending:
                return
            tracker.pending.pop(id(request), None)
            tracker.last_activity = time.monotonic()
            try:
                response = request.response
                response = response() if callable(response) else response
            except Exception:
                response = None
            if response is not None:
                tracker.responses.append(response)
                status = getattr(response, "status", None)
                tracker.events.append({
                    "event": "response_finished",
                    "method": str(getattr(request, "method", "?")),
                    "url": self._safe_attachment_url(getattr(request, "url", "")),
                    "httpStatus": status,
                    "response": self._safe_attachment_response(response),
                    "finishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                })
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                if self._attachment_response_is_pending(payload):
                    tracker.backend_pending.append({
                        "method": str(getattr(request, "method", "?")),
                        "url": self._safe_attachment_url(getattr(request, "url", "")),
                        "response": self._safe_attachment_response(response),
                    })
                if getattr(response, "ok", True) is False or (
                    isinstance(status, int) and status >= 400
                ):
                    path = urlsplit(str(getattr(request, "url", "") or "")).path
                    tracker.failures.append(
                        f"{getattr(request, 'method', '?')} {path} HTTP {status}"
                    )

        def request_failed(request) -> None:
            if id(request) not in tracker.pending:
                return
            tracker.pending.pop(id(request), None)
            tracker.last_activity = time.monotonic()
            failure = getattr(request, "failure", "") or "<unknown>"
            if callable(failure):
                try:
                    failure = failure()
                except Exception:
                    failure = "<unreadable>"
            path = urlsplit(str(getattr(request, "url", "") or "")).path
            tracker.failures.append(
                f"{getattr(request, 'method', '?')} {path}: {failure}"
            )
            tracker.events.append({
                "event": "request_failed",
                "method": str(getattr(request, "method", "?")),
                "url": self._safe_attachment_url(getattr(request, "url", "")),
                "reason": str(failure)[:500],
                "finishedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            })

        tracker.callbacks = {
            "request": request_started,
            "requestfinished": request_finished,
            "requestfailed": request_failed,
        }
        for event, callback in tracker.callbacks.items():
            self.page.on(event, callback)
        tracker.enabled = True
        return tracker

    def _wait_for_attachment_lifecycle(
        self,
        tracker: AttachmentLifecycleTracker,
        *,
        phase: str,
        timeout_ms: int = 30_000,
        quiet_ms: int = 500,
    ) -> None:
        if not tracker.enabled or not tracker.requests:
            return
        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        quiet_seconds = max(0, quiet_ms) / 1000
        while time.monotonic() < deadline:
            if tracker.failures:
                break
            if (
                not tracker.pending
                and time.monotonic() - tracker.last_activity >= quiet_seconds
            ):
                break
            self.page.wait_for_timeout(100)
        else:
            message = (
                f"{phase}请求未在 {timeout_ms / 1000:g} 秒内完成："
                f"pending={len(tracker.pending)}"
            )
            classification = (
                "backend_task_processing_timeout"
                if tracker.backend_pending else "network_request_timeout"
            )
            self._capture_attachment_failure(
                tracker, classification=classification, message=message,
            )
            raise AssertionError(f"附件超时待诊断（{classification}）：{message}")

        if tracker.failures:
            message = f"{phase}请求失败：{' | '.join(tracker.failures[-3:])}"
            self._capture_attachment_failure(
                tracker, classification="attachment_request_failed", message=message,
            )
            raise AssertionError(f"附件/存储服务明确失败：{message}")
        for response in tracker.responses:
            response_id = id(response)
            if response_id in tracker.validated_responses:
                continue
            tracker.validated_responses.add(response_id)
            try:
                payload = response.json()
            except Exception:
                continue
            try:
                self._assert_business_success(payload, phase)
            except AssertionError as exc:
                path = urlsplit(str(getattr(response, "url", "") or "")).path
                message = f"{phase}请求业务失败：{path}: {exc}"
                self._capture_attachment_failure(
                    tracker, classification="attachment_backend_rejected", message=message,
                )
                raise AssertionError(f"附件/存储服务明确失败：{message}") from exc
        print(
            f"ATTACHMENT_LIFECYCLE phase={phase} "
            f"observed={len(tracker.requests)} pending={len(tracker.pending)}",
            flush=True,
        )

    def _stop_attachment_lifecycle_tracking(
        self, tracker: AttachmentLifecycleTracker,
    ) -> None:
        if not tracker.enabled:
            return
        for event, callback in tracker.callbacks.items():
            self.page.remove_listener(event, callback)
        tracker.enabled = False

    def _wait_for_file_upload(
        self, file_input, file_name: str,
        *, tracker: AttachmentLifecycleTracker | None = None,
    ) -> None:
        if not hasattr(file_input, "element_handle"):
            self.page.wait_for_timeout(800)
            return
        handle = file_input.element_handle()
        if handle is None:
            raise AssertionError("附件输入控件在上传期间已失效")
        try:
            self.page.wait_for_function(
                """({input, fileName}) => {
                const el = input;
                const scope = el.closest('.el-form-item,.purvar_form_item,.ant-form-item') || el.parentElement;
                if (!scope) return false;
                const rows = [...new Set(Array.from(scope.querySelectorAll(
                    '.el-upload-list__item,.ant-upload-list-item,' +
                    '[data-upload-name],[data-file-name]'
                )).map(node => node.closest(
                    '.el-upload-list__item,.ant-upload-list-item'
                ) || node))];
                const rowNames = row => [
                    row.getAttribute('title'),
                    row.getAttribute('data-upload-name'),
                    row.getAttribute('data-file-name'),
                    ...Array.from(row.querySelectorAll(
                        '.el-upload-list__item-name,.ant-upload-list-item-name,' +
                        '[data-upload-name],[data-file-name],[class*="file-name"],[class*="filename"]'
                    )).flatMap(node => [
                        node.getAttribute('title'),
                        node.getAttribute('data-upload-name'),
                        node.getAttribute('data-file-name'),
                        node.textContent,
                    ]),
                ].map(value => String(value || '').trim()).filter(Boolean);
                const targetRows = rows.filter(row => rowNames(row).includes(fileName));
                if (!targetRows.length) return false;
                const hasState = (row, selector) => row.matches(selector) || !!row.querySelector(selector);
                const failedSelector =
                    '.el-upload-list__item.is-fail,.ant-upload-list-item-error,[class*="upload-error"]';
                if (targetRows.some(row => hasState(row, failedSelector))) return 'failed';
                const pendingSelector =
                    '.el-upload-list__item.is-uploading,.el-progress,' +
                    '.ant-upload-list-item-uploading,[class*="uploading"]';
                if (targetRows.some(row => hasState(row, pendingSelector))) return false;
                const successSelector =
                    '.el-upload-list__item.is-success,.ant-upload-list-item-done,' +
                    '.el-icon--upload-success,' +
                    '[data-upload-status="success"],[data-status="done"],[class*="upload-success"]';
                return targetRows.some(row => hasState(row, successSelector));
            }""",
                arg={"input": handle, "fileName": file_name},
                timeout=30_000,
            )
        except PlaywrightTimeoutError as exc:
            classification = self._attachment_timeout_classification(tracker) if tracker else (
                "frontend_render_timeout"
            )
            if tracker is not None:
                self._capture_attachment_failure(
                    tracker,
                    classification=classification,
                    message=f"附件超时待诊断（{classification}）：{file_name}",
                )
            raise AssertionError(
                f"附件组件在 30 秒内未完成：{file_name}; classification={classification}"
            ) from exc
        failed = file_input.evaluate("""el => {
            const scope = el.closest('.el-form-item,.purvar_form_item,.ant-form-item') || el.parentElement;
            return !!scope?.querySelector(
                '.el-upload-list__item.is-fail,.ant-upload-list-item-error,[class*="upload-error"]'
            );
        }""")
        if failed:
            raise AssertionError(f"附件控件报告上传失败：{file_name}")

    @staticmethod
    def _file_input_has_value(file_input) -> bool:
        return bool(file_input.evaluate("""el => {
            if (el.files && el.files.length > 0) return true;
            const scope = el.closest('.el-form-item,.purvar_form_item,.ant-form-item') || el.parentElement;
            return !!scope?.querySelector(
                '.el-upload-list__item.is-success,' +
                '.ant-upload-list-item.ant-upload-list-item-done,' +
                '.el-upload-list__item .el-icon--upload-success,' +
                '[data-upload-status="success"],[data-status="done"]'
            );
        }"""))

    @staticmethod
    def _file_input_has_failure(file_input) -> bool:
        if not hasattr(file_input, "evaluate"):
            return False
        return bool(file_input.evaluate("""el => {
            const scope = el.closest('.el-form-item,.purvar_form_item,.ant-form-item') || el.parentElement;
            return !!scope?.querySelector(
                '.el-upload-list__item.is-fail,.ant-upload-list-item-error,[class*="upload-error"]'
            );
        }"""))

    @staticmethod
    def _assert_business_success(payload: Any, operation: str = "保存") -> None:
        if not isinstance(payload, dict):
            return
        code = payload.get("code", payload.get("status"))
        success = payload.get("success")
        errors = payload.get("errors")
        has_errors = isinstance(errors, list) and bool(errors)
        if success is False or has_errors or (code is not None and str(code).lower() not in {"0", "1", "200", "success", "true"}):
            message = payload.get("message") or payload.get("msg") or payload
            raise AssertionError(f"{operation}接口返回业务失败：code={code!r}, message={message!r}")

    @classmethod
    def _find_detail_response(cls, responses, save_response, business_id: str):
        tokens = (
            "/detail", "detail/", "getdetail", "getbyid", "querybyid", "/info",
            "/projappinfo/listpage", "detailfiletype",
        )
        eligible = [
            response for response in responses
            if response is not save_response and response.ok
            and getattr(response.request, "resource_type", "xhr") in {"xhr", "fetch"}
            and "json" in str(getattr(response, "headers", {}).get("content-type", "application/json")).lower()
        ]
        associated = [
            response for response in eligible
            if cls._response_associates_business_id(response, business_id)
        ]
        if associated:
            return associated[-1]
        candidates = [
            response for response in eligible
            if any(token in response.url.lower() for token in tokens)
        ]
        return candidates[-1] if candidates else None

    @classmethod
    def _find_associated_detail_response(
        cls, responses, save_response, business_id: str,
    ):
        """Return only JSON traffic that exactly identifies the saved record."""
        candidates = [
            response for response in responses
            if response is not save_response
            and response.ok
            and getattr(response.request, "resource_type", "xhr") in {"xhr", "fetch"}
            and "json" in str(
                getattr(response, "headers", {}).get(
                    "content-type", "application/json"
                )
            ).lower()
            and cls._response_associates_business_id(response, business_id)
        ]
        return candidates[-1] if candidates else None

    @classmethod
    def _saved_record_identity_payload(
        cls, responses, save_body: Any, business_id: str,
    ) -> Any:
        """Retain response evidence for later strict delete-row association."""
        if business_id:
            for response in reversed(list(responses)):
                if response is None or not getattr(response, "ok", False):
                    continue
                if not cls._response_associates_business_id(response, business_id):
                    continue
                try:
                    return response.json()
                except Exception:
                    continue
        return save_body

    @classmethod
    def _same_resource_detail_url(
        cls, save_url: str, business_id: str,
    ) -> str:
        """Derive ``detail/{id}`` only from a known CRUD action resource path."""
        normalized_id = cls._normalize_record_text(business_id)
        if not normalized_id:
            return ""
        parts = urlsplit(str(save_url or ""))
        segments = [segment for segment in parts.path.split("/") if segment]
        if not segments:
            return ""
        action_tokens = {
            token.lstrip("/")
            for token in cls.BUSINESS_MUTATION_URL_TOKENS
        }
        action_index = next(
            (
                index
                for index in range(len(segments) - 1, -1, -1)
                if segments[index].lower() in action_tokens
            ),
            -1,
        )
        if action_index < 1:
            return ""
        resource_segments = segments[:action_index]
        if not resource_segments:
            return ""
        detail_path = "/" + "/".join(
            [*resource_segments, "detail", quote(normalized_id, safe="")]
        )
        return urlunsplit((parts.scheme, parts.netloc, detail_path, "", ""))

    def _request_same_resource_detail_response(
        self, save_response, business_id: str,
    ):
        """Fetch one exact-ID detail candidate with the current browser login."""
        detail_url = self._same_resource_detail_url(
            getattr(save_response, "url", ""), business_id
        )
        if not detail_url:
            return None
        request_context = getattr(self.page, "request", None)
        get = getattr(request_context, "get", None)
        if not callable(get):
            return None
        try:
            response = get(detail_url)
        except Exception:
            return None
        if not getattr(response, "ok", False):
            return None
        headers = dict(getattr(response, "headers", {}) or {})
        content_type = str(headers.get("content-type", "")).lower()
        if "json" not in content_type:
            return None
        try:
            payload = response.json()
        except Exception:
            return None
        wrapped = _RequestContextDetailResponse(
            response, detail_url, payload, headers
        )
        if not self._request_contains_business_id(wrapped.request, business_id):
            return None
        normalized_id = self._normalize_record_text(business_id)
        if normalized_id not in self._record_scalar_texts(payload):
            return None
        direct_ids = {
            self._direct_record_business_id(record)
            for record in self._collect_dicts(payload)
            if self._direct_record_business_id(record)
        }
        if direct_ids and normalized_id not in direct_ids:
            return None
        return wrapped

    @classmethod
    def _response_associates_business_id(cls, response, business_id: str) -> bool:
        normalized_id = cls._normalize_record_text(business_id)
        if not normalized_id:
            return False
        if cls._request_contains_business_id(response.request, normalized_id):
            return True
        try:
            payload = response.json()
        except Exception:
            return False
        return normalized_id in cls._record_scalar_texts(payload)

    def _reset_nested_evidence(self) -> None:
        """Start a new physical form without inherited child-table assertions."""
        self._nested_evidence = []

    def _remember_nested_evidence(
        self,
        *,
        section: str,
        field: str,
        code: str,
        value: Any,
        submitted_key: str,
    ) -> None:
        """Track one child-table field by stable key instead of append-only text."""
        if value in (None, ""):
            return
        stable = self._unique_nested_source_field(code=code, label=field)
        if stable is not None:
            code = stable[0]
            field = stable[1] or field
            submitted_key = code
        item = {
            "section": str(section),
            "field": str(field or code),
            "code": str(code or ""),
            "submitted_key": str(submitted_key or ""),
            "value": str(value),
        }
        for existing in self._nested_evidence:
            if (
                existing.get("section") == item["section"]
                and (
                    existing.get("submitted_key") == item["submitted_key"]
                    or (
                        existing.get("code")
                        and existing.get("code") == item["code"]
                    )
                )
            ):
                existing.update(item)
                return
        self._nested_evidence.append(item)

    @staticmethod
    def _nested_leaf_code(code: str) -> str:
        return str(code or "").replace(".*.", ".").split(".")[-1]

    def _nested_submitted_value(
        self, item: dict[str, str], submitted: dict[str, Any] | None,
    ) -> Any:
        if not submitted:
            return None
        section = item.get("section", "")
        code = item.get("code", "")
        submitted_key = item.get("submitted_key", "")
        stable = self._unique_nested_source_field(
            code=code,
            label=item.get("field", ""),
        )
        if stable is not None:
            code = stable[0]
            submitted_key = code
            item["code"] = code
            item["submitted_key"] = code
            item["field"] = stable[1] or item.get("field", "")
        leaf = self._nested_leaf_code(code)
        candidates = [
            submitted_key,
            code,
            f"{section}.{code}" if section and code else "",
            leaf,
            f"{section}.{leaf}" if section and leaf else "",
        ]
        for key in candidates:
            if key and key in submitted:
                return submitted[key]
        normalized_code = code.replace(".*.", ".")
        for key, value in submitted.items():
            text = str(key)
            normalized_key = text.replace(".*.", ".")
            if (
                (submitted_key and text == submitted_key)
                or (code and text.endswith(f".{code}"))
                or (normalized_code and normalized_key.endswith(f".{normalized_code}"))
                or (leaf and text.endswith(f".{leaf}"))
            ):
                return value
        return None

    @staticmethod
    def _is_nested_source_code(code: str) -> bool:
        normalized = re.sub(
            r"\$\{[^}]+\}", "*", str(code or "").strip().strip("`")
        )
        return ".*." in normalized

    def _unique_nested_source_field(
        self,
        *,
        code: str = "",
        label: str = "",
        dom=None,
    ) -> tuple[str, str, bool] | None:
        """Resolve a child-table field without positional source fallback."""

        candidates = [
            field
            for field in getattr(self, "source_fields", [])
            if field and self._is_nested_source_code(field[0])
        ]
        if code and not self._is_generated_identifier(code):
            code_matches = [
                field
                for field in candidates
                if self._source_code_matches_runtime(field[0], code)
            ]
            unique_codes = {str(field[0]).lower() for field in code_matches}
            if len(unique_codes) == 1:
                return code_matches[0]

        normalized_label = self._normalize_identity_label(label)
        if normalized_label:
            label_matches = [
                field
                for field in candidates
                if self._normalize_identity_label(field[1]) == normalized_label
            ]
            unique_codes = {str(field[0]).lower() for field in label_matches}
            if len(unique_codes) == 1:
                return label_matches[0]

        if dom is not None:
            semantic_matches = [
                field
                for field in candidates
                if self._semantic_numeric_source_identity_safe(
                    dom, field[0], field[1]
                )
            ]
            unique_codes = {str(field[0]).lower() for field in semantic_matches}
            if len(unique_codes) == 1:
                return semantic_matches[0]
        return None

    def _runtime_identity_for_nested_dom(
        self, dom, index: int
    ) -> tuple[str, str, bool, str, str, bool]:
        stable = self._unique_nested_source_field(
            code=dom.field_code,
            label=dom.label,
            dom=dom,
        )
        if stable is None:
            code = dom.field_code or f"nested_field_{index}"
            return code, dom.label or code, bool(dom.qcc_remote), code, dom.label, False
        source_code, source_label, *rest = stable
        source_qcc = bool(rest[0]) if rest else False
        return (
            source_code,
            source_label or dom.label or source_code,
            source_qcc,
            source_code,
            source_label,
            True,
        )

    def _nested_submitted_key(self, section: str, code: str) -> str:
        stable = self._unique_nested_source_field(code=code)
        if stable is not None:
            return stable[0]
        return f"{section}.{code}"

    def _current_nested_evidence(
        self, submitted: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        evidence: list[dict[str, str]] = []
        for item in self._nested_evidence:
            current = self._nested_submitted_value(item, submitted)
            value = item.get("value", "")
            if current not in (None, "", []):
                value = str(current)
                item["value"] = value
            if str(value).strip():
                evidence.append({**item, "value": str(value)})
        return evidence

    def _prepare_implicit_required_nested_baselines(self, scope) -> dict[str, Any]:
        """Fill empty nested detail tables that silently gate form submission."""

        nested_submitted: dict[str, Any] = {}
        for section in self._implicit_required_nested_sections(scope):
            nested_submitted.update(
                self._prepare_nested_add_baseline(scope, section, implicit=True)
            )
        return nested_submitted

    def _implicit_required_nested_sections(self, scope) -> list[str]:
        try:
            candidates = scope.evaluate(
                """root => {
                    const visible = (el) => {
                        if (!(el instanceof HTMLElement)) return false;
                        if (el.closest('[hidden], [aria-hidden="true"]')) return false;
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none' && style.visibility !== 'hidden' &&
                            rect.width > 0 && rect.height > 0;
                    };
                    const textOf = (el) =>
                        (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
                    const addRe = /^(新增|添加|新建|创建)$/;
                    const results = [];
                    for (const node of [...root.querySelectorAll('*')].filter(visible)) {
                        const text = textOf(node);
                        if (!text || text.length > 40 || text.includes('\\n')) continue;
                        let current = node.parentElement;
                        for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                            if (!visible(current)) continue;
                            const hasAdd = [...current.querySelectorAll('button')]
                                .some((button) => visible(button) && addRe.test(textOf(button)));
                            const hasTable = !!current.querySelector('.el-table,table,.ant-table');
                            if (!hasAdd || !hasTable) continue;
                            const rows = [...current.querySelectorAll(
                                '.el-table__body-wrapper .el-table__row,tbody tr'
                            )].filter((row) => visible(row) && !textOf(row).includes('暂无数据'));
                            if (rows.length === 0) results.push(text);
                            break;
                        }
                    }
                    return [...new Set(results)];
                }"""
            )
        except Exception:
            return []
        result: list[str] = []
        for candidate in candidates if isinstance(candidates, list) else []:
            title = str(candidate).strip()
            if title and IMPLICIT_REQUIRED_NESTED_SECTION_RE.search(title):
                result.append(title)
        return result

    def _prepare_nested_add_baseline(
        self, scope, section: str, *, implicit: bool = False,
    ) -> dict[str, Any]:
        title = scope.get_by_text(str(section), exact=True).last
        title.wait_for(state="visible", timeout=10_000)
        title.scroll_into_view_if_needed()
        section_scope = self._nested_section_scope(title, section)
        collection_spec = self._configured_collection_for_section(section)
        if collection_spec is not None:
            rows = section_scope.locator(collection_spec.item_selector)
        else:
            primary_rows = section_scope.locator(
                ".el-table__body-wrapper .el-table__row"
            )
            rows = primary_rows if primary_rows.count() else section_scope.locator(
                ".el-table__row"
            )
        before_rows = rows.count()
        button = section_scope.get_by_role(
            "button", name=re.compile(r"^(新增|添加|新建|创建)$")
        ).first
        button.wait_for(state="visible", timeout=10_000)
        button.click()
        after_rows = self._wait_for_row_count_change(rows, before_rows, increase=True)
        if after_rows <= before_rows:
            prefix = "隐式必填明细" if implicit else "嵌套操作"
            raise AssertionError(
                f"{prefix}“{section}”已点击新增，但表格行数未增加："
                f"before={before_rows}, after={after_rows}"
            )
        created_row = rows.nth(after_rows - 1)
        row_submitted = (
            self._fill_configured_collection_row(
                collection_spec, created_row, after_rows - 1
            )
            if collection_spec is not None
            else self._fill_dialog(dom_scope=created_row)
        )
        row_fields = scan_dom_fields(self.page, created_row)
        if not row_fields:
            prefix = "隐式必填明细" if implicit else "嵌套操作"
            raise AssertionError(
                f"{prefix}“{section}”新增了表格行，但没有发现可编辑字段"
            )
        empty_required = [
            field.label or field.field_code
            for field in row_fields
            if field.required and not self._dom_field_has_value(field)
        ]
        if self._fill_failures or empty_required:
            details = [*self._fill_failures, *empty_required]
            prefix = "隐式必填明细" if implicit else "嵌套操作"
            raise AssertionError(
                f"{prefix}“{section}”新增行未填写完整：" + "; ".join(details)
            )
        if not row_submitted:
            prefix = "隐式必填明细" if implicit else "嵌套操作"
            raise AssertionError(f"{prefix}“{section}”新增行没有形成提交字段证据")
        nested_submitted = {
            self._nested_submitted_key(str(section), code): value
            for code, value in row_submitted.items()
        }
        if collection_spec is not None:
            self._remember_configured_collection_evidence(
                collection_spec, str(section), after_rows - 1, row_submitted
            )
        else:
            for index, field in enumerate(row_fields, 1):
                code, label, *_ = self._runtime_identity_for_nested_dom(field, index)
                value = row_submitted.get(code)
                if field.kind in {"text", "textarea", "number"} and value not in (None, ""):
                    self._remember_nested_evidence(
                        section=str(section),
                        field=label or code,
                        code=code,
                        value=value,
                        submitted_key=self._nested_submitted_key(str(section), code),
                    )
        return nested_submitted

    def _prepare_nested_operation(self, scope) -> dict[str, Any]:
        nested_submitted: dict[str, Any] = {}
        raw_paths = os.getenv("EI_ACTION_PATHS_JSON", "").strip()
        if raw_paths:
            try:
                paths = json.loads(raw_paths)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"EI_ACTION_PATHS_JSON 不是有效 JSON：{exc}") from exc
        else:
            raw_path = os.getenv("EI_ACTION_PATH", "").strip()
            if not raw_path:
                return nested_submitted
            try:
                paths = [json.loads(raw_path)]
            except json.JSONDecodeError as exc:
                raise AssertionError(f"EI_ACTION_PATH 不是有效 JSON：{exc}") from exc
        if not isinstance(paths, list):
            return nested_submitted
        operations = []
        for steps in paths:
            if isinstance(steps, list) and len(steps) >= 3:
                operations.extend(zip(steps[1::2], steps[2::2]))
        for section, action in operations:
            title = scope.get_by_text(str(section), exact=True).last
            title.wait_for(state="visible", timeout=10_000)
            title.scroll_into_view_if_needed()
            section_scope = self._nested_section_scope(title, section)
            collection_spec = self._configured_collection_for_section(str(section))
            if collection_spec is not None:
                rows = section_scope.locator(collection_spec.item_selector)
            else:
                primary_rows = section_scope.locator(
                    ".el-table__body-wrapper .el-table__row"
                )
                rows = primary_rows if primary_rows.count() else section_scope.locator(
                    ".el-table__row"
                )
            before_rows = rows.count()
            if str(action).startswith(NESTED_ADD_ACTIONS):
                button = section_scope.get_by_role("button", name=str(action), exact=True).first
                button.wait_for(state="visible", timeout=10_000)
                button.click()
                after_rows = self._wait_for_row_count_change(rows, before_rows, increase=True)
                if after_rows <= before_rows:
                    raise AssertionError(
                        f"嵌套操作“{section} / {action}”已点击，但表格行数未增加："
                        f"before={before_rows}, after={after_rows}"
                    )
                created_row = rows.nth(after_rows - 1)
                if collection_spec is not None:
                    row_submitted = self._fill_configured_collection_row(
                        collection_spec, created_row, after_rows - 1
                    )
                    row_fill_failures: list[str] = []
                else:
                    row_submitted = self._fill_dialog(dom_scope=created_row)
                    row_fill_failures = list(self._fill_failures)
                row_fields = scan_dom_fields(self.page, created_row)
                if not row_fields:
                    raise AssertionError(
                        f"嵌套操作“{section} / {action}”新增了表格行，但没有发现可编辑字段"
                    )
                empty_required = [
                    field.label or field.field_code
                    for field in row_fields
                    if field.required and not self._dom_field_has_value(field)
                ]
                if row_fill_failures or empty_required:
                    details = [*row_fill_failures, *empty_required]
                    raise AssertionError(
                        f"嵌套操作“{section} / {action}”新增行未填写完整："
                        + "; ".join(details)
                    )
                if not row_submitted:
                    raise AssertionError(
                        f"嵌套操作“{section} / {action}”新增行没有形成提交字段证据"
                    )
                nested_submitted.update({
                    self._nested_submitted_key(str(section), code): value
                    for code, value in row_submitted.items()
                })
                if collection_spec is not None:
                    self._remember_configured_collection_evidence(
                        collection_spec, str(section), after_rows - 1, row_submitted
                    )
                else:
                    for index, field in enumerate(row_fields, 1):
                        code, label, *_ = self._runtime_identity_for_nested_dom(
                            field, index
                        )
                        value = row_submitted.get(code)
                        if field.kind in {"text", "textarea", "number"} and value not in (None, ""):
                            self._remember_nested_evidence(
                                section=str(section),
                                field=label or code,
                                code=code,
                                value=value,
                                submitted_key=self._nested_submitted_key(
                                    str(section), code
                                ),
                            )
            elif str(action).startswith(NESTED_DESTRUCTIVE_ACTIONS):
                add = section_scope.get_by_role(
                    "button", name=re.compile(r"^(新增|添加|新建|创建)$")
                ).first
                add.wait_for(state="visible", timeout=10_000)
                add.click()
                created_rows = self._wait_for_row_count_change(rows, before_rows, increase=True)
                if created_rows <= before_rows:
                    raise AssertionError(f"无法为嵌套删除创建自动化临时行：{section}")
                if before_rows == 0:
                    add.click()
                    second_count = self._wait_for_row_count_change(
                        rows, created_rows, increase=True
                    )
                    if second_count <= created_rows:
                        raise AssertionError(f"无法为必填嵌套分区保留可保存行：{section}")
                    created_rows = second_count
                created_row = rows.nth(created_rows - 1)
                delete = created_row.get_by_role("button", name=str(action), exact=True).first
                delete.wait_for(state="visible", timeout=10_000)
                delete.click()
                confirm = self.page.locator(
                    '.el-message-box:visible,[role="alertdialog"]:visible'
                ).last
                if confirm.count() and confirm.is_visible():
                    confirm.get_by_role("button", name=re.compile(r"^(确定|确认)$")).last.click()
                remaining_rows = self._wait_for_row_count_change(
                    rows, created_rows, increase=False
                )
                if remaining_rows >= created_rows:
                    raise AssertionError(
                        f"嵌套操作“{section} / {action}”未删除自动化临时行："
                        f"before={created_rows}, after={remaining_rows}"
                    )
            else:
                button = section_scope.get_by_role("button", name=str(action), exact=True).first
                button.wait_for(state="visible", timeout=10_000)
                responses = []
                self.page.on("response", lambda response: responses.append(response))
                before_url = self.page.url
                before_dialogs = self.page.locator(DIALOG).count()
                button.click()
                self.page.wait_for_timeout(1_000)
                business_responses = [
                    response for response in responses
                    if getattr(response.request, "resource_type", "") in {"xhr", "fetch"}
                ]
                after_dialogs = self.page.locator(DIALOG).count()
                if not (
                    business_responses or self.page.url != before_url
                    or after_dialogs > before_dialogs
                ):
                    raise AssertionError(
                        f"嵌套操作“{section} / {action}”已点击，但未观察到业务效果"
                    )
            self.page.wait_for_timeout(1_000)
        return nested_submitted

    @staticmethod
    def _nested_section_scope(title, section: str):
        """Return the nearest table section instead of a shared dialog ancestor."""
        enterprise_section = title.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),"
            "' enterprise-section ')][1]"
        )
        if enterprise_section.count() and enterprise_section.is_visible():
            return enterprise_section
        section_scope = title.locator(
            "xpath=ancestor::*[.//button and (.//table or "
            ".//*[contains(@class, 'table')])][1]"
        )
        if not section_scope.count() or not section_scope.is_visible():
            raise AssertionError(f"嵌套操作“{section}”未找到独立表格区块")
        return section_scope

    def _configured_collection_for_section(
        self, section: str,
    ) -> DynamicCollectionSpec | None:
        matches = [
            spec for spec in getattr(self, "dynamic_collections", ())
            if spec.section_title and spec.section_title == str(section).strip()
        ]
        if len(matches) > 1:
            raise DynamicFieldContractError(
                f"动态字段契约重复：区块“{section}”匹配到多个集合"
            )
        return matches[0] if matches else None

    def _remember_configured_collection_evidence(
        self,
        spec: DynamicCollectionSpec,
        section: str,
        row_index: int,
        submitted: dict[str, Any],
    ) -> None:
        for child in spec.children:
            if child.kind not in {"text", "textarea", "number"}:
                continue
            code = child.field_code_template.format(index=row_index)
            value = submitted.get(code)
            if value in (None, ""):
                continue
            self._remember_nested_evidence(
                section=section,
                field=child.label or code,
                code=code,
                value=value,
                submitted_key=self._nested_submitted_key(section, code),
            )

    @staticmethod
    def _request_payload(request) -> Any:
        try:
            payload = request.post_data_json
            return payload() if callable(payload) else payload
        except Exception:
            raw = getattr(request, "post_data", "") or ""
            try:
                return json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                return raw

    @staticmethod
    def _payload_shape_type(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, int):
            return "integer"
        if isinstance(value, float):
            return "number"
        if isinstance(value, str):
            return "string"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return f"array[{len(value)}]"
        return type(value).__name__

    @classmethod
    def _request_shape_summary(
        cls, request, *, max_paths: int = 200, max_depth: int = 8,
    ) -> str:
        """Describe a JSON request without retaining submitted values."""
        payload = cls._request_payload(request)
        if not isinstance(payload, (dict, list)):
            return json.dumps({"$": "non_json_or_unavailable"}, ensure_ascii=False)

        shapes: dict[str, set[str]] = {}
        truncated = False

        def remember(path: str, value: Any) -> bool:
            nonlocal truncated
            if path not in shapes and len(shapes) >= max_paths:
                truncated = True
                return False
            shapes.setdefault(path, set()).add(cls._payload_shape_type(value))
            return True

        def visit(value: Any, path: str, depth: int) -> None:
            nonlocal truncated
            if not remember(path, value):
                return
            if depth >= max_depth:
                if isinstance(value, (dict, list)) and value:
                    truncated = True
                return
            if isinstance(value, dict):
                for key in sorted(value, key=lambda item: str(item)):
                    safe_key = re.sub(r"[\x00-\x1f\x7f]", "?", str(key))[:120]
                    visit(value[key], f"{path}.{safe_key}", depth + 1)
                    if len(shapes) >= max_paths:
                        truncated = True
                        break
            elif isinstance(value, list):
                for item in value:
                    visit(item, f"{path}[]", depth + 1)
                    if len(shapes) >= max_paths:
                        truncated = True
                        break

        visit(payload, "$", 0)
        summary = {
            path: "|".join(sorted(types))
            for path, types in shapes.items()
        }
        if truncated:
            summary["$.__truncated__"] = "true"
        return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def _failed_save_response_detail(cls, response) -> str:
        try:
            response_text = response.text()[:1000]
        except Exception:
            response_text = "<响应正文不可读>"
        request = getattr(response, "request", None)
        return (
            f"response={response_text}; "
            f"requestShape={cls._request_shape_summary(request)}"
        )

    @classmethod
    def _payload_scalar_values(cls, payload: Any) -> set[str]:
        values: set[str] = set()
        if isinstance(payload, dict):
            for value in payload.values():
                values.update(cls._payload_scalar_values(value))
        elif isinstance(payload, list):
            for value in payload:
                values.update(cls._payload_scalar_values(value))
        elif payload not in (None, ""):
            values.add(str(payload).strip())
        return values

    def _assert_nested_values_in_payload(
        self,
        payload: Any,
        *,
        stage: str,
        submitted: dict[str, Any] | None = None,
        synchronize_support: bool = False,
        required_codes: set[str] | None = None,
    ) -> None:
        if synchronize_support:
            self._synchronize_nested_support_evidence_from_payload(
                payload, submitted
            )
        evidence = self._current_nested_evidence(submitted)
        if required_codes is not None:
            evidence = [
                item for item in evidence
                if self._nested_evidence_targets_code(item, required_codes)
            ]
        if not evidence:
            return
        values = self._payload_scalar_values(payload)
        missing = [
            f"{item['section']} / {item['field']}={item['value']}"
            for item in evidence
            if not self._nested_evidence_value_matches(item, values)
        ]
        if missing:
            nested_lists = {
                key: value
                for key, value in payload.items()
                if isinstance(payload, dict)
                and isinstance(value, list)
                and any(isinstance(row, dict) for row in value)
            } if isinstance(payload, dict) else {}
            raise AssertionError(
                f"{stage}没有持久化嵌套行字段：" + "; ".join(missing)
                + f"; payload_keys={sorted(payload) if isinstance(payload, dict) else []!r}"
                + f"; nested_lists={nested_lists!r}"
            )

    @classmethod
    def _nested_evidence_targets_code(
        cls, item: dict[str, str], required_codes: set[str],
    ) -> bool:
        candidates = {
            str(item.get("code", "")).strip(),
            str(item.get("submitted_key", "")).strip(),
        } - {""}
        return any(
            cls._source_code_matches_runtime(candidate, required)
            or cls._source_code_matches_runtime(required, candidate)
            for candidate in candidates
            for required in required_codes
        )

    @classmethod
    def _nested_evidence_value_matches(
        cls, item: dict[str, str], actual_values: set[str],
    ) -> bool:
        expected = str(item.get("value", "")).strip()
        if expected in actual_values:
            return True
        return cls._semantic_numeric_readback_values_match(
            str(item.get("code") or item.get("submitted_key") or ""),
            expected,
            list(actual_values),
            field_label=str(item.get("field", "")),
        )

    def _synchronize_nested_support_evidence_from_payload(
        self,
        payload: Any,
        submitted: dict[str, Any] | None,
    ) -> None:
        """Refresh retained-form support values from the outgoing save payload."""
        if not isinstance(payload, dict):
            return
        for item in self._nested_evidence:
            if self._nested_submitted_value(item, submitted) not in (None, "", []):
                continue
            stable = self._unique_nested_source_field(
                code=item.get("code", ""),
                label=item.get("field", ""),
            )
            code = stable[0] if stable is not None else item.get("code", "")
            if not code or self._is_generated_identifier(code):
                continue
            current_values = {
                str(value)
                for value in self._field_values_from_record(payload, code)
                if value not in (None, "", [])
            }
            if len(current_values) != 1:
                continue
            item["code"] = code
            item["submitted_key"] = code
            if stable is not None:
                item["field"] = stable[1] or item.get("field", "")
            item["value"] = next(iter(current_values))

    def _assert_nested_values_in_open_form(self) -> None:
        for item in self._current_nested_evidence():
            title = self.page.get_by_text(item["section"], exact=True).last
            title.wait_for(state="visible", timeout=10_000)
            section_scope = title.locator("xpath=ancestor::*[.//button or .//table][1]")
            values = section_scope.locator("input,textarea").evaluate_all(
                "els => els.map(el => String(el.value || '').trim()).filter(Boolean)"
            )
            if not self._nested_evidence_value_matches(item, set(values)):
                raise AssertionError(
                    "保存后编辑表单没有回显嵌套行字段："
                    f"{item['section']} / {item['field']}={item['value']}"
                )

    def _wait_for_row_count_change(self, rows, before: int, *, increase: bool) -> int:
        current = rows.count()
        for _attempt in range(15):
            if (increase and current > before) or (not increase and current < before):
                break
            self.page.wait_for_timeout(200)
            current = rows.count()
        return current

    @classmethod
    def _submitted_identity_values(cls, submitted: dict[str, Any]) -> list[str]:
        values = [
            str(value).strip()
            for code, value in submitted.items()
            if value not in (None, "", []) and code.lower() in cls.RECORD_IDENTITY_CODES
        ]
        return cls._record_identity_marker_variants(values)

    @classmethod
    def _submitted_automation_marker_values(
        cls, submitted: dict[str, Any],
    ) -> list[str]:
        """Return only explicit automation-owned values for identity fallback."""
        values = [
            str(value).strip()
            for value in submitted.values()
            if value not in (None, "", [])
            and str(value).strip().startswith(AUTOMATION_RECORD_PREFIXES)
        ]
        return cls._record_identity_marker_variants(values)

    @classmethod
    def _record_identity_marker_variants(cls, values: Iterable[Any]) -> list[str]:
        markers: list[str] = []
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            markers.append(text)
            if text.startswith(AUTOMATION_RECORD_PREFIXES):
                token_matches = list(
                    re.finditer(r"_S\d{3,}(?=$|[_-])", text)
                )
                if token_matches:
                    markers.append(text[:token_matches[-1].end()])
        return cls._deduplicate(markers)

    @staticmethod
    def _is_stable_save_token_marker(value: Any) -> bool:
        text = str(value or "").strip()
        return (
            text.startswith(AUTOMATION_RECORD_PREFIXES)
            and re.search(r"_S\d{3,}$", text) is not None
        )

    @classmethod
    def _automation_owned_markers(cls, values: list[str]) -> list[str]:
        return cls._deduplicate([
            str(value).strip()
            for value in values
            if str(value).strip().startswith(AUTOMATION_RECORD_PREFIXES)
        ])

    @staticmethod
    def _normalize_record_text(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip()

    @classmethod
    def _delete_display_identity_values(
        cls, submitted: dict[str, Any], markers: list[str]
    ) -> list[str]:
        marker_values = {cls._normalize_record_text(value) for value in markers}
        values: list[str] = []
        for code, value in submitted.items():
            if cls._is_generated_identifier(str(code)) or value in (None, "", []):
                continue
            text = cls._normalize_record_text(value)
            if text and text not in marker_values:
                values.append(text)
        return cls._deduplicate(values)

    def _find_unique_delete_row(
        self,
        business_id: str,
        markers: list[str],
        *,
        fallback_values: list[str] | None = None,
        response_payload: Any = None,
        rows=None,
        allow_arbitrary_row: bool = False,
    ):
        rows = rows if rows is not None else self.page.locator(".el-table__row:visible")
        row_items = [rows.nth(index) for index in range(rows.count())]
        normalized_id = self._normalize_record_text(business_id)
        if normalized_id:
            id_matches = self._record_containers_matching_business_id(
                row_items,
                normalized_id,
                operation="删除",
            )
            if len(id_matches) == 1:
                return id_matches[0], business_id
            if len(id_matches) > 1:
                raise AssertionError(
                    f"业务 ID 精确匹配到 {len(id_matches)} 条记录，禁止删除：{business_id}"
                )

        normalized_markers = {
            self._normalize_record_text(marker): marker
            for marker in markers
            if self._normalize_record_text(marker)
        }
        marker_matches: list[tuple[Any, str]] = []
        for row in row_items:
            cells = row.locator("td,[role='cell']")
            cell_texts = {
                self._normalize_record_text(text)
                for text in cells.all_inner_texts()
            }
            matched_markers = [
                original
                for normalized, original in normalized_markers.items()
                if normalized in cell_texts
            ]
            if matched_markers:
                marker_matches.append((row, matched_markers[0]))
        if len(marker_matches) == 1:
            return marker_matches[0]
        if not marker_matches:
            if response_payload is not None and normalized_id:
                try:
                    response_row, evidence = self._find_response_associated_record_container(
                        response_payload, normalized_id, containers=row_items
                    )
                except AssertionError:
                    pass
                else:
                    return response_row, evidence
            normalized_fallbacks = {
                self._normalize_record_text(value)
                for value in fallback_values or ()
                if self._normalize_record_text(value)
            }
            if len(normalized_fallbacks) >= 2:
                fallback_matches = []
                for row in row_items:
                    cells = row.locator("td,[role='cell']")
                    cell_texts = {
                        self._normalize_record_text(text)
                        for text in cells.all_inner_texts()
                    }
                    if normalized_fallbacks.issubset(cell_texts):
                        fallback_matches.append(row)
                if len(fallback_matches) == 1:
                    return fallback_matches[0], "保存字段组合"
                if len(fallback_matches) > 1:
                    raise AssertionError(
                        "保存字段组合精确匹配到 "
                        f"{len(fallback_matches)} 条记录，禁止删除：{sorted(normalized_fallbacks)}"
                    )
        if not marker_matches:
            if allow_arbitrary_row:
                for row in row_items:
                    commands = row.locator("button,[role='button']")
                    for index in range(commands.count()):
                        command = commands.nth(index)
                        if self._normalize_record_text(command.inner_text()) != "删除":
                            continue
                        if str(command.get_attribute("disabled") or "").lower() in {
                            "disabled", "true"
                        }:
                            continue
                        if str(command.get_attribute("aria-disabled") or "").lower() == "true":
                            continue
                        return row, ARBITRARY_DELETE_ROW_MARKER
            raise AssertionError(
                "未找到单元格文本精确匹配的本次自动化记录，且本次保存响应业务 ID、"
                "自动化标识或保存字段组合均无法唯一定位，"
                f"禁止删除：markers={markers}, fallback={fallback_values or []}"
            )
        raise AssertionError(
            f"自动化标识精确匹配到 {len(marker_matches)} 条记录，禁止删除：{markers}"
        )

    def _row_business_id(self, row) -> str:
        identities = self._record_container_business_ids(row)
        if len(identities) > 1:
            raise AssertionError(
                "记录行及其操作节点暴露了冲突的业务 ID："
                + ", ".join(sorted(identities))
            )
        return next(iter(identities), "")

    @classmethod
    def _record_container_business_ids(cls, container) -> set[str]:
        """Read stable record IDs from a row/card and its command nodes."""
        attributes = (
            "data-business-id", "data-key", "data-row-key", "data-id",
            "data-record-id", "row-key",
        )
        identities: set[str] = set()
        for attribute in attributes:
            try:
                value = cls._normalize_record_text(container.get_attribute(attribute))
            except Exception:
                value = ""
            if value:
                identities.add(value)

        command_selector = ",".join(
            f"button[{attribute}],a[{attribute}],"
            f"[role='button'][{attribute}],[role='link'][{attribute}]"
            for attribute in attributes
        )
        try:
            commands = container.locator(command_selector)
            for index in range(commands.count()):
                command = commands.nth(index)
                try:
                    command_label = " ".join(
                        str(value or "")
                        for value in (
                            command.inner_text(),
                            command.get_attribute("title"),
                            command.get_attribute("aria-label"),
                        )
                    )
                except Exception:
                    command_label = ""
                if not any(
                    token in cls._normalize_record_text(command_label)
                    for token in ("查看", "详情", "编辑", "修改", "删除", "移除")
                ):
                    continue
                for attribute in attributes:
                    value = cls._normalize_record_text(
                        command.get_attribute(attribute)
                    )
                    if value:
                        identities.add(value)
        except Exception:
            pass
        return identities

    def _record_containers_matching_business_id(
        self,
        containers: Iterable[Any],
        business_id: str,
        *,
        operation: str,
    ) -> list[Any]:
        """Return exact ID matches while rejecting conflicting row/action identity."""
        normalized_id = self._normalize_record_text(business_id)
        matches: list[Any] = []
        for container in containers:
            identities = self._record_container_business_ids(container)
            if normalized_id not in identities:
                continue
            if identities != {normalized_id}:
                raise AssertionError(
                    f"{operation}目标记录的行与操作节点业务 ID 冲突："
                    f"目标={business_id}，实际={sorted(identities)}"
                )
            matches.append(container)
        return matches

    def _wait_for_deleted_record_absent(
        self,
        business_id: str,
        markers: list[str],
        *,
        fallback_values: list[str] | None = None,
        response_payload: Any = None,
        responses: list[Any] | None = None,
        after_response: Any = None,
        timeout: int = 10_000,
    ) -> None:
        """Verify the automation-owned record disappeared by identity, not row position."""
        deadline = time.monotonic() + timeout / 1000
        last_identity = ""
        last_error = ""
        while True:
            api_presence = self._latest_collection_record_presence(
                responses or (),
                after_response=after_response,
                business_id=business_id,
            )
            if api_presence is False:
                return
            if api_presence is True:
                last_identity = business_id
                last_error = ""
            rows = self.page.locator(".el-table__row:visible")
            try:
                _row, identity = self._find_unique_delete_row(
                    business_id,
                    markers,
                    fallback_values=fallback_values,
                    response_payload=response_payload,
                    rows=rows,
                )
                last_identity = identity
                last_error = ""
            except AssertionError as exc:
                message = str(exc)
                if (
                    "未找到单元格文本精确匹配" in message
                    or "未找到本次保存响应业务 ID" in message
                ):
                    if api_presence is not True:
                        return
                    last_error = (
                        f"删除后的列表接口仍返回本次业务 ID：{business_id}"
                    )
                else:
                    last_error = message
            if not hasattr(self.page, "wait_for_timeout") or time.monotonic() >= deadline:
                break
            self.page.wait_for_timeout(300)
        if last_error:
            if business_id and api_presence is None and (
                "保存字段组合精确匹配到" in last_error
                or "自动化标识精确匹配到" in last_error
            ):
                raise AssertionError(
                    "删除接口已成功，但刷新后的列表响应未提供"
                    f"可用的业务 ID 回读，无法以本次记录为单位确认已删除："
                    f"business_id={business_id}; dom={last_error}"
                )
            raise AssertionError(f"删除后无法确认本次记录消失：{last_error}")
        identity = last_identity or business_id or ", ".join(markers)
        raise AssertionError(f"删除接口成功但本次记录仍在列表中：{identity}")

    @classmethod
    def _latest_collection_record_presence(
        cls,
        responses: Iterable[Any],
        *,
        after_response: Any,
        business_id: str,
    ) -> bool | None:
        """Read the latest same-resource list response after a delete."""
        normalized_id = cls._normalize_record_text(business_id)
        if not normalized_id or after_response is None:
            return None
        response_items = list(responses)
        delete_index = next(
            (
                index
                for index, response in enumerate(response_items)
                if response is after_response
            ),
            -1,
        )
        if delete_index < 0:
            return None
        delete_path = urlsplit(str(getattr(after_response, "url", ""))).path.lower()
        delete_segments = [segment for segment in delete_path.split("/") if segment]
        if (
            len(delete_segments) >= 2
            and delete_segments[-2] in {"delete", "remove"}
        ):
            resource_path = "/" + "/".join(delete_segments[:-2])
        else:
            resource_path = delete_path.rsplit("/", 1)[0]
        if not resource_path:
            return None
        candidates: list[Any] = []
        for response in response_items[delete_index + 1:]:
            request = getattr(response, "request", None)
            response_path = urlsplit(str(getattr(response, "url", ""))).path.lower()
            if (
                not getattr(response, "ok", False)
                or getattr(request, "resource_type", "xhr") not in {"xhr", "fetch"}
                or response_path.rsplit("/", 1)[0] != resource_path
                or not any(token in response_path.rsplit("/", 1)[-1] for token in (
                    "list", "page", "query", "search",
                ))
                or "json" not in str(
                    getattr(response, "headers", {}).get(
                        "content-type", "application/json"
                    )
                ).lower()
            ):
                continue
            try:
                payload = response.json()
            except Exception:
                continue
            if cls._payload_has_record_collection(payload):
                candidates.append(payload)
        if not candidates:
            return None
        return normalized_id in cls._record_scalar_texts(candidates[-1])

    @classmethod
    def _payload_has_record_collection(cls, payload: Any) -> bool:
        if isinstance(payload, list):
            return True
        if not isinstance(payload, dict):
            return False
        collection_keys = {"records", "rows", "list", "items", "content"}
        direct_collection_keys = {"data", "result", "results"}
        for key, value in payload.items():
            normalized_key = str(key).lower()
            is_record_list = isinstance(value, list) and (
                not value or all(isinstance(item, dict) for item in value)
            )
            if normalized_key in collection_keys and is_record_list:
                return True
            if normalized_key in direct_collection_keys and is_record_list:
                return True
            if isinstance(value, dict) and cls._payload_has_record_collection(value):
                return True
        return False

    def _refresh_list_after_delete(self) -> bool:
        """Refresh a page-level list so delete verification never reads stale rows."""
        try:
            candidates = self.page.locator("button:visible,a:visible,[role='button']:visible")
            refresh = None
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                if (
                    self._normalize_record_text(candidate.inner_text()) != "刷新"
                    or not candidate.is_visible()
                    or not candidate.is_enabled()
                ):
                    continue
                if candidate.evaluate(
                    "el => !el.closest('.el-table__row,.ant-table-row,' +"
                    "'.mujijin-cardBox,.platform-card,.fund-card,.category-item')"
                ):
                    refresh = candidate
                    break
            if refresh is None:
                return False
            refresh.click()
            loading = self.page.locator(
                ".el-loading-mask:visible,.ant-spin-spinning:visible,[aria-busy='true']:visible"
            ).first
            if loading.count() and loading.is_visible():
                loading.wait_for(state="hidden", timeout=20_000)
            return True
        except Exception:
            # Some pages refresh automatically and do not expose a page-level
            # refresh command.  The subsequent identity polling remains the
            # authoritative delete assertion.
            return False

    @classmethod
    def _normalize_identity_label(cls, value: str) -> str:
        normalized = cls._normalize_label(value)
        for prefix in cls.RECORD_IDENTITY_PROMPT_PREFIXES:
            if normalized.startswith(prefix):
                return normalized[len(prefix):]
        return normalized

    def _is_record_identity_field(self, field) -> bool:
        if (
            field.field_code
            and not self._is_generated_identifier(field.field_code)
            and field.field_code.lower() in self.RECORD_IDENTITY_CODES
        ):
            return True

        normalized_label = self._normalize_identity_label(field.label)
        if not normalized_label:
            return False
        source_codes = {
            code.lower()
            for code, label, *_ in self.source_fields
            if self._normalize_identity_label(label) == normalized_label
        }
        if len(source_codes) == 1 and source_codes.pop() in self.RECORD_IDENTITY_CODES:
            return True
        return any(
            normalized_label.endswith(suffix)
            for suffix in self.RECORD_IDENTITY_LABEL_SUFFIXES
        )

    def _record_identity_value(self, field) -> str:
        try:
            locator = self.page.locator(field.selector).first
            if not locator.count() or not locator.is_visible():
                return ""
            value = locator.evaluate("""el => {
                if (el.closest('td,.el-table__row,.ant-table-row')) return '';
                const type = (el.type || '').toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                if (type === 'radio' || type === 'checkbox' || type === 'file' ||
                    ['radio', 'checkbox', 'switch'].includes(role)) return '';
                if ((el.tagName || '').toLowerCase() === 'select') {
                    return Array.from(el.selectedOptions || [])
                        .map(option => (option.textContent || '').trim())
                        .filter(Boolean).join(',');
                }
                const owner = el.closest('.el-select,.el-cascader,.ant-select');
                const selected = owner?.querySelectorAll(
                    '.el-select__selected-item:not(.is-transparent),.el-select__tags-text,' +
                    '.el-cascader__tags-text,.ant-select-selection-item'
                ) || [];
                const selectedText = Array.from(selected)
                    .map(node => (node.textContent || '').trim()).filter(Boolean).join(',');
                if (selectedText) return selectedText;
                if (el.isContentEditable) return (el.textContent || '').trim();
                return String(el.value ?? '').trim();
            }""")
            return str(value or "").strip()
        except Exception:
            return ""

    def _collect_record_identity_markers(
        self, submitted: dict[str, Any], *, scope=None,
    ) -> tuple[str, ...]:
        markers = self._submitted_identity_values(submitted)
        try:
            dom_fields = (
                scan_dom_fields(self.page, scope)
                if scope is not None
                else scan_dom_fields(self.page)
            )
        except Exception:
            dom_fields = []
        for field in dom_fields:
            if self._is_record_identity_field(field):
                if value := self._record_identity_value(field):
                    markers.extend(self._record_identity_marker_variants([value]))
        if not markers:
            markers.extend(self._submitted_automation_marker_values(submitted))
        return tuple(self._deduplicate(markers))

    def _open_detail(
        self,
        echo_values: list[str],
        business_id: str,
        *,
        response_payload: Any = None,
        display_identity_values: list[str] | None = None,
    ) -> None:
        try:
            row, _identity = self._find_unique_record_container(
                business_id,
                echo_values,
                display_identity_values=display_identity_values,
            )
        except AssertionError as exc:
            if response_payload is None:
                raise AssertionError(
                    "保存后无法在当前列表定位本次记录，不能打开详情核对"
                ) from exc
            try:
                row, _identity = self._find_response_associated_record_container(
                    response_payload, business_id, visible_only=True
                )
            except AssertionError as response_exc:
                probe = self._probe_response_associated_detail_row(
                    response_payload, business_id, visible_only=True
                )
                if probe is None:
                    raise AssertionError(
                        "保存后无法用业务 ID 或关联响应唯一定位本次记录，"
                        "不能打开详情核对"
                    ) from response_exc
                return
        classes = row.get_attribute("class") or ""
        if any(card_class in classes for card_class in (
            "mujijin-cardBox", "platform-card", "fund-card", "el-tree-node__content",
        )):
            row.click()
            self.page.wait_for_timeout(1_500)
            return
        action = row.locator(
            "button:has-text('查看'),button:has-text('详情'),button:has-text('编辑'),"
            "a:has-text('查看'),a:has-text('详情'),a:has-text('编辑')"
        ).first
        if not action.count() or not action.is_visible():
            raise AssertionError("本次保存记录没有可用的查看/详情/编辑入口，不能打开详情核对")
        action.click()
        self.page.wait_for_timeout(1500)

    def _open_record_action(
        self,
        markers: list[str],
        business_id: str,
        *,
        action_names: tuple[str, ...],
        allow_row_click: bool,
    ) -> None:
        """Open an action only inside the uniquely identified saved record."""
        container, identity = self._find_unique_record_container(business_id, markers)
        self._open_record_container_action(
            container,
            identity,
            markers,
            action_names=action_names,
            allow_row_click=allow_row_click,
        )

    def open_latest_editable_record_form(self, action: str) -> bool:
        """Open the newest safely mapped row-level editor from a fresh list response.

        Prefer the newest response-mapped row, ranked by creation time and
        business ID. Detail sub-tables can hide their child IDs and omit enough
        display fields to make that mapping impossible. For an Edit/Modify
        case, the operation only needs to exercise an available editor rather
        than prove it is the record created by this run, so then use any
        currently visible row with an enabled row-local editor.

        ``False`` means no row-level action exists, so a caller may still use
        a page-level edit action.
        """
        responses: list[Any] = []
        listener = lambda response: responses.append(response)
        listening = hasattr(self.page, "on") and hasattr(self.page, "remove_listener")
        if listening:
            self.page.on("response", listener)
        try:
            refreshed = self._refresh_list_after_delete()
            if refreshed and hasattr(self.page, "wait_for_timeout"):
                # Let the refreshed list response arrive after the loading
                # layer clears; it remains the source of ordering, not DOM order.
                self.page.wait_for_timeout(500)
        finally:
            if listening:
                self.page.remove_listener("response", listener)

        candidates = self._latest_editable_response_candidates(responses, action)
        if candidates:
            business_id, container, evidence = candidates[0]
            self._open_record_container_action(
                container,
                f"最新可编辑记录 ID={business_id}; {evidence}",
                [],
                action_names=self._row_edit_action_names(action),
                allow_row_click=False,
            )
            print(
                "EDIT_RECORD_CANDIDATE_SELECTED "
                f"business_id={business_id} evidence={evidence}",
                flush=True,
            )
            return True

        fallback_container = self._first_visible_record_container_with_enabled_action(
            action
        )
        if fallback_container is not None:
            self._open_record_container_action(
                fallback_container,
                "当前可编辑记录（无需新增记录关联）",
                [],
                action_names=self._row_edit_action_names(action),
                allow_row_click=False,
            )
            print(
                "EDIT_RECORD_CANDIDATE_SELECTED "
                "evidence=visible-enabled-row-fallback",
                flush=True,
            )
            return True
        return False

    def _latest_editable_response_candidates(
        self,
        responses: Iterable[Any],
        action: str,
    ) -> list[tuple[str, Any, str]]:
        """Return row-local editable candidates ordered by ``createDt``, then ID."""
        records_by_id: dict[str, dict[str, Any]] = {}
        for response in responses:
            if not self._is_json_collection_response(response):
                continue
            try:
                payload = response.json()
            except Exception:
                continue
            for record in self._collect_dicts(payload):
                business_id = self._direct_record_business_id(record)
                if not business_id or not self._record_create_time_key(record):
                    continue
                previous = records_by_id.get(business_id)
                if previous is None or self._record_recency_key(record) > self._record_recency_key(previous):
                    records_by_id[business_id] = record

        candidates: list[tuple[tuple[int, int, str], str, Any, str]] = []
        for business_id, record in records_by_id.items():
            display_codes = self._visible_response_field_codes(record)
            try:
                container, evidence = self._find_response_associated_record_container(
                    {"data": {"records": [record]}},
                    business_id,
                    display_field_codes=display_codes,
                )
            except AssertionError:
                continue
            if not any(
                self._record_container_has_enabled_action(container, name)
                for name in self._row_edit_action_names(action)
            ):
                continue
            candidates.append((
                self._record_recency_key(record), business_id, container, evidence,
            ))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [
            (business_id, container, evidence)
            for _key, business_id, container, evidence in candidates
        ]

    @classmethod
    def _is_json_collection_response(cls, response: Any) -> bool:
        request = getattr(response, "request", None)
        if (
            not getattr(response, "ok", False)
            or getattr(request, "resource_type", "xhr") not in {"xhr", "fetch"}
            or "json" not in str(
                getattr(response, "headers", {}).get("content-type", "application/json")
            ).lower()
        ):
            return False
        try:
            return cls._payload_has_record_collection(response.json())
        except Exception:
            return False

    @classmethod
    def _record_create_time_key(cls, record: dict[str, Any]) -> int:
        """Normalize an audit creation timestamp without treating it as an ID."""
        lowered = {str(key).lower(): value for key, value in record.items()}
        value = lowered.get("createdt")
        if value in (None, ""):
            return 0
        digits = re.sub(r"\D", "", str(value))
        return int(digits) if digits else 0

    @classmethod
    def _record_recency_key(cls, record: dict[str, Any]) -> tuple[int, int, str]:
        business_id = cls._direct_record_business_id(record)
        numeric_id = int(business_id) if business_id.isdecimal() else 0
        return cls._record_create_time_key(record), numeric_id, business_id

    @classmethod
    def _visible_response_field_codes(cls, record: dict[str, Any]) -> tuple[str, ...]:
        """Keep response fields that can be tested against current row cells."""
        return tuple(
            str(code)
            for code, value in record.items()
            if str(code)
            and not cls._direct_record_business_id({str(code): value})
            and not isinstance(value, (dict, list, bool))
            and value not in (None, "")
        )

    @staticmethod
    def _row_edit_action_names(action: str) -> tuple[str, ...]:
        if action in {"编辑", "修改"}:
            return ("编辑", "修改")
        return (action,)

    @staticmethod
    def _record_container_has_enabled_action(container, action: str) -> bool:
        for role in ("button", "link"):
            try:
                candidate = container.get_by_role(role, name=action, exact=True).first
                if candidate.count() and candidate.is_visible() and candidate.is_enabled():
                    return True
            except Exception:
                continue
        return False

    def _has_visible_row_action(self, action: str) -> bool:
        return self._first_visible_record_container_with_enabled_action(action) is not None

    def _first_visible_record_container_with_enabled_action(self, action: str):
        """Return one current row with an enabled requested row-level action."""
        containers = self.page.locator(
            ".el-table__row:visible,.ant-table-row:visible,"
            ".mujijin-cardBox:visible,.platform-card:visible,.fund-card:visible,"
            ".category-item:visible,.el-tree-node__content:visible"
        )
        try:
            for index in range(containers.count()):
                container = containers.nth(index)
                if any(
                    self._record_container_has_enabled_action(container, name)
                    for name in self._row_edit_action_names(action)
                ):
                    return container
        except Exception:
            pass
        return None

    def _open_record_container_action(
        self,
        container,
        identity: str,
        markers: list[str],
        *,
        action_names: tuple[str, ...],
        allow_row_click: bool,
    ) -> None:
        """Open an action from one container already associated with the saved record."""
        for action_name in action_names:
            for role in ("button", "link"):
                action = container.get_by_role(role, name=action_name, exact=True).first
                if action.count() and action.is_visible() and action.is_enabled():
                    action.click()
                    self.page.wait_for_timeout(1_500)
                    return

        if allow_row_click and any(name in {"查看", "详情"} for name in action_names):
            identity_action = self._record_identity_link_action(
                container, [identity, *markers]
            )
            if identity_action is not None:
                identity_action.click()
                self.page.wait_for_timeout(1_500)
                return

        classes = container.get_attribute("class") or ""
        is_card = any(card_class in classes for card_class in (
            "mujijin-cardBox", "platform-card", "fund-card", "category-item",
            "el-tree-node__content",
        ))
        if allow_row_click and is_card:
            container.click()
            self.page.wait_for_timeout(1_500)
            return
        raise AssertionError(
            f"本次新增记录 {identity!r} 没有可用的"
            + "/".join(action_names)
            + "入口"
        )

    def _find_response_associated_record_container(
        self,
        payload: Any,
        business_id: str,
        *,
        containers: Iterable[Any] | None = None,
        display_field_codes: Iterable[str] | None = None,
        visible_only: bool = False,
    ):
        """Associate an ID-bearing response record with one exact visible row/card."""
        id_matches, matches = self._response_associated_record_container_candidates(
            payload,
            business_id,
            containers=containers,
            display_field_codes=display_field_codes,
            visible_only=visible_only,
        )
        if len(id_matches) == 1:
            return id_matches[0], f"操作节点业务 ID={business_id}"
        if len(id_matches) > 1:
            raise AssertionError(
                f"业务 ID {business_id} 的操作节点匹配到多条可见记录，"
                "无法唯一操作"
            )
        if len(matches) != 1:
            raise AssertionError(
                f"业务 ID {business_id} 的响应记录字段匹配到多条可见记录，"
                "无法唯一操作"
            )
        container, evidence = matches[0]
        return container, "响应关联字段=" + ", ".join(evidence)

    def _response_associated_record_container_candidates(
        self,
        payload: Any,
        business_id: str,
        *,
        containers: Iterable[Any] | None = None,
        display_field_codes: Iterable[str] | None = None,
        visible_only: bool = False,
    ) -> tuple[list[Any], list[tuple[Any, tuple[str, ...]]]]:
        """Return exact ID matches and strict display-evidence row candidates.

        Callers that need one row must keep the normal unique-match requirement.
        The detail-probe fallback below is the sole consumer allowed to inspect
        several equally matched candidates.
        """
        normalized_id = self._normalize_record_text(business_id)
        records = [
            record
            for record in self._collect_dicts(payload)
            if self._direct_record_business_id(record) == normalized_id
        ]
        if not records:
            raise AssertionError(
                f"关联响应中未找到业务 ID 对应记录：{business_id}"
            )

        if containers is None:
            containers = self.page.locator(
                ".el-table__row:visible,.ant-table-row:visible,"
                ".mujijin-cardBox:visible,.platform-card:visible,.fund-card:visible,"
                ".category-item:visible,.el-tree-node__content:visible"
            )
        count = getattr(containers, "count", None)
        container_items = (
            [containers.nth(index) for index in range(count())]
            if callable(count) and hasattr(containers, "nth")
            else list(containers)
        )
        id_matches = self._record_containers_matching_business_id(
            container_items,
            normalized_id,
            operation="响应关联",
        )

        response_values = {
            value
            for record in records
            for value in self._response_display_identity_values(
                record,
                normalized_id,
                field_codes=display_field_codes,
            )
        }
        rendered_containers: list[tuple[Any, list[str]]] = []
        for container in container_items:
            cells = self._record_container_cell_texts(container)
            if not cells:
                try:
                    cells = container.inner_text().splitlines()
                except Exception:
                    cells = []
            rendered_containers.append((container, [
                self._normalize_record_text(value)
                for value in cells
                if self._normalize_record_text(value)
            ]))
        if display_field_codes is not None or visible_only:
            response_values = {
                value
                for value in response_values
                if any(
                    self._response_display_value_matches_cell(value, cell_values)
                    for _container, cell_values in rendered_containers
                )
            }
        if len(response_values) < 2:
            raise AssertionError(
                f"业务 ID {business_id} 的关联响应记录缺少至少两个稳定展示字段，"
                "无法唯一操作"
            )
        matches: list[tuple[Any, tuple[str, ...]]] = []
        for container, cell_values in rendered_containers:
            evidence = tuple(
                value
                for value in sorted(response_values, key=len, reverse=True)
                if self._response_display_value_matches_cell(value, cell_values)
            )
            if len(evidence) == len(response_values):
                matches.append((container, evidence))
        if not matches:
            raise AssertionError(
                f"业务 ID {business_id} 的响应记录稳定展示字段未完整匹配任何可见记录行"
            )
        return id_matches, matches

    def _probe_response_associated_detail_row(
        self,
        payload: Any,
        business_id: str,
        *,
        display_field_codes: Iterable[str] | None = None,
        visible_only: bool = False,
    ) -> tuple[str, str, Any] | None:
        """Resolve visually identical rows only through their read-only detail GET.

        This deliberately sits behind the ordinary strict association method.
        It never chooses a row by position: every candidate is opened through
        its existing view/detail control, and exactly one returned detail body
        must contain the Save response business ID.
        """
        id_matches, candidates = self._response_associated_record_container_candidates(
            payload,
            business_id,
            display_field_codes=display_field_codes,
            visible_only=visible_only,
        )
        if id_matches or len(candidates) < 2:
            return None

        list_url = self.page.url
        verified: list[tuple[str, tuple[str, ...]]] = []
        for index, (container, evidence) in enumerate(candidates, 1):
            action = self._record_detail_probe_action(container, evidence)
            if action is None:
                raise AssertionError(
                    f"业务 ID {business_id} 的候选记录 {index} 没有可用的查看/详情入口，"
                    "不能用详情响应确认记录身份"
                )
            marker = f"ei-detail-probe-{uuid.uuid4().hex}"
            self._mark_runtime_record_container(container, marker)
            response = self._click_detail_probe_action(action, business_id)
            if response is not None:
                verified.append((marker, evidence))
            self._return_to_record_list(list_url)

        if len(verified) != 1:
            raise AssertionError(
                f"业务 ID {business_id} 的候选详情探测未得到唯一匹配："
                f"candidates={len(candidates)}, verified={len(verified)}"
            )

        marker, evidence = verified[0]
        container = self._runtime_marked_record_container(marker)
        action = self._record_detail_probe_action(container, evidence)
        if action is None:
            raise AssertionError(
                f"业务 ID {business_id} 的已验证记录在回读前失去查看/详情入口"
            )
        response = self._click_detail_probe_action(action, business_id)
        if response is None:
            raise AssertionError(
                f"业务 ID {business_id} 的已验证记录未返回匹配的只读详情响应"
            )
        return "详情响应关联字段=" + ", ".join(evidence), marker, response

    def _record_detail_probe_action(self, container, evidence: tuple[str, ...]):
        for action_name in ("查看", "详情"):
            for role in ("button", "link"):
                try:
                    action = container.get_by_role(
                        role, name=action_name, exact=True
                    ).first
                    if action.count() and action.is_visible() and action.is_enabled():
                        return action
                except Exception:
                    continue
        return self._record_identity_link_action(container, list(evidence))

    @staticmethod
    def _mark_runtime_record_container(container, marker: str) -> None:
        try:
            container.evaluate(
                "(node, value) => node.setAttribute('data-ei-detail-probe', value)",
                marker,
            )
        except Exception as exc:
            raise AssertionError("候选记录无法写入运行时详情探测标记") from exc

    def _runtime_marked_record_container(self, marker: str):
        escaped = re.sub(r"[^A-Za-z0-9_-]", "", marker)
        candidates = self.page.locator(
            f"[data-ei-detail-probe='{escaped}']:visible"
        )
        if candidates.count() != 1:
            raise AssertionError(
                "详情探测后无法重新定位唯一的已验证记录："
                f"marker={escaped!r}, matches={candidates.count()}"
            )
        return candidates.first

    def _click_detail_probe_action(self, action, business_id: str):
        responses: list[Any] = []
        mutation_requests: list[Any] = []
        listening = hasattr(self.page, "on") and hasattr(self.page, "remove_listener")
        if not listening:
            raise AssertionError("当前页面不支持监听详情响应，禁止用候选行探测记录身份")

        def response_received(response) -> None:
            responses.append(response)

        def request_started(request) -> None:
            method = str(getattr(request, "method", "")).upper()
            if method not in {"POST", "PUT", "PATCH", "DELETE"}:
                return
            url = str(getattr(request, "url", ""))
            if not self._is_business_mutation_url(url):
                return
            try:
                payload = self._request_payload(request)
                if self._is_non_business_mutation_endpoint(url, payload):
                    return
            except Exception:
                pass
            mutation_requests.append(request)

        self.page.on("response", response_received)
        self.page.on("request", request_started)
        try:
            action.click()
            deadline = time.monotonic() + 5
            inspected = 0
            while time.monotonic() < deadline:
                if mutation_requests:
                    request = mutation_requests[0]
                    raise AssertionError(
                        "候选记录详情探测触发了写请求，禁止继续："
                        f"{getattr(request, 'method', '')} {getattr(request, 'url', '')}"
                    )
                for response in responses[inspected:]:
                    if self._is_exact_readonly_detail_response(response, business_id):
                        return response
                inspected = len(responses)
                self.page.wait_for_timeout(100)
            return None
        finally:
            self.page.remove_listener("response", response_received)
            self.page.remove_listener("request", request_started)

    @classmethod
    def _is_exact_readonly_detail_response(cls, response, business_id: str) -> bool:
        request = getattr(response, "request", None)
        if (
            not getattr(response, "ok", False)
            or str(getattr(request, "method", "")).upper() != "GET"
            or getattr(request, "resource_type", "xhr") not in {"xhr", "fetch"}
            or "json" not in str(
                getattr(response, "headers", {}).get("content-type", "")
            ).lower()
            or not any(token in str(getattr(response, "url", "")).lower() for token in (
                "/detail", "detail/", "getdetail", "getbyid", "querybyid", "/info",
            ))
        ):
            return False
        try:
            payload = response.json()
            cls._assert_business_success(payload, operation="候选详情")
        except Exception:
            return False
        expected = cls._normalize_record_text(business_id)
        direct_ids = {
            cls._direct_record_business_id(record)
            for record in cls._collect_dicts(payload)
            if cls._direct_record_business_id(record)
        }
        return bool(expected and expected in direct_ids)

    @classmethod
    def _response_display_identity_values(
        cls,
        record: dict[str, Any],
        business_id: str,
        *,
        field_codes: Iterable[str] | None = None,
    ) -> set[str]:
        """Keep only stable response scalars that a rendered row can display."""
        if field_codes is not None:
            logical_records = cls._collect_logical_record_dicts(record)
            values: set[str] = set()
            for code in dict.fromkeys(str(value) for value in field_codes if str(value)):
                alias_values = [
                    value
                    for alias in DETAIL_DISPLAY_ALIASES.get(code, ())
                    for item in logical_records
                    for value in cls._field_values_from_record(item, alias)
                ]
                candidates = alias_values or [
                    value
                    for item in logical_records
                    for value in cls._field_values_from_record(item, code)
                ]
                for value in candidates:
                    if isinstance(value, (dict, list, bool)) or value in (None, ""):
                        continue
                    normalized = cls._normalize_record_text(value)
                    if normalized and normalized != business_id:
                        values.add(normalized)
            return values

        excluded_key_tokens = ("id", "code", "key", "flag", "status")
        values: set[str] = set()
        for item in cls._collect_logical_record_dicts(record):
            for key, value in item.items():
                key_text = str(key).strip().lower()
                if (
                    any(token in key_text for token in excluded_key_tokens)
                    or isinstance(value, (dict, list, bool))
                    or value in (None, "")
                ):
                    continue
                normalized = cls._normalize_record_text(value)
                if normalized and normalized != business_id:
                    values.add(normalized)
        return values

    def _source_response_association_codes(self) -> tuple[str, ...] | None:
        """Return stable form fields allowed to identify a row during edit readback."""
        codes = tuple(dict.fromkeys(
            str(field[0])
            for field in getattr(self, "source_fields", ())
            if field and not self._is_generated_identifier(str(field[0]))
        ))
        return codes or None

    @classmethod
    def _response_display_value_matches_cell(
        cls, expected: str, cell_values: list[str]
    ) -> bool:
        if expected in cell_values:
            return True
        # List endpoints commonly return an audit timestamp while a table
        # intentionally renders only its date component.  This is still an
        # exact displayed value comparison, not a fuzzy time-range match.
        date_match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?:[ T].*)?", expected)
        if date_match and date_match.group(1) in cell_values:
            return True
        expected_number = cls._decimal_readback_value(expected)
        return expected_number is not None and any(
            cls._decimal_readback_value(actual) == expected_number
            for actual in cell_values
        )

    def _record_identity_link_action(self, container, identities: list[str]):
        """Return a clickable record-name link/button inside a unique row/card."""
        normalized_identities = [
            self._normalize_record_text(identity)
            for identity in identities
            if self._normalize_record_text(identity)
        ]
        if not normalized_identities:
            return None
        actions = container.locator(
            "a:visible,button:visible,[role='link']:visible,[role='button']:visible"
        )
        for index in range(actions.count()):
            action = actions.nth(index)
            try:
                if not action.is_visible() or not action.is_enabled():
                    continue
                raw_text = action.inner_text()
                raw_title = action.get_attribute("title") or ""
                raw_label = action.get_attribute("aria-label") or ""
                candidates = [
                    self._normalize_record_text(value)
                    for value in (raw_text, raw_title, raw_label)
                    if self._normalize_record_text(value)
                ]
                if any(
                    candidate == marker or candidate.startswith(marker)
                    for candidate in candidates
                    for marker in normalized_identities
                ):
                    return action
            except Exception:
                continue
        return None

    def _current_detail_edit_button(self, markers: list[str]):
        """Return the page-level edit action when the saved record is already in detail."""
        edit_pattern = re.compile(r"^\s*(?:编辑|修改|编辑.+|修改.+)\s*$")
        edits = []
        candidates = self.page.locator("button:visible,a:visible").filter(
            has_text=edit_pattern
        )
        for index in range(candidates.count()):
            edit = candidates.nth(index)
            if not edit.is_visible() or not edit.is_enabled():
                continue
            if edit.evaluate(
                "el => !el.closest('.el-table__row,.ant-table-row,' +"
                "'.mujijin-cardBox,.platform-card,.fund-card,.category-item')"
            ):
                edits.append(edit)
        normalized_markers = [
            self._normalize_record_text(marker) for marker in markers
            if self._normalize_record_text(marker)
        ]
        marker_visible_outside_list = False
        for marker in normalized_markers:
            nodes = self.page.get_by_text(marker, exact=True)
            for index in range(nodes.count()):
                node = nodes.nth(index)
                if not node.is_visible():
                    continue
                if node.evaluate(
                    "el => !el.closest('.el-table__row,.ant-table-row,' +"
                    "'.mujijin-cardBox,.platform-card,.fund-card,.category-item')"
                ):
                    marker_visible_outside_list = True
                    break
            if marker_visible_outside_list:
                break
        if not marker_visible_outside_list:
            if self._url_is_detail_page(self.page.url) and len(edits) == 1:
                return edits[0]
            return None
        return edits[0] if edits else None

    def _detail_route_has_current_record(self, business_id: str, markers: list[str]) -> bool:
        """True when a detail route embeds a row/card for the newly saved record."""
        try:
            self._find_unique_record_container(
                business_id,
                markers,
                allow_search=False,
            )
            return True
        except AssertionError:
            return False

    @staticmethod
    def _url_is_detail_page(url: str) -> bool:
        return bool(re.search(r"/detail(?:[/?#]|$)", str(url or "").lower()))

    def _find_unique_record_container(
        self,
        business_id: str,
        markers: list[str],
        *,
        allow_search: bool = True,
        display_identity_values: list[str] | None = None,
    ):
        containers = self.page.locator(
            ".el-table__row:visible,.ant-table-row:visible,"
            ".mujijin-cardBox:visible,.platform-card:visible,.fund-card:visible,"
            ".category-item:visible,.el-tree-node__content:visible"
        )
        items = [containers.nth(index) for index in range(containers.count())]
        normalized_id = self._normalize_record_text(business_id)
        if normalized_id:
            id_matches = self._record_containers_matching_business_id(
                items,
                normalized_id,
                operation="打开",
            )
            if len(id_matches) == 1:
                return id_matches[0], business_id
            if len(id_matches) > 1:
                raise AssertionError(
                    f"业务 ID 精确匹配到 {len(id_matches)} 条记录，无法唯一打开：{business_id}"
                )

        normalized_markers = {
            self._normalize_record_text(marker): marker
            for marker in markers
            if self._normalize_record_text(marker)
        }
        stable_prefix_markers = {
            normalized: original
            for normalized, original in normalized_markers.items()
            if self._is_stable_save_token_marker(original)
        }
        exact_matches: list[tuple[Any, str]] = []
        prefix_matches: list[tuple[Any, str]] = []
        for item in items:
            cells = item.locator("td,[role='cell']")
            texts = cells.all_inner_texts() if cells.count() else item.inner_text().splitlines()
            normalized_texts = {
                self._normalize_record_text(text) for text in texts
                if self._normalize_record_text(text)
            }
            exact = [
                original for normalized, original in normalized_markers.items()
                if normalized in normalized_texts
            ]
            if exact:
                exact_matches.append((item, max(exact, key=len)))
            prefix = [
                original
                for normalized, original in stable_prefix_markers.items()
                if any(text.startswith(normalized) for text in normalized_texts)
            ]
            if prefix:
                prefix_matches.append((item, max(prefix, key=len)))

        if exact_matches:
            strongest_length = max(
                len(self._normalize_record_text(marker))
                for _item, marker in exact_matches
            )
            strongest = [
                match for match in exact_matches
                if len(self._normalize_record_text(match[1])) == strongest_length
            ]
            if len(strongest) == 1:
                return strongest[0]
            raise AssertionError(
                f"本次新增记录完整标识精确匹配到 {len(strongest)} 条记录，"
                f"无法唯一打开：{markers}"
            )
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if not prefix_matches:
            stable_values = {
                self._normalize_record_text(marker)
                for marker in (display_identity_values or ())
                if self._normalize_record_text(marker)
            }
            if len(stable_values) >= 2:
                stable_matches = []
                for item in items:
                    cells = item.locator("td,[role='cell']")
                    texts = cells.all_inner_texts() if cells.count() else item.inner_text().splitlines()
                    cell_texts = {
                        self._normalize_record_text(text)
                        for text in texts
                        if self._normalize_record_text(text)
                    }
                    if all(
                        self._auxiliary_display_value_matches(value, cell_texts)
                        for value in stable_values
                    ):
                        stable_matches.append(item)
                if len(stable_matches) == 1:
                    return stable_matches[0], "保存字段组合"
                if len(stable_matches) > 1:
                    raise AssertionError(
                        "保存字段组合精确匹配到 "
                        f"{len(stable_matches)} 条记录，无法唯一打开"
                    )
            if allow_search and self._search_record_list_by_markers(markers):
                return self._find_unique_record_container(
                    business_id, markers, allow_search=False
                )
            raise AssertionError(
                "保存后无法精确定位本次新增记录："
                f"business_id={business_id!r}, markers={markers!r}"
            )
        raise AssertionError(
            f"本次新增记录稳定前缀匹配到 {len(prefix_matches)} 条记录，"
            f"无法唯一打开：{markers}"
        )

    @classmethod
    def _auxiliary_display_value_matches(
        cls, expected: str, actual_values: set[str],
    ) -> bool:
        normalized = cls._normalize_record_text(expected)
        if normalized in actual_values:
            return True
        expected_number = cls._decimal_readback_value(normalized)
        return expected_number is not None and any(
            cls._decimal_readback_value(actual) == expected_number
            for actual in actual_values
        )

    def _search_record_list_by_markers(self, markers: list[str]) -> bool:
        search_values = [
            marker for marker in markers
            if self._normalize_record_text(marker).startswith(AUTOMATION_RECORD_PREFIXES)
        ]
        for marker in search_values:
            if self._search_record_list(str(marker)):
                return True
        return False

    def _search_record_list(self, keyword: str) -> bool:
        keyword = str(keyword or "").strip()
        if not keyword:
            return False
        inputs = self.page.locator("input:visible:not([readonly]):not([disabled])")
        candidates = []
        for index in range(inputs.count()):
            control = inputs.nth(index)
            try:
                if not control.is_visible() or not control.is_editable():
                    continue
                if control.evaluate(
                    "el => !!el.closest('[role=\"dialog\"],.el-dialog,.el-drawer,"
                    ".el-table__row,.ant-table-row')"
                ):
                    continue
                placeholder = control.get_attribute("placeholder") or ""
                score = 0
                if any(token in placeholder for token in ("关键字", "名称", "标题")):
                    score += 2
                if any(token in placeholder for token in ("请输入", "搜索", "查询")):
                    score += 1
                candidates.append((score, index, control))
            except Exception:
                continue
        if not candidates:
            return False
        candidates.sort(key=lambda item: (-item[0], item[1]))
        control = candidates[0][2]
        try:
            control.fill(keyword)
        except Exception:
            return False
        button = self._record_list_search_button()
        try:
            if button is not None:
                button.click()
            else:
                control.press("Enter")
        except Exception:
            return False
        self.page.wait_for_timeout(1_000)
        loading = self.page.locator(
            ".el-loading-mask:visible,.ant-spin-spinning:visible,[aria-busy='true']:visible"
        ).first
        if loading.count() and loading.is_visible():
            try:
                loading.wait_for(state="hidden", timeout=10_000)
            except Exception:
                pass
        self.page.wait_for_timeout(500)
        return True

    def _record_list_search_button(self):
        buttons = self.page.locator(
            "button:visible:has-text('搜索'),button:visible:has-text('查询')"
        )
        for index in range(buttons.count()):
            button = buttons.nth(index)
            try:
                if not button.is_visible() or not button.is_enabled():
                    continue
                if button.evaluate(
                    "el => !!el.closest('[role=\"dialog\"],.el-dialog,.el-drawer)"
                ):
                    continue
                return button
            except Exception:
                continue
        return None

    def _return_to_record_list(self, list_url: str) -> None:
        """Leave the detail view and wait until record containers are rendered again."""
        dialog = self.page.locator(DIALOG).last
        if dialog.count() and dialog.is_visible():
            close = dialog.locator(
                'button[aria-label="Close"]:visible,button:has-text("关闭"):visible,'
                'button:has-text("返回"):visible'
            ).last
            if close.count() and close.is_visible():
                close.click()
                try:
                    dialog.wait_for(state="hidden", timeout=10_000)
                except Exception as exc:
                    raise AssertionError("详情弹窗关闭后未返回记录列表") from exc
            else:
                self.page.reload(wait_until="domcontentloaded")
        elif self.page.url != list_url:
            self.page.goto(list_url, wait_until="domcontentloaded")
        else:
            self.page.reload(wait_until="domcontentloaded")

        loading = self.page.locator(
            ".el-loading-mask:visible,.ant-spin-spinning:visible,[aria-busy='true']:visible"
        ).first
        if loading.count() and loading.is_visible():
            try:
                loading.wait_for(state="hidden", timeout=20_000)
            except Exception as exc:
                raise AssertionError("返回记录列表后页面在 20 秒内未加载完成") from exc
        records = self.page.locator(
            ".el-table__row:visible,.ant-table-row:visible,"
            ".mujijin-cardBox:visible,.platform-card:visible,.fund-card:visible,"
            ".category-item:visible,.el-tree-node__content:visible"
        ).first
        try:
            records.wait_for(state="visible", timeout=15_000)
        except Exception as exc:
            raise AssertionError("返回后没有发现可操作的记录列表") from exc

    @classmethod
    def _assert_detail_values(
        cls,
        payload: Any,
        submitted: dict[str, Any],
        *,
        required_codes: set[str] | None = None,
        business_id: str = "",
        record_markers: tuple[str, ...] = (),
        detail_request=None,
        submitted_payload: Any = None,
    ) -> None:
        records = cls._collect_dicts(payload)
        display_aliases = DETAIL_DISPLAY_ALIASES
        required = set(required_codes) if required_codes is not None else None
        if required is not None:
            missing_submitted = sorted(required - submitted.keys())
            if missing_submitted:
                raise AssertionError(
                    "指定回读字段未出现在本次提交值："
                    + ", ".join(missing_submitted)
                )
        identity_codes = required if required is not None else set(submitted)
        if required is not None or business_id or record_markers:
            records = cls._strict_detail_record_dicts(
                payload,
                identity_codes,
                display_aliases,
                business_id=business_id,
                record_markers=record_markers,
                detail_request=detail_request,
            )
        if required is not None:
            missing_detail = sorted(
                code
                for code in required
                if not any(cls._detail_field_values(record, code, display_aliases) for record in records)
            )
        submitted_for_comparison = (
            {
                code: value
                for code, value in submitted.items()
                if code in required
            }
            if required is not None
            else submitted
        )
        comparable = {
            code: value
            for code, value in submitted_for_comparison.items()
            if (
                any(
                    cls._detail_field_values(record, code, display_aliases)
                    for record in records
                )
                or (
                    required is not None
                    and code in required
                    and not cls._normalize_record_text(value)
                )
            )
        }
        if not comparable:
            raise AssertionError("详情接口未返回任何本次提交字段，无法核对保存结果")
        failures = []
        coded_values = {
            "itemType": {"功能": {"1"}, "附件": {"2"}},
            "isRequired": {"是": {"1", "true"}, "否": {"0", "false"}},
            "needIntelligent": {"是": {"1", "true"}, "否": {"0", "false"}},
            "editAble": {"只读": {"1"}, "可编辑": {"2"}},
            "isRegion": {"境内": {"0"}, "境外": {"1"}},
            "isRegister": {"已注册": {"1"}, "未注册": {"2"}},
        }
        for code, expected in comparable.items():
            actual_values = [
                actual
                for record in records
                for actual in cls._detail_field_values(record, code, display_aliases)
            ]
            alias_values = [
                actual
                for record in records
                for candidate in display_aliases.get(code, ())
                for actual in cls._field_values_from_record(record, candidate)
            ]
            if not cls._normalize_record_text(expected):
                if not any(
                    cls._normalize_record_text(actual) for actual in actual_values
                ):
                    continue
                failures.append(
                    f"{code}: expected empty, actual={actual_values!r}"
                )
                continue
            submitted_code_values = (
                cls._field_values_from_record(submitted_payload, code)
                if isinstance(submitted_payload, dict)
                else []
            )
            if (
                (business_id or record_markers)
                and not alias_values
                and submitted_code_values
                and any(
                    cls._normalize_record_text(actual)
                    == cls._normalize_record_text(submitted_code)
                    for actual in actual_values
                    for submitted_code in submitted_code_values
                )
            ):
                continue
            if (
                display_aliases.get(code)
                and not alias_values
                and not str(expected).isdigit()
                and actual_values
                and all(str(actual).isdigit() for actual in actual_values)
            ):
                if any(
                    str(actual) == str(submitted_code)
                    for actual in actual_values
                    for submitted_code in submitted_code_values
                ):
                    continue
                if required is not None and code in required:
                    failures.append(
                        f"{code}: expected={expected!r}, actual={actual_values!r}, "
                        "详情仅返回 ID 且没有显示名称，无法比较"
                    )
                continue
            accepted_codes = coded_values.get(code, {}).get(str(expected), set())
            if not accepted_codes:
                accepted_codes = cls._generic_boolean_code_values(code, expected)
            if accepted_codes and any(str(actual).lower() in accepted_codes for actual in actual_values):
                continue
            if cls._semantic_numeric_readback_values_match(
                code, expected, actual_values
            ):
                continue
            if not any(str(actual) == str(expected) for actual in actual_values):
                failures.append(f"{code}: expected={expected!r}, actual={actual_values!r}")
        if failures:
            raise AssertionError("详情接口数据与本次提交不一致：\n" + "\n".join(failures))

    @classmethod
    def _detail_field_values(
        cls,
        record: dict[str, Any],
        code: str,
        display_aliases: dict[str, tuple[str, ...]],
    ) -> list[Any]:
        values: list[Any] = []
        for candidate in (code, *display_aliases.get(code, ())):
            values.extend(cls._field_values_from_record(record, candidate))
        return values

    @classmethod
    def _field_values_from_record(cls, record: dict[str, Any], field_path: str) -> list[Any]:
        if not isinstance(record, dict) or not field_path:
            return []
        if field_path in record:
            return cls._flatten_detail_value(record[field_path])
        if "." not in field_path and "*" not in field_path:
            return []
        parts = [part for part in str(field_path).split(".") if part]
        if not parts:
            return []
        return cls._values_at_detail_path(record, parts)

    @classmethod
    def _values_at_detail_path(cls, value: Any, parts: list[str]) -> list[Any]:
        if not parts:
            return cls._flatten_detail_value(value)
        head, *tail = parts
        if head == "*":
            if isinstance(value, list):
                return [
                    found
                    for item in value
                    for found in cls._values_at_detail_path(item, tail)
                ]
            if isinstance(value, dict):
                return [
                    found
                    for item in value.values()
                    if isinstance(item, (dict, list))
                    for found in cls._values_at_detail_path(item, tail)
                ]
            return []
        if isinstance(value, dict):
            if head in value:
                return cls._values_at_detail_path(value[head], tail)
            return []
        if isinstance(value, list):
            return [
                found
                for item in value
                for found in cls._values_at_detail_path(item, parts)
            ]
        return []

    @classmethod
    def _flatten_detail_value(cls, value: Any) -> list[Any]:
        if isinstance(value, list):
            return [
                found
                for item in value
                for found in cls._flatten_detail_value(item)
            ]
        if isinstance(value, dict):
            return []
        return [value]

    @classmethod
    def _record_has_detail_field(
        cls,
        record: dict[str, Any],
        code: str,
        display_aliases: dict[str, tuple[str, ...]],
    ) -> bool:
        return bool(cls._detail_field_values(record, code, display_aliases))

    @staticmethod
    def _generic_boolean_code_values(code: str, expected: Any) -> set[str]:
        normalized_expected = str(expected).strip()
        if normalized_expected not in {"是", "否"}:
            return set()
        normalized_code = re.sub(r"[^a-z0-9]", "", code or "", flags=re.I).lower()
        boolean_semantic = (
            normalized_code.startswith(("is", "has", "need", "enable", "allow"))
            or normalized_code.endswith(("flag", "status", "state", "enabled"))
        )
        if not boolean_semantic:
            return set()
        return {"1", "true"} if normalized_expected == "是" else {"0", "false"}

    @classmethod
    def _strict_detail_record_dicts(
        cls,
        payload: Any,
        required_codes: set[str],
        display_aliases: dict[str, tuple[str, ...]],
        *,
        business_id: str,
        record_markers: tuple[str, ...],
        detail_request,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []

        def collect(value: Any, *, list_item: bool = False) -> None:
            if isinstance(value, list):
                for nested in value:
                    collect(nested, list_item=True)
                return
            if isinstance(value, dict):
                has_required_field = any(
                    cls._record_has_detail_field(value, code, display_aliases)
                    for code in required_codes
                )
                if list_item and has_required_field:
                    candidates.append(value)
                    return
                for envelope in (
                    "data", "result", "body", "content", "records", "rows", "items", "list",
                ):
                    nested = value.get(envelope)
                    if not isinstance(nested, (dict, list)):
                        continue
                    before = len(candidates)
                    collect(nested)
                    if len(candidates) > before:
                        return
                if has_required_field:
                    candidates.append(value)
                    return
                for nested in value.values():
                    if isinstance(nested, (dict, list)):
                        collect(nested)

        collect(payload)
        if not candidates:
            return []

        normalized_id = cls._normalize_record_text(business_id)
        if normalized_id:
            id_matches = [
                record
                for record in candidates
                if cls._direct_record_business_id(record) == normalized_id
            ]
            if len(id_matches) == 1:
                return cls._collect_logical_record_dicts(id_matches[0])
            if len(id_matches) > 1:
                raise AssertionError(
                    f"详情响应中业务 ID {business_id} 匹配到多条记录，无法唯一回读"
                )
            explicit_ids = {
                cls._direct_record_business_id(record)
                for record in candidates
                if cls._direct_record_business_id(record)
            }
            if explicit_ids:
                raise AssertionError(
                    f"详情响应未返回本轮业务 ID {business_id}；"
                    f"候选记录 ID={sorted(explicit_ids)!r}"
                )

        normalized_markers = {
            cls._normalize_record_text(marker)
            for marker in record_markers
            if cls._normalize_record_text(marker)
        }
        if normalized_markers:
            marker_matches = [
                record
                for record in candidates
                if normalized_markers & cls._logical_record_scalar_texts(record)
            ]
            if len(marker_matches) == 1:
                return cls._collect_logical_record_dicts(marker_matches[0])
            if len(marker_matches) > 1:
                raise AssertionError(
                    "详情响应中自动化记录标识匹配到多条记录，无法唯一回读"
                )

        request_identifies_record = (
            normalized_id
            and cls._request_contains_business_id(detail_request, normalized_id)
        )
        if len(candidates) == 1 and (
            request_identifies_record
            or (not normalized_id and not normalized_markers)
        ):
            return cls._collect_logical_record_dicts(candidates[0])
        raise AssertionError(
            f"详情响应无法唯一关联本轮新增记录；"
            f"业务 ID={business_id!r}，标识={list(record_markers)!r}，"
            f"候选记录数={len(candidates)}"
        )

    @classmethod
    def _direct_record_business_id(cls, record: dict[str, Any]) -> str:
        lowered = {str(key).lower(): value for key, value in record.items()}
        for key in BUSINESS_ID_KEYS:
            value = lowered.get(key.lower())
            if value not in (None, "") and not isinstance(value, (dict, list)):
                return cls._normalize_record_text(value)
        return ""

    @classmethod
    def _record_scalar_texts(cls, value: Any) -> set[str]:
        if isinstance(value, dict):
            return {
                normalized
                for nested in value.values()
                for normalized in cls._record_scalar_texts(nested)
            }
        if isinstance(value, list):
            return {
                normalized
                for nested in value
                for normalized in cls._record_scalar_texts(nested)
            }
        normalized = cls._normalize_record_text(value)
        return {normalized} if normalized else set()

    @classmethod
    def _collect_logical_record_dicts(cls, record: dict[str, Any]) -> list[dict[str, Any]]:
        found = [record]
        for nested in record.values():
            if isinstance(nested, dict):
                found.extend(cls._collect_logical_record_dicts(nested))
        return found

    @classmethod
    def _logical_record_scalar_texts(cls, record: dict[str, Any]) -> set[str]:
        return {
            normalized
            for item in cls._collect_logical_record_dicts(record)
            for value in item.values()
            if not isinstance(value, (dict, list))
            if (normalized := cls._normalize_record_text(value))
        }

    @classmethod
    def _request_contains_business_id(cls, request, business_id: str) -> bool:
        if request is None or not business_id:
            return False
        identity_pattern = re.compile(
            rf"(?<![0-9A-Za-z_-]){re.escape(business_id)}(?![0-9A-Za-z_-])"
        )
        if identity_pattern.search(str(getattr(request, "url", "") or "")):
            return True
        # Playwright exposes ``post_data_json`` as a property. Accessing it for
        # a multipart upload raises instead of returning ``None``, so keep that
        # transport detail from aborting unrelated detail-response matching.
        try:
            payload = getattr(request, "post_data_json", None)
        except Exception:
            payload = None
        if callable(payload):
            try:
                payload = payload()
            except Exception:
                payload = None
        if business_id in cls._record_scalar_texts(payload):
            return True
        return bool(
            identity_pattern.search(str(getattr(request, "post_data", "") or ""))
        )

    @classmethod
    def _collect_dicts(cls, value: Any) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        if isinstance(value, dict):
            found.append(value)
            for nested in value.values():
                found.extend(cls._collect_dicts(nested))
        elif isinstance(value, list):
            for nested in value:
                found.extend(cls._collect_dicts(nested))
        return found

    def _prepare_configured_dynamic_collections(self, scope) -> dict[str, Any]:
        """Apply manifest-owned collection rules without changing application markup."""
        submitted: dict[str, Any] = {}
        for spec in getattr(self, "dynamic_collections", ()):
            root = scope.locator(spec.root_selector).first
            if not root.count() or not root.is_visible():
                raise DynamicFieldContractError(
                    f"动态字段契约缺失：{spec.field_code} 未找到集合根节点"
                )
            rows = self._configured_collection_rows(root, spec)
            if len(rows) < spec.min_rows:
                trigger = root.locator(spec.create_selector).first
                if not trigger.count() or not trigger.is_visible():
                    raise DynamicFieldContractError(
                        f"动态字段契约缺失：{spec.field_code} 没有可新增行"
                    )
                self._activate_configured_collection_trigger(trigger, spec)
                rows = self._wait_for_configured_collection_rows(root, spec)
            # min_rows is a creation threshold. Hydrated collections may already
            # contain more rows, and every rendered row participates in Save.
            for row_index, row in enumerate(rows):
                submitted.update(self._fill_configured_collection_row(
                    spec, row, row_index, value_offset=len(submitted)
                ))
            submitted[spec.field_code] = spec.mode
            self._collection_submission_codes.add(spec.field_code)
        return submitted

    @staticmethod
    def _configured_collection_row_headers(row) -> list[str]:
        return [
            str(value or "").strip()
            for value in row.locator("td").evaluate_all(
                r"""cells => cells.map((cell) => {
                    const clean = (value) => (value || '')
                        .replace(/^\s*\*\s*/, '').replace(/\s+/g, ' ').trim();
                    const table = cell.closest('table');
                    const headerRows = [...(table?.querySelectorAll('thead tr') || [])];
                    const direct = headerRows.at(-1)?.cells?.[cell.cellIndex];
                    const directText = clean(direct?.innerText || direct?.textContent || '');
                    if (directText) return directText;
                    const columnClass = [...cell.classList]
                        .find((name) => /_column_\d+$/.test(name));
                    const tableRoot = cell.closest('.el-table,.ant-table');
                    const header = columnClass
                        ? tableRoot?.querySelector(`th.${CSS.escape(columnClass)}`)
                        : null;
                    return clean(header?.innerText || header?.textContent || '');
                })"""
            )
        ]

    def _configured_collection_rows(
        self, root, spec: DynamicCollectionSpec,
    ) -> list[Any]:
        expected_headers = {
            child.column_header for child in spec.children if child.column_header
        }
        candidates = root.locator(spec.item_selector)
        rows: list[Any] = []
        for index in range(candidates.count()):
            row = candidates.nth(index)
            if not expected_headers:
                rows.append(row)
                continue
            headers = set(self._configured_collection_row_headers(row))
            if expected_headers.issubset(headers):
                rows.append(row)
        return rows

    def _configured_collection_child_scope(
        self, row, spec: DynamicCollectionSpec, child, row_index: int,
    ):
        if not child.column_header:
            return row
        headers = self._configured_collection_row_headers(row)
        matches = [
            index for index, header in enumerate(headers)
            if header == child.column_header
        ]
        if len(matches) != 1:
            raise DynamicFieldContractError(
                f"动态字段契约缺失：{spec.field_code} 第{row_index + 1}行"
                f"无法唯一定位表头“{child.column_header}”"
            )
        return row.locator("td").nth(matches[0])

    def _fill_configured_collection_row(
        self,
        spec: DynamicCollectionSpec,
        row,
        row_index: int,
        *,
        value_offset: int = 0,
    ) -> dict[str, Any]:
        submitted: dict[str, Any] = {}
        generated_codes: set[str] = set()
        numeric_values: dict[str, Any] = {}
        numeric_bindings: dict[str, tuple[ResolvedField, Any]] = {}
        for child_index, child in enumerate(spec.children, 1):
            field_code = child.field_code_template.format(index=row_index)
            child_scope = self._configured_collection_child_scope(
                row, spec, child, row_index
            )
            controls = child_scope.locator(child.selector)
            visible_controls = [
                controls.nth(index)
                for index in range(controls.count())
                if controls.nth(index).is_visible()
            ]
            if len(visible_controls) != 1:
                raise DynamicFieldContractError(
                    f"动态字段契约缺失：{spec.field_code} 子字段未唯一渲染："
                    f"{field_code}; count={len(visible_controls)}"
                )
            definition = FieldDefinition(
                field_code=field_code,
                field_name=child.label or field_code,
                field_type=TYPE_BY_KIND.get(child.kind, "TEXT"),
                required=child.required,
                source="dynamic-collection-manifest",
            )
            dom = DomField(
                field_code=field_code,
                label=definition.field_name,
                kind=child.kind,
                selector=child.selector,
                required=child.required,
            )
            resolved = ResolvedField(definition, dom)
            if child.kind == "number":
                numeric_bindings[field_code] = (resolved, child_scope)
                existing_value = self._configured_collection_numeric_value(
                    visible_controls[0]
                )
                if existing_value not in (None, ""):
                    numeric_values[field_code] = existing_value
            if self._dom_field_has_value(
                dom,
                root=child_scope,
                field_code=field_code,
                field_label=definition.field_name,
            ):
                continue
            value = self.data_strategy.value_for(
                definition, value_offset + child_index
            )
            actual = self.interactor.fill(
                resolved, value, root=child_scope
            )
            if actual in (None, "", []):
                raise DynamicFieldContractError(
                    f"动态字段契约缺失：{spec.field_code} 子字段未成功填写：{field_code}"
                )
            submitted[field_code] = actual
            generated_codes.add(field_code)
            if child.kind == "number":
                numeric_values[field_code] = actual
        self._enforce_configured_collection_value_relations(
            spec,
            row_index,
            submitted=submitted,
            generated_codes=generated_codes,
            numeric_values=numeric_values,
            numeric_bindings=numeric_bindings,
        )
        return submitted

    @staticmethod
    def _configured_collection_numeric_value(control) -> Any:
        try:
            return control.input_value()
        except Exception:
            try:
                return control.get_attribute("value")
            except Exception:
                return None

    @staticmethod
    def _configured_collection_decimal(value: Any) -> Decimal:
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError) as exc:
            raise DynamicFieldContractError(
                "动态字段值关系无法读取数值端点"
            ) from exc

    def _enforce_configured_collection_value_relations(
        self,
        spec: DynamicCollectionSpec,
        row_index: int,
        *,
        submitted: dict[str, Any],
        generated_codes: set[str],
        numeric_values: dict[str, Any],
        numeric_bindings: dict[str, tuple[ResolvedField, Any]],
    ) -> None:
        for relation in spec.value_relations:
            left_code = relation.left_field_template.format(index=row_index)
            right_code = relation.right_field_template.format(index=row_index)
            if left_code not in numeric_values or right_code not in numeric_values:
                raise DynamicFieldContractError(
                    f"动态字段值关系缺少端点：{left_code} lte {right_code}"
                )
            left = self._configured_collection_decimal(numeric_values[left_code])
            right = self._configured_collection_decimal(numeric_values[right_code])
            if left <= right:
                continue

            adjusted = False
            for side in relation.adjust_order:
                target_code = left_code if side == "left" else right_code
                if target_code not in generated_codes:
                    continue
                binding = numeric_bindings.get(target_code)
                if binding is None:
                    continue
                replacement = right if side == "left" else left
                resolved, child_scope = binding
                actual = self.interactor.fill(
                    resolved, format(replacement, "f"), root=child_scope
                )
                if actual in (None, "", []):
                    continue
                numeric_values[target_code] = actual
                submitted[target_code] = actual
                left = self._configured_collection_decimal(numeric_values[left_code])
                right = self._configured_collection_decimal(numeric_values[right_code])
                if left <= right:
                    adjusted = True
                    break
            if not adjusted:
                raise DynamicFieldContractError(
                    f"动态字段值关系无法安全调整：{left_code} lte {right_code}"
                )

    def _assert_configured_dynamic_collection_controls(self, scope) -> None:
        """Refuse to submit when a configured collection exposes an unknown child."""
        for spec in getattr(self, "dynamic_collections", ()):
            root = scope.locator(spec.root_selector).first
            if not root.count() or not root.is_visible():
                raise DynamicFieldContractError(
                    f"动态字段契约缺失：{spec.field_code} 未找到集合根节点"
                )
            rows = self._configured_collection_rows(root, spec)
            if len(rows) < spec.min_rows:
                raise DynamicFieldContractError(
                    f"动态字段契约缺失：{spec.field_code} 未渲染最少明细行"
                )
            for row_index, row in enumerate(rows):
                grouped: dict[str, list[Any]] = {}
                for child in spec.children:
                    if child.column_header:
                        grouped.setdefault(child.column_header, []).append(child)
                    child_scope = self._configured_collection_child_scope(
                        row, spec, child, row_index
                    )
                    controls = child_scope.locator(child.selector)
                    visible_count = sum(
                        1 for index in range(controls.count())
                        if controls.nth(index).is_visible()
                    )
                    if visible_count != 1:
                        field_code = child.field_code_template.format(index=row_index)
                        raise DynamicFieldContractError(
                            f"动态字段契约缺失：{spec.field_code} 子字段未唯一渲染："
                            f"{field_code}; count={visible_count}"
                        )
                for header, children in grouped.items():
                    cell = self._configured_collection_child_scope(
                        row, spec, children[0], row_index
                    )
                    actual_count = int(cell.evaluate(
                        """node => [...node.querySelectorAll(
                            'input:not([type=hidden]),textarea,select,[role=combobox]'
                        )].filter((el) => {
                            const style = getComputedStyle(el);
                            const rect = el.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden' &&
                                rect.width > 0 && rect.height > 0;
                        }).length"""
                    ))
                    if actual_count != len(children):
                        raise DynamicFieldContractError(
                            f"动态字段契约缺失：{spec.field_code} 第{row_index + 1}行"
                            f"表头“{header}”控件数不一致："
                            f"expected={len(children)}, actual={actual_count}"
                        )

    def _dom_field_is_in_configured_collection(self, scope, dom: DomField) -> bool:
        """Keep ordinary DOM filling out of manifest-owned detail rows."""
        selector = str(getattr(dom, "selector", "") or "").strip()
        if not selector:
            return False
        for spec in getattr(self, "dynamic_collections", ()):
            try:
                root = scope.locator(spec.root_selector).first
                if (
                    root.count()
                    and root.is_visible()
                    and root.locator(selector).count()
                ):
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _activate_configured_collection_trigger(
        trigger, spec: DynamicCollectionSpec,
    ) -> None:
        """Click the visible component wrapper, not its clipped native input."""
        try:
            trigger.scroll_into_view_if_needed()
            trigger.click(force=True)
        except Exception as exc:
            raise DynamicFieldContractError(
                f"动态字段契约缺失：{spec.field_code} 无法选择可见集合项"
            ) from exc

    def _wait_for_configured_collection_rows(
        self, root, spec: DynamicCollectionSpec,
    ) -> list[Any]:
        for _attempt in range(20):
            rows = self._configured_collection_rows(root, spec)
            if len(rows) >= spec.min_rows:
                return rows
            self.page.wait_for_timeout(200)
        raise DynamicFieldContractError(
            f"动态字段契约缺失：{spec.field_code} 未渲染可填写明细项"
        )

    def _dynamic_collection_contracts(self, scope) -> list[dict[str, Any]]:
        try:
            contracts = scope.evaluate(DYNAMIC_COLLECTION_CONTRACT_SCRIPT)
        except Exception as exc:
            raise DynamicFieldContractError("动态字段契约缺失：无法读取集合定义") from exc
        if not isinstance(contracts, list):
            raise DynamicFieldContractError("动态字段契约缺失：集合定义不是数组")
        seen: set[str] = set()
        for contract in contracts:
            code = str(contract.get("fieldCode") or "").strip() if isinstance(contract, dict) else ""
            path = str(contract.get("path") or "").strip() if isinstance(contract, dict) else ""
            mode = str(contract.get("mode") or "").strip() if isinstance(contract, dict) else ""
            child_fields = contract.get("childFields") if isinstance(contract, dict) else None
            try:
                min_rows = int(contract.get("minRows", 0)) if isinstance(contract, dict) else 0
            except (TypeError, ValueError):
                min_rows = 0
            if (
                not code
                or not path
                or mode not in {"add-row", "selection"}
                or min_rows < 1
                or not isinstance(child_fields, list)
                or not child_fields
                or not all(str(field).strip() for field in child_fields)
            ):
                raise DynamicFieldContractError(
                    f"动态字段契约缺失：{code or '未命名集合'}"
                )
            if code in seen:
                raise DynamicFieldContractError(f"动态字段契约重复：{code}")
            seen.add(code)
        return contracts

    def _wait_for_collection_rows(self, collection, minimum: int, field_code: str):
        rows = collection.locator("[data-ei-collection-item]")
        for _attempt in range(20):
            if rows.count() >= minimum:
                return rows
            self.page.wait_for_timeout(200)
        raise DynamicFieldContractError(
            f"动态字段契约缺失：{field_code} 未渲染可填写明细项"
        )

    @staticmethod
    def _collection_child_field_codes(row) -> set[str]:
        values = row.evaluate(
            """node => [node, ...node.querySelectorAll('[data-ei-collection-field-code]')]
                .map((item) => (item.getAttribute('data-ei-collection-field-code') || '').trim())
                .filter(Boolean)"""
        )
        return {str(value).strip() for value in values or [] if str(value).strip()}

    def _prepare_dynamic_collection_baselines(self, scope) -> dict[str, Any]:
        """Create the minimum collection shape before regular DOM field scanning."""
        submitted: dict[str, Any] = {}
        for contract in self._dynamic_collection_contracts(scope):
            field_code = str(contract["fieldCode"]).strip()
            mode = str(contract["mode"]).strip()
            minimum = int(contract["minRows"])
            expected_children = {str(item).strip() for item in contract["childFields"]}
            collection = scope.locator(
                f'[data-ei-collection-field="{field_code}"]'
            ).first
            if not collection.count() or not collection.is_visible():
                raise DynamicFieldContractError(f"动态字段契约缺失：{field_code}")
            rows = collection.locator("[data-ei-collection-item]")
            if rows.count() < minimum:
                trigger = collection.locator(
                    "[data-ei-collection-select]" if mode == "selection" else "[data-ei-collection-add]"
                ).first
                if not trigger.count() or not trigger.is_visible():
                    raise DynamicFieldContractError(
                        f"动态字段契约缺失：{field_code} 没有可新增行"
                    )
                trigger.scroll_into_view_if_needed()
                choice = trigger.locator(
                    'input[type="checkbox"],input[type="radio"],[role="checkbox"],[role="radio"]'
                ).first
                (choice if choice.count() else trigger).click(force=True)
                rows = self._wait_for_collection_rows(collection, minimum, field_code)
            for index in range(minimum):
                row = rows.nth(index)
                configured_children = self._collection_child_field_codes(row)
                missing_children = sorted(expected_children - configured_children)
                if missing_children:
                    raise DynamicFieldContractError(
                        f"动态字段契约缺失：{field_code} 子字段配置不完整："
                        + ", ".join(missing_children)
                    )
                row_submitted = self._fill_dialog(dom_scope=row)
                if not row_submitted:
                    raise DynamicFieldContractError(
                        f"动态字段契约缺失：{field_code} 明细项没有可填写子字段"
                    )
                submitted.update(row_submitted)
            item_keys = collection.locator("[data-ei-collection-item]").evaluate_all(
                "els => els.map((el) => (el.getAttribute('data-ei-collection-item-key') || '').trim()).filter(Boolean)"
            )
            submitted[field_code] = ",".join(str(key) for key in item_keys) or mode
            self._collection_submission_codes.add(field_code)
        return submitted

    def _fill_dialog(
        self,
        only_codes: set[str] | None = None,
        *,
        dom_scope=None,
    ) -> dict[str, Any]:
        source_identity = dom_scope is None
        if dom_scope is None:
            dom_scope = getattr(self, "_common_form_scope", None)
        submitted: dict[str, Any] = {}
        failures: list[str] = []
        optional_failures: list[str] = []
        scan_fields = lambda: (
            scan_dom_fields(self.page, dom_scope)
            if dom_scope is not None
            else scan_dom_fields(self.page)
        )
        collection_scope = dom_scope or getattr(self, "_form_scope_for_collections", None)
        configured_collections = bool(getattr(self, "dynamic_collections", ()))
        # Conditional collection roots can depend on a normal baseline field.
        # Fill those ordinary fields first, then create and fill configured rows.
        if (
            source_identity
            and collection_scope is not None
            and hasattr(collection_scope, "evaluate")
            and not configured_collections
        ):
            submitted.update(self._prepare_dynamic_collection_baselines(collection_scope))
        fields = scan_fields()
        field_count = max(len(fields), len(self.source_fields) if source_identity else 0)
        for index in range(1, field_count + 1):
            current_fields = scan_fields()
            if index > len(current_fields):
                break
            dom = current_fields[index - 1]
            if dom.readonly or not dom.field_code:
                continue
            if (
                source_identity
                and configured_collections
                and collection_scope is not None
                and self._dom_field_is_in_configured_collection(collection_scope, dom)
            ):
                continue
            if source_identity:
                (
                    field_code,
                    field_label,
                    source_qcc,
                    source_code,
                    _source_label,
                    _source_identity_safe,
                ) = self._runtime_identity_for_dom(dom, index)
            else:
                (
                    field_code,
                    field_label,
                    source_qcc,
                    source_code,
                    _source_label,
                    _source_identity_safe,
                ) = self._runtime_identity_for_nested_dom(dom, index)
            if only_codes is not None and field_code.lower() not in only_codes:
                continue
            definition = FieldDefinition(
                field_code=field_code, field_name=field_label,
                field_type=TYPE_BY_KIND.get(dom.kind, "ElInput-TEXT"),
                required=dom.required, readonly=dom.readonly, source="runtime-dom",
            )
            preferred_choice = None
            preferred_choice_for = getattr(
                self.data_strategy, "preferred_choice_for", None
            )
            if callable(preferred_choice_for) and dom.kind in {
                "select", "multi_select", "radio", "checkbox",
            }:
                choice_definition = FieldDefinition(
                    field_code=source_code or field_code,
                    field_name=field_label,
                    field_type=definition.field_type,
                    required=definition.required,
                    readonly=definition.readonly,
                    source=definition.source,
                )
                preferred_choice = preferred_choice_for(choice_definition)
            has_value = (
                self._dom_field_has_value(
                    dom, root=dom_scope, field_code=field_code, field_label=field_label,
                )
                if dom_scope is not None
                else self._dom_field_has_value(
                    dom, field_code=field_code, field_label=field_label,
                )
            )
            if has_value and preferred_choice in (None, "", []):
                continue
            if (
                dom.kind in {"select", "multi_select"}
                and not dom.required
                and field_code.lower() in self.OPTIONAL_RELATIONSHIP_FIELDS
            ):
                continue
            value = self.data_strategy.value_for(definition, index)
            try:
                if dom.kind == "radio":
                    radios = self._radio_group(
                        field_code, dom.selector, dom_scope=dom_scope,
                        field_label=field_label,
                    )
                    actual = self._select_radio_choice(
                        radios,
                        preferred_choice,
                        prefer_last=(
                            not preferred_choice
                            and field_code in {"registerStatus", "isRegister"}
                            and radios.count() > 1
                        ),
                    )
                elif dom.kind in {"select", "multi_select"} and field_label:
                    if source_code == "orgGroupId":
                        actual = self._select_group_with_backtracking(
                            dom_scope=dom_scope
                        )
                    elif source_code == "registerStatus":
                        actual = self._select_by_label(
                            field_label, option_index=1, field_code=source_code,
                            qcc_remote=source_qcc or dom.qcc_remote,
                            dom_scope=dom_scope,
                        )
                    else:
                        select_kwargs = {
                            "field_code": field_code,
                            "selector": dom.selector,
                            "qcc_remote": source_qcc or dom.qcc_remote,
                            "dom_scope": dom_scope,
                        }
                        if preferred_choice not in (None, "", []):
                            select_kwargs["preferred_value"] = preferred_choice
                        actual = self._select_by_label(field_label, **select_kwargs)
                else:
                    resolved = ResolvedField(definition, dom)
                    actual = (
                        self.interactor.fill(resolved, value, root=dom_scope)
                        if dom_scope is not None
                        else self.interactor.fill(resolved, value)
                    )
                if actual not in (None, "", []):
                    submitted.setdefault(definition.field_code, actual)
                wait_for_timeout = getattr(self.page, "wait_for_timeout", None)
                if callable(wait_for_timeout):
                    wait_for_timeout(500)
            except Exception as exc:
                detail = f"{dom.label or dom.field_code} ({field_code}): {exc}"
                (failures if dom.required else optional_failures).append(detail)
        if (
            source_identity
            and configured_collections
            and collection_scope is not None
            and hasattr(collection_scope, "evaluate")
        ):
            submitted.update(self._prepare_configured_dynamic_collections(collection_scope))
            self._assert_configured_dynamic_collection_controls(collection_scope)
        self._fill_failures = failures
        self._optional_fill_failures = optional_failures
        return submitted

    def _retry_field_codes(self, submitted: dict[str, Any]) -> set[str]:
        retry_codes: set[str] = set()
        submitted_codes = {code.lower() for code in submitted}
        dom_scope = getattr(self, "_common_form_scope", None)
        dom_fields = (
            scan_dom_fields(self.page, dom_scope)
            if dom_scope is not None
            else scan_dom_fields(self.page)
        )
        for index, dom in enumerate(
            dom_fields, start=1
        ):
            code, _label, *_ = self._runtime_identity_for_dom(dom, index)
            semantic_choice_value = (
                dom.kind in {"select", "multi_select", "radio", "checkbox"}
                and code.lower() in submitted_codes
            )
            has_value = (
                self._dom_field_has_value(
                    dom, root=dom_scope, field_code=code, field_label=_label,
                )
                if dom_scope is not None
                else self._dom_field_has_value(
                    dom, field_code=code, field_label=_label,
                )
            )
            if not dom.readonly and not has_value and not semantic_choice_value:
                retry_codes.add(code.lower())
        for failure in self._fill_failures:
            match = re.search(r"\(([^()]+)\):", failure)
            if match:
                retry_codes.add(match.group(1).lower())
        return retry_codes

    def check_field_completion(
        self, submitted: dict[str, Any], fill_failed: list[str] | None = None,
    ) -> FieldCompletionReport:
        """Inspect the actual visible form controls and their current DOM values."""
        dom_scope = getattr(self, "_common_form_scope", None)
        dom_fields = (
            scan_dom_fields(self.page, dom_scope)
            if dom_scope is not None
            else scan_dom_fields(self.page)
        )
        resolved_fields = []
        for index, field in enumerate(dom_fields, 1):
            code, label, *_ = self._runtime_identity_for_dom(field, index)
            resolved_fields.append((field, code, label))
        rendered_codes = {code.lower() for _, code, _ in resolved_fields if code}
        self._expected_not_rendered = [
            self._field_display(code, label)
            for code, label, *_ in self.source_fields
            if code.lower() not in rendered_codes
        ]
        submitted_codes = {code.lower() for code in submitted}
        not_filled = []
        optional_not_filled = []
        for field, code, label in resolved_fields:
            if field.readonly:
                continue
            has_semantic_choice_value = (
                field.kind in {"select", "multi_select", "radio", "checkbox"}
                and code.lower() in submitted_codes
            )
            has_value = (
                self._dom_field_has_value(
                    field, root=dom_scope, field_code=code, field_label=label,
                )
                if dom_scope is not None
                else self._dom_field_has_value(
                    field, field_code=code, field_label=label,
                )
            )
            if not has_value and not has_semantic_choice_value:
                target = not_filled if field.required else optional_not_filled
                target.append(self._field_display(code, label))
        return FieldCompletionReport(
            [], self._deduplicate(not_filled),
            self._deduplicate(list(fill_failed or [])),
            self._deduplicate(optional_not_filled),
            self._deduplicate(getattr(self, "_optional_fill_failures", [])),
        )

    def _source_field_is_visible(self, label: str) -> bool:
        try:
            nodes = self.page.get_by_text(label, exact=True)
            return any(nodes.nth(index).is_visible() for index in range(nodes.count()))
        except (AttributeError, TypeError):
            return True
        except Exception:
            return False

    def write_field_diagnostics(
        self, report: FieldCompletionReport, submitted: dict[str, Any], attempts: int,
    ) -> Path:
        """Write a machine-readable report for the next AI repair iteration."""
        root = Path(os.getenv("EI_FIELD_DIAGNOSTICS_DIR", "artifacts/field-diagnostics"))
        root.mkdir(parents=True, exist_ok=True)
        module_id = os.getenv("EI_MODULE_ID", "unknown-module")
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", module_id).strip("_") or "unknown-module"
        fields = []
        for field in scan_dom_fields(self.page):
            fields.append({
                "fieldCode": field.field_code,
                "label": field.label,
                "kind": field.kind,
                "selector": field.selector,
                "required": field.required,
                "readonly": field.readonly,
                "hasValue": True if field.readonly else self._dom_field_has_value(field),
            })
        payload = {
            "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
            "moduleId": module_id,
            "moduleName": os.getenv("EI_MODULE_NAME", ""),
            "component": os.getenv("EI_COMPONENT", ""),
            "pageUrl": str(getattr(self.page, "url", "")).split("?", 1)[0],
            "attempts": attempts,
            "status": "passed" if report.ok else "needs_repair",
            "notLocated": report.not_located,
            "expectedNotRendered": getattr(self, "_expected_not_rendered", []),
            "notFilled": report.not_filled,
            "fillFailed": report.fill_failed,
            "optionalNotFilled": report.optional_not_filled,
            "optionalFillFailed": report.optional_fill_failed,
            "attachment": {
                "status": getattr(self, "last_attachment_report", AttachmentCompletionReport()).status,
                "uploaded": getattr(self, "last_attachment_report", AttachmentCompletionReport()).uploaded,
                "existing": getattr(self, "last_attachment_report", AttachmentCompletionReport()).existing,
                "pending": getattr(self, "last_attachment_report", AttachmentCompletionReport()).pending,
                "requestsObserved": getattr(self, "last_attachment_report", AttachmentCompletionReport()).requests_observed,
                "errors": getattr(self, "last_attachment_report", AttachmentCompletionReport()).errors,
                "classification": getattr(self, "last_attachment_report", AttachmentCompletionReport()).classification,
                "lifecycle": getattr(self, "last_attachment_report", AttachmentCompletionReport()).lifecycle,
            },
            "validationRepairs": getattr(self, "_validation_repairs", []),
            "submittedFieldCodes": sorted(submitted),
            "expectedFields": [
                {"fieldCode": code, "label": label, "qccRemote": bool(qcc)}
                for code, label, qcc in self.source_fields
            ],
            "actualFields": fields,
        }
        target = root / f"{safe_name}.json"
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    def _repair_visible_validation_errors(
        self, scope, submitted: dict[str, Any], *, trigger_blur: bool = True,
    ) -> dict[str, Any]:
        repaired: dict[str, Any] = {}
        max_attempts = max(0, int(os.getenv("EI_VALIDATION_REPAIR_ATTEMPTS", "3")))
        if max_attempts == 0 or not hasattr(self.data_strategy, "repair_value"):
            return repaired
        if trigger_blur:
            try:
                scope.locator("input:visible,textarea:visible").evaluate_all(
                    "elements => elements.forEach(element => element.blur())"
                )
                self.page.wait_for_timeout(200)
            except Exception:
                pass
        for attempt in range(1, max_attempts + 1):
            issues = self._collect_validation_issues()
            if not issues:
                break
            changed = False
            for issue in issues:
                definition, dom = self._definition_for_validation_issue(issue)
                if definition is None or dom is None:
                    continue
                old_value = repaired.get(definition.field_code, submitted.get(definition.field_code))
                result = self.data_strategy.repair_value(
                    definition, old_value, issue["message"], attempt
                )
                if result is None or dom.kind in {"select", "multi_select", "radio", "checkbox"}:
                    continue
                new_value, constraint = result
                try:
                    self.interactor.fill(ResolvedField(definition, dom), new_value)
                    locator = self.page.locator(dom.selector).first
                    try:
                        locator.blur()
                    except Exception:
                        pass
                    repaired[definition.field_code] = new_value
                    self._validation_repairs.append({
                        "fieldCode": definition.field_code,
                        "label": definition.field_name,
                        "oldValue": old_value,
                        "validationMessage": issue["message"],
                        "constraint": constraint,
                        "newValue": new_value,
                        "attempt": attempt,
                        "repairResult": "applied",
                    })
                    changed = True
                except Exception as exc:
                    self._validation_repairs.append({
                        "fieldCode": definition.field_code,
                        "validationMessage": issue["message"],
                        "constraint": constraint,
                        "attempt": attempt,
                        "repairResult": "failed",
                        "error": str(exc),
                    })
            if not changed:
                break
            self.page.wait_for_timeout(250)
        return repaired

    def _collect_validation_issues(self) -> list[dict[str, str]]:
        return self.page.evaluate(r"""
        () => [...document.querySelectorAll('.el-form-item__error,.ant-form-item-explain-error')]
          .filter(error => {
            const style = getComputedStyle(error);
            const rect = error.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
          }).map(error => {
            const item = error.closest('.el-form-item,.ant-form-item');
            const outer = error.closest('.purvar_form_item') || item;
            const control = item?.querySelector('input:not([type=hidden]),textarea,select,[contenteditable=true]');
            const firstLine = (outer?.innerText || '').split('\n').map(text => text.trim()).find(Boolean) || '';
            const code = item?.getAttribute('prop') || control?.getAttribute('name') || control?.id || '';
            const selector = control?.id ? `#${CSS.escape(control.id)}` :
              (control?.getAttribute('name') ? `[name="${CSS.escape(control.getAttribute('name'))}"]` : '');
            return {message: (error.textContent || '').trim(), code, label: firstLine.replace(/^\*\s*/, ''), selector};
          })
        """)

    def _definition_for_validation_issue(self, issue: dict[str, str]):
        dom_fields = scan_dom_fields(self.page)
        normalized_label = self._normalize_label(issue.get("label", ""))
        for index, dom in enumerate(dom_fields, 1):
            if issue.get("selector") and dom.selector == issue["selector"]:
                break
            if normalized_label and self._normalize_label(dom.label) == normalized_label:
                break
        else:
            return None, None
        source_code, source_label, *_ = self._source_for_dom(dom, index)
        field_code = source_code if self._is_generated_identifier(dom.field_code) else dom.field_code
        definition = FieldDefinition(
            field_code=field_code,
            field_name=dom.label or source_label,
            field_type=TYPE_BY_KIND.get(dom.kind, "ElInput-TEXT"),
            required=dom.required,
            readonly=dom.readonly,
            source="runtime-validation",
            props={
                "domKind": dom.kind,
                "maxlength": dom.maxlength,
                "min": dom.minimum,
                "max": dom.maximum,
                "step": dom.step,
                "pattern": dom.pattern,
            },
        )
        return definition, dom

    def _dom_field_has_value(
        self, field, *, root=None, field_code: str = "", field_label: str = "",
    ) -> bool:
        stable_code = field_code or field.field_code
        stable_label = field_label or field.label
        locator = (
            root.locator(field.selector).first
            if root is not None
            else self.page.locator(field.selector).first
        )
        try:
            if not locator.count():
                if field.kind in {"radio", "checkbox"}:
                    return self._choice_field_has_checked_value(
                        stable_code, stable_label, root=root,
                    )
                return False
            if field.kind == "file":
                return self._file_input_has_value(locator)
            if field.kind in {"radio", "checkbox"}:
                if bool(locator.evaluate("""el => {
                    const item = el.closest('.el-form-item,.ant-form-item,[prop]') || el;
                    return !!el.checked || el.getAttribute('aria-checked') === 'true' ||
                        !!item.querySelector?.('input:checked,[aria-checked="true"]');
                }""")):
                    return True
                return self._choice_field_has_checked_value(
                    stable_code, stable_label, root=root,
                )
            if not locator.is_visible():
                return False
            return bool(locator.evaluate("""el => {
                const type = (el.type || '').toLowerCase();
                const role = (el.getAttribute('role') || '').toLowerCase();
                const cls = `${el.className || ''}`.toLowerCase();
                if (type === 'radio' || type === 'checkbox' || role === 'radio' ||
                    role === 'checkbox' || role === 'switch' ||
                    role === 'radiogroup' || cls.includes('radio-group') ||
                    cls.includes('checkbox-group')) {
                    const item = el.closest('.el-form-item,.ant-form-item,[prop]');
                    return !!el.checked || el.getAttribute('aria-checked') === 'true' ||
                        !!item?.querySelector('input:checked,[aria-checked="true"]');
                }
                if (el.isContentEditable) return !!(el.textContent || '').trim();
                if (String(el.value ?? '').trim().length > 0) return true;
                const select = el.closest('.el-select,.el-cascader');
                const selected = select?.querySelectorAll(
                    '.el-select__selected-item:not(.is-transparent),.el-select__tags-text,' +
                    '.el-cascader__tags-text,' +
                    '.el-input__inner[value]'
                ) || [];
                const wrapper = select?.querySelector('.el-select__wrapper');
                const wrapperClone = wrapper?.cloneNode(true);
                wrapperClone?.querySelectorAll('.el-select__placeholder,[placeholder]').forEach(node => node.remove());
                const wrapperText = (wrapperClone?.innerText || '').trim();
                return !!wrapperText || Array.from(selected).some(node =>
                    !!(node.textContent || '').trim() ||
                    !!String(node.getAttribute?.('value') || '').trim()
                );
            }"""))
        except Exception:
            return False

    def _choice_field_has_checked_value(
        self, field_code: str, field_label: str, *, root=None,
    ) -> bool:
        """Read checked evidence from a stable radio/checkbox field container."""

        root_scope = root if root is not None else self.page
        selectors: list[str] = []
        if field_code and not self._is_generated_identifier(field_code):
            code = field_code.replace('"', r'\"')
            selectors.extend([
                f'[data-field-code="{code}"]',
                f'[field-code="{code}"]',
                f'[prop="{code}"]',
                f'.el-form-item[prop="{code}"]',
                f'.ant-form-item[prop="{code}"]',
            ])
        label = (field_label or "").strip()
        if label and not self._is_generated_identifier(label):
            label_text = json.dumps(label, ensure_ascii=False)
            selectors.extend([
                f'.purvar_form_item:has-text({label_text})',
                f'.el-col:has-text({label_text})',
                f'.el-form-item:has-text({label_text})',
                f'.ant-form-item:has-text({label_text})',
            ])
        checked_selector = (
            'input:checked,[aria-checked="true"],[role="radio"][aria-checked="true"],'
            '[role="checkbox"][aria-checked="true"],.el-radio.is-checked,'
            '.el-checkbox.is-checked,.is-checked input'
        )
        for selector in self._deduplicate(selectors):
            try:
                container = root_scope.locator(selector)
                if not container.count():
                    continue
                if container.locator(checked_selector).count():
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _field_display(code: str, label: str) -> str:
        if label and code and label != code:
            return f"{label} ({code})"
        return label or code or "未命名字段"

    @classmethod
    def _readback_label(cls, value: str) -> str:
        label = re.sub(r"^file\s*[:：]\s*", "", str(value or ""), flags=re.I)
        return cls._normalize_label(label)

    @staticmethod
    def _deduplicate(values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))

    def _radio_group(
        self, field_code: str, fallback_selector: str, *, dom_scope=None,
        field_label: str = "",
    ):
        root = dom_scope if dom_scope is not None else self.page
        if field_code and not self._is_generated_identifier(field_code):
            group = root.locator(
                f'[prop="{field_code}"] input[type="radio"],'
                f'[prop="{field_code}"] [role="radio"]'
            )
            if group.count():
                return group
            label = field_label or self._source_label_for_code(field_code)
            if label:
                label_text = json.dumps(label, ensure_ascii=False)
                labelled = root.locator(
                    f'.purvar_form_item:has-text({label_text}) input[type="radio"],'
                    f'.purvar_form_item:has-text({label_text}) [role="radio"],'
                    f'.el-col:has-text({label_text}) input[type="radio"],'
                    f'.el-col:has-text({label_text}) [role="radio"],'
                    f'.el-form-item:has-text({label_text}) input[type="radio"],'
                    f'.el-form-item:has-text({label_text}) [role="radio"]'
                )
                if labelled.count():
                    return labelled
        fallback = root.locator(fallback_selector)
        try:
            nested = fallback.locator('input[type="radio"],[role="radio"]')
            if nested.count():
                return nested
        except Exception:
            pass
        return fallback

    @staticmethod
    def _normalized_choice_value(value: Any) -> str:
        return re.sub(r"\s+", "", str(value or "")).lower()

    @classmethod
    def _radio_choice_values(cls, radio) -> tuple[str, ...]:
        values: list[str] = []
        for attribute in ("value", "data-value", "aria-label"):
            try:
                value = radio.get_attribute(attribute)
            except Exception:
                value = None
            if value not in (None, ""):
                values.append(str(value).strip())
        try:
            radio_item = radio.locator(
                "xpath=ancestor-or-self::*[@role='radio' or "
                "contains(concat(' ',normalize-space(@class),' '),' el-radio ')][1]"
            )
            if radio_item.count():
                text = str(radio_item.inner_text() or "").strip()
                if text:
                    values.append(text)
        except Exception:
            pass
        try:
            parent_text = str(radio.locator("xpath=..").inner_text() or "").strip()
            if parent_text:
                values.append(parent_text)
        except Exception:
            pass
        return tuple(dict.fromkeys(values))

    @classmethod
    def _select_radio_choice(
        cls, radios, preferred_value: Any = None, *, prefer_last: bool = False,
    ) -> str:
        count = radios.count()
        if count <= 0:
            raise AssertionError("单选字段没有可用选项")
        wanted = cls._normalized_choice_value(preferred_value)
        matches: list[tuple[Any, tuple[str, ...]]] = []
        for index in range(count):
            radio = radios.nth(index)
            values = cls._radio_choice_values(radio)
            if wanted and any(
                cls._normalized_choice_value(value) == wanted for value in values
            ):
                matches.append((radio, values))
        if wanted:
            if len(matches) != 1:
                raise AssertionError(
                    f"单选字段没有唯一匹配的基线选项：{preferred_value}; "
                    f"matches={len(matches)}"
                )
            target, values = matches[0]
        else:
            target = radios.nth(count - 1 if prefer_last else 0)
            values = cls._radio_choice_values(target)
        target.check(force=True)
        return next((value for value in values if value), str(preferred_value or ""))

    def _source_for_dom(self, dom, index: int) -> tuple[str, str, bool]:
        if dom.field_code and not self._is_generated_identifier(dom.field_code):
            match = next(
                (
                    field for field in self.source_fields
                    if self._source_code_matches_runtime(field[0], dom.field_code)
                ),
                None,
            )
            if match:
                return match
        normalized_label = self._normalize_label(dom.label)
        if normalized_label:
            match = next(
                (field for field in self.source_fields if self._normalize_label(field[1]) == normalized_label),
                None,
            )
            if match:
                return match
            semantic_match = next(
                (
                    field for field in self.source_fields
                    if self._semantic_numeric_source_identity_safe(
                        dom, field[0], field[1]
                    )
                ),
                None,
            )
            if semantic_match:
                return semantic_match
            semantic_code = self.SEMANTIC_LABEL_CODES.get(
                self._normalize_identity_label(dom.label)
            )
            if semantic_code:
                return (semantic_code, dom.label, bool(dom.qcc_remote))
        generated_choice = self._unique_generated_choice_source_for_dom(dom)
        if generated_choice:
            return generated_choice
        if self._generated_choice_has_only_option_label(dom):
            return (dom.field_code, dom.label, bool(dom.qcc_remote))
        if index <= len(self.source_fields):
            return self.source_fields[index - 1]
        return (dom.field_code, dom.label, False)

    def _unique_generated_choice_source_for_dom(self, dom):
        if not self._generated_choice_has_only_option_label(dom):
            return None
        candidates = []
        seen_codes = set()
        for field in self.source_fields:
            source_code = field[0] if field else ""
            source_label = field[1] if len(field) > 1 else ""
            if not self._generated_choice_source_identity_safe(
                dom, source_code, source_label
            ):
                continue
            normalized_code = (source_code or "").lower()
            if normalized_code in seen_codes:
                continue
            seen_codes.add(normalized_code)
            candidates.append(field)
        return candidates[0] if len(candidates) == 1 else None

    def _generated_choice_has_only_option_label(self, dom) -> bool:
        if not self._is_generated_identifier(dom.field_code):
            return False
        if dom.kind not in {"radio", "checkbox"}:
            return False
        normalized = self._normalize_identity_label(dom.label)
        return normalized in {"", "是", "否", "是否", "yes", "no"}

    def _source_label_for_code(self, field_code: str) -> str:
        normalized_code = (field_code or "").lower()
        match = next(
            (
                field
                for field in getattr(self, "source_fields", [])
                if field and str(field[0]).lower() == normalized_code
            ),
            None,
        )
        return str(match[1] or "") if match and len(match) > 1 else ""

    def _source_identity_safe_for_dom(
        self, dom, source_code: str, source_label: str
    ) -> bool:
        """Only trust source-order fallback when the rendered field proves identity.

        Element Plus generated ids are re-created on every render.  They can be
        enriched from source metadata only when the runtime label still matches
        the source business label; otherwise a missing select/radio would shift
        later controls onto the wrong source fields.
        """

        if not self._is_generated_identifier(dom.field_code):
            return True
        if not source_code or self._is_generated_identifier(source_code):
            return False
        label = (dom.label or "").strip()
        if self._generated_choice_source_identity_safe(dom, source_code, source_label):
            return True
        if not label or self._is_generated_identifier(label):
            return False
        source_label = (source_label or "").strip()
        if not source_label:
            return False
        if self._semantic_numeric_source_identity_safe(
            dom, source_code, source_label
        ):
            return True
        if self._normalize_label(label) == self._normalize_label(source_label):
            return True
        if self._normalize_identity_label(label) == self._normalize_identity_label(
            source_label
        ):
            return True
        return False

    @classmethod
    def _semantic_numeric_source_identity_safe(
        cls, dom, source_code: str, source_label: str
    ) -> bool:
        """Allow generated text inputs to recover source identity when only units differ."""

        if not cls._is_generated_identifier(dom.field_code):
            return False
        if dom.kind not in {"text", "number"}:
            return False
        if not is_semantic_numeric_field(source_code, source_label):
            return False
        runtime_label = cls._normalize_label_without_trailing_unit(dom.label)
        source_label = cls._normalize_label_without_trailing_unit(source_label)
        return bool(runtime_label and source_label and runtime_label == source_label)

    @staticmethod
    def _source_code_matches_runtime(source_code: str, runtime_code: str) -> bool:
        source = re.sub(r"\$\{[^}]+\}", "*", str(source_code or "").strip().strip("`")).lower()
        runtime = str(runtime_code or "").strip().strip("`").lower()
        if source == runtime:
            return True
        if "*" not in source:
            return False
        pattern = "^" + re.escape(source).replace(r"\*", r"[^.]+") + "$"
        return bool(re.match(pattern, runtime))

    @classmethod
    def _normalize_label_without_trailing_unit(cls, value: str) -> str:
        normalized = cls._normalize_identity_label(value)
        normalized = re.sub(r"[：:]", "", normalized)
        normalized = re.sub(r"(?:（[^）]*）|\([^)]*\))$", "", normalized)
        normalized = normalized.replace("%", "").replace("％", "")
        for unit in ("万元", "亿元", "万", "元", "年", "月", "天"):
            if normalized.endswith(unit) and len(normalized) > len(unit):
                normalized = normalized[: -len(unit)]
                break
        return normalized

    def _generated_choice_source_identity_safe(
        self, dom, source_code: str, source_label: str
    ) -> bool:
        """Allow generated radio/checkbox ids to use source identity only when semantic.

        Some Purvar/Element Plus yes-or-no controls expose only per-render
        ``el-id-*`` radio ids, while the stable business label is rendered
        outside the inner option DOM.  Mapping those controls by plain position
        is unsafe, so this fallback is limited to generated choice controls
        whose source code/label clearly describes a boolean or status choice.
        """

        if not self._is_generated_identifier(dom.field_code):
            return False
        if dom.kind not in {"radio", "checkbox"}:
            return False
        normalized_label = self._normalize_label(source_label)
        normalized_code = re.sub(r"[^a-z0-9]", "", source_code or "", flags=re.I).lower()
        if not normalized_label or normalized_label in {"是", "否", "是否"}:
            return False
        if "是否" in normalized_label or normalized_label.startswith(("是否", "需不需要")):
            return True
        return (
            normalized_code.startswith(("is", "has", "need", "enable", "allow"))
            or normalized_code.endswith(("flag", "status", "state", "enabled"))
        )

    def _runtime_identity_for_dom(
        self, dom, index: int, *, source_identity: bool = True
    ) -> tuple[str, str, bool, str, str, bool]:
        """Resolve rendered DOM identity without unsafe positional remapping."""

        if not source_identity:
            code = dom.field_code or f"nested_field_{index}"
            return code, dom.label or code, bool(dom.qcc_remote), code, dom.label, False

        source_code, source_label, *rest = self._source_for_dom(dom, index)
        source_qcc = bool(rest[0]) if rest else False
        identity_safe = self._source_identity_safe_for_dom(
            dom, source_code, source_label
        )
        field_code = (
            source_code
            if self._is_generated_identifier(dom.field_code) and identity_safe
            else dom.field_code
        )
        use_source_label = identity_safe and (
            self._is_generated_identifier(dom.label)
            or self._generated_choice_has_only_option_label(dom)
        )
        field_label = (
            source_label
            if use_source_label
            or self._semantic_numeric_source_identity_safe(dom, source_code, source_label)
            else (dom.label or (source_label if identity_safe else ""))
        )
        return (
            field_code,
            field_label,
            source_qcc if identity_safe else bool(dom.qcc_remote),
            source_code if identity_safe else field_code,
            source_label,
            identity_safe,
        )

    @staticmethod
    def _normalize_label(value: str) -> str:
        normalized = re.sub(r"[：:*\s]", "", value or "")
        for prefix in ("请输入", "请填写", "请录入", "请选择", "请上传"):
            if normalized.startswith(prefix) and len(normalized) > len(prefix):
                return normalized[len(prefix):]
        return normalized

    @staticmethod
    def _is_generated_identifier(value: str) -> bool:
        return not value or value.lower().startswith("el-id-")

    def _select_group_with_backtracking(self, *, dom_scope=None) -> str:
        try:
            return self._select_by_label("所属小组", dom_scope=dom_scope)
        except AssertionError:
            pass
        for option_index in range(1, 8):
            self._select_by_label(
                "所属部门", option_index=option_index, dom_scope=dom_scope
            )
            self.page.wait_for_timeout(700)
            try:
                return self._select_by_label("所属小组", dom_scope=dom_scope)
            except AssertionError:
                continue
        raise AssertionError("已尝试多个部门，均没有可选小组")

    def _select_by_label(
        self, label: str, option_index: int = 0, field_code: str = "", qcc_remote: bool = False,
        selector: str = "", dom_scope=None, preferred_value: Any = None,
    ) -> str:
        dialog = (
            dom_scope
            if dom_scope is not None
            else self.page.locator('[role="dialog"]:visible,.el-dialog:visible').last
        )
        is_company_remote = qcc_remote or self._is_company_remote(field_code, label)
        lookup_label = label
        def resolve_row():
            label_node = dialog.get_by_text(lookup_label, exact=True).first
            row = label_node.locator(
                "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' purvar_form_item ')][1]"
            )
            if not row.count():
                row = dialog.locator(
                    ".purvar_form_item,.el-form-item"
                ).filter(has_text=lookup_label).first
            return row

        def resolve_select_control():
            row = resolve_row()
            wrapper = row.locator(
                ".el-select__wrapper,.el-cascader .el-input__wrapper"
            ).first
            scanned = None
            if selector:
                scanned = dialog.locator(selector).first
                if scanned.count():
                    if scanned.evaluate(
                        "el => el.matches('.el-select__wrapper,.el-input__wrapper')"
                    ):
                        wrapper = scanned
                    else:
                        nested = scanned.locator(
                            ".el-select__wrapper,.el-cascader .el-input__wrapper"
                        ).first
                        ancestor = scanned.locator(
                            "xpath=ancestor::*[contains(@class,'el-select__wrapper') "
                            "or contains(@class,'el-input__wrapper')][1]"
                        )
                        wrapper = nested if nested.count() else ancestor
            return scanned, wrapper

        row = resolve_row()
        if is_company_remote:
            if not row.count():
                raise AssertionError(f"未找到企查查字段 {field_code or label} 对应的输入控件")
            remote_input = row.locator("input").first
            remote_input.wait_for(state="visible", timeout=10_000)
            remote_input.click(force=True)
            keywords = [
                os.getenv("EI_QCC_KEYWORD", "北京汽车"),
                "上海科技",
                "深圳投资",
                "江苏科技",
            ]
            remote_input.fill(keywords[min(option_index, len(keywords) - 1)])
            self.page.wait_for_timeout(1_500)
        else:
            _scanned, wrapper = resolve_select_control()
            if not wrapper.count() or not wrapper.is_visible():
                raise AssertionError(f"未找到 {label} 的选择控件")
            wrapper.scroll_into_view_if_needed()
            wrapper.click(force=True)
        return self._select_business_value(
            # QccSelect is an Element Plus remote el-select.  Keep its control
            # resolver so options are scoped to the popper owned by this field
            # instead of looking only for legacy table/tree result widgets.
            resolve_select_control=resolve_select_control,
            field_code=field_code,
            lookup_label=lookup_label,
            option_index=option_index,
            is_company_remote=is_company_remote,
            preferred_value=preferred_value,
        )

    def _select_business_value(
        self,
        *,
        resolve_select_control: Callable[[], tuple[Any | None, Any]] | None,
        field_code: str,
        lookup_label: str,
        option_index: int,
        is_company_remote: bool,
        preferred_value: Any = None,
    ) -> str:
        """Wait for real options while reacquiring Element Plus ownership each poll."""

        timeout_ms = max(250, int(os.getenv("EI_SELECT_OPTIONS_TIMEOUT_MS", "5000")))
        poll_ms = max(50, int(os.getenv("EI_SELECT_OPTIONS_POLL_MS", "150")))
        attempts = max(2, (timeout_ms + poll_ms - 1) // poll_ms)
        option_selector = (
            ".el-select-dropdown__item:not(.is-disabled),"
            ".el-cascader-node:not(.is-disabled),"
            ".el-tree-node__content,.el-table__row"
        )

        for attempt in range(attempts):
            wrapper = None
            popper = None
            popper_visible = False
            try:
                scanned, wrapper = resolve_select_control()
                controls_id = self._select_controls_id(scanned, wrapper)
                if controls_id:
                    popper = self.page.locator(f"#{controls_id}")
                    popper_visible = bool(
                        popper.count() and popper.is_visible()
                    )
                else:
                    popper = self.page.locator(
                        ".el-popper:visible,.el-popover:visible"
                    ).last
                    popper_visible = bool(
                        popper.count() and popper.is_visible()
                    )
            except Exception:
                wrapper = None

            if popper_visible and popper is not None:
                options = popper.locator(option_selector)
            elif is_company_remote:
                # Older deployed QccSelect variants render results as a table
                # or tree.  This fallback is deliberately used only when the
                # current select has no owned visible popper.
                options = self.page.locator(
                    ".el-dialog:visible .el-table__row,"
                    ".el-popover:visible .el-table__row,"
                    ".el-popper:visible .el-tree-node__content"
                )
            else:
                options = self.page.locator(".__ei_no_select_options__")

            valid_options = []
            try:
                for index in range(options.count()):
                    option = options.nth(index)
                    text = (option.inner_text() or "").strip()
                    enabled = option.evaluate("""el => {
                        const disabled = el.closest('.is-disabled,[aria-disabled="true"]');
                        return !disabled;
                    }""")
                    in_viewport = option.evaluate("""el => {
                        const rect = el.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0
                            && rect.bottom > 0 && rect.top < innerHeight;
                    }""")
                    if (
                        option.is_visible()
                        and in_viewport
                        and enabled
                        and text
                        and not any(
                            word in text
                            for word in ("请选择", "全部", "暂无", "无数据", "加载")
                        )
                    ):
                        keyed_node = option.locator(
                            "xpath=ancestor-or-self::*[@data-key][1]"
                        )
                        business_value = (
                            keyed_node.get_attribute("data-key")
                            if keyed_node.count()
                            else text
                        )
                        valid_options.append((option, text, business_value or text))
            except Exception:
                valid_options = []
            if valid_options:
                wanted = self._normalized_choice_value(preferred_value)
                if wanted:
                    matches = [
                        item for item in valid_options
                        if wanted in {
                            self._normalized_choice_value(item[1]),
                            self._normalized_choice_value(item[2]),
                        }
                    ]
                    if len(matches) != 1:
                        raise AssertionError(
                            f"{lookup_label} 没有唯一匹配的基线选项："
                            f"{preferred_value}; matches={len(matches)}"
                        )
                    option, text, business_value = matches[0]
                else:
                    selection_index = (
                        0
                        if is_company_remote
                        else min(option_index, len(valid_options) - 1)
                    )
                    option, text, business_value = valid_options[selection_index]
                try:
                    option.click(force=True)
                    self.page.wait_for_timeout(500)
                    if field_code == "company":
                        confirm = self.page.locator(
                            ".el-dialog:visible button:has-text('确定'),"
                            ".el-popover:visible button:has-text('确定')"
                        ).last
                        if confirm.count() and confirm.is_visible():
                            confirm.click(force=True)
                            self.page.wait_for_timeout(500)
                    return business_value or text
                except Exception:
                    # Element Plus may replace both the option and its owner while
                    # the popper is rendering. Re-resolve them on the next poll.
                    valid_options = []

            if (
                not is_company_remote
                and not popper_visible
                and attempt > 0
                and attempt % 3 == 0
                and wrapper is not None
                and wrapper.count()
                and wrapper.is_visible()
            ):
                try:
                    wrapper.click(force=True)
                except Exception:
                    pass
            if attempt + 1 < attempts:
                self.page.wait_for_timeout(poll_ms)
        raise AssertionError(f"{lookup_label} 没有可选业务数据")

    @staticmethod
    def _select_controls_id(*controls) -> str:
        """Read the current select's owned popper id from wrapper or inner control."""

        for control in controls:
            if control is None:
                continue
            try:
                if not control.count():
                    continue
                controls_id = control.get_attribute("aria-controls") or ""
                if controls_id:
                    return controls_id
                nested = control.locator("[aria-controls]").first
                if nested.count():
                    controls_id = nested.get_attribute("aria-controls") or ""
                    if controls_id:
                        return controls_id
                owner = control.locator(
                    "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-select ') "
                    "or contains(concat(' ',normalize-space(@class),' '),' el-cascader ')][1]"
                )
                if owner.count():
                    owned_control = owner.locator("[aria-controls]").first
                    if owned_control.count():
                        controls_id = (
                            owned_control.get_attribute("aria-controls") or ""
                        )
                        if controls_id:
                            return controls_id
            except Exception:
                continue
        return ""

    @staticmethod
    def _is_company_remote(field_code: str, label: str) -> bool:
        company_codes = {"mcname", "companyname", "enterprisename", "entname"}
        return field_code.lower() in company_codes or any(
            token in label for token in ("公司全称", "企业全称", "企业名称")
        )

    @classmethod
    def _find_save_response(cls, responses, submitted: dict[str, Any] | None = None):
        candidates = []
        for response in responses:
            if response.request.method not in {"POST", "PUT", "PATCH"}:
                continue
            if not cls._is_business_mutation_url(response.url):
                continue
            if cls._is_non_business_mutation_endpoint(
                response.url, cls._request_payload(response.request)
            ):
                continue
            candidates.append(response)
        if not candidates or not submitted:
            return candidates[-1] if candidates else None
        expected_values = {
            str(value).strip() for value in submitted.values()
            if value not in (None, "", [])
        }
        if not expected_values:
            return candidates[-1]

        def score(response) -> tuple[int, int]:
            payload_values = cls._payload_scalar_values(
                cls._request_payload(response.request)
            )
            overlap = len(expected_values & payload_values)
            return overlap, candidates.index(response)

        scored = [(score(response), response) for response in candidates]
        overlapped = [item for item in scored if item[0][0] > 0]
        if overlapped:
            return max(overlapped, key=lambda item: item[0])[1]
        return candidates[-1]

    @classmethod
    def _is_business_mutation_url(cls, url: str) -> bool:
        """Whether a request path is a candidate CRUD save/submit action."""
        path = urlsplit(str(url or "")).path.lower()
        return any(token in path for token in cls.BUSINESS_MUTATION_URL_TOKENS)

    @classmethod
    def _is_non_business_mutation_endpoint(cls, url: str, payload: Any = None) -> bool:
        """Exclude attachment/list side effects from business save matching."""
        normalized = urlsplit(str(url or "")).path.lower()
        if any(
            token in normalized
            for token in (
                "/foundation/commfile/",
                "/foundation/oss/",
                "/oss/endpoint/file-list",
                "/commfile/savefilebatch",
                "/commfile/selectcommfilelist",
                "/attachment/",
                "/attachments/",
            )
        ):
            return True
        compact = re.sub(r"[^a-z0-9]+", "", normalized)
        if any(
            token in compact
            for token in (
                "savefilebatch",
                "selectcommfilelist",
                "filelist",
            )
        ):
            return True
        keys = cls._payload_key_names(payload)
        if (
            any(token in normalized for token in ("/file", "/oss", "commfile"))
            and keys
            and keys <= {
                "file",
                "files",
                "fileid",
                "fileids",
                "filelist",
                "filename",
                "filepath",
                "groupid",
                "functiontype",
                "functiondataid",
                "functiondataname",
                "functionreladataid",
                "moduledataid",
                "stage",
                "stagetype",
                "stagestep",
                "parentid",
                "relaparentid",
                "itemtype",
                "datatype",
                "platform",
                "limit",
                "data",
                "params",
                "page",
                "pagesize",
            }
        ):
            return True
        if any(token in compact for token in ("select", "query", "listpage")):
            return not any(token in compact for token in ("save", "add", "insert", "create"))
        return False

    @classmethod
    def _payload_key_names(cls, payload: Any) -> set[str]:
        keys: set[str] = set()
        if isinstance(payload, dict):
            for key, value in payload.items():
                keys.add(str(key).lower())
                keys.update(cls._payload_key_names(value))
        elif isinstance(payload, list):
            for value in payload:
                keys.update(cls._payload_key_names(value))
        return keys
