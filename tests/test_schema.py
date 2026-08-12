from ei_ui_smoke.contracts import normalize_field
from ei_ui_smoke.models import DomField
from ei_ui_smoke.schema import extract_runtime_fields, match_dom_fields, merge_definitions


def test_extract_runtime_fields_from_nested_api_envelope():
    response = {"code": 200, "data": {"data": {"fields": [
        {"fieldCode": "name", "fieldName": "名称", "fixedType": 1},
        {"fieldCode": "custom", "fieldName": "扩展", "fixedType": 0},
    ]}}}
    fields = extract_runtime_fields(response)
    assert [field.field_code for field in fields] == ["name", "custom"]


def test_runtime_definition_overrides_checked_in_json():
    old = normalize_field({"fieldCode": "name", "fieldName": "旧名称"}, "json")
    new = normalize_field({"fieldCode": "name", "fieldName": "新名称"}, "api")
    assert merge_definitions([old], [new])[0].field_name == "新名称"


def test_match_prefers_field_code_then_label():
    definitions = [
        normalize_field({"fieldCode": "name", "fieldName": "名称"}, "api"),
        normalize_field({"fieldCode": "phone", "fieldName": "联系电话"}, "api"),
    ]
    dom = [
        DomField("name", "别的文本", "text", "[name=name]"),
        DomField("", "联系电话", "text", "#phone-input"),
    ]
    matched = match_dom_fields(definitions, dom)
    assert matched[0].dom.selector == "[name=name]"
    assert matched[1].dom.selector == "#phone-input"

