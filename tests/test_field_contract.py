import json

import pytest

from ei_ui_smoke.field_contract import FieldContractResolver
from ei_ui_smoke.module_driver import ModuleSmokeDriver


class _RuntimeApi:
    config_calls = 0
    data_calls = 0

    def __init__(self, _page):
        pass

    def get_form_config(self, form_code):
        type(self).config_calls += 1
        assert form_code == "PERSON_ASSIGNMENT"
        return {"data": {"fields": [
            {
                "fieldCode": "userId",
                "fieldName": "经办人",
                "fieldType": "PurvarSelectUser-USER_SELECT",
                "fixedType": 0,
            },
            {
                "fieldCode": "postName",
                "fieldName": "岗位",
                "fieldType": "ElInput-TEXT",
                "fixedType": 0,
            },
        ]}}

    def get_form_data(self, form_code, business_id):
        type(self).data_calls += 1
        assert (form_code, business_id) == ("PERSON_ASSIGNMENT", "record-1")
        return {"data": {"dataJson": json.dumps({
            "userId": "42",
            "postName": "投资经理",
            "dynamicFieldLabels": json.dumps({
                "userId": [{"name": "张三", "value": "42"}],
            }, ensure_ascii=False),
        }, ensure_ascii=False)}}


def test_field_contract_merges_source_runtime_and_exact_manifest_once(tmp_path):
    manifest = tmp_path / "field_contracts.json"
    manifest.write_text(json.dumps({"forms": {
        "PERSON_ASSIGNMENT": {
            "fields": [{"fieldCode": "postName", "fieldName": "岗位名称"}],
            "components": {
                "investment/assignment/index": {
                    "fields": [{
                        "fieldCode": "userId",
                        "fieldName": "委派人员",
                        "fixedType": 0,
                    }],
                },
            },
        },
    }}, ensure_ascii=False), encoding="utf-8")
    _RuntimeApi.config_calls = 0
    resolver = FieldContractResolver(
        object(),
        form_code="PERSON_ASSIGNMENT",
        component="src/views/investment/assignment/index.vue?mode=add",
        source_fields=[("projectId", "项目", False)],
        manifest_path=manifest,
        api_factory=_RuntimeApi,
    )

    contract = resolver.resolve()

    assert _RuntimeApi.config_calls == 1
    assert resolver.resolve() is contract
    assert _RuntimeApi.config_calls == 1
    labels = {field.field_code: field.field_name for field in contract.definitions}
    assert labels == {"postName": "岗位名称", "projectId": "项目", "userId": "委派人员"}
    assert contract.runtime_codes == {"postName", "userId"}


def test_runtime_readback_accepts_dynamic_display_cache_without_runtime_dom_id():
    _RuntimeApi.data_calls = 0
    resolver = FieldContractResolver(
        object(),
        form_code="PERSON_ASSIGNMENT",
        api_factory=_RuntimeApi,
    )
    driver = object.__new__(ModuleSmokeDriver)
    driver._runtime_field_codes = frozenset({"userId", "postName"})
    driver._field_contract_resolver = resolver

    verified, payload = driver._verify_runtime_readback(
        "record-1",
        {"userId": "张三", "postName": "投资经理"},
        {"userId", "postName"},
    )

    assert verified == {"userId", "postName"}
    assert payload["data"]["dataJson"]
    assert _RuntimeApi.data_calls == 1


def test_runtime_readback_rejects_persisted_value_mismatch():
    resolver = FieldContractResolver(
        object(),
        form_code="PERSON_ASSIGNMENT",
        api_factory=_RuntimeApi,
    )
    driver = object.__new__(ModuleSmokeDriver)
    driver._runtime_field_codes = frozenset({"postName"})
    driver._field_contract_resolver = resolver

    with pytest.raises(AssertionError, match="postName"):
        driver._verify_runtime_readback(
            "record-1", {"postName": "风控经理"}, {"postName"}
        )
