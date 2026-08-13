"""Read-only deployment probes used to block tests ahead of an unavailable API."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import ssl
import subprocess
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_PROBES_FILE = Path("data") / "environment_api_probes.json"
DEFAULT_VERSION_STATE_FILE = Path("artifacts") / "environment-api-version-state.json"
_ALLOWED_METHODS = {"GET", "HEAD", "POST"}
_WRITE_PATH_TOKENS = {
    "add", "batchdelete", "create", "delete", "insert", "remove", "save",
    "submit", "update", "upload",
}


class EnvironmentProbeConfigError(ValueError):
    """The local probe manifest is unsafe or cannot identify its target."""


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


@dataclass(frozen=True)
class ApiProbeResult:
    probe: ApiProbe
    url: str
    status: int | None
    deployed_version: str = ""
    error: str = ""

    @property
    def unavailable(self) -> bool:
        return self.status == 404


@dataclass(frozen=True)
class EnvironmentBlock:
    target_name: str
    probe_id: str
    url: str
    status: int


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
    method = str(entry.get("method", "GET")).upper().strip()
    path = str(entry.get("path", "")).strip()
    body = entry.get("body")
    if not probe_id or not (form_codes or components):
        raise EnvironmentProbeConfigError(
            f"probes[{index}] 必须包含 id 和 formCodes 或 components"
        )
    if method not in _ALLOWED_METHODS or method == "POST" and entry.get("readOnly") is not True:
        raise EnvironmentProbeConfigError(
            f"probes[{index}] 只允许 GET/HEAD，或显式 readOnly=true 的 POST"
        )
    if not path.startswith("/") or "://" in path:
        raise EnvironmentProbeConfigError(f"probes[{index}] path 必须是站内绝对路径")
    if _looks_like_write_path(path):
        raise EnvironmentProbeConfigError(
            f"probes[{index}] path 不能指向保存、删除、提交或上传等写接口"
        )
    if method == "POST" and not isinstance(body, (dict, list)):
        raise EnvironmentProbeConfigError(f"probes[{index}] 的只读 POST 必须提供 JSON body")
    if method != "POST" and body is not None:
        raise EnvironmentProbeConfigError(f"probes[{index}] 的 {method} 请求不能带 body")
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
    )


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
    form_code = str(environment.get("EI_FORM_CODE", "")).strip()
    component = str(environment.get("EI_COMPONENT", "")).strip().strip("/")
    return [
        probe
        for probe in probes
        if form_code in probe.form_codes or component in probe.components
    ]


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
        url = _service_url(base_url, probe.path)
        try:
            status, headers = requester(
                url, method=probe.method, body=probe.body, storage_state=storage_state,
            )
            deployed_version = _header_value(headers, probe.version_header)
            results.append(ApiProbeResult(probe, url, int(status), deployed_version))
        except Exception as exc:
            results.append(ApiProbeResult(probe, url, None, error=str(exc)))
    return results


def _service_url(base_url: str, path: str) -> str:
    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise EnvironmentProbeConfigError(f"目标环境地址无效：{base_url}")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _http_request(
    url: str, *, method: str, body: dict[str, Any] | list[Any] | None,
    storage_state: str,
) -> tuple[int, Mapping[str, str]]:
    headers = {"Accept": "application/json"}
    if authorization := _storage_authorization(storage_state, url):
        headers["Authorization"] = authorization
    if cookie_header := _storage_cookie_header(storage_state, url):
        headers["Cookie"] = cookie_header
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15, context=ssl._create_unverified_context()) as response:
            return int(response.status), dict(response.headers.items())
    except HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}
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
    unavailable = {result.probe.id: result for result in results if result.unavailable}
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
            }
            for result in results
        ],
        "blocked": [
            {**asdict(block), "classification": "environment-version-mismatch"}
            for block in blocks
        ],
        "warnings": list(warnings),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
