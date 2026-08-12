from ei_ui_smoke.dynamic_list import build_query_values, create_query_model, display_value, flatten_columns


def test_dynamic_query_only_submits_condition_code_and_value():
    queries = [{"conditionCode": "name", "operator": "LIKE", "enabled": 1, "defaultValue": "foo"}]
    assert build_query_values(queries, create_query_model(queries)) == [{"conditionCode": "name", "value": "foo"}]


def test_display_field_priority_matches_ei_parent():
    row = {"status": "1", "statusName": "有效", "dynamicFieldLabels": {"status": "标签"}}
    column = {"fieldCode": "status", "displayFieldCode": "statusName"}
    assert display_value(row, column) == "有效"


def test_stacked_columns_flatten_to_enabled_leaf_columns():
    columns = [{"children": [{"fieldCode": "a", "enabled": 1}, {"fieldCode": "b", "enabled": 0}]}]
    assert [item["fieldCode"] for item in flatten_columns(columns)] == ["a"]

