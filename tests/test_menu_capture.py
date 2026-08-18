from ei_ui_smoke.menu_capture import _capture_detail_father_trees, _menu_leaves


class _Response:
    ok = True

    def json(self):
        return {"data": [{"funcCode": "OVERVIEW"}]}


class _RequestClient:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


class _Context:
    def __init__(self):
        self.request = _RequestClient()


def test_menu_leaves_yield_only_visible_leaf_routes():
    nodes = [{"funcCode": "PROJECT", "path": "/project", "children": [
        {"funcCode": "POOL", "path": "pool", "children": []},
        {"funcCode": "HIDDEN", "path": "hidden", "meta": {"hidden": True}, "children": []},
    ]}]

    assert list(_menu_leaves(nodes)) == [("POOL", "/project/pool")]


def test_father_detail_tree_capture_reuses_menu_auth_and_source_proxy_path(tmp_path):
    view = tmp_path / "ei-view"
    views = view / "src" / "views" / "projectManage" / "before"
    views.mkdir(parents=True)
    (view / ".env").write_text("VITE_APP_BASE_API=/ezgo\n", encoding="utf-8")
    (views / "detail.vue").write_text(
        'CommonAPI.getUserFuncPermTree({ fatherId: "30021" })', encoding="utf-8"
    )
    context = _Context()

    trees = _capture_detail_father_trees(
        context,
        origin="https://example.test",
        app_id="10015",
        auth_headers={"authorization": "masked", "x-tenant-id": "tenant"},
        source_root=tmp_path,
        timeout_ms=1234,
    )

    assert trees == [{
        "fatherId": "30021",
        "sourceComponent": "projectManage/before/detail",
        "nodes": [{"funcCode": "OVERVIEW"}],
    }]
    assert context.request.calls == [(
        "https://example.test/ezgo/ei-service/funcPerm/getUserFuncPermTree",
        {
            "params": {"appId": "10015", "fatherId": "30021"},
            "headers": {"authorization": "masked", "x-tenant-id": "tenant"},
            "timeout": 1234,
        },
    )]
