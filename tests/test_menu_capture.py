from ei_ui_smoke.menu_capture import _menu_leaves


def test_menu_leaves_yield_only_visible_leaf_routes():
    nodes = [{"funcCode": "PROJECT", "path": "/project", "children": [
        {"funcCode": "POOL", "path": "pool", "children": []},
        {"funcCode": "HIDDEN", "path": "hidden", "meta": {"hidden": True}, "children": []},
    ]}]

    assert list(_menu_leaves(nodes)) == [("POOL", "/project/pool")]
