from ei_ui_smoke.source_form import (
    SourceBranchCandidate,
    SourceDetailEndpoint,
    discover_custom_form_fields,
    discover_form_branch_candidates,
    discover_form_contract,
    discover_form_detail_endpoint,
)


def test_follows_dialog_component_and_extracts_interactive_fields(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views"
    (views / "fund/components").mkdir(parents=True)
    (views / "fund/index.vue").write_text('componentPath: "fund/components/Form"', encoding="utf-8")
    (views / "fund/components/Form.vue").write_text('''
      <PurvarCol field-code="fundName" :label="fieldLabel('fundName', '基金名称')">
        <el-form-item prop="fundName"><el-input /></el-form-item>
      </PurvarCol>
      <PurvarCol field-code="readonly"><el-form-item><span>只读</span></el-form-item></PurvarCol>
    ''', encoding="utf-8")
    assert discover_custom_form_fields(tmp_path, "fund/index") == [("fundName", "基金名称", False)]


def test_resolves_runtime_src_ei_view_alias(tmp_path):
    src_views = tmp_path / "ei-view" / "src" / "views"
    src_ei_views = tmp_path / "ei-view" / "srcEi" / "views"
    (src_views / "resource").mkdir(parents=True)
    (src_ei_views / "resource").mkdir(parents=True)
    (src_views / "resource/index.vue").write_text('''
      <PurvarCol field-code="wrong" label="错误字段">
        <el-form-item prop="wrong"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")
    (src_ei_views / "resource/index.vue").write_text(
        'componentPath: "resource/Modify"', encoding="utf-8"
    )
    (src_ei_views / "resource/Modify.vue").write_text('''
      <PurvarCol field-code="projName" label="项目名称">
        <el-form-item prop="projName"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "/srcEi/views/resource/index") == [
        ("projName", "项目名称", False),
    ]


def test_prefers_local_dialog_component_over_unrelated_component_path(tmp_path):
    views = tmp_path / "ei-view" / "srcEi" / "views" / "resource"
    followup = tmp_path / "ei-view" / "srcEi" / "views" / "followup"
    (tmp_path / "ei-view" / "src" / "views").mkdir(parents=True)
    views.mkdir(parents=True)
    followup.mkdir(parents=True)
    (views / "index.vue").write_text('''
      localComponent: "Modify",
      componentPath: "followup/Modify",
    ''', encoding="utf-8")
    (views / "Modify.vue").write_text('''
      <PurvarCol field-code="projName" label="项目名称">
        <el-form-item prop="projName"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")
    (followup / "Modify.vue").write_text('''
      <PurvarCol field-code="wrong" label="错误字段">
        <el-form-item prop="wrong"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "/srcEi/views/resource/index") == [
        ("projName", "项目名称", False),
    ]


def test_prefers_explicit_add_dialog_component_when_page_has_multiple_dialogs(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "resource"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <script setup>
      const openAddDialog = () => {
        dialogProps.value = {
          componentPath: "resource/AddForm",
        };
      };
      const openStoreDialog = () => {
        dialogProps.value = {
          componentPath: "resource/StoreForm",
        };
      };
      </script>
    ''', encoding="utf-8")
    (views / "AddForm.vue").write_text('''
      <PurvarCol field-code="projObjectName" label="企业全称">
        <el-form-item prop="projObjectName"><QccSelect /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")
    (views / "StoreForm.vue").write_text('''
      <PurvarCol field-code="projectName" label="项目名称">
        <el-form-item prop="projectName"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "resource/index") == [
        ("projObjectName", "企业全称", True),
    ]


def test_missing_local_dialog_does_not_fall_back_to_unrelated_component_path(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views"
    (views / "resource").mkdir(parents=True)
    (views / "followup").mkdir(parents=True)
    (views / "resource/index.vue").write_text('''
      localComponent: "MissingModify",
      componentPath: "followup/Modify",
    ''', encoding="utf-8")
    (views / "followup/Modify.vue").write_text('''
      <PurvarCol field-code="wrong" label="错误字段">
        <el-form-item prop="wrong"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "resource/index") == []


def test_extracts_plain_purvar_col_label_and_form_prop(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "investor"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol label="投资人名称" :required="true">
        <el-form-item prop="investorName"><QccSelect /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "investor/index") == [
        ("investorName", "投资人名称", True),
    ]


def test_marks_any_qcc_select_as_qcc_remote(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views"
    (views / "company").mkdir(parents=True)
    (views / "company/index.vue").write_text('''
      <PurvarCol field-code="counterparty" :label="fieldLabel('counterparty', '交易对手')">
        <el-form-item prop="counterparty"><QccSelect /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")
    assert discover_custom_form_fields(tmp_path, "company/index") == [("counterparty", "交易对手", True)]


def test_extracts_plain_element_form_items_from_v_model(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "basicConfig/manage"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <el-form-item label="分类编码"><el-input v-model="categoryForm.code" /></el-form-item>
      <el-form-item label="分类名称"><el-input v-model="categoryForm.name" /></el-form-item>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "basicConfig/manage/index") == [
        ("code", "分类编码", False), ("name", "分类名称", False),
    ]


def test_follows_local_form_component_import(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "fileType"
    (views / "components").mkdir(parents=True)
    (views / "index.vue").write_text('''
      <FileTypeForm />
      <script setup>import FileTypeForm from "./components/FileTypeForm.vue";</script>
    ''', encoding="utf-8")
    (views / "components/FileTypeForm.vue").write_text('''
      <el-form-item label="ID" prop="itemId"><el-input v-model="form.itemId" /></el-form-item>
      <el-form-item label="名称" prop="itemName"><el-input v-model="form.itemName" /></el-form-item>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "fileType/index") == [
        ("itemId", "ID", False), ("itemName", "名称", False),
    ]


def test_follows_single_local_field_component_without_form_suffix(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "buildProject/establishment"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <template>
        <PurvarSubTitle title="基本信息" />
        <el-button>编辑</el-button>
        <Modify :is-edit="isEditing" />
      </template>
      <script setup>
      import PurvarSubTitle from "@/commonModules/components/PurvarSubTitle/index.vue";
      import Modify from "./Modify.vue";
      </script>
    ''', encoding="utf-8")
    (views / "Modify.vue").write_text('''
      <PurvarCol field-code="isGmoDecision" :label="fieldLabel('isGmoDecision', '是否需总经办决策')">
        <el-form-item prop="isGmoDecision"><PurvarCodeSelect selector-type="el-radio" /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "buildProject/establishment/index") == [
        ("isGmoDecision", "是否需总经办决策", False),
    ]


def test_follows_transparent_wrapper_and_static_component_path_constant(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "projectDecision"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <template><DecisionList /></template>
      <script setup>import DecisionList from "./DecisionList.vue";</script>
    ''', encoding="utf-8")
    (views / "DecisionList.vue").write_text('''
      <script setup lang="ts">
      const modifyComponentPath = "projectDecision/DecisionForm";
      const dialogProps = { componentPath: modifyComponentPath };
      </script>
    ''', encoding="utf-8")
    (views / "DecisionForm.vue").write_text('''
      <PurvarCol field-code="matterName" :label="fieldLabel('matterName', '事项名称')">
        <el-form-item prop="matterName"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "projectDecision/index") == [
        ("matterName", "事项名称", False),
    ]


def test_table_slot_dynamic_props_use_column_labels_and_stable_row_wildcard(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "projectDecision"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <template>
        <PurvarTable :columns="financeTableColumns">
          <template #amount="{ scope }">
            <el-form-item :prop="`financeSources.${scope.$index}.amount`">
              <el-input v-model="scope.row.amount" />
            </el-form-item>
          </template>
        </PurvarTable>
      </template>
      <script setup>
      const financeTableColumns = [
        { prop: "amount", label: "预算金额（万元）" },
      ];
      </script>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "projectDecision/index") == [
        ("financeSources.*.amount", "预算金额（万元）", False),
    ]


def test_expands_static_v_for_field_array_into_stable_source_fields(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "risk"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <template>
        <PurvarCol v-for="field in textFields" :key="field.fieldCode"
          :field-code="field.fieldCode" :label="fieldLabel(field.fieldCode, field.label)">
          <el-form-item :prop="field.fieldCode"><el-input v-model="form[field.fieldCode]" /></el-form-item>
        </PurvarCol>
      </template>
      <script setup lang="ts">
      const textFields: Array<{ fieldCode: string; label: string }> = [
        { fieldCode: "riskSummary", label: "风险概况" },
        { fieldCode: "riskReason", label: "发生原因" },
      ];
      </script>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "risk/index") == [
        ("riskSummary", "风险概况", False),
        ("riskReason", "发生原因", False),
    ]


def test_does_not_guess_between_multiple_field_bearing_dialog_components(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "ambiguous"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      componentPath: "ambiguous/FirstForm",
      componentPath: "ambiguous/SecondForm",
    ''', encoding="utf-8")
    for name, code in (("FirstForm", "firstName"), ("SecondForm", "secondName")):
        (views / f"{name}.vue").write_text(f'''
          <PurvarCol field-code="{code}" label="名称">
            <el-form-item prop="{code}"><el-input /></el-form-item>
          </PurvarCol>
        ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "ambiguous/index") == []


def test_discovers_direct_visible_and_required_branch_conditions(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "investment"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol field-code="tradeType"
        v-if="formData.investType === 'NON_EQUITY'" label="交易类型">
        <el-form-item prop="tradeType"><el-select /></el-form-item>
      </PurvarCol>
      <PurvarCol field-code="currency"
        v-show='"FOREIGN" !== model.investScope' label="币种">
        <el-form-item prop="currency"
          :required="dialogForm.tradeType == 'CROSS_BORDER'">
          <el-select />
        </el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_form_branch_candidates(tmp_path, "investment/index") == [
        SourceBranchCandidate(
            driver_field="investType",
            operator="eq",
            value="NON_EQUITY",
            affected_field="tradeType",
            effect="visible",
        ),
        SourceBranchCandidate(
            driver_field="investScope",
            operator="neq",
            value="FOREIGN",
            affected_field="currency",
            effect="visible",
        ),
        SourceBranchCandidate(
            driver_field="tradeType",
            operator="eq",
            value="CROSS_BORDER",
            affected_field="currency",
            effect="required",
        ),
    ]


def test_branch_discovery_retains_runtime_hints_for_complex_conditions(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "investment"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol field-code="combined"
        v-if="formData.kind === 'A' && formData.region === 'CN'" label="组合条件">
        <el-form-item prop="combined"><el-input /></el-form-item>
      </PurvarCol>
      <PurvarCol field-code="computed" v-show="showComputed(formData.kind)" label="计算条件">
        <el-form-item prop="computed" :required="requiredFlag"><el-input /></el-form-item>
      </PurvarCol>
      <PurvarCol field-code="dynamic" v-if="formData.kind === option.value" label="动态值">
        <el-form-item prop="dynamic"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_form_branch_candidates(tmp_path, "investment/index") == [
        SourceBranchCandidate("kind", "runtime", "", "combined", "visible"),
        SourceBranchCandidate("region", "runtime", "", "combined", "visible"),
        SourceBranchCandidate("kind", "runtime", "", "computed", "visible"),
        SourceBranchCandidate("kind", "runtime", "", "dynamic", "visible"),
    ]


def test_branch_candidates_are_deduplicated_in_source_order(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "investment"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol field-code="first" v-if="form.kind === 'A'" label="第一字段">
        <el-form-item prop="first" v-show="form.kind === 'A'"><el-input /></el-form-item>
      </PurvarCol>
      <el-form-item prop="second" label="第二字段" v-if="form.kind !== 'B'">
        <el-input v-model="form.second" />
      </el-form-item>
    ''', encoding="utf-8")

    assert discover_form_branch_candidates(tmp_path, "investment/index") == [
        SourceBranchCandidate("kind", "eq", "A", "first", "visible"),
        SourceBranchCandidate("kind", "neq", "B", "second", "visible"),
    ]


def test_branch_discovery_rejects_dynamic_identity_and_uses_primary_v_model(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "investment"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol :field-code="field.fieldCode"
        v-if="form.kind === 'DYNAMIC'" label="动态字段">
        <el-form-item :prop="field.fieldCode">
          <el-input v-model:ent-id="form.companyId" />
        </el-form-item>
      </PurvarCol>
      <el-form-item label="备注" v-if="form.kind === 'NOTE'">
        <el-input v-model:ent-id="form.companyId" v-model="form.note" />
      </el-form-item>
    ''', encoding="utf-8")

    assert discover_form_branch_candidates(tmp_path, "investment/index") == [
        SourceBranchCandidate("kind", "eq", "NOTE", "note", "visible"),
    ]


def test_branch_discovery_uses_the_explicit_add_dialog_and_preserves_legacy_fields(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "resource"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <script setup>
      const openAddDialog = () => {
        dialogProps.value = { componentPath: "resource/AddForm" };
      };
      const openEditDialog = () => {
        dialogProps.value = { componentPath: "resource/EditForm" };
      };
      </script>
    ''', encoding="utf-8")
    (views / "AddForm.vue").write_text('''
      <PurvarCol field-code="companyName"
        v-if="formData.investType === 'EQUITY'" label="公司全称">
        <el-form-item prop="companyName"><QccSelect /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")
    (views / "EditForm.vue").write_text('''
      <PurvarCol field-code="wrongField"
        v-if="formData.investType === 'OTHER'" label="错误字段">
        <el-form-item prop="wrongField"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")

    assert discover_custom_form_fields(tmp_path, "resource/index") == [
        ("companyName", "公司全称", True),
    ]
    assert discover_form_branch_candidates(tmp_path, "resource/index") == [
        SourceBranchCandidate(
            "investType", "eq", "EQUITY", "companyName", "visible",
        ),
    ]


def test_discovers_imported_resource_pool_query_detail_endpoint(tmp_path):
    view_root = tmp_path / "ei-view" / "src"
    views = view_root / "views" / "projectResourcePool"
    api = view_root / "api"
    views.mkdir(parents=True)
    api.mkdir(parents=True)
    (tmp_path / "ei-view" / ".env").write_text(
        "VITE_APP_BASE_API=/ezgo\n", encoding="utf-8",
    )
    (views / "index.vue").write_text('''
      <script setup>
      const openAddDialog = () => {
        dialogProps.value = { localComponent: "Modify" };
      };
      const openStoreDialog = () => {
        dialogProps.value = { localComponent: "putStore" };
      };
      </script>
    ''', encoding="utf-8")
    (views / "Modify.vue").write_text('''
      <template>
        <PurvarCol field-code="projObjectName" label="企业全称">
          <el-form-item prop="projObjectName"><el-input /></el-form-item>
        </PurvarCol>
      </template>
      <script setup lang="ts">
      import ProjectAPI from "@/api/project";
      const loadRecord = (id: string) => ProjectAPI.projStorageDetail(id);
      const saveRecord = (data: object) => ProjectAPI.projStorageAdd(data);
      </script>
    ''', encoding="utf-8")
    (views / "putStore.vue").write_text('''
      <PurvarCol field-code="storeReason" label="入库原因">
        <el-form-item prop="storeReason"><el-input /></el-form-item>
      </PurvarCol>
    ''', encoding="utf-8")
    (api / "project.ts").write_text('''
      const ProjectAPI = {
        projStorageAdd: (data: object) => request({
          url: "/ei-service/projStorage/add",
          method: "post",
          data,
        }),
        projStorageDetail: (id: string, projId?: string) => request({
          url: "/ei-service/projStorage/detail",
          method: "get",
          params: { id, ...(projId ? { projId } : {}) },
        }),
      };
      export default ProjectAPI;
    ''', encoding="utf-8")

    endpoint = SourceDetailEndpoint(
        method="GET",
        path_template="/ei-service/projStorage/detail",
        id_location="query",
        id_query_key="id",
        api_base_path="/ezgo",
    )
    contract = discover_form_contract(tmp_path, "projectResourcePool/index")

    assert contract.fields == (("projObjectName", "企业全称", False),)
    assert contract.branch_candidates == ()
    assert contract.detail_endpoints == (endpoint,)
    assert discover_form_detail_endpoint(
        tmp_path, "projectResourcePool/index",
    ) == endpoint


def test_discovers_local_static_query_detail_endpoint(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "risk"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <template>
        <PurvarCol field-code="riskName" label="风险名称">
          <el-form-item prop="riskName"><el-input /></el-form-item>
        </PurvarCol>
      </template>
      <script setup>
      const createRisk = (data) => request({
        url: "/fi-service/risk/add", method: "post", data,
      });
      const readRisk = (recordId) => request({
        url: "/fi-service/risk/detail",
        method: "get",
        params: { id: recordId },
      });
      const save = (data) => createRisk(data);
      const load = (id) => readRisk(id);
      </script>
    ''', encoding="utf-8")

    assert discover_form_detail_endpoint(tmp_path, "risk/index") == SourceDetailEndpoint(
        method="GET",
        path_template="/fi-service/risk/detail",
        id_location="query",
        id_query_key="id",
    )


def test_discovers_static_path_id_detail_endpoint(tmp_path):
    views = tmp_path / "fi-view" / "src" / "views" / "decision"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol field-code="matterName" label="事项名称">
        <el-form-item prop="matterName"><el-input /></el-form-item>
      </PurvarCol>
      <script setup>
      const createDecision = (data) => request({
        url: "/fi-service/decision/create", method: "post", data,
      });
      const readDecision = (decisionId) => request({
        url: `/fi-service/decision/detail/${decisionId}`,
        method: "get",
      });
      createDecision(formData);
      readDecision(props.id);
      </script>
    ''', encoding="utf-8")

    assert discover_form_detail_endpoint(
        tmp_path, "decision/index",
    ) == SourceDetailEndpoint(
        method="GET",
        path_template="/fi-service/decision/detail/{business_id}",
        id_location="path",
    )


def test_detail_endpoint_discovery_rejects_ambiguous_detail_contracts(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "ambiguousApi"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol field-code="recordName" label="名称">
        <el-form-item prop="recordName"><el-input /></el-form-item>
      </PurvarCol>
      <script setup>
      const createRecord = (data) => request({
        url: "/ei-service/record/add", method: "post", data,
      });
      const readDetail = (id) => request({
        url: "/ei-service/record/detail", method: "get", params: { id },
      });
      const readById = (businessId) => request({
        url: "/ei-service/record/getById",
        method: "get",
        params: { businessId },
      });
      createRecord(formData);
      readDetail(props.id);
      readById(props.id);
      </script>
    ''', encoding="utf-8")

    assert discover_form_detail_endpoint(tmp_path, "ambiguousApi/index") is None


def test_detail_endpoint_discovery_rejects_dynamic_or_unrelated_endpoints(tmp_path):
    views = tmp_path / "ei-view" / "src" / "views" / "unsafeApi"
    views.mkdir(parents=True)
    (views / "index.vue").write_text('''
      <PurvarCol field-code="recordName" label="名称">
        <el-form-item prop="recordName"><el-input /></el-form-item>
      </PurvarCol>
      <script setup>
      const createRecord = (data) => request({
        url: "/ei-service/record/add", method: "post", data,
      });
      const dynamicDetail = (id) => request({
        url: detailBase + "/detail", method: "get", params: { id },
      });
      const unrelatedDetail = (id) => request({
        url: "/ei-service/other/detail", method: "get", params: { id },
      });
      createRecord(formData);
      dynamicDetail(props.id);
      unrelatedDetail(props.id);
      </script>
    ''', encoding="utf-8")

    assert discover_form_detail_endpoint(tmp_path, "unsafeApi/index") is None
