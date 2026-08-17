import json
import re
from pathlib import Path

import pytest

from ei_ui_smoke.contracts import normalize_field
from ei_ui_smoke.data_pool import (
    ConstrainedGenerator,
    GlobalDataPool,
    UniqueConstraintSpec,
)
from ei_ui_smoke.data_strategy import (
    ProbeDataStrategy,
    StableDataStrategy,
    StandardDataStrategy,
    create_data_strategy,
)


def pool(unique_constraints=()):
    return GlobalDataPool(
        common={
            "generators": {
                "mobile": {"prefixes": ["139"]},
                "creditCode": {"registrationAuthority": "91", "organizationCodePrefix": "320500"},
                "amount": {"min": 10, "max": 20, "scale": 2},
                "enterpriseName": {"prefix": "测试企业"},
                "businessIdentifier": {"digits": 16},
                "text": {"prefix": "测试"},
            },
            "candidatePools": {"currency": ["人民币"]},
            "fieldMappings": {
                "mobile": ["phone", "手机号"],
                "creditCode": ["creditCode", "统一社会信用代码"],
                "amount": ["amount", "金额"],
                "enterpriseName": ["companyName", "企业名称"],
                "businessIdentifier": ["itemId", "ID"],
            },
        },
        collected={"forms": {"FORM": {"fields": {"status": {"values": ["有效"]}}}}},
        overrides={"forms": {"FORM": {"values": {"amount": 88}}}},
        unique_constraints=tuple(unique_constraints),
    )


def test_unique_key_reservation_is_atomic_alias_aware_and_scope_isolated():
    constraint = UniqueConstraintSpec(
        form_code="BUILD_NETASSETS_MAINTAIL",
        field_codes=("belongSection", "assetYear"),
        repair_field="assetYear",
        message_includes=("已存在",),
    )
    first = StandardDataStrategy(
        pool([constraint]), "run-a", form_code=constraint.form_code
    )
    second = StandardDataStrategy(
        pool([constraint]), "run-b", form_code=constraint.form_code
    )
    key_by_code_and_name = (
        frozenset({"1", "四川板块"}), frozenset({"2030"}),
    )
    key_by_name = (frozenset({"四川板块"}), frozenset({"2030"}))

    assert first.reserve_unique_key_if_available(
        constraint, key_by_code_and_name, page_scope="https://one/list"
    )
    assert not second.reserve_unique_key_if_available(
        constraint, key_by_name, page_scope="https://one/list"
    )
    assert second.reserve_unique_key_if_available(
        constraint, key_by_name, page_scope="https://two/list"
    )
    first.release_unique_key(
        constraint, key_by_code_and_name, page_scope="https://one/list"
    )
    assert second.reserve_unique_key_if_available(
        constraint, key_by_name, page_scope="https://one/list"
    )


def field(code, name="", field_type="ElInput-TEXT"):
    return normalize_field({"fieldCode": code, "fieldName": name, "fieldType": field_type}, "test")


def test_probe_generates_valid_semantic_values_without_page_data():
    strategy = ProbeDataStrategy(pool(), "run-1")
    mobile = strategy.value_for(field("contactPhone", "联系电话"), 1)
    credit = strategy.value_for(field("creditCode", "统一社会信用代码"), 2)
    amount = strategy.value_for(field("amount", "金额", "ElInputNumber-NUMBER"), 3)
    assert re.fullmatch(r"139\d{8}", mobile)
    assert len(credit) == 18 and all(char in ConstrainedGenerator.USCC_CHARS for char in credit)
    assert 10 <= amount <= 20


def test_probe_is_reproducible_with_same_run_id():
    first = ProbeDataStrategy(pool(), "same").value_for(field("contactPhone"), 1)
    second = ProbeDataStrategy(pool(), "same").value_for(field("contactPhone"), 1)
    assert first == second


def test_visible_business_item_id_uses_numeric_identifier():
    strategy = ProbeDataStrategy(pool(), "20260731153000")

    value = strategy.value_for(field("itemId", "ID"), 1)

    assert value.isdigit()
    assert len(value) == 16
    assert pool().semantic_for(field("orgId", "关联组织")) == ""


def test_short_rate_alias_does_not_match_registration_status():
    strategy = ProbeDataStrategy(pool(), "same")

    value = strategy.value_for(
        field("registrationStatus", "管理人登记情况", "PurvarCodeSelect-RADIO"), 1
    )

    assert value == ""


def test_stable_priority_is_override_then_collected_then_common_or_generated():
    strategy = StableDataStrategy(pool(), "FORM", "stable")
    assert strategy.value_for(field("amount", "金额"), 1) == 88
    assert strategy.value_for(field("status", "状态", "PurvarCodeSelect-SELECT"), 2) == "有效"
    assert strategy.value_for(field("companyName", "企业名称"), 3).startswith("测试企业_")


def test_choice_baseline_matches_exact_field_code_then_label_alias():
    data_pool = GlobalDataPool(
        common={},
        collected={},
        overrides={"forms": {"FORM": {"choiceValues": {
            "isRegister": {
                "value": "未注册",
                "labels": ["注册状态"],
            },
        }}}},
    )
    strategy = StandardDataStrategy(data_pool, "run", form_code="FORM")

    assert strategy.preferred_choice_for(
        field("isRegister", "其他标签", "ElRadioGroup-RADIO")
    ) == "未注册"
    assert strategy.preferred_choice_for(
        field("el-id-1", "注册状态", "ElRadioGroup-RADIO")
    ) == "未注册"
    assert StableDataStrategy(
        data_pool, "FORM", "run"
    ).preferred_choice_for(
        field("isRegister", "注册状态", "ElRadioGroup-RADIO")
    ) == "未注册"
    assert ProbeDataStrategy(
        data_pool, "run", form_code="FORM"
    ).preferred_choice_for(
        field("isRegister", "注册状态", "ElRadioGroup-RADIO")
    ) is None
    assert StandardDataStrategy(
        data_pool, "run", form_code="OTHER"
    ).preferred_choice_for(
        field("isRegister", "注册状态", "ElRadioGroup-RADIO")
    ) is None


def test_choice_baseline_rejects_ambiguous_label_alias():
    data_pool = GlobalDataPool(
        common={},
        collected={},
        overrides={"forms": {"FORM": {"choiceValues": {
            "first": {"value": "A", "labels": ["状态"]},
            "second": {"value": "B", "labels": ["状态"]},
        }}}},
    )

    with pytest.raises(ValueError, match="选择基线标签不唯一"):
        data_pool.preferred_choice("FORM", "el-id-1", "状态")


def test_resource_pool_standard_mode_declares_unregistered_legal_branch():
    project_root = Path(__file__).resolve().parents[1]
    data_pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = StandardDataStrategy(
        data_pool, "run", form_code="POOL_RESOURCE"
    )

    assert strategy.preferred_choice_for(
        field("inveProjType", "投资类型", "ElRadioGroup-RADIO")
    ) == "POOL_RESOURCE"
    assert strategy.preferred_choice_for(
        field("isRegion", "运营主体所在地", "ElRadioGroup-RADIO")
    ) == "0"
    assert strategy.preferred_choice_for(
        field("isRegister", "注册状态", "ElRadioGroup-RADIO")
    ) == "2"


def test_resource_pool_standard_mode_uses_scoped_legal_capital_baseline():
    project_root = Path(__file__).resolve().parents[1]
    data_pool = GlobalDataPool.from_directory(project_root / "data")
    strategy = StandardDataStrategy(
        data_pool, "run", form_code="POOL_RESOURCE"
    )

    registered = strategy.value_for(
        field("registAmount", "注册资本", "ElInputNumber-NUMBER"), 1
    )
    received = strategy.value_for(
        field("recCapAmount", "实收资本", "ElInputNumber-NUMBER"), 2
    )

    assert (registered, received) == (1000, 500)
    assert received <= registered
    assert StandardDataStrategy(
        data_pool, "run", form_code="OTHER"
    ).value_for(
        field("registAmount", "注册资本", "ElInputNumber-NUMBER"), 1
    ) != 1000
    assert ProbeDataStrategy(
        data_pool, "run", form_code="POOL_RESOURCE"
    ).value_for(
        field("registAmount", "注册资本", "ElInputNumber-NUMBER"), 1
    ) != 1000


def test_resource_pool_probe_keeps_the_general_data_strategy():
    strategy = create_data_strategy("probe", pool(), "POOL_RESOURCE")

    assert isinstance(strategy, ProbeDataStrategy)


def test_resource_pool_duplicate_message_declares_proj_object_name_repair():
    project_root = Path(__file__).resolve().parents[1]
    strategy = create_data_strategy(
        "probe",
        GlobalDataPool.from_directory(project_root / "data"),
        "POOL_RESOURCE",
        run_id="run",
    )
    submitted = {"projObjectName": "测试企业"}

    constraint = strategy.declared_unique_constraint_for_message(
        "资源池已存在同名企业请勿重复录入", submitted,
    )

    assert constraint is not None
    assert constraint.field_codes == ("projObjectName",)
    assert constraint.repair_field == "projObjectName"
    assert constraint.list_url_includes == ("/projStorage/list",)
    assert constraint.record_paths == ("data",)
    assert constraint.aliases_for("projObjectName") == (
        "projObjectName", "name", "enterpriseOrProjectName",
    )
    assert strategy.unique_repair_field(
        "资源池已存在同名企业请勿重复录入", submitted,
    ) == "projObjectName"


def test_mode_switch_rejects_unknown_mode():
    assert isinstance(create_data_strategy("probe", pool(), "FORM"), ProbeDataStrategy)
    assert isinstance(create_data_strategy("stable", pool(), "FORM"), StableDataStrategy)
    assert isinstance(create_data_strategy("standard", pool(), "FORM"), StandardDataStrategy)
    assert create_data_strategy("standard", pool(), "FORM").strict_field_validation
    try:
        create_data_strategy("random", pool(), "FORM")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown mode must fail")


def test_strategy_uses_one_launcher_run_id_with_distinct_target_sequences(monkeypatch):
    monkeypatch.setenv("EI_AUTOMATION_RUN_ID", "20260812230000123456_ab12cd34")
    monkeypatch.setenv("EI_AUTOMATION_TARGET_SEQUENCE", "7")
    first = create_data_strategy("standard", pool(), "FORM")
    monkeypatch.setenv("EI_AUTOMATION_TARGET_SEQUENCE", "8")
    second = create_data_strategy("standard", pool(), "FORM")

    assert first.generator.run_id == "20260812230000123456_ab12cd34_7"
    assert second.generator.run_id == "20260812230000123456_ab12cd34_8"
    assert first.value_for(field("name", "名称"), 1) != second.value_for(
        field("name", "名称"), 1
    )


def test_strategy_scopes_generated_values_per_independent_action(monkeypatch):
    monkeypatch.setenv("EI_AUTOMATION_RUN_ID", "20260812230000123456_ab12cd34")
    monkeypatch.setenv("EI_AUTOMATION_TARGET_SEQUENCE", "7")
    monkeypatch.setenv("EI_AUTOMATION_ACTION_SCOPE", "outer-add")
    first = create_data_strategy("standard", pool(), "FORM")
    monkeypatch.setenv("EI_AUTOMATION_ACTION_SCOPE", "nested-ownership-add")
    second = create_data_strategy("standard", pool(), "FORM")

    assert first.generator.run_id.endswith("_7_outer-add")
    assert second.generator.run_id.endswith("_7_nested-ownership-add")
    assert first.value_for(field("name"), 1) != second.value_for(field("name"), 1)


def test_strategy_repairs_value_only_for_supported_validation_message():
    strategy = ProbeDataStrategy(pool(), "run-1")
    definition = field("email", "联系邮箱")

    repaired = strategy.repair_value(definition, "bad", "邮箱格式不正确", 1)

    assert repaired is not None
    assert "@" in repaired[0]
    assert strategy.repair_value(definition, "bad", "无操作权限", 1) is None


def test_global_default_upload_file_is_read_from_common_data(tmp_path):
    attachment = tmp_path / "attachment.jpg"
    attachment.write_bytes(b"image")
    data_pool = GlobalDataPool(
        common={"uploads": {"defaultFile": str(attachment)}},
        collected={},
        overrides={},
    )

    assert data_pool.default_upload_file() == Path(attachment).resolve()


def test_declared_composite_unique_constraint_uses_batch_year_sequence():
    constraint = UniqueConstraintSpec(
        form_code="BUILD_NETASSETS_MAINTAIL",
        field_codes=("belongSection", "assetYear"),
        repair_field="assetYear",
        message_includes=("板块", "年度", "已存在"),
    )
    strategy = StandardDataStrategy(
        pool([constraint]), "run", form_code="BUILD_NETASSETS_MAINTAIL"
    )
    submitted = {"belongSection": "1", "assetYear": "2026", "netAssetAmount": "10"}
    definition = field("assetYear", "年度", "DATE")

    first, first_meta = strategy.allocate_unique_value(definition, "2026")
    second, second_meta = strategy.allocate_unique_value(definition, "2026")

    assert strategy.declared_unique_repair_fields(submitted) == ("assetYear",)
    assert strategy.unique_repair_field(
        "该板块对应年度的净资产已存在", submitted
    ) == "assetYear"
    assert (first, second) == ("2027", "2028")
    assert (first_meta["sequence"], second_meta["sequence"]) == (1, 2)


def test_unique_sequence_is_isolated_by_form_code():
    constraint = UniqueConstraintSpec(
        form_code="FORM_A",
        field_codes=("section", "year"),
        repair_field="year",
        message_includes=("已存在",),
    )
    strategy = StandardDataStrategy(pool([constraint]), "run", form_code="FORM_B")

    assert strategy.declared_unique_repair_fields({"section": "1", "year": "2026"}) == ()
    assert strategy.unique_repair_field(
        "记录已存在", {"section": "1", "year": "2026"}
    ) == ""


def test_global_pool_loads_and_validates_unique_constraint_manifest(tmp_path):
    (tmp_path / "unique_constraints.json").write_text(
        json.dumps({"constraints": [{
            "formCode": "FORM_A",
            "fieldCodes": ["section", "year"],
            "repairField": "year",
            "messageIncludes": ["年度", "已存在"],
        }]}),
        encoding="utf-8",
    )

    loaded = GlobalDataPool.from_directory(tmp_path)

    assert loaded.unique_constraints[0].field_codes == ("section", "year")
    assert loaded.unique_constraints[0].repair_field == "year"
