---
name: ui-smoke-test
dynamic_collection_workflow: |
  Load data/dynamic_collections.json in the module and module-action runners using EI_FORM_CODE and EI_COMPONENT.
  Normalize component paths by removing @/, src/views/, srcEi/views/, views/, .vue, query, and fragment wrappers before exact matching.
  Keep page differences in matching manifest entries, not frontend source changes or project-specific Python branches.
  Treat invalid matching configuration as a contract failure before CRUD execution; a missing manifest is valid.
description: 在本仓库扫描 UI 模块，或运行、排查、改造 Python Playwright UI 冒烟测试时使用，尤其适用于源码页面层级异常、运行时菜单和路由获取、自动新增与保存假通过、详情接口回读、下拉和企查查候选值选择、图形启动器或 pytest 测试，以及 Allure 报告维护的场景。
---

## Detail Data Preconditions

- For a project-progress batch, select its parent only once. The first project-progress action expands the project-list search criteria and queries the writable lifecycle statuses in order, currently `项目决策` followed by `项目实施`; the source-confirmed lifecycle continues through `项目竣工` and `已终止`, but the progress Add backend rejects those two states. Enter the first rendered result from the first non-empty eligible query, record its business ID and verified detail URL under this `EI_AUTOMATION_RUN_ID`, parent-list route, and project-progress menu, then reuse that exact context for every later Add/Edit/Delete command, including separate pytest subprocesses. Later actions must open that saved detail URL and verify its ID; an unavailable, redirected, or mismatched parent is a blocked precondition, never a reason to query or choose another project. If an Edit/Delete action has no usable row-level action in the fixed parent's child table, create one child record through that same parent's Add form, return to the saved parent URL, and retry exactly once. Only the first Add action may provision a parent when both eligible queries are empty; it must then submit project decision through the UI and cache that exact parent. Keep the normal Add execution responsible for the actual Add click.
- For project-operation/project-running-information Add, Edit, and Delete prerequisites, a visible `新增` button is not eligibility evidence. Call the source-confirmed read-only `projRegularReport/preAdd/{projId}` gate for the selected parent and proceed only when `canAdd=true`; an ordinary newly created parent at `立项论证` lacks the required approved finish node and cannot seed a report row. When the child table is empty and the gate rejects the parent, select or provision a parent that satisfies the approved finish and reporting-window contract before opening the Add form. Preserve the first gate reason as the shared prerequisite failure and skip the remaining parametrized Delete rules instead of repeating the same form timeout.
- Detail actions must enter through a real parent-list record. If that list is stably loaded but empty, or every bounded existing candidate reaches the requested child module with a rendered `暂无数据` table, provision one automation-owned parent record using the normal add/save path before attempting detail navigation. Do not provision for permission, route, component, or other non-data navigation failures.
- When an add or prior lookup has supplied a persisted business ID, detail navigation must use that ID as the sole record identity. Do not combine it with display markers or names, which can match an unrelated business record; markers remain a fallback only when no persisted ID exists.
- Navigation after provisioning must use the returned business ID or automation marker to locate one exact parent row/card. Do not substitute the first visible business record; an absent or ambiguous match is a failed precondition.
- Keep the provisioned result for subsequent detail actions in the same execution batch, so every action targets the same known record.
- When a row-level edit/view action is unavailable on that provisioned parent because its target child table is empty, enter the same child module through its real Add action, create one automation-owned child record, then re-enter the exact parent and retry the requested action. This seed is only for the isolated automation parent; never create a child record under an existing business parent.
- A `requires_business_id=True` detail module with no operation button is still executable. Run its selected `详情` worksheet cases through the parent-record navigation context with `EI_ACTION=详情`; do not redirect those cases to an `编辑` button or an add-form executor.
- Do not suppress a no-operation detail-page target merely because child actions share its route and component. The page target owns native detail worksheet rows such as `VIEW-001`; keep it in the command plan alongside its edit, submit, or cancel child actions.
- Cover this scheduling rule through the complete pipeline: route/component action suppression, detail-row feature routing, and base-command suppression must leave the page row on `test_common_detail_validation.py` while child-operation rows remain on their matching workflow checks.
- Execute only worksheet rows whose feature is `页面`/`详情`/`查看` and whose control is `对话框名称` as native read-only detail checks, including the selected `VIEW-001` contract. After entering the real detail context, check whether a visible Dialog/Drawer/modal actually presents that detail. When the application uses the list itself as the detail carrier and no independent dialog exists, skip `VIEW-001` with the explicit no-dialog reason; do not relabel it as a page-name assertion or fail because the list has no dialog title. Split a selected `详情` worksheet by its `功能` column before dispatch: 新增/取消关闭 rows go to the add flow, 编辑/修改 rows go to the edit flow, and 提交 rows go to the submit flow. Do not pass the full sheet to any one operation; unsupported rows are reported as not applicable only when their required workflow was not selected.
- A detail-sheet row may use an add, edit, or submit flow as its precondition, but its Allure child suite must remain `详情`. Pass `EI_ALLURE_SUB_SUITE=详情` on every dispatched detail-sheet command so the report separates detail verification from the precondition action.
- A shared read-only detail rule such as `VIEW-001` is intentionally run once for each selected detail module. Before navigation or any possible skip, set its Allure title to the full module path plus the rule ID and hide the `detail_case` pytest parameter; never leave several different modules as the indistinguishable `test_selected_common_detail_case[VIEW-001]` title under the same `详情` sub-suite. A list-backed detail that skips because it has no independent dialog must keep that same contextual title.
- The launcher must invoke `tests/test_common_detail_validation.py::test_selected_common_detail_case`, not the entire test file. File-level unit regressions are framework checks and must not appear as passed/failed business-detail cases in a live Allure report.
- The browser fixture must prefer `EI_ENTRY_URL` over `EI_FORM_URL` as its initial route. A detail form URL can be a synthesized route that the deployed application cannot open directly; enter the real parent list first and use the record-scoped navigation flow.
- Detail action lookup retains the ordinary rendering wait while the selected content is absent or loading. Once visible target content stays unchanged through a short grace window and the requested action remains absent, raise the existing missing-action contract failure immediately rather than repeating the full action timeout for every candidate parent record. This must not preflight dynamic-collection roots, because conditional form fields can render only after baseline linkage.
- Cover both branches in `tests/test_detail_navigation.py`: a stable rendered target without the action exits after the grace window, while an absent target container consumes the normal bounded timeout.
- Test doubles for this navigation path must model the Playwright collection access used by the implementation, including `.last` for the selected visible target.

## Runtime Manifest Progress

- Schedule every common-field validation command as a two-stage pair: `test_common_field_discovery.py` followed by `test_common_field_validation.py`. Both commands must receive the same manifest path, target context, worksheet, and selected case IDs.
- Run and prioritize discovery before validation. The launcher must register the validation total only from that run's discovered manifest; do not use a previous manifest, Excel-row count, or partial collected-command count as a progress denominator.
- Before a discovery command starts, remove its target manifest. A discovery failure must omit every dependent validation command and cannot read or display a prior run's manifest count.
- Pagination regressions for persisted parent lookup must model the rendered collection's `count()`, `nth()`, and text snapshot contract, so later-page exact-ID lookup remains covered without a live browser.
- Until discovery has registered every dependent validation total, progress is intentionally unknown. Once registration completes, show the exact independent pytest/Allure item total and completed count.
- Cover the pair in launcher regression tests: each eligible worksheet must plan discovery before validation, and the two environments must have identical manifest path, worksheet, selected IDs, and form action.
- A parent-node click may cascade selection to its descendants, but the final execution plan must use the current selected executable node IDs. Do not re-expand selected parent paths during planning: a descendant that the user manually deselects, together with its own descendants, must not run. Cover both the full cascade and a manually removed child branch.

## Browser Navigation Recovery

- On a browser-test failure, attach the sanitized failure URL and screenshot as usual. When the executor captured structured diagnostics, attach their JSON and a bounded rendered-DOM snapshot to the same Allure result; use this for evidence collection only, not a persistent monitoring dashboard. Diagnostics must contain no URL query values, request bodies, cookies, tokens, or credentials.

- Treat initial deployed-page navigation as a per-item browser/environment precondition. On failure, close the entire page, context, and browser, then retry once in a newly created browser session; do not retry inside the failed session.
- Count only fully exhausted fresh-session recovery failures, not individual attempts. Let later parametrized items start their own fresh-session recovery so transient failures lose only the current item. Reset the consecutive-failure count only after navigation reaches a visible, non-empty page.
- After the configured consecutive-item threshold (default three), open a module-local circuit and explicitly skip remaining items with the environment-precondition reason. Do not spend the navigation timeout for every remaining parameter or contaminate later modules. Do not eagerly obtain a page in a module-scoped executor fixture: construct it without a page and bind it only after the current function-scoped browser fixture has recovered, otherwise pytest caches one setup failure across the parameter set.
- Require post-navigation page readiness in addition to HTTP/navigation completion. Record the URL, attempt count, consecutive failure count, error summary, and failure evidence for an exhausted current item. Keep `EI_BROWSER_NAVIGATION_ATTEMPTS` and `EI_BROWSER_NAVIGATION_CIRCUIT_FAILURES` positive and configurable.

## Environment API Preflight

- After source synchronization and before pytest collection, run only configured read-only API probes matched by the selected form code or component. `GET`/`HEAD` are allowed; `POST` requires an explicit `readOnly=true` declaration and a JSON body. Never probe a write endpoint.
- Preflight probes may reuse the saved browser-session credentials only for the exact target host and must never emit authorization headers, cookies, or storage-state values to run logs, Allure, or diagnostics. A matching HTTP 404 blocks only its dependent commands as `environment-version-mismatch`; network/TLS probe failures remain preflight evidence, not an automatic product defect.
- A concrete HTTP 404 blocks only the commands that depend on that probe, records an environment-preflight report, and must not be reclassified as a product defect. Other status codes remain ordinary execution evidence. Compare an optional deployed-version header with the selected source revision and warn only after a configured persistent mismatch period.
- The launcher runs this preflight only after a successful source sync and before every pytest collection command, then writes `artifacts/runs/environment-api-preflight.json` and appends its blocked/warning counts to the source-sync log.
- `source-sync.log` is a `pathlib.Path`, not an open stream. Rewrite the synchronization output with `write_text`, but append the preflight summary through `append_environment_preflight_summary`, which opens the path in append mode; never call `.write(...)` directly on the `Path`. Cover this with a temporary source-sync log regression.
- Give every launcher batch one generated automation run ID and every dispatched pytest command a stable target sequence. The pair must be forwarded through the command environment so generated data and automation-owned-record evidence can distinguish simultaneous module executions.
- Cover the probe manifest's read-only POST guard, per-command 404 blocking, non-404 pass-through, configured request payload, and delayed version-mismatch warning in `tests/test_environment_api.py`.
- Probe matching must accept either the selected real form code or normalized component path. Missing probe configuration means no preflight target, not a failed test; malformed configuration must block before tests are scheduled.

## Launcher Freshness Guard

- `run_test.vbs` must clean only the process tree recorded by the previous launcher, never kill every Python, browser, or driver process by executable name.
- Record the launcher PID together with its Windows creation time and refuse to terminate a reused PID whose creation time differs.
- Capture a content fingerprint of every `src/ei_ui_smoke/**/*.py` file when the launcher starts. Before any selected test is scheduled, recompute and compare it; if it changed, block the run and require a launcher restart so old in-memory orchestration code cannot run. Read Git HEAD directly only as an optional display label: never depend on `git status`, global Git configuration, user ignore files, or an external Git process for this guard.
- A temporary source-file read failure must not prevent the GUI from opening, but execution must retry the fingerprint and block with its actionable error rather than scheduling tests against unverifiable loaded code. Structural unit-test doubles that call `run_selected` without constructing a GUI may omit it, allowing the existing command-planning behavior to be tested without reading the checkout.
- Preserve this guard with unit coverage for identical and changed fingerprints, and for stale process-record cleanup that cannot remove a newer launcher record.
- Before `run_test.vbs` starts cleanup Python or the launcher, remove inherited `TortoiseSVN\bin` entries from that process's `PATH`. TortoiseSVN installations can ship an obsolete private `MSVCP140.dll`; allowing Python to load it can crash `pythonw.exe` with `0xc0000005`. Keep the system runtime and Git command directories available, and cover the launcher-script contract in `tests/test_execution_guard.py`.

## Environment API Deployment Guard

- Configure new-module deployment probes in `data/environment_api_probes.json`, matching the real `formCode` and/or normalized component path. Each probe must target a source-confirmed read-only endpoint; `POST` is accepted only with an explicit `readOnly: true` marker and a bounded query payload. Never probe save, delete, or other write endpoints.
- Treat `readOnly: true` as necessary but insufficient: reject a probe path containing an explicit write verb such as save, create, update, delete, remove, submit, or upload. Only a successful `2xx` response may establish or advance a source-versus-deployment version mismatch; a 404 or other non-success response is not version-header evidence.
- After source synchronization and before pytest collection, probe only the endpoints required by the selected commands using the saved browser-session cookies and the front-end-compatible `Authorization` token from storage state. A `404` blocks only matching commands as `environment-version-mismatch`; record the endpoint URL and status in `artifacts/runs/environment-api-preflight.json`, do not submit a product bug, and do not report the blocked command as passed.
- Capture the selected run's storage state and source root before removing blocked commands. A batch in which every command is blocked must still emit the complete preflight and version-mismatch report without reading from an empty command list.
- Authentication, TLS, network, and non-404 HTTP failures are not deployment evidence. Preserve their normal test behavior rather than reclassifying them as environment mismatch.
- When an opted-in deployment response emits the configured source-revision header, persist its first observed mismatch. Warn only after the configured duration (default 24 hours); a version mismatch does not silently reroute an endpoint or downgrade a later 404 to success.

# UI 冒烟测试

## 事实标准

开始前先阅读以下文件，只复用现有实现，不另写重复的 Allure 脚本：

- `src/ei_ui_smoke/launcher.py`：图形启动器和多模块执行编排。
- `src/ei_ui_smoke/module_index.py`：源码页面扫描、运行时菜单转换和模块搜索。
- `src/ei_ui_smoke/allure_report.py`：时间戳路径、环境信息、报告生成、latest 路径和报告服务。
- `tests/conftest.py`：`--browser-smoke`、浏览器和登录夹具。
- `tests/test_module_index.py`：扫描层级、标题和去重规则的回归测试。
- `pyproject.toml`：pytest 配置和 `allure-pytest` 依赖。

## 源码扫描与运行时菜单

- 把“扫描”结果视为源码页面索引，不把它当成真实业务菜单。真实中文菜单、用户权限和页面路由必须以“连接并获取菜单”取得的运行时接口数据为准。
- 同时支持 `ei-parent/ei-view` 和 `fi-parent/fi-view` 项目布局；不要把项目名或视图目录名写死成其中一种。
- 菜单采集和测试执行必须先用 `resolve_view_root(source_root).name` 取得所选源码的实际 `*-view`，再统一对齐部署地址。登录页 URL 只替换 `redirect` 目标内最后一个 `*-view` 路径段，直接业务 URL 只替换自身 path 中最后一个该路径段，并保留认证入口、query 和 hash；地址已经对齐时原样返回，避免无变化情况下重编码登录参数。菜单抓取、`EI_BASE_URL` 与 `EI_FORM_URL` 必须使用同一个对齐结果，禁止把一个应用的运行时菜单路由拼到另一个应用基址。
- 把 `index.vue`、`list.vue` 识别为候选入口，排除 `components/component` 内部组件。
- 同一目录同时存在 `index.vue` 和 `list.vue` 时只输出一个页面：优先选择能解析出 `formCode` 的入口；能力相同时优先 `list.vue`。
- 只把静态标题属性作为页面标题。`<PurvarSubTitle :title="variable">` 是绑定表达式，不能把变量名当成业务标题。
- 用页面标题替换最后一级技术目录名，不要把标题追加成假子模块。例如输出 `funds Daily / 定期报告`，不要输出 `funds Daily / daily Regular Report / 定期报告`。
- 扫描失败时立即清空当前树和选择状态，禁止继续展示上一次成功扫描的旧结果。
- 修改扫描器后，用 `ei-parent` 和 `fi-parent` 各执行一次真实扫描，并运行 `tests/test_module_index.py`。
- 页面入口是“仅渲染一个本地组件”的透明包装器时，扫描回归必须覆盖该子组件按钮及祖先容器上的 `$hasButton` 权限；同时保留一个含布局或多个子组件的反例，证明普通嵌套组件按钮不会被提升。
- 修改对话框子模块发现后，使用 public API 组合覆盖“透明包装入口 -> 同文件静态常量 `componentPath` -> 被引用表单的多个静态标题分区”。精确断言同名操作的完整 `operation_path`、源码顺序、外层权限过滤和真实对话框标题；共享同一对话框的新增/编辑调用还要分别成链并继承各自权限。保留未引用组件、无法解析标题和多组件归属不明的反例，证明不会扫目录、跨对话框组合或把按钮串入上一分区。
- 通用新增的源码字段发现复用同一条透明包装和静态组件引用链，使运行时字段映射到真实业务码；多个字段表单候选并存时保持未解析。详细填写与回读规则由 `generic-module-crud-smoke` 维护。
- 页面入口自身没有字段但渲染唯一一个相对 import 的本地字段组件时，源码字段发现应跟进该字段组件，使详情内联编辑等场景仍能拿到稳定业务字段码；存在多个含字段本地组件时保持未解析。字段填写、已有值保持和 generated choice 控件规则继续由 `generic-module-crud-smoke` 维护。
- 源码字段发现遇到表格插槽内的 `el-form-item :prop="\`list.${index}.field\`"` 时，先把动态行号归一为 `list.*.field`，再用同文件表格列配置里的 `prop + label` 补全业务标签，例如 `amount` -> `预算金额（万元）`。这样运行时新增行的 `list.0.field` 能稳定匹配源码字段，不因行号或 Element Plus 生成 ID 漂移。
- 源码字段由模板中的静态 `v-for` 字段配置数组生成时，只展开同文件可确定的静态对象项，并从每项的 `fieldCode` 与 `label` 保留源码顺序生成字段契约。变量数组、缺少稳定字段码/标签或多个候选来源保持未解析，禁止按渲染位置猜测。
- 修改启动器或扫描代码后，关闭已经运行的图形窗口并重新执行 `run_test.vbs`；现有 Python 进程不会热加载新代码。

不要把标题解析正则、去重实现或项目目录清单复制进 Skill。实现以 `module_index.py` 为准，行为以 `test_module_index.py` 为准。

## 新增与持久化校验

把新增测试实现为完整闭环：生成并记录预期值，经 UI 填写和提交，检查保存响应并提取业务主键，再通过当前页唯一列表/只读区域、详情接口或重新打开的编辑表单逐字段比较实际值与本次预期值。

同时检查 HTTP 状态和业务状态。HTTP 200 不代表业务成功；继续检查响应中的 `code`、`status`、`success` 和错误消息。以下任一情况必须使测试失败：

- 找不到新增入口、保存按钮或必填控件。
- 页面显示校验错误、错误消息，或新增弹窗未按预期关闭。
- 未捕获保存响应、保存响应失败，或无法取得业务主键。
- 页面既不能在当前页唯一列表/只读区域完整回读，也没有可用详情响应，还无法重新打开编辑表单核对。
- 当前页、详情或编辑表单没有返回可比较的本次提交字段，或字段值不一致。

不要把点击保存、弹窗关闭、成功提示或只出现记录名的列表回显单独当成新增成功。当前页能唯一定位本次记录且完整核对目标字段时返回 `add_and_list_verified`；详情接口核对通过时返回 `add_and_detail_verified`；确认页面不发详情请求且编辑表单逐字段核对通过时返回 `add_and_edit_form_verified`。

标记为“自动识别新增”的测试不得静默降级为页面访问测试。只有明确标记为“页面访问”的任务才允许仅验证路由和页面可打开。

模块、动作、公共字段发现和公共字段批处理创建数据策略时，统一传 `EI_FORM_CODE`/页面真实 `formCode`；运行时菜单 ID 只用于用例身份和日志，不能作为 stable 数据或业务约束的查找键。

标准/稳定执行可从 `overrides.json` 的精确 `formCode.choiceValues` 取得少量、已验证的合法选择分支；字段码优先、唯一标签别名次之，并由交互层按真实选项文字/value 精确选择。启动器必须继续传真实 `formCode`，否则配置不得按模块名或菜单 ID 回退命中；probe 模式忽略这类页面专属基线。

外部动态集合配置按规范化后的组件路径精确匹配。配置 root 必须选择部署 DOM 中实际稳定渲染的集合表单区，例如 `.adjustment-type-form`；不得依赖自定义 Vue 组件是否把 `field-code` 透传到最终 HTML。组件值可来自 `@/src/views/...`、`srcEi/views/...` 或带 `.vue?query` 的导入路径，规范化后仍应命中同一配置。

运行时菜单项提供了组件路径但未匹配到所选源码时，不具备判定“页面访问”或“支持新增”的可靠契约。启动器必须在启动浏览器前失败并提示源码/部署版本不一致，禁止用 pytest 返回码 `0` 报告成功。新增操作完成当前页列表/只读区域、详情接口或编辑表单回读时，分别接受 `add_and_list_verified`、`add_and_detail_verified` 和 `add_and_edit_form_verified`；三者都是完整闭环，`page_access` 仍不得通过新增断言。

## 测试值与关联控件

- 对文本、数字和日期使用满足格式、长度、范围及字段联动约束的动态值，并记录 `runId` 或随机种子、实际提交值、业务主键、保存和详情接口 URL、预期值及实际值。
- 对下拉、树、组织、人员和业务关联控件，从页面或运行时接口选择真实且可用的候选项；过滤禁用、注销、暂无数据和无权限项，不直接填入随机文本。
- 以运行时网络请求作为保存、详情和候选值接口的事实标准。源码只用于补充路由、`formCode`、字段定义和组件类型，不用源码推断替代真实接口验证。

把企查查作为数据源能力识别，不绑定模块名、单个字段码或中文标签。源码使用 `QccSelect`、DOM 明确包含企查查提示，或运行时使用 `dataManager/entSearch` 时，统一执行：输入关键词、等待搜索响应、过滤无效企业、点击真实候选项，并使用控件最终选中值作为提交预期。字段码和标签别名只作为旧页面兼容兜底。

- Qcc 部署页回归只验证“打开新增、搜索并选择真实候选”，不点击保存；实际模块入口通过 `EI_FORM_URL`、`EI_QCC_ADD_BUTTON` 和 `EI_QCC_FIELD_LABEL` 传入，避免把已废弃的页面或某一个业务模块写死到通用检查中。

随机值和核对结果可以写入 Allure 附件或运行日志，但必须排除用户名、密码、Cookie、Token 和 storage state 内容。

浏览器用例失败时，Allure 只附加两项：移除 query 参数后的失败页面 URL，以及一张失败页面截图。有异常现场缓存时使用清理前的当前视口截图，没有时回退到失败时全页截图。不要附加阶段截图、控制台日志、页面脚本异常、网络请求、请求头、请求体、Cookie 或 Token。

页面字段校验触发后，通过错误节点所属表单项和控件 selector 反查字段，不要求提示文字重复字段标签。把该字段的 DOM/源码结构约束与规范化后的约束类别交给共享数据修复器；禁止为提示完整句子的措辞变体添加页面分支。权限、流程状态、系统、网络、模糊业务、上下界冲突和不支持的 pattern 错误保持失败，不得自动猜值。
底层字段交互定位必须同时覆盖原业务字段码、当前 DOM selector 和规范化标签兜底。规范化标签要去除“请输入/请选择/请上传/请勾选”等提示前缀；可见控件候选不仅包括 input/textarea，也包括 Element Plus/Ant Design 的 select、radio、checkbox、switch、date 和 number 外壳及其内部可操作控件。截图中字段可见而 `Field not rendered` 时，先补共享定位兜底和回归测试，不要写模块专用 selector。
- DOM 字段契约采集数值控件的真实 `min`、`max`、`step` 和源码 `precision`，并在 manifest 中保留这些可选值；缺失值保持 `None`，不得由字段名称或控件大类臆造。字段级用例如何依据这些约束绑定由 `generic-module-crud-smoke` 维护。

在执行器清理页面前，把最终异常消息以临时、醒目的页面提示条合并进同一张现场截图，截图后立即移除提示条；不要为错误文本新增第三个附件。提示条只解释自动化断言，不能伪装成系统自身的报错弹窗。Allure 测试详情中位于图片下方的红色 `statusDetails` 区域不属于 PNG；判断错误文字是否已经写进截图时，直接检查 results 目录中的原始 `*-attachment.png`，并确认该报告产生于代码修改之后。

## 执行流程

执行配置按“标准自动化、快速探测、稳定冒烟”排列，并默认选择标准自动化。标准自动化必须进入独立 `standard` 模式并严格检查所有当前可编辑字段，不得只修改界面文字或复用 `stable` 冒充。企查查控件仍由普通表单字段能力覆盖，不提供单独的企查查验证执行入口。

1. 在项目根目录工作，确认 `.venv` 可用；依赖不完整时运行 `.\.venv\Scripts\python.exe -m pip install -e .`。
2. 先运行 `.\.venv\Scripts\python.exe -m pytest -q`，验证本地契约测试没有回归。
3. 执行已部署 UI 时，使用 `run_test.vbs` 打开图形启动器，或按测试要求设置 `EI_*` 环境变量后运行带 `--browser-smoke` 的 pytest。图形启动器可仅为同一活动批次的 pytest 子进程设置 `EI_DEFER_SKILL_MAINTENANCE_GATE=true`，并必须在批次结束后执行一次正常门禁检查；直接 pytest 仍在会话启动时执行门禁，不得绕过。
4. 图形启动器和直接 pytest 启动前必须把业务源码作为只读输入检查：默认扫描 `D:\Auto_Testing\Project_Purvar\SHZY` 下的直接子级 Git 工程（如 `ei-parent`、`fi-parent`），逐个执行只读的 `git status --porcelain=v1 --untracked-files=all`，只有标准输出为空才通过。仓库不存在、Git 状态命令失败或有任何已跟踪/未跟踪改动时，必须阻断本轮 pytest，并把结果写入 `artifacts/runs/business-source-readonly.log`；不得执行 `fetch`、`pull`、`restore`、`reset`、`clean` 或其他源码写操作。通过 `EI_SOURCE_READONLY_ROOT` 配置检查根目录，兼容旧的 `EI_AUTO_PULL_SOURCE_ROOT` 仅作为根目录别名。批次执行完成后先生成并打开已有 Allure 结果，再执行一次正常 Skill 门禁检查；门禁失败必须显式展示，不能让已完成的运行无报告或伪装为成功。
5. 一次执行只创建一个时间戳 Allure results 目录；多模块测试全部追加到该目录，禁止每个模块互相覆盖结果。
6. 测试结束后生成报告，记录最新结果和报告路径。即使测试失败，也保留 Allure 原始结果与 `artifacts/runs` 日志。
7. 报告生成后通过 `open_allure_report()` 或 `allure open <report-dir>` 启动本地 HTTP 服务。

批量执行失败时，启动器必须汇总并展示全部失败模块的完整层级名称和失败总数，不能只展示列表中的一个模块。多个操作合并为一个 pytest 命令后，命令返回码只代表批次状态；必须从 pytest 失败 node id/参数序号还原具体失败操作，映射回 `EI_ACTIONS_JSON` 中的逻辑目标，禁止把整批失败都显示成批次首个按钮。无法取得逐项状态时才保守列出该批全部逻辑目标。弹窗保持简洁，将逐模块堆栈留在 `artifacts/runs` 日志和 Allure 报告中。

批处理的物理命令数和用户选择的逻辑目标数必须分开。启动前数量、执行状态、Allure 环境中的 `module_count` 和成功弹窗都从各命令的 `EI_ACTIONS_JSON` 展开后统计；一个命令包含七个参数化操作时必须报告七个目标，并列出七个实际执行项，不能显示成一个批次或批次首项。

标准自动化的同一模块、同一操作的通用字段发现和验证可合并为一个批处理 pytest 命令，以复用一次浏览器会话；批处理 runner 必须先生成当前 manifest，再执行既有事务规划，且在日志、失败名称和阶段显示中明确标识为“批量字段验证”。启动器回归必须断言标准命令扩展只产生这一条批处理命令，并仍透传已选工作表、用例 ID 和 manifest。保留独立发现/验证入口用于直接调试和兼容流程，禁止把批处理简化成按 Excel 行的无状态循环。批处理启动后必须用逻辑 `collected/finished` 事件把外层一个 pytest item 替换为全部字段绑定数；每个绑定无论成功、失败或被共享前置条件阻断都只完成一次，忽略随后外层 item 的物理完成事件，进程异常退出或硬超时时把已登记但未回报的逻辑项收敛为已处理，避免进度永久停在中间值。

通用字段批处理的硬超时按“基础预算 + 物理事务数 × 单事务预算”动态计算并设置上限，优先使用当前 manifest 的事务计划，缺失时按已选规则数给出保守预算；允许显式总预算覆盖。禁止沿用普通模块固定 300 秒，也禁止只因为慢而无限放宽上限。日志必须写入最终预算，事务内的详情身份复用和共享前置失败快速终止仍是减少实际耗时的主路径。

同一业务目标包含普通操作、字段发现、字段验证等多个命令阶段时，汇总名称必须附带阶段名，不能产生多个无法区分的同名失败项。后续阶段依赖前置产物时维护显式依赖状态；字段发现失败、超时或无法启动后，跳过依赖其清单的字段验证，保留前置根因，不再制造“清单不存在”的连锁失败。

同一模块批量执行时严格使用启动器树的当前 UI 顺序，不按“新增/编辑/删除”等名称设置人工优先级，也不使用用户点击选择的先后顺序。对话框嵌套操作必须紧跟 `operation_path` 对应的外层操作，并与展示树复用同一有序目标流；每个操作仍须独立构造前置条件，不得依赖上一用例遗留的记录或选中状态。

执行顺序用于保持业务流程可读性。同一 pytest/browser 批次、同一页面已经成功创建自动化记录时，后续编辑、业务弹窗操作和删除应优先复用该记录，避免重复新增；批次中没有可复用记录时，各操作仍必须自行构造安全前置条件。复用记录必须依赖稳定业务名称/主键并在每项开始后重新定位，不依赖残留 DOM 选中状态；稳定名称可以来自自动填写值，也可以来自保持不变的表单预填值，并通过新增结果的独立记录标识传递，不能要求它必须存在于 `submitted`。

为每个模块创建独立命令环境副本，并在设置当前模块能力前移除继承的 `EI_REQUIRE_ADD`。只有明确支持标准新增或新增操作链的目标才重新设置为 `true`，防止上一个 CRUD 模块把设置页误带入新增闭环。启动器与执行器必须复用同一动作语义：`新增…`、`添加…`、`新建…` 等前缀型操作均进入新增闭环，`删除…`、`移除…`、`清空…` 均先创建自动化前置数据；禁止启动器只精确匹配“新增/删除”，而执行器使用前缀匹配，导致 `新增项目` 进入测试后因未设置 `EI_REQUIRE_ADD` 返回 `page_access`。

多模块执行虽然复用同一个 results 目录，但每个模块必须注入非排除的 `module_id` Allure 参数，并设置模块标题与 Suite 层级。不同模块如果重复执行同一个 pytest 测试函数而没有唯一参数，会得到相同 `historyId`，被 Allure 合并成同一用例的重试，只显示一条执行信息。`form_code` 可以作为展示参数，但不能替代 `module_id`，因为部分模块没有 `formCode`。

参数化用例不要让 Allure 展示完整 dataclass/object `repr`，否则灰色参数文本会挤压 Suites 中的业务标题。用 `allure.dynamic.parameter()` 覆盖同名 pytest 参数为稳定短 ID，业务含义放在动态标题中；短 ID 仍须保证同一测试函数内唯一，不能为美化界面破坏历史区分。

通用用例标题在 Suites 中按两行展示：第一行写业务文字，第二行写 `【用例ID】` 或 `【ID1 / ID2】`。原始 results 继续保留稳定参数用于历史区分；生成 HTML 后必须给 Allure 样式补充 `white-space: pre-line`，并清理两行标题用例在 report data 中的展示参数和 `parameterValues`。清理必须幂等且只命中名称含换行 `【...】` 的两行用例，禁止改写其他报告节点或原始 results。不要只依赖 Allure `hidden` 模式，因为 pytest 原生参数仍可能在 Suites 和 Parameters 面板中可见。

公共字段批次把逻辑报告条目数、物理保存事务数和浏览器会话数分开统计。一次选择只建立一个 pytest/browser session；先把兼容的不同字段正向用例规划为物理事务，事务内只打开和保存一次新增表单，再把事务展开为每个绑定字段一个独立 pytest/Allure 条目。各条目标题显示自己的字段、场景和用例 ID，并通过隐藏事务 ID 共享同一份执行结果缓存；成功和失败都不得触发第二次保存。同字段的不同场景及负向、选择和命令用例保持独立事务。整页必填单次提交仍保留一个报告条目，并在条目内列出全部字段结果。

通用 Excel 用例页签要和执行目标的操作语义匹配。给新增页面或新增操作追加公共字段发现/验证时，只运行“新增/添加/新建”页签以及不带明确操作语义的通用页签；明确属于“编辑/修改”或“删除/移除/清空”的页签必须留给对应操作目标。`详情/查看` 页签只挂到 `requires_business_id=true`、没有 `operation_path`、且会打开表单的详情外层操作（如编辑、立项准备、入库申请、跟进），不得挂到取消、关闭、查询、重置、刷新、导出、下载、打印或删除类动作。非新增/编辑名称但已确认会打开可编辑业务表单的操作，保留真实操作名作为 `EI_COMMON_FORM_ACTION` 点击入口，并按“编辑”生命周期匹配 `EDIT-*`；禁止把 `EDIT-*` 用例挂到新增表单上。

- 删除/移除/清空页签不是字段发现或字段填写用例：启动器和直接命令行都必须传递选中的删除用例 ID 到删除专用参数化执行器，不能设置 `EI_COMMON_FORM_ACTION=删除` 后运行通用字段发现，否则会把确认框误判为新增表单。删除专用命令覆盖原始删除操作时，只移除不带删除用例配置的原始命令，保留删除专用命令；Allure 每条删除用例均独立展示用例 ID 和最终结果。
- 同一删除专用命令中的参数化规则共享详情前置条件。首条规则无法进入详情或无法建立自动化子记录时，保留该条的原始失败；同一模块后续删除规则必须以“删除共享前置条件未满足”跳过，不得再次尝试创建数据或重复相同失败。每条规则仍保留独立 Allure 标题和用例 ID。

标准自动化先把已选嵌套 `operation_path` 归一为其外层表单上下文，再追加一次通用字段发现与验证；外层上下文按页面、组件、表单码和外层动作去重，嵌套按钮本身仍只在共享嵌套执行器中执行一次。禁止为每个嵌套按钮重复打开外层表单，也禁止在归一化之前直接丢弃嵌套选择，导致外层字段用例没有计划。

详情子模块中没有 `operation_path` 的外层新增或表单型外层操作可以追加页面级通用字段发现/验证，但命令必须保留 `requires_business_id=true`、详情 URL、完整模块路径和外层操作名。通用执行器每次需要新表单时，先按统一详情导航恢复“父列表选记录 -> 进入详情 -> 逐级进入子模块”上下文；新增类操作继续定位该子模块的外层新增，编辑等表单型操作必须通过 `EI_COMMON_FORM_ACTION` 点击目标操作打开表单，禁止直接回到父列表后误点父级新增。嵌套操作的外层上下文同样适用前述归一化和去重规则。模块专属 Excel 用例除业务名称外还必须按稳定组件身份匹配，禁止仅凭祖先路径含模块名就绑定到详情子模块。启动器回归同时断言详情外层新增和详情编辑类操作生成上下文化通用命令、取消/关闭类动作不生成，并保留真实父级新增覆盖。

每条用户选择的 Excel 行都必须在 Allure 有独立结果：已绑定字段输出执行结果；没有渲染控件、确认能力或执行器支持的行输出带具体原因的 `not_applicable`/`unsupported` 跳过项。禁止仅把这些行写入 coverage JSON 后从报告中静默消失；进度总数也必须包含这些显式结果。

图形启动器已经完成第 4 至第 6 步。修改执行流程时继续复用这些方法：

```python
paths = create_allure_paths(project_root)
write_environment(paths.results, values)
report_dir = generate_allure_report(paths, project_root=project_root)
open_allure_report(report_dir)
```

## 命令行报告

需要单独执行 pytest 时，使用时间戳目录，避免覆盖历史结果：

```powershell
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$results = Join-Path $PWD "artifacts/allure/allure-results-$stamp"
$report = Join-Path $PWD "artifacts/allure/allure-report-$stamp"
.\.venv\Scripts\python.exe -m pytest tests\test_form_smoke.py --browser-smoke --alluredir $results -v
$pytestExit = $LASTEXITCODE
allure generate $results -o $report --clean
allure open $report
exit $pytestExit
```

不要直接双击或链接报告目录中的 `index.html`。Allure 页面会异步加载 JSON，使用 `file://` 打开时会出现 `404 [object Object]`；必须通过 `allure open` 提供的 `http://127.0.0.1:<port>/` 地址访问。

## 图形启动器维护

修改 `src/ei_ui_smoke/launcher.py` 时遵循以下规则：

- 使用 `grid` 权重分配可伸缩空间，让模块列表占据主要剩余高度；环境和执行配置保持紧凑，不设置会挤压列表的固定高度。
- 需要让相邻列按明确比例分配最终宽度时，把列放进同一个 `uniform` 分组并设置对应 `weight`；只设置权重会先受子控件请求宽度影响，不能保证最终比例。修改后按实际窗口宽度核对控件边界，不要仅根据权重值推断视觉结果。
- 通用用例和模块用例各保留独立的“页签”多选框，并额外提供与其联动的可选“用例 ID”多选框。页签变化后只展示所选页签中的 ID，保留仍有效的已选 ID 并移除失效项；未选择任何 ID 时执行所选页签内全部有效 ID，选择了 ID 时只执行所选 ID。清空页签仍须在启动 pytest 前阻止执行，所选页签没有任何有效 ID 时报告配置错误。界面可只显示编号，但内部必须用 `用例ID（页签）` 作为选择身份以区分跨页签同名编号；按工作簿顺序和所选页签分组，每组向 pytest 同时传递单个页签名和最终生效的编号 JSON。模块个性化命令只收集真实业务测试 node，禁止把同文件的加载器契约测试混入 Allure 批次。
- 窗口初始尺寸按屏幕宽高比例计算并居中，保留合理的最小尺寸；不要依赖固定像素尺寸或强制最大化。兼顾 Windows DPI 缩放、任务栏可用区域和窗口恢复状态。
- 将“开始执行”等关键操作放在始终可见的配置区域内，不依赖窗口最底部；缩小窗口后仍应能访问关键操作和状态。
- 为失败自动录入禅道提供当前运行级复选框。图形启动器初始化时默认不勾选，不得因系统环境中的 `ZENTAO_AUTO_SUBMIT=true` 自动授权外部提交；只有用户本轮手动勾选后，才把 `ZENTAO_AUTO_SUBMIT=true` 写入传给上报器的环境副本，不修改系统环境变量。勾选时在启动测试前校验禅道 URL、用户名和密码；未勾选仍生成本地草稿，不执行外部提交。
- 优先复用现有 `StringVar`、回调和测试编排，只调整视图层时不要改动业务行为。
- UI 样式使用明确的主次按钮对比和轻量边界，但不要把一次性的颜色值、间距值或审美偏好固化为 skill 规则。
- 文件选择器根据当前输入值设置 `initialdir`：已有文件时打开其父目录并预选文件，相对路径从项目根目录解析，空值回退到该输入类型的项目约定目录（例如登录状态使用 artifacts、Excel 使用公共用例目录）；约定目录不存在时回退项目根目录。不要依赖系统最近访问目录，也不要把目录路径写入要求具体文件的输入框。
- 启动器隐藏登录状态文件、用户名和密码控件时，仍须自动读取 `EI_STORAGE_STATE`；未配置时加载项目内已存在的 `artifacts/auth-state.json`。菜单连接成功后继续更新内存中的状态文件路径，pytest 仍通过 `EI_STORAGE_STATE` 使用该状态；用户名和密码保留环境变量或内部变量兜底，不在常规执行配置中占位。
- 对已部署系统地址等需要重复输入的非敏感值，使用可编辑 `Combobox` 提供历史选择，同时保留自由输入能力；点击或聚焦输入区时展示历史项，不要求用户必须点击细小箭头。历史按最近成功使用排序、精确去重并限制数量，缓存放在已忽略的 `artifacts` 目录，不写入源码配置。
- 只在对应操作成功后更新输入历史，失败、取消或空输入不得污染缓存。使用临时文件替换方式写入 JSON，损坏或缺失的缓存按空历史处理；只保存业务地址等非敏感值，禁止缓存账号、密码、Token、Cookie、请求头或 storage state 内容。
- 树形控件的展开热区应覆盖父节点的层级单元格，且只有真实存在子节点的行显示展开指示器。具体菜单建模和层级规则以 `skills/runtime-module-discovery/SKILL.md` 为准。
- 登录、菜单接口、详情树和按钮权限等耗时采集不得阻塞 Tk 主线程。用工作线程执行采集，只通过 `after(...)` 回到主线程更新控件、状态和弹窗；开始时禁用重复触发，成功或失败后恢复按钮，避免窗口假死和并发采集覆盖状态。
- 操作节点执行时传递 `EI_ACTION`；嵌套对话框操作同时传递序列化的 `EI_ACTION_PATH`。新增类操作（包括带路径的分区新增）走完整新增与详情回读测试；删除、移除、清空默认复用可验证的自动化记录。用户明确授权“有符合的数据即可删除”时，删除专用用例改为优先选择当前列表任一启用的行级删除按钮；确认、取消和实际删除都复用该选择，验证确认框和删除响应，但不将无关记录与新增 ID 比对。没有可删行才创建一条前置数据后重试。
- 批量动作序列化时，缺失的可选 `form_code` 可能变成字符串 `None`、`null` 或 `undefined`。执行器必须把这些哨兵视作未提供，回退到本次目标的真实表单编码；不得让它们覆盖数据策略、动态字段契约或声明式组合唯一约束。
- 表单编码回退仅影响缺失值解析；已有真实编码仍原样传递，并且同一解析结果必须同时用于数据策略和动态集合契约，避免一个执行路径已应用组合唯一键、另一条路径静默失效。
- 当动作工作表没有提供表单编码且环境中也没有有效编码时，按当前组件的顶层视图目录从已同步源码解析唯一的 `FORM_CODE` 后再创建数据策略；解析失败才回退模块 ID。此规则确保删除等独立动作命令仍能应用已声明的组合唯一约束。
- 详情子模块的行级删除、移除、清空按钮可能只有表格存在数据行后才渲染。进入详情菜单时不要把这类目标按钮作为导航成功前置条件；先用同一子模块内可见的安全前置入口（通常是“新增”）确认已进入目标组件，再由执行阶段创建自动化自有记录并只对该记录执行破坏性操作。
- 详情页的“编辑、立项准备、入库申请、跟进”等表单型操作点击后不一定打开 Dialog/Drawer，也可能在当前详情组件内原位切换成编辑表单。执行器应接受“新弹窗/抽屉出现”或“当前详情页出现可编辑控件及保存/取消命令”两种效果，并复用同一保存、响应捕获和字段填写闭环；保存/取消命令可以位于表单兄弟工具栏或页脚，不必在 `.el-form` 内。禁止因没有弹窗就判定编辑未执行。`取消编辑/取消修改` 属于进入编辑态后的命令，详情导航和候选记录筛选先用可见的“编辑/修改”入口，进入编辑态后再执行取消并验证返回只读态或弹窗关闭，不得把取消编辑当成详情初始按钮直接寻找。
- 同一页面已经选择明确操作节点时，不再额外调度该页面的普通 `module_smoke`；页面访问和新增能力已由操作批次覆盖，否则会在操作“新增”和删除前置之外再产生一次隐式新增。标准自动化已经为某个新增目标生成通用 Excel 用例或模块个性化用例命令时，同样移除该目标原始的 `module_action`、`module_smoke` 或 `form_smoke`，只执行字段发现和用户明确选择的 Excel 用例；不得在 `ADD-*` 用例前额外执行一次泛化“新增/新增项目”。其他未被 Excel 用例覆盖的查询、编辑、删除等目标保持原计划。批次新增结果供后续操作复用；若前序状态变更使自动化记录的删除按钮按业务规则禁用，保持 UI 顺序且不得绕过禁用状态，只额外创建一条专用于删除的自动化记录并立即确认删除。
- 把用户选中的每个稳定操作目标恰好执行一次。生成执行计划时按“运行时菜单 ID + 规范化操作名 + `operation_path`”去重并保持树中顺序；父子同时选中、`ALL` 展开或同名按钮不得重复加入同一目标。启动前在日志中写入最终目标总数和顺序，执行中显示当前序号。
- 提供批量排除删除操作时，先把当前父子选择解析成明确的可执行叶子目标，过滤 `operation` 中的删除操作，再用剩余叶子重建 Treeview selection。禁止只取消可见删除行而保留已选父节点，否则目标解析会再次把删除后代隐式加入；名称包含“删除记录”但没有删除 `operation` 的普通页面应保留。
- 同一轮选中的所有操作按钮合并为一个操作计划，只启动一次 pytest 进程和一个 session 级 Playwright 浏览器上下文；用 pytest 参数化把计划展开成独立测试用例，按树中有序目标流执行，每项开始前导航到自身页面 URL，以清理上一个模块的弹窗和页面状态。源码或权限过滤改变树顺序后，重新获取菜单得到的新树顺序即为执行顺序；普通页面/表单测试不混入操作批次。
- 操作批处理必须为每项保留模块 ID、模块名称、页面 URL、组件、`formCode`、操作名和 `operation_path`。单项失败时记录“当前序号/总数 + 操作名”，重新导航后继续执行剩余操作，最后统一汇总，不能让首个失败跳过后续按钮；整批硬超时按单项超时乘操作数计算，避免合并后沿用单项上限导致误杀。
- `requires_business_id=True` 的详情操作必须区分“浏览器首次入口”和“目标详情描述”。首次入口使用父列表 URL，目标仍保留详情层级、组件和操作信息；登录恢复、浏览器重建及 session 级 fixture 都不得先访问合成的 `*/detail`，否则测试函数执行前就可能被 SPA 路由守卫送到 `#/404`。进入父列表后由操作测试点击真实记录并按运行时层级导航目标详情子模块。父列表记录没有“详情/查看”按钮时，优先点击该记录内部的业务名称超链接；若点击记录后 URL 未变化但当前页已经出现目标详情菜单、目标组件或目标操作，把当前列表视为详情承载页继续执行，禁止因为没有详情按钮或路由未变化直接失败。
- 详情导航每次回到父列表后，必须先等待可见加载遮罩消失，并确认当前可见记录的业务标识/文本快照在有界安静窗口内保持稳定，再取得记录 Locator 和点击。遮罩后的残留行、列表显示 `0` 条期间的旧行或持续重渲染的行都不得作为可点击记录；父列表未就绪或点击前仍在刷新时生成一次带 `loading`、记录数和 URL 的前置失败，不要按候选记录数重复耗尽点击超时。
- 详情候选记录序号必须跨分页解释：从第一页取得真实页大小，按绝对候选序号计算目标页和页内位置，通过分页器跳页并等待新页记录稳定后再点击。禁止每次返回父列表都停在第一页，导致“前 N 条”实际只覆盖第一页。目标操作文字已经渲染但按钮处于禁用态时，短暂等待异步启用后立即判定该业务记录不适用并继续下一条；默认扫描上限由 `EI_DETAIL_RECORD_SCAN_LIMIT` 控制。候选耗尽时应区分“页面没有操作”和“操作存在但因业务阶段禁用”。
- 同一详情通用字段批处理首次选中可用父记录时，在点击前提取唯一行键、业务 ID 或业务名称并缓存其纯值；tuple/list 标识按其成员归一化，不能先按对象属性读取后因空占位丢失。终结事务重开表单时只按该值重新定位同一记录，不缓存 DOM Locator；仅在精确身份确认不存在后清空缓存并重新选择一次，身份命中多条时直接失败。
- 详情树节点在真实菜单层级之后还可能包含外层操作、弹窗标题、分区标题和嵌套操作。构造详情导航计划时，在“详情”后的第一个外层操作名处截断；截断前才是页签/纵向菜单，截断后全部交给父弹窗和嵌套操作执行器。进入候选业务记录前只生成并自检一次该计划，日志输出 `DETAIL_NAVIGATION_SELF_CHECK`；禁止把弹窗或分区层级当菜单对每条记录重复等待。
- 批处理连续操作同一个 hash 路由时必须强制 `page.reload()`，不能只调用同 URL 的 `page.goto()`；否则上一个弹窗/遮罩仍在，后续页面按钮会被 overlay 拦截并连续耗尽 Playwright 点击超时。跨路由使用 `goto()`，每项开始后实时输出 `ACTION_START/PASSED/FAILED`，pytest 使用无捕获输出，使运行日志能立即显示具体卡在哪个按钮。
- Allure 中每个操作按钮必须对应一个独立参数化用例，使用该项自己的模块 ID、模块名称、`formCode` 和操作标题；禁止把整批包装成首个按钮（例如“查询”）的一条用例。单项失败不得阻止 pytest 继续执行后续参数项。
- Add source-discovery regression coverage for a static `v-for` field array. The asserted manifest must contain every explicit `fieldCode` and label in source order, never the Vue loop expression; this coverage is required before using the manifest for required-field or attachment validation.
- 新增或编辑被执行多次时，先比较执行计划、模块日志头和 pytest node id，区分“目标重复入队”“同一测试被收集多次”和“驱动内部重试”。保存请求已发送或业务已成功后禁止重新点击新增/保存；定位或回读失败只能进入对应诊断，不能从打开新增页面重新开始制造重复数据。
- 执行查询、重置等普通操作时，`domcontentloaded` 只表示 HTML 已加载，不表示 Vue 路由和按钮已经渲染。对目标按钮使用可见性条件等待后再判断缺失；禁止用即时 `count()` 造成毫秒级误报，也不要在失败截图钩子中增加固定延迟掩盖页面就绪问题。
- 验证界面改动前先关闭旧启动器进程并重新启动，避免把仍在运行的旧界面误认为新代码未生效。
- `run_test.vbs` 通常只负责拉起启动器，VBS 宿主退出不代表 `pythonw` 启动器或 pytest 子进程已经停止。停止时先按进程命令行中的仓库路径、`run_test.vbs`/启动模块、启动时间和父子关系确认本轮进程，再依次停止对应 pytest/浏览器子进程和启动器；不要用“结束全部 `wscript.exe`/`pythonw.exe`/Chrome”作为停止方法。重新验证代码前确认旧启动器已退出，再执行仓库中的 `run_test.vbs`。
- 至少检查常用窗口尺寸、最小尺寸和 DPI 缩放下的布局，确认控件不重叠、不被任务栏遮挡，模块表格和滚动条可用。

## 验证要求

- 确认 pytest 已生成 `*-result.json` 和 `*-container.json`。
- 确认报告目录存在 `index.html`，且 `allure generate` 返回成功。
- 用 HTTP 地址检查 Overview、Suites 和测试详情能够加载，不以本地 `index.html` 能显示侧栏作为成功标准。
- 修改 Allure 工具或启动器后，至少运行 `.\.venv\Scripts\python.exe -m pytest -q`。
- 修改启动器后先运行 `.\.venv\Scripts\python.exe -m py_compile src\ei_ui_smoke\launcher.py`，再运行完整 pytest。
- 修改输入历史时覆盖：缓存缺失或损坏、成功后写入、失败不写入、最近项置顶、重复项去重、数量上限和敏感字段不落盘。
- 修改操作调度时覆盖：用户选择顺序被打乱后仍按树中 UI 顺序输出；对话框嵌套操作紧跟对应外层操作；`EI_ACTIONS_JSON` 保持同一有序目标流；查询、重置等操作不被名称优先级重排。
- 修改批量结果汇总时覆盖：同一命令内部分参数通过、部分失败；全部失败；无法解析逐项状态；不同辅助阶段同名；前置阶段失败后依赖阶段不再执行。
- 若缺少 `allure-pytest`，安装项目依赖；若缺少 Allure CLI，明确报告原始结果仍可用，但 HTML 无法生成或打开。

## 失败诊断与结果分类

### 默认自修复闭环

- 启动器、模块操作和公共字段入口向数据策略传递的必须是解析后的真实业务 `formCode`。序列化产生的空值字符串 `None`/`null`/`undefined` 一律按缺失处理，再从设置或运行时组件解析；不得把模块 ID、动作 ID 或字面量空值传给策略，否则页面专属唯一约束和数据覆盖会静默失效。删除用例创建前置数据时也必须遵守同一规则。
- 字段定位失败必须在异常与字段诊断中输出当前表单的无值运行时库存：业务代码、标签、控件类型和当前选择器。用该证据先区分字段未渲染、身份映射错误和动态选择器失效；禁止要求人工先指出漏掉的字段，或用旧 `el-id-*` 猜测定位。
- 部署页面浏览器初始导航失败时，以全新 browser/context/page 重试当前 pytest 项的有限次数，并关闭失败会话；成功即清零失败计数。连续失败达到模块级阈值才熔断后续项并标为浏览器/环境前置，不能把一次空白或超时会话归为字段定位、输入或产品保存失败。
- 任一模块未通过时，除非用户明确只要求诊断，否则默认执行“读取本轮日志、Allure 与字段诊断 -> 写出可证伪根因 -> 修复公共根因 -> 运行定向回归 -> 复跑同一真实模块”的闭环。不得停在首次失败分析、只修本地测试，或跳过失败模块继续宣称批次完成。
- 修复必须落在最小的通用职责层，并为根因补回归测试；禁止仅按模块名、动态 DOM ID、临时按钮位置或本次测试数据写硬编码。修复模块发现后复跑发现用例，修复字段/交互后复跑 CRUD 用例，修复编排后复跑启动器用例，最后运行完整 `pytest -q`。
- 只有同一真实目标通过，或形成可复现且已排除自动化提交、权限、登录、版本、网络和测试数据问题的系统缺陷证据，才结束闭环。HTTP 5xx、“系统异常”、页面白屏或截图中的错误提示本身都不足以直接判定系统 Bug。
- 系统缺陷或外部阻塞至少保留：模块完整层级、运行时路由、操作路径、失败阶段、最终 pytest 状态、脱敏后的 HTTP/业务状态与消息、失败 URL 和全页截图。权限不足、登录失效、源码与部署明确不一致、网络/数据源不可用要分别报告，不得统称为系统 Bug。
- 命令包装器或工具调用超时不等于 pytest 失败。超时后先检查对应 pytest/浏览器进程是否仍在运行，再读取本轮时间戳 results 目录中的最终 `*-result.json`、模块日志和字段诊断；没有目标用例终态时报告“执行被中断/结果不完整”，禁止根据前置辅助用例或 `latest.json` 宣称通过。
- 启动器执行操作批次时，为本轮收集和执行子进程设置 `EI_DEFER_SKILL_MAINTENANCE_GATE=true`，全程不校验正在运行的操作；所有操作结束后立即统一运行正式维护 Skill 门禁，检查本轮方法是否已写入所属 Skill。后置门禁失败时列出待处理文件，不等待、不映射成模块业务失败、不自动登记，也不提交禅道；直接从命令行运行 pytest 时仍保留会话启动门禁。
- 测试子进程结束后必须先从本轮 Allure results 生成并打开 HTML 报告，再执行后置维护 Skill 门禁。后置门禁失败只阻止禅道处理和最终成功汇总，不得删除原始结果、跳过报告生成或把已完成的业务测试描述成尚未执行。
- 检查残留进程时按启动时间、命令行和本轮 results/module ID 确认归属。只终止本轮 pytest 及其浏览器子进程；禁止批量结束所有 `pythonw`、`wscript` 或 Chrome，以免停止用户的启动器和其他任务。

- Windows 桌面启动器自身可使用 `pythonw.exe`，但 pytest/browser smoke 子进程必须优先使用同一虚拟环境的 `python.exe`，否则子进程启动失败可能没有控制台和有效日志。
- Windows 图形启动器调用 pytest、Allure generate/open 或其他仅写文件日志的后台命令时，统一传 `CREATE_NO_WINDOW`；不要让无控制台的 `pythonw.exe` 父进程触发 Windows Terminal 新窗口。浏览器进程不隐藏，pytest/Allure 标准输出继续定向到运行日志或管道。
- 页面和操作节点的模块 ID 可能包含 `/`、`\\`、`::action::` 等字符。创建 `artifacts/runs` 日志前必须统一替换 Windows 非法文件名字符和控制字符；禁止直接把模块 ID 拼成日志路径。
- 在启动 pytest 前创建并刷新日志头，记录模块 ID、测试文件和脱敏命令。子进程启动异常也必须写入该日志并恢复启动器窗口，不能只留下空的 Allure `environment.properties`。
- 批量 pytest 执行与菜单采集一样不得占用 Tk 主线程，也不要通过 `withdraw()` 隐藏窗口制造“已经结束”的错觉。用守护后台线程串行执行目标，主线程保持窗口可操作，并显示当前模块层级和测试阶段；执行期间禁用开始按钮，最终统一恢复并弹出汇总。
- 批量执行期间在常驻状态区显示用例级进度：分母汇总各实际启动 pytest 进程在 `pytest_collection_finish` 中收集到的 item 数，分子只在 `pytest_runtest_logfinish` 收到唯一 `(command_id, nodeid)` 终态事件后递增；任一仍活跃命令尚未完成 collection 时分母保持未知，只有全部活跃命令都已收集或被明确移除后才求和。禁止再用物理 pytest 命令批次数、Excel 行数或原始操作数代替总用例数。启动器与 pytest 通过独立 JSONL 事件文件传递 `collected`、`finished`，不得解析面向人工的 `-v` 输出。
- 通用字段验证依赖本轮字段发现生成的 manifest；该依赖尚未完成时分母保持未知，禁止读取旧 manifest，也禁止为验证命令提前启动独立 `collect-only`。发现成功后，启动器用共享纯规划器按该 manifest、当前 Excel 页签和选中 ID 立即登记对应验证命令的报告项总数；正式运行的 `collected` 事件只复核并覆盖同一命令的数量，不重复计数。发现失败且验证命令未启动时将其从本轮总数中移除。总数确定后统一显示 `N/M` 和百分比。进度限制在 `0..100`，超时、进程异常或中断时保留真实的未完成比例，只有所有已收集 item 均到达终态才能显示 100%。
- 为尽早显示准确总数，执行计划必须先运行所有仅生成本轮 manifest 的通用字段发现命令，再运行详情校验、普通页面操作和字段验证；只前移发现辅助阶段，用户选中的页面操作相对顺序不得改变。同一个 manifest、模块、入口 URL、组件、外层操作和 `operation_path` 构成一个物理表单发现身份，Excel 页签与用例 ID 只是该 manifest 的验证消费者，不得让“编辑页签 + 详情页签”等组合对同一表单重复启动 pytest/浏览器发现；不同路由、组件或表单操作仍须保持独立。一次发现成功后，分别按每个共享消费者自己的页签和选中 ID 登记纯规划数量；发现失败、超时或无法启动时立即移除该身份下全部尚未启动的验证命令。禁止用旧 manifest、Excel 行数或额外 pytest 进程替代。
- 通用字段验证尚未全部登记总数时，进度文案必须显示已完成用例数、物理表单“字段发现进度 X/Y”、当前已登记用例数和剩余待发现组数；只有没有可识别发现组但总数仍未知时才回退到“等待后置字段校验登记总数”。不得笼统显示“正在统计总数”，该阶段通常仍在执行前置发现或已收集用例，避免误导为统计线程卡死。
- 每个测试目标必须设置独立硬超时（默认 300 秒，可由 `EI_MODULE_TIMEOUT_SECONDS` 调整）。超时后终止该 pytest/Playwright 子进程，将超时秒数写入对应模块日志并计为失败，然后继续汇总其他目标；禁止让一个新增操作无限阻塞整批任务。
- 操作批次调用 `tests/test_module_action.py` 时必须指定 `::test_selected_page_action`，不得把文件内用于本地回归的辅助测试一起写入 Allure，避免一个页面操作在报告中显示成多次执行。
- 删除、移除和清空操作无论由图形启动器、批量 `EI_ACTIONS_JSON` 或单命令环境变量启动，都必须在执行器中保留 `EI_REQUIRE_ADD=true`，以便没有安全复用候选或候选不可删时仍能创建并定位本次 `AUTO_` 自有记录，再确认删除及验证接口响应和记录消失。不得让单命令回退路径清除此开关，否则详情父列表无法构造前置数据，或删除会误操作业务记录。
- 删除 Excel 页签属于专用参数化执行：命令必须保留 `EI_COMMON_DELETE_CASES_EXCEL`、页签和 `DELETE-*` ID，并单独运行 `tests/test_module_action.py`。普通动作批量合并只序列化动作元数据，不能合并这类命令，否则会丢失专用环境变量并静默退化为普通删除。
- 操作节点的 `::action::<position>` 后缀只在当前树实例内有效。执行计划和失败重试不得在重新获取菜单后复用旧位置 ID；重新解析稳定操作身份并确认仍对应同一操作后再调度。
- 普通操作不能以“点击后页面未关闭”作为通过标准。查询/搜索/重置必须监听点击后实际发出的 XHR/Fetch request，不得把固定时间内尚未到达的 response 误报成“未发请求”；使用有界等待并在每项操作后移除 request/response 监听器，真实未发请求仍失败。编辑、立项准备、入库申请、跟进等弹窗操作必须出现新的可见业务弹窗。删除、移除、清空若没有自动化自建记录，只能验证入口或在确认框取消且不得宣称删除闭环通过；正式删除用例必须对自建记录点击确认，并验证删除响应和记录消失。未观察到对应效果时必须失败。
- 快速重复点击保存时，第二次点击被 UI 禁止且只观察到一次业务保存响应是有效的防重复结果，执行器返回 `rapid_click_blocked_by_ui`，公共字段验证入口必须将其视为通过。若上层允许结果集合漏掉该值，属于测试契约错误，不得作为产品 API 缺陷上报。
- `EI_ACTION_PATH` 只在确有至少三段嵌套操作路径时传递和启用。空元组序列化出的 `[]` 必须移除或解析为空路径，禁止仅按环境变量字符串非空判断；否则所有顶层查询、重置、新增、编辑、删除都会误走嵌套新增分支，只打开父新增弹窗后假通过。

把失败截图视为断言发生时的浏览器状态，而不是独立的根因结论。会在异常后通过 `finally` 关闭弹窗、收起下拉或导航离开的执行器，必须在清理前捕获当前视口现场并交给统一 Allure 钩子；钩子优先附加现场图，没有现场证据时才回退到失败时全页截图，且最终仍只保留一张“失败页面截图”。截图仍显示加载遮罩、空弹窗或骨架屏时，先对照最终断言和页面就绪条件，排查是否在动态内容渲染前开始扫描；不要先认定截图抓取错误，也不要把“尚未加载”归类为字段全部缺失。截图只能证明断言发生时的可见页面状态，不能单独证明某个网络响应不存在；这类结论必须同时来自断言消息、响应监听逻辑、用例时长和本轮原始结果。

按实际完成的最深阶段报告结果，禁止把前置步骤成功扩大解释为业务成功：

| 现象 | 正确结论 | 首要检查 |
|---|---|---|
| 出现源码技术目录或“待获取路由” | 只完成源码扫描 | 已部署 URL、登录状态、运行时菜单请求 |
| 页面能打开，但没有点击新增 | 页面访问成功 | 模块是否有新增入口、当前用户权限 |
| 点击保存，但没有捕获保存响应 | 新增失败 | 按钮作用域、请求监听时机、保存接口 |
| HTTP 200，但响应体业务码失败 | 新增失败 | 响应消息、必填/联动值、后端校验 |
| 保存返回 ID，但列表没有记录 | 尚未完成闭环 | 筛选、分页、租户、列表刷新、详情或编辑回读 |
| 详情模块没有业务 ID | 前置条件缺失 | 从列表已有记录或新增结果取得 ID |
| pytest 显示 `SKIPPED` | 未执行，不是通过 | `--browser-smoke`、URL、formCode、环境开关 |
| Allure 页面 404 | 报告服务方式错误 | 使用 `allure open`，不要使用 `file://` |

诊断时保留并关联模块名称、运行时路由、数据模式、保存 URL、HTTP 状态、业务消息、业务 ID、详情 URL 和 pytest 最终状态。凭证、Cookie、Token 和 storage state 内容必须脱敏或省略。

涉及模块树时读取 `skills/runtime-module-discovery/SKILL.md`；涉及真实新增闭环时读取 `skills/generic-module-crud-smoke/SKILL.md`；涉及字段值来源时读取 `skills/smoke-test-data-strategy/SKILL.md`，不要在总控 Skill 重复维护其细节。

将失败结果录入缺陷系统或判断严重程度、优先级时，读取 `skills/bug-severity-priority/SKILL.md`，并以 `src/ei_ui_smoke/bug_priority.py` 的自动判级结果作为初始建议；需要人工复核时不得自动宣称最终定级。

将 Allure 失败项正式录入禅道，或修改产品、项目、模块、正文截图、附件和保存校验逻辑时，读取 `skills/zentao-bug-submission/SKILL.md`。不要在本 Skill 重复维护禅道内部接口细节。

## Date Picker Interaction Contract

- Interaction-unit coverage for a date picker must prove that date fields route to picker selection instead of input `fill()`, the requested date cell is clicked and read back, and an open picker is closed before subsequent commands.

## Parametrized Common-field Reporting

- One selected common-field command must start one pytest process and one browser session. Use `tests/test_common_field_validation.py` as the launcher entry point, not the aggregate Batch test, so pytest collection and Allure expose one independent item for every bound field.
- The command may share physical transactions through the executor cache. Field-level results must be read from the cached transaction by stable item index, including required-field batches; do not reopen a form or repeat Save merely to create another report item.
- The common-case and module-case worksheet selectors start empty and retain only explicit valid selections after a workbook refresh; their labels must state `未选=全部`. At run time, an empty worksheet selection expands to every worksheet in workbook order; an empty case-ID selection expands to every available case in that effective worksheet scope. The launcher must still emit one command per worksheet rather than passing a multi-sheet value to a single-sheet pytest loader.

## Dynamic Collection Configuration Coverage

- A change to `data/dynamic_collections.json` must have a loader regression that asserts the exact matching `formCode`/component, section title, rendered column headers, declared child paths, and semantic kinds for numeric and select controls. The regression must reject selectors that assume a Vue component `prop` is a rendered DOM attribute. Keep generic driver behavior tests separate from this configuration contract.
- A configured `valueRelations` entry must be covered as part of the exact collection contract: both endpoints are numeric children of that collection, the operator is supported, and `adjustOrder` names a nonempty unique subset of relation sides. Reject cross-collection, missing-child, nonnumeric, or ambiguous adjustment declarations during manifest loading rather than at Save time.
- When an exact section-scoped root already makes a collection unique, its create selector should use the source-confirmed rendered toolbar/button classes and runtime visibility checks. Do not add unverified direct-child or exact-text pseudo constraints that can reject a visible framework-wrapped button; assert the final root/create/item selectors in the configuration regression.

## 安全边界

- Failure attachments may include an explicit sanitized diagnostic object and a bounded DOM structure snapshot containing only selected visible-container selectors, counts, tag names, class names and ARIA state. Never retain full page DOM/HTML, text content, form values, console output, request bodies, credentials, Cookie, Token, or storage state.
- Keep a failure-evidence regression proving that a diagnostic object survives capture/consumption while no DOM snapshot attribute is retained and no DOM-read evaluation is issued.

- Runtime field location must use the same label-class vocabulary as DOM discovery. When a form exposes only a framework-specific `*label*` class, retain it in locator fallback coverage so required-validation evidence can resolve the same visible control.
- Browser navigation recovery is a shared execution precondition: preserve the first navigation failure evidence, recover one fresh browser session through the navigation circuit, and stop the affected command when the circuit opens. Do not let each parametrized field retry a failed navigation independently, because one route outage would otherwise be reported as many unrelated field failures.
- Cover failure evidence as part of the browser-runner contract: capture failures must tolerate screenshot or page-evaluation errors, and consuming evidence must clear the page-local cache so a later result cannot inherit an earlier screenshot.

- 不把用户名、密码、Cookie、Token 或 storage state 内容写入 Allure 环境信息和附件。
- 不清理历史报告，除非用户明确要求；使用新的时间戳目录。
- 不让报告生成错误掩盖 pytest 的原始退出码和失败日志。
