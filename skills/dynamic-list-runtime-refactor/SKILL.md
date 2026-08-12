---
name: dynamic-list-runtime-refactor
description: 当需要将 ezgo3-ei-parent 的 EI 业务列表页面改造成 UIM 运行时表头、堆叠表头、公式列、动态查询条件、固定字段 displayFieldCode 展示、自定义列表或后端动态列表公共模式时使用。
---

# 动态列表运行时改造

## 概述

使用本 skill 时，将 EI 业务列表从“静态表头 + 固定查询条件”改造成 UIM 运行时列表配置模式。推荐保留原业务页面结构、接口路径、原有固定筛选条件和按钮逻辑，只把普通业务列、动态表头、公式列和动态查询条件接入运行时配置。

需要查看项目尽职调查的具体文件位置、类名、方法名和测试样例时，读取 `references/due-diligence-pattern.md`。

## 必须遵守

- 保留自动表格辅助列：左侧 `index` / `selection` / `expand`，右侧 `operation` / `actions` / `operate`。
- 不要在 UIM 列表设计中配置序号列、多选列、展开列和操作列。
- 按 `formCode` 获取运行时列表配置，再由返回的 `columns` 和 `queries` 生成表头与查询条件；配置接口 body 不传 `tenantId`。
- 动态查询提交值只能是 `{ conditionCode, value }`；不要信任前端传来的操作符、表名、字段名或 SQL 片段。
- 动态查询来源只允许 UIM 已配置且启用的字段。手动新增的展示型业务字段默认不允许查询，除非后端明确补了固定字段白名单和 SQL 支持。
- 半固定字段（`fixedType = 2`）对应大 JSON 数据，不允许作为查询条件。
- 前端归一化列表行时必须先保留 `...item`，避免丢失 `dynamicFields`、`dynamicFieldLabels` 和公式字段值。
- `displayFieldCode` 只影响单元格展示；字段元数据匹配、配置主键和查询编译仍使用 `fieldCode`。
- 后端查询 request 的 `formCode`、`listQueryValues` 由 `BaseSearchRequest` 承载，不要在每个业务 request 重复声明；租户由后端上下文或 `AuthUtil` 解析，前端业务列表 body 不传 `tenantId`。
- 后端列表 VO 的 `dynamicFields`、`dynamicFieldLabels` 由 `BaseVO` 承载，不要在每个业务 VO 重复声明。
- MyBatis XML 里的 `${...}` 只能消费后端校验并编译出的表名、别名和列名，用户输入值必须继续用 `#{...}` 绑定。

## 前端改造流程

1. 找到目标静态列表页面和表头定义文件。静态表头通常只保留辅助列和操作列，普通业务列交给 UIM 运行时配置。
2. 引入运行时配置 API、动态查询表单和表头工具函数：

```ts
import DynamicListQueryForm from '@/components/DynamicListQueryForm.vue'
import { getRuntimeListConfig, type RuntimeListConfigData } from '@/api/formConfig'
import {
  buildDynamicListQueryValues,
  createDynamicListQueryModel,
  getEnabledDynamicListQueries,
  mergeDynamicTableColumns,
  toPurvarTableColumns,
} from '@/utils/dynamicTableColumns'
```

3. 在页面中定义 `FORM_CODE`，值必须与 UIM 租户表单编码一致。
4. 增加运行时状态：`runtimeListConfig`、`runtimeConfigLoaded`、`dynamicQueryModel`。
5. 页面挂载时调用 `getRuntimeListConfig({ formCode: FORM_CODE })`，不要额外传 `tenantId`。
6. 用 `toPurvarTableColumns(runtimeListConfig.value?.columns || [])` 生成动态表头，再用 `mergeDynamicTableColumns(staticTableColumns, dynamicColumns)` 与静态辅助列、操作列合并。
7. 用 `getEnabledDynamicListQueries(runtimeListConfig.value?.queries || [])` 生成动态查询项，用 `createDynamicListQueryModel` 初始化查询模型，并交给 `DynamicListQueryForm` 渲染。
8. 查询列表时，在原固定筛选参数之外追加 `BaseSearchRequest` 公共字段：

```ts
formCode: FORM_CODE,
listQueryValues: buildDynamicListQueryValues(dynamicQueryItems.value, dynamicQueryModel),
```

9. 重置动态查询时，根据当前动态查询项重新创建模型，不要只在旧对象上删改字段。
10. 列表行归一化时先展开后端原始行，再追加页面派生字段：

```ts
const normalizeRow = (item: Record<string, any>) => ({
  ...item,
  // 这里继续补充原页面已有的派生展示字段
})
```

## 动态查询表单布局

`DynamicListQueryForm.vue` 是列表动态查询条件的唯一公共渲染组件。业务页面不要按字段类型手写重复控件，也不要复制组件结构到页面目录。布局不满足时优先扩展公共组件，并保持所有列表页一致。

组件结构必须复用列表页公共搜索样式，便于 `commonModules/styles/list.scss` 统一生效：

```vue
<el-form class="search-container">
  <div class="search-first">
    <div class="search-first-content">
      <!-- 第一行只显示 defaultFlag === "1" 的动态查询条件 -->
      <el-form-item />
      <el-form-item class="search-more">
        <el-button>···</el-button>
      </el-form-item>
    </div>
    <div class="search-button">
      <el-button type="primary">查询</el-button>
      <el-button plain>重置</el-button>
    </div>
  </div>
  <div class="search-more-list">
    <!-- 第二行放其余动态查询条件 -->
  </div>
</el-form>
```

布局约定：
- 第一行只展示 `defaultFlag === "1"` 的动态查询条件，不要简单取查询数组第一个。
- `defaultFlag !== "1"` 的条件默认放入展开区。
- 如果没有任何 `defaultFlag === "1"` 的查询条件，则不渲染整个动态查询表单。
- 如果存在展开区条件，第一行在默认查询条件右侧显示 `el-form-item.search-more`。
- 展开/收起按钮沿用项目现有列表页写法，按钮文案保持 `···`，不要改成独立的“展开 / 收起”样式。
- 展开后的条件放入 `.search-more-list`，不要自造第二行容器。
- 查询按钮区域使用 `.search-button`，按钮顺序保持“查询 / 重置”。
- 搜索控件宽度在 `DynamicListQueryForm.vue` 内统一维护，当前约定为 `260px`；`PurvarCodeSelect` 也要保持同宽。
- 不新增 `dynamic-list-query__actions`、`dynamic-list-query__break` 等独立布局类来替代公共搜索结构。

## 查询控件规则

| 字段类型 | 查询控件 |
| --- | --- |
| `TEXT`, `INPUT`, `TEXTAREA` | `el-input` |
| `NUMBER`, `SLIDER`, `RATE` | `el-input-number` |
| `SELECT`, `RADIO`, `MULTI_SELECT`, `CHECKBOX`, `PurvarCodeSelect-*` | `PurvarCodeSelect` 多选下拉 |
| `DATE`, `DATETIME` | `el-date-picker`，使用匹配的值格式 |
| `TIME` | `el-time-picker` |
| `SWITCH` | 是/否下拉 |
| `USER_SELECT` | `PurvarSelectUser` |
| `ORG_SELECT`, `TREE`, `COMPANY`, `DEPT`, `GROUP` | `PurvarDepartment` |
| `valueMode === 'NONE'` | 无输入值条件，用勾选控件表示是否启用 |
| `valueMode === 'MULTI'` | 提交数组值 |

## 后端改造流程

1. 列表查询 request 继承 `com.purvar.ezgo.framework.request.BaseSearchRequest`。不要在业务 request 里重复声明 `formCode`、`List<ListQueryValueRequest> listQueryValues`；租户由后端上下文解析，不作为前端业务列表 body 字段传入。需要引用查询值类型时使用 `com.purvar.ezgo.framework.request.ListQueryValueRequest`。
2. 列表 VO 继承 `com.purvar.ezgo.framework.vo.BaseVO`。不要在业务 VO 里重复声明 `dynamicFields` 和 `dynamicFieldLabels`；只有页面确实需要单独返回公式字段时，再增加业务自己的公式字段 map。
3. 在业务列表 service 中用公共入口编译动态查询上下文。固定 `listCode` 使用：

```java
DynamicListQueryContext dynamicQuery =
        dynamicListQueryService.compile(DynamicListConstants.FUND_ALLOCATION_LIST_CODE, request);
```

`listCode` 需要按 `formCode` 切换时，只保留业务解析器：

```java
DynamicListQueryContext dynamicQuery =
        dynamicListQueryService.compile(this::resolvePaymentListCode, request);

private String resolvePaymentListCode(String formCode) {
    if (DynamicListConstants.FUND_INVESTOR_CONTRIBUTION_FORM_CODE.equals(formCode)) {
        return DynamicListConstants.FUND_INVESTOR_CONTRIBUTION_LIST_CODE;
    }
    return DynamicListConstants.PROJECT_BEFORE_INVESTMENT_LIST_CODE;
}
```

需要先计算默认 `formCode` 时，显式传入已解析值：

```java
String formCode = resolveDynamicListFormCode(request);
DynamicListQueryContext dynamicQuery =
        dynamicListQueryService.compile(resolveDynamicListCode(formCode), formCode, request);
```

4. Mapper 方法用 `@Param("dynamicQuery")` 接收 `DynamicListQueryContext`。
5. Mapper XML 只在需要动态字段过滤时 JOIN 运行时动态表，JOIN 条件里的业务主键列按当前 mapper 自己确定；过滤条件统一引用公共片段：

```xml
<if test="dynamicQuery != null and dynamicQuery.requiresDynamicJoin">
    LEFT JOIN ${dynamicQuery.tableSql} ${dynamicQuery.alias}
    ON ${dynamicQuery.alias}.id = p.ID
    AND ${dynamicQuery.alias}.tenant_id = #{dynamicQuery.tenantId}
    AND ${dynamicQuery.alias}.is_delete = '0'
</if>

<include refid="dynamicQuerySql.dynamicQueryFilters"/>
```

`${dynamicQuery.tableSql}`、`${dynamicQuery.alias}`、`${filter.columnSql}` 只能来自后端可信编译结果。
6. 分页查询出业务行后，在 controller 调用公共增强器回填非固定字段。业务 VO 必须继承 `BaseVO`，业务主键 getter 按页面选择：

```java
dynamicRuntimeListEnhancer.enrich(request, data, AllocationPageListVO::getAllId);
```

有汇总行或特殊行时，在 getter 中返回 `null` 跳过：

```java
dynamicRuntimeListEnhancer.enrich(
        request,
        pageInfo.getRecords(),
        row -> row == null || "TOTAL".equals(row.getFundId()) ? null : row.getFundId()
);
```

7. 不要在 controller 里散落 `enrichXxxRuntimeFields`、`TenantContextSupport.resolveTenantId()` 或 `request.setTenantId(...)`。公共 `compile` 和 `enrich` 会用 `AuthUtil.getTenantId()` 解析租户。
8. 只有业务后端真实支持查询的固定字段，才加入固定字段白名单。展示型外部业务字段默认不要加入查询配置提供者。

## 展示字段兼容

固定码值字段可能“查询字段”和“展示字段”不一致，例如尽职范围查询用 `surveyScope`，展示用 `surveyScopeName`。列表列配置可写成：

```json
{
  "fieldCode": "surveyScope",
  "displayFieldCode": "surveyScopeName",
  "displayName": "尽职范围"
}
```

运行时单元格展示建议按以下优先级取值：

1. `row[column.displayFieldCode]`
2. `row.dynamicFieldLabels[column.fieldCode]`
3. `row[column.fieldCode]` 或 `row.dynamicFields[column.fieldCode]`

查询条件仍保持 `fieldCode = surveyScope`；`displayFieldCode` 不改变查询语义。

## 校验清单

- UIM 运行时配置请求只带正确的 `formCode`，不带 `tenantId`。
- 业务列表查询请求带 `formCode` 和 `listQueryValues`，不带 `tenantId`。
- 表格保留序号列、多选列、展开列、操作列，普通业务列由运行时配置替换。
- 堆叠表头、`minWidth`、`width`、`align`、启用状态、公式列、`displayFieldCode` 都能正确渲染。
- 查询条件控件能按字段组件类型渲染，码值字段使用 `PurvarCodeSelect` 多选下拉。
- 前端动态查询只提交 `{ conditionCode, value }`。
- 后端查询 request 继承 `BaseSearchRequest`，VO 继承 `BaseVO`，service 使用 `dynamicListQueryService.compile(...)`，controller 使用 `dynamicRuntimeListEnhancer.enrich(...)`，mapper XML 使用 `dynamicQuerySql.dynamicQueryFilters`。
- 动态筛选能影响 SQL，参与查询的非固定字段也能通过 `dynamicFields` / `dynamicFieldLabels` 返回给列表。
- 原有固定查询条件、按钮、操作列、分页、导出和弹窗逻辑仍可用。
- 至少覆盖静态配置兜底、运行时表头、动态查询提交、固定字段 `displayFieldCode` 展示这几类测试或定向手工检查。

## 常见错误

| 错误 | 修正方式 |
| --- | --- |
| 直接用动态表头替换整个静态表头数组 | 用 `mergeDynamicTableColumns` 把动态业务列插入辅助列和操作列之间。 |
| 行归一化时没有保留 `...item` | 先保留后端原始行，再追加派生展示字段。 |
| 前端提交操作符、字段名或 SQL | 前端只提交 `conditionCode` 和值；后端按 UIM 配置编译。 |
| 让手动新增展示字段默认参与查询 | 保持展示字段只展示，除非后端补固定字段白名单和 SQL 支持。 |
| 把 `displayFieldCode` 当成查询字段 | 查询用 `fieldCode`；展示用 `displayFieldCode || fieldCode`。 |
| 在每个 request 重复声明 `formCode`、`listQueryValues` | 继承 `BaseSearchRequest`，使用框架公共字段。 |
| 前端调用 `/tenantForm/list/config` 或业务列表接口时传 `tenantId` | 配置请求只传 `formCode`，业务列表请求只追加 `formCode` 和 `listQueryValues`；租户由后端上下文解析。 |
| 在每个 VO 重复声明 `dynamicFields`、`dynamicFieldLabels` | 继承 `BaseVO`，使用公共 getter/setter。 |
| 在 service 中复制 `compileXxxDynamicQuery` | 改用 `dynamicListQueryService.compile(...)`；只保留业务 `listCode`/`formCode` 解析器。 |
| 在 controller 中复制 `enrichXxxRuntimeFields` 或手动 set tenantId | 改用 `dynamicRuntimeListEnhancer.enrich(request, rows, idGetter)`。 |
| 在每个 mapper XML 复制动态过滤 `<foreach>` | 改用 `<include refid="dynamicQuerySql.dynamicQueryFilters"/>`。 |
| 忘记回填动态字段 | 列表业务行查询后调用 `DynamicRuntimeListEnhancer.enrich` 公共方法。 |
| 每个页面各自硬编码查询控件类型 | 复用 `DynamicListQueryForm` 和 `dynamicTableColumns.ts`。 |
