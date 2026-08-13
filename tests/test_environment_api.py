from datetime import UTC, datetime, timedelta
import json

import pytest

from ei_ui_smoke.environment_api import (
    ApiProbe,
    ApiProbeResult,
    EnvironmentProbeConfigError,
    block_unavailable_commands,
    load_environment_api_probes,
    matching_probes,
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


def test_read_only_post_probe_uses_the_configured_payload_and_reports_404():
    seen = {}

    def request(url, **kwargs):
        seen.update(url=url, **kwargs)
        return 404, {"X-Source-Revision": "deploy-sha"}

    result = probe_environment_apis([_probe()], base_url="https://env/fi-view/#/major", request=request)[0]

    assert result.status == 404
    assert seen["url"] == "https://env/fi-service/projMajorAdjustment/listPage"
    assert seen["body"] == {"currPage": 1}


def test_saved_browser_state_authorizes_a_probe_without_exposing_the_token(tmp_path, monkeypatch):
    from ei_ui_smoke import environment_api

    state = tmp_path / "auth.json"
    state.write_text(json.dumps({
        "cookies": [],
        "origins": [{"origin": "https://env", "localStorage": [
            {"name": "accessToken", "value": "secret-token"},
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
