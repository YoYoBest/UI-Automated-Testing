import json
import zipfile
from pathlib import Path

import pytest

from ei_ui_smoke.common_field_cases import (
    BoundCommonCase,
    NotApplicableCommonReportItem,
    CommonFieldRule,
    count_common_field_report_items,
    DiscoveredCommonField,
    FieldConstraints,
    REQUIRED_ERRORS_RECOVER,
    REQUIRED_ERRORS_TRIGGER,
    bind_common_rules,
    build_common_case_coverage,
    classify_common_field_type,
    common_case_coverage_path,
    discover_common_fields,
    expand_common_case_report_items,
    load_common_field_rules,
    load_bound_common_cases,
    load_field_manifest,
    list_xlsx_case_ids,
    list_xlsx_sheets,
    case_selection_label,
    group_case_selections_by_sheet,
    plan_common_case_transactions,
    plan_common_field_report_items,
    resolve_rule_value,
    save_field_manifest,
)
from ei_ui_smoke.models import DomField, FieldDefinition


def test_classifies_semantic_number_fields_without_overriding_control_kind():
    assert classify_common_field_type("投资金额", "number") == "amount"
    assert classify_common_field_type("投资比例", "number") == "percentage"
    assert classify_common_field_type("预计回报", "number", "expectedReturnRate") == "percentage"
    assert classify_common_field_type("项目名称", "text") == "text"
    assert classify_common_field_type("项目基本情况", "textarea") == "textarea"
    assert classify_common_field_type("金额币种", "select") == "select"


def test_binds_choice_rules_only_to_matching_runtime_control_kinds():
    fields = discover_common_fields([
        DomField("type", "项目类型", "select", "#type"),
        DomField("decision", "是否决策", "radio", "#decision"),
    ])
    rules = [
        CommonFieldRule("SELECT", "select", "固定码值", None, "accepted"),
        CommonFieldRule("RADIO", "radio", "互斥选择", None, "accepted"),
        CommonFieldRule("TEXT", "text", "长度边界", "value", "accepted"),
    ]

    cases = bind_common_rules(fields, rules)

    assert [(case.field_key, case.case_id) for case in cases] == [
        ("type", "SELECT"),
        ("decision", "RADIO"),
    ]


def test_character_content_rules_bind_to_text_and_textarea_only():
    fields = discover_common_fields([
        DomField("name", "项目名称", "text", "#name"),
        DomField("description", "项目情况", "textarea", "#description"),
        DomField("amount", "投资金额", "number", "#amount"),
    ])
    rules = [
        CommonFieldRule(
            "ADD-011", "text", "中英文及常用标点", "中文ABC 123，。-()", "accepted"
        ),
        CommonFieldRule(
            "ADD-012", "text", "HTML/脚本字符", "<script>alert(1)</script>", "field_error"
        ),
    ]

    cases = bind_common_rules(fields, rules)

    assert [(case.field_key, case.case_id) for case in cases] == [
        ("name", "ADD-011"),
        ("name", "ADD-012"),
        ("description", "ADD-011"),
        ("description", "ADD-012"),
    ]


def test_edit_value_rule_binds_text_and_textarea_in_one_transaction():
    fields = discover_common_fields([
        DomField("projName", "项目名称", "text", "#name"),
        DomField("shortIntro", "项目基本情况", "textarea", "#intro"),
        DomField("projClassify", "项目类型", "select", "#type"),
    ])
    rule = CommonFieldRule(
        "EDIT-002",
        "text",
        "修改值正确性检查",
        "__REPLACE_LAST_WITH_9__",
        "accepted",
    )

    cases = bind_common_rules(fields, [rule])
    transactions = plan_common_case_transactions(cases)

    assert [(case.field_key, case.case_id) for case in cases] == [
        ("projName", "EDIT-002"),
        ("shortIntro", "EDIT-002"),
    ]
    assert len(transactions) == 1
    assert [case.field_key for case in transactions[0].cases] == [
        "projName", "shortIntro",
    ]


def test_report_item_plan_count_matches_pytest_parameterization(tmp_path):
    workbook = tmp_path / "common-cases.xlsx"
    manifest = tmp_path / "fresh-fields.json"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果", "优先级", "是否适用"],
            ["EDIT-002", "单行文本", "修改值正确性检查", "新值", "保存成功"],
            ["EDIT-003", "单行文本", "修改值正确性检查", "新值", "保存成功", "", "否"],
        ],
        sheet_names=["编辑"],
    )
    save_field_manifest(
        manifest,
        discover_common_fields([
            DomField("projName", "项目名称", "text", "#name"),
            DomField("shortIntro", "项目简介", "textarea", "#intro"),
        ]),
    )

    report_items = plan_common_field_report_items(
        workbook, manifest, sheet_name="编辑", case_ids=["EDIT-002"],
    )

    assert [item.pytest_id for item in report_items] == [
        "projName-EDIT-002", "shortIntro-EDIT-002",
    ]
    assert count_common_field_report_items(
        workbook, manifest, sheet_name="编辑", case_ids=["EDIT-002"],
    ) == len(report_items)
    assert count_common_field_report_items(
        workbook, manifest, sheet_name="编辑", case_ids=["EDIT-003"],
    ) == 1


def test_optional_clear_rule_binds_to_rendered_save_command():
    save = DiscoveredCommonField(
        "__save_command", "保存", "save_command", "button", "",
        FieldConstraints(),
    )
    rule = CommonFieldRule(
        "EDIT-005",
        "save_command",
        "非必填项值清空正确性检查",
        None,
        "accepted",
    )

    cases = bind_common_rules([save], [rule])

    assert [(case.case_id, case.field_key) for case in cases] == [
        ("EDIT-005", "__save_command")
    ]


def test_text_length_rules_do_not_expand_to_textarea():
    fields = discover_common_fields([
        DomField("name", "项目名称", "text", "#name", maxlength=100),
        DomField("description", "项目情况", "textarea", "#description", maxlength=2000),
    ])
    rules = [
        CommonFieldRule(
            "ADD-014", "text", "长度边界", "__MAX_LENGTH__", "accepted"
        ),
        CommonFieldRule(
            "ADD-020", "textarea", "长度边界", "__MAX_LENGTH__", "accepted"
        ),
    ]

    cases = bind_common_rules(fields, rules)

    assert [(case.field_key, case.case_id, len(case.input_value)) for case in cases] == [
        ("name", "ADD-014", 100),
        ("description", "ADD-020", 2000),
    ]


def test_year_picker_binds_year_rule_instead_of_select_rules():
    fields = discover_common_fields([
        DomField("assetYear", "年度", "year", "#asset-year", required=True),
    ])
    rules = [
        CommonFieldRule("ADD-053", "year", "选择年份", 2026, "accepted"),
        CommonFieldRule("ADD-059", "select", "固定码值", None, "accepted"),
        CommonFieldRule("ADD-060", "select", "只能选择一项", None, "accepted"),
    ]

    cases = bind_common_rules(fields, rules)

    assert fields[0].field_type == "year"
    assert [(case.field_key, case.case_id, case.input_value) for case in cases] == [
        ("assetYear", "ADD-053", 2026),
    ]


def test_discovers_constraints_from_dom_and_source_definition():
    dom = DomField(
        field_code="amount",
        label="投资金额",
        kind="number",
        selector="#amount",
        required=True,
        minimum="1",
        maximum="1000",
    )
    definition = FieldDefinition(
        field_code="amount",
        field_name="投资金额",
        field_type="ElInputNumber-NUMBER",
        props={"precision": 2},
        source="runtime-api",
    )

    field = discover_common_fields([dom], [definition])[0]

    assert field.field_type == "amount"
    assert field.constraints.required is True
    assert str(field.constraints.minimum) == "1"
    assert str(field.constraints.maximum) == "1000"
    assert field.constraints.precision == 2
    assert field.selector == "#amount"


def test_integer_only_rule_requires_proven_integer_constraint():
    fields = discover_common_fields(
        [
            DomField("unknownNumber", "未知数字", "number", "#unknown"),
            DomField("stepNumber", "步长数字", "number", "#step", step="1"),
            DomField("precisionNumber", "整数精度", "number", "#precision"),
        ],
        [
            FieldDefinition(
                field_code="precisionNumber",
                field_name="整数精度",
                field_type="ElInputNumber-NUMBER",
                props={"precision": 0},
            )
        ],
    )
    rule = CommonFieldRule(
        "ADD-025", "number", "小数输入整数框", 1.5, "field_error"
    )

    cases = bind_common_rules(fields, [rule])

    assert [(case.field_key, case.input_value) for case in cases] == [
        ("stepNumber", 1.5),
        ("precisionNumber", 1.5),
    ]


def test_percentage_boundaries_require_real_constraints():
    unbounded = discover_common_fields([
        DomField("expectedReturnRate", "预计回报率（%）", "number", "#rate")
    ])[0]
    bounded = discover_common_fields([
        DomField(
            "boundedRate", "投资比例", "number", "#bounded-rate",
            minimum="0", maximum="100",
        )
    ])[0]
    rules = [
        CommonFieldRule(
            "ADD-029", "percentage", "最小值", "__MIN_VALUE__", "accepted"
        ),
        CommonFieldRule(
            "ADD-030", "percentage", "超过最大值", "__ABOVE_MAX__", "field_error"
        ),
    ]

    assert unbounded.constraints.minimum is None
    assert unbounded.constraints.maximum is None
    assert unbounded.constraints.precision is None
    assert bind_common_rules([unbounded], rules) == []
    assert [(case.case_id, case.input_value) for case in bind_common_rules([bounded], rules)] == [
        ("ADD-029", 0),
        ("ADD-030", 101),
    ]


def test_source_text_definition_does_not_downgrade_runtime_numeric_semantics():
    fields = discover_common_fields(
        [
            DomField(
                field_code="buildPeriodMonth",
                label="Build period",
                kind="number",
                selector="#period",
                required=True,
            ),
            DomField(
                field_code="buildScale",
                label="Build scale",
                kind="text",
                selector="#scale",
                required=True,
            ),
        ],
        [
            FieldDefinition(
                field_code="buildPeriodMonth",
                field_name="Build period",
                field_type="ElInput-TEXT",
                source="source-code",
            ),
            FieldDefinition(
                field_code="buildScale",
                field_name="Build scale",
                field_type="ElInput-TEXT",
                source="source-code",
            ),
        ],
    )

    assert [(field.field_key, field.kind, field.field_type) for field in fields] == [
        ("buildPeriodMonth", "number", "number"),
        ("buildScale", "text", "text"),
    ]


def test_common_field_discovery_promotes_stable_code_numeric_text_inputs():
    field = discover_common_fields(
        [
            DomField(
                field_code="buildPeriodMonth",
                label="Build period",
                kind="text",
                selector="#period",
            ),
        ],
        [
            FieldDefinition(
                field_code="buildPeriodMonth",
                field_name="Build period",
                field_type="ElInput-TEXT",
                source="source-code",
            ),
        ],
    )[0]

    assert field.kind == "number"
    assert field.field_type == "number"


def test_binds_only_applicable_rules_and_resolves_dynamic_boundaries():
    dom = DomField(
        field_code="name",
        label="项目名称",
        kind="text",
        selector="#name",
        required=True,
        maxlength=3,
    )
    field = discover_common_fields([dom])[0]
    rules = [
        CommonFieldRule("TEXT-EMPTY", "text", "空值提交", "__EMPTY__", "field_error"),
        CommonFieldRule("TEXT-MAX", "text", "长度边界", "__MAX_LENGTH__", "accepted"),
        CommonFieldRule("TEXT-OVER", "text", "超过长度", "__MAX_LENGTH_PLUS_1__", "field_error"),
        CommonFieldRule("AMOUNT-NEG", "amount", "负数", -1, "field_error"),
    ]

    cases = bind_common_rules([field], rules)

    assert [case.pytest_id for case in cases] == [
        "name-TEXT-EMPTY", "name-TEXT-MAX", "name-TEXT-OVER"
    ]
    assert [case.input_value for case in cases] == ["", "测测测", "测测测测"]


def test_manifest_round_trip_preserves_decimal_constraints(tmp_path):
    dom = DomField(
        field_code="ratio",
        label="投资比例",
        kind="number",
        selector="#ratio",
        minimum="0",
        maximum="100",
    )
    field = discover_common_fields([dom])[0]
    path = tmp_path / "fields.json"

    save_field_manifest(path, [field])
    loaded = load_field_manifest(path)

    assert loaded == [field]
    assert json.loads(path.read_text(encoding="utf-8"))[0]["field_type"] == "percentage"


def test_manifest_round_trip_preserves_branch_conditions(tmp_path):
    field = DiscoveredCommonField(
        "approvalDate",
        "批复时间",
        "date",
        "date",
        "#approval-date",
        FieldConstraints(required=True),
        branch_conditions=(("progressType", "已取得批复"),),
    )
    path = tmp_path / "fields.json"

    save_field_manifest(path, [field])
    loaded = load_field_manifest(path)

    assert loaded == [field]
    assert json.loads(path.read_text(encoding="utf-8"))[0]["branch_conditions"] == [
        ["progressType", "已取得批复"]
    ]


def test_manifest_keeps_dialog_title_metadata_out_of_text_case_bindings(tmp_path):
    manifest = tmp_path / "fields.json"
    title = DiscoveredCommonField(
        "__dialog_title", "对话框名称", "dialog_title", "text", "",
        FieldConstraints(), source="dom-dialog-title",
    )
    save_field_manifest(manifest, [title])

    loaded = load_field_manifest(manifest)
    cases = bind_common_rules(
        loaded,
        [
            CommonFieldRule("ADD-001", "dialog_title", "对话框名称检查", None, "accepted"),
            CommonFieldRule("ADD-012", "text", "中英文及常用标点", "测试", "accepted"),
            CommonFieldRule("ADD-013", "text", "HTML/脚本字符", "<script>", "field_error"),
        ],
    )

    assert loaded == [title]
    assert [(case.case_id, case.field_key, case.field_type) for case in cases] == [
        ("ADD-001", "__dialog_title", "dialog_title")
    ]


def test_load_field_manifest_normalizes_stale_semantic_types(tmp_path):
    path = tmp_path / "fields.json"
    path.write_text(
        json.dumps(
            [
                {
                    "field_key": "buildPeriodMonth",
                    "label": "请输入建设周期",
                    "field_type": "text",
                    "kind": "text",
                    "selector": "#period",
                    "constraints": {},
                },
                {
                    "field_key": "expectedReturnRate",
                    "label": "请输入预计回报率",
                    "field_type": "number",
                    "kind": "number",
                    "selector": "#rate",
                    "constraints": {},
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    fields = load_field_manifest(path)

    assert [(field.field_key, field.kind, field.field_type) for field in fields] == [
        ("buildPeriodMonth", "number", "number"),
        ("expectedReturnRate", "number", "percentage"),
    ]


def test_loads_generic_rules_from_new_sheet_without_openpyxl(tmp_path):
    workbook = tmp_path / "cases.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果", "优先级", "是否适用"],
            ["ADD-001", "金额", "负数", "-1", "保存被阻止并提示金额不能小于0", "P1", "是"],
            ["ADD-002", "单行文本（50字）", "超过长度", "51个字符", "提示最多50个字符", "P0", "是"],
            ["ADD-003", "页面", "标题与布局", "", "显示正确", "P2", "是"],
            ["ADD-004", "百分比", "超过最大值", "100.01", "提示不能大于100", "P1", "否"],
            ["ADD-005", "金额", "最大值", "100", "保存成功", "P1", "待确认"],
            ["ADD-006", "数字", "整数输入", "123", "保存成功", "P1", ""],
        ],
    )

    rules = load_common_field_rules(workbook)

    assert [(rule.case_id, rule.field_type, rule.input_spec) for rule in rules] == [
        ("ADD-001", "amount", -1),
        ("ADD-002", "text", "__MAX_LENGTH_PLUS_1__"),
        ("ADD-006", "number", 123),
    ]


def test_loads_script_rule_as_safe_handling_contract(tmp_path):
    workbook = tmp_path / "safe-script.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            [
                "ADD-013",
                "文本",
                "HTML/脚本字符",
                "<script>alert(1)</script>",
                "页面不执行脚本；内容被安全转义或按规则拒绝",
            ],
        ],
    )

    rules = load_common_field_rules(workbook)

    assert len(rules) == 1
    assert rules[0].expected_type == "safe_handling"


def test_lists_xlsx_sheets_in_workbook_order(tmp_path):
    workbook = tmp_path / "sheets.xlsx"
    _write_minimal_xlsx(workbook, [["header"]], sheet_names=["说明", "新增", "编辑"])

    assert list_xlsx_sheets(workbook) == ["说明", "新增", "编辑"]


def test_lists_case_ids_with_their_sheet_and_filters_selected_rules(tmp_path):
    workbook = tmp_path / "cases.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["ADD-001", "金额", "负数", "-1", "保存被阻止"],
            ["ADD-002", "单行文本（50字）", "超过长度", "51个字符", "提示超长"],
        ],
    )

    assert list_xlsx_case_ids(workbook) == [
        ("新增", "ADD-001"), ("新增", "ADD-002"),
    ]
    assert [rule.case_id for rule in load_common_field_rules(
        workbook, case_ids=["ADD-002"]
    )] == ["ADD-002"]


def test_explicit_empty_or_unknown_case_id_selection_is_rejected(tmp_path):
    workbook = tmp_path / "cases.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["ADD-001", "金额", "负数", "-1", "保存被阻止"],
        ],
    )

    with pytest.raises(ValueError, match="未选择任何用例编号"):
        load_common_field_rules(workbook, case_ids=[])
    with pytest.raises(ValueError, match="Unknown case IDs"):
        load_common_field_rules(workbook, case_ids=["ADD-999"])


def test_pending_common_rules_are_reported_not_applicable(tmp_path):
    workbook = tmp_path / "cases.xlsx"
    manifest = tmp_path / "fields.json"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果", "是否适用"],
            ["ADD-001", "金额", "负数", "-1", "保存被阻止", "待确认"],
        ],
    )
    save_field_manifest(
        manifest,
        discover_common_fields([DomField("amount", "投资金额", "number", "#amount")]),
    )

    assert load_common_field_rules(workbook) == []
    coverage = build_common_case_coverage(workbook, manifest)

    assert coverage["counts"] == {
        "executed": 0,
        "not_applicable": 1,
        "unsupported": 0,
    }
    assert coverage["items"][0]["reason"] == "case applicability is 待确认"


def test_groups_selected_case_labels_by_sheet_in_workbook_order():
    references = [
        ("新增", "ADD-001"), ("新增", "ADD-002"), ("编辑", "EDIT-001"),
    ]

    assert group_case_selections_by_sheet(
        references, ["EDIT-001（编辑）", "ADD-002（新增）"]
    ) == [
        ("新增", ["ADD-002"]), ("编辑", ["EDIT-001"]),
    ]
    assert case_selection_label("新增", "ADD-001") == "ADD-001（新增）"
    with pytest.raises(ValueError, match="未找到用例编号选择项"):
        group_case_selections_by_sheet(references, ["ADD-999（新增）"])


def test_coverage_path_distinguishes_sheet_and_selected_case_ids(tmp_path):
    manifest = tmp_path / "POOL.json"

    first = common_case_coverage_path(manifest, "新增", ["ADD-001"])
    second = common_case_coverage_path(manifest, "编辑", ["EDIT-001"])
    third = common_case_coverage_path(manifest, "新增", ["ADD-002"])

    assert len({first.name, second.name, third.name}) == 3
    assert first.name.startswith("POOL_新增_")


def test_list_xlsx_sheets_rejects_invalid_workbook(tmp_path):
    workbook = tmp_path / "invalid.xlsx"
    workbook.write_text("not an xlsx archive", encoding="utf-8")

    with pytest.raises(ValueError, match="无法读取 Excel 工作簿"):
        list_xlsx_sheets(workbook)


def test_reads_absolute_ooxml_worksheet_relationship(tmp_path):
    workbook = tmp_path / "absolute-target.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["ADD-001", "百分比", "最大值", "100", "保存成功"],
        ],
        worksheet_target="/xl/worksheets/sheet1.xml",
    )

    rules = load_common_field_rules(workbook)

    assert len(rules) == 1
    assert rules[0].field_type == "percentage"


def test_text_length_profiles_only_bind_to_matching_real_field(tmp_path):
    workbook = tmp_path / "profiles.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["TEXT-50", "单行文本（50字）", "长度边界", "50个字符", "保存成功"],
            ["TEXT-100", "整行文本（100字）", "长度边界", "100个字符", "保存成功"],
        ],
    )
    field = discover_common_fields([
        DomField("name", "项目名称", "text", "#name", maxlength=100, layout_profile="full")
    ])[0]

    cases = bind_common_rules([field], load_common_field_rules(workbook))

    assert [case.case_id for case in cases] == ["TEXT-100"]
    assert len(cases[0].input_value) == 100


def test_duplicate_case_ids_with_distinct_length_profiles_are_supported(tmp_path):
    workbook = tmp_path / "profiles.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["ADD-027", "单行文本（50字）", "长度下边界", "49个字符", "保存成功"],
            ["ADD-027", "整行文本（100字）", "长度下边界", "99个字符", "保存成功"],
            ["ADD-027", "文本域（2000字）", "长度下边界", "1999个字符", "保存成功"],
        ],
    )
    fields = discover_common_fields([
        DomField("name", "名称", "text", "#name", maxlength=100, layout_profile="full"),
        DomField("description", "说明", "textarea", "#description", maxlength=2000),
    ])

    selected_rules = load_common_field_rules(workbook, case_ids=["ADD-027"])
    cases = bind_common_rules(fields, selected_rules)

    assert len(selected_rules) == 3
    assert [(case.field_key, case.case_id, len(case.input_value)) for case in cases] == [
        ("name", "ADD-027", 99),
        ("description", "ADD-027", 1999),
    ]
    assert [case.source_row for case in cases] == [3, 4]


def test_exact_duplicate_common_case_definition_is_rejected(tmp_path):
    workbook = tmp_path / "duplicates.xlsx"
    row = ["ADD-027", "单行文本（50字）", "长度下边界", "49个字符", "保存成功"]
    _write_minimal_xlsx(
        workbook,
        [["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"], row, row],
    )

    with pytest.raises(ValueError, match="Duplicate common case definition"):
        load_common_field_rules(workbook)


def test_full_row_text_rule_does_not_bind_half_row_field(tmp_path):
    workbook = tmp_path / "layout.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["FULL", "整行文本（100字）", "长度边界", "100个字符", "保存成功"],
            ["HALF", "半行文本框（100字）", "长度边界", "100个字符", "保存成功"],
        ],
    )
    field = discover_common_fields([
        DomField(
            "projName", "项目名称", "text", "#name", maxlength=100,
            layout_profile="half",
        )
    ])[0]

    cases = bind_common_rules([field], load_common_field_rules(workbook))

    assert [case.case_id for case in cases] == ["HALF"]


def test_lengthless_half_row_rules_use_runtime_maxlength(tmp_path):
    workbook = tmp_path / "layout-without-length.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["HALF-LOW", "半行文本框", "长度下边界", "49个字符", "保存成功"],
            ["HALF-MAX", "半行文本框", "长度边界", "50个字符", "保存成功"],
            ["HALF-OVER", "半行文本框", "超过长度", "51个字符", "保存被阻止"],
            ["FULL-MAX", "整行文本", "长度边界", "100个字符", "保存成功"],
        ],
    )
    field = discover_common_fields([
        DomField(
            "projName", "项目名称", "text", "#name", maxlength=100,
            layout_profile="half",
        )
    ])[0]

    rules = load_common_field_rules(workbook)
    cases = bind_common_rules([field], rules)

    assert all(rule.required_max_length is None for rule in rules)
    assert [case.case_id for case in cases] == ["HALF-LOW", "HALF-MAX", "HALF-OVER"]
    assert [len(case.input_value) for case in cases] == [99, 100, 101]


def test_required_rules_expand_to_real_required_fields(tmp_path):
    workbook = tmp_path / "required.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["REQ-EMPTY", "必填字段", "空值提交", "", "操作被阻止并提示"],
            ["REQ-SPACE", "必填字段", "仅输入空格", "空格", "操作被阻止并提示"],
        ],
    )
    fields = discover_common_fields([
        DomField("name", "名称", "text", "#name", required=True),
        DomField("type", "类型", "select", "#type", required=True),
        DomField("remark", "备注", "text", "#remark", required=False),
    ])

    cases = bind_common_rules(fields, load_common_field_rules(workbook))

    assert [(case.field_key, case.case_id, case.field_type) for case in cases] == [
        ("name", "REQ-EMPTY", "required"),
        ("name", "REQ-SPACE", "required"),
        ("type", "REQ-EMPTY", "required"),
    ]


def test_add_001_binds_required_file_and_plans_one_page_transaction(tmp_path):
    workbook = tmp_path / "required.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["ADD-001", "必填字段", "空值提交", "", "操作被阻止并提示"],
        ],
    )
    fields = discover_common_fields([
        DomField("name", "名称", "text", "#name", required=True),
        DomField(
            "requiredFile", "必填附件", "file",
            '[field-code="requiredFile"] input[type="file"]',
            required=True,
        ),
    ])

    cases = bind_common_rules(fields, load_common_field_rules(workbook))
    transactions = plan_common_case_transactions(cases)

    assert [case.field_key for case in cases] == ["name", "requiredFile"]
    assert len(transactions) == 1
    assert transactions[0].pytest_id == "ADD-001-all-required"
    assert transactions[0].cases == tuple(cases)


def test_required_workflow_uses_scenario_codes_after_case_ids_change(tmp_path):
    workbook = tmp_path / "required-workflow.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果", "场景编码"],
            [
                "RENAMED-TRIGGER", "必填字段", "全部必填项空值单次提交", "",
                "保存被阻止并显示字段级提示", REQUIRED_ERRORS_TRIGGER,
            ],
            [
                "RENAMED-RECOVER", "必填字段", "同一表单内逐项消除必填提示", "合法值",
                "当前提示消失，其他提示保留", REQUIRED_ERRORS_RECOVER,
            ],
        ],
    )
    fields = discover_common_fields([
        DomField("name", "名称", "text", "#name", required=True),
        DomField("type", "类型", "select", "#type", required=True),
    ])

    rules = load_common_field_rules(workbook)
    cases = bind_common_rules(fields, rules)
    transactions = plan_common_case_transactions(cases)

    assert [rule.scenario_code for rule in rules] == [
        REQUIRED_ERRORS_TRIGGER, REQUIRED_ERRORS_RECOVER,
    ]
    assert len(transactions) == 1
    assert [case.case_id for case in transactions[0].cases] == [
        "RENAMED-TRIGGER", "RENAMED-TRIGGER",
        "RENAMED-RECOVER", "RENAMED-RECOVER",
    ]
    assert [case.field_key for case in transactions[0].cases] == [
        "name", "type", "name", "type",
    ]


def test_branch_required_workflow_is_planned_per_branch(tmp_path):
    workbook = tmp_path / "required-branch.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果", "场景编码"],
            [
                "ADD-001", "必填字段", "全部必填项空值单次提交", "",
                "保存被阻止并显示字段级提示", REQUIRED_ERRORS_TRIGGER,
            ],
        ],
    )
    fields = [
        DiscoveredCommonField(
            "approvalDate", "批复时间", "date", "date", "#approval-date",
            FieldConstraints(required=True),
            branch_conditions=(("progressType", "已取得批复"),),
        ),
        DiscoveredCommonField(
            "paymentAmount", "累计付款金额", "amount", "number", "#payment",
            FieldConstraints(required=True),
            branch_conditions=(("progressType", "已付款"),),
        ),
    ]

    cases = bind_common_rules(fields, load_common_field_rules(workbook))
    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 2
    assert [
        transaction.cases[0].branch_conditions
        for transaction in transactions
    ] == [
        (("progressType", "已取得批复"),),
        (("progressType", "已付款"),),
    ]


def test_amount_digit_boundary_uses_max_digits_and_text_maxlength_intersection():
    field = discover_common_fields([
        DomField("amount", "投资金额", "number", "#amount", maxlength=20)
    ])[0]
    rules = [
        CommonFieldRule("AMT-MAX", "amount", "最大总长度边界", "__MAX_DIGITS__", "accepted"),
        CommonFieldRule("AMT-OVER", "amount", "超过总长度", "__MAX_DIGITS_PLUS_1__", "field_error"),
    ]

    cases = bind_common_rules([field], rules)

    assert cases[0].input_value == "9999999999999.999999"
    assert cases[1].input_value == "999999999999999.999999"
    assert len(str(cases[0].input_value).replace(".", "")) == 19
    assert len(str(cases[0].input_value)) == 20
    assert len(str(cases[1].input_value).replace(".", "")) == 21


def test_amount_digit_boundary_honors_field_specific_max_digits():
    constraints = FieldConstraints(precision=6, max_digits=19)

    boundary, boundary_supported = resolve_rule_value("__MAX_DIGITS__", constraints)
    over_limit, over_limit_supported = resolve_rule_value(
        "__MAX_DIGITS_PLUS_1__", constraints
    )

    assert boundary_supported and boundary == "9999999999999.999999"
    assert over_limit_supported and over_limit == "99999999999999.999999"


def test_load_bound_cases_combines_manifest_and_excel(tmp_path):
    workbook = tmp_path / "cases.xlsx"
    manifest = tmp_path / "fields.json"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["PCT-MAX", "百分比", "最大值", "100", "保存成功"],
        ],
    )
    fields = discover_common_fields([
        DomField("ratio", "投资比例", "number", "#ratio", maximum="100")
    ])
    save_field_manifest(manifest, fields)

    cases = load_bound_common_cases(workbook, manifest)

    assert len(cases) == 1
    assert cases[0].pytest_id == "ratio-PCT-MAX"


def test_plans_distinct_positive_text_fields_in_one_transaction():
    cases = [
        _bound_case("ADD-013", "projName", field_label="项目名称"),
        _bound_case("ADD-023", "isGmoDecision", field_type="textarea", field_label="项目基本情况"),
        _bound_case("ADD-023", "shortIntro", field_type="textarea", field_label="项目建设目的"),
        _bound_case("ADD-023", "buildTarget", field_type="textarea", field_label="潜在风险分析"),
    ]

    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 1
    assert transactions[0].transaction_id == "TX-001"
    assert transactions[0].pytest_id == "TX-001"
    assert transactions[0].cases == tuple(cases)
    assert len({case.field_key for case in transactions[0].cases}) == 4


def test_plans_compatible_attachment_fields_in_one_save_transaction():
    cases = [
        _bound_case(
            "EDIT-004", "approvalFile", field_type="file",
            field_label="批复文件", scenario="附件正确性检查",
        ),
        _bound_case(
            "EDIT-004", "dataFile", field_type="file",
            field_label="资料附件", scenario="附件正确性检查",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 1
    assert transactions[0].execution_mode == "attachment_persistence"
    assert transactions[0].cases == tuple(cases)
    assert len(expand_common_case_report_items(transactions)) == 2


def test_keeps_attachment_cases_in_different_branches_isolated():
    cases = [
        _bound_case(
            "EDIT-004", "approvalFile", field_type="file",
            branch_conditions=(("progressType", "已取得批复"),),
        ),
        _bound_case(
            "EDIT-004", "paymentFile", field_type="file",
            branch_conditions=(("progressType", "已付款"),),
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert [transaction.execution_mode for transaction in transactions] == [
        "attachment_persistence", "attachment_persistence",
    ]
    assert [[case.field_key for case in transaction.cases] for transaction in transactions] == [
        ["approvalFile"], ["paymentFile"],
    ]


def test_plans_mergeable_positive_cases_only_with_same_branch():
    cases = [
        _bound_case(
            "ADD-013",
            "approvalName",
            field_label="批复名称",
            branch_conditions=(("progressType", "已取得批复"),),
        ),
        _bound_case(
            "ADD-013",
            "paymentName",
            field_label="付款名称",
            branch_conditions=(("progressType", "已付款"),),
        ),
        _bound_case(
            "ADD-013",
            "approvalSummary",
            field_type="textarea",
            field_label="批复说明",
            branch_conditions=(("progressType", "已取得批复"),),
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert [
        [(case.field_key, case.branch_conditions) for case in transaction.cases]
        for transaction in transactions
    ] == [
        [
            ("approvalName", (("progressType", "已取得批复"),)),
            ("approvalSummary", (("progressType", "已取得批复"),)),
        ],
        [("paymentName", (("progressType", "已付款"),))],
    ]


def test_plans_text_and_textarea_length_boundary_in_one_scenario_transaction():
    cases = [
        _bound_case(
            "TEXT-BOUNDARY",
            "projName",
            field_type="text",
            scenario="长度边界",
            expected_value="输入和保存成功；内容完整",
        ),
        _bound_case(
            "TEXTAREA-BOUNDARY",
            "shortIntro",
            field_type="textarea",
            scenario="长度边界",
            expected_value="输入和保存成功；计数显示 2000/2000；内容完整",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 1
    assert transactions[0].cases == tuple(cases)
    assert transactions[0].execution_mode == "probe_persistence"


def test_plans_special_character_text_and_textarea_in_one_scenario_transaction():
    cases = [
        _bound_case(
            "TEXT-SCRIPT",
            "projName",
            field_type="text",
            scenario="HTML/脚本字符",
            expected_value="页面不执行脚本；内容被安全转义或按规则拒绝",
        ),
        _bound_case(
            "TEXTAREA-SCRIPT",
            "riskAnalysis",
            field_type="textarea",
            scenario="HTML / 脚本字符",
            expected_value="详情页不执行脚本；内容安全显示",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 1
    assert transactions[0].cases == tuple(cases)


def test_plans_unrelated_accepted_text_scenarios_in_separate_transactions():
    cases = [
        _bound_case(
            "TEXT-SCRIPT",
            "projName",
            field_type="text",
            scenario="HTML/脚本字符",
        ),
        _bound_case(
            "TEXTAREA-BOUNDARY",
            "shortIntro",
            field_type="textarea",
            scenario="长度边界",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert [[case.case_id for case in item.cases] for item in transactions] == [
        ["TEXT-SCRIPT"],
        ["TEXTAREA-BOUNDARY"],
    ]


def test_plans_reversible_text_and_number_boundaries_in_separate_probe_transactions():
    cases = [
        _bound_case(
            "TEXT-BOUNDARY",
            "projName",
            field_type="text",
            scenario="长度边界",
        ),
        _bound_case(
            "NUMBER-BOUNDARY",
            "amount",
            field_type="number",
            scenario="长度边界",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 2
    assert [transaction.cases for transaction in transactions] == [(cases[0],), (cases[1],)]
    assert all(transaction.execution_mode == "probe_persistence" for transaction in transactions)


def test_probe_transactions_isolate_numeric_families_and_branches():
    cases = [
        _bound_case(
            "AMOUNT-INTEGER", "amount", field_type="amount",
            scenario="合法整数",
        ),
        _bound_case(
            "NUMBER-INTEGER", "count", field_type="number",
            scenario="整数输入",
        ),
        _bound_case(
            "PERCENT-DECIMAL", "ratio", field_type="percentage",
            scenario="两位小数",
        ),
        _bound_case(
            "PERCENT-MAX", "approvalRatio", field_type="percentage",
            scenario="最大值",
            branch_conditions=(("progressType", "已取得批复"),),
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert [transaction.execution_mode for transaction in transactions] == [
        "probe_persistence", "probe_persistence", "probe_persistence", "probe_persistence",
    ]
    assert [transaction.cases for transaction in transactions] == [
        (cases[0],), (cases[1],), (cases[2],), (cases[3],),
    ]


@pytest.mark.parametrize(
    ("scenario", "expected_type"),
    [
        ("超过长度", "field_error"),
        ("超过最大值", "field_error"),
        ("HTML/脚本字符", "safe_handling"),
        ("前后空格", "accepted"),
    ],
)
def test_non_reversible_or_risky_cases_remain_persistence_transactions(
    scenario, expected_type,
):
    case = _bound_case(
        "ISOLATED", "name", scenario=scenario, expected_type=expected_type,
    )

    transaction = plan_common_case_transactions([case])[0]

    assert transaction.execution_mode == "persistence"


def test_merged_transaction_expands_to_one_report_item_per_bound_field():
    cases = [
        _bound_case("ADD-011", "projName", field_label="项目名称"),
        _bound_case(
            "ADD-023", "shortIntro", field_type="textarea",
            field_label="项目建设目的",
        ),
    ]
    transaction = plan_common_case_transactions(cases)[0]

    report_items = expand_common_case_report_items([transaction])

    assert [item.pytest_id for item in report_items] == [
        "projName-ADD-011", "shortIntro-ADD-023",
    ]
    assert [item.case for item in report_items] == cases
    assert all(item.transaction is transaction for item in report_items)


def test_required_page_batch_expands_to_one_report_item_per_bound_field():
    cases = [
        _bound_case(
            "ADD-001", "projName", field_type="required",
            scenario="全部必填项空值单次提交",
            expected_type="field_error",
            scenario_code=REQUIRED_ERRORS_TRIGGER,
        ),
        _bound_case(
            "ADD-001", "attachment", field_type="required",
            scenario="全部必填项空值单次提交",
            expected_type="field_error",
            scenario_code=REQUIRED_ERRORS_TRIGGER,
        ),
    ]
    transaction = plan_common_case_transactions(cases)[0]

    report_items = expand_common_case_report_items([transaction])

    assert [item.pytest_id for item in report_items] == [
        "projName-ADD-001", "attachment-ADD-001",
    ]
    assert [item.case_index for item in report_items] == [0, 1]


def test_plans_same_field_error_rule_for_distinct_fields_in_one_transaction():
    cases = [
        _bound_case(
            "ADD-OVER",
            "projName",
            scenario="超过最大长度",
            expected_type="field_error",
            expected_value="提示长度错误",
        ),
        _bound_case(
            "ADD-OVER",
            "shortIntro",
            field_type="textarea",
            scenario="超过最大长度",
            expected_type="field_error",
            expected_value="提示长度错误",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert len(transactions) == 1
    assert transactions[0].cases == tuple(cases)
    assert [case.field_key for case in transactions[0].cases] == [
        "projName",
        "shortIntro",
    ]


def test_repeated_cases_for_same_field_are_layered_across_transactions():
    cases = [
        _bound_case("NAME-A", "projName"),
        _bound_case("NAME-B", "projName"),
        _bound_case("DESC-A", "description", field_type="textarea"),
        _bound_case("DESC-B", "description", field_type="textarea"),
    ]

    transactions = plan_common_case_transactions(cases)

    assert [[case.case_id for case in item.cases] for item in transactions] == [
        ["NAME-A", "DESC-A"],
        ["NAME-B", "DESC-B"],
    ]
    assert all(
        len({case.field_key for case in transaction.cases})
        == len(transaction.cases)
        for transaction in transactions
    )
    assert {
        case.pytest_id
        for transaction in transactions
        for case in transaction.cases
    } == {case.pytest_id for case in cases}


def test_character_compatibility_cases_fill_all_text_fields_once_per_case():
    cases = []
    for field_key, field_type in (
        ("projName", "text"),
        ("shortIntro", "textarea"),
        ("buildTarget", "textarea"),
        ("riskAnalysis", "textarea"),
    ):
        cases.extend((
            _bound_case(
                "ADD-011",
                field_key,
                field_type=field_type,
                scenario="中英文及常用标点",
            ),
            _bound_case(
                "ADD-012",
                field_key,
                field_type=field_type,
                scenario="HTML/脚本字符",
                expected_value="页面不执行脚本；内容被安全转义或按规则拒绝",
            ),
        ))

    transactions = plan_common_case_transactions(cases)

    assert [
        [(case.case_id, case.field_key) for case in transaction.cases]
        for transaction in transactions
    ] == [
        [
            ("ADD-011", "projName"),
            ("ADD-011", "shortIntro"),
            ("ADD-011", "buildTarget"),
            ("ADD-011", "riskAnalysis"),
        ],
        [
            ("ADD-012", "projName"),
            ("ADD-012", "shortIntro"),
            ("ADD-012", "buildTarget"),
            ("ADD-012", "riskAnalysis"),
        ],
    ]


def test_distinct_accepted_scenarios_do_not_share_one_transaction():
    cases = [
        _bound_case(
            "ADD-012",
            "projName",
            scenario="HTML/脚本字符",
            expected_value="页面不执行脚本；内容被安全转义或按规则拒绝",
        ),
        _bound_case(
            "ADD-020",
            "shortIntro",
            field_type="textarea",
            scenario="长度边界",
            expected_value="输入和保存成功；计数显示 2000/2000；内容完整",
        ),
        _bound_case(
            "ADD-020",
            "buildTarget",
            field_type="textarea",
            scenario="长度边界",
            expected_value="输入和保存成功；计数显示 2000/2000；内容完整",
        ),
        _bound_case(
            "ADD-020",
            "riskAnalysis",
            field_type="textarea",
            scenario="长度边界",
            expected_value="输入和保存成功；计数显示 2000/2000；内容完整",
        ),
    ]

    transactions = plan_common_case_transactions(cases)

    assert [
        [(case.case_id, case.field_key) for case in transaction.cases]
        for transaction in transactions
    ] == [
        [("ADD-012", "projName")],
        [
            ("ADD-020", "shortIntro"),
            ("ADD-020", "buildTarget"),
            ("ADD-020", "riskAnalysis"),
        ],
    ]


def test_negative_choice_required_and_command_cases_remain_singletons():
    mergeable = [
        _bound_case("TEXT-A", "name"),
        _bound_case("TEXT-B", "description", field_type="textarea"),
    ]
    isolated = [
        _bound_case("NEGATIVE", "remark", expected_type="field_error"),
        _bound_case("REJECTED", "summary", expected_value="拒绝保存"),
        _bound_case("CHOICE", "type", field_type="select"),
        _bound_case("REQUIRED", "requiredName", field_type="required", expected_type="field_error"),
        _bound_case("COMMAND", "__save_command", field_type="save_command"),
    ]

    transactions = plan_common_case_transactions([mergeable[0], *isolated, mergeable[1]])

    assert transactions[0].cases == tuple(mergeable)
    singleton_ids = {
        transaction.cases[0].pytest_id
        for transaction in transactions[1:]
        if len(transaction.cases) == 1
    }
    assert singleton_ids == {case.pytest_id for case in isolated}
    assert all(len(transaction.cases) == 1 for transaction in transactions[1:])


def test_coverage_reports_executed_not_applicable_and_unsupported_cases(tmp_path):
    workbook = tmp_path / "cases.xlsx"
    manifest = tmp_path / "fields.json"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["TEXT", "文本", "中英文及常用标点", "abc", "输入成功"],
            ["EMAIL", "邮箱", "格式错误", "bad", "提示格式错误"],
            ["BUTTON", "保存按钮", "快速重复点击", "", "只保存一次"],
        ],
    )
    save_field_manifest(
        manifest,
        discover_common_fields([DomField("name", "名称", "text", "#name")]),
    )

    coverage = build_common_case_coverage(workbook, manifest)

    assert coverage["bound_instances"] == 1
    assert coverage["transaction_count"] == 1
    assert coverage["counts"] == {
        "executed": 1,
        "not_applicable": 2,
        "unsupported": 0,
    }
    assert coverage["items"][0]["bound_fields"] == ["name"]
    assert coverage["items"][0]["transaction_ids"] == ["TX-001"]

    selected = build_common_case_coverage(
        workbook, manifest, case_ids=["EMAIL"]
    )
    assert [item["case_id"] for item in selected["items"]] == ["EMAIL"]
    assert selected["items"][0]["source_row"] == 3
    assert selected["template_cases"] == 1

    report_items = plan_common_field_report_items(workbook, manifest)
    skipped = [
        item for item in report_items
        if isinstance(item, NotApplicableCommonReportItem)
    ]
    assert [(item.case_id, item.status) for item in skipped] == [
        ("EMAIL", "not_applicable"), ("BUTTON", "not_applicable"),
    ]


def test_edit_attachment_rule_binds_to_rendered_file_field(tmp_path):
    workbook = tmp_path / "edit-cases.xlsx"
    manifest = tmp_path / "fields.json"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["EDIT-004", "附件", "修改附件正确性检查", "重复上传附件", "附件重复显示"],
        ],
        sheet_names=["编辑"],
    )
    save_field_manifest(
        manifest,
        [
            DiscoveredCommonField(
                "file:会议纪要", "会议纪要", "file", "file", "#file",
                FieldConstraints(),
            )
        ],
    )

    coverage = build_common_case_coverage(
        workbook, manifest, sheet_name="编辑"
    )

    assert coverage["items"][0]["status"] == "executed"
    assert coverage["items"][0]["bound_fields"] == ["file:会议纪要"]


def test_button_rules_bind_only_to_discovered_form_commands(tmp_path):
    workbook = tmp_path / "commands.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["SAVE", "保存按钮", "快速重复点击", "", "只保存一次"],
            ["SAVE-CANCEL", "保存按钮", "二次确认取消", "", "确认框关闭且不保存"],
            ["SUBMIT", "提交按钮", "提交成功", "", "提交成功"],
            ["CANCEL", "取消按钮", "取消新增", "", "关闭新增页"],
            ["CLOSE", "关闭图标 X", "关闭新增", "", "关闭新增页且不保存"],
        ],
    )
    commands = [
        DiscoveredCommonField(
            "__save_command", "保存", "save_command", "button", "",
            FieldConstraints(),
        ),
        DiscoveredCommonField(
            "__cancel_command", "取消", "cancel_command", "button", "",
            FieldConstraints(),
        ),
        DiscoveredCommonField(
            "__close_command", "关闭图标 X", "close_command", "button", "",
            FieldConstraints(),
        ),
    ]

    cases = bind_common_rules(commands, load_common_field_rules(workbook))

    assert [(case.case_id, case.field_type) for case in cases] == [
        ("SAVE", "save_command"),
        ("CANCEL", "cancel_command"),
        ("CLOSE", "close_command"),
    ]


def test_secondary_confirmation_commands_require_confirm_capability(tmp_path):
    workbook = tmp_path / "commands.xlsx"
    _write_minimal_xlsx(
        workbook,
        [
            ["用例ID", "字段/控件", "测试场景", "测试数据", "预期结果"],
            ["SAVE-CANCEL", "保存按钮", "二次确认取消", "", "确认框关闭且不保存"],
            ["SAVE-OK", "保存按钮", "二次确认保存", "", "确认后保存成功"],
        ],
    )
    plain_save = DiscoveredCommonField(
        "__save_command", "保存", "save_command", "button", "",
        FieldConstraints(), source="dom-command",
    )
    confirm_save = DiscoveredCommonField(
        "__save_command", "保存", "save_command", "button-confirmation", "",
        FieldConstraints(), source="dom-command-confirmation",
    )

    assert bind_common_rules([plain_save], load_common_field_rules(workbook)) == []

    cases = bind_common_rules([confirm_save], load_common_field_rules(workbook))
    assert [(case.case_id, case.field_type) for case in cases] == [
        ("SAVE-CANCEL", "save_command"),
        ("SAVE-OK", "save_command"),
    ]


def _bound_case(
    case_id: str,
    field_key: str,
    *,
    field_type: str = "text",
    field_label: str | None = None,
    scenario: str = "合法输入",
    expected_type: str = "accepted",
    expected_value: str = "保存成功",
    scenario_code: str = "",
    branch_conditions: tuple[tuple[str, str], ...] = (),
) -> BoundCommonCase:
    return BoundCommonCase(
        case_id=case_id,
        field_key=field_key,
        field_label=field_label or field_key,
        field_type=field_type,
        selector=f"#{field_key}",
        scenario=scenario,
        input_value=f"value-{case_id}",
        expected_type=expected_type,
        expected_value=expected_value,
        priority="P1",
        scenario_code=scenario_code,
        branch_conditions=branch_conditions,
    )


def _write_minimal_xlsx(
    path: Path,
    rows: list[list[str]],
    *,
    worksheet_target: str = "worksheets/sheet1.xml",
    sheet_names: list[str] | None = None,
) -> None:
    cells = []
    for row_index, row in enumerate(rows, start=1):
        encoded = []
        for column_index, value in enumerate(row, start=1):
            reference = f"{_column_name(column_index)}{row_index}"
            encoded.append(
                f'<c r="{reference}" t="inlineStr"><is><t>{_xml(value)}</t></is></c>'
            )
        cells.append(f'<row r="{row_index}">{"".join(encoded)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
    )
    names = sheet_names or ["新增"]
    sheet_nodes = "".join(
        f'<sheet name="{_xml(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, name in enumerate(names, 1)
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheets>{sheet_nodes}</sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="{worksheet_target}"/></Relationships>',
        )
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def _column_name(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _xml(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
