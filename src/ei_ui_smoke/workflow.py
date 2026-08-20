from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import json
import math
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, ContextManager, Iterable, Mapping, Protocol
from urllib.parse import urlsplit, urlunsplit

from .allure_report import set_allure_module_metadata, set_allure_hidden_parameter
from .data_pool import atomic_write_json
from .module_driver import ModuleSmokeDriver, ModuleSmokeResult


WORKFLOW_DATA_MODES = frozenset({"probe", "stable", "standard"})
DEFAULT_WORKFLOW_DATA_MODES = frozenset({"probe", "standard"})
WORKFLOW_STEP_TYPES = frozenset({"read_only", "mutation", "dynamic_mutation"})
WORKFLOW_CLEANUP_DISPOSITIONS = frozenset({"pending", "deleted", "retained"})
AUTOMATION_MARKER_PREFIXES = ("AUTO_", "UI自动化")
_STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SENSITIVE_KEY_PARTS = (
    "accesskey",
    "apikey",
    "authorization",
    "body",
    "cookie",
    "credential",
    "headers",
    "localstorage",
    "password",
    "payload",
    "postdata",
    "requestbody",
    "requestheaders",
    "requestpayload",
    "responsebody",
    "responseheaders",
    "responsepayload",
    "secret",
    "privatekey",
    "sessionid",
    "sessionstorage",
    "storage",
    "storagestate",
    "submitted",
    "token",
)
_BEARER_VALUE = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_CREDENTIAL_VALUE = re.compile(
    r"(?i)(password|token|secret|authorization|cookie)"
    r"[A-Za-z0-9_-]*[\"']?\s*[:=]\s*[\"']?[^\"',;\s}\]]+"
)
_SAFE_OBSERVATION_KEYS = {
    "businesscode": "business_code",
    "businessstatus": "business_status",
    "httpstatus": "http_status",
    "moduleresultmode": "module_result_mode",
    "mutationcount": "mutation_count",
    "pollattempts": "poll_attempts",
    "requestcount": "request_count",
    "responsecount": "response_count",
    "statesource": "state_source",
    "statuscode": "status_code",
    "transition": "transition",
}
_COUNT_OBSERVATION_KEYS = frozenset(
    {"mutationcount", "pollattempts", "requestcount", "responsecount"}
)
_CODE_OBSERVATION_KEYS = frozenset({"businesscode", "statuscode"})
_IDENTIFIER_OBSERVATION_KEYS = frozenset({"moduleresultmode", "statesource"})
_SAFE_OBSERVATION_CODE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SAFE_OBSERVATION_TEXT = re.compile(
    r"^[A-Za-z0-9_.:/+>\-\u4e00-\u9fff ]{1,128}$"
)
_JWT_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_JWT_BEARER_PREFIX = re.compile(r"(?i)^bearer[ \t]+")
_JWT_PRINCIPAL_CLAIMS = (
    ("sub", "sub"),
    ("userId", "user_id"),
    ("user_id", "user_id"),
    ("uid", "uid"),
    ("user", "user"),
)
_WORKFLOW_LOGIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}$")
_LOGIN_NAME_SELECTOR = (
    'input[name="loginName"],input[placeholder*="账号"],'
    'input[placeholder*="用户名"]'
)
_LOGIN_PASSWORD_SELECTOR = 'input[name="password"],input[type="password"]'
_LOGIN_SUBMIT_SELECTOR = (
    'button[type="submit"],button.submit,.el-button--primary'
)
_OMIT_OBSERVATION = object()


class WorkflowConfigurationError(ValueError):
    """The selected workflow cannot be run safely as configured."""


class WorkflowPreconditionError(AssertionError):
    """A dependency, role, business identity, or prior state is missing."""


class WorkflowResultError(AssertionError):
    """A workflow step returned an incomplete or inconsistent result."""


class WorkflowSessionError(RuntimeError):
    """A role-scoped browser session could not be created or reused."""


class WorkflowStateTimeout(AssertionError):
    """A bounded business-state poll did not reach its expected value."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _normalized_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    if mode not in WORKFLOW_DATA_MODES:
        raise WorkflowConfigurationError(f"不支持的流程数据模式：{mode or '<empty>'}")
    return mode


def _stable_id(value: str, *, label: str) -> str:
    candidate = str(value or "").strip()
    if not _STABLE_ID.fullmatch(candidate):
        raise WorkflowConfigurationError(
            f"{label} 必须是稳定 ID，只能包含字母、数字、点、下划线和连字符"
        )
    return candidate


def page_scope_from_url(value: str) -> str:
    """Remove URL query data while retaining a stable hash-route scope."""
    try:
        parts = urlsplit(str(value or "").strip())
        hostname = parts.hostname or ""
        port = parts.port
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    if not hostname:
        return ""
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    fragment = parts.fragment.split("?", 1)[0]
    return urlunsplit((parts.scheme, netloc, parts.path, "", fragment))


def workflow_snapshot_path(
    project_root: Path, *, run_id: str, workflow_id: str
) -> Path:
    """Derive one collision-resistant artifact path without exposing it as config."""
    stable_workflow_id = _stable_id(workflow_id, label="workflow_id")
    raw_run_id = str(run_id or "").strip()
    if not raw_run_id:
        raise WorkflowConfigurationError("run_id 不能为空")
    segment = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw_run_id).strip("._-")
    if not segment:
        segment = "run"
    if segment != raw_run_id:
        digest = hashlib.sha256(raw_run_id.encode("utf-8")).hexdigest()[:10]
        segment = f"{segment[:80]}-{digest}"
    elif len(segment) > 100:
        digest = hashlib.sha256(raw_run_id.encode("utf-8")).hexdigest()[:10]
        segment = f"{segment[:80]}-{digest}"
    return (
        Path(project_root)
        / "artifacts"
        / "runs"
        / "workflows"
        / segment
        / f"{stable_workflow_id}.json"
    )


def _automation_markers(values: Iterable[Any]) -> tuple[str, ...]:
    markers: list[str] = []
    for value in values:
        marker = str(value or "").strip()
        if marker.startswith(AUTOMATION_MARKER_PREFIXES) and marker not in markers:
            markers.append(marker)
    return tuple(markers)


def _normalized_snapshot_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _snapshot_value(value: Any, *, key: str = "") -> Any:
    normalized_key = _normalized_snapshot_key(key)
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        descriptor = " ".join(
            str(value.get(candidate, ""))
            for candidate in ("name", "key", "field", "header", "property")
        )
        normalized_descriptor = _normalized_snapshot_key(descriptor)
        if any(part in normalized_descriptor for part in _SENSITIVE_KEY_PARTS):
            return "[REDACTED]"
        return {
            str(child_key): _snapshot_value(child_value, key=str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_snapshot_value(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return f"<{type(value).__name__}>"
    text = value
    if text.lstrip().startswith(("{", "[")):
        return "[REDACTED]"
    if text.startswith(("http://", "https://")):
        text = page_scope_from_url(text)
    text = _BEARER_VALUE.sub("Bearer [REDACTED]", text)
    text = _CREDENTIAL_VALUE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    return text if len(text) <= 1000 else text[:997] + "..."


def _safe_observation_value(normalized_key: str, value: Any) -> Any:
    if normalized_key in _COUNT_OBSERVATION_KEYS:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return _OMIT_OBSERVATION
    if normalized_key == "httpstatus":
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
        return _OMIT_OBSERVATION
    if normalized_key in _CODE_OBSERVATION_KEYS:
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        text = value.strip() if isinstance(value, str) else ""
        return text if _SAFE_OBSERVATION_CODE.fullmatch(text) else _OMIT_OBSERVATION
    if normalized_key in _IDENTIFIER_OBSERVATION_KEYS:
        text = value.strip() if isinstance(value, str) else ""
        return text if _SAFE_OBSERVATION_CODE.fullmatch(text) else _OMIT_OBSERVATION
    if normalized_key in {"businessstatus", "transition"}:
        text = value.strip() if isinstance(value, str) else ""
        return text if _SAFE_OBSERVATION_TEXT.fullmatch(text) else _OMIT_OBSERVATION
    return _OMIT_OBSERVATION


def _snapshot_observations(values: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in values.items():
        normalized_key = _normalized_snapshot_key(key)
        output_key = _SAFE_OBSERVATION_KEYS.get(normalized_key)
        if output_key is None:
            continue
        safe_value = _safe_observation_value(normalized_key, value)
        if safe_value is not _OMIT_OBSERVATION:
            safe[output_key] = safe_value
    return safe


def _normalized_correlation_ids(values: Mapping[str, str] | None) -> dict[str, str]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise WorkflowResultError("correlation_ids 必须是字符串映射")
    if len(values) > 32:
        raise WorkflowResultError("correlation_ids 最多允许 32 项")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        if not isinstance(raw_key, str) or not _STABLE_ID.fullmatch(raw_key.strip()):
            raise WorkflowResultError("correlation_ids key 必须是稳定 ID")
        key = raw_key.strip()
        normalized_key = _normalized_snapshot_key(key)
        if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
            raise WorkflowResultError(f"correlation_ids 禁止敏感 key：{key}")
        if not isinstance(raw_value, str):
            raise WorkflowResultError(f"correlation_ids[{key}] 必须是字符串")
        value = raw_value.strip()
        if (
            not _SAFE_OBSERVATION_TEXT.fullmatch(value)
            or value.startswith(("http://", "https://", "{", "["))
            or _BEARER_VALUE.search(value)
            or _CREDENTIAL_VALUE.search(value)
        ):
            raise WorkflowResultError(
                f"correlation_ids[{key}] 必须是安全的短标量"
            )
        normalized[key] = value
    return normalized


def _normalized_evidence_identity(value: Any, *, label: str) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str):
        raise WorkflowResultError(f"step evidence {label} 必须是安全的短标量")
    identity = value.strip()
    if not identity:
        return ""
    if (
        not _SAFE_OBSERVATION_TEXT.fullmatch(identity)
        or identity.startswith(("http://", "https://", "{", "["))
        or _BEARER_VALUE.search(identity)
        or _CREDENTIAL_VALUE.search(identity)
    ):
        raise WorkflowResultError(f"step evidence {label} 必须是安全的短标量")
    return identity


@dataclass(frozen=True, slots=True)
class RoleSessionSpec:
    role: str
    storage_state: str | Path = field(repr=False)

    def __post_init__(self) -> None:
        role = str(self.role or "").strip()
        state = str(self.storage_state or "").strip()
        if not role:
            raise WorkflowConfigurationError("角色名称不能为空")
        if not state:
            raise WorkflowConfigurationError(f"角色 {role} 缺少 storage state")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "storage_state", Path(state))


def parse_role_session_specs(
    raw: str, *, base_dir: Path
) -> tuple[RoleSessionSpec, ...]:
    """Parse role-to-file mappings; inline storage-state content is not accepted."""
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise WorkflowConfigurationError(f"角色配置重复：{key}")
            result[key] = value
        return result

    try:
        payload = json.loads(str(raw or ""), object_pairs_hook=unique_object)
    except json.JSONDecodeError as exc:
        raise WorkflowConfigurationError("角色 storage state 配置不是有效 JSON") from exc
    if not isinstance(payload, dict) or not payload:
        raise WorkflowConfigurationError(
            "EI_WORKFLOW_ROLE_STATES_JSON 必须是非空 role -> 文件路径对象"
        )
    specs: list[RoleSessionSpec] = []
    for role, state in payload.items():
        if not isinstance(state, str):
            raise WorkflowConfigurationError(
                f"角色 {str(role).strip() or '<empty>'} 只能配置 storage state 文件路径"
            )
        path = Path(state.strip())
        if not path.is_absolute():
            path = Path(base_dir) / path
        specs.append(RoleSessionSpec(str(role), path.resolve()))
    _role_spec_map(specs)
    return tuple(specs)


def _role_spec_map(
    specs: Iterable[RoleSessionSpec],
) -> dict[str, RoleSessionSpec]:
    mapped: dict[str, RoleSessionSpec] = {}
    duplicates: list[str] = []
    for spec in specs:
        if spec.role in mapped:
            duplicates.append(spec.role)
        mapped[spec.role] = spec
    if duplicates:
        raise WorkflowConfigurationError(
            "角色配置重复：" + ", ".join(dict.fromkeys(duplicates))
        )
    return mapped


def _decode_jwt_object(segment: str) -> Mapping[str, Any] | None:
    if not segment or not _JWT_SEGMENT.fullmatch(segment):
        return None
    padding = "=" * ((4 - len(segment) % 4) % 4)
    try:
        decoded = base64.b64decode(
            (segment + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        ).decode("utf-8")
        payload = json.loads(decoded)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
    ):
        return None
    return payload if isinstance(payload, Mapping) else None


def _finite_timestamp(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        timestamp = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return timestamp if math.isfinite(timestamp) else None


@dataclass(frozen=True, slots=True)
class _JwtEvidence:
    expires_at: float | None
    principal_fingerprint: str = ""


def _principal_fingerprint(payload: Mapping[str, Any]) -> str:
    for claim, canonical_claim in _JWT_PRINCIPAL_CLAIMS:
        value = payload.get(claim)
        if isinstance(value, str):
            stable_value = value.strip()
            if (
                not stable_value
                or len(stable_value) > 256
                or any(character.isspace() for character in stable_value)
                or any(ord(character) < 32 for character in stable_value)
            ):
                continue
        elif isinstance(value, int) and not isinstance(value, bool):
            stable_value = str(value)
        else:
            continue
        return hashlib.sha256(
            f"{canonical_claim}\0{stable_value}".encode("utf-8")
        ).hexdigest()
    return ""


def _jwt_evidence(value: Any) -> _JwtEvidence | None:
    if not isinstance(value, str):
        return None
    candidate = _JWT_BEARER_PREFIX.sub("", value.strip(), count=1)
    segments = candidate.split(".")
    if len(segments) != 3 or any(
        not segment or not _JWT_SEGMENT.fullmatch(segment) for segment in segments
    ):
        return None
    header = _decode_jwt_object(segments[0])
    payload = _decode_jwt_object(segments[1])
    if header is None or payload is None:
        return None
    algorithm = header.get("alg")
    if not isinstance(algorithm, str) or not algorithm.strip():
        return None
    return _JwtEvidence(
        expires_at=_finite_timestamp(payload.get("exp")),
        principal_fingerprint=_principal_fingerprint(payload),
    )


def _storage_state_jwt_evidence(
    payload: Mapping[str, Any], *, now: float
) -> tuple[bool, frozenset[str]]:
    expired = False
    principals: set[str] = set()
    cookies = payload.get("cookies", [])
    if isinstance(cookies, list):
        for cookie in cookies:
            if not isinstance(cookie, Mapping):
                continue
            evidence = _jwt_evidence(cookie.get("value"))
            if evidence is None:
                continue
            if evidence.principal_fingerprint:
                principals.add(evidence.principal_fingerprint)
            if evidence.expires_at is not None and evidence.expires_at <= now:
                expired = True
            cookie_expires = _finite_timestamp(cookie.get("expires"))
            if cookie_expires is not None and 0 <= cookie_expires <= now:
                expired = True

    origins = payload.get("origins", [])
    if isinstance(origins, list):
        for origin in origins:
            if not isinstance(origin, Mapping):
                continue
            local_storage = origin.get("localStorage", [])
            if not isinstance(local_storage, list):
                continue
            for entry in local_storage:
                if not isinstance(entry, Mapping):
                    continue
                evidence = _jwt_evidence(entry.get("value"))
                if evidence is None:
                    continue
                if evidence.principal_fingerprint:
                    principals.add(evidence.principal_fingerprint)
                if evidence.expires_at is not None and evidence.expires_at <= now:
                    expired = True
    return expired, frozenset(principals)


def validate_role_session_specs(
    specs: Iterable[RoleSessionSpec], required_roles: Iterable[str]
) -> None:
    mapped = _role_spec_map(specs)
    roles = tuple(dict.fromkeys(str(role or "").strip() for role in required_roles))
    if any(not role for role in roles):
        raise WorkflowConfigurationError("流程角色名称不能为空")
    missing = [role for role in roles if role and role not in mapped]
    if missing:
        raise WorkflowConfigurationError("缺少流程角色登录态：" + ", ".join(missing))
    invalid = [
        role
        for role in roles
        if role and not Path(mapped[role].storage_state).is_file()
    ]
    if invalid:
        raise WorkflowConfigurationError(
            "流程角色 storage state 文件不可用：" + ", ".join(invalid)
        )
    malformed: list[str] = []
    expired: list[str] = []
    state_fingerprints: dict[str, str] = {}
    state_principals: dict[str, str] = {}
    now = time.time()
    for role in roles:
        path = Path(mapped[role].storage_state)
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            malformed.append(role)
            continue
        if not isinstance(payload, dict):
            malformed.append(role)
            continue
        if any(
            key in payload and not isinstance(payload[key], list)
            for key in ("cookies", "origins")
        ):
            malformed.append(role)
            continue
        canonical_state = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        state_fingerprints[role] = hashlib.sha256(
            canonical_state.encode("utf-8")
        ).hexdigest()
        has_expired_jwt, principals = _storage_state_jwt_evidence(
            payload, now=now
        )
        if has_expired_jwt:
            expired.append(role)
        if len(principals) == 1:
            state_principals[role] = next(iter(principals))
    if malformed:
        raise WorkflowConfigurationError(
            "流程角色 storage state JSON 无效：" + ", ".join(malformed)
        )
    if expired:
        raise WorkflowConfigurationError(
            "流程角色 storage state 文件中的认证已过期：" + ", ".join(expired)
        )
    duplicated_principals: list[str] = []
    principal_owners: dict[str, str] = {}
    for role in roles:
        principal = state_principals.get(role, "")
        owner = principal_owners.get(principal, "")
        if principal and owner:
            duplicated_principals.extend((owner, role))
        elif principal:
            principal_owners[principal] = role
    if duplicated_principals:
        raise WorkflowConfigurationError(
            "不同流程角色必须使用不同认证主体："
            + ", ".join(dict.fromkeys(duplicated_principals))
        )
    duplicated_states: list[str] = []
    fingerprint_owners: dict[str, str] = {}
    for role in roles:
        fingerprint = state_fingerprints.get(role, "")
        owner = fingerprint_owners.get(fingerprint, "")
        if fingerprint and owner:
            duplicated_states.extend((owner, role))
        elif fingerprint:
            fingerprint_owners[fingerprint] = role
    if duplicated_states:
        raise WorkflowConfigurationError(
            "不同流程角色必须使用独立登录态："
            + ", ".join(dict.fromkeys(duplicated_states))
        )


@dataclass(slots=True)
class _RoleSession:
    role: str
    context: Any
    page: Any


class RoleSessionPool:
    """One browser with one isolated BrowserContext for every workflow role."""

    def __init__(
        self,
        browser: Any,
        specs: Iterable[RoleSessionSpec],
        *,
        context_initializer: Callable[[Any, str], None] | None = None,
        owns_browser: bool = False,
    ) -> None:
        self._browser = browser
        self._specs = _role_spec_map(tuple(specs))
        self._context_initializer = context_initializer
        self._owns_browser = bool(owns_browser)
        self._sessions: dict[str, _RoleSession] = {}
        self._creation_order: list[str] = []
        self._current_role = ""
        self._current_workflow_role = ""
        self._closed = False

    def __repr__(self) -> str:
        return (
            f"RoleSessionPool(roles={list(self._specs)}, "
            f"opened_roles={list(self._creation_order)})"
        )

    @property
    def current_role(self) -> str:
        return self._current_role

    @property
    def current_workflow_role(self) -> str:
        """Logical workflow role for the current step, distinct from a dynamic login."""
        return self._current_workflow_role

    def bind_workflow_role(self, role: str) -> None:
        role = str(role or "").strip()
        if not role:
            raise WorkflowSessionError("流程步骤角色不能为空")
        self._current_workflow_role = role

    @property
    def current_page(self) -> Any | None:
        session = self._sessions.get(self._current_role)
        return session.page if session is not None else None

    def ensure_roles(self, roles: Iterable[str]) -> None:
        if self._closed:
            raise WorkflowSessionError("角色会话池已经关闭")
        required = tuple(
            dict.fromkeys(str(role or "").strip() for role in roles)
        )
        validate_role_session_specs(self._specs.values(), required)
        opened: list[str] = []
        completed = False
        try:
            for role in required:
                if role not in self._sessions:
                    self._open_role(role)
                    opened.append(role)
            completed = True
        finally:
            if not completed:
                for role in reversed(opened):
                    self._discard_role(role)
                self._current_role = ""

    def page_for(self, role: str) -> Any:
        role = str(role or "").strip()
        self._current_role = ""
        self.ensure_roles((role,))
        session = self._sessions.get(role)
        if session is None:
            raise WorkflowSessionError(f"角色 {role} 浏览器会话预热失败")
        is_closed = getattr(session.page, "is_closed", None)
        if callable(is_closed) and is_closed():
            raise WorkflowSessionError(
                f"角色 {role} 页面已关闭，流程禁止从中间步骤静默重建会话"
            )
        self._current_role = role
        return session.page

    def context_for(self, role: str) -> Any:
        self.page_for(role)
        return self._sessions[str(role).strip()].context

    def page_for_login(
        self,
        login_name: str,
        *,
        entry_url: str,
        password: str,
        state_dir: Path,
    ) -> Any:
        """Return an isolated actor session, reusing only that login's cached state."""
        if self._closed:
            raise WorkflowSessionError("角色会话池已经关闭")
        login_name = str(login_name or "").strip()
        if not _WORKFLOW_LOGIN_NAME.fullmatch(login_name):
            raise WorkflowConfigurationError("流程预览未返回安全的唯一登录名")
        if not str(entry_url or "").strip() or not str(password or ""):
            raise WorkflowConfigurationError("动态审批登录缺少入口地址或固定密码")
        session_key = "login-" + hashlib.sha256(
            login_name.encode("utf-8")
        ).hexdigest()[:24]
        existing = self._sessions.get(session_key)
        if existing is not None:
            is_closed = getattr(existing.page, "is_closed", None)
            if callable(is_closed) and is_closed():
                self._discard_role(session_key)
            else:
                self._current_role = session_key
                return existing.page

        cache_dir = Path(state_dir).resolve()
        cache_dir.mkdir(parents=True, exist_ok=True)
        state_path = cache_dir / f"{session_key}.json"
        session = self._open_login_session(
            session_key=session_key,
            login_name=login_name,
            entry_url=str(entry_url),
            password=str(password),
            state_path=state_path,
        )
        self._sessions[session_key] = session
        self._creation_order.append(session_key)
        self._current_role = session_key
        return session.page

    @staticmethod
    def _login_form_visible(page: Any) -> bool:
        try:
            locator = page.locator(_LOGIN_NAME_SELECTOR)
            return bool(locator.count() and locator.first.is_visible())
        except Exception:
            return False

    def _open_login_session(
        self,
        *,
        session_key: str,
        login_name: str,
        entry_url: str,
        password: str,
        state_path: Path,
    ) -> _RoleSession:
        """Use a cached state only when it reaches the app without a login form."""
        def new_session(*, use_state: bool) -> _RoleSession:
            context_args: dict[str, Any] = {"ignore_https_errors": True}
            if use_state:
                context_args["storage_state"] = str(state_path)
            context = self._browser.new_context(**context_args)
            if self._context_initializer is not None:
                self._context_initializer(context, session_key)
            page = context.new_page()
            try:
                page.goto(entry_url, wait_until="domcontentloaded")
            except Exception:
                try:
                    page.close()
                except Exception:
                    pass
                try:
                    context.close()
                except Exception:
                    pass
                raise WorkflowSessionError("动态审批角色无法打开登录入口") from None
            return _RoleSession(role=session_key, context=context, page=page)

        session = new_session(use_state=state_path.is_file())
        if not self._login_form_visible(session.page):
            if state_path.is_file():
                return session
            self._discard_unregistered_session(session)
            raise WorkflowSessionError("动态审批登录入口未出现可验证登录页")

        if state_path.is_file():
            self._discard_unregistered_session(session)
            session = new_session(use_state=False)
            if not self._login_form_visible(session.page):
                self._discard_unregistered_session(session)
                raise WorkflowSessionError("动态审批登录入口未出现可验证登录页")
        try:
            session.page.locator(_LOGIN_NAME_SELECTOR).first.fill(login_name)
            session.page.locator(_LOGIN_PASSWORD_SELECTOR).first.fill(password)
            session.page.locator(_LOGIN_SUBMIT_SELECTOR).first.click()
            deadline = time.monotonic() + 30.0
            while self._login_form_visible(session.page):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkflowSessionError("动态审批角色登录超时")
                session.page.wait_for_timeout(min(250, int(remaining * 1_000)))
            session.context.storage_state(path=str(state_path))
            return session
        except WorkflowSessionError:
            self._discard_unregistered_session(session)
            raise
        except Exception:
            self._discard_unregistered_session(session)
            raise WorkflowSessionError("动态审批角色登录失败") from None

    @staticmethod
    def _discard_unregistered_session(session: _RoleSession) -> None:
        try:
            session.page.close()
        except Exception:
            pass
        try:
            session.context.close()
        except Exception:
            pass

    def _open_role(self, role: str) -> _RoleSession:
        spec = self._specs[role]
        context = None
        page = None
        completed = False
        try:
            context = self._browser.new_context(
                storage_state=str(spec.storage_state),
                ignore_https_errors=True,
            )
            if self._context_initializer is not None:
                self._context_initializer(context, role)
            page = context.new_page()
            session = _RoleSession(role=role, context=context, page=page)
            self._sessions[role] = session
            self._creation_order.append(role)
            completed = True
            return session
        except Exception:
            raise WorkflowSessionError(
                f"角色 {role} 浏览器会话初始化失败"
            ) from None
        finally:
            if not completed:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass

    def _discard_role(self, role: str) -> None:
        session = self._sessions.pop(role, None)
        if session is None:
            return
        self._creation_order = [item for item in self._creation_order if item != role]
        try:
            session.page.close()
        except Exception:
            pass
        try:
            session.context.close()
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for role in reversed(self._creation_order):
            session = self._sessions.get(role)
            if session is None:
                continue
            try:
                session.page.close()
            except Exception:
                pass
            try:
                session.context.close()
            except Exception:
                pass
        if self._owns_browser:
            try:
                self._browser.close()
            except Exception:
                pass
        self._sessions.clear()
        self._creation_order.clear()
        self._current_role = ""
        self._current_workflow_role = ""

    def __enter__(self) -> RoleSessionPool:
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class WorkflowStepEvidence:
    record_visible: bool
    action_available: bool
    readback_business_id: str
    state_source: str
    poll_attempts: int
    readback_bounded: bool = True
    next_action_available: bool | None = None
    mutation_count: int = 0
    request_count: int = 0
    response_count: int = 0
    http_status: int = 0
    business_success: bool = False
    business_code: str | int = ""
    response_business_id: str = ""
    mutation_business_id: str = ""
    mutation_id_source: str = ""
    mutation_correlation_key: str = ""

    def __post_init__(self) -> None:
        business_code = str(
            self.business_code if self.business_code is not None else ""
        ).strip()
        response_business_id = _normalized_evidence_identity(
            self.response_business_id,
            label="response_business_id",
        )
        mutation_business_id = _normalized_evidence_identity(
            self.mutation_business_id,
            label="mutation_business_id",
        )
        mutation_id_source = str(self.mutation_id_source or "").strip().lower()
        mutation_correlation_key = str(self.mutation_correlation_key or "").strip()
        readback_business_id = _normalized_evidence_identity(
            self.readback_business_id,
            label="readback_business_id",
        )
        state_source = str(self.state_source or "").strip()
        if business_code and not _SAFE_OBSERVATION_CODE.fullmatch(business_code):
            raise WorkflowResultError("step evidence 包含不安全的 business_code")
        if not _SAFE_OBSERVATION_CODE.fullmatch(state_source):
            raise WorkflowResultError("step evidence 缺少安全的 state_source")
        if mutation_id_source and mutation_id_source not in {"request", "response"}:
            raise WorkflowResultError(
                "step evidence mutation_id_source 必须是 request 或 response"
            )
        if mutation_correlation_key:
            if not _STABLE_ID.fullmatch(mutation_correlation_key):
                raise WorkflowResultError(
                    "step evidence mutation_correlation_key 必须是稳定 ID"
                )
            normalized_key = _normalized_snapshot_key(mutation_correlation_key)
            if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
                raise WorkflowResultError(
                    "step evidence mutation_correlation_key 不能是敏感 key"
                )
        object.__setattr__(self, "business_code", business_code)
        object.__setattr__(self, "response_business_id", response_business_id)
        object.__setattr__(self, "mutation_business_id", mutation_business_id)
        object.__setattr__(self, "mutation_id_source", mutation_id_source)
        object.__setattr__(
            self, "mutation_correlation_key", mutation_correlation_key
        )
        object.__setattr__(self, "readback_business_id", readback_business_id)
        object.__setattr__(self, "state_source", state_source)

    def snapshot(self) -> dict[str, Any]:
        return {
            "action_available": self.action_available,
            "record_visible": self.record_visible,
            "next_action_available": self.next_action_available,
            "mutation_count": self.mutation_count,
            "request_count": self.request_count,
            "response_count": self.response_count,
            "http_status": self.http_status,
            "business_success": self.business_success,
            "business_code": self.business_code,
            "response_business_id": self.response_business_id,
            "mutation_business_id": self.mutation_business_id,
            "mutation_id_source": self.mutation_id_source,
            "mutation_correlation_key": self.mutation_correlation_key,
            "readback_business_id": self.readback_business_id,
            "state_source": self.state_source,
            "poll_attempts": self.poll_attempts,
            "readback_bounded": self.readback_bounded,
        }
@dataclass(frozen=True, slots=True)
class WorkflowCleanupDisposition:
    disposition: str
    retention_reason: str = ""

    def __post_init__(self) -> None:
        disposition = str(self.disposition or "").strip().lower()
        retention_reason = str(self.retention_reason or "").strip()
        if disposition not in WORKFLOW_CLEANUP_DISPOSITIONS:
            raise WorkflowResultError(
                f"不支持的 cleanup disposition：{disposition or '<empty>'}"
            )
        if disposition == "retained":
            if not retention_reason:
                raise WorkflowResultError("retained cleanup disposition 必须声明 retention_reason")
            if (
                not _SAFE_OBSERVATION_TEXT.fullmatch(retention_reason)
                or retention_reason.startswith(("http://", "https://", "{", "["))
                or _BEARER_VALUE.search(retention_reason)
                or _CREDENTIAL_VALUE.search(retention_reason)
            ):
                raise WorkflowResultError("retention_reason 必须是安全的简短文本")
        elif retention_reason:
            raise WorkflowResultError(
                f"cleanup disposition {disposition} 不能声明 retention_reason"
            )
        object.__setattr__(self, "disposition", disposition)
        object.__setattr__(self, "retention_reason", retention_reason)


@dataclass(frozen=True, slots=True)
class WorkflowStepResult:
    business_id: str = ""
    page_scope: str = ""
    record_markers: tuple[str, ...] = ()
    actual_status: str = ""
    observations: Mapping[str, Any] = field(default_factory=dict)
    created_by_workflow: bool = False
    cleanup_allowed: bool = False
    step_evidence: WorkflowStepEvidence | None = None
    cleanup_disposition: WorkflowCleanupDisposition | None = None
    correlation_ids: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.cleanup_allowed and not self.created_by_workflow:
            raise WorkflowResultError(
                "只有本流程明确创建的记录才能登记为允许清理"
            )
        object.__setattr__(self, "business_id", str(self.business_id or "").strip())
        object.__setattr__(self, "page_scope", page_scope_from_url(self.page_scope))
        object.__setattr__(
            self, "record_markers", _automation_markers(self.record_markers)
        )
        object.__setattr__(self, "actual_status", str(self.actual_status or "").strip())
        object.__setattr__(self, "observations", dict(self.observations or {}))
        object.__setattr__(
            self,
            "correlation_ids",
            _normalized_correlation_ids(self.correlation_ids),
        )
        if self.step_evidence is not None and not isinstance(
            self.step_evidence, WorkflowStepEvidence
        ):
            raise WorkflowResultError(
                "step_evidence 必须是 WorkflowStepEvidence"
            )
        if self.cleanup_disposition is not None and not isinstance(
            self.cleanup_disposition, WorkflowCleanupDisposition
        ):
            raise WorkflowResultError(
                "cleanup_disposition 必须是 WorkflowCleanupDisposition"
            )

    @classmethod
    def from_module_result(
        cls,
        result: ModuleSmokeResult,
        *,
        page_scope: str = "",
        actual_status: str = "",
        observations: Mapping[str, Any] | None = None,
        created_by_workflow: bool = False,
        cleanup_allowed: bool = False,
        step_evidence: WorkflowStepEvidence | None = None,
        cleanup_disposition: WorkflowCleanupDisposition | None = None,
        correlation_ids: Mapping[str, str] | None = None,
    ) -> WorkflowStepResult:
        safe_observations = {"module_result_mode": str(result.mode or "")}
        safe_observations.update(dict(observations or {}))
        return cls(
            business_id=result.business_id,
            page_scope=page_scope,
            record_markers=result.record_markers,
            actual_status=actual_status,
            observations=safe_observations,
            created_by_workflow=created_by_workflow,
            cleanup_allowed=cleanup_allowed,
            step_evidence=step_evidence,
            cleanup_disposition=cleanup_disposition,
            correlation_ids=correlation_ids or {},
        )


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    step_id: str
    title: str
    role: str
    handler: Callable[[WorkflowStepExecution], WorkflowStepResult | ModuleSmokeResult] = field(
        repr=False, compare=False
    )
    depends_on: tuple[str, ...] = ()
    modes: frozenset[str] = field(
        default_factory=lambda: DEFAULT_WORKFLOW_DATA_MODES
    )
    requires_business_id: bool = False
    produces_business_id: bool = False
    requires_status: str = ""
    expected_status: str = ""
    created_record: bool = False
    cleanup_allowed: bool = False
    step_type: str = "read_only"
    requires_next_action: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "step_id", _stable_id(self.step_id, label="step_id")
        )
        title = str(self.title or "").strip()
        role = str(self.role or "").strip()
        if not title or not role:
            raise WorkflowConfigurationError("流程步骤必须同时声明 title 和 role")
        if not callable(self.handler):
            raise WorkflowConfigurationError(f"流程步骤 {self.step_id} 缺少可调用 handler")
        dependencies = tuple(
            dict.fromkeys(_stable_id(item, label="depends_on") for item in self.depends_on)
        )
        modes = frozenset(_normalized_mode(item) for item in self.modes)
        step_type = str(self.step_type or "").strip().lower()
        requires_status = str(self.requires_status or "").strip()
        expected_status = str(self.expected_status or "").strip()
        if not modes:
            raise WorkflowConfigurationError(f"流程步骤 {self.step_id} 没有可执行模式")
        if self.step_id in dependencies:
            raise WorkflowConfigurationError(f"流程步骤 {self.step_id} 不能依赖自身")
        if self.created_record and not self.produces_business_id:
            raise WorkflowConfigurationError(
                f"创建步骤 {self.step_id} 必须声明 produces_business_id=True"
            )
        if self.cleanup_allowed and not self.created_record:
            raise WorkflowConfigurationError(
                f"步骤 {self.step_id} 不是创建步骤，不能允许清理"
            )
        if step_type not in WORKFLOW_STEP_TYPES:
            raise WorkflowConfigurationError(
                f"流程步骤 {self.step_id} 的 step_type 必须是 mutation、dynamic_mutation 或 read_only"
            )
        if self.created_record and step_type != "mutation":
            raise WorkflowConfigurationError(
                f"创建步骤 {self.step_id} 必须声明 step_type='mutation'"
            )
        if step_type in {"mutation", "dynamic_mutation"}:
            if not (self.produces_business_id or self.requires_business_id):
                raise WorkflowConfigurationError(
                    f"迁移步骤 {self.step_id} 必须声明业务 ID 输入或输出"
                )
            if not expected_status:
                raise WorkflowConfigurationError(
                    f"迁移步骤 {self.step_id} 必须声明 expected_status"
                )
        if self.requires_next_action and step_type not in {"mutation", "dynamic_mutation"}:
            raise WorkflowConfigurationError(
                f"只有迁移步骤 {self.step_id} 可以要求下一操作可用"
            )
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "depends_on", dependencies)
        object.__setattr__(self, "modes", modes)
        object.__setattr__(self, "step_type", step_type)
        object.__setattr__(self, "requires_status", requires_status)
        object.__setattr__(self, "expected_status", expected_status)


@dataclass(frozen=True, slots=True)
class WorkflowDefinition:
    workflow_id: str
    title: str
    steps: tuple[WorkflowStep, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "workflow_id", _stable_id(self.workflow_id, label="workflow_id")
        )
        title = str(self.title or "").strip()
        steps = tuple(self.steps)
        if not title or not steps:
            raise WorkflowConfigurationError("流程必须声明 title 和至少一个步骤")
        seen: set[str] = set()
        for step in steps:
            if step.step_id in seen:
                raise WorkflowConfigurationError(f"流程步骤 ID 重复：{step.step_id}")
            unavailable = [dependency for dependency in step.depends_on if dependency not in seen]
            if unavailable:
                raise WorkflowConfigurationError(
                    f"流程步骤 {step.step_id} 只能依赖此前步骤：{', '.join(unavailable)}"
                )
            seen.add(step.step_id)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "steps", steps)

    def steps_for(self, data_mode: str) -> tuple[WorkflowStep, ...]:
        mode = _normalized_mode(data_mode)
        active = tuple(step for step in self.steps if mode in step.modes)
        if not active:
            raise WorkflowConfigurationError(
                f"流程 {self.workflow_id} 没有声明 {mode} 模式步骤"
            )
        mutations = [
            step.step_id
            for step in active
            if step.step_type in {"mutation", "dynamic_mutation"}
        ]
        if mode == "stable" and mutations:
            raise WorkflowConfigurationError(
                f"流程 {self.workflow_id} 的 stable 模式只能运行只读步骤："
                + ", ".join(mutations)
            )
        active_ids = {step.step_id for step in active}
        for step in active:
            missing = [dependency for dependency in step.depends_on if dependency not in active_ids]
            if missing:
                raise WorkflowConfigurationError(
                    f"流程 {self.workflow_id} 的 {mode} 模式缺少步骤依赖："
                    f"{step.step_id} -> {', '.join(missing)}"
                )
        return active

    def required_roles(self, data_mode: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys(step.role for step in self.steps_for(data_mode)))


@dataclass(frozen=True, slots=True)
class WorkflowCleanupRecord:
    business_id: str
    page_scope: str
    record_markers: tuple[str, ...]
    created_step_id: str
    run_id: str
    disposition: str = "pending"
    retention_reason: str = ""
    disposition_step_id: str = ""


@dataclass(frozen=True, slots=True)
class WorkflowStepRecord:
    step_id: str
    title: str
    role: str
    status: str
    started_at: str
    completed_at: str
    business_id: str = ""
    page_scope: str = ""
    expected_status: str = ""
    actual_status: str = ""
    record_markers: tuple[str, ...] = ()
    observations: Mapping[str, Any] = field(default_factory=dict)
    step_type: str = "read_only"
    step_evidence: WorkflowStepEvidence | None = None
    cleanup_disposition: str = ""
    retention_reason: str = ""
    correlation_ids: Mapping[str, str] = field(default_factory=dict)
    error_type: str = ""


@dataclass(slots=True)
class WorkflowContext:
    workflow_id: str
    run_id: str
    data_mode: str
    business_id: str = ""
    page_scope: str = ""
    record_markers: tuple[str, ...] = ()
    correlation_ids: dict[str, str] = field(default_factory=dict)
    expected_status: str = ""
    actual_status: str = ""
    status: str = "pending"
    current_step_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    failure_type: str = ""
    snapshot_error_type: str = ""
    step_records: list[WorkflowStepRecord] = field(default_factory=list)
    cleanup_records: list[WorkflowCleanupRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.workflow_id = _stable_id(self.workflow_id, label="workflow_id")
        self.run_id = str(self.run_id or "").strip()
        if not self.run_id:
            raise WorkflowConfigurationError("run_id 不能为空")
        self.data_mode = _normalized_mode(self.data_mode)
        self.business_id = str(self.business_id or "").strip()
        self.page_scope = page_scope_from_url(self.page_scope)
        self.record_markers = _automation_markers(self.record_markers)
        self.correlation_ids = _normalized_correlation_ids(self.correlation_ids)

    @property
    def completed_step_ids(self) -> frozenset[str]:
        return frozenset(
            record.step_id for record in self.step_records if record.status == "passed"
        )

    def require_business_id(self) -> str:
        if not self.business_id:
            raise WorkflowPreconditionError("当前流程尚未取得 business_id")
        return self.business_id

    def start(self) -> None:
        if self.status != "pending":
            raise WorkflowPreconditionError(f"流程上下文不能从 {self.status} 状态重新启动")
        self.status = "running"
        self.started_at = _utc_now()

    def start_step(self, step: WorkflowStep) -> str:
        self.current_step_id = step.step_id
        return _utc_now()

    def complete_step(
        self, step: WorkflowStep, result: WorkflowStepResult, *, started_at: str
    ) -> None:
        self._bind_result_identity(step, result)
        self.expected_status = step.expected_status
        if result.actual_status:
            self.actual_status = result.actual_status
        self.step_records.append(
            WorkflowStepRecord(
                step_id=step.step_id,
                title=step.title,
                role=step.role,
                status="passed",
                started_at=started_at,
                completed_at=_utc_now(),
                business_id=self.business_id,
                page_scope=self.page_scope,
                expected_status=step.expected_status,
                actual_status=result.actual_status,
                record_markers=result.record_markers,
                observations=result.observations,
                step_type=step.step_type,
                step_evidence=result.step_evidence,
                cleanup_disposition=(
                    result.cleanup_disposition.disposition
                    if result.cleanup_disposition is not None
                    else ""
                ),
                retention_reason=(
                    result.cleanup_disposition.retention_reason
                    if result.cleanup_disposition is not None
                    else ""
                ),
                correlation_ids=result.correlation_ids,
            )
        )
        self.current_step_id = ""

    def fail_step(
        self,
        step: WorkflowStep,
        error: Exception,
        *,
        started_at: str,
        result: WorkflowStepResult | None = None,
    ) -> None:
        result_correlations = result.correlation_ids if result is not None else {}
        safe_result_correlations = {
            key: value
            for key, value in result_correlations.items()
            if key not in self.correlation_ids or self.correlation_ids[key] == value
        }
        if result is not None:
            self._bind_result_identity(
                step, result, allow_existing_cleanup_update=False
            )
            self.expected_status = step.expected_status
            if result.actual_status:
                self.actual_status = result.actual_status
        self.status = "failed"
        self.failure_type = type(error).__name__
        self.completed_at = _utc_now()
        self.step_records.append(
            WorkflowStepRecord(
                step_id=step.step_id,
                title=step.title,
                role=step.role,
                status="failed",
                started_at=started_at,
                completed_at=self.completed_at,
                business_id=(result.business_id if result is not None else "")
                or self.business_id,
                page_scope=(result.page_scope if result is not None else "")
                or self.page_scope,
                expected_status=step.expected_status,
                actual_status=(result.actual_status if result is not None else "")
                or self.actual_status,
                record_markers=(result.record_markers if result is not None else ()),
                observations=(result.observations if result is not None else {}),
                step_type=step.step_type,
                step_evidence=(
                    result.step_evidence if result is not None else None
                ),
                cleanup_disposition=(
                    result.cleanup_disposition.disposition
                    if result is not None and result.cleanup_disposition is not None
                    else ""
                ),
                retention_reason=(
                    result.cleanup_disposition.retention_reason
                    if result is not None and result.cleanup_disposition is not None
                    else ""
                ),
                correlation_ids=safe_result_correlations,
                error_type=type(error).__name__,
            )
        )
        self.current_step_id = ""

    def _bind_result_identity(
        self,
        step: WorkflowStep,
        result: WorkflowStepResult,
        *,
        allow_existing_cleanup_update: bool = True,
    ) -> None:
        if (
            result.business_id
            and self.business_id
            and result.business_id != self.business_id
        ):
            return
        if result.business_id:
            self.business_id = result.business_id
        if result.page_scope:
            self.page_scope = result.page_scope
        if result.record_markers:
            self.record_markers = tuple(
                dict.fromkeys((*self.record_markers, *result.record_markers))
            )
        conflicts = {
            key
            for key, value in result.correlation_ids.items()
            if key in self.correlation_ids and self.correlation_ids[key] != value
        }
        if not conflicts:
            self.correlation_ids.update(result.correlation_ids)
        cleanup_disposition = result.cleanup_disposition
        if cleanup_disposition is None or not result.business_id:
            return
        if result.created_by_workflow:
            expected_disposition = "pending" if step.cleanup_allowed else "retained"
            if cleanup_disposition.disposition != expected_disposition:
                return
        matching_indexes = [
            index
            for index, record in enumerate(self.cleanup_records)
            if record.business_id == result.business_id
        ]
        if matching_indexes:
            if not result.created_by_workflow and not allow_existing_cleanup_update:
                return
            for index in matching_indexes:
                record = self.cleanup_records[index]
                self.cleanup_records[index] = replace(
                    record,
                    disposition=cleanup_disposition.disposition,
                    retention_reason=cleanup_disposition.retention_reason,
                    disposition_step_id=step.step_id,
                )
            return
        if not result.created_by_workflow or not result.page_scope:
            return
        self.cleanup_records.append(
            WorkflowCleanupRecord(
                business_id=result.business_id,
                page_scope=result.page_scope,
                record_markers=result.record_markers,
                created_step_id=step.step_id,
                run_id=self.run_id,
                disposition=cleanup_disposition.disposition,
                retention_reason=cleanup_disposition.retention_reason,
                disposition_step_id=step.step_id,
            )
        )

    def checkpoint_result(
        self, step: WorkflowStep, result: WorkflowStepResult
    ) -> None:
        """Persist identity immediately after a confirmed workflow-owned create."""
        self._bind_result_identity(step, result)
        if result.actual_status:
            self.actual_status = result.actual_status

    def checkpoint_cleanup_disposition(
        self,
        step: WorkflowStep,
        disposition: WorkflowCleanupDisposition,
    ) -> None:
        """Persist a terminal cleanup decision after an irreversible mutation."""
        business_id = self.require_business_id()
        matching_indexes = [
            index
            for index, record in enumerate(self.cleanup_records)
            if record.business_id == business_id
        ]
        if not matching_indexes:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 的 cleanup checkpoint 未匹配本流程创建记录"
            )
        for index in matching_indexes:
            record = self.cleanup_records[index]
            self.cleanup_records[index] = replace(
                record,
                disposition=disposition.disposition,
                retention_reason=disposition.retention_reason,
                disposition_step_id=step.step_id,
            )

    def mark_snapshot_failure(self, error: Exception) -> None:
        self.status = "failed"
        self.snapshot_error_type = type(error).__name__
        self.completed_at = _utc_now()
        self.current_step_id = ""

    def finish(self) -> None:
        self.status = "passed"
        self.completed_at = _utc_now()
        self.current_step_id = ""

    def snapshot(self) -> dict[str, Any]:
        payload = {
            "schema_version": 2,
            "workflow_id": self.workflow_id,
            "run_id": self.run_id,
            "data_mode": self.data_mode,
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "failure_type": self.failure_type,
            "snapshot_error_type": self.snapshot_error_type,
            "current_step_id": self.current_step_id,
            "business_id": self.business_id,
            "page_scope": self.page_scope,
            "record_markers": list(self.record_markers),
            "correlation_ids": dict(self.correlation_ids),
            "expected_status": self.expected_status,
            "actual_status": self.actual_status,
            "steps": [
                {
                    "step_id": record.step_id,
                    "title": record.title,
                    "role": record.role,
                    "status": record.status,
                    "started_at": record.started_at,
                    "completed_at": record.completed_at,
                    "business_id": record.business_id,
                    "page_scope": record.page_scope,
                    "expected_status": record.expected_status,
                    "actual_status": record.actual_status,
                    "record_markers": list(record.record_markers),
                    "observations": _snapshot_observations(record.observations),
                    "step_type": record.step_type,
                    "step_evidence": (
                        record.step_evidence.snapshot()
                        if record.step_evidence is not None
                        else None
                    ),
                    "cleanup_disposition": record.cleanup_disposition,
                    "retention_reason": record.retention_reason,
                    "correlation_ids": dict(record.correlation_ids),
                    "error_type": record.error_type,
                }
                for record in self.step_records
            ],
            "cleanup_records": [
                {
                    "business_id": record.business_id,
                    "page_scope": record.page_scope,
                    "record_markers": list(record.record_markers),
                    "created_step_id": record.created_step_id,
                    "run_id": record.run_id,
                    "disposition": record.disposition,
                    "retention_reason": record.retention_reason,
                    "disposition_step_id": record.disposition_step_id,
                }
                for record in self.cleanup_records
            ],
        }
        return _snapshot_value(payload)

    def write_snapshot(self, path: Path) -> None:
        atomic_write_json(Path(path), self.snapshot())


@dataclass(frozen=True, slots=True)
class _WorkflowReadbackEvent:
    business_id: str
    state_source: str
    attempts: int


@dataclass(slots=True)
class WorkflowStepExecution:
    context: WorkflowContext
    step: WorkflowStep
    page: Any
    sessions: Any | None = field(default=None, repr=False, compare=False)
    checkpoint_writer: Callable[
        [WorkflowStepResult | ModuleSmokeResult], WorkflowStepResult
    ] | None = field(default=None, repr=False, compare=False)
    cleanup_checkpoint_writer: Callable[
        [WorkflowCleanupDisposition], WorkflowCleanupDisposition
    ] | None = field(default=None, repr=False, compare=False)
    _readback_events: list[_WorkflowReadbackEvent] = field(
        default_factory=list, repr=False, compare=False
    )

    @property
    def business_id(self) -> str:
        return self.context.business_id

    def require_business_id(self) -> str:
        return self.context.require_business_id()

    @property
    def correlation_ids(self) -> Mapping[str, str]:
        return dict(self.context.correlation_ids)

    def require_correlation_id(self, key: str) -> str:
        normalized_key = str(key or "").strip()
        value = self.context.correlation_ids.get(normalized_key, "")
        if not value:
            raise WorkflowPreconditionError(
                f"当前流程尚未取得 correlation_id：{normalized_key or '<empty>'}"
            )
        return value

    def checkpoint(
        self, result: WorkflowStepResult | ModuleSmokeResult
    ) -> WorkflowStepResult:
        """Record a created ID before later readback assertions can fail."""
        if self.checkpoint_writer is None:
            raise WorkflowPreconditionError("当前步骤没有创建记录 checkpoint 能力")
        return self.checkpoint_writer(result)

    def checkpoint_cleanup(
        self, disposition: WorkflowCleanupDisposition
    ) -> WorkflowCleanupDisposition:
        """Persist retained/deleted before later readback can fail."""
        if self.cleanup_checkpoint_writer is None:
            raise WorkflowPreconditionError("当前步骤没有 cleanup checkpoint 能力")
        return self.cleanup_checkpoint_writer(disposition)

    def use_page(self, page: Any) -> Any:
        """Bind a preview-selected actor page to this step before returning evidence."""
        if page is None:
            raise WorkflowSessionError("动态流程角色没有可用页面")
        self.page = page
        return page

    def page_for_login(
        self,
        login_name: str,
        *,
        entry_url: str,
        password: str,
        state_dir: Path,
    ) -> Any:
        """Reuse or log in one isolated context selected by a process preview."""
        resolver = getattr(self.sessions, "page_for_login", None)
        if not callable(resolver):
            raise WorkflowSessionError("当前流程会话池不支持按登录名切换角色")
        return self.use_page(
            resolver(
                login_name,
                entry_url=entry_url,
                password=password,
                state_dir=state_dir,
            )
        )

    def wait_for_status(
        self,
        probe: Callable[[], Any],
        *,
        expected: Any,
        business_id: str,
        state_source: str,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 0.25,
    ) -> Any:
        """Poll until the deadline; the synchronous probe must enforce its own timeout."""
        if timeout_seconds <= 0 or interval_seconds <= 0:
            raise WorkflowConfigurationError("状态轮询 timeout 和 interval 必须大于 0")
        expected_text = self._status_scalar(expected, source="expected")
        declared_expected = str(self.step.expected_status or "").strip()
        if declared_expected and expected_text != declared_expected:
            raise WorkflowConfigurationError(
                f"步骤 {self.step.step_id} 的轮询目标与 expected_status 不一致"
            )
        readback_business_id = str(business_id or "").strip()
        if not readback_business_id:
            raise WorkflowPreconditionError("状态回读必须声明 business_id")
        if self.context.business_id and readback_business_id != self.context.business_id:
            raise WorkflowResultError(
                f"步骤 {self.step.step_id} 的状态回读使用了不同 business_id"
            )
        normalized_source = str(state_source or "").strip()
        if not _SAFE_OBSERVATION_CODE.fullmatch(normalized_source):
            raise WorkflowConfigurationError("状态回读必须声明安全的 state_source")
        deadline = time.monotonic() + timeout_seconds
        attempts = 0
        while True:
            last_value = probe()
            attempts += 1
            actual_text = self._status_scalar(last_value, source="probe")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WorkflowStateTimeout(
                    f"步骤 {self.step.step_id} 状态轮询超时："
                    f"attempts={attempts}；probe 自身必须配置独立超时"
                )
            if actual_text == expected_text:
                self._readback_events.append(
                    _WorkflowReadbackEvent(
                        business_id=readback_business_id,
                        state_source=normalized_source,
                        attempts=attempts,
                    )
                )
                return last_value
            time.sleep(min(interval_seconds, remaining))

    @staticmethod
    def _status_scalar(value: Any, *, source: str) -> str:
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise WorkflowResultError(
                f"状态 {source} 必须返回简短标量，不能返回 {type(value).__name__}"
            )
        text = str(value if value is not None else "").strip()
        if len(text) > 128 or text.startswith(("{", "[")):
            raise WorkflowResultError(f"状态 {source} 必须返回不超过 128 字符的简短标量")
        return text

    @property
    def last_readback_event(self) -> _WorkflowReadbackEvent | None:
        return self._readback_events[-1] if self._readback_events else None


@dataclass(frozen=True, slots=True)
class WorkflowBuildContext:
    project_root: Path
    workflow_id: str
    run_id: str
    data_mode: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root).resolve())
        object.__setattr__(
            self, "workflow_id", _stable_id(self.workflow_id, label="workflow_id")
        )
        run_id = str(self.run_id or "").strip()
        if not run_id:
            raise WorkflowConfigurationError("run_id 不能为空")
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "data_mode", _normalized_mode(self.data_mode))


def load_workflow_definition(
    factory_spec: str, build_context: WorkflowBuildContext
) -> WorkflowDefinition:
    """Load one Python workflow factory without introducing a step-expression DSL."""
    module_name, separator, factory_name = str(factory_spec or "").strip().partition(":")
    if not separator or not module_name or not factory_name:
        raise WorkflowConfigurationError(
            "EI_WORKFLOW_FACTORY 必须使用 python.module:factory 格式"
        )
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
    except Exception as exc:
        raise WorkflowConfigurationError(
            f"无法加载已选择的流程工厂：{type(exc).__name__}"
        ) from None
    if not callable(factory):
        raise WorkflowConfigurationError("已选择的流程工厂不可调用")
    try:
        definition = factory(build_context)
    except WorkflowConfigurationError:
        raise
    except Exception as exc:
        raise WorkflowConfigurationError(
            f"流程工厂构建失败：{type(exc).__name__}"
        ) from None
    if not isinstance(definition, WorkflowDefinition):
        raise WorkflowConfigurationError("流程工厂必须返回 WorkflowDefinition")
    if definition.workflow_id != build_context.workflow_id:
        raise WorkflowConfigurationError(
            "流程工厂返回的 workflow_id 与 EI_WORKFLOW_ID 不一致"
        )
    return definition


class WorkflowReporter(Protocol):
    def start(
        self, definition: WorkflowDefinition, context: WorkflowContext
    ) -> None: ...

    def step(
        self, step: WorkflowStep, *, index: int
    ) -> ContextManager[Any]: ...

    def evidence(self, step: WorkflowStep, result: WorkflowStepResult) -> None: ...


class AllureWorkflowReporter:
    def start(
        self, definition: WorkflowDefinition, context: WorkflowContext
    ) -> None:
        set_allure_module_metadata(
            module_id=f"workflow::{definition.workflow_id}",
            module_name=f"业务流程/{definition.title}",
            test_title=f"{definition.title} [{definition.workflow_id}]",
        )
        import allure

        allure.dynamic.parameter("workflow_id", definition.workflow_id)
        allure.dynamic.parameter("data_mode", context.data_mode, excluded=True)
        set_allure_hidden_parameter("workflow_run_id", context.run_id)

    def step(self, step: WorkflowStep, *, index: int) -> ContextManager[Any]:
        import allure

        return allure.step(
            f"{index:02d} {step.title} [{step.step_id} / {step.role}]"
        )

    def evidence(self, step: WorkflowStep, result: WorkflowStepResult) -> None:
        import allure

        parts = [f"type={step.step_type}"]
        evidence = result.step_evidence
        if evidence is not None:
            parts.extend(
                (
                    f"record_visible={str(evidence.record_visible).lower()}",
                    f"action_available={str(evidence.action_available).lower()}",
                    f"poll_attempts={evidence.poll_attempts}",
                    f"state_source={evidence.state_source}",
                )
            )
            if step.step_type in {"mutation", "dynamic_mutation"}:
                parts.extend(
                    (
                        f"mutation_count={evidence.mutation_count}",
                        f"http_status={evidence.http_status}",
                        f"business_code={evidence.business_code}",
                    )
                )
            if step.requires_next_action:
                parts.append(
                    "next_action_available="
                    f"{str(evidence.next_action_available).lower()}"
                )
        if result.cleanup_disposition is not None:
            parts.append(f"cleanup={result.cleanup_disposition.disposition}")
            if result.cleanup_disposition.retention_reason:
                parts.append(
                    "retention_reason="
                    + result.cleanup_disposition.retention_reason
                )
        if result.correlation_ids:
            parts.append("correlations=" + ",".join(sorted(result.correlation_ids)))
        with allure.step("证据摘要 " + "; ".join(parts)):
            pass


class WorkflowRunner:
    def __init__(
        self,
        sessions: Any,
        *,
        snapshot_path: Path | None = None,
        reporter: WorkflowReporter | None = None,
    ) -> None:
        self.sessions = sessions
        self.snapshot_path = Path(snapshot_path) if snapshot_path is not None else None
        self.reporter = reporter or AllureWorkflowReporter()

    def run(
        self, definition: WorkflowDefinition, context: WorkflowContext
    ) -> WorkflowContext:
        if definition.workflow_id != context.workflow_id:
            raise WorkflowConfigurationError("流程定义与 WorkflowContext 的 ID 不一致")
        steps = definition.steps_for(context.data_mode)
        roles = definition.required_roles(context.data_mode)
        self.sessions.ensure_roles(roles)
        self.reporter.start(definition, context)
        for role in roles:
            self.sessions.page_for(role)
        context.start()
        try:
            self._persist(context)
        except Exception as exc:
            context.mark_snapshot_failure(exc)
            raise
        for index, step in enumerate(steps, 1):
            started_at = context.start_step(step)
            try:
                self._persist(context)
            except Exception as exc:
                context.mark_snapshot_failure(exc)
                raise
            result: WorkflowStepResult | None = None
            try:
                with self.reporter.step(step, index=index):
                    page = self.sessions.page_for(step.role)
                    bind_role = getattr(self.sessions, "bind_workflow_role", None)
                    if callable(bind_role):
                        bind_role(step.role)
                    self._assert_step_preconditions(step, context)

                    def checkpoint_writer(
                        checkpoint_result: WorkflowStepResult | ModuleSmokeResult,
                    ) -> WorkflowStepResult:
                        if not step.created_record:
                            raise WorkflowResultError(
                                f"步骤 {step.step_id} 不是创建步骤，不能登记 checkpoint"
                            )
                        normalized = self._normalize_result(
                            checkpoint_result, step=step, page=page
                        )
                        if not normalized.business_id or not normalized.page_scope:
                            raise WorkflowResultError(
                                f"步骤 {step.step_id} checkpoint 缺少 business_id/page_scope"
                            )
                        self._assert_same_business_id(step, context, normalized)
                        self._assert_correlation_ids(step, context, normalized)
                        self._assert_cleanup_disposition(
                            step, context, normalized, checkpoint=True
                        )
                        context.checkpoint_result(step, normalized)
                        self._persist(context)
                        return normalized

                    def cleanup_checkpoint_writer(
                        disposition: WorkflowCleanupDisposition,
                    ) -> WorkflowCleanupDisposition:
                        if (
                            step.step_type not in {"mutation", "dynamic_mutation"}
                            or step.created_record
                        ):
                            raise WorkflowResultError(
                                f"步骤 {step.step_id} 不能更新 cleanup checkpoint"
                            )
                        if not isinstance(disposition, WorkflowCleanupDisposition):
                            raise WorkflowResultError(
                                "cleanup checkpoint 必须是 WorkflowCleanupDisposition"
                            )
                        if disposition.disposition == "pending":
                            raise WorkflowResultError(
                                "cleanup checkpoint 不能恢复为 pending"
                            )
                        context.checkpoint_cleanup_disposition(step, disposition)
                        self._persist(context)
                        return disposition

                    execution = WorkflowStepExecution(
                        context=context,
                        step=step,
                        page=page,
                        sessions=self.sessions,
                        checkpoint_writer=checkpoint_writer,
                        cleanup_checkpoint_writer=cleanup_checkpoint_writer,
                    )
                    raw_result = step.handler(execution)
                    result = self._normalize_result(
                        raw_result, step=step, page=execution.page
                    )
                    self._assert_step_result(
                        step, context, result, execution=execution
                    )
                    report_evidence = getattr(self.reporter, "evidence", None)
                    if callable(report_evidence):
                        report_evidence(step, result)
                context.complete_step(step, result, started_at=started_at)
            except Exception as exc:
                context.fail_step(
                    step, exc, started_at=started_at, result=result
                )
                try:
                    self._persist(context)
                except Exception as snapshot_error:
                    context.mark_snapshot_failure(snapshot_error)
                    try:
                        exc.add_note("workflow failure snapshot could not be updated")
                    except (AttributeError, TypeError):
                        pass
                raise
            try:
                self._persist(context)
            except Exception as exc:
                context.mark_snapshot_failure(exc)
                raise
        context.finish()
        try:
            self._persist(context)
        except Exception as exc:
            context.mark_snapshot_failure(exc)
            raise
        return context

    def _persist(self, context: WorkflowContext) -> None:
        if self.snapshot_path is not None:
            context.write_snapshot(self.snapshot_path)

    @staticmethod
    def _assert_step_preconditions(
        step: WorkflowStep, context: WorkflowContext
    ) -> None:
        missing = [
            dependency
            for dependency in step.depends_on
            if dependency not in context.completed_step_ids
        ]
        if missing:
            raise WorkflowPreconditionError(
                f"步骤 {step.step_id} 缺少已完成依赖：{', '.join(missing)}"
            )
        if step.requires_business_id:
            context.require_business_id()
        if step.requires_status and context.actual_status != step.requires_status:
            raise WorkflowPreconditionError(
                f"步骤 {step.step_id} 前态不匹配："
                f"expected={step.requires_status!r}, actual={context.actual_status!r}"
            )

    @staticmethod
    def _normalize_result(
        raw_result: WorkflowStepResult | ModuleSmokeResult,
        *,
        step: WorkflowStep,
        page: Any,
    ) -> WorkflowStepResult:
        scope = page_scope_from_url(str(getattr(page, "url", "") or ""))
        if raw_result is None:
            raise WorkflowResultError(
                f"流程步骤 {step.step_id} 不能返回 None 或作为 no-op 通过"
            )
        elif isinstance(raw_result, ModuleSmokeResult):
            if (
                step.created_record
                and raw_result.mode not in ModuleSmokeDriver.CREATE_VERIFIED_MODES
            ):
                raise WorkflowResultError(
                    f"创建步骤 {step.step_id} 的 ModuleSmokeResult 未完成新增回读"
                )
            result = WorkflowStepResult.from_module_result(
                raw_result,
                page_scope=scope,
                created_by_workflow=step.created_record,
                cleanup_allowed=step.cleanup_allowed,
            )
        elif isinstance(raw_result, WorkflowStepResult):
            result = raw_result
            if (
                result.created_by_workflow != step.created_record
                or result.cleanup_allowed != step.cleanup_allowed
            ):
                raise WorkflowResultError(
                    f"步骤 {step.step_id} 的创建/清理结果与步骤声明不一致"
                )
        else:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 必须返回 WorkflowStepResult 或 ModuleSmokeResult"
            )
        if not result.page_scope and scope:
            result = replace(result, page_scope=scope)
        elif result.page_scope and scope and result.page_scope != scope:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 返回的 page_scope 与当前角色页面不一致"
            )
        return result

    @staticmethod
    def _assert_step_result(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
        *,
        execution: WorkflowStepExecution,
    ) -> None:
        if step.produces_business_id and not result.business_id:
            raise WorkflowResultError(f"步骤 {step.step_id} 未产出 business_id")
        if step.requires_business_id and not result.business_id:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 未返回已核对的 business_id"
            )
        WorkflowRunner._assert_same_business_id(step, context, result)
        WorkflowRunner._assert_correlation_ids(step, context, result)
        if step.expected_status and result.actual_status != step.expected_status:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 后态不匹配："
                f"expected={step.expected_status!r}, actual={result.actual_status!r}"
            )
        if step.step_type in {"mutation", "dynamic_mutation"}:
            WorkflowRunner._assert_step_evidence(
                step, context, result, execution=execution
            )
        elif step.requires_business_id:
            WorkflowRunner._assert_readback_evidence(
                step, context, result, execution=execution
            )
        elif result.step_evidence is not None:
            WorkflowRunner._assert_readback_evidence(
                step, context, result, execution=execution
            )
        WorkflowRunner._assert_cleanup_disposition(step, context, result)
        if result.created_by_workflow and result.cleanup_allowed:
            if not result.business_id or not result.page_scope:
                raise WorkflowResultError(
                    f"步骤 {step.step_id} 缺少 business_id/page_scope，不能登记清理"
                )

    @staticmethod
    def _assert_step_evidence(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
        *,
        execution: WorkflowStepExecution,
    ) -> None:
        evidence = result.step_evidence
        if evidence is None:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 缺少 WorkflowStepEvidence"
            )
        if evidence.action_available is not True:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 未证明当前角色可执行动作"
            )
        if step.requires_next_action and evidence.next_action_available is not True:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 未证明下一业务操作可用"
            )
        exact_counts = {
            "mutation_count": evidence.mutation_count,
            "request_count": evidence.request_count,
            "response_count": evidence.response_count,
        }
        if step.step_type == "dynamic_mutation":
            invalid_counts = [
                name
                for name, value in exact_counts.items()
                if not isinstance(value, int) or isinstance(value, bool) or value < 1
            ]
            if not invalid_counts and not (
                evidence.mutation_count
                == evidence.request_count
                == evidence.response_count
            ):
                invalid_counts = [
                    "mutation_count/request_count/response_count"
                ]
        else:
            invalid_counts = [
                name
                for name, value in exact_counts.items()
                if not isinstance(value, int) or isinstance(value, bool) or value != 1
            ]
        if invalid_counts:
            expected = "至少一次且次数一致" if step.step_type == "dynamic_mutation" else "必须且只能发生一次"
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} {expected}真实写请求和响应："
                + ", ".join(invalid_counts)
            )
        if (
            not isinstance(evidence.http_status, int)
            or isinstance(evidence.http_status, bool)
            or not 200 <= evidence.http_status < 300
        ):
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} HTTP 状态不是 2xx"
            )
        if evidence.business_success is not True:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 业务状态未成功"
            )
        if not evidence.business_code:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 缺少业务成功码"
            )
        WorkflowRunner._assert_mutation_identity(step, context, result)
        WorkflowRunner._assert_readback_evidence(
            step, context, result, execution=execution
        )

    @staticmethod
    def _assert_mutation_identity(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
    ) -> None:
        evidence = result.step_evidence
        assert evidence is not None
        expected_business_id = context.business_id or result.business_id
        if step.produces_business_id:
            if evidence.mutation_id_source != "response":
                raise WorkflowResultError(
                    f"创建步骤 {step.step_id} 的业务 ID 必须来自响应"
                )
            if (
                evidence.response_business_id != expected_business_id
                or evidence.mutation_business_id != expected_business_id
            ):
                raise WorkflowResultError(
                    f"创建步骤 {step.step_id} 的响应 business_id 与流程不一致"
                )
            if evidence.mutation_correlation_key:
                raise WorkflowResultError(
                    f"创建步骤 {step.step_id} 不能用 correlation ID 替代响应主 ID"
                )
            return
        if (
            evidence.mutation_id_source not in {"request", "response"}
            or not evidence.mutation_business_id
        ):
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 缺少响应 ID 或请求 identity 证据"
            )
        correlation_key = evidence.mutation_correlation_key
        if correlation_key:
            expected_identity = context.correlation_ids.get(correlation_key, "")
            if not expected_identity:
                raise WorkflowResultError(
                    f"迁移步骤 {step.step_id} 使用了未登记的 correlation key："
                    f"{correlation_key}"
                )
            identity_label = "correlation ID"
        else:
            expected_identity = expected_business_id
            identity_label = "business_id"
        source_label = "响应" if evidence.mutation_id_source == "response" else "请求"
        if evidence.mutation_business_id != expected_identity:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 的{source_label} "
                f"{identity_label} 与流程不一致"
            )
        if evidence.mutation_id_source == "response":
            if evidence.response_business_id != expected_identity:
                raise WorkflowResultError(
                    f"迁移步骤 {step.step_id} 的响应 {identity_label} 与流程不一致"
                )
            return
        if evidence.response_business_id:
            raise WorkflowResultError(
                f"迁移步骤 {step.step_id} 的请求 identity 证据不能同时声明响应 ID"
            )

    @staticmethod
    def _assert_readback_evidence(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
        *,
        execution: WorkflowStepExecution,
    ) -> None:
        evidence = result.step_evidence
        if evidence is None:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 缺少 WorkflowStepEvidence"
            )
        if evidence.record_visible is not True:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 未证明同一业务记录对当前角色可见"
            )
        event = execution.last_readback_event
        if event is None:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 未通过 wait_for_status 完成有界回读"
            )
        if evidence.readback_bounded is not True:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 状态回读没有有界超时证据"
            )
        if (
            not isinstance(evidence.poll_attempts, int)
            or isinstance(evidence.poll_attempts, bool)
            or evidence.poll_attempts < 1
        ):
            raise WorkflowResultError(
                f"步骤 {step.step_id} 缺少至少一次有界状态回读"
            )
        expected_business_id = context.business_id or result.business_id
        evidence_ids = {"result": result.business_id, "readback": evidence.readback_business_id}
        if event.business_id != evidence.readback_business_id:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 的回读事件 business_id 与证据不一致"
            )
        if event.state_source != evidence.state_source:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 的回读事件 state_source 与证据不一致"
            )
        if event.attempts != evidence.poll_attempts:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 的回读次数与证据不一致"
            )
        missing_ids = [name for name, value in evidence_ids.items() if not value]
        if missing_ids:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 缺少业务 ID 证据："
                + ", ".join(missing_ids)
            )
        mismatched_ids = [
            name
            for name, value in evidence_ids.items()
            if value != expected_business_id
        ]
        if mismatched_ids:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 的回读 business_id 与流程不一致："
                + ", ".join(mismatched_ids)
            )

    @staticmethod
    def _assert_cleanup_disposition(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
        *,
        checkpoint: bool = False,
    ) -> None:
        disposition = result.cleanup_disposition
        if step.created_record:
            if disposition is None:
                raise WorkflowResultError(
                    f"创建步骤 {step.step_id} 缺少 cleanup disposition"
                )
            expected = "pending" if step.cleanup_allowed else "retained"
            if disposition.disposition != expected:
                raise WorkflowResultError(
                    f"创建步骤 {step.step_id} 的 cleanup disposition 必须是 {expected}"
                )
            return
        if disposition is None:
            return
        if disposition.disposition == "pending":
            raise WorkflowResultError(
                f"非创建步骤 {step.step_id} 不能新增 pending 清理授权"
            )
        business_id = result.business_id or context.business_id
        known = any(
            record.business_id == business_id for record in context.cleanup_records
        )
        if not known:
            phase = "checkpoint" if checkpoint else "结果"
            raise WorkflowResultError(
                f"步骤 {step.step_id} 的 cleanup {phase} 未匹配本流程创建记录"
            )

    @staticmethod
    def _assert_correlation_ids(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
    ) -> None:
        conflicts = sorted(
            key
            for key, value in result.correlation_ids.items()
            if key in context.correlation_ids and context.correlation_ids[key] != value
        )
        if conflicts:
            raise WorkflowResultError(
                f"步骤 {step.step_id} 试图改写 correlation_ids："
                + ", ".join(conflicts)
            )

    @staticmethod
    def _assert_same_business_id(
        step: WorkflowStep,
        context: WorkflowContext,
        result: WorkflowStepResult,
    ) -> None:
        if (
            result.business_id
            and context.business_id
            and result.business_id != context.business_id
        ):
            raise WorkflowResultError(
                f"步骤 {step.step_id} 返回了不同 business_id，已停止跨记录流程"
            )
