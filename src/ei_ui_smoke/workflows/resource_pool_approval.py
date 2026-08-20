from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from ..config import Settings
from ..data_pool import GlobalDataPool
from ..data_strategy import create_data_strategy
from ..dynamic_collections import load_dynamic_collection_specs
from ..module_driver import ModuleSmokeDriver
from ..source_form import SourceFormContract, discover_form_contract
from ..workflow import (
    WorkflowBuildContext,
    WorkflowCleanupDisposition,
    WorkflowConfigurationError,
    WorkflowDefinition,
    WorkflowResultError,
    WorkflowStep,
    WorkflowStepEvidence,
    WorkflowStepExecution,
    WorkflowStepResult,
)


WORKFLOW_ID = "resource-pool-approval"
CONFIG_ENV = "EI_RESOURCE_POOL_APPROVAL_CONFIG_JSON"
FORM_CODE = "POOL_RESOURCE"
COMPONENT = "projectResourcePool/index"
MODULE_ID = "EI::resourcePool"
MODULE_NAME = "资源池"

RESOURCE_LIST_PATH = "/ezgo/ei-service/projStorage/list"
RESOURCE_ADD_PATH = "/ezgo/ei-service/projStorage/add"
RESOURCE_DETAIL_PATH = "/ezgo/ei-service/projStorage/detail"
STORAGE_APPLICATION_DETAIL_PATH = "/ezgo/ei-service/projStorage/detail"
RESOURCE_RK_PATH = "/ezgo/ei-service/projStorage/rk"
TODO_PATH = "/ezgo/ezgo_api/client/v1/common/toDoTaskByPage"
FLOW_APP_ID = "app_49z06fqkug"
FORBIDDEN_APPROVAL_CALLBACK_PATH = "/projStorage/approval"
APPROVAL_SUCCESS_ENVELOPES = {
    "state": ("SUCCESS",),
    "code": ("000000",),
    "status": ("0",),
}

STATUS_DRAFT = "0"
STATUS_APPROVING = "1"
STATUS_APPROVED = "2"
STATE_SOURCE = "proj_storage_detail"
DEEP_STATE_SOURCE = "resource_pool_list_and_detail"
CORE_MODES = frozenset({"probe", "standard"})
STANDARD_ONLY = frozenset({"standard"})
STABLE_ONLY = frozenset({"stable"})

_PATH_TOKEN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SAFE_SCALAR = re.compile(r"^[A-Za-z0-9_.:/+\-]{1,256}$")
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|secret|storage.?state|token)", re.I
)
_PREVIEW_WRITE_VERB = re.compile(
    r"(?:add|save|update|delete|remove|approval|approve|complete|submit)",
    re.I,
)
_LOGIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")
WORKFLOW_LOGIN_PASSWORD_ENV = "EI_WORKFLOW_LOGIN_PASSWORD"


@dataclass(frozen=True, slots=True)
class TodoMatcher:
    field_path: str
    operator: str
    value_from: str
    query_key: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalButton:
    text: str = ""
    selector: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalResponse:
    method: str
    url_path: str
    business_code_path: str
    success_values: tuple[str, ...]
    response_business_id_path: str = ""
    request_business_id_path: str = ""
    request_business_id_query_key: str = ""


@dataclass(frozen=True, slots=True)
class ProcessPreview:
    method: str
    url_path: str
    request_id_key: str
    request_id_from: str
    request_body_path: str
    nodes_path: str
    node_status_path: str
    active_status_values: tuple[str, ...]
    assignee_login_name_path: str
    max_transitions: int


@dataclass(frozen=True, slots=True)
class ResourcePoolApprovalConfig:
    resource_pool_url: str
    todo_page_url: str
    todo_rows_path: str
    todo_open_url_path: str
    todo_matcher: TodoMatcher
    process_preview: ProcessPreview
    approval_button: ApprovalButton
    approval_response: ApprovalResponse
    approved_status: str
    approval_confirmation: ApprovalButton | None = None
    request_timeout_ms: int = 10_000
    mutation_quiet_ms: int = 500
    state_timeout_seconds: float = 60.0
    todo_timeout_seconds: float = 60.0


@dataclass(frozen=True, slots=True)
class _DriverResources:
    project_root: Path
    source_contract: SourceFormContract
    data_pool: GlobalDataPool
    dynamic_collections: tuple[Any, ...]
    data_mode: str
    run_id: str


@dataclass(frozen=True, slots=True)
class _HttpResult:
    status: int
    url: str
    body: Any


@dataclass(frozen=True, slots=True)
class _MutationObservation:
    request_count: int
    response_count: int
    http_status: int
    business_code: str
    request_url: str
    request_body: Any
    response_body: Any


def _require_object(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowConfigurationError(f"{label} 必须是 JSON object")
    return dict(value)


def _reject_unknown_keys(
    value: Mapping[str, Any], allowed: Iterable[str], *, label: str
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise WorkflowConfigurationError(
            f"{label} 包含不支持的配置项：{', '.join(unknown)}"
        )


def _required_text(value: Any, *, label: str, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text:
        raise WorkflowConfigurationError(f"{label} 不能为空")
    if len(text) > maximum:
        raise WorkflowConfigurationError(f"{label} 过长")
    return text


def _validate_path(value: Any, *, label: str) -> str:
    path = _required_text(value, label=label, maximum=256)
    if any(not _PATH_TOKEN.fullmatch(token) for token in path.split(".")):
        raise WorkflowConfigurationError(
            f"{label} 只能使用点分隔的 JSON object 字段路径"
        )
    return path


def _validate_page_url(value: Any, *, label: str) -> str:
    url = _required_text(value, label=label, maximum=2_048)
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise WorkflowConfigurationError(f"{label} 不是有效 URL") from exc
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username
        or parts.password
    ):
        raise WorkflowConfigurationError(
            f"{label} 必须是无内嵌凭证的 http/https 绝对 URL"
        )
    return url


def _positive_number(
    value: Any, *, label: str, default: int | float, integer: bool = False
) -> int | float:
    if value is None:
        return default
    if isinstance(value, bool):
        raise WorkflowConfigurationError(f"{label} 必须是正数")
    try:
        number = int(value) if integer else float(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowConfigurationError(f"{label} 必须是正数") from exc
    if number <= 0:
        raise WorkflowConfigurationError(f"{label} 必须是正数")
    return number


def _parse_todo_matcher(value: Any) -> TodoMatcher:
    item = _require_object(value, label="todo_matcher")
    _reject_unknown_keys(
        item,
        {"field_path", "operator", "value_from", "query_key"},
        label="todo_matcher",
    )
    field_path = _validate_path(item.get("field_path"), label="todo_matcher.field_path")
    operator = _required_text(
        item.get("operator"), label="todo_matcher.operator", maximum=32
    ).lower()
    if operator not in {"equals", "url_query_equals"}:
        raise WorkflowConfigurationError(
            "todo_matcher.operator 只能是 equals 或 url_query_equals"
        )
    value_from = _required_text(
        item.get("value_from"), label="todo_matcher.value_from", maximum=32
    )
    if value_from not in {"business_id", "projId"}:
        raise WorkflowConfigurationError(
            "todo_matcher.value_from 必须绑定 business_id 或 projId"
        )
    query_key = str(item.get("query_key") or "").strip()
    if operator == "url_query_equals":
        if not _PATH_TOKEN.fullmatch(query_key):
            raise WorkflowConfigurationError(
                "url_query_equals matcher 必须声明安全的 query_key"
            )
    elif query_key:
        raise WorkflowConfigurationError("equals matcher 不能声明 query_key")
    return TodoMatcher(field_path, operator, value_from, query_key)


def _parse_approval_button(value: Any, *, label: str = "approval_button") -> ApprovalButton:
    item = _require_object(value, label=label)
    _reject_unknown_keys(item, {"text", "selector"}, label=label)
    text = str(item.get("text") or "").strip()
    selector = str(item.get("selector") or "").strip()
    if bool(text) == bool(selector):
        raise WorkflowConfigurationError(
            f"{label} 必须且只能声明 text 或 selector"
        )
    if len(text) > 64 or len(selector) > 512:
        raise WorkflowConfigurationError(f"{label} 配置过长")
    return ApprovalButton(text=text, selector=selector)


def _normalized_url_path(value: Any, *, label: str) -> str:
    path = _required_text(value, label=label, maximum=512)
    parts = urlsplit(path)
    if (
        parts.scheme
        or parts.netloc
        or parts.query
        or parts.fragment
        or not parts.path.startswith("/")
    ):
        raise WorkflowConfigurationError(f"{label} 必须是无 query 的绝对 URL path")
    return "/" + parts.path.lstrip("/")


def _parse_approval_response(value: Any) -> ApprovalResponse:
    item = _require_object(value, label="approval_response")
    _reject_unknown_keys(
        item,
        {
            "method",
            "url_path",
            "business_code_path",
            "success_values",
            "response_business_id_path",
            "request_business_id_path",
            "request_business_id_query_key",
        },
        label="approval_response",
    )
    method = _required_text(
        item.get("method"), label="approval_response.method", maximum=16
    ).upper()
    if method not in {"POST", "PUT", "PATCH", "DELETE"}:
        raise WorkflowConfigurationError(
            "approval_response.method 必须是 POST/PUT/PATCH/DELETE"
        )
    url_path = _normalized_url_path(
        item.get("url_path"), label="approval_response.url_path"
    )
    if url_path.lower().rstrip("/").endswith(
        FORBIDDEN_APPROVAL_CALLBACK_PATH.lower()
    ):
        raise WorkflowConfigurationError(
            "审批动作禁止匹配 /projStorage/approval BPM 回调接口"
        )
    business_code_path = _validate_path(
        item.get("business_code_path"),
        label="approval_response.business_code_path",
    )
    response_business_id_path = str(
        item.get("response_business_id_path") or ""
    ).strip()
    request_business_id_path = str(
        item.get("request_business_id_path") or ""
    ).strip()
    request_business_id_query_key = str(
        item.get("request_business_id_query_key") or ""
    ).strip()
    if response_business_id_path:
        response_business_id_path = _validate_path(
            response_business_id_path,
            label="approval_response.response_business_id_path",
        )
    if request_business_id_path:
        request_business_id_path = _validate_path(
            request_business_id_path,
            label="approval_response.request_business_id_path",
        )
    if request_business_id_query_key and not _PATH_TOKEN.fullmatch(
        request_business_id_query_key
    ):
        raise WorkflowConfigurationError(
            "approval_response.request_business_id_query_key 必须是安全 query key"
        )
    identity_fields = (
        response_business_id_path,
        request_business_id_path,
        request_business_id_query_key,
    )
    if sum(bool(value) for value in identity_fields) != 1:
        raise WorkflowConfigurationError(
            "approval_response 必须且只能声明一种 identity："
            "response_business_id_path、request_business_id_path 或 "
            "request_business_id_query_key"
        )
    raw_success = item.get("success_values")
    if not isinstance(raw_success, list) or not raw_success:
        raise WorkflowConfigurationError(
            "approval_response.success_values 必须是非空数组"
        )
    success_values = tuple(dict.fromkeys(str(item).strip() for item in raw_success))
    if any(not value or not _SAFE_SCALAR.fullmatch(value) for value in success_values):
        raise WorkflowConfigurationError(
            "approval_response.success_values 只能包含安全的短标量"
        )
    expected_values = APPROVAL_SUCCESS_ENVELOPES.get(business_code_path)
    if success_values != expected_values:
        accepted = "、".join(
            f"{path}={values[0]}"
            for path, values in APPROVAL_SUCCESS_ENVELOPES.items()
        )
        raise WorkflowConfigurationError(
            "approval_response 只能使用源码确认的成功信封：" + accepted
        )
    return ApprovalResponse(
        method=method,
        url_path=url_path,
        business_code_path=business_code_path,
        success_values=success_values,
        response_business_id_path=response_business_id_path,
        request_business_id_path=request_business_id_path,
        request_business_id_query_key=request_business_id_query_key,
    )


def _parse_process_preview(value: Any) -> ProcessPreview:
    item = _require_object(value, label="process_preview")
    _reject_unknown_keys(
        item,
        {
            "method",
            "url_path",
            "request_id_key",
            "request_id_from",
            "request_body_path",
            "nodes_path",
            "node_status_path",
            "active_status_values",
            "assignee_login_name_path",
            "max_transitions",
        },
        label="process_preview",
    )
    method = _required_text(
        item.get("method"), label="process_preview.method", maximum=16
    ).upper()
    if method not in {"GET", "POST"}:
        raise WorkflowConfigurationError("process_preview.method 只能是 GET 或 POST")
    url_path = _normalized_url_path(
        item.get("url_path"), label="process_preview.url_path"
    )
    if _PREVIEW_WRITE_VERB.search(url_path):
        raise WorkflowConfigurationError("process_preview 必须指向明确的只读接口")
    request_id_key = _required_text(
        item.get("request_id_key"),
        label="process_preview.request_id_key",
        maximum=64,
    )
    if not _PATH_TOKEN.fullmatch(request_id_key):
        raise WorkflowConfigurationError("process_preview.request_id_key 必须是安全字段名")
    request_id_from = _required_text(
        item.get("request_id_from"),
        label="process_preview.request_id_from",
        maximum=32,
    )
    if request_id_from not in {"business_id", "projId"}:
        raise WorkflowConfigurationError(
            "process_preview.request_id_from 必须绑定 business_id 或 projId"
        )
    request_body_path = str(item.get("request_body_path") or "").strip()
    if method == "GET" and request_body_path:
        raise WorkflowConfigurationError("GET process_preview 不能声明 request_body_path")
    if request_body_path:
        request_body_path = _validate_path(
            request_body_path, label="process_preview.request_body_path"
        )
    raw_status_values = item.get("active_status_values")
    if not isinstance(raw_status_values, list) or not raw_status_values:
        raise WorkflowConfigurationError(
            "process_preview.active_status_values 必须是非空数组"
        )
    active_status_values = tuple(
        dict.fromkeys(str(status).strip() for status in raw_status_values)
    )
    if any(
        not status or not _SAFE_SCALAR.fullmatch(status)
        for status in active_status_values
    ):
        raise WorkflowConfigurationError(
            "process_preview.active_status_values 只能包含安全短标量"
        )
    return ProcessPreview(
        method=method,
        url_path=url_path,
        request_id_key=request_id_key,
        request_id_from=request_id_from,
        request_body_path=request_body_path,
        nodes_path=_validate_path(
            item.get("nodes_path"), label="process_preview.nodes_path"
        ),
        node_status_path=_validate_path(
            item.get("node_status_path"),
            label="process_preview.node_status_path",
        ),
        active_status_values=active_status_values,
        assignee_login_name_path=_validate_path(
            item.get("assignee_login_name_path"),
            label="process_preview.assignee_login_name_path",
        ),
        max_transitions=int(
            _positive_number(
                item.get("max_transitions"),
                label="process_preview.max_transitions",
                default=12,
                integer=True,
            )
        ),
    )


def _assert_no_sensitive_config(value: Any, *, path: str = "config") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if _SENSITIVE_KEY.search(str(key)):
                raise WorkflowConfigurationError(
                    f"{path} 禁止包含凭证或 storage-state 配置"
                )
            _assert_no_sensitive_config(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for child in value:
            _assert_no_sensitive_config(child, path=path)


def parse_resource_pool_approval_config(raw: str) -> ResourcePoolApprovalConfig:
    try:
        payload = json.loads(str(raw or ""))
    except json.JSONDecodeError as exc:
        raise WorkflowConfigurationError(f"{CONFIG_ENV} 不是有效 JSON") from exc
    item = _require_object(payload, label=CONFIG_ENV)
    _assert_no_sensitive_config(item)
    _reject_unknown_keys(
        item,
        {
            "resource_pool_url",
            "todo_page_url",
            "todo_rows_path",
            "todo_open_url_path",
            "todo_matcher",
            "process_preview",
            "approval_button",
            "approval_confirmation",
            "approval_response",
            "approved_status",
            "request_timeout_ms",
            "mutation_quiet_ms",
            "state_timeout_seconds",
            "todo_timeout_seconds",
        },
        label=CONFIG_ENV,
    )
    resource_pool_url = _validate_page_url(
        item.get("resource_pool_url"), label="resource_pool_url"
    )
    route = urlsplit(resource_pool_url).fragment.split("?", 1)[0].rstrip("/")
    if route != "/resourcePool":
        raise WorkflowConfigurationError(
            "resource_pool_url 必须指向已确认的 #/resourcePool 路由"
        )
    approved_status = _required_text(
        item.get("approved_status"), label="approved_status", maximum=16
    )
    if approved_status != STATUS_APPROVED:
        raise WorkflowConfigurationError("资源池审批通过状态必须是源码确认的 2")
    return ResourcePoolApprovalConfig(
        resource_pool_url=resource_pool_url,
        todo_page_url=_validate_page_url(
            item.get("todo_page_url"), label="todo_page_url"
        ),
        todo_rows_path=_validate_path(
            item.get("todo_rows_path"), label="todo_rows_path"
        ),
        todo_open_url_path=_validate_path(
            item.get("todo_open_url_path"), label="todo_open_url_path"
        ),
        todo_matcher=_parse_todo_matcher(item.get("todo_matcher")),
        process_preview=_parse_process_preview(item.get("process_preview")),
        approval_button=_parse_approval_button(item.get("approval_button")),
        approval_response=_parse_approval_response(item.get("approval_response")),
        approved_status=approved_status,
        approval_confirmation=(
            _parse_approval_button(
                item["approval_confirmation"], label="approval_confirmation"
            )
            if item.get("approval_confirmation") is not None
            else None
        ),
        request_timeout_ms=int(
            _positive_number(
                item.get("request_timeout_ms"),
                label="request_timeout_ms",
                default=10_000,
                integer=True,
            )
        ),
        mutation_quiet_ms=int(
            _positive_number(
                item.get("mutation_quiet_ms"),
                label="mutation_quiet_ms",
                default=500,
                integer=True,
            )
        ),
        state_timeout_seconds=float(
            _positive_number(
                item.get("state_timeout_seconds"),
                label="state_timeout_seconds",
                default=60.0,
            )
        ),
        todo_timeout_seconds=float(
            _positive_number(
                item.get("todo_timeout_seconds"),
                label="todo_timeout_seconds",
                default=60.0,
            )
        ),
    )


def _load_config() -> ResourcePoolApprovalConfig:
    raw = os.getenv(CONFIG_ENV, "").strip()
    if not raw:
        raise WorkflowConfigurationError(
            f"选择 {WORKFLOW_ID} 时必须配置 {CONFIG_ENV}"
        )
    return parse_resource_pool_approval_config(raw)


def _build_driver_resources(build: WorkflowBuildContext) -> _DriverResources:
    settings = Settings.from_env()
    source_contract = discover_form_contract(settings.source_root, COMPONENT)
    data_pool = GlobalDataPool.from_directory(build.project_root / "data")
    collections = load_dynamic_collection_specs(
        build.project_root / "data", form_code=FORM_CODE, component=COMPONENT
    )
    return _DriverResources(
        project_root=build.project_root,
        source_contract=source_contract,
        data_pool=data_pool,
        dynamic_collections=tuple(collections),
        data_mode=build.data_mode,
        run_id=build.run_id,
    )


def _new_driver(page: Any, resources: _DriverResources) -> ModuleSmokeDriver:
    strategy = create_data_strategy(
        resources.data_mode,
        resources.data_pool,
        FORM_CODE,
        run_id=resources.run_id,
    )
    contract = resources.source_contract
    return ModuleSmokeDriver(
        page,
        strategy,
        source_fields=list(contract.fields),
        source_branch_candidates=contract.branch_candidates,
        source_detail_endpoints=contract.detail_endpoints,
        default_upload_file=resources.data_pool.default_upload_file(),
        dynamic_collections=list(resources.dynamic_collections),
        form_code=FORM_CODE,
        component=COMPONENT,
        automation_record_registry=(
            resources.project_root / "artifacts" / "automation-record-registry.json"
        ),
    )


@contextmanager
def _module_environment(config: ResourcePoolApprovalConfig):
    values = {
        "EI_MODULE_ID": MODULE_ID,
        "EI_MODULE_NAME": MODULE_NAME,
        "EI_COMPONENT": COMPONENT,
        "EI_FORM_CODE": FORM_CODE,
        "EI_FORM_URL": config.resource_pool_url,
        "EI_REQUIRE_ADD": "true",
        "EI_AUTOMATION_ACTION_SCOPE": "workflow-resource-pool-approval",
    }
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _event_value(value: Any) -> Any:
    return value() if callable(value) else value


def _request_method(request: Any) -> str:
    return str(_event_value(getattr(request, "method", "")) or "").upper()


def _request_url(request: Any) -> str:
    return str(_event_value(getattr(request, "url", "")) or "")


def _url_path(url: str) -> str:
    try:
        return urlsplit(str(url or "")).path.rstrip("/") or "/"
    except ValueError:
        return ""


def _request_matches(request: Any, *, method: str, path: str) -> bool:
    return (
        _request_method(request) == method
        and _url_path(_request_url(request)) == path.rstrip("/")
    )


def _request_body(request: Any) -> Any:
    value = _event_value(getattr(request, "post_data_json", None))
    if value is not None:
        return value
    raw = _event_value(getattr(request, "post_data", None))
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


class _MutationCapture:
    def __init__(
        self,
        page: Any,
        *,
        method: str,
        path: str,
        timeout_ms: int,
        quiet_ms: int,
        forbidden_paths: Iterable[str] = (),
    ) -> None:
        self.page = page
        self.method = method
        self.path = path
        self.timeout_ms = timeout_ms
        self.quiet_ms = quiet_ms
        self.forbidden_paths = tuple(item.rstrip("/").lower() for item in forbidden_paths)
        self.requests: list[Any] = []
        self.responses: list[Any] = []
        self.forbidden_requests: list[Any] = []
        self._last_activity_at: float | None = None

        def mark_activity() -> None:
            self._last_activity_at = time.monotonic()

        def request_listener(request: Any) -> None:
            request_path = _url_path(_request_url(request)).lower()
            forbidden = any(
                request_path.endswith(path) for path in self.forbidden_paths
            )
            matched = _request_matches(
                request, method=self.method, path=self.path
            )
            if forbidden:
                self.forbidden_requests.append(request)
            if matched:
                self.requests.append(request)
            if forbidden or matched:
                mark_activity()

        def response_listener(response: Any) -> None:
            request = _event_value(getattr(response, "request", None))
            if request is not None and _request_matches(
                request, method=self.method, path=self.path
            ):
                self.responses.append(response)
                mark_activity()

        self._request_listener = request_listener
        self._response_listener = response_listener

    def __enter__(self) -> _MutationCapture:
        self.page.on("request", self._request_listener)
        self.page.on("response", self._response_listener)
        return self

    def wait(self) -> None:
        deadline = time.monotonic() + self.timeout_ms / 1_000
        quiet_seconds = self.quiet_ms / 1_000
        while True:
            now = time.monotonic()
            if (
                self.responses
                and self._last_activity_at is not None
                and now - self._last_activity_at >= quiet_seconds
            ):
                return
            if now >= deadline:
                return
            remaining_ms = max(1, int((deadline - now) * 1_000))
            if self.responses and self._last_activity_at is not None:
                quiet_remaining_ms = max(
                    1,
                    int(
                        (
                            quiet_seconds
                            - (now - self._last_activity_at)
                        )
                        * 1_000
                    ),
                )
                remaining_ms = min(remaining_ms, quiet_remaining_ms)
            wait = getattr(self.page, "wait_for_timeout", None)
            wait_ms = min(100, remaining_ms)
            if callable(wait):
                wait(wait_ms)
            else:
                time.sleep(wait_ms / 1_000)

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        remove = getattr(self.page, "remove_listener", None)
        if callable(remove):
            remove("request", self._request_listener)
            remove("response", self._response_listener)

    def observation(
        self,
        *,
        business_code_path: str,
        success_values: Iterable[str],
    ) -> _MutationObservation:
        if self.forbidden_requests:
            raise WorkflowResultError(
                "审批 UI 触发了禁止的 /projStorage/approval BPM 回调接口"
            )
        if len(self.requests) != 1 or len(self.responses) != 1:
            raise WorkflowResultError(
                "业务迁移必须且只能捕获一次匹配的写请求和响应："
                f"requests={len(self.requests)}, responses={len(self.responses)}"
            )
        response = self.responses[0]
        status = int(_event_value(getattr(response, "status", 0)) or 0)
        if not 200 <= status < 300:
            raise WorkflowResultError(f"业务迁移 HTTP 状态不是 2xx：{status}")
        try:
            response_body = response.json()
        except Exception as exc:
            raise WorkflowResultError("业务迁移响应不是 JSON") from exc
        code = str(_extract_path(response_body, business_code_path) or "").strip()
        accepted = {str(item).strip() for item in success_values}
        if not code or code not in accepted:
            raise WorkflowResultError(f"业务迁移响应业务码失败：code={code!r}")
        return _MutationObservation(
            request_count=1,
            response_count=1,
            http_status=status,
            business_code=code,
            request_url=_request_url(self.requests[0]),
            request_body=_request_body(self.requests[0]),
            response_body=response_body,
        )


def _extract_path(value: Any, path: str) -> Any:
    current = value
    for token in str(path or "").split("."):
        if not isinstance(current, Mapping) or token not in current:
            return None
        current = current[token]
    return current


def _put_path(target: dict[str, Any], path: str, value: str) -> None:
    current = target
    parts = str(path).split(".")
    for token in parts[:-1]:
        child = current.get(token)
        if not isinstance(child, dict):
            child = {}
            current[token] = child
        current = child
    current[parts[-1]] = value


def _browser_json_request(
    page: Any,
    *,
    method: str,
    path: str,
    timeout_ms: int,
    body: Mapping[str, Any] | None = None,
) -> _HttpResult:
    result = page.evaluate(
        """async ({requestUrl, method, body, timeoutMs}) => {
          const headers = {'Content-Type': 'application/json'};
          const token = localStorage.getItem('accessToken');
          const tenant = localStorage.getItem('tenantId') || localStorage.getItem('tenant-id');
          if (token) headers.Authorization = token;
          if (tenant) {
            headers.tenantId = tenant;
            headers['x-tenant-id'] = tenant;
            headers['X-Tenant-Id'] = tenant;
          }
          headers['X-Language'] = localStorage.getItem('i18n-language') || 'zh_CN';
          const controller = new AbortController();
          const timer = setTimeout(() => controller.abort(), timeoutMs);
          try {
            const options = {
              method,
              headers,
              credentials: 'same-origin',
              signal: controller.signal,
            };
            if (body !== null) options.body = JSON.stringify(body);
            const response = await fetch(requestUrl, options);
            const text = await response.text();
            let parsed;
            try { parsed = JSON.parse(text); } catch { parsed = text; }
            return {status: response.status, url: response.url, body: parsed};
          } finally {
            clearTimeout(timer);
          }
        }""",
        {
            "requestUrl": path,
            "method": method,
            "body": dict(body) if body is not None else None,
            "timeoutMs": timeout_ms,
        },
    )
    if not isinstance(result, dict):
        raise WorkflowResultError("认证业务读取未返回结构化结果")
    return _HttpResult(
        status=int(result.get("status") or 0),
        url=str(result.get("url") or path),
        body=result.get("body"),
    )


def _assert_fixed_read_success(
    result: _HttpResult, *, operation: str
) -> Mapping[str, Any]:
    if not 200 <= result.status < 300:
        raise WorkflowResultError(f"{operation} HTTP 状态不是 2xx：{result.status}")
    if not isinstance(result.body, Mapping):
        raise WorkflowResultError(f"{operation} 未返回 JSON object")
    code = str(result.body.get("status", "")).strip()
    if code != "0":
        raise WorkflowResultError(f"{operation} 业务码失败：status={code!r}")
    return result.body


def _assert_flow_read_success(
    result: _HttpResult, *, operation: str
) -> Mapping[str, Any]:
    if not 200 <= result.status < 300:
        raise WorkflowResultError(f"{operation} HTTP 状态不是 2xx：{result.status}")
    if not isinstance(result.body, Mapping):
        raise WorkflowResultError(f"{operation} 未返回 JSON object")
    accepted = (
        str(result.body.get("state", "")).strip() == "SUCCESS"
        or str(result.body.get("code", "")).strip() == "000000"
        or str(result.body.get("status", "")).strip() == "0"
    )
    if not accepted:
        raise WorkflowResultError(f"{operation} 业务成功信封不匹配")
    return result.body


def _read_detail(
    page: Any,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    proj_id: str = "",
) -> str:
    query = {"id": business_id}
    if proj_id:
        query["projId"] = proj_id
    detail_path = (
        STORAGE_APPLICATION_DETAIL_PATH if proj_id else RESOURCE_DETAIL_PATH
    )
    result = _browser_json_request(
        page,
        method="GET",
        path=f"{detail_path}?{urlencode(query)}",
        timeout_ms=config.request_timeout_ms,
    )
    body = _assert_fixed_read_success(result, operation="资源池详情回读")
    data = body.get("data")
    if not isinstance(data, Mapping):
        raise WorkflowResultError("资源池详情回读缺少 data object")
    readback_id = str(data.get("id") or "").strip()
    if readback_id != business_id:
        raise WorkflowResultError(
            "资源池详情回读 business_id 不一致："
            f"expected={business_id}, actual={readback_id or '<empty>'}"
        )
    if proj_id:
        readback_proj_id = str(data.get("projId") or "").strip()
        if readback_proj_id != proj_id:
            raise WorkflowResultError(
                "资源池详情回读 projId 不一致："
                f"expected={proj_id}, actual={readback_proj_id or '<empty>'}"
            )
    return str(data.get("statusProj", data.get("status", "")) or "").strip()


def _read_list_record(
    page: Any,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    marker: str,
) -> Mapping[str, Any]:
    result = _browser_json_request(
        page,
        method="POST",
        path=RESOURCE_LIST_PATH,
        timeout_ms=config.request_timeout_ms,
        body={
            "currPage": 1,
            "pageSize": 100,
            "keyWord": marker,
            "status": [],
            "formCode": FORM_CODE,
        },
    )
    body = _assert_fixed_read_success(result, operation="资源池列表读取")
    rows = body.get("data")
    if not isinstance(rows, list):
        raise WorkflowResultError("资源池列表读取缺少 data array")
    matches = [
        row
        for row in rows
        if isinstance(row, Mapping) and str(row.get("id") or "").strip() == business_id
    ]
    if len(matches) != 1:
        raise WorkflowResultError(
            "资源池列表必须按同一 business_id 唯一定位记录："
            f"matches={len(matches)}"
        )
    scalar_values = {
        str(value).strip()
        for value in matches[0].values()
        if isinstance(value, (str, int)) and not isinstance(value, bool)
    }
    if marker not in scalar_values:
        raise WorkflowResultError("资源池列表目标 ID 未携带本轮自动化标识")
    return matches[0]


def _record_status(record: Mapping[str, Any]) -> str:
    return str(record.get("statusProj", record.get("status", "")) or "").strip()


def _goto(page: Any, url: str) -> None:
    if str(getattr(page, "url", "") or "") == url:
        page.reload(wait_until="domcontentloaded")
    else:
        page.goto(url, wait_until="domcontentloaded")


def _visible_exact(locator: Any, *, label: str) -> Any:
    matches = []
    for index in range(locator.count()):
        candidate = locator.nth(index)
        if candidate.is_visible():
            matches.append(candidate)
    if len(matches) != 1:
        raise WorkflowResultError(f"{label} 必须唯一可见：matches={len(matches)}")
    return matches[0]


def _resource_row(
    page: Any,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    marker: str,
) -> Any:
    _goto(page, config.resource_pool_url)
    _read_list_record(page, config, business_id=business_id, marker=marker)
    search = _visible_exact(
        page.locator("input[placeholder='请输入企业名称/企业简称']"),
        label="资源池关键字输入框",
    )
    search.fill(marker)
    query = _visible_exact(
        page.get_by_role("button", name="查询", exact=True),
        label="资源池查询按钮",
    )
    query.click()
    page.get_by_text(marker, exact=True).first.wait_for(
        state="visible", timeout=config.request_timeout_ms
    )
    rows = page.locator(".el-table__row:visible")
    matched = []
    for index in range(rows.count()):
        row = rows.nth(index)
        cells = row.locator("td,[role='cell']")
        texts = {re.sub(r"\s+", " ", text).strip() for text in cells.all_inner_texts()}
        if marker in texts:
            matched.append(row)
    if len(matched) != 1:
        raise WorkflowResultError(
            "资源池 UI 必须按本轮 ID 已核对标识唯一定位记录："
            f"matches={len(matched)}"
        )
    return matched[0]


def _row_action(row: Any, name: str) -> Any:
    candidates = []
    for role in ("button", "link"):
        locator = row.get_by_role(role, name=name, exact=True)
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible() and candidate.is_enabled():
                candidates.append(candidate)
    if len(candidates) != 1:
        raise WorkflowResultError(
            f"目标资源池记录必须有且只有一个可用“{name}”动作：matches={len(candidates)}"
        )
    return candidates[0]


def _workflow_marker(execution: WorkflowStepExecution) -> str:
    markers = tuple(execution.context.record_markers)
    if not markers:
        raise WorkflowResultError("资源池流程缺少本轮自动化记录标识")
    return markers[0]


def _poll_detail_status(
    execution: WorkflowStepExecution,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    expected: str,
    proj_id: str = "",
) -> tuple[str, int]:
    attempts = 0

    def probe() -> str:
        nonlocal attempts
        attempts += 1
        return _read_detail(
            execution.page,
            config,
            business_id=business_id,
            proj_id=proj_id,
        )

    actual = execution.wait_for_status(
        probe,
        expected=expected,
        business_id=business_id,
        state_source=STATE_SOURCE,
        timeout_seconds=config.state_timeout_seconds,
        interval_seconds=0.5,
    )
    return str(actual), attempts


def _poll_deep_status(
    execution: WorkflowStepExecution,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    proj_id: str,
    marker: str,
    expected: str,
) -> tuple[str, int]:
    attempts = 0

    def probe() -> str:
        nonlocal attempts
        attempts += 1
        record = _read_list_record(
            execution.page,
            config,
            business_id=business_id,
            marker=marker,
        )
        list_status = _record_status(record)
        detail_status = _read_detail(
            execution.page,
            config,
            business_id=business_id,
            proj_id=proj_id,
        )
        if list_status == expected and detail_status == expected:
            return expected
        return f"list={list_status or 'empty'};detail={detail_status or 'empty'}"

    actual = execution.wait_for_status(
        probe,
        expected=expected,
        business_id=business_id,
        state_source=DEEP_STATE_SOURCE,
        timeout_seconds=config.state_timeout_seconds,
        interval_seconds=0.5,
    )
    return str(actual), attempts


def _fixed_response_id(body: Any, path: str) -> str:
    return str(_extract_path(body, path) or "").strip()


def _mutation_evidence(
    observation: _MutationObservation,
    *,
    business_id: str,
    poll_attempts: int,
    mutation_business_id: str,
    mutation_id_source: str,
    mutation_correlation_key: str = "",
    response_business_id: str = "",
    next_action_available: bool | None = None,
    mutation_count: int = 1,
    request_count: int | None = None,
    response_count: int | None = None,
) -> WorkflowStepEvidence:
    return WorkflowStepEvidence(
        record_visible=True,
        action_available=True,
        next_action_available=next_action_available,
        readback_business_id=business_id,
        state_source=STATE_SOURCE,
        poll_attempts=poll_attempts,
        mutation_count=mutation_count,
        request_count=(
            observation.request_count if request_count is None else request_count
        ),
        response_count=(
            observation.response_count if response_count is None else response_count
        ),
        http_status=observation.http_status,
        business_success=True,
        business_code=observation.business_code,
        response_business_id=response_business_id,
        mutation_business_id=mutation_business_id,
        mutation_id_source=mutation_id_source,
        mutation_correlation_key=mutation_correlation_key,
    )


def _create_handler(
    config: ResourcePoolApprovalConfig, resources: _DriverResources
):
    def create(execution: WorkflowStepExecution) -> WorkflowStepResult:
        page = execution.page
        _goto(page, config.resource_pool_url)
        add = _visible_exact(
            page.get_by_role("button", name="新增", exact=True),
            label="资源池新增按钮",
        )
        if not add.is_enabled():
            raise WorkflowResultError("经办人资源池新增按钮不可用")
        with _module_environment(config):
            driver = _new_driver(page, resources)
            with _MutationCapture(
                page,
                method="POST",
                path=RESOURCE_ADD_PATH,
                timeout_ms=config.request_timeout_ms,
                quiet_ms=config.mutation_quiet_ms,
            ) as capture:
                module_result = driver.run()
                capture.wait()
        if module_result.mode not in ModuleSmokeDriver.CREATE_VERIFIED_MODES:
            raise WorkflowResultError("资源池新增未完成保存后精确回读")
        observation = capture.observation(
            business_code_path="status", success_values=("0",)
        )
        response_id = _fixed_response_id(observation.response_body, "data")
        business_id = str(module_result.business_id or "").strip()
        if not business_id or response_id != business_id:
            raise WorkflowResultError("资源池新增响应 ID 与 ModuleSmokeResult 不一致")
        checkpoint_result = WorkflowStepResult.from_module_result(
            module_result,
            page_scope=config.resource_pool_url,
            created_by_workflow=True,
            cleanup_allowed=True,
            cleanup_disposition=WorkflowCleanupDisposition("pending"),
        )
        execution.checkpoint(checkpoint_result)
        actual, attempts = _poll_detail_status(
            execution,
            config,
            business_id=business_id,
            expected=STATUS_DRAFT,
        )
        row = _resource_row(
            page,
            config,
            business_id=business_id,
            marker=_workflow_marker(execution),
        )
        _row_action(row, "入库申请")
        result = WorkflowStepResult.from_module_result(
            module_result,
            page_scope=config.resource_pool_url,
            actual_status=actual,
            observations={
                "business_code": observation.business_code,
                "http_status": observation.http_status,
                "mutation_count": 1,
                "poll_attempts": attempts,
                "request_count": observation.request_count,
                "response_count": observation.response_count,
                "state_source": STATE_SOURCE,
                "transition": "none>0",
            },
            created_by_workflow=True,
            cleanup_allowed=True,
            step_evidence=_mutation_evidence(
                observation,
                business_id=business_id,
                poll_attempts=attempts,
                mutation_business_id=business_id,
                mutation_id_source="response",
                response_business_id=business_id,
                next_action_available=True,
            ),
            cleanup_disposition=WorkflowCleanupDisposition("pending"),
        )
        return result

    return create


def _submit_handler(
    config: ResourcePoolApprovalConfig, resources: _DriverResources
):
    def submit(execution: WorkflowStepExecution) -> WorkflowStepResult:
        business_id = execution.require_business_id()
        marker = _workflow_marker(execution)
        row = _resource_row(
            execution.page,
            config,
            business_id=business_id,
            marker=marker,
        )
        _row_action(row, "入库申请").click()
        with _module_environment(config):
            driver = _new_driver(execution.page, resources)
            with _MutationCapture(
                execution.page,
                method="POST",
                path=RESOURCE_RK_PATH,
                timeout_ms=config.request_timeout_ms,
                quiet_ms=config.mutation_quiet_ms,
            ) as capture:
                module_result = driver.save_open_dialog(
                    "提交", established_business_id=business_id
                )
                capture.wait()
        if module_result.business_id != business_id:
            raise WorkflowResultError("入库申请返回了不同的资源池 business_id")
        observation = capture.observation(
            business_code_path="status", success_values=("0",)
        )
        response_id = _fixed_response_id(observation.response_body, "data.id")
        proj_id = _fixed_response_id(observation.response_body, "data.projId")
        if response_id != business_id or not proj_id:
            raise WorkflowResultError("入库申请响应必须同时返回资源池 ID 和 projId")
        actual, attempts = _poll_detail_status(
            execution,
            config,
            business_id=business_id,
            proj_id=proj_id,
            expected=STATUS_APPROVING,
        )
        return WorkflowStepResult(
            business_id=business_id,
            page_scope=config.resource_pool_url,
            record_markers=execution.context.record_markers,
            actual_status=actual,
            observations={
                "business_code": observation.business_code,
                "http_status": observation.http_status,
                "mutation_count": 1,
                "poll_attempts": attempts,
                "request_count": observation.request_count,
                "response_count": observation.response_count,
                "state_source": STATE_SOURCE,
                "transition": "0>1",
            },
            step_evidence=_mutation_evidence(
                observation,
                business_id=business_id,
                poll_attempts=attempts,
                mutation_business_id=business_id,
                mutation_id_source="response",
                response_business_id=business_id,
            ),
            correlation_ids={"projId": proj_id},
        )

    return submit


def _query_values(url: str, key: str) -> list[str]:
    try:
        parts = urlsplit(url)
    except ValueError:
        return []
    values = list(parse_qs(parts.query, keep_blank_values=True).get(key, ()))
    if "?" in parts.fragment:
        values.extend(
            parse_qs(
                parts.fragment.split("?", 1)[1], keep_blank_values=True
            ).get(key, ())
        )
    return values


def _todo_matches(
    row: Mapping[str, Any],
    matcher: TodoMatcher,
    *,
    business_id: str,
    proj_id: str,
) -> bool:
    expected = business_id if matcher.value_from == "business_id" else proj_id
    value = _extract_path(row, matcher.field_path)
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return False
    text = str(value).strip()
    if matcher.operator == "equals":
        return text == expected
    return _query_values(text, matcher.query_key) == [expected]


def _read_todos(
    page: Any, config: ResourcePoolApprovalConfig
) -> list[Mapping[str, Any]]:
    result = _browser_json_request(
        page,
        method="POST",
        path=TODO_PATH,
        timeout_ms=config.request_timeout_ms,
        body={
            "page": {"page_index": 1, "page_size": 100},
            "query": {
                "app_id": FLOW_APP_ID,
                "agent_id": "",
                "create_time_end": "",
                "create_time_start": "",
                "end_time_end": "",
                "end_time_start": "",
                "subject": "",
                "time_sort": "desc",
            },
        },
    )
    body = _assert_flow_read_success(result, operation="审批人待办读取")
    rows = _extract_path(body, config.todo_rows_path)
    if not isinstance(rows, list):
        raise WorkflowResultError("审批待办 rows path 未返回 array")
    return [row for row in rows if isinstance(row, Mapping)]


def _wait_for_exact_todo(
    page: Any,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    proj_id: str,
) -> Mapping[str, Any]:
    deadline = time.monotonic() + config.todo_timeout_seconds
    while True:
        matches = [
            row
            for row in _read_todos(page, config)
            if _todo_matches(
                row,
                config.todo_matcher,
                business_id=business_id,
                proj_id=proj_id,
            )
        ]
        if len(matches) > 1:
            raise WorkflowResultError(
                f"审批待办 matcher 命中多条记录：matches={len(matches)}"
            )
        if matches:
            return matches[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise WorkflowResultError("审批待办在有界时间内未出现")
        wait = getattr(page, "wait_for_timeout", None)
        milliseconds = min(500, max(1, int(remaining * 1_000)))
        if callable(wait):
            wait(milliseconds)
        else:
            time.sleep(milliseconds / 1_000)


def _read_process_preview(
    page: Any,
    config: ResourcePoolApprovalConfig,
    *,
    business_id: str,
    proj_id: str,
) -> list[Mapping[str, Any]]:
    spec = config.process_preview
    identifier = business_id if spec.request_id_from == "business_id" else proj_id
    if spec.method == "GET":
        path = f"{spec.url_path}?{urlencode({spec.request_id_key: identifier})}"
        body = None
    else:
        body = {}
        if spec.request_body_path:
            _put_path(body, spec.request_body_path, identifier)
        else:
            body[spec.request_id_key] = identifier
        path = spec.url_path
    result = _browser_json_request(
        page,
        method=spec.method,
        path=path,
        timeout_ms=config.request_timeout_ms,
        body=body,
    )
    response = _assert_flow_read_success(result, operation="流程预览读取")
    nodes = _extract_path(response, spec.nodes_path)
    if not isinstance(nodes, list):
        raise WorkflowResultError("流程预览 nodes_path 未返回 array")
    return [node for node in nodes if isinstance(node, Mapping)]


def _next_preview_login_name(
    nodes: Iterable[Mapping[str, Any]], config: ResourcePoolApprovalConfig
) -> str:
    spec = config.process_preview
    active = [
        node
        for node in nodes
        if str(_extract_path(node, spec.node_status_path) or "").strip()
        in spec.active_status_values
    ]
    if not active:
        return ""
    names = {
        str(_extract_path(node, spec.assignee_login_name_path) or "").strip()
        for node in active
    }
    if "" in names or any(not _LOGIN_NAME.fullmatch(name) for name in names):
        raise WorkflowResultError("流程预览当前节点缺少唯一登录名")
    if len(names) != 1:
        raise WorkflowResultError("流程预览当前节点存在多个候选登录名")
    return next(iter(names))


def _safe_task_url(open_url: Any, *, todo_page_url: str) -> str:
    raw = _required_text(open_url, label="审批待办 open_url", maximum=2_048)
    resolved = urljoin(todo_page_url, raw)
    target = urlsplit(resolved)
    base = urlsplit(todo_page_url)
    if (
        target.scheme not in {"http", "https"}
        or not target.netloc
        or target.username
        or target.password
        or (target.scheme, target.netloc) != (base.scheme, base.netloc)
    ):
        raise WorkflowResultError("审批待办 open_url 必须是同部署的安全页面 URL")
    return resolved


def _approval_action(
    page: Any,
    spec: ApprovalButton,
    *,
    label: str,
    timeout_ms: int,
) -> Any:
    locator = (
        page.get_by_role("button", name=spec.text, exact=True)
        if spec.text
        else page.locator(spec.selector)
    )
    deadline = time.monotonic() + timeout_ms / 1_000
    last_count = 0
    while True:
        visible = []
        for index in range(locator.count()):
            candidate = locator.nth(index)
            if candidate.is_visible():
                visible.append(candidate)
        last_count = len(visible)
        if len(visible) > 1:
            raise WorkflowResultError(f"{label} 必须唯一可见：matches={len(visible)}")
        if len(visible) == 1 and visible[0].is_enabled():
            return visible[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if len(visible) == 1:
                raise WorkflowResultError(f"{label} 不可用")
            raise WorkflowResultError(f"{label} 必须唯一可见：matches={last_count}")
        wait_ms = min(100, max(1, int(remaining * 1_000)))
        wait = getattr(page, "wait_for_timeout", None)
        if callable(wait):
            wait(wait_ms)
        else:
            time.sleep(wait_ms / 1_000)


def _approval_identity(
    observation: _MutationObservation,
    spec: ApprovalResponse,
    *,
    business_id: str,
    proj_id: str,
) -> tuple[str, str, str, str]:
    if spec.response_business_id_path:
        response_id = _fixed_response_id(
            observation.response_body, spec.response_business_id_path
        )
        if response_id != proj_id:
            raise WorkflowResultError(
                "审批响应 business key 与已登记 projId 不一致"
            )
        return response_id, "response", "projId", response_id
    if spec.request_business_id_path:
        request_proj_id = _fixed_response_id(
            observation.request_body, spec.request_business_id_path
        )
    else:
        values = _query_values(
            observation.request_url, spec.request_business_id_query_key
        )
        request_proj_id = values[0] if len(values) == 1 else ""
    if request_proj_id != proj_id:
        raise WorkflowResultError("审批请求 business key 与已登记 projId 不一致")
    return request_proj_id, "request", "projId", ""


def _approve_handler(
    config: ResourcePoolApprovalConfig, project_root: Path
):
    def approve(execution: WorkflowStepExecution) -> WorkflowStepResult:
        business_id = execution.require_business_id()
        proj_id = execution.require_correlation_id("projId")
        response_spec = config.approval_response
        password = os.getenv(WORKFLOW_LOGIN_PASSWORD_ENV, "")
        if not password:
            raise WorkflowConfigurationError(
                f"动态审批流程必须配置 {WORKFLOW_LOGIN_PASSWORD_ENV}"
            )
        session_pool = execution.sessions
        maker_page_for = getattr(session_pool, "page_for", None)
        if not callable(maker_page_for):
            raise WorkflowResultError("动态审批流程缺少经办人会话")
        state_dir = (
            Path(project_root)
            / "artifacts"
            / "workflow-auth"
            / WORKFLOW_ID
            / "dynamic"
        )
        total_requests = 0
        total_responses = 0
        mutation_count = 0
        last_observation: _MutationObservation | None = None
        last_identity: tuple[str, str, str, str] | None = None
        final_task_url = ""
        actual = STATUS_APPROVING
        attempts = 0
        retained = False

        for _transition in range(config.process_preview.max_transitions):
            preview_page = maker_page_for("maker")
            login_name = _next_preview_login_name(
                _read_process_preview(
                    preview_page,
                    config,
                    business_id=business_id,
                    proj_id=proj_id,
                ),
                config,
            )
            if actual == config.approved_status and login_name:
                raise WorkflowResultError(
                    "资源池已审批通过，但流程预览仍存在待处理节点"
                )
            if not login_name:
                actual, attempts = _poll_detail_status(
                    execution,
                    config,
                    business_id=business_id,
                    proj_id=proj_id,
                    expected=config.approved_status,
                )
                break
            actor_page = execution.page_for_login(
                login_name,
                entry_url=config.todo_page_url,
                password=password,
                state_dir=state_dir,
            )
            _goto(actor_page, config.todo_page_url)
            todo = _wait_for_exact_todo(
                actor_page,
                config,
                business_id=business_id,
                proj_id=proj_id,
            )
            task_url = _safe_task_url(
                _extract_path(todo, config.todo_open_url_path),
                todo_page_url=config.todo_page_url,
            )
            actor_page.goto(task_url, wait_until="domcontentloaded")
            final_task_url = task_url
            action = _approval_action(
                actor_page,
                config.approval_button,
                label="审批动作按钮",
                timeout_ms=config.request_timeout_ms,
            )
            with _MutationCapture(
                actor_page,
                method=response_spec.method,
                path=response_spec.url_path,
                timeout_ms=config.request_timeout_ms,
                quiet_ms=config.mutation_quiet_ms,
                forbidden_paths=(FORBIDDEN_APPROVAL_CALLBACK_PATH,),
            ) as capture:
                action.click()
                if config.approval_confirmation is not None:
                    confirmation = _approval_action(
                        actor_page,
                        config.approval_confirmation,
                        label="审批二次确认按钮",
                        timeout_ms=config.request_timeout_ms,
                    )
                    confirmation.click()
                capture.wait()
            observation = capture.observation(
                business_code_path=response_spec.business_code_path,
                success_values=response_spec.success_values,
            )
            identity = _approval_identity(
                observation,
                response_spec,
                business_id=business_id,
                proj_id=proj_id,
            )
            if not retained:
                execution.checkpoint_cleanup(
                    WorkflowCleanupDisposition(
                        "retained", "approved_record_not_safely_deletable"
                    )
                )
                retained = True
            mutation_count += 1
            total_requests += observation.request_count
            total_responses += observation.response_count
            last_observation = observation
            last_identity = identity
            actual = _read_detail(
                actor_page,
                config,
                business_id=business_id,
                proj_id=proj_id,
            )
            if actual != STATUS_APPROVING:
                if actual != config.approved_status:
                    raise WorkflowResultError(
                        "动态审批后资源池状态不是审批中或审批通过"
                    )
        else:
            raise WorkflowResultError("动态审批超过 process_preview.max_transitions")

        if not last_observation or not last_identity or actual != config.approved_status:
            raise WorkflowResultError("流程预览未驱动任何审批动作到审批通过状态")
        (
            mutation_business_id,
            mutation_id_source,
            mutation_correlation_key,
            response_business_id,
        ) = last_identity
        return WorkflowStepResult(
            business_id=business_id,
            page_scope=str(
                getattr(execution.page, "url", "") or final_task_url
            ),
            record_markers=execution.context.record_markers,
            actual_status=actual,
            observations={
                "business_code": last_observation.business_code,
                "http_status": last_observation.http_status,
                "mutation_count": mutation_count,
                "poll_attempts": attempts,
                "request_count": total_requests,
                "response_count": total_responses,
                "state_source": STATE_SOURCE,
                "transition": "1>2",
            },
            step_evidence=_mutation_evidence(
                last_observation,
                business_id=business_id,
                poll_attempts=attempts,
                mutation_business_id=mutation_business_id,
                mutation_id_source=mutation_id_source,
                mutation_correlation_key=mutation_correlation_key,
                response_business_id=response_business_id,
                mutation_count=mutation_count,
                request_count=total_requests,
                response_count=total_responses,
            ),
            correlation_ids={"projId": proj_id},
        )

    return approve


def _readback_handler(config: ResourcePoolApprovalConfig):
    def readback(execution: WorkflowStepExecution) -> WorkflowStepResult:
        business_id = execution.require_business_id()
        proj_id = execution.require_correlation_id("projId")
        _resource_row(
            execution.page,
            config,
            business_id=business_id,
            marker=_workflow_marker(execution),
        )
        actual, attempts = _poll_detail_status(
            execution,
            config,
            business_id=business_id,
            proj_id=proj_id,
            expected=config.approved_status,
        )
        return WorkflowStepResult(
            business_id=business_id,
            page_scope=config.resource_pool_url,
            record_markers=execution.context.record_markers,
            actual_status=actual,
            observations={
                "business_status": actual,
                "poll_attempts": attempts,
                "state_source": STATE_SOURCE,
            },
            step_evidence=WorkflowStepEvidence(
                record_visible=True,
                action_available=False,
                readback_business_id=business_id,
                state_source=STATE_SOURCE,
                poll_attempts=attempts,
            ),
            correlation_ids={"projId": proj_id},
        )

    return readback


def _deep_readback_handler(config: ResourcePoolApprovalConfig):
    def readback(execution: WorkflowStepExecution) -> WorkflowStepResult:
        business_id = execution.require_business_id()
        proj_id = execution.require_correlation_id("projId")
        marker = _workflow_marker(execution)
        _resource_row(
            execution.page,
            config,
            business_id=business_id,
            marker=marker,
        )
        actual, attempts = _poll_deep_status(
            execution,
            config,
            business_id=business_id,
            proj_id=proj_id,
            marker=marker,
            expected=config.approved_status,
        )
        return WorkflowStepResult(
            business_id=business_id,
            page_scope=config.resource_pool_url,
            record_markers=execution.context.record_markers,
            actual_status=actual,
            observations={
                "business_status": actual,
                "poll_attempts": attempts,
                "state_source": DEEP_STATE_SOURCE,
            },
            step_evidence=WorkflowStepEvidence(
                record_visible=True,
                action_available=False,
                readback_business_id=business_id,
                state_source=DEEP_STATE_SOURCE,
                poll_attempts=attempts,
            ),
            correlation_ids={"projId": proj_id},
        )

    return readback


def _stable_health_handler(config: ResourcePoolApprovalConfig):
    def health(execution: WorkflowStepExecution) -> WorkflowStepResult:
        _goto(execution.page, config.resource_pool_url)
        _visible_exact(
            execution.page.locator(
                "input[placeholder='请输入企业名称/企业简称']"
            ),
            label="资源池关键字输入框",
        )
        result = _browser_json_request(
            execution.page,
            method="POST",
            path=RESOURCE_LIST_PATH,
            timeout_ms=config.request_timeout_ms,
            body={
                "currPage": 1,
                "pageSize": 1,
                "keyWord": "",
                "status": [],
                "formCode": FORM_CODE,
            },
        )
        body = _assert_fixed_read_success(result, operation="资源池只读健康检查")
        if not isinstance(body.get("data"), list):
            raise WorkflowResultError("资源池只读健康检查缺少 data array")
        return WorkflowStepResult(
            page_scope=config.resource_pool_url,
            observations={
                "business_code": str(body.get("status") or ""),
                "http_status": result.status,
                "state_source": "resource_pool_list",
            },
        )

    return health


def _stable_todo_health_handler(config: ResourcePoolApprovalConfig):
    def health(execution: WorkflowStepExecution) -> WorkflowStepResult:
        _goto(execution.page, config.todo_page_url)
        _read_todos(execution.page, config)
        return WorkflowStepResult(
            page_scope=str(
                getattr(execution.page, "url", "") or config.todo_page_url
            ),
            observations={
                "request_count": 1,
                "state_source": "approval_todo_list",
            },
        )

    return health


def build_workflow(build: WorkflowBuildContext) -> WorkflowDefinition:
    if build.workflow_id != WORKFLOW_ID:
        raise WorkflowConfigurationError(
            f"本 factory 只支持 EI_WORKFLOW_ID={WORKFLOW_ID}"
        )
    config = _load_config()
    if build.data_mode in CORE_MODES and not os.getenv(
        WORKFLOW_LOGIN_PASSWORD_ENV, ""
    ):
        raise WorkflowConfigurationError(
            f"{WORKFLOW_LOGIN_PASSWORD_ENV} 不能为空，动态审批账号无法登录"
        )
    resources = _build_driver_resources(build)
    return WorkflowDefinition(
        workflow_id=WORKFLOW_ID,
        title="资源池新增入库审批黄金流程",
        steps=(
            WorkflowStep(
                step_id="create-resource-pool",
                title="经办人新增资源池记录",
                role="maker",
                handler=_create_handler(config, resources),
                modes=CORE_MODES,
                produces_business_id=True,
                expected_status=STATUS_DRAFT,
                created_record=True,
                cleanup_allowed=True,
                step_type="mutation",
                requires_next_action=True,
            ),
            WorkflowStep(
                step_id="submit-storage-application",
                title="经办人提交入库申请",
                role="maker",
                handler=_submit_handler(config, resources),
                depends_on=("create-resource-pool",),
                modes=CORE_MODES,
                requires_business_id=True,
                requires_status=STATUS_DRAFT,
                expected_status=STATUS_APPROVING,
                step_type="mutation",
            ),
            WorkflowStep(
                step_id="approve-storage-application",
                title="按流程预览动态登录并审批通过",
                role="maker",
                handler=_approve_handler(config, resources.project_root),
                depends_on=("submit-storage-application",),
                modes=CORE_MODES,
                requires_business_id=True,
                requires_status=STATUS_APPROVING,
                expected_status=STATUS_APPROVED,
                step_type="dynamic_mutation",
            ),
            WorkflowStep(
                step_id="readback-approved-storage",
                title="经办人回查审批结果",
                role="maker",
                handler=_readback_handler(config),
                depends_on=("approve-storage-application",),
                modes=CORE_MODES,
                requires_business_id=True,
                requires_status=STATUS_APPROVED,
                expected_status=STATUS_APPROVED,
                step_type="read_only",
            ),
            WorkflowStep(
                step_id="deep-readback-approved-storage",
                title="经办人深度回查资源池与入库详情",
                role="maker",
                handler=_deep_readback_handler(config),
                depends_on=("readback-approved-storage",),
                modes=STANDARD_ONLY,
                requires_business_id=True,
                requires_status=STATUS_APPROVED,
                expected_status=STATUS_APPROVED,
                step_type="read_only",
            ),
            WorkflowStep(
                step_id="stable-resource-pool-health",
                title="经办人检查资源池只读健康状态",
                role="maker",
                handler=_stable_health_handler(config),
                modes=STABLE_ONLY,
                step_type="read_only",
            ),
        ),
    )
