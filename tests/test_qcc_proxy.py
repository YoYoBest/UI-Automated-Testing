import json
import threading
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

import pytest

from ei_ui_smoke.qcc_proxy import QccSearchService, QccSettings, clean_companies, create_server


def test_clean_companies_whitelists_fields_deduplicates_and_marks_inactive():
    payload = {"Result": [
        {"KeyNo": "1", "Name": "甲公司", "CreditCode": "A", "Status": "存续", "Secret": "drop"},
        {"KeyNo": "1", "Name": "甲公司重复", "CreditCode": "A", "Status": "存续"},
        {"KeyNo": "2", "Name": "乙公司", "CreditCode": "B", "Status": "注销"},
        {"KeyNo": "3", "CreditCode": "C"},
    ]}

    assert clean_companies(payload) == [
        {"keyNo": "1", "name": "甲公司", "creditCode": "A", "status": "存续", "selectable": True},
        {"keyNo": "2", "name": "乙公司", "creditCode": "B", "status": "注销", "selectable": False},
    ]


def test_mock_search_uses_memory_cache_without_persisting_data():
    service = QccSearchService(QccSettings(mode="mock", cache_ttl_seconds=60))
    first, first_source = service.search("北京汽车")
    second, second_source = service.search(" 北京 汽车 ")

    assert first_source == "mock"
    assert second_source == "cache"
    assert second == first
    assert all(set(item) == {"keyNo", "name", "creditCode", "status", "selectable"} for item in first)


def test_real_mode_requires_credentials():
    service = QccSearchService(QccSettings(mode="real"))
    with pytest.raises(RuntimeError, match="QCC_API_KEY"):
        service.search("测试企业")


def test_http_contract_and_validation():
    server = create_server("127.0.0.1", 0, QccSettings(mode="mock"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        keyword = quote("北京汽车")
        with urlopen(f"{base_url}/api/qcc/companies?keyword={keyword}", timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["code"] == "0"
        assert payload["source"] == "mock"
        assert payload["data"]

        with pytest.raises(HTTPError) as error:
            urlopen(f"{base_url}/api/qcc/companies?keyword=a", timeout=2)
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
