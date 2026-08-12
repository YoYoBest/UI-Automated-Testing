# ei-parent 拆解覆盖清单

本工程不导入 Vue/Java 运行时代码，而是对照 `ei-parent` 的公共契约重新实现 Python 适配层。

| ei-parent 来源 | 已拆到本工程 | 状态 |
| --- | --- | --- |
| `ei-view/src/api/formConfig.ts` | `runtime_api.py` | 表单配置、运行时查询/保存/更新、列表配置 |
| `ei-view/src/utils/useDynamicFormConfig.ts` | `contracts.py`、`schema.py` | 配置数字/布尔、固定/动态字段、显隐、JSON 属性 |
| `ei-view/src/utils/dynamicFormField.ts` | `contracts.py`、`values.py` | 新旧字段类型、默认值、运行时 payload、显示缓存、extraBindings |
| `ei-view/src/components/DynamicFormField/index.vue` | `interactions.py` | 文本、数字、码值、日期、选人、机构、树、开关、文件等交互入口 |
| `ei-view/src/utils/dynamicTableColumns.ts` | `dynamic_list.py` | 动态查询值、堆叠列、展示字段取值优先级 |
| `ei-view/**/*.json` | `schema.py` | 按 formCode 发现并读取本地配置 JSON |
| 浏览器实际页面 | `dom.py` | 弹窗优先扫描、fieldCode/label 匹配、实际渲染验证 |
| 公共业务数据 | `common_data.json`、`data_pool.py` | 合法手机号、信用代码、金额区间、企业名称及字段语义映射 |
| 双模式策略 | `data_strategy.py` | probe自动生成、stable公共池/采集缓存/覆盖 |
| 自动采集 | `collector.py`、`collection_sources.json` | 列表/详情采集、白名单、去重、原子写入 |
| 中央页面配置 | `pages.json`、`case_data.py` | 页面地址、保存接口、详情地址，不包含完整业务数据 |
| 保存与详情闭环 | `verification.py`、`orchestrator.py` | 保存响应、业务主键、运行时详情、页面回显 |

## 数据来源优先级

稳定模式优先级：

1. `overrides.json` 的少量表单特殊覆盖。
2. `collected_data.json` 自动采集的合法值。
3. `common_data.json` 公共候选池或受约束生成器。
4. 运行时配置顶层 `defaultValue` / `propsJson.defaultValue`。
5. 按字段类型生成的确定性兜底值。
6. 下拉、选人、机构和树组件从真实接口加载出的可见候选项选择。

## 固定与动态字段

- `fixedType=1`：固定业务字段，保存时应进入原业务接口。
- `fixedType=0/2`：运行时字段，只进入租户表单运行时接口。
- `dynamicFieldLabels`：显示缓存，运行时 payload 中必须是 JSON 字符串。
- `tenantId`：不进入请求 body，由当前登录上下文请求头提供。

## 需要项目配置而非通用代码猜测的内容

- 登录方式及验证码。
- 具体模块路由和新增按钮。
- 各业务表单原始保存接口及成功响应中的业务主键字段。
- 必须使用特定业务记录的选人、组织、码值和附件测试数据。
- 保存后清理测试数据的业务接口。

这些内容必须通过环境配置或按模块增加 Page Object，不能从公共工具方法可靠推断。
