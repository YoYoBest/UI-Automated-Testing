from ei_ui_smoke.common_delete_cases import load_common_delete_cases


def test_delete_cases_choose_confirm_cancel_or_safe_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ei_ui_smoke.common_delete_cases.read_xlsx_records",
        lambda _path, _sheet: [
            {"用例ID": "DELETE-001", "测试场景": "删除确认提示", "预期结果": "存在关联不能删除"},
            {"用例ID": "DELETE-002", "测试场景": "删除确认提示", "预期结果": "确认删除成功"},
            {"用例ID": "DELETE-003", "测试场景": "取消删除有效性", "预期结果": "删除不成功"},
        ],
    )

    cases = load_common_delete_cases(tmp_path / "rules.xlsx", "删除")

    assert [case.case_id for case in cases] == [
        "DELETE-001", "DELETE-002", "DELETE-003",
    ]
    assert [case.behavior for case in cases] == [
        "confirm_then_cancel", "confirm", "cancel",
    ]


def test_delete_cases_filter_to_selected_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "ei_ui_smoke.common_delete_cases.read_xlsx_records",
        lambda _path, _sheet: [
            {"用例ID": "DELETE-001", "测试场景": "删除确认提示", "预期结果": "确认删除"},
            {"用例ID": "DELETE-002", "测试场景": "取消删除有效性", "预期结果": "删除不成功"},
        ],
    )

    cases = load_common_delete_cases(
        tmp_path / "rules.xlsx", "删除", ["DELETE-002"]
    )

    assert [case.case_id for case in cases] == ["DELETE-002"]
