"""Read-only deployment probes used to block tests ahead of an unavailable API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import ssl
import subprocess
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_PROBES_FILE = Path("data") / "environment_api_probes.json"
DEFAULT_VERSION_STATE_FILE = Path("artifacts") / "environment-api-version-state.json"
_ALLOWED_METHODS = {"GET", "HEAD", "POST"}
_WRITE_PATH_TOKENS = {
    "add", "batchdelete", "create", "delete", "insert", "remove", "save",
    "submit", "update", "upload",
}
_PATH_TEMPLATE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_MAX_RESPONSE_BYTES = 1024 * 1024


class EnvironmentProbeConfigError(ValueError):
    """The local probe manifest is unsafe or cannot identify its target."""


@dataclass(frozen=True)
class ApiProbeStep:
    id: str
    method: str
    path: str
    body: dict[str, Any] | list[Any] | None
    extracts: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class ApiProbe:
    id: str
    form_codes: tuple[str, ...]
    components: tuple[str, ...]
    method: str
    path: str
    body: dict[str, Any] | list[Any] | None
    version_header: str = ""
    version_mismatch_alert_hours: int = 24
    steps: tuple[ApiProbeStep, ...] = ()
    action_paths: tuple[tuple[str, ...], ...] = ()
    capability_name: str = ""
    capability_failure_statuses: tuple[int, ...] = ()


@dataclass(frozen=True)
class ApiProbeStepResult:
    id: str
    method: str
    url: str
    status: int | None
    error: str = ""


@dataclass(frozen=True)
class ApiProbeResult:
    probe: ApiProbe
    url: str
    status: int | None
    deployed_version: str = ""
    error: str = ""
    steps: tuple[ApiProbeStepResult, ...] = ()

    @property
    def unavailable(self) -> bool:
        return self.status == 404

    @property
    def blocking_classification(self) -> str:
        if self.status == 404:
            return "environment-version-mismatch"
        if self.status in self.probe.capability_failure_statuses:
            return "environment-capability-unavailable"
        return ""


@dataclass(frozen=True)
class EnvironmentBlock:
    target_name: str
    probe_id: str
    url: str
    status: int
    classification: str = "environment-version-mismatch"
    capability_name: str = ""
    action_path: tuple[str, ...] = ()


def load_environment_api_probes(path: Path) -> list[ApiProbe]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EnvironmentProbeConfigError(f"环境接口预检配置无效：{exc}") from exc
    entries = payload.get("probes", []) if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise EnvironmentProbeConfigError("环境接口预检配置的 probes 必须是数组")
    return [_parse_probe(entry, index) for index, entry in enumerate(entries, 1)]


def _parse_probe(entry: Any, index: int) -> ApiProbe:
    if not isinstance(entry, dict):
        raise EnvironmentProbeConfigError(f"probes[{index}] 必须是对象")
    probe_id = str(entry.get("id", "")).strip()
    form_codes = _string_tuple(entry.get("formCodes"))
    components = _string_tuple(entry.get("components"))
    if not probe_id or not (form_codes or components):
        raise EnvironmentProbeConfigError(
            f"probes[{index}] 必须包含 id 和 formCodes 或 components"
        )
    raw_steps = entry.get("steps")
    if raw_steps is None:
        steps = (_parse_probe_step(entry, f"probes[{index}]", "request"),)
    else:
        if not isinstance(raw_steps, list) or not raw_steps:
            raise EnvironmentProbeConfigError(f"probes[{index}].steps 必须是非空数组")
        steps = tuple(
            _parse_probe_step(raw_step, f"probes[{index}].steps[{step_index}]", f"step-{step_index}")
            for step_index, raw_step in enumerate(raw_steps, 1)
        )
    _validate_step_templates(steps, f"probes[{index}]")
    method, path, body = steps[0].method, steps[0].path, steps[0].body
    action_paths = _parse_action_paths(entry.get("actionPaths"), f"probes[{index}]")
    capability_statuses = _parse_capability_statuses(
        entry.get("capabilityFailureStatuses"), f"probes[{index}]"
    )
    hours = entry.get("versionMismatchAlertHours", 24)
    try:
        hours = max(1, int(hours))
    except (TypeError, ValueError) as exc:
        raise EnvironmentProbeConfigError(
            f"probes[{index}] versionMismatchAlertHours 必须是正整数"
        ) from exc
    return ApiProbe(
        id=probe_id,
        form_codes=form_codes,
        components=components,
        method=method,
        path=path,
        body=body,
        version_header=str(entry.get("versionHeader", "")).strip(),
        version_mismatch_alert_hours=hours,
        steps=steps if raw_steps is not None else (),
        action_paths=action_paths,
        capability_name=str(entry.get("capabilityName", "")).strip(),
        capability_failure_statuses=capability_statuses,
    )


def _parse_probe_step(entry: Any, location: str, default_id: str) -> ApiProbeStep:
    if not isinstance(entry, dict):
        raise EnvironmentProbeConfigError(f"{location} 必须是对象")
    method = str(entry.get("method", "GET")).upper().strip()
    path = str(entry.get("path", "")).strip()
    body = entry.get("body")
    if method not in _ALLOWED_METHODS or method == "POST" and entry.get("readOnly") is not True:
        raise EnvironmentProbeConfigError(
            f"{location} 只允许 GET/HEAD，或显式 readOnly=true 的 POST"
        )
    if not path.startswith("/") or "://" in path or "#" in path:
        raise EnvironmentProbeConfigError(f"{location}.path 必须是无 fragment 的站内绝对路径")
    if _looks_like_write_path(path):
        raise EnvironmentProbeConfigError(
            f"{location}.path 不能指向保存、删除、提交或上传等写接口"
        )
    if method == "POST" and not isinstance(body, (dict, list)):
        raise EnvironmentProbeConfigError(f"{location} 的只读 POST 必须提供 JSON body")
    if method != "POST" and body is not None:
        raise EnvironmentProbeConfigError(f"{location} 的 {method} 请求不能带 body")
    extracts = _parse_extracts(entry.get("extract"), location)
    return ApiProbeStep(
        id=str(entry.get("id", default_id)).strip() or default_id,
        method=method,
        path=path,
        body=body,
        extracts=extracts,
    )


def _parse_extracts(value: Any, location: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise EnvironmentProbeConfigError(f"{location}.extract 必须是变量到 JSON 路径的对象")
    extracts = []
    for variable, raw_paths in value.items():
        if not isinstance(variable, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", variable):
            raise EnvironmentProbeConfigError(f"{location}.extract 包含无效变量名")
        paths = [raw_paths] if isinstance(raw_paths, str) else raw_paths
        if not isinstance(paths, list) or not paths or not all(
            isinstance(path, str) and path.strip() for path in paths
        ):
            raise EnvironmentProbeConfigError(
                f"{location}.extract.{variable} 必须是 JSON 路径或非空路径数组"
            )
        extracts.append((variable, tuple(path.strip() for path in paths)))
    return tuple(extracts)


def _parse_action_paths(value: Any, location: str) -> tuple[tuple[str, ...], ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EnvironmentProbeConfigError(f"{location}.actionPaths 必须是路径数组")
    paths = []
    for path in value:
        if not isinstance(path, list) or not path or not all(
            isinstance(part, str) and part.strip() for part in path
        ):
            raise EnvironmentProbeConfigError(
                f"{location}.actionPaths 每项必须是非空字符串数组"
            )
        paths.append(tuple(part.strip() for part in path))
    return tuple(paths)


def _parse_capability_statuses(value: Any, location: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise EnvironmentProbeConfigError(
            f"{location}.capabilityFailureStatuses 必须是非空 5xx 状态码数组"
        )
    statuses = []
    for raw_status in value:
        if isinstance(raw_status, bool):
            status = 0
        else:
            try:
                status = int(raw_status)
            except (TypeError, ValueError):
                status = 0
        if status < 500 or status > 599:
            raise EnvironmentProbeConfigError(
                f"{location}.capabilityFailureStatuses 只允许 5xx 状态码"
            )
        if status not in statuses:
            statuses.append(status)
    return tuple(statuses)


def _validate_step_templates(steps: tuple[ApiProbeStep, ...], location: str) -> None:
    available: set[str] = set()
    for step in steps:
        missing = set(_PATH_TEMPLATE.findall(step.path)) - available
        if missing:
            names = ", ".join(sorted(missing))
            raise EnvironmentProbeConfigError(
                f"{location} 的步骤 {step.id} 使用了尚未提取的变量：{names}"
            )
        available.update(variable for variable, _paths in step.extracts)


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item.strip() for item in value if isinstance(item, str) and item.strip())


def _looks_like_write_path(path: str) -> bool:
    """Reject explicit write verbs even when a manifest incorrectly says readOnly."""
    normalized = path.lower().replace("-", "_")
    parts = [part.replace("_", "") for part in normalized.split("/") if part]
    return any(
        token in part
        for part in parts
        for token in _WRITE_PATH_TOKENS
    )


def matching_probes(probes: Iterable[ApiProbe], environment: Mapping[str, str]) -> list[ApiProbe]:
    action_cases = _environment_action_cases(environment)
    return [
        probe for probe in probes
        if any(probe_matches_action(probe, action, environment) for action in action_cases)
    ]


def probe_matches_action(
    probe: ApiProbe,
    action: Mapping[str, Any],
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Return whether one command/action has the exact capability dependency."""
    fallback = environment or {}
    form_code = str(
        action.get("form_code") or action.get("formCode") or fallback.get("EI_FORM_CODE", "")
    ).strip()
    component = _normalize_component(str(
        action.get("component") or fallback.get("EI_COMPONENT", "")
    ))
    if form_code not in probe.form_codes and component not in {
        _normalize_component(value) for value in probe.components
    }:
        return False
    if not probe.action_paths:
        return True
    actual_path = _coerce_action_path(
        action.get("action_path") or action.get("operation_path")
    )
    if not actual_path:
        operation = str(
            action.get("action") or action.get("operation") or fallback.get("EI_ACTION", "")
        ).strip()
        actual_path = (operation,) if operation else ()
    return actual_path in probe.action_paths


def _environment_action_cases(environment: Mapping[str, str]) -> list[Mapping[str, Any]]:
    raw_actions = environment.get("EI_ACTIONS_JSON", "")
    if raw_actions:
        try:
            parsed = json.loads(raw_actions)
        except (TypeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, list):
            actions = [action for action in parsed if isinstance(action, dict)]
            if actions:
                return actions
    return [{
        "form_code": environment.get("EI_FORM_CODE", ""),
        "component": environment.get("EI_COMPONENT", ""),
        "action": environment.get("EI_ACTION", ""),
        "action_path": environment.get("EI_ACTION_PATH", ""),
    }]


def _coerce_action_path(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(part).strip() for part in value if str(part).strip())


def _normalize_component(value: str) -> str:
    component = value.strip().replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
    component = component.lstrip("/")
    for prefix in ("@/", "src/views/", "srcEi/views/", "views/"):
        if component.startswith(prefix):
            component = component[len(prefix):]
            break
    return component.removesuffix(".vue").strip("/")


def probe_environment_apis(
    probes: Iterable[ApiProbe],
    *,
    base_url: str,
    storage_state: str = "",
    request: Callable[..., tuple[int, Mapping[str, str]]] | None = None,
) -> list[ApiProbeResult]:
    requester = request or _http_request
    results = []
    for probe in probes:
        context: dict[str, Any] = {}
        step_results: list[ApiProbeStepResult] = []
        deployed_version = ""
        current_url = _service_url(base_url, probe.path)
        terminal_status: int | None = None
        terminal_error = ""
        steps = probe.steps or (ApiProbeStep(
            id="request", method=probe.method, path=probe.path, body=probe.body,
        ),)
        for step in steps:
            current_url = _service_url(base_url, _render_step_path(step.path, context))
            try:
                raw_response = requester(
                    current_url, method=step.method, body=step.body,
                    storage_state=storage_state,
                )
                status, headers, payload = _unpack_response(raw_response)
                terminal_status = int(status)
                deployed_version = (
                    _header_value(headers, probe.version_header) or deployed_version
                )
                step_results.append(ApiProbeStepResult(
                    step.id, step.method, current_url, terminal_status,
                ))
                if not 200 <= terminal_status < 300:
                    break
                if step.extracts:
                    parsed_payload = _parse_json_payload(payload)
                    for variable, json_paths in step.extracts:
                        extracted = _first_json_path(parsed_payload, json_paths)
                        if extracted is None or extracted == "":
                            terminal_status = None
                            terminal_error = (
                                f"步骤 {step.id} 的成功响应未包含链式变量 {variable}"
                            )
                            step_results[-1] = ApiProbeStepResult(
                                step.id, step.method, current_url,
                                step_results[-1].status, terminal_error,
                            )
                            break
                        context[variable] = extracted
                    if terminal_error:
                        break
            except Exception as exc:
                terminal_status = None
                terminal_error = str(exc)
                step_results.append(ApiProbeStepResult(
                    step.id, step.method, current_url, None, terminal_error,
                ))
                break
        results.append(ApiProbeResult(
            probe, current_url, terminal_status, deployed_version,
            terminal_error, tuple(step_results),
        ))
    return results


def _render_step_path(path: str, context: Mapping[str, Any]) -> str:
    return _PATH_TEMPLATE.sub(
        lambda match: quote(str(context[match.group(1)]), safe=""),
        path,
    )


def _unpack_response(
    response: Any,
) -> tuple[int, Mapping[str, str], Any]:
    if not isinstance(response, tuple) or len(response) not in {2, 3}:
        raise RuntimeError("环境接口预检请求器必须返回 (status, headers[, body])")
    status, headers = response[:2]
    if not isinstance(headers, Mapping):
        raise RuntimeError("环境接口预检响应 headers 必须是映射")
    return int(status), headers, response[2] if len(response) == 3 else None


def _parse_json_payload(payload: Any) -> Any:
    if isinstance(payload, (dict, list)):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    if not isinstance(payload, str) or not payload.strip():
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def _first_json_path(payload: Any, paths: tuple[str, ...]) -> Any:
    for path in paths:
        current = payload
        found = True
        for part in path.split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                current = current[int(part)]
            else:
                found = False
                break
        if found:
            return current
    return None


def _service_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise EnvironmentProbeConfigError(f"目标环境地址无效：{base_url}")
    relative = urlsplit(path)
    return urlunsplit((parsed.scheme, parsed.netloc, relative.path, relative.query, ""))


def _http_request(
    url: str, *, method: str, body: dict[str, Any] | list[Any] | None,
    storage_state: str,
) -> tuple[int, Mapping[str, str], bytes]:
    headers = {"Accept": "application/json"}
    if authorization := _storage_authorization(storage_state, url):
        headers["Authorization"] = authorization
    if cookie_header := _storage_cookie_header(storage_state, url):
        headers["Cookie"] = cookie_header
    if tenant_id := _storage_tenant_id(storage_state, url):
        headers["x-tenant-id"] = tenant_id
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15, context=ssl._create_unverified_context()) as response:
            reader = getattr(response, "read", None)
            payload = reader(_MAX_RESPONSE_BYTES + 1) if callable(reader) else b""
            return int(response.status), dict(response.headers.items()), payload[:_MAX_RESPONSE_BYTES]
    except HTTPError as exc:
        payload = exc.read(_MAX_RESPONSE_BYTES + 1)
        return (
            int(exc.code), dict(exc.headers.items()) if exc.headers else {},
            payload[:_MAX_RESPONSE_BYTES],
        )
    except URLError as exc:
        raise RuntimeError(f"网络或 TLS 预检失败：{exc.reason}") from exc


def _storage_cookie_header(storage_state: str, url: str) -> str:
    payload = _load_storage_state(storage_state)
    if not payload:
        return ""
    host = urlsplit(url).hostname or ""
    cookies = payload.get("cookies", []) if isinstance(payload, dict) else []
    pairs = []
    for cookie in cookies if isinstance(cookies, list) else []:
        if not isinstance(cookie, dict):
            continue
        domain = str(cookie.get("domain", "")).lstrip(".")
        if domain and (host == domain or host.endswith("." + domain)):
            name, value = cookie.get("name"), cookie.get("value")
            if isinstance(name, str) and isinstance(value, str):
                pairs.append(f"{name}={value}")
    return "; ".join(pairs)


def _storage_authorization(storage_state: str, url: str) -> str:
    payload = _load_storage_state(storage_state)
    if not payload:
        return ""
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    for item in payload.get("origins", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("origin") != origin:
            continue
        for entry in item.get("localStorage", []) if isinstance(item.get("localStorage"), list) else []:
            if isinstance(entry, dict) and entry.get("name") == "accessToken":
                token = str(entry.get("value", "")).strip()
                if token:
                    return token
    return ""


def _storage_tenant_id(storage_state: str, url: str) -> str:
    payload = _load_storage_state(storage_state)
    if not payload:
        return ""
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"
    accepted_names = {"tenantId", "tenant_id", "x-tenant-id"}
    for item in payload.get("origins", []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict) or item.get("origin") != origin:
            continue
        entries = item.get("localStorage", [])
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and entry.get("name") in accepted_names:
                tenant_id = str(entry.get("value", "")).strip()
                if tenant_id:
                    return tenant_id
    return ""


def _load_storage_state(storage_state: str) -> dict[str, Any] | None:
    if not storage_state:
        return None
    try:
        payload = json.loads(Path(storage_state).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _header_value(headers: Mapping[str, str], name: str) -> str:
    if not name:
        return ""
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value).strip()
    return ""


def block_unavailable_commands(commands, results: Iterable[ApiProbeResult]):
    unavailable = {
        result.probe.id: result
        for result in results
        if result.blocking_classification
    }
    runnable, blocked = [], []
    for command in commands:
        target, environment, _test_file = command
        matching = matching_probes(
            (result.probe for result in unavailable.values()), environment,
        )
        if not matching:
            runnable.append(command)
            continue
        for probe in matching:
            result = unavailable[probe.id]
            blocked.append(EnvironmentBlock(
                target_name=" / ".join(target.path) or target.name,
                probe_id=probe.id,
                url=result.url,
                status=result.status or 404,
                classification=result.blocking_classification,
                capability_name=probe.capability_name,
                action_path=tuple(getattr(target, "operation_path", ()) or ()),
            ))
    return runnable, blocked


def source_revision(source_root: str) -> str:
    root = Path(source_root).expanduser()
    if not source_root or not root.exists():
        return ""
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or "").strip() if completed.returncode == 0 else ""


def update_version_mismatch_state(
    results: Iterable[ApiProbeResult],
    *,
    source_version: str,
    state_file: Path,
    now: datetime | None = None,
) -> list[str]:
    """Warn only after a concrete source/deployment mismatch persists past its threshold."""
    if not source_version:
        return []
    timestamp = now or datetime.now(UTC)
    try:
        saved = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        saved = {}
    active: dict[str, dict[str, str]] = {}
    warnings = []
    for result in results:
        if result.status is None or not 200 <= result.status < 300:
            continue
        deployed = result.deployed_version
        if not deployed or deployed == source_version:
            continue
        key = f"{result.probe.id}:{source_version}:{deployed}"
        previous = saved.get(key, {}) if isinstance(saved, dict) else {}
        first_seen = _parse_time(previous.get("firstSeen")) or timestamp
        active[key] = {"firstSeen": first_seen.isoformat()}
        if timestamp - first_seen >= timedelta(hours=result.probe.version_mismatch_alert_hours):
            warnings.append(
                f"环境版本差异持续超过 {result.probe.version_mismatch_alert_hours} 小时："
                f"{result.probe.id} source={source_version[:12]} deployed={deployed[:12]}"
            )
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(active, ensure_ascii=False, indent=2), encoding="utf-8")
    return warnings


def _parse_time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None


def write_environment_preflight_report(
    path: Path,
    results: Iterable[ApiProbeResult],
    blocks: Iterable[EnvironmentBlock],
    warnings: Iterable[str],
) -> None:
    payload = {
        "classification": "environment-api-preflight",
        "probes": [
            {
                "id": result.probe.id,
                "method": result.probe.method,
                "url": result.url,
                "status": result.status,
                "deployedVersion": result.deployed_version,
                "error": result.error,
                "classification": result.blocking_classification,
                "capabilityName": result.probe.capability_name,
                "actionPaths": [list(path) for path in result.probe.action_paths],
                "steps": [asdict(step) for step in result.steps],
            }
            for result in results
        ],
        "blocked": [
            asdict(block)
            for block in blocks
        ],
        "warnings": list(warnings),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
