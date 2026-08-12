from ei_ui_smoke.module_index import modules_from_menu


def test_runtime_menu_keeps_referenced_dialog_section_actions(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    (views / "components").mkdir(parents=True)
    (views / "index.vue").write_text(
        '''<el-button>新增</el-button><script>
        const FORM_CODE = "PROJECT_POOL";
        const props = { componentPath: "projectResourcePool/Modify", dialogTitle: "新增资源池企业" };
        </script>''',
        encoding="utf-8",
    )
    (views / "Modify.vue").write_text(
        '<script setup>import Tables from "./components/Tables.vue";</script><template><Tables /></template>',
        encoding="utf-8",
    )
    (views / "components" / "Tables.vue").write_text(
        '''<template><div>{{ ownershipTitle }}</div><el-button>新增</el-button></template>
        <script>defineProps({ownershipTitle:{default:"股权结构"}})</script>''',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "POOL_RESOURCE",
            "path": "/resourcePool",
            "component": "/src/views/projectResourcePool/index",
            "meta": {"title": "资源池"},
            "children": [],
        }]},
        "_visibleActions": {"POOL_RESOURCE": ["新增"]},
    }

    nested = [item for item in modules_from_menu(payload, tmp_path) if item.operation_path]

    assert len(nested) == 1
    assert nested[0].path == ("资源池", "新增", "新增资源池企业", "股权结构", "新增")
    assert nested[0].operation_path == ("新增", "股权结构", "新增")


def test_runtime_menu_follows_local_component_dialog_sections(tmp_path):
    views = tmp_path / "ei-view" / "srcEi" / "views" / "projectResourcePool"
    (views / "components").mkdir(parents=True)
    (tmp_path / "ei-view" / "src" / "views").mkdir(parents=True)
    (views / "index.vue").write_text(
        '''<el-button v-if="$hasButton('pool_add')">新增</el-button><script>
        const FORM_CODE = "PROJECT_POOL";
        const props = { localComponent: "Modify", dialogTitle: "新增资源池企业" };
        </script>''',
        encoding="utf-8",
    )
    (views / "Modify.vue").write_text(
        '<script setup>import Tables from "./components/Tables.vue";</script><template><Tables /></template>',
        encoding="utf-8",
    )
    (views / "components" / "Tables.vue").write_text(
        '''<template>
        <div>{{ ownershipTitle }}</div><el-button>新增</el-button><el-button>删除</el-button>
        <div>{{ entInvestTitle }}</div><el-button>新增</el-button><el-button>删除</el-button>
        </template><script>defineProps({
        ownershipTitle:{default:"股权结构"},entInvestTitle:{default:"对外投资"}
        })</script>''',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "POOL_RESOURCE",
            "path": "/resourcePool",
            "component": "/srcEi/views/projectResourcePool/index",
            "meta": {"title": "资源池"},
            "children": [],
        }]},
        "_buttonCodes": ["pool_add"],
    }

    nested = [item for item in modules_from_menu(payload, tmp_path) if item.operation_path]

    assert [item.path for item in nested] == [
        ("资源池", "新增", "新增资源池企业", "股权结构", "新增"),
        ("资源池", "新增", "新增资源池企业", "股权结构", "删除"),
        ("资源池", "新增", "新增资源池企业", "对外投资", "新增"),
        ("资源池", "新增", "新增资源池企业", "对外投资", "删除"),
    ]
    assert all(item.permission_codes == ("pool_add",) for item in nested)

    denied_payload = {**payload, "_buttonCodes": []}
    denied_actions = [
        item
        for item in modules_from_menu(denied_payload, tmp_path)
        if item.operation
    ]
    assert denied_actions == []


def test_runtime_legacy_alias_restores_only_nested_dialog_structure(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "projectResourcePool"
    (views / "components").mkdir(parents=True)
    (views / "index.vue").write_text(
        '''<el-button>错误的旧版按钮</el-button><script>
        const props = { componentPath: "projectResourcePool/Modify", dialogTitle: "新增资源池企业" };
        </script>''',
        encoding="utf-8",
    )
    (views / "Modify.vue").write_text(
        '<script setup>import Tables from "./components/Tables.vue";</script><template><Tables /></template>',
        encoding="utf-8",
    )
    (views / "components" / "Tables.vue").write_text(
        '''<template><div>{{ ownershipTitle }}</div><el-button>新增</el-button></template>
        <script>defineProps({ownershipTitle:{default:"股权结构"}})</script>''',
        encoding="utf-8",
    )
    payload = {"data": {"funcPerm": [{
        "funcCode": "POOL_RESOURCE",
        "path": "/resourcePool",
        "component": "/srcEi/views/projectResourcePool/index",
        "meta": {"title": "资源池"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    flat = [item for item in items if item.operation and not item.operation_path]
    nested = [item for item in items if item.operation_path]

    assert flat == []
    assert [item.path for item in nested] == [("资源池", "新增", "新增资源池企业", "股权结构", "新增")]
    assert nested[0].component == "projectResourcePool/index"


def test_source_permissions_and_dialog_sections_form_the_complete_operation_tree(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "pool"
    (views / "components").mkdir(parents=True)
    (views / "index.vue").write_text(
        '''
        <el-button>查询</el-button><el-button>重置</el-button>
        <el-button v-if="$hasButton('pool_add')">新增</el-button>
        <el-button v-if="$hasButton('pool_stale_a')">旧操作甲</el-button>
        <el-button v-if="$hasButton('pool_stale_b')">旧操作乙</el-button>
        <script>const props={componentPath:"pool/Modify",dialogTitle:"新增对话框"}</script>
        ''',
        encoding="utf-8",
    )
    (views / "Modify.vue").write_text(
        '<script setup>import Tables from "./components/Tables.vue";</script><template><Tables /></template>',
        encoding="utf-8",
    )
    (views / "components" / "Tables.vue").write_text(
        '''<template>
        <div>{{ firstTitle }}</div><el-button>新增</el-button><el-button>删除</el-button>
        <div>{{ secondTitle }}</div><el-button>新增</el-button><el-button>删除</el-button>
        </template><script>defineProps({
        firstTitle:{default:"股权结构"},secondTitle:{default:"对外投资"}
        })</script>''',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "POOL", "path": "/pool",
            "component": "/src/views/pool/index", "meta": {"title": "资源池"},
            "children": [],
        }]},
        "_buttonCodes": ["pool_add", "pool_prepare", "pool_edit", "pool_del"],
        "_deployedActions": {"POOL": ["新增", "立项准备", "编辑", "删除"]},
    }

    items = modules_from_menu(payload, tmp_path)
    flat = [item.operation for item in items if item.operation and not item.operation_path]
    nested_paths = [item.path for item in items if item.operation_path]
    operation_order = [
        "/".join(item.path[1:])
        for item in items
        if item.operation
    ]

    assert flat == ["查询", "重置", "新增"]
    assert operation_order == [
        "查询",
        "重置",
        "新增",
        "新增/新增对话框/股权结构/新增",
        "新增/新增对话框/股权结构/删除",
        "新增/新增对话框/对外投资/新增",
        "新增/新增对话框/对外投资/删除",
    ]
    assert nested_paths == [
        ("资源池", "新增", "新增对话框", "股权结构", "新增"),
        ("资源池", "新增", "新增对话框", "股权结构", "删除"),
        ("资源池", "新增", "新增对话框", "对外投资", "新增"),
        ("资源池", "新增", "新增对话框", "对外投资", "删除"),
    ]


def test_runtime_menu_always_ignores_stale_deployed_actions(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "pool"
    views.mkdir(parents=True)
    (views / "index.vue").write_text(
        '<el-button>查询</el-button><el-button v-if="$hasButton(\'pool_add\')">新增</el-button>',
        encoding="utf-8",
    )
    payload = {
        "data": {"funcPerm": [{
            "funcCode": "POOL", "path": "/pool",
            "component": "/src/views/pool/index", "meta": {"title": "资源池"},
            "children": [],
        }]},
        "_buttonCodes": ["pool_add"],
        "_deployedActions": {"POOL": ["错误的旧操作"]},
    }

    operations = [item.operation for item in modules_from_menu(payload, tmp_path) if item.operation]

    assert operations == ["查询", "新增"]


def test_nested_operations_follow_their_outer_action_for_every_module(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "customer"
    (views / "components").mkdir(parents=True)
    (views / "index.vue").write_text(
        '''<template><el-button>查询</el-button><el-button>编辑</el-button></template>
        <script>const props={localComponent:"EditForm",dialogTitle:"编辑客户"}</script>''',
        encoding="utf-8",
    )
    (views / "EditForm.vue").write_text(
        '<script setup>import Contacts from "./components/Contacts.vue";</script><template><Contacts /></template>',
        encoding="utf-8",
    )
    (views / "components" / "Contacts.vue").write_text(
        '''<template><div>{{ contactsTitle }}</div><el-button>新增</el-button><el-button>删除</el-button></template>
        <script>defineProps({contactsTitle:{default:"联系人"}})</script>''',
        encoding="utf-8",
    )
    payload = {"data": {"funcPerm": [{
        "funcCode": "CUSTOMER", "path": "/customer",
        "component": "/src/views/customer/index", "meta": {"title": "客户管理"},
        "children": [],
    }]}}

    items = modules_from_menu(payload, tmp_path)
    operations = ["/".join(item.path[1:]) for item in items if item.operation]

    assert operations == [
        "查询",
        "编辑",
        "编辑/编辑客户/联系人/新增",
        "编辑/编辑客户/联系人/删除",
    ]
    nested = [item for item in items if item.operation_path]
    assert [item.operation_path for item in nested] == [
        ("编辑", "联系人", "新增"),
        ("编辑", "联系人", "删除"),
    ]
