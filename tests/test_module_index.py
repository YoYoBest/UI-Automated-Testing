from ei_ui_smoke.module_index import discover_modules, search_modules


def make_page(tmp_path, relative, content=""):
    path = tmp_path / "ei-view" / "src" / "views" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_discovers_hierarchy_form_code_and_all(tmp_path):
    make_page(tmp_path, "cashFlow/fund/list.vue", '<PurvarSubTitle title="基金现金流"/><script>const formCode: string = "FUND_CASH"</script>')
    items = discover_modules(tmp_path)
    assert items[0].id == "ALL"
    assert items[1].path == ("cash Flow", "基金现金流")
    assert items[1].form_code == "FUND_CASH"
    assert items[1].runnable


def test_search_matches_parent_child_and_code(tmp_path):
    make_page(tmp_path, "project/pool/index.vue", 'const FORM_CODE = "PROJECT_POOL"')
    items = discover_modules(tmp_path)
    assert [item.form_code for item in search_modules(items, "project_pool")] == ["PROJECT_POOL"]
    assert [item.form_code for item in search_modules(items, "project pool")] == ["PROJECT_POOL"]


def test_components_are_not_modules(tmp_path):
    make_page(tmp_path, "fund/components/index.vue", 'const FORM_CODE = "INNER"')
    assert [item.id for item in discover_modules(tmp_path)] == ["ALL"]


def test_bound_title_is_not_treated_as_a_literal_child_module(tmp_path):
    make_page(
        tmp_path,
        "selfManagedFunds/fundsDaily/dailyMeeting/index.vue",
        '<PurvarSubTitle :title="dailyMeetingTitle"/>',
    )
    item = discover_modules(tmp_path)[1]
    assert item.name == "daily Meeting"
    assert item.path == ("self Managed Funds", "funds Daily", "daily Meeting")


def test_prefers_runnable_list_when_directory_has_index_and_list(tmp_path):
    make_page(tmp_path, "fund/report/index.vue", "<template><RouterView /></template>")
    make_page(
        tmp_path,
        "fund/report/list.vue",
        '<PurvarSubTitle title="投后管理报告"/><script>const FORM_CODE = "FUND_REPORT"</script>',
    )
    items = discover_modules(tmp_path)
    assert len(items) == 2
    assert items[1].id == "fund/report/list"
    assert items[1].path == ("fund", "投后管理报告")
    assert items[1].runnable


def test_detects_standard_add_capability(tmp_path):
    make_page(
        tmp_path,
        "base/manage/index.vue",
        "const openAddDialog = () => {}; const dialogConfirm = () => {};",
    )
    assert discover_modules(tmp_path)[1].supports_add


def test_does_not_treat_inline_quick_edit_as_crud_add(tmp_path):
    make_page(tmp_path, "base/label/index.vue", "const showInput = () => {}")
    assert not discover_modules(tmp_path)[1].supports_add


def test_discovers_static_page_buttons_as_operation_nodes(tmp_path):
    make_page(
        tmp_path,
        "project/pool/index.vue",
        """
        <el-button type="primary">查询</el-button>
        <el-button>重置</el-button>
        <el-button>新增</el-button>
        <el-button link>编辑</el-button>
        <el-button link>删除</el-button>
        <el-button>保存</el-button>
        const FORM_CODE = "PROJECT_POOL"
        """,
    )

    items = discover_modules(tmp_path)
    page = items[1]
    actions = items[2:]

    assert [item.operation for item in actions] == ["查询", "重置", "新增", "编辑", "删除"]
    assert all(item.path[:-1] == page.path for item in actions)
    assert all(item.component == page.component for item in actions)
    assert all(item.runnable for item in actions)


def test_discovers_static_values_from_dynamic_button_text(tmp_path):
    make_page(
        tmp_path,
        "base/settings/index.vue",
        '<el-button>{{ editing ? "取消编辑" : "编辑" }}</el-button>',
    )

    assert [item.operation for item in discover_modules(tmp_path)[2:]] == ["取消编辑", "编辑"]


def test_discovers_titled_export_component_as_operation(tmp_path):
    make_page(
        tmp_path,
        "cashFlow/fund/list.vue",
        '<PurvarExport title="导出" @click="handleExport" />',
    )

    assert [item.operation for item in discover_modules(tmp_path)[2:]] == ["导出"]


def test_does_not_promote_buttons_from_sibling_or_nested_components(tmp_path):
    make_page(tmp_path, "fund/baseInfo/index.vue", "<el-button>编辑</el-button>")
    make_page(tmp_path, "fund/baseInfo/Modify.vue", "<el-button>更新工商信息</el-button>")
    make_page(tmp_path, "fund/baseInfo/components/History.vue", "<el-button>变更记录</el-button>")

    actions = [item.operation for item in discover_modules(tmp_path) if item.operation]

    assert actions == ["编辑"]


def test_discovers_buttons_from_a_transparent_local_page_wrapper(tmp_path):
    make_page(
        tmp_path,
        "buildProject/before/projectDecision/index.vue",
        '''
        <template><DecisionList :business-id="businessId" /></template>
        <script setup>
        import DecisionList from "./DecisionList.vue";
        </script>
        ''',
    )
    make_page(
        tmp_path,
        "buildProject/before/projectDecision/DecisionList.vue",
        '''
        <template>
          <div v-if="$hasButton('buildProjectDecisionAdd')">
            <el-button @click="handleAdd">新增</el-button>
          </div>
        </template>
        ''',
    )

    action = next(item for item in discover_modules(tmp_path) if item.operation == "新增")

    assert action.permission_codes == ("buildProjectDecisionAdd",)


def test_does_not_promote_buttons_from_a_regular_imported_child_component(tmp_path):
    make_page(
        tmp_path,
        "fund/baseInfo/index.vue",
        '''
        <template><main><History /></main></template>
        <script setup>import History from "./History.vue";</script>
        ''',
    )
    make_page(tmp_path, "fund/baseInfo/History.vue", "<el-button>变更记录</el-button>")

    assert not [item for item in discover_modules(tmp_path) if item.operation]


def test_discovers_actionable_sections_in_referenced_add_dialog_components(tmp_path):
    make_page(
        tmp_path,
        "projectResourcePool/index.vue",
        '''
        <el-button @click="openAddDialog">新增</el-button>
        <script>
        const FORM_CODE = "PROJECT_POOL";
        const openAddDialog = () => {
          dialogProps.value = {
            componentPath: "projectResourcePool/Modify",
            dialogTitle: "新增资源池企业",
          };
        };
        </script>
        ''',
    )
    make_page(
        tmp_path,
        "projectResourcePool/Modify.vue",
        '''
        <template><EnterpriseInfoTables /></template>
        <script setup>
        import EnterpriseInfoTables from "./components/EnterpriseInfoTables.vue";
        </script>
        ''',
    )
    make_page(
        tmp_path,
        "projectResourcePool/components/EnterpriseInfoTables.vue",
        '''
        <template>
          <section><div>{{ ownershipTitle }}</div><el-button @click="addOwnership">新增</el-button></section>
          <section><div>{{ entInvestTitle }}</div><el-button @click="addEntInvest">新增</el-button></section>
        </template>
        <script setup>
        defineProps({
          ownershipTitle: { type: String, default: "股权结构" },
          entInvestTitle: { type: String, default: "对外投资" },
        });
        </script>
        ''',
    )

    actions = [item for item in discover_modules(tmp_path) if item.operation_path]

    assert [item.path[-3:] for item in actions] == [
        ("新增资源池企业", "股权结构", "新增"),
        ("新增资源池企业", "对外投资", "新增"),
    ]
    assert all(item.operation_path == ("新增", item.path[-2], "新增") for item in actions)


def test_discovers_static_dialog_sections_through_a_transparent_wrapper(tmp_path):
    make_page(
        tmp_path,
        "buildProject/before/projectDecision/index.vue",
        '''
        <template><DecisionList /></template>
        <script setup>
        import DecisionList from "./DecisionList.vue";
        const FORM_CODE = "PROJECT_DECISION";
        </script>
        ''',
    )
    make_page(
        tmp_path,
        "buildProject/before/projectDecision/DecisionList.vue",
        '''
        <template>
          <el-button @click="search">查询</el-button>
          <div v-if="$hasButton('project_decision_add')">
            <el-button @click="handleAdd">新增</el-button>
          </div>
          <el-button v-if="$hasButton('project_decision_edit')" @click="handleEdit">编辑</el-button>
        </template>
        <script setup>
        const modifyComponentPath =
          "buildProject/before/projectDecision/DecisionForm";
        const unusedComponentPath =
          "buildProject/before/projectDecision/UnreferencedForm";
        const openDialog = (options) => {
          dialogProps.value = {
            componentPath: modifyComponentPath,
            dialogTitle: options.title,
          };
        };
        openDialog({ title: "新增项目决策", mode: "add" });
        openDialog({ title: "编辑项目决策", mode: "edit" });
        </script>
        ''',
    )
    make_page(
        tmp_path,
        "buildProject/before/projectDecision/DecisionForm.vue",
        '''
        <template>
          <PurvarSubTitle title="可行性研究与建设方案" />
          <el-input />
          <PurvarSubTitle title="预算及资金来源明细" />
          <el-button @click="addFinanceSource">新增</el-button>
          <el-button @click="removeFinanceSource">删除</el-button>
          <PurvarSubTitle title="项目融资明细" />
          <el-button @click="addProjectFinance">新增</el-button>
        </template>
        ''',
    )
    make_page(
        tmp_path,
        "buildProject/before/projectDecision/UnreferencedForm.vue",
        '<PurvarSubTitle title="未引用分区"/><el-button>新增</el-button>',
    )

    actions = [item for item in discover_modules(tmp_path) if item.operation]

    assert [(item.operation, item.operation_path) for item in actions] == [
        ("查询", ()),
        ("新增", ()),
        ("新增", ("新增", "预算及资金来源明细", "新增")),
        ("删除", ("新增", "预算及资金来源明细", "删除")),
        ("新增", ("新增", "项目融资明细", "新增")),
        ("编辑", ()),
        ("新增", ("编辑", "预算及资金来源明细", "新增")),
        ("删除", ("编辑", "预算及资金来源明细", "删除")),
        ("新增", ("编辑", "项目融资明细", "新增")),
    ]
    nested = [item for item in actions if item.operation_path]
    assert [item.path[-4:] for item in nested] == [
        ("新增", "新增项目决策", "预算及资金来源明细", "新增"),
        ("新增", "新增项目决策", "预算及资金来源明细", "删除"),
        ("新增", "新增项目决策", "项目融资明细", "新增"),
        ("编辑", "编辑项目决策", "预算及资金来源明细", "新增"),
        ("编辑", "编辑项目决策", "预算及资金来源明细", "删除"),
        ("编辑", "编辑项目决策", "项目融资明细", "新增"),
    ]
    assert [item.permission_codes for item in nested] == [
        ("project_decision_add",),
        ("project_decision_add",),
        ("project_decision_add",),
        ("project_decision_edit",),
        ("project_decision_edit",),
        ("project_decision_edit",),
    ]
    assert not any("未引用分区" in item.path for item in actions)


def test_static_subtitle_actions_stop_at_an_unresolved_title_boundary(tmp_path):
    make_page(
        tmp_path,
        "projectDecision/index.vue",
        '''
        <el-button>新增</el-button>
        <script>
        const props = {
          componentPath: "projectDecision/DecisionForm",
          dialogTitle: "新增项目决策",
        };
        </script>
        ''',
    )
    make_page(
        tmp_path,
        "projectDecision/DecisionForm.vue",
        '''
        <PurvarSubTitle title="预算明细" />
        <el-button>新增</el-button>
        <PurvarSubTitle :title="runtimeOnlyTitle" />
        <el-button>不应串入预算明细</el-button>
        <PurvarSubTitle title="融资明细" />
        <el-button>新增</el-button>
        ''',
    )

    nested = [item for item in discover_modules(tmp_path) if item.operation_path]

    assert [item.operation_path for item in nested] == [
        ("新增", "预算明细", "新增"),
        ("新增", "融资明细", "新增"),
    ]
