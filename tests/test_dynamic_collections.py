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
                "columnHeader": "名称",
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
    assert specs[0].children[0].column_header == "名称"
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


def test_project_decision_finance_sources_match_the_exact_component_contract():
    data_dir = Path(__file__).resolve().parents[1] / "data"

    specs = load_dynamic_collection_specs(
        data_dir,
        component="src/views/buildProject/before/projectDecision/index.vue",
    )

    assert len(specs) == 1
    spec = specs[0]
    assert spec.field_code == "financeSources"
    assert spec.section_title == "预算及资金来源明细子表"
    assert spec.create_on_outer_add is False
    assert spec.root_selector == ".form_content:has(.finance-table)"
    assert spec.create_selector == "button:has-text('新增')"
    assert spec.item_selector == (
        ".finance-table .el-table__body-wrapper .el-table__row:has(input)"
    )
    assert [child.field_code_template for child in spec.children] == [
        "financeSources.{index}.sourceFrom",
        "financeSources.{index}.amount",
        "financeSources.{index}.fundsPlan",
    ]


def test_resource_pool_collections_declare_all_persisted_numeric_and_currency_children():
    data_dir = Path(__file__).resolve().parents[1] / "data"

    specs = load_dynamic_collection_specs(
        data_dir, form_code="POOL_RESOURCE", component="projectResourcePool/index"
    )

    by_code = {spec.field_code: spec for spec in specs}
    assert set(by_code) == {"ownershipStructureList", "entInvestList"}
    assert [child.field_code_template for child in by_code["ownershipStructureList"].children] == [
        "ownershipStructureList.{index}.stockName",
        "ownershipStructureList.{index}.stockPercent",
        "ownershipStructureList.{index}.subscribedCapital",
        "ownershipStructureList.{index}.subscribedCapitalCcy",
        "ownershipStructureList.{index}.paidUpCapital",
        "ownershipStructureList.{index}.paidUpCapitalCcy",
    ]
    assert [child.kind for child in by_code["entInvestList"].children] == [
        "text", "number", "number", "select"
    ]
    assert by_code["ownershipStructureList"].section_title == "股权结构"
    assert by_code["ownershipStructureList"].create_on_outer_add is False
    assert by_code["ownershipStructureList"].root_selector == (
        '.enterprise-section:has(.enterprise-section__title:text-is("股权结构"))'
    )
    assert by_code["ownershipStructureList"].create_selector == (
        ".enterprise-section__toolbar button.el-button"
    )
    assert by_code["ownershipStructureList"].item_selector == (
        ".el-table__body-wrapper .el-table__row:has(input)"
    )
    assert by_code["entInvestList"].root_selector == (
        '.enterprise-section:has(.enterprise-section__title:text-is("对外投资"))'
    )
    assert by_code["entInvestList"].create_on_outer_add is False
    assert by_code["entInvestList"].create_selector == (
        ".enterprise-section__toolbar button.el-button"
    )
    assert [child.column_header for child in by_code["entInvestList"].children] == [
        "企业名称", "持股比例(%)", "投资额(万元)", "投资额(万元)"
    ]
    assert by_code["entInvestList"].children[0].max_length == 50
    assert [
        child.probe_value for child in by_code["entInvestList"].children
    ] == ["UI探测企业", 1, 1, None]
    assert [
        (
            relation.left_field_template,
            relation.operator,
            relation.right_field_template,
            relation.adjust_order,
        )
        for relation in by_code["ownershipStructureList"].value_relations
    ] == [(
        "ownershipStructureList.{index}.paidUpCapital",
        "lte",
        "ownershipStructureList.{index}.subscribedCapital",
        ("left",),
    )]
    assert by_code["entInvestList"].value_relations == ()
    assert all(
        "[prop" not in child.selector
        for spec in specs
        for child in spec.children
    )


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


@pytest.mark.parametrize("max_length", [0, -1, "invalid"])
def test_dynamic_collection_child_rejects_invalid_max_length(
    tmp_path, max_length,
):
    (tmp_path / "dynamic_collections.json").write_text(
        json.dumps({"collections": [{
            "formCode": "FORM_A",
            "fieldCode": "items",
            "mode": "add-row",
            "rootSelector": ".root",
            "createSelector": ".add",
            "itemSelector": ".row",
            "minRows": 1,
            "children": [{
                "fieldCodeTemplate": "items.{index}.name",
                "selector": "input",
                "maxLength": max_length,
            }],
        }]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="maxLength 必须是正整数"):
        load_dynamic_collection_specs(tmp_path, form_code="FORM_A")


def test_dynamic_collection_child_rejects_structured_probe_value(tmp_path):
    (tmp_path / "dynamic_collections.json").write_text(
        json.dumps({"collections": [{
            "formCode": "FORM_A",
            "fieldCode": "items",
            "mode": "add-row",
            "rootSelector": ".root",
            "createSelector": ".add",
            "itemSelector": ".row",
            "minRows": 1,
            "children": [{
                "fieldCodeTemplate": "items.{index}.name",
                "selector": "input",
                "probeValue": {"unsafe": "nested"},
            }],
        }]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="probeValue 必须是标量"):
        load_dynamic_collection_specs(tmp_path, form_code="FORM_A")


@pytest.mark.parametrize("relation", [
    {
        "leftField": "items.{index}.paid",
        "operator": "gt",
        "rightField": "items.{index}.subscribed",
        "adjustOrder": ["left"],
    },
    {
        "leftField": "items.{index}.missing",
        "operator": "lte",
        "rightField": "items.{index}.subscribed",
        "adjustOrder": ["left"],
    },
    {
        "leftField": "items.{index}.paid",
        "operator": "lte",
        "rightField": "items.{index}.subscribed",
        "adjustOrder": [],
    },
])
def test_dynamic_collection_value_relation_rejects_unsafe_contract(
    tmp_path, relation,
):
    (tmp_path / "dynamic_collections.json").write_text(
        json.dumps({"collections": [{
            "formCode": "FORM_A",
            "fieldCode": "items",
            "mode": "add-row",
            "rootSelector": ".root",
            "createSelector": ".add",
            "itemSelector": ".row",
            "minRows": 1,
            "children": [
                {
                    "fieldCodeTemplate": "items.{index}.subscribed",
                    "selector": "input",
                    "kind": "number",
                },
                {
                    "fieldCodeTemplate": "items.{index}.paid",
                    "selector": "input",
                    "kind": "number",
                },
            ],
            "valueRelations": [relation],
        }]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="集合值关系配置不完整"):
        load_dynamic_collection_specs(tmp_path, form_code="FORM_A")
