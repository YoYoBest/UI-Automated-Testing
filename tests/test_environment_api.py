from datetime import UTC, datetime, timedelta
import json

import pytest

from ei_ui_smoke.environment_api import (
    ApiProbe,
    ApiProbeResult,
    ApiProbeStep,
    EnvironmentProbeConfigError,
    block_unavailable_commands,
    load_environment_api_probes,
    matching_probes,
    probe_matches_action,
    probe_environment_apis,
    update_version_mismatch_state,
)
from ei_ui_smoke.module_index import ModuleItem


def _probe() -> ApiProbe:
    return ApiProbe(
        id="major-adjustment", form_codes=("BUILD_PROJ_MAJOR_ADJUSTMENT",),
        components=("buildProject/before/projectChange/index",), method="POST",
        path="/fi-service/projMajorAdjustment/listPage", body={"currPage": 1},
        version_header="X-Source-Revision", version_mismatch_alert_hours=24,
    )


def _resource_pool_probe() -> ApiProbe:
    return ApiProbe(
        id="resource-pool-enterprise-sections",
        form_codes=("POOL_RESOURCE",),
        components=("projectResourcePool/index",),
        method="POST",
        path="/ezgo/ei-service/project/projStorage/list",
        body={"currPage": 1, "pageSize": 1, "formCode": "POOL_RESOURCE"},
        steps=(
            ApiProbeStep(
                "list", "POST", "/ezgo/ei-service/project/projStorage/list",
                {"currPage": 1, "pageSize": 1, "formCode": "POOL_RESOURCE"},
                (("resourcePoolId", ("data.0.id",)),),
            ),
            ApiProbeStep(
                "detail", "GET",
                "/ezgo/ei-service/project/projStorage/detail?id={resourcePoolId}", None,
            ),
        ),
        action_paths=(
            ("新增", "股权结构", "删除"),
            ("新增", "对外投资", "新增"),
        ),
        capability_name="资源池企业子表能力",
        capability_failure_statuses=(500,),
    )


def test_probe_manifest_rejects_a_write_like_post_without_read_only_marker(tmp_path):
    path = tmp_path / "probes.json"
    path.write_text(json.dumps({"probes": [{
        "id": "bad", "formCodes": ["FORM"], "method": "POST", "path": "/save", "body": {},
    }]}), encoding="utf-8")

    with pytest.raises(EnvironmentProbeConfigError, match="readOnly"):
        load_environment_api_probes(path)


@pytest.mark.parametrize("path", ["/fi-service/projMajorAdjustment/save", "/api/delete/123"])
def test_probe_manifest_rejects_write_paths_even_when_marked_read_only(tmp_path, path):
    manifest = tmp_path / "probes.json"
    manifest.write_text(json.dumps({"probes": [{
        "id": "unsafe", "formCodes": ["FORM"], "method": "POST", "readOnly": True,
        "path": path, "body": {"currPage": 1},
    }]}), encoding="utf-8")

    with pytest.raises(EnvironmentProbeConfigError, match="写接口"):
        load_environment_api_probes(manifest)


def test_probe_manifest_loads_a_read_only_chain_and_exact_action_paths(tmp_path):
    manifest = tmp_path / "probes.json"
    manifest.write_text(json.dumps({"probes": [{
        "id": "resource-pool", "formCodes": ["POOL_RESOURCE"],
        "actionPaths": [["新增", "对外投资", "新增"]],
        "capabilityName": "资源池企业子表能力",
        "capabilityFailureStatuses": [500],
        "steps": [
            {
                "id": "list", "method": "POST", "readOnly": True,
                "path": "/api/list", "body": {"currPage": 1},
                "extract": {"recordId": "data.0.id"},
            },
            {"id": "detail", "method": "GET", "path": "/api/detail?id={recordId}"},
        ],
    }]}), encoding="utf-8")

    probe = load_environment_api_probes(manifest)[0]

    assert [step.id for step in probe.steps] == ["list", "detail"]
    assert probe.action_paths == (("新增", "对外投资", "新增"),)
    assert probe.capability_failure_statuses == (500,)


@pytest.mark.parametrize("statuses", [[404], [200], [500, 601]])
def test_probe_manifest_rejects_non_5xx_capability_statuses(tmp_path, statuses):
    manifest = tmp_path / "probes.json"
    manifest.write_text(json.dumps({"probes": [{
        "id": "bad", "formCodes": ["FORM"], "method": "GET", "path": "/detail",
        "capabilityFailureStatuses": statuses,
    }]}), encoding="utf-8")

    with pytest.raises(EnvironmentProbeConfigError, match="5xx"):
        load_environment_api_probes(manifest)


def test_probe_manifest_rejects_a_chain_template_without_prior_extraction(tmp_path):
    manifest = tmp_path / "probes.json"
    manifest.write_text(json.dumps({"probes": [{
        "id": "bad", "formCodes": ["FORM"],
        "steps": [{"method": "GET", "path": "/detail?id={recordId}"}],
    }]}), encoding="utf-8")

    with pytest.raises(EnvironmentProbeConfigError, match="尚未提取"):
        load_environment_api_probes(manifest)


def test_404_blocks_only_commands_that_depend_on_the_missing_api():
    target = ModuleItem("major", "重大调整", ("建设项目", "重大调整"))
    other = ModuleItem("other", "其他", ("建设项目", "其他"))
    result = ApiProbeResult(_probe(), "https://env/fi-service/projMajorAdjustment/listPage", 404)
    commands = [
        (target, {"EI_FORM_CODE": "BUILD_PROJ_MAJOR_ADJUSTMENT"}, "tests/test_form_smoke.py"),
        (other, {"EI_FORM_CODE": "OTHER"}, "tests/test_form_smoke.py"),
    ]

    runnable, blocked = block_unavailable_commands(commands, [result])

    assert runnable == [commands[1]]
    assert [(block.probe_id, block.status) for block in blocked] == [("major-adjustment", 404)]


def test_non_404_response_is_not_reclassified_as_an_environment_blocker():
    result = ApiProbeResult(_probe(), "https://env/fi-service/projMajorAdjustment/listPage", 401)
    commands = [(ModuleItem("major", "重大调整", ("重大调整",)), {"EI_FORM_CODE": "BUILD_PROJ_MAJOR_ADJUSTMENT"}, "test.py")]

    runnable, blocked = block_unavailable_commands(commands, [result])

    assert runnable == commands
    assert blocked == []


def test_only_configured_5xx_is_classified_as_a_capability_blocker():
    probe = _resource_pool_probe()
    target = ModuleItem(
        "pool", "新增", ("资源池", "新增", "对外投资", "新增"),
        operation="新增", operation_path=("新增", "对外投资", "新增"),
    )
    command = (
        target,
        {
            "EI_FORM_CODE": "POOL_RESOURCE",
            "EI_ACTION": "新增",
            "EI_ACTION_PATH": json.dumps(["新增", "对外投资", "新增"], ensure_ascii=False),
        },
        "tests/test_module_action.py",
    )

    runnable, blocked = block_unavailable_commands(
        [command], [ApiProbeResult(probe, "https://env/detail?id=1", 500)]
    )

    assert runnable == []
    assert len(blocked) == 1
    assert blocked[0].classification == "environment-capability-unavailable"
    assert blocked[0].capability_name == "资源池企业子表能力"
    assert blocked[0].action_path == ("新增", "对外投资", "新增")


def test_undeclared_500_remains_ordinary_execution_evidence():
    result = ApiProbeResult(_probe(), "https://env/probe", 500)
    command = (
        ModuleItem("major", "重大调整", ("重大调整",)),
        {"EI_FORM_CODE": "BUILD_PROJ_MAJOR_ADJUSTMENT"},
        "test.py",
    )

    runnable, blocked = block_unavailable_commands([command], [result])

    assert runnable == [command]
    assert blocked == []


def test_probe_action_matching_is_exact_for_grouped_operation_paths():
    probe = _resource_pool_probe()
    outer_add = {
        "form_code": "POOL_RESOURCE", "component": "projectResourcePool/index",
        "action": "新增", "action_path": [],
    }
    equity_add = {
        **outer_add, "action_path": ["新增", "股权结构", "新增"],
    }
    equity_delete = {
        **outer_add, "action": "删除", "action_path": ["新增", "股权结构", "删除"],
    }

    assert not probe_matches_action(probe, outer_add)
    assert not probe_matches_action(probe, equity_add)
    assert probe_matches_action(probe, equity_delete)
    assert matching_probes([probe], {
        "EI_ACTIONS_JSON": json.dumps([outer_add, equity_delete], ensure_ascii=False),
    }) == [probe]


def test_read_only_post_probe_uses_the_configured_payload_and_reports_404():
    seen = {}

    def request(url, **kwargs):
        seen.update(url=url, **kwargs)
        return 404, {"X-Source-Revision": "deploy-sha"}

    result = probe_environment_apis([_probe()], base_url="https://env/fi-view/#/major", request=request)[0]

    assert result.status == 404
    assert seen["url"] == "https://env/fi-service/projMajorAdjustment/listPage"
    assert seen["body"] == {"currPage": 1}


def test_read_only_chain_extracts_an_existing_id_before_calling_detail():
    calls = []

    def request(url, **kwargs):
        calls.append((url, kwargs))
        if url.endswith("/projStorage/list"):
            return 200, {}, {"status": "0", "data": [{"id": "pool id/1"}]}
        return 500, {}, {"status": "10001", "msg": "系统异常"}

    result = probe_environment_apis(
        [_resource_pool_probe()], base_url="https://env/ei-view/#/resourcePool",
        request=request,
    )[0]

    assert [call[0] for call in calls] == [
        "https://env/ezgo/ei-service/project/projStorage/list",
        "https://env/ezgo/ei-service/project/projStorage/detail?id=pool%20id%2F1",
    ]
    assert calls[0][1]["body"]["formCode"] == "POOL_RESOURCE"
    assert result.status == 500
    assert result.blocking_classification == "environment-capability-unavailable"
    assert [(step.id, step.status) for step in result.steps] == [("list", 200), ("detail", 500)]


def test_chain_without_an_existing_id_is_inconclusive_and_does_not_call_detail():
    calls = []

    def request(url, **_kwargs):
        calls.append(url)
        return 200, {}, {"status": "0", "data": []}

    result = probe_environment_apis(
        [_resource_pool_probe()], base_url="https://env/ei-view", request=request,
    )[0]

    assert calls == ["https://env/ezgo/ei-service/project/projStorage/list"]
    assert result.status is None
    assert "resourcePoolId" in result.error
    assert result.blocking_classification == ""


def test_saved_browser_state_authorizes_a_probe_without_exposing_the_token(tmp_path, monkeypatch):
    from ei_ui_smoke import environment_api

    state = tmp_path / "auth.json"
    state.write_text(json.dumps({
        "cookies": [],
        "origins": [{"origin": "https://env", "localStorage": [
            {"name": "accessToken", "value": "secret-token"},
            {"name": "tenantId", "value": "secret-tenant"},
        ]}],
    }), encoding="utf-8")
    captured = {}

    class Response:
        status = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *_args): return False

    monkeypatch.setattr(environment_api, "urlopen", lambda request, **_kwargs: captured.update(request.headers) or Response())

    environment_api._http_request("https://env/fi-service/probe", method="GET", body=None, storage_state=str(state))

    assert captured["Authorization"] == "secret-token"
    assert captured["X-tenant-id"] == "secret-tenant"


def test_version_mismatch_warns_only_after_the_configured_duration(tmp_path):
    state = tmp_path / "version-state.json"
    result = ApiProbeResult(_probe(), "https://env/probe", 200, deployed_version="deployed")
    first = datetime(2026, 8, 12, tzinfo=UTC)

    assert update_version_mismatch_state([result], source_version="source", state_file=state, now=first) == []
    warnings = update_version_mismatch_state(
        [result], source_version="source", state_file=state, now=first + timedelta(hours=24),
    )

    assert len(warnings) == 1
    assert "major-adjustment" in warnings[0]


def test_non_success_response_header_cannot_start_a_version_mismatch_warning(tmp_path):
    state = tmp_path / "version-state.json"
    result = ApiProbeResult(_probe(), "https://env/probe", 404, deployed_version="old")

    assert update_version_mismatch_state(
        [result], source_version="source", state_file=state,
        now=datetime(2026, 8, 12, tzinfo=UTC),
    ) == []
    assert json.loads(state.read_text(encoding="utf-8")) == {}
