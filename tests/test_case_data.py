import json

from ei_ui_smoke.case_data import DEFAULT_SAVE_BUTTON, load_smoke_case


def test_central_page_and_override_files_replace_per_page_data(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "pages.json").write_text(json.dumps({"forms": {
        "FORM_A": {"formUrl": "https://example/add", "saveApiPattern": "*/save"}
    }}), encoding="utf-8")
    (data_dir / "overrides.json").write_text(json.dumps({"forms": {
        "FORM_A": {"values": {"phone": "13800138000"}}
    }}), encoding="utf-8")
    case = load_smoke_case(tmp_path, "FORM_A")
    assert case.values == {"phone": "13800138000"}
    assert case.page.save_api_pattern == "*/save"
    assert case.runtime_expected == {}


def test_central_override_contains_expected_and_runtime_values(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "pages.json").write_text(json.dumps({"forms": {
        "FORM_A": {"formUrl": "https://example/add"}
    }}), encoding="utf-8")
    (data_dir / "overrides.json").write_text(json.dumps({"forms": {
        "FORM_A": {
            "values": {"phone": "13900000000"},
            "expected": {"phone": "13900000000"},
            "runtimeExpected": {"customStatus": "1"}
        }
    }}), encoding="utf-8")
    case = load_smoke_case(tmp_path, "FORM_A")
    assert case.expected["phone"] == "13900000000"
    assert case.runtime_expected["customStatus"] == "1"


def test_default_form_smoke_save_selector_accepts_temporary_storage():
    assert "button:has-text('暂存')" in DEFAULT_SAVE_BUTTON
