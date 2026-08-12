from ei_ui_smoke.data_strategy import StandardDataStrategy
from ei_ui_smoke.models import FieldDefinition
from ei_ui_smoke.validation_repair import generate_repair_value, parse_validation_message


def field(code="value", name="字段", field_type="ElInput-TEXT", props=None):
    return FieldDefinition(code, name, field_type, props=props or {})


def test_parses_email_and_generates_valid_value():
    definition = field("email", "联系邮箱", props={"maxlength": 40})
    constraint = parse_validation_message("请输入正确的邮箱", definition)

    value = generate_repair_value(constraint, definition, "bad", 1, "20260731")

    assert constraint.kind == "email"
    assert "@" in value and len(value) <= 40


def test_parses_mobile_length_and_generates_eleven_digits():
    definition = field("phoneNo", "联系电话")
    constraint = parse_validation_message("请输入正确的11位手机号", definition)

    value = generate_repair_value(constraint, definition, "AUTO", 1, "20260731")

    assert value.startswith("139")
    assert len(value) == 11 and value.isdigit()


def test_parses_max_length_and_truncates_value():
    definition = field("name", "名称")
    constraint = parse_validation_message("长度不能超过5个字符", definition)

    assert generate_repair_value(constraint, definition, "123456789", 1, "run") == "12345"


def test_parses_minimum_number():
    definition = field("amount", "金额", "ElInputNumber-NUMBER")
    constraint = parse_validation_message("金额必须大于0", definition)

    assert generate_repair_value(constraint, definition, -1, 1, "run") > 0


def test_duplicate_value_gets_unique_suffix():
    definition = field("name", "名称")
    constraint = parse_validation_message("该名称已存在", definition)

    assert generate_repair_value(constraint, definition, "测试名称", 2, "run1") == "测试名称_run1_2"


def test_duplicate_year_stays_a_valid_four_digit_year():
    definition = field("assetYear", "年度", "DATE")
    constraint = parse_validation_message("该年度已存在", definition)

    assert generate_repair_value(
        constraint, definition, "2026", 2, "run1"
    ) == "2028"


def test_duplicate_date_offsets_days_instead_of_appending_text():
    definition = field("reportDate", "报告日期", "DATE")
    constraint = parse_validation_message("该日期已存在", definition)

    assert generate_repair_value(
        constraint, definition, "2026-08-11", 2, "run1"
    ) == "2026-08-13"


def test_duplicate_number_uses_declared_step():
    definition = field(
        "serial", "序号", "ElInputNumber-NUMBER", props={"step": "2"}
    )
    constraint = parse_validation_message("序号已存在", definition)

    assert generate_repair_value(constraint, definition, 10, 3, "run1") == 16


def test_permission_error_is_not_treated_as_value_constraint():
    assert parse_validation_message("无操作权限", field()) is None


def test_generates_lowercase_slug_from_backend_hint():
    definition = field("categoryCode", "分类编码", props={"maxlength": 30})
    constraint = parse_validation_message("分类编码只能包含小写字母、数字、下划线", definition)

    value = generate_repair_value(constraint, definition, "错误编码", 2, "Run 01")

    assert value == "ui_run_01_2"


def test_classifies_positive_integer_without_matching_full_message():
    definition = field("period", "建设周期（月）")
    constraint = parse_validation_message("该字段只能输入正整数!", definition)
    value = generate_repair_value(constraint, definition, "AUTO", 1, "run")

    assert constraint.kind == "number"
    assert constraint.integer is True
    assert constraint.minimum == 1
    assert value == 1


def test_classifies_generic_numeric_hint_for_text_component():
    definition = field("returnRate", "预计回报率（%）")
    constraint = parse_validation_message("只能输入数字", definition)

    assert constraint.kind == "number"
    assert generate_repair_value(constraint, definition, "AUTO", 1, "run") == 1


def test_uses_structured_numeric_props_with_generic_format_error():
    definition = field(
        "quantity", "数量", "ElInput-TEXT",
        props={"inputmode": "numeric", "min": "2", "max": "9", "step": "1"},
    )
    constraint = parse_validation_message("格式不符合要求", definition)

    assert constraint.to_dict() == {
        "kind": "number", "minimum": 2.0, "maximum": 9.0, "integer": True,
    }
    assert generate_repair_value(constraint, definition, "bad", 1, "run") == 2


def test_duplicate_remains_unique_constraint_for_numeric_field():
    definition = field("serial", "序号", "ElInputNumber-NUMBER")

    assert parse_validation_message("序号已存在", definition).kind == "unique"


def test_generates_value_for_supported_dom_pattern():
    definition = field(
        "code", "编码", props={"pattern": r"[A-Z][A-Za-z0-9]+", "maxlength": 20}
    )
    constraint = parse_validation_message("格式不符合要求", definition)
    value = generate_repair_value(constraint, definition, "bad", 1, "20260805")

    assert constraint.kind == "pattern"
    assert value == "A202608051"


def test_business_state_error_is_not_structurally_repaired():
    definition = field("period", "建设周期（月）", "ElInputNumber-NUMBER")

    assert parse_validation_message("当前状态不允许新增", definition) is None


def test_conflicting_structured_bounds_are_not_repaired():
    strategy = StandardDataStrategy.__new__(StandardDataStrategy)
    definition = field(
        "quantity", "数量", "ElInputNumber-NUMBER", props={"min": 10, "max": 2}
    )

    assert strategy.repair_value(definition, "bad", "只能输入数字", 1) is None
