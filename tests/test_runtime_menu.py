from ei_ui_smoke.module_index import modules_from_menu


def test_runtime_menu_preserves_chinese_hierarchy_and_routes(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "cashFlow" / "fund"
    views.mkdir(parents=True)
    (views / "list.vue").write_text('const formCode = "FUND_CASH"', encoding="utf-8")
    payload = {"data": {"funcPerm": [{
        "funcCode": "CASH", "path": "/cash", "component": "Layout", "meta": {"title": "现金流"},
        "children": [{"funcCode": "FUND", "path": "fund", "component": "cashFlow/fund/list", "meta": {"title": "基金现金流"}, "children": []}],
    }]}}
    items = modules_from_menu(payload, tmp_path)
    assert items[-1].path == ("现金流", "基金现金流")
    assert items[-1].route == "/cash/fund"
    assert items[-1].form_code == "FUND_CASH"
    assert items[-1].runnable


def test_runtime_menu_never_executes_a_directory_with_a_route(tmp_path):
    (tmp_path / "ei-view" / "src" / "views").mkdir(parents=True)
    payload = {"data": {"funcPerm": [{
        "funcCode": "BASIC",
        "path": "/baseManage",
        "component": "Layout",
        "meta": {"title": "基础管理"},
        "children": [{
            "funcCode": "PLATFORM",
            "path": "managePlatform",
            "component": "baseManage/managePlatform/index",
            "meta": {"title": "管理平台"},
            "children": [],
        }],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    basic = next(item for item in items if item.id == "BASIC")
    platform = next(item for item in items if item.id == "PLATFORM")

    assert not basic.runnable
    assert platform.runnable


def test_runtime_menu_keeps_routable_parent_page_and_its_actions(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "buildProject"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        '<el-button @click="search">搜索</el-button>'
        '<el-button @click="reset">重置</el-button>'
        '<el-button v-if="$hasButton(\'buildProjectAdd\')"><span>新增项目</span></el-button>',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "BUILD_PROJECT",
            "path": "/buildProject",
            "component": "buildProject/index",
            "meta": {"title": "建设项目"},
            "children": [{
                "funcCode": "NET_ASSETS",
                "path": "netAssets",
                "component": "netAssets/index",
                "meta": {"title": "净资产维护"},
                "children": [],
            }],
        }]},
        "_buttonCodes": ["buildProjectAdd"],
    }

    items = modules_from_menu(payload, tmp_path)
    parent = next(item for item in items if item.id == "BUILD_PROJECT")
    actions = [item for item in items if item.operation]

    assert parent.runnable
    assert [item.path for item in actions] == [
        ("建设项目", "搜索"),
        ("建设项目", "重置"),
        ("建设项目", "新增项目"),
    ]
    assert all(item.route == "/buildProject" and item.runnable for item in actions)


def test_runtime_menu_preserves_duplicate_parent_group_and_page_names(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "selfManagedFunds"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('const formCode = "SELF_FUND"', encoding="utf-8")
    sibling_views = tmp_path / "ei-view" / "src" / "views" / "netAssets"
    sibling_views.mkdir(parents=True)
    (sibling_views / "index.vue").write_text('const formCode = "NET_ASSETS"', encoding="utf-8")
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "SELF_GROUP",
            "path": "/selfManagedFunds",
            "component": "Layout",
            "meta": {"title": "自管基金"},
            "children": [{
                "funcCode": "SELF_PAGE",
                "path": "index",
                "component": "selfManagedFunds/index",
                "meta": {"title": "自管基金"},
                "children": [],
            }, {
                "funcCode": "NET_ASSETS",
                "path": "netAssets",
                "component": "netAssets/index",
                "meta": {"title": "净资产维护"},
                "children": [],
            }],
        }]},
        "_detailTrees": {"ZGJJ_": [{
            "funcCode": "BASE",
            "meta": {"title": "基本信息"},
            "children": [],
        }]},
    }

    items = modules_from_menu(payload, tmp_path)
    group = next(item for item in items if item.id == "SELF_GROUP")
    page = next(item for item in items if item.id == "SELF_PAGE")
    sibling = next(item for item in items if item.id == "NET_ASSETS")
    detail = next(item for item in items if item.id == "detail:ZGJJ_:BASE")

    assert group.path == ("自管基金",)
    assert not group.runnable
    assert page.path == ("自管基金", "自管基金")
    assert page.route == "/selfManagedFunds/index"
    assert page.form_code == "SELF_FUND"
    assert page.runnable
    assert group.source_file == ""
    assert sibling.path == ("自管基金", "净资产维护")
    assert len(page.path) == len(sibling.path)
    assert detail.path == ("自管基金", "自管基金", "详情", "基本信息")


def test_runtime_menu_assigns_shared_page_content_to_deepest_owner(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "buildProject"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        '<el-button @click="search">搜索</el-button>'
        '<el-button @click="reset">重置</el-button>'
        '<el-button v-if="$hasButton(\'buildProjectAdd\')">新增项目</el-button>',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "BUILD_GROUP",
            "path": "/buildProject",
            "component": "buildProject/index",
            "meta": {"title": "建设项目"},
            "children": [{
                "funcCode": "BUILD_PAGE",
                "path": "",
                "component": "buildProject/index",
                "meta": {"title": "建设项目"},
                "children": [],
            }],
        }]},
        "_buttonCodes": ["buildProjectAdd"],
        "_detailTrees": {"buildProject": [{
            "funcCode": "BASE",
            "meta": {"title": "基本信息"},
            "children": [],
        }]},
    }

    items = modules_from_menu(payload, tmp_path)
    group = next(item for item in items if item.id == "BUILD_GROUP")
    page = next(item for item in items if item.id == "BUILD_PAGE")
    actions = [item for item in items if item.operation]
    detail = next(item for item in items if item.id == "detail:buildProject:BASE")

    assert group.path == ("建设项目",)
    assert page.path == ("建设项目", "建设项目")
    assert [item.path for item in actions] == [
        ("建设项目", "建设项目", "搜索"),
        ("建设项目", "建设项目", "重置"),
        ("建设项目", "建设项目", "新增项目"),
    ]
    assert detail.path == ("建设项目", "建设项目", "详情", "基本信息")


def test_runtime_menu_keeps_shared_component_actions_on_distinct_routes(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "buildProject"
    views.mkdir(parents=True)
    (views / "index.vue").write_text("<el-button>搜索</el-button>", encoding="utf-8")
    payload = {"data": {"funcPerm": [{
        "funcCode": "CURRENT_PROJECTS",
        "path": "/buildProject/current",
        "component": "buildProject/index",
        "meta": {"title": "当前项目"},
        "children": [],
    }, {
        "funcCode": "ARCHIVED_PROJECTS",
        "path": "/buildProject/archived",
        "component": "buildProject/index",
        "meta": {"title": "归档项目"},
        "children": [],
    }]}}

    actions = [item for item in modules_from_menu(payload, tmp_path) if item.operation]

    assert [item.path for item in actions] == [("当前项目", "搜索"), ("归档项目", "搜索")]


def test_runtime_menu_keeps_actions_for_distinct_detail_nodes_sharing_component(tmp_path):
    self_views = tmp_path / "fi-view" / "src" / "views" / "selfManagedFunds"
    self_views.mkdir(parents=True)
    (self_views / "index.vue").write_text("", encoding="utf-8")
    shared_views = tmp_path / "fi-view" / "src" / "views" / "sharedDetail"
    shared_views.mkdir(parents=True)
    (shared_views / "index.vue").write_text("<el-button>编辑</el-button>", encoding="utf-8")
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "SELF",
            "path": "/selfManagedFunds",
            "component": "selfManagedFunds/index",
            "meta": {"title": "自管基金"},
            "children": [],
        }]},
        "_detailTrees": {"ZGJJ_": [{
            "funcCode": "BASE",
            "component": "sharedDetail/index",
            "meta": {"title": "基本信息"},
            "children": [],
        }, {
            "funcCode": "RISK",
            "component": "sharedDetail/index",
            "meta": {"title": "风险信息"},
            "children": [],
        }]},
    }

    actions = [item for item in modules_from_menu(payload, tmp_path) if item.operation]

    assert [item.path for item in actions] == [
        ("自管基金", "详情", "基本信息", "编辑"),
        ("自管基金", "详情", "风险信息", "编辑"),
    ]


def test_runtime_detail_tree_belongs_to_inner_page_not_same_named_group(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "buildProject"
    views.mkdir(parents=True)
    (views / "index.vue").write_text("<el-button>搜索</el-button>", encoding="utf-8")
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "BUILD_GROUP",
            "path": "/buildProject",
            "component": "Layout",
            "meta": {"title": "建设项目"},
            "children": [{
                "funcCode": "BUILD_PAGE",
                "path": "",
                "component": "buildProject/index",
                "meta": {"title": "建设项目"},
                "children": [],
            }, {
                "funcCode": "NET_ASSETS",
                "path": "netAssets",
                "component": "netAssets/index",
                "meta": {"title": "净资产维护"},
                "children": [],
            }],
        }]},
        "_detailTrees": {"buildProject": [{
            "funcCode": "BASE",
            "meta": {"title": "基本信息"},
            "children": [],
        }]},
    }

    items = modules_from_menu(payload, tmp_path)
    detail = next(item for item in items if item.id == "detail:buildProject:BASE")

    assert detail.path == ("建设项目", "建设项目", "详情", "基本信息")


def test_runtime_menu_attaches_source_actions_to_business_module(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectPool"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        '<el-button>查询</el-button><el-button>新增</el-button>const FORM_CODE = "PROJECT_POOL"',
        encoding="utf-8",
    )
    payload = {"data": {"funcPerm": [{
        "funcCode": "PROJECT_POOL",
        "path": "/projectPool",
        "component": "projectPool/index",
        "meta": {"title": "资源池"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    actions = [item for item in items if item.operation]

    assert [item.path for item in actions] == [("资源池", "查询"), ("资源池", "新增")]
    assert all(item.route == "/projectPool" for item in actions)
    assert all(item.form_code == "PROJECT_POOL" for item in actions)
    assert all(item.runnable for item in actions)


def test_runtime_menu_resolves_route_alias_to_source_actions(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        '<el-button>查询</el-button><el-button>新增</el-button>'
        'const FORM_CODE = "POOL_RESOURCE"',
        encoding="utf-8",
    )
    payload = {"data": {"funcPerm": [{
        "funcCode": "RESOURCE_POOL",
        "path": "/resourcePool",
        "component": "projectManage/resourcePool/index",
        "meta": {"title": "资源池"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    resource_pool = next(item for item in items if item.id == "RESOURCE_POOL")
    actions = [item for item in items if item.operation]

    assert resource_pool.source_file.replace("\\", "/").endswith("projectResourcePool/index.vue")
    assert resource_pool.form_code == "POOL_RESOURCE"
    assert [item.operation for item in actions] == ["查询", "新增"]


def test_runtime_menu_does_not_guess_an_ambiguous_route_alias(tmp_path):
    for directory in ("projectResourcePool", "fundResourcePool"):
        views = tmp_path / "ei-view" / "src" / "views" / directory
        views.mkdir(parents=True)
        (views / "index.vue").write_text("<el-button>查询</el-button>", encoding="utf-8")
    payload = {"data": {"funcPerm": [{
        "funcCode": "POOL",
        "path": "/resourcePool",
        "component": "resourcePool/index",
        "meta": {"title": "资源池"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)

    assert not [item for item in items if item.operation]


def test_runtime_menu_appends_nested_detail_modules(tmp_path):
    (tmp_path / "ei-view" / "src" / "views").mkdir(parents=True)
    payload = {
        "data": {"funcPerm": [{"funcCode": "SELF", "path": "/selfManagedFunds", "component": "selfManagedFunds/index", "meta": {"title": "自管基金"}, "children": []}]},
        "_detailTrees": {"ZGJJ_": [{"funcCode": "BASE", "meta": {"title": "基本信息"}, "children": [
            {"funcCode": "ACCOUNT", "meta": {"title": "账户信息"}, "component": "selfManagedFunds/accountInfo/index", "children": []}
        ]}]},
    }
    items = modules_from_menu(payload, tmp_path)
    detail = items[-1]
    assert detail.path == ("自管基金", "详情", "基本信息", "账户信息")
    assert detail.requires_business_id
    assert detail.route == "/selfManagedFunds/detail"


def test_runtime_menu_attaches_actions_to_detail_nodes(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "selfManagedFunds" / "baseInfo"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        "<el-button>编辑</el-button><el-button>变更记录</el-button><el-button>更新工商信息</el-button>",
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "SELF",
            "path": "/selfManagedFunds",
            "component": "selfManagedFunds/index",
            "meta": {"title": "自管基金"},
            "children": [],
        }]},
        "_detailTrees": {"ZGJJ_": [{
            "funcCode": "BASE",
            "component": "selfManagedFunds/baseInfo/index",
            "meta": {"title": "基本信息"},
            "children": [],
        }]},
    }

    items = modules_from_menu(payload, tmp_path)
    actions = [item for item in items if item.operation]

    assert [item.path for item in actions] == [
        ("自管基金", "详情", "基本信息", "编辑"),
        ("自管基金", "详情", "基本信息", "变更记录"),
        ("自管基金", "详情", "基本信息", "更新工商信息"),
    ]
    assert all(item.requires_business_id for item in actions)
    assert all(not item.runnable for item in actions)


def test_runtime_menu_ignores_legacy_visible_action_snapshot(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        "<el-button>查询</el-button><el-button>新增</el-button>"
        "<el-button>导出</el-button><el-button>删除</el-button>",
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "POOL_RESOURCE",
            "path": "/resourcePool",
            "component": "projectResourcePool/index",
            "meta": {"title": "资源池"},
            "children": [],
        }]},
        "_visibleActions": {"POOL_RESOURCE": ["查询", "新增", "删除"]},
    }

    actions = [item.operation for item in modules_from_menu(payload, tmp_path) if item.operation]

    assert actions == ["查询", "新增", "导出", "删除"]


def test_runtime_menu_filters_source_buttons_by_current_user_permission_codes(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        '<el-button>查询</el-button>'
        '<el-button v-if="$hasButton(\'pool_add\')">新增</el-button>'
        '<el-button v-if="$hasButton(\'pool_export\')">导出</el-button>'
        '<el-button v-if="$hasButton(\'pool_del\')">删除</el-button>',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "POOL",
            "path": "/pool",
            "component": "projectResourcePool/index",
            "meta": {"title": "资源池"},
            "children": [],
        }]},
        "_buttonCodes": ["pool_add", "pool_del"],
    }

    actions = [item.operation for item in modules_from_menu(payload, tmp_path) if item.operation]

    assert actions == ["查询", "新增", "删除"]


def test_runtime_srcei_component_does_not_fall_back_to_src_page(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    views.mkdir(parents=True)
    (views / "index.vue").write_text("<el-button>错误按钮</el-button>", encoding="utf-8")
    payload = {"data": {"funcPerm": [{
        "funcCode": "POOL",
        "path": "/resourcePool",
        "component": "/srcEi/views/projectResourcePool/index",
        "meta": {"title": "资源池"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    page = next(item for item in items if item.id == "POOL")
    actions = [item.operation for item in items if item.operation]

    assert page.form_code == ""
    assert page.source_file == ""
    assert actions == []


def test_runtime_component_uses_matching_source_root_when_both_exist(tmp_path):
    src = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    src_ei = tmp_path / "ei-view" / "srcEi" / "views" / "projectResourcePool"
    src.mkdir(parents=True)
    src_ei.mkdir(parents=True)
    (src / "index.vue").write_text("<el-button>旧版按钮</el-button>", encoding="utf-8")
    (src_ei / "index.vue").write_text("<el-button>立项准备</el-button>", encoding="utf-8")
    payload = {"data": {"funcPerm": [{
        "funcCode": "POOL",
        "path": "/resourcePool",
        "component": "/srcEi/views/projectResourcePool/index",
        "meta": {"title": "资源池"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    page = next(item for item in items if item.id == "POOL")
    actions = [item.operation for item in items if item.operation]

    assert page.source_file.replace("\\", "/").startswith("srcEi/")
    assert actions == ["立项准备"]
