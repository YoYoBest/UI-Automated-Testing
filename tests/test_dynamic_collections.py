import json
from pathlib import Path

import pytest

from ei_ui_smoke.dynamic_collections import load_dynamic_collection_specs


def test_load_dynamic_collection_specs_matches_form_and_component(tmp_path):
    (tmp_path / "dynamic_collections.json").write_text(
        json.dumps({"collections": [{
            "formCode": "FORM_A",
            "component": "module/example/index.vue",
            "fieldCode": "items",
            "mode": "add-row",
            "rootSelector": "[field-code='items']",
            "createSelector": ".add-row",
            "itemSelector": ".item-row",
            "minRows": 1,
            "children": [{
                "fieldCodeTemplate": "items.{index}.name",
                "selector": "input",
            }],
        }]}),
        encoding="utf-8",
    )

    specs = load_dynamic_collection_specs(
        tmp_path, form_code="FORM_A", component="module\\example\\index"
    )

    assert len(specs) == 1
    assert specs[0].field_code == "items"
    assert specs[0].children[0].field_code_template == "items.{index}.name"
    assert load_dynamic_collection_specs(
        tmp_path, form_code="FORM_B", component="module/example/index"
    ) == []


def test_component_matching_ignores_vue_source_prefix_and_query(tmp_path):
    (tmp_path / "dynamic_collections.json").write_text(
        json.dumps({"collections": [{
            "formCode": "FORM_A",
            "component": "module/example/index",
            "fieldCode": "items",
            "mode": "selection",
            "rootSelector": ".collection-root",
            "createSelector": ".choose",
            "itemSelector": ".row",
            "minRows": 1,
            "children": [{
                "fieldCodeTemplate": "items.{index}.name",
                "selector": "input",
            }],
        }]}),
        encoding="utf-8",
    )

    specs = load_dynamic_collection_specs(
        tmp_path,
        form_code="FORM_A",
        component="@/src/views/module/example/index.vue?import",
    )

    assert len(specs) == 1
    assert specs[0].component == "module/example/index"


def test_project_adjustment_collection_uses_rendered_form_root():
    data_dir = Path(__file__).resolve().parents[1] / "data"

    specs = load_dynamic_collection_specs(
        data_dir,
        form_code="BUILD_PROJ_MAJOR_ADJUSTMENT",
        component="src/views/buildProject/before/projectChange/index.vue",
    )

    assert len(specs) == 1
    assert specs[0].field_code == "adjustmentItems"
    assert specs[0].root_selector == ".adjustment-type-form"


@pytest.mark.parametrize("entry", [
    {"formCode": "FORM_A", "fieldCode": "items"},
    {
        "formCode": "FORM_A", "fieldCode": "items", "mode": "add-row",
        "rootSelector": ".root", "createSelector": ".add", "itemSelector": ".row",
        "minRows": 1, "children": [{"fieldCodeTemplate": "items.name", "selector": "input"}],
    },
])
def test_load_dynamic_collection_specs_rejects_incomplete_matching_contract(tmp_path, entry):
    (tmp_path / "dynamic_collections.json").write_text(
        json.dumps({"collections": [entry]}), encoding="utf-8"
    )

    with pytest.raises(ValueError):
        load_dynamic_collection_specs(tmp_path, form_code="FORM_A")
