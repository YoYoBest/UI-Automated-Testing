---
name: dynamic-form-runtime-fields
description: 当需要改造本仓库 Vue3 表单页面以支持配置端运行时字段时使用，尤其适用于非固定字段 fixedType=0、半固定字段 fixedType=2 的动态渲染、独立保存/更新/查询、dynamicFieldLabels 显示缓存，以及复用公共动态字段组件和工具的场景。
---

# 动态表单运行时字段

## 概览

使用这个 skill，把当前公共动态字段组件和“基金基本信息”中已经验证过的非固定字段、半固定字段改造模式接入到其他 Vue 表单页面。

核心规则：
- 固定字段继续走业务页面原有接口。
- `fixedType=0/2` 的字段统一走租户表单运行时接口：`/tenantForm/getFormData`、`/tenantForm/saveFormData`、`/tenantForm/updateFormData`。
- 动态字段渲染统一复用 `@/components/DynamicFormField/index.vue`，运行时字段工具统一复用 `@/utils/dynamicFormField.ts`。
- 表单配置和运行时字段接口的请求 body/params 不传 `tenantId`；租户从公共请求头或后端上下文解析。
- `formCode` 沿用目标表单原有业务编码；不要因为同模块列表按场景切换列表配置 code，就反推表单也要拆分 code。只有目标表单原实现、后端契约或 UIM 配置已经明确区分多个表单 code 时，才按既有上下文选择。
- 保留目标页面原有结构、分区、标题和保存编排。动态字段要按后端字段顺序放回所属页面区域，不要为了接入动态字段把多分区表单拍平成一个 `customFields` 渲染区。
- 不在业务页面目录新增重复的 `DynamicFormField.vue` 或 `dynamicFormField.ts` 副本；如公共组件或工具不能满足需求，先评估是否应扩展公共实现，并保持兼容。

## 优先阅读

开始改造前，先阅读这些本地文件。它们是当前实现的事实标准：

| 文件 | 用途 |
| --- | --- |
| `docs/features/dynamic-form/非固定字段保存查询接口使用文档.md` | 运行时接口契约，以及 `fixedType` 字段存储规则 |
| `docs/features/components/Purvar组件库.md` | 当前工程公共组件库用法 |
| `docs/features/components/字段组件说明文档.md` | 配置端 `fieldType` 与公共组件的映射关系 |
| `ei-view/src/api/formConfig.ts` | 租户表单配置和运行时数据接口封装 |
| `ei-view/src/utils/useDynamicFormConfig.ts` | 配置加载、显隐、必填规则、`customFields` 拆分逻辑 |
| `ei-view/src/utils/dynamicFormField.ts` | 动态字段取值、显示缓存、运行时 payload 构造工具 |
| `ei-view/src/components/DynamicFormField/index.vue` | 通用动态字段渲染组件 |
| `ei-view/src/views/selfManagedFunds/baseInfo/components/BaseInfoForm.vue` | 表单内集成动态字段的参考实现 |
| `ei-view/src/views/selfManagedFunds/baseInfo/index.vue` | 业务保存成功后再保存运行时字段的参考顺序 |

如果文档和代码不一致，先确认代码是否仍符合 `docs/features/dynamic-form/非固定字段保存查询接口使用文档.md`，再决定以哪一侧为准。

## 改造边界

- 不修改底层公共组件，例如 `PurvarSelectUser`、`PurvarCodeSelect`、`PurvarDepartment`、`PurvarLinkTag`、`PurvarLibrary`。
- 不复制公共动态字段组件和工具到业务目录；页面只通过 `@/components/DynamicFormField/index.vue` 和 `@/utils/dynamicFormField` 引用。
- 不把非固定字段、半固定字段提交到原业务保存接口。
- 不把固定字段提交到租户表单运行时接口。
- `dynamicFieldLabels` 只作为显示缓存，不作为字段真实保存值。字段自身保存值必须是 ID、编码、主键等唯一值。
- 运行时 `dataJson` 中的 `dynamicFieldLabels` 必须是 JSON 字符串，不能是嵌套对象。
- 更新运行时字段时，要提交完整的半固定字段集合，因为后端会整体重写 `params`。

## 改造流程

### 1. 确认表单和业务主键

先定位目标页面的表单组件，以及父级保存页面。记录这些信息：

| 信息 | 示例 |
| --- | --- |
| `formCode` | `FUND_BASICINFO` |
| 业务主键来源 | `savedId`、`detailId`、`props.id`、接口返回的 `response.id` |
| 原固定字段保存接口 | 例如 `FundAPI.add(payload)` |
| 原详情查询接口 | 例如 `FundAPI.getDetail(id)` |

运行时接口的 `businessId` 必须和业务数据主键保持一致。

如果列表页和表单页分别使用运行时配置，先分别确认各自的既有 `formCode`。列表配置 code 不等于表单运行时字段 code 的自动来源，不能仅根据 `bizType`、列表分支或菜单入口新增表单 code 分流。

### 2. 加载配置并渲染动态字段

在表单组件中接入 `useDynamicFormConfig`：

```ts
const FORM_CODE = "YOUR_FORM_CODE";

const {
  effectiveRules,
  fieldColStyle,
  fieldLabel,
  fieldPlaceholder,
  formConfig,
  getFieldSpan,
  initFormConfig,
  isFieldAlone,
  isFieldRequired,
  isFieldVisible,
} = useDynamicFormConfig<FormDataType>({
  formCode: FORM_CODE,
  defaultFieldOrder,
  rules,
  isEdit,
  isExisting: () => Boolean(props.id),
});

void initFormConfig();
```

`initFormConfig` 不接收 `tenantId` 参数，底层配置接口只传 `formCode`。不要写 `initFormConfig(detail.tenantId)`，也不要在业务页面手动从 `localStorage` 取 `tenantId` 拼进动态表单配置请求。

只在目标页面区域内，用 `DynamicFormField.vue` 渲染 `formConfig.customFields`：

```ts
import DynamicFormField from "@/components/DynamicFormField/index.vue";
```

```vue
<DynamicFormField
  v-for="field in formConfig.customFields"
  :key="field.fieldCode"
  :ref="setDynamicFieldRef"
  :field="field"
  :form-data="formData"
  :is-edit="isEdit"
  :function-data-id="attachmentDataId"
  :module-data-id="attachmentDataId"
  :function-data-name="functionDataName"
  :field-label="fieldLabel"
  :field-placeholder="fieldPlaceholder"
  :is-field-alone="isFieldAlone"
  :is-field-required="isFieldRequired"
  :get-field-span="getFieldSpan"
  :field-col-style="fieldColStyle"
/>
```

如果表单有多个 `PurvarSubTitle`、多个 `el-row` 或明显的业务区块，先判断动态字段属于上方内容还是下方内容，再放入对应区域。可以用 `getFieldOrder` 与区块锚点字段比较，或按本页面既有字段顺序拆分 `formConfig.customFields`，但不要改变原有区块结构。

动态字段 ref 采集逻辑参考 `BaseInfoForm.vue`，需要支持：
- `syncFieldValue`
- `syncUploadFile`
- `checkFile`
- `saveFile`

#### 通用文件库组件渲染规则

当动态字段是 `PurvarLibrary-FILE_LIBRARY` 或配置端 `fieldType=FILE_LIBRARY` 时，不要把它放进 `PurvarCol` 或 `el-form-item`。`PurvarLibrary` 自身已经包含附件项标题，外层再显示字段标题会出现双标题。

参考 `DynamicFormField.vue` 的结构做顶层分支：

```vue
<template>
  <el-col
    v-if="isFileLibraryField"
    :span="fileLibraryColSpan"
    :style="fileLibraryColStyle"
  >
    <PurvarLibrary
      ref="fileRef"
      :is-edit="!isReadonly"
      :is-form="true"
      :function-type="libraryFunctionType"
      :function-data-id="libraryFunctionDataId"
      :function-data-name="libraryFunctionDataName"
      :function-rela-data-id="libraryFunctionRelaDataId"
      :module-data-id="libraryModuleDataId"
      :stage-type="libraryStageType"
      :stage-step="libraryStageStep"
      :parent-id="libraryParentId"
      :rela-parent-id="libraryRelaParentId"
      :item-type="libraryItemType"
      :data-type="libraryDataType"
      :platform="libraryPlatform"
      :limit="libraryLimit"
    />
  </el-col>

  <PurvarCol
    v-else
    :field-code="field.fieldCode"
    :style="fileLibraryColStyle"
  >
    <!-- 普通动态字段仍保留字段标题、必填、校验布局 -->
  </PurvarCol>
</template>
```

文件库字段的 `span` 和 `style` 要复用父表单传入的 `getFieldSpan`、`fieldColStyle`，保持与固定字段文件库一致的宽度、排序、显隐逻辑。普通动态字段也要通过 `fieldColStyle(field.fieldCode)` 传给 `PurvarCol`，保证后端字段顺序和显隐配置生效。不要通过给 `PurvarLibrary` 传 `span1=0` 隐藏内部标题；这会改变组件自身布局，不等同于固定字段的实现方式。

### 3. 初始化动态字段默认值

复用 `ensureDynamicFieldDefaults` 这类模式：

- 遍历 `formConfig.customFields`。
- 当表单数据中缺少对应字段时，用 `getDynamicFieldDefaultValue(field)` 设置默认值。
- 当字段存在 `extraBindings` 时，把目标字段初始化为 `""`。
- 表单默认数据中保留 `dynamicFieldLabels: {}`。

### 4. 固定字段详情回填后，再查询运行时字段

原业务详情数据回填完成后，再调用运行时查询接口：

```ts
const fetchDynamicRuntimeData = async (businessId = dynamicRuntimeBusinessId.value) => {
  if (!hasDynamicRuntimeFields.value || !businessId) return null;

  const response = await getFormData({
    formCode: FORM_CODE,
    businessId,
  });

  const runtimeData = response?.data?.data;
  if (!runtimeData?.dataJson) return null;

  applyDynamicRuntimeData(runtimeData.dataJson);
  hasDynamicRuntimeRecord.value = true;
  dynamicRuntimeDataKey.value = String(runtimeData.dataKey || "");
  return runtimeData;
};
```

回填运行时数据时使用：
- `parseDynamicRuntimeDataJson`
- `normalizeDynamicFieldLabels`

因为配置加载和详情加载经常不是同一时序，所以要在详情加载完成后、`formConfig.customFields` 变化后都能触发运行时数据回填。

### 5. 固定字段保存前，移除运行时字段

调用原业务保存接口前，先从 payload 中移除非固定字段和半固定字段：

```ts
const payload = removeDynamicRuntimeFields(
  { ...toRaw(formData.value) },
  formConfig.value.customFields,
  {
    keepFieldCodes: [
      ...defaultFieldOrder,
      ...Object.keys(formConfig.value.fixedFields),
      "id",
      "fundId",
      "fundType",
    ],
  }
);
```

然后继续执行页面原有的 trim、格式转换、文件字段处理、固定字段转换等逻辑。

### 6. 获取业务主键后，再保存运行时字段

在表单组件中暴露保存运行时字段的方法：

```ts
const saveDynamicRuntimeData = async (businessId: string) => {
  if (!hasDynamicRuntimeFields.value || !businessId) return;

  const request = {
    formCode: FORM_CODE,
    businessId,
    dataKey: dynamicRuntimeDataKey.value || undefined,
    dataJson: stringifyDynamicRuntimeDataJson(formData.value, formConfig.value.customFields),
  };

  if (hasDynamicRuntimeRecord.value) {
    await updateFormData(request);
    return;
  }

  await saveFormData(request);
  hasDynamicRuntimeRecord.value = true;
};
```

在父级页面保存流程中，等固定字段业务接口成功后再调用：

```ts
const response = await BusinessAPI.save(payload);
const savedId = String(response?.id || response?.businessId || payload.id || detailId.value || "");

if (savedId && formRef.value?.saveDynamicRuntimeData) {
  await formRef.value.saveDynamicRuntimeData(savedId);
}
```

除非模块本身有更严格的保存顺序，否则运行时字段保存应放在固定字段接口成功之后、附件保存或成功提示之前。
如果原模块是弹窗表单组件内部 `saveHandle` 自己完成业务保存，就在原 `saveHandle` 业务保存成功后保存运行时字段；不要为了动态字段强行改成父级统一保存。

### 7. 提交校验前同步动态组件值

在 `formRef.validate()` 前，先同步动态组件内部值：

```ts
for (const dynamicFieldRef of dynamicFieldRefs.value) {
  await dynamicFieldRef.syncFieldValue?.();
  await dynamicFieldRef.syncUploadFile?.();
}
```

这一步对选人、码值、机构、树、地址、文件等组件很关键，用来确保最终提交的是 ID、编码、主键，并同步 `dynamicFieldLabels` 显示缓存。

## 运行时 payload 规则

使用 `@/utils/dynamicFormField.ts` 中的工具方法，不要手写临时 JSON 拼装逻辑：

```ts
import {
  buildDynamicRuntimeDataJson,
  parseDynamicRuntimeDataJson,
  removeDynamicRuntimeFields,
  stringifyDynamicRuntimeDataJson,
} from "@/utils/dynamicFormField";
```

| 工具方法 | 用途 |
| --- | --- |
| `buildDynamicRuntimeDataJson(formData, fields)` | 构造只包含 `fixedType=0/2` 字段和字符串化 `dynamicFieldLabels` 的对象 |
| `stringifyDynamicRuntimeDataJson(formData, fields)` | 构造最终请求中的 `dataJson` 字符串 |
| `parseDynamicRuntimeDataJson(dataJson)` | 解析运行时查询结果，并解析内层显示缓存 |
| `removeDynamicRuntimeFields(payload, fields, options)` | 从固定字段保存 payload 中移除动态字段和额外绑定字段 |

期望的运行时请求结构：

```json
{
  "formCode": "YOUR_FORM_CODE",
  "businessId": "10001",
  "dataJson": "{\"custom_name\":\"value\",\"semi_user\":\"1001,1002\",\"dynamicFieldLabels\":\"{\\\"semi_user\\\":[{\\\"name\\\":\\\"张三\\\",\\\"value\\\":\\\"1001\\\"}]}\"}"
}
```

## 组件取值规则

| 组件类别 | 字段真实保存值 | `dynamicFieldLabels` 显示缓存 |
| --- | --- | --- |
| 选人组件 | 用户 ID | `{ name: userName, value: userId }[]` |
| 码值选择、码值标签 | 码值编码或值 | `{ name: codeName, value: codeValue }[]` |
| 机构、部门、组织 | 机构、部门、组织 ID | `{ name: label, value: id }[]` |
| 树形选择 | 节点值 | `{ name: nodeLabel, value: nodeValue }[]` |
| 地址组件 | 配置指定的保存值或 JSON | `{ name: province/city/area, value }[]` |
| 文件、附件上传 | 文件 ID 或组件约定值 | 按组件实际约定处理 |

如果组件事件只抛出了名称，没有抛出 ID，不要改公共组件；应在动态渲染层的事件适配、选项缓存或反查逻辑中补齐真实值。

## 常见问题

- 把 `dynamicFieldLabels` 作为对象直接放进 `dataJson`。后端期望它是字符串。
- 没有先查询运行时记录，就直接调用 `updateFormData`。应先查询；没有记录时调用 `saveFormData`。
- 更新时只提交某一个半固定字段，导致其他 `params` 字段被覆盖。
- 忘记从原业务保存 payload 中移除非固定字段、半固定字段。
- 忘记在校验前调用动态字段 ref 的 `syncFieldValue`。
- 在业务目录下复制 `DynamicFormField.vue` 或 `dynamicFormField.ts`，导致后续维护出现多份重复实现。
- 误以为业务详情接口会返回动态字段。固定字段详情接口只覆盖固定业务字段。
- 在不应该渲染 `formConfig.customFields` 的嵌入式表单里误用这套逻辑。需要用页面区域或 `showType` 做保护。
- `PurvarLibrary-FILE_LIBRARY` 仍包在 `PurvarCol` 中，导致外层字段标题和文件库内部标题同时出现。文件库动态字段要单独渲染为 `el-col + PurvarLibrary`。
- 为了解决双标题给 `PurvarLibrary` 强行传 `span1=0`。不要这样做；正确做法是去掉动态字段外层标题，保留文件库组件自身标题。

## 验证清单

至少做这些检查：

```bash
cd ei-view
node <小型辅助脚本>   # 验证运行时 dataJson：固定字段被排除，dynamicFieldLabels 被字符串化
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit --pretty false
```

如果完整 `vue-tsc` 因项目既有问题失败，需要筛选本次改造文件相关诊断，并同时说明：
- 全量检查失败的事实。
- 本次改造文件是否有新增错误。

同时手动检查网络请求：
- 固定字段保存接口 payload 中没有 `fixedType=0/2` 字段，也没有 `dynamicFieldLabels`。
- 表单配置、运行时查询、运行时保存/更新接口 payload 中不包含 `tenantId`。
- 表单 `formCode` 沿用原业务表单编码，没有因为列表配置 code、`bizType` 或菜单入口新增不必要分流。
- 多分区表单中的动态字段按后端顺序插入对应区域，没有破坏原页面结构。
- 运行时保存/更新接口 payload 中包含 `formCode`、`businessId`、字符串类型的 `dataJson`。
- `dataJson` 内层的 `dynamicFieldLabels` 是字符串。
- 查询响应 `data.dataJson` 能正确回填动态字段真实值和显示名称。
- 如果页面配置了 `FILE_LIBRARY` 动态字段，检查页面上只显示文件库组件自己的标题，不再显示动态字段外层标题。
