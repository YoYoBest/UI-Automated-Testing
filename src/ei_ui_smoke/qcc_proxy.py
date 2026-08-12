from __future__ import annotations

import argparse
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse
from urllib.request import Request, urlopen


DEFAULT_SEARCH_URL = "https://api.qichacha.com/ECIV4/SearchWide"
INACTIVE_STATUSES = {"注销", "吊销", "撤销", "停业", "清算"}
MOCK_COMPANIES = (
    {"KeyNo": "mock-001", "Name": "北京汽车集团产业投资有限公司", "CreditCode": "91110108757728843G", "Status": "存续"},
    {"KeyNo": "mock-002", "Name": "北京汽车集团有限公司", "CreditCode": "911100001011596199", "Status": "存续"},
    {"KeyNo": "mock-003", "Name": "北京示例科技有限公司", "CreditCode": "91110000MOCK00001X", "Status": "注销"},
)


class QccProxyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class QccSettings:
    mode: str = "mock"
    api_key: str = ""
    secret_key: str = ""
    search_url: str = DEFAULT_SEARCH_URL
    cache_ttl_seconds: int = 600
    timeout_seconds: float = 8.0
    max_results: int = 20

    @classmethod
    def from_env(cls) -> "QccSettings":
        return cls(
            mode=os.getenv("QCC_MODE", "mock").strip().lower(),
            api_key=os.getenv("QCC_API_KEY", "").strip(),
            secret_key=os.getenv("QCC_SECRET_KEY", "").strip(),
            search_url=os.getenv("QCC_SEARCH_URL", DEFAULT_SEARCH_URL).strip(),
            cache_ttl_seconds=max(0, int(os.getenv("QCC_CACHE_TTL_SECONDS", "600"))),
            timeout_seconds=max(0.1, float(os.getenv("QCC_TIMEOUT_SECONDS", "8"))),
            max_results=max(1, min(100, int(os.getenv("QCC_MAX_RESULTS", "20")))),
        )


class MemoryTtlCache:
    def __init__(self, ttl_seconds: int, clock: Callable[[], float] = time.monotonic):
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._values: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> list[dict[str, Any]] | None:
        with self._lock:
            cached = self._values.get(key)
            if cached is None:
                return None
            expires_at, value = cached
            if expires_at <= self.clock():
                self._values.pop(key, None)
                return None
            return [dict(item) for item in value]

    def set(self, key: str, value: list[dict[str, Any]]) -> None:
        if self.ttl_seconds <= 0:
            return
        with self._lock:
            self._values[key] = (self.clock() + self.ttl_seconds, [dict(item) for item in value])


def normalize_keyword(keyword: str) -> str:
    clean = "".join(keyword.split()).strip()
    if len(clean) < 2:
        raise ValueError("keyword must contain at least 2 characters")
    if len(clean) > 100:
        raise ValueError("keyword must not exceed 100 characters")
    return clean


def clean_companies(payload: Any, max_results: int = 20) -> list[dict[str, Any]]:
    raw_result = payload.get("Result", payload) if isinstance(payload, dict) else payload
    if isinstance(raw_result, dict):
        raw_result = raw_result.get("Data") or raw_result.get("Items") or raw_result.get("Result") or []
    if not isinstance(raw_result, list):
        return []

    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_result:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("Name") or raw.get("name") or "").strip()
        key_no = str(raw.get("KeyNo") or raw.get("keyNo") or "").strip()
        credit_code = str(raw.get("CreditCode") or raw.get("creditCode") or "").strip()
        status = str(raw.get("Status") or raw.get("status") or "").strip()
        if not name:
            continue
        identity = key_no or credit_code or name.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        cleaned.append({
            "keyNo": key_no,
            "name": name,
            "creditCode": credit_code,
            "status": status,
            "selectable": bool(credit_code) and status not in INACTIVE_STATUSES,
        })
        if len(cleaned) >= max_results:
            break
    return cleaned


class QccSearchService:
    def __init__(self, settings: QccSettings, opener: Callable[..., Any] = urlopen):
        if settings.mode not in {"mock", "real"}:
            raise ValueError("QCC_MODE must be 'mock' or 'real'")
        self.settings = settings
        self.opener = opener
        self.cache = MemoryTtlCache(settings.cache_ttl_seconds)

    def search(self, keyword: str) -> tuple[list[dict[str, Any]], str]:
        clean_keyword = normalize_keyword(keyword)
        cached = self.cache.get(clean_keyword.casefold())
        if cached is not None:
            return cached, "cache"
        raw = self._mock_search(clean_keyword) if self.settings.mode == "mock" else self._real_search(clean_keyword)
        result = clean_companies(raw, self.settings.max_results)
        self.cache.set(clean_keyword.casefold(), result)
        return result, self.settings.mode

    def _mock_search(self, keyword: str) -> list[dict[str, str]]:
        return [item for item in MOCK_COMPANIES if keyword.casefold() in item["Name"].casefold()]

    def _real_search(self, keyword: str) -> Any:
        if not self.settings.api_key or not self.settings.secret_key:
            raise QccProxyError("QCC_API_KEY and QCC_SECRET_KEY are required in real mode")
        timespan = str(int(time.time()))
        token = hashlib.md5(
            f"{self.settings.api_key}{timespan}{self.settings.secret_key}".encode("utf-8")
        ).hexdigest().upper()
        url = f"{self.settings.search_url}?key={quote(self.settings.api_key)}&keyword={quote(keyword)}"
        request = Request(url, headers={"Token": token, "Timespan": timespan, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=self.settings.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise QccProxyError("QCC search request failed") from exc
        status = str(payload.get("Status", "200")) if isinstance(payload, dict) else "200"
        if status not in {"200", "0"}:
            raise QccProxyError("QCC returned an unsuccessful response")
        return payload


def make_handler(service: QccSearchService) -> type[BaseHTTPRequestHandler]:
    class QccHandler(BaseHTTPRequestHandler):
        def do_OPTIONS(self) -> None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors_headers()
            self.end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/health":
                self._json(HTTPStatus.OK, {"status": "ok", "mode": service.settings.mode})
                return
            if parsed.path != "/api/qcc/companies":
                self._json(HTTPStatus.NOT_FOUND, {"code": "NOT_FOUND", "message": "route not found"})
                return
            keyword = parse_qs(parsed.query).get("keyword", [""])[0]
            try:
                companies, source = service.search(keyword)
                self._json(HTTPStatus.OK, {"code": "0", "data": companies, "source": source})
            except ValueError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"code": "INVALID_ARGUMENT", "message": str(exc)})
            except QccProxyError as exc:
                self._json(HTTPStatus.BAD_GATEWAY, {"code": "UPSTREAM_ERROR", "message": str(exc)})

        def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(body)

        def _cors_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "http://localhost:4015")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def log_message(self, format: str, *args: Any) -> None:
            return

    return QccHandler


def create_server(host: str, port: int, settings: QccSettings | None = None) -> ThreadingHTTPServer:
    service = QccSearchService(settings or QccSettings.from_env())
    return ThreadingHTTPServer((host, port), make_handler(service))


def main() -> None:
    parser = argparse.ArgumentParser(description="Local QCC proxy and mock server for UI verification")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(args.host, args.port)
    print(f"QCC verification server: http://{args.host}:{args.port} ({QccSettings.from_env().mode})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
