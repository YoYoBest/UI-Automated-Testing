import json

from ei_ui_smoke.qcc_browser import (
    backend_qcc_url,
    extract_backend_qcc_payload,
    extract_search_keyword,
    frontend_search_payload,
)


def test_extracts_keyword_from_existing_management_platform_request():
    assert extract_search_keyword("https://host/common-service/dataManager/entSearch?params=%E5%8C%97%E4%BA%AC") == "北京"


def test_extracts_keyword_from_local_verification_contract():
    assert extract_search_keyword("http://127.0.0.1:8765/api/qcc/companies?keyword=%E6%B1%BD%E8%BD%A6") == "汽车"


def test_frontend_payload_matches_qcc_select_contract():
    companies = [{"keyNo": "1", "name": "甲公司", "creditCode": "A", "status": "存续", "selectable": True}]
    assert frontend_search_payload(companies) == {"status": "0", "code": "0", "data": companies}


def test_builds_deployed_qcc_url_with_existing_gateway_prefix():
    url = "https://host/ezgo/common-service/dataManager/entSearch?params=x"
    assert backend_qcc_url(url, "北京 汽车") == (
        "https://host/ezgo/ei-service/BPI/FUND/QCCSearchData?keyword=%E5%8C%97%E4%BA%AC%20%E6%B1%BD%E8%BD%A6"
    )


def test_extracts_raw_qcc_json_from_deployed_response():
    class Response:
        @staticmethod
        def json():
            return {"status": "0", "data": {"value": json.dumps({"Status": "200", "Result": []})}}

    assert extract_backend_qcc_payload(Response()) == {"Status": "200", "Result": []}
