# 业务流程编排参考

## 定义边界

- `WorkflowDefinition` 声明稳定 `workflow_id`、标题和有序 `WorkflowStep`。
- `WorkflowStep.handler` 接收一个 `WorkflowStepExecution`，必须返回 `WorkflowStepResult` 或 `ModuleSmokeResult`；`None` 是契约失败，不能把未实现步骤报告为通过。
- 创建步骤声明 `produces_business_id=True` 和 `created_record=True`。只有确实允许后续清理时才同时声明 `cleanup_allowed=True`。
- 后续步骤声明 `depends_on`、`requires_business_id=True`，并用 `requires_status` 和 `expected_status` 定义前后态。
- handler 每次从 `execution.page` 和 `execution.require_business_id()` 开始重新导航、查询和定位。不要把 DOM 对象写进上下文或闭包供下一步骤复用。
- 直接返回 `ModuleSmokeResult` 时 Runner 只提取业务 ID、自动化标识和 driver 结果模式。需要断言业务状态时，使用 `WorkflowStepResult.from_module_result(..., actual_status=...)` 包装结果。
- 创建 handler 在取得已确认的业务 ID 后、继续做可能失败的额外回读前，调用 `execution.checkpoint(result)`；这样后续断言失败时快照仍保留本轮记录和清理台账。checkpoint 不代表步骤通过。
- 创建数据复用 `ModuleSmokeDriver` 的 UI 保存与回读闭环，或由页面动作触发真实业务请求；流程测试不直写业务数据库。前置数据也必须保留本轮自动化归属和真实业务校验，不能靠数据库脚本绕过权限、校验或流程状态。

Factory 使用 `python.module:factory` 形式。factory 接收 `WorkflowBuildContext`，并必须返回与 `build_context.workflow_id` 相同的 `WorkflowDefinition`。页面定位、网络监听、角色业务动作和复杂分支留在 Python handler 中，不创建步骤表达式 DSL。

## 运行配置

必需环境变量：

- `EI_WORKFLOW_ID`：稳定流程 ID，也是 live 入口的启用开关。
- `EI_WORKFLOW_FACTORY`：Python factory，例如 `tests.workflows.project_approval:build_workflow`。
- `EI_WORKFLOW_ROLE_STATES_JSON`：只允许 `maker -> storage-state 文件路径` 的 JSON object；禁止内嵌 state、Cookie 或 Token。仅在其他固定角色步骤存在时才额外声明该角色。
- `EI_WORKFLOW_LOGIN_PASSWORD`：动态处理人登录的固定密码；只从进程环境读取，禁止写入流程 JSON、快照、Allure 或日志。

启动器内建 `resource-pool-approval`（资源池入库审批），会自动选择 `ei_ui_smoke.workflows.resource_pool_approval:build_workflow` 和 EI 项目；自定义流程仍可填写自己的 `python.module:factory`。内建流程只要求经办人 state、固定密码和 `EI_RESOURCE_POOL_APPROVAL_CONFIG_JSON`；处理人由严格配置的流程预览接口返回，不能预先在 UI 中配置或复制审批人 state。

选择内建流程时，通过启动器采集经办人登录态；采集使用可见浏览器并保存到 `artifacts/workflow-auth/` 下的独立文件，成功后自动生成 maker-to-file JSON。若没有经办人 state，由使用者在弹出的浏览器中完成登录；登录与菜单响应仍受有界超时约束。动态处理人 state 缓存只由运行器管理，文件名不得暴露 loginName。不要复制普通 `auth-state.json` 充当处理人，也不要把 state 内容粘进流程 JSON。

角色文件校验会在 Chromium 启动前解析明确的 JWT 证据：数字 `exp` 已过期即失败；多个角色能唯一解析到同一稳定主体时也失败。opaque token、没有 `exp` 或没有可靠主体 claim 的认证不会被臆测为失效或同一账号，后续仍由真实页面/API 前置检查判定。错误信息不得包含 token、claim 值或 state 文件路径。

复用现有 `EI_DATA_MODE`、`EI_AUTOMATION_RUN_ID`、`EI_AUTOMATION_TARGET_SEQUENCE`、`EI_HEADLESS` 和页面/业务配置。命令环境不得带入 `EI_ACTION`、`EI_ACTIONS_JSON`、`EI_ACTION_PATH` 或 `EI_ACTION_PATHS_JSON`。

启动器为完整流程提供独立的外层进程超时，默认 1200 秒，可用正整数 `EI_WORKFLOW_TIMEOUT_SECONDS` 调整；每个网络读取、状态轮询和页面动作仍必须保留自己的更短超时，不能依赖外层预算终止失控步骤。

Live pytest 入口在模块内通过 `pytest_generate_tests` 建立唯一流程参数。未选择 `EI_WORKFLOW_ID` 时只参数化一个带 skip mark 的占位项；一旦选择流程，就在 collection 阶段完成 factory、mode、步骤依赖和角色 state 配置校验，配置错误直接抛 `pytest.UsageError`，不得进入 test call 或生成 failed/broken 业务结果。

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_business_workflow.py::test_selected_business_workflow --browser-smoke --data-mode standard -q
```

Runner 会把原子快照写到 `artifacts/runs/workflows/<run_id>/<workflow_id>.json`。快照是状态与归属证据，不是跨进程动作缓存，也不授权删除未登记记录。

快照中的 `observations` 只保留安全标量白名单：`business_code`、`business_status`、`http_status`、`module_result_mode`、`mutation_count`、`poll_attempts`、`request_count`、`response_count`、`state_source`、`status_code` 和 `transition`。未知字段、复杂对象、响应体及凭证相关内容直接省略，不依赖敏感词黑名单兜底。

## 步骤断言

每个真实业务迁移步骤至少确认：当前角色可执行动作、对应写请求只发生一次、HTTP 和业务码成功、响应业务 ID 与上下文一致、按该 ID 有界回读到目标状态。切换角色前先完成本步骤断言；切换后从该角色自己的 BrowserContext 重新按业务 ID 查询记录。状态 probe 自身的网络/UI 读取也必须带独立超时；`wait_for_status()` 在每次 probe 返回后检查总截止时间，不能中断一个内部无限阻塞的同步调用。

主业务记录 ID 与流程引擎 business key 必须分开建模。例如资源池记录使用 `business_id`，提交入库返回的项目 ID 登记为 `correlation_ids["projId"]`。审批请求或响应通过 `mutation_correlation_key="projId"` 证明关联身份，Runner 只接受步骤开始前已经登记且值一致的 correlation；禁止同一步先返回新 correlation 再用它证明自身。

## 资源池审批 Adapter

- 固定业务接口为 `/ezgo/ei-service/projStorage/list`、`/add`、`/detail`、`/rk`；待办读取为 `/ezgo/ezgo_api/client/v1/common/toDoTaskByPage`，请求必须带源码确认的 `query.app_id=app_49z06fqkug`。流程预览接口和节点字段未在本仓库业务源码中确认，必须由 `process_preview` 显式配置为只读 GET 或带受限 JSON body 的 read-only POST；不得编造默认路径。
- `approval_response.url_path` 必须是审批任务页实际发出的写请求，明确拒绝 `/projStorage/approval` 回调路径。该回调由 BPM 调用业务系统，不能证明审批人在 UI 中只执行了一次动作。
- `approval_response.business_code_path + success_values` 只能是 `state + ["SUCCESS"]`、`code + ["000000"]` 或 `status + ["0"]`；不接受任意自定义字段和值把失败响应声明为成功。
- 审批 identity 必须且只能配置 `response_business_id_path`、`request_business_id_path`、`request_business_id_query_key` 之一。无论 ID 来自响应、请求 JSON 还是 URL query，其值都必须等于提交步骤已登记的 `projId`。
- 待办打开地址从配置的 `todo_open_url_path` 读取，允许同部署绝对 URL 或相对 URL，拒绝跨 origin 和内嵌凭证。审批页面有二次确认时声明 `approval_confirmation`；没有时不配置。
- `process_preview` 每次审批前后读取当前节点。节点过滤后必须只有一个合规 `assignee_login_name_path` 值；从该 `loginName` 获取隔离会话，再按 `business_id`/`projId` 找唯一待办并处理。没有 active 节点才进入最终状态轮询；达到通过状态却仍有 active 节点必须失败，超过 `max_transitions` 同样失败。
- 审批写响应成功后立即 checkpoint 为 `retained`，再轮询资源池状态 `2`。即使后续详情回读失败，快照也必须保留已经发生不可逆审批的清理结论和审批任务页 scope。

`probe` 与 `standard` 运行同一条创建、提交、审批和最终状态回查主链，`standard` 只额外执行深度回读；`stable` 只能执行 factory 明确声明的只读健康检查，任何 mutation 步骤都会在浏览器启动前被拒绝。不要把 `stable` 当成缩短版审批流程。

流程失败附件先消费执行器缓存的清理前 evidence；仅当没有缓存、会话池当前角色与失败步骤记录的角色一致且当前 page 存在时，才现场捕获一次。角色会话初始化或步骤前置在切换前失败时，不得回退截图上一角色页面。

创建成功后的 registry 仍由 `ModuleSmokeDriver` 维护。流程 cleanup 台账只追加当前 run 中由创建步骤返回、且具备精确 page scope 和业务 ID 的记录；审批后不能安全删除的记录保留，并由具体流程在报告中说明。
