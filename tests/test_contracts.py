import json

from ei_ui_smoke.contracts import build_runtime_data, field_kind, normalize_field, remove_runtime_fields


def fields():
    return [
        normalize_field({"fieldCode": "fixed", "fixedType": 1}, "test"),
        normalize_field({"fieldCode": "custom", "fixedType": 0, "viewVisible": 1}, "test"),
        normalize_field({"fieldCode": "semi", "fixedType": 2, "viewVisible": 1,
                         "propsJson": '{"extraBindings":{"label":"semiName"}}'}, "test"),
        normalize_field({"fieldCode": "hidden", "fixedType": 0, "viewVisible": 0}, "test"),
    ]


def test_field_type_aliases_cover_new_and_legacy_types():
    assert field_kind("TEXT") == "text"
    assert field_kind("ElInput-TEXT") == "text"
    assert field_kind("PurvarDepartment-dept") == "org_select"
    assert field_kind("PurvarLibrary-FILE_LIBRARY") == "file_library"
    assert field_kind("PurvarAiParsePanel-AI_PARSE") == "ai_parse"
    assert field_kind("USER_SELECT_TABLE") == "user_select"


def test_runtime_payload_only_contains_visible_fixed_type_zero_and_two():
    result = build_runtime_data({
        "fixed": "F", "custom": ["A", "B"], "semi": {"id": 1}, "hidden": "H",
        "dynamicFieldLabels": {"custom": [{"name": "甲", "value": "A"}], "fixed": [1]},
    }, fields())
    assert result["custom"] == "A,B"
    assert result["semi"] == '{"id":1}'
    assert "fixed" not in result and "hidden" not in result
    assert json.loads(result["dynamicFieldLabels"]) == {"custom": [{"name": "甲", "value": "A"}]}


def test_business_payload_removes_runtime_fields_labels_and_extra_bindings():
    result = remove_runtime_fields({
        "fixed": "F", "custom": "C", "semi": "S", "semiName": "name",
        "dynamicFieldLabels": {},
    }, fields())
    assert result == {"fixed": "F"}
