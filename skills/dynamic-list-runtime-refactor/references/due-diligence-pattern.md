# 项目尽职调查动态列表改造参考

使用 `dynamic-list-runtime-refactor` 改造其他 EI 列表时，再读取本参考文档。下面路径均以 `E:\fangzheng\ezgo3\ei-parent` 为根目录。

## 前端锚点

| 用途 | 文件 |
| --- | --- |
| 项目尽职调查列表主实现 | `ei-view/src/views/projectManage/before/investProcess/dueDiligence/index.vue` |
| 项目尽职调查静态辅助列和操作列 | `ei-view/src/views/projectManage/before/investProcess/dueDiligence/components/data.ts` |
| 自管基金尽职调查同模式示例 | `ei-view/src/views/selfManagedFunds/investmentProcess/dueDiligence/index.vue` |
| 动态表头和动态查询 helper | `ei-view/src/utils/dynamicTableColumns.ts` |
| 动态查询表单渲染组件 | `ei-view/src/components/DynamicListQueryForm.vue` |
| UIM 表单/列表配置 API | `ei-view/src/api/formConfig.ts` |
| 租户 id 工具 | `ei-view/src/utils/auth.ts` |
| 基金相关 API 类型和尽调列表调用 | `ei-view/src/api/fund.ts` |

## 前端实现要点

项目尽职调查使用 `const FORM_CODE = "FUND_DILIGENCE"`。改造其他页面时，替换为目标页面对应的 UIM 表单编码。

页面保留原有固定查询条件和操作按钮，在其周围追加动态查询区域。`DynamicListQueryForm` 接收 `items` 和 `model`，负责按字段类型渲染查询控件；`buildDynamicListQueryValues` 负责把运行时查询模型转换成后端需要的动态查询 payload。

静态表头通常只保留：

- `type: "index"`：序号列。
- `type: "selection"`：多选列，页面需要多选时保留。
- `type: "expand"`：展开列，页面需要展开行时保留。
- `prop: "operation"`、`prop: "actions"` 或 `prop: "operate"`：右侧操作列。

`mergeDynamicTableColumns` 会保留上述静态辅助列和操作列，并把 UIM 配置的业务列插入中间。

`dynamicTableColumns.ts` 还负责列表单元格展示取值：

- 优先取 `displayFieldCode` 指向的行字段。
- 其次取 `dynamicFieldLabels[fieldCode]`。
- 最后兜底取固定字段原始值或 `dynamicFields[fieldCode]`。
- 公式列使用 `${fieldCode}` 占位符计算安全的四则运算表达式。

## 后端锚点

| 用途 | 文件或类 |
| --- | --- |
| 查询请求公共字段 | `com.purvar.ezgo.framework.request.BaseSearchRequest`（framework 依赖） |
| 动态查询值请求 DTO | `com.purvar.ezgo.framework.request.ListQueryValueRequest`（framework 依赖） |
| VO 动态字段公共字段 | `com.purvar.ezgo.framework.vo.BaseVO`（framework 依赖） |
| 尽调计划查询请求示例，继承 `BaseSearchRequest` | `ei-facade/src/main/java/com/purvar/petou/ei/request/surveyplan/SurveyPlanSearchRequest.java` |
| 尽调计划列表 VO 示例，继承 `BaseVO` | `ei-facade/src/main/java/com/purvar/petou/ei/vo/surveyplan/SurveyPlanListVO.java` |
| 动态查询编译服务公共接口 | `ei-service/src/main/java/com/purvar/petou/ei/dynamic/list/DynamicListQueryService.java` |
| 动态查询编译服务实现 | `ei-service/src/main/java/com/purvar/petou/ei/dynamic/list/DefaultDynamicListQueryService.java` |
| 编译后的动态 SQL 上下文 | `ei-service/src/main/java/com/purvar/petou/ei/dynamic/list/DynamicListQueryContext.java` |
| 运行时动态字段回填增强器 | `ei-service/src/main/java/com/purvar/petou/ei/dynamic/list/DynamicRuntimeListEnhancer.java` |
| UIM 查询条件配置提供者 | `ei-service/src/main/java/com/purvar/petou/ei/dynamic/list/UimRuntimeListQueryConditionConfigProvider.java` |
| 动态查询 XML 公共过滤片段 | `ei-service/src/main/resources/mybatis/DynamicQuerySqlMapper.xml` |
| 尽调计划 service 集成点 | `ei-service/src/main/java/com/purvar/petou/ei/service/impl/SurveyPlanServiceImpl.java` |
| 尽调计划 controller 回填集成点 | `ei-service/src/main/java/com/purvar/petou/ei/controller/SurveyPlanController.java` |
| 尽调计划 mapper XML 动态 JOIN 与公共过滤引用 | `ei-service/src/main/resources/mybatis/SurveyPlan_mapper.xml` |

## 运行时身份清单

改代码前先确定目标列表的这些值：

| 运行时值 | 尽调示例 | 作用 |
| --- | --- | --- |
| `formCode` | `FUND_DILIGENCE` | 获取 UIM 运行时表头、查询条件和动态字段值；后端从 `BaseSearchRequest` 读取。 |
| `listCode` | `FUND_DILIGENCE_LIST` | 编译正确的列表查询条件配置。 |
| 租户 id | 前端运行时配置用 `getTenantId()`；后端公共 helper 用 `AuthUtil.getTenantId()` | 限定 UIM 配置、动态查询编译和运行时数据回填的租户范围。 |
| 业务主键 getter | `SurveyPlanListVO::getBusinessId` / plan id | 用返回行关联 UIM 动态运行时数据。 |
| 业务表别名 | `p` | 固定字段白名单列按这个别名编译。 |
| 动态表别名 | 由 `DynamicListQueryContext` 生成 | 只有动态字段过滤需要 JOIN 时使用。 |

上述值不清楚时，不要先改 mapper XML。

## 后端实现要点

列表 request 和 VO 只保留业务字段：request 公共字段来自 `BaseSearchRequest`，VO 动态字段 map 来自 `BaseVO`。项目内旧的 `com.purvar.petou.ei.request.dynamiclist.ListQueryValueRequest` 已移除，不要再引入。

`DynamicListQueryService` 提供 `compile(listCode, request)`、`compile(listCode, formCode, request)` 和 `compile(listCodeResolver, request)` 公共入口。它用前端提交的 `conditionCode/value` 匹配 UIM 运行时查询配置，并在后端确定操作符、值模式、固定/动态字段处理方式以及安全的表名、别名、列名；业务 controller 不要从前端接收这些 SQL 相关信息。

`DynamicRuntimeListEnhancer.enrich(request, rows, idGetter)` 会按当前页业务 id 批量调用 UIM `/tenantForm/listFormData`，并通过 `BaseVO::setDynamicFields`、`BaseVO::setDynamicFieldLabels` 回填运行时动态值。业务 controller 只传 request、行集合和业务主键 getter，不要再复制 `enrichXxxRuntimeFields` 或手动 `setTenantId`。

Mapper XML 仍按业务主键列决定是否动态 JOIN，但过滤条件统一写：

```xml
<include refid="dynamicQuerySql.dynamicQueryFilters"/>
```

`UimRuntimeListQueryConditionConfigProvider` 只应该暴露业务后端已支持的固定字段 SQL 映射。半固定 JSON 字段和手动新增的展示型业务字段默认不进入查询 provider。

## 可复制或改造的测试

| 层级 | 现有测试 |
| --- | --- |
| 动态表头合并和展示取值 | `ei-view/tests/dynamicTableColumns.test.mjs` |
| 项目尽职调查前端运行时行为 | `ei-view/tests/projectDueDiligenceDynamicRuntime.test.mjs` |
| 动态查询表单控件 | `ei-view/tests/dynamicListQueryForm.test.mjs` |
| 动态查询编译器 | `ei-service/src/test/java/com/purvar/petou/ei/dynamic/list/DefaultDynamicListQueryServiceTest.java` |
| UIM 查询配置 provider | `ei-service/src/test/java/com/purvar/petou/ei/dynamic/list/UimRuntimeListQueryConditionConfigProviderTest.java` |
| 尽调计划 controller/list 集成 | `ei-service/src/test/java/com/purvar/petou/ei/controller/SurveyPlanControllerPageListTest.java` |

改造新功能时，优先复制最接近的列表页测试。至少补一个动态查询 payload 提交用例，以及一个能证明动态条件进入 service/mapper 路径的后端用例。
