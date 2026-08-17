from ei_ui_smoke.source_form import discover_custom_form_fields


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
