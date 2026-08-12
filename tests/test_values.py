from ei_ui_smoke.contracts import normalize_field
from ei_ui_smoke.values import ValueFactory


def test_business_override_wins_over_config_default_and_fallback():
    field = normalize_field({"fieldCode": "phone", "defaultValue": "config"}, "test")
    assert ValueFactory({"phone": "13800138000"}, "run").value_for(field) == "13800138000"


def test_config_default_wins_over_generated_fallback():
    field = normalize_field({"fieldCode": "name", "defaultValue": "配置值"}, "test")
    assert ValueFactory(run_id="run").value_for(field) == "配置值"


def test_text_fallback_is_deterministic_for_run():
    field = normalize_field({"fieldCode": "name", "fieldType": "ElInput-TEXT"}, "test")
    assert ValueFactory(run_id="run").value_for(field, 2) == "AUTO_run_2"

