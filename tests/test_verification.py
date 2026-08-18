from ei_ui_smoke.contracts import normalize_field
from ei_ui_smoke.verification import assert_runtime_values, extract_business_id, response_matches


def test_extract_business_id_from_nested_save_response():
    assert extract_business_id({"code": 200, "data": {"businessId": 123}}) == "123"


def test_extract_fund_id_from_custom_add_response():
    assert extract_business_id({"status": "0", "fundId": "2082747613298864130"}) == "2082747613298864130"


def test_extract_manage_platform_id_from_success_response():
    response = {
        "status": "0",
        "msg": "新增成功",
        "data": None,
        "errors": [],
        "mcId": "2082997035735756802",
    }

    assert extract_business_id(response) == "2082997035735756802"


def test_extract_file_type_item_id_from_success_response():
    assert extract_business_id({"status": "0", "data": None, "itemId": "10001"}) == "10001"


def test_extract_business_id_rejects_child_table_only_id():
    response = {
        "status": "0",
        "data": {
            "ownershipStructureList": [
                {"id": "child-row-1", "shareholderName": "测试股东"},
            ],
        },
    }

    assert extract_business_id(response) == ""


def test_extract_business_id_rejects_conflicting_main_record_ids():
    response = {
        "status": "0",
        "businessId": "record-1",
        "data": {"id": "record-2"},
    }

    assert extract_business_id(response) == ""


def test_extract_business_id_rejects_multiple_response_records():
    response = {
        "status": "0",
        "data": [{"id": "record-1"}, {"id": "record-2"}],
    }

    assert extract_business_id(response) == ""


def test_extract_business_id_accepts_one_record_in_known_envelope():
    response = {"status": "0", "data": [{"id": "record-1"}]}

    assert extract_business_id(response) == "record-1"


def test_save_url_pattern_supports_glob_and_substring():
    url = "https://host/api/fund/save?id=1"
    assert response_matches(url, "*/api/fund/save*")
    assert response_matches(url, "/api/fund/save")


def test_runtime_detail_is_compared_with_business_values():
    fields = [normalize_field({"fieldCode": "custom", "fixedType": 0, "viewVisible": 1}, "test")]
    response = {"data": {"data": {"dataJson": '{"custom":"A,B"}'}}}
    assert_runtime_values(response, fields, {"custom": ["A", "B"]})
