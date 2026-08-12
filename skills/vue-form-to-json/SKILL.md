---
name: vue-form-to-json
description: 将 Vue 3 + Element Plus + PurvarCol 的硬编码表单页面提取为 JSON 动态配置数组。当用户提到"表单转JSON"、"提取表单配置"、"form to JSON"、"生成表单JSON"、"分析表单字段"、"把表单变成配置"、要把 .vue 文件中的表单转为动态配置、或者要梳理一个表单组件中所有字段时使用。
---

# Vue 3 表单转 JSON 配置提取器

将 Vue 3 + Element Plus + Purvar 组件写死在页面里的表单字段，提取为后端配置化可用的 JSON 字段数组。

输入：一个 `.vue` 表单页面文件路径。

输出：
- `ei-view/jsonMock/<pageName>.json`
- `ei-view/jsonMock/<pageName>.md`

## 核心规则

- `fieldType` 使用新版“组件名-分类”格式，例如 `ElInput-TEXT`、`PurvarCodeSelect-SELECT`、`PurvarSelectUser-USER_SELECT`。
- 不再输出旧裸枚举，例如 `TEXT`、`SELECT`、`RADIO`、`USER_SELECT`、`ORG_SELECT`、`FILE_LIBRARY`。
- 字段设计的 `fieldType` 以 `docs/features/components/字段组件说明文档.md` 为准。
- 组件行为、保存格式和当前组件存在性以 `docs/features/components/Purvar组件库.md` 为准。
- 当前页面里已经存在硬编码渲染逻辑的字段，默认都是固定字段，`fixedType: 1`。

## 参考文件

提取前先查阅这些项目文件，确保与当前组件库一致：

| 文件 | 用途 |
|------|------|
| `docs/features/components/字段组件说明文档.md` | 新版 `fieldType` 命名规则、组件映射、默认 `dataType` |
| `docs/features/components/Purvar组件库.md` | 当前工程实际存在的 Purvar 公共组件、组件用法、保存格式 |
| `ei-view/src/commonModules/components/PurvarCol/index.vue` | 表单布局组件，确认 `is-alone` 和 label 行为 |
| `ei-view/src/commonModules/components/PurvarCodeSelect/index.vue` | 码值选择组件，确认 `selector-type`、`code-type`、多选行为 |
| `ei-view/src/commonModules/components/PurvarSelectUser/index.vue` | 选人组件，确认 `v-model`、`:user-ids`、`chooseUser` 行为 |
| `ei-view/src/commonModules/components/PurvarDepartment/index.vue` | 组织选择组件，确认 `selector-type` 和 label 绑定 |
| `ei-view/src/commonModules/components/PurvarLinkTag/index.vue` | 标签式码值选择，确认单选/多选行为 |
| `ei-view/src/commonModules/components/PurvarLibrary/index.vue` | 文件库组件，确认附件业务上下文参数 |
| `ei-view/src/commonModules/components/PurvarTextarea/index.vue` | 文本域组件，确认编辑/只读切换 |
| `ei-view/jsonMock/putStore.json` | 只作为输出结构参考；其中旧 `fieldType` 不能继续照抄 |

如果旧 mock 或旧页面和两份文档冲突，以 `docs/features/components/字段组件说明文档.md` 和 `docs/features/components/Purvar组件库.md` 为准。

## 输出 Schema

每个字段生成一个 JSON 对象：

```json
{
  "fieldCode": "fundName",
  "fieldName": "基金名称",
  "fieldType": "ElInput-TEXT",
  "fixedType": 1,
  "sortOrder": 3,
  "width": "50%",
  "required": 1,
  "readonly": 0,
  "locked": 0,
  "addVisible": 1,
  "editVisible": 1,
  "viewVisible": 1,
  "overrideType": "OVERRIDE",
  "propsJson": "",
  "linkageJson": ""
}
```

| 字段 | 说明 | 取值 |
|------|------|------|
| `fieldCode` | 字段编码，对应 `v-model="formData.xxx"` 的 key | 字符串 |
| `fieldName` | 显示名称，取自 `PurvarCol label` 或 `el-form-item label` | 字符串 |
| `fieldType` | 新版组件类型 | 见“组件类型映射表” |
| `fixedType` | 固定字段类型 | 0=非固定，1=固定，2=半固定 |
| `sortOrder` | 排序序号，按页面出现顺序从 1 递增 | 正整数 |
| `width` | 字段宽度 | `"50%"` 或 `"100%"` |
| `required` | 是否必填 | 0=否，1=是 |
| `readonly` | 是否只读 | 0=否，1=是 |
| `locked` | 是否锁定，锁定字段不渲染 | 0=否，1=是 |
| `addVisible` | 新增模式是否显示 | 0=否，1=是 |
| `editVisible` | 编辑模式是否显示 | 0=否，1=是 |
| `viewVisible` | 查看模式是否显示 | 0=否，1=是 |
| `overrideType` | 覆盖类型 | 固定 `"OVERRIDE"` |
| `propsJson` | 组件特殊属性 | JSON 字符串或空字符串 |
| `linkageJson` | 字段联动条件 | JSON 字符串或空字符串 |
| `formId` | 关联表单 ID | 仅确有业务需要时输出 |

## fixedType 语义

- `fixedType: 1`：固定字段。当前 Vue 模板中已经存在专门渲染逻辑的字段都按固定字段处理。
- `fixedType: 0`：非固定字段。完全由 JSON 配置驱动渲染。
- `fixedType: 2`：半固定字段。有代码逻辑，但部分 label、必填、显隐或组件属性允许配置覆盖。

执行“表单转 JSON”时，目标通常是把现有页面字段登记为配置端字段，所以默认输出 `fixedType: 1`。不要为了“以后动态渲染”把当前页面已有字段改成 `fixedType: 0`。

## width 映射规则

| 页面写法 | width |
|---|---|
| `<PurvarCol>` 默认半行 | `"50%"` |
| `<PurvarCol is-alone>` | `"100%"` |
| `<PurvarCol :is-alone="true">` | `"100%"` |
| `<el-col :span="24">` | `"100%"` |

如果是旧页面的 `el-col :span="12"`，按 `"50%"` 处理；`span=24` 按 `"100%"` 处理。新增配置只保留 50% 和 100% 两种宽度。

## 提取流程

### Step 1: 读取目标 Vue 文件

读取完整 `.vue` 文件。大文件先分段读取：
- 先读 `<template>`，定位字段顺序、label、组件、显隐条件。
- 再读 `<script>`，定位 `rules`、默认值、computed、事件处理和字段联动。

### Step 2: 识别表单字段

常见字段结构：

```vue
<PurvarCol label="基金注册状态" :required="isEdit">
  <el-form-item prop="registerStatus">
    <PurvarCodeSelect
      v-if="isEdit"
      v-model="formData.registerStatus"
      code-type="FUND_REG_STATUS"
      selector-type="el-radio"
    />
    <span v-else>{{ formData.registerStatusName }}</span>
  </el-form-item>
</PurvarCol>
```

```vue
<PurvarCol is-alone label="投资方向" :required="isEdit">
  <el-form-item prop="otherMatter">
    <PurvarTextarea v-model="formData.otherMatter" :is-edit="isEdit" />
  </el-form-item>
</PurvarCol>
```

```vue
<el-col :span="24">
  <PurvarLibrary
    ref="entFile"
    :is-edit="isEdit"
    :is-form="true"
    :function-data-id="props.id"
    function-type="XMGG"
  />
</el-col>
```

识别要点：
- `PurvarCol` 的 `label` 是 `fieldName`。
- `el-form-item prop` 通常与 `fieldCode` 一致，但最终以主 `v-model` 绑定为准。
- `PurvarSubTitle` 是分组标题，不生成字段。
- `PurvarTable`、`el-table` 内的 `scope.row.xxx` 是行内编辑字段，不计入主表单字段。
- 编辑态组件决定 `fieldType`，查看态 `<span>` 不参与 `fieldType` 判断。

### Step 3: 逐字段提取元数据

| 属性 | 提取规则 |
|------|----------|
| `fieldCode` | 从主 `v-model="formData.xxx"` 提取 `xxx` |
| `fieldName` | 优先取 `PurvarCol label`，其次取 `el-form-item label`，再从上下文推断 |
| `fieldType` | 根据编辑态组件按新版映射表输出 |
| `sortOrder` | 按字段在模板中出现顺序从 1 递增 |
| `width` | 按 `PurvarCol`/`el-col` 宽度规则输出 |
| `required` | 优先读取 `rules` 中 `required: true`，其次读取 `PurvarCol required` |
| `readonly` | 没有编辑态组件或组件显式 disabled 时为 1 |
| `locked` | 默认 0，除非字段明确不应渲染 |
| `fixedType` | 当前模板已有渲染逻辑时为 1 |
| `addVisible` | 默认 1，有新增模式隐藏条件时为 0 |
| `editVisible` | 默认 1，有编辑模式隐藏条件时为 0 |
| `viewVisible` | 默认 1，有查看模式隐藏条件时为 0 |

### Step 4: 提取多 v-model 绑定

当组件使用 `v-model:arg` 时，主 `v-model` 作为 `fieldCode`，辅助绑定写入 `propsJson.extraBindings`。

```vue
<QccSelect v-model="formData.mcName" v-model:ent-id="formData.mcId" />
```

输出要点：
- `fieldCode = "mcName"`
- `propsJson.extraBindings.entId = "mcId"`

```vue
<PurvarDepartment v-model="formData.deptId" v-model:label="formData.deptName" />
```

输出要点：
- `fieldCode = "deptId"`
- `propsJson.extraBindings.label = "deptName"`

选人、机构、码值、标签类字段如果存在名称回显字段，也应放入 `extraBindings`，例如：

```json
"{\"extraBindings\":{\"label\":\"managerDeptName\"}}"
```

## 组件类型映射表

新版 `fieldType` 必须使用“组件名-分类”。下表是提取时的默认映射：

| 页面组件或场景 | 输出 fieldType | 说明 |
|---|---|---|
| `el-input`，无 `type` 或 `type="text"` | `ElInput-TEXT` | 单行文本 |
| `el-input type="textarea"` | `PurvarTextarea-TEXTAREA` | 动态配置统一使用 PurvarTextarea 承载 |
| `PurvarTextarea` | `PurvarTextarea-TEXTAREA` | 文本域 |
| `el-input-number` | `ElInputNumber-NUMBER` | 数字输入 |
| `PurvarCodeSelect selector-type="el-select"` | `PurvarCodeSelect-SELECT` | 码值下拉单选 |
| `PurvarCodeSelect selector-type="el-select" :multiple="true"` | `PurvarCodeSelect-MULTI_SELECT` | 码值下拉多选 |
| `PurvarCodeSelect selector-type="el-radio"` | `PurvarCodeSelect-RADIO` | 码值平铺单选 |
| `PurvarCodeSelect selector-type="el-checkbox"` | `PurvarCodeSelect-CHECKBOX` | 码值平铺多选 |
| `el-select` 绑定码值或可配置 options | `PurvarCodeSelect-SELECT` 或 `PurvarCodeSelect-MULTI_SELECT` | 将静态 options 写入 `propsJson.options` |
| `el-radio-group` 绑定码值或可配置 options | `PurvarCodeSelect-RADIO` | 将 options 写入 `propsJson.options` |
| `el-checkbox-group` 绑定码值或可配置 options | `PurvarCodeSelect-CHECKBOX` | 将 options 写入 `propsJson.options` |
| `el-date-picker type="date"` | `ElDatePicker-DATE` | 日期 |
| `el-date-picker type="datetime"` | `ElDatePicker-DATETIME` | 日期时间 |
| `el-time-picker` | `ElTimePicker-TIME` | 时间 |
| `el-switch` | `ElSwitch-SWITCH` | 开关 |
| `el-slider` | `ElSlider-SLIDER` | 滑块 |
| `el-rate` | `ElRate-RATE` | 评分 |
| `PurvarSelectUser` | `PurvarSelectUser-USER_SELECT` | 选人 |
| `PurvarSelectUser/Dropdown.vue` | `PurvarSelectUserDropdown-USER_SELECT_DROP` | 下拉选人，只有确认配置端支持时使用 |
| `PurvarDepartment selector-type="tree"` | `PurvarDepartment-tree` | 公司/部门树 |
| `PurvarDepartment selector-type="company"` | `PurvarDepartment-company` | 只选公司 |
| `PurvarDepartment selector-type="dept"` | `PurvarDepartment-dept` | 选部门 |
| `PurvarDepartment selector-type="group"` | `PurvarDepartment-group` | 选小组 |
| `PurvarAddress` | `PurvarAddress-ADDRESS` | 地址 |
| `PurvarLibrary` | `PurvarLibrary-FILE_LIBRARY` | 通用文件库 |
| `PurvarUpload` | `PurvarUpload-FILE` | 普通附件上传 |
| `PurvarUploadImg` | `PurvarUploadImg-IMAGE` | 图片上传 |
| `PurvarTreeSelect` 或 `el-tree-select` | `PurvarTreeSelect-TREE_SELECT` | 树形选择，由组件库文档和命名规则推导 |
| `PurvarLinkTag` 多选 | `PurvarLinkTag-LINK_TAG` | 标签式码值多选，由组件库文档和命名规则推导 |
| `PurvarLinkTag` 单选 | `PurvarLinkTag-LINK_TAG_SINGLE` | 标签式码值单选，由组件库文档和命名规则推导 |
| 公式配置或计算字段 | `FormulaConfig-FORMULA` | 公式配置字段 |

### 类型判断细则

- `PurvarCodeSelect` 的 `selector-type` 决定分类：`el-select`、`el-radio`、`el-checkbox`。
- `PurvarCodeSelect selector-type="el-select"` 且 `multiple` 为真时，输出 `PurvarCodeSelect-MULTI_SELECT`。
- `PurvarDepartment` 必须根据 `selector-type` 输出 `PurvarDepartment-tree/company/dept/group`，不要再输出 `ORG_SELECT`。
- `PurvarSelectUser` 默认输出 `PurvarSelectUser-USER_SELECT`。只有实际使用 Dropdown 子入口，且配置端支持时，才输出 `PurvarSelectUserDropdown-USER_SELECT_DROP`。
- `PurvarLinkTag` 和 `PurvarTreeSelect` 在 `字段组件说明文档.md` 中没有单独列出，但 `Purvar组件库.md` 确认它们是当前组件库动态字段可用组件；按新版命名规则输出 `PurvarLinkTag-*`、`PurvarTreeSelect-TREE_SELECT`。
- `el-date-picker type="daterange"` 暂无正式新版 `fieldType`。不要自动输出旧 `DATE_RANGE`；优先拆成两个 `ElDatePicker-DATE` 字段，或在结果说明中标注需要人工确认。
- 页面中出现非 Purvar 公共组件时，优先映射到配置端已支持的组件类型；无法可靠映射时，在结果说明中列出人工确认项。

## propsJson 提取规则

`propsJson` 必须是 JSON 字符串。无特殊属性时输出空字符串 `""`。

### PurvarCodeSelect

```json
"{\"codeType\":\"FUND_REG_STATUS\",\"selectorType\":\"el-radio\"}"
```

可提取属性：
- `code-type` -> `codeType`
- `code-id` -> `codeId`
- `parent-id` -> `parentId`
- `selector-type` -> `selectorType`
- `multiple` -> `multiple`
- 静态选项 -> `options`

旧页面中的 `codeParentId` 可保留，但新增优先使用 `parentId`。

### PurvarSelectUser

```json
"{\"multiple\":true,\"extraBindings\":{\"userName\":\"memberName\"}}"
```

提取要点：
- `multiple` 根据组件 prop 输出。
- 主字段应保存用户 ID。
- 名称字段、辅助 ID 字段写入 `extraBindings`，例如 `userName`、`userIds`、`label`。
- 如果页面只绑定姓名，没有绑定 ID，需要在结果说明中标注该字段需要人工确认保存值。

### PurvarDepartment

```json
"{\"selectorType\":\"dept\",\"companyId\":\"formData.companyId\",\"extraBindings\":{\"label\":\"deptName\"}}"
```

提取要点：
- `selector-type` -> `selectorType`
- `company-id`、`dept-id` 等上下文字段写入对应属性。
- `v-model:label` 写入 `extraBindings.label`。

### PurvarLinkTag

```json
"{\"codeType\":\"enterprise_tags\",\"multiple\":true,\"extraBindings\":{\"label\":\"enterpriseTagsName\"}}"
```

提取要点：
- 单选输出 `PurvarLinkTag-LINK_TAG_SINGLE`。
- 多选输出 `PurvarLinkTag-LINK_TAG`。
- 如果页面通过 `select-type="2"` 表示单选，写入 `propsJson.selectType = 2`。

### PurvarTreeSelect

```json
"{\"multiple\":false,\"treeProps\":{\"label\":\"label\",\"value\":\"id\",\"children\":\"children\"}}"
```

如果树数据来自页面变量，写入 `propsJson.optionsField` 或在说明中标注数据源需要页面侧适配。

### PurvarAddress

```json
"{\"level\":3,\"saveMode\":\"json\",\"extraBindings\":{\"province\":\"province\",\"city\":\"city\",\"area\":\"area\"}}"
```

如果页面拆分保存省、市、区，写入 `extraBindings`。如果单字段保存完整地址，建议 `saveMode` 为 `json`。

### 附件组件

`PurvarLibrary-FILE_LIBRARY` 示例：

```json
"{\"functionType\":\"FUND\",\"functionDataId\":\"formData.id\",\"moduleDataId\":\"formData.projId\",\"stageType\":\"1001\",\"stageStep\":\"1001-1\",\"parentId\":\"30\"}"
```

`PurvarUpload-FILE` 示例：

```json
"{\"limit\":5,\"accept\":\".pdf,.doc,.docx\",\"maxSize\":50}"
```

`PurvarUploadImg-IMAGE` 示例：

```json
"{\"limit\":1,\"contextPath\":\"/ezgo/foundation/\"}"
```

### 输入、数字、日期、开关

文本：

```json
"{\"maxlength\":\"200\"}"
```

数字：

```json
"{\"min\":0,\"max\":100,\"step\":1,\"precision\":2}"
```

日期：

```json
"{\"format\":\"YYYY-MM-DD\",\"valueFormat\":\"YYYY-MM-DD\"}"
```

日期时间：

```json
"{\"format\":\"YYYY-MM-DD HH:mm:ss\",\"valueFormat\":\"YYYY-MM-DD HH:mm:ss\"}"
```

时间：

```json
"{\"format\":\"HH:mm:ss\",\"valueFormat\":\"HH:mm:ss\"}"
```

开关：

```json
"{\"activeValue\":\"1\",\"inactiveValue\":\"0\"}"
```

### FormulaConfig

```json
"{\"formula\":\"{amount}[金额] * {rate}[费率]\",\"description\":\"管理费 = 金额 * 费率\",\"resultField\":\"manageFee\"}"
```

公式字段优先按配置字段处理。如果页面只是展示 computed 值，没有公式配置来源，需要在输出说明中标注表达式需要人工确认。

## linkageJson 提取规则

字段显隐依赖其他字段时，提取为 `linkageJson`。

简单等值：

```vue
<PurvarCol v-if="formData.isRecord === '1'" label="备案号">
```

```json
"{\"conditions\":[{\"field\":\"isRecord\",\"operator\":\"eq\",\"value\":\"1\"}]}"
```

不等值：

```json
"{\"conditions\":[{\"field\":\"status\",\"operator\":\"neq\",\"value\":\"0\"}]}"
```

包含：

```vue
<PurvarCol v-if="formData.platforms?.includes('3')" label="xxx">
```

```json
"{\"conditions\":[{\"field\":\"platforms\",\"operator\":\"contains\",\"value\":\"3\"}]}"
```

支持的 operator：
- `eq`
- `neq`
- `contains`
- `notContains`

多条件默认按 AND 处理。复杂业务逻辑无法完整表达时，提取能表达的部分，并在 `.md` 输出中写明需要代码侧兜底。

## 输出步骤

1. 按模板出现顺序生成字段数组。
2. 确保 `sortOrder` 从 1 开始连续递增。
3. 输出 JSON 到 `ei-view/jsonMock/<pageName>.json`。
4. 输出 Markdown 到 `ei-view/jsonMock/<pageName>.md`，包含 JSON 代码块和人工确认项。
5. 如果发现旧 mock 中有旧裸枚举，不要照抄，按新版映射转换。

## 验证清单

输出后逐项检查：

1. JSON 字段数量与模板主表单字段数量一致，排除 `scope.row.xxx`。
2. `fieldCode` 唯一。
3. `sortOrder` 从 1 连续递增。
4. 所有 `fieldType` 都是新版“组件名-分类”格式，或是明确记录的人工确认项。
5. 不存在旧裸枚举：`TEXT`、`TEXTAREA`、`NUMBER`、`SELECT`、`MULTI_SELECT`、`RADIO`、`CHECKBOX`、`DATE`、`DATETIME`、`TIME`、`SWITCH`、`SLIDER`、`RATE`、`FORMULA`、`TREE_SELECT`、`ADDRESS`、`LINK_TAG`、`LINK_TAG_SINGLE`、`USER_SELECT`、`ORG_SELECT`、`FILE_LIBRARY`、`FILE`、`IMAGE`。
6. `propsJson` 和 `linkageJson` 是合法 JSON 字符串或空字符串。
7. `linkageJson.conditions[].field` 引用的字段存在于当前 JSON 字段列表。
8. `PurvarCol label` 与 `fieldName` 一致。
9. 选人、码值、机构、标签类字段保留 ID/编码作为主字段值，名称回显字段进入 `extraBindings` 或输出人工确认项。

可用这些命令辅助检查：

```powershell
Get-Content -Raw -Encoding UTF8 ei-view/jsonMock/<pageName>.json | ConvertFrom-Json | Out-Null
rg -n '"fieldType":\s*"(TEXT|TEXTAREA|NUMBER|SELECT|MULTI_SELECT|RADIO|CHECKBOX|DATE|DATETIME|TIME|SWITCH|SLIDER|RATE|FORMULA|TREE_SELECT|ADDRESS|LINK_TAG|LINK_TAG_SINGLE|USER_SELECT|ORG_SELECT|FILE_LIBRARY|FILE|IMAGE)"' ei-view/jsonMock/<pageName>.json
```

第二条命令应无输出。若有输出，说明仍残留旧裸枚举，需要改成新版 `fieldType`。

## 注意事项

- When a `PurvarCol` is rendered by `v-for` over a local static array of `{ fieldCode, label }` objects, expand that array into one stable field per entry. Do not emit the loop expression such as `field.fieldCode` as a field code; it prevents runtime controls with generated IDs from being mapped back to their required business fields.

- `fieldType` 表达组件和分类，`dataType` 才表达数据库物理存储类型，两者不要混用。
- `componentType` 是基础配置/业务配置页面概念，业务表单动态字段输出使用 `fieldType`。
- `PurvarTable` 内字段不计入主表单字段。
- 同一字段通过 `v-if/v-else` 切换编辑态和查看态时，按编辑态组件确定 `fieldType`。
- `PurvarSubTitle` 不产生字段。
- 同一页面多个 `PurvarLibrary` 要分别生成字段，并在 `propsJson` 中记录各自 `functionType`、`stageType` 等参数。
- `rules` 中的必填校验优先级高于模板上的 `required`。
- Vue 3 `v-model:arg` 的辅助绑定必须写入 `propsJson.extraBindings`。
- Element Plus 日期格式在 props 中使用 `YYYY-MM-DD`、`YYYY-MM-DD HH:mm:ss`、`HH:mm:ss`。
- 如果无法确认字段的新版 `fieldType`，不要猜旧枚举；先输出最接近的新类型，并在 `.md` 的人工确认项中说明原因。
