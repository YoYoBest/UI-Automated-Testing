---
name: zentao-bug-submission
description: 将 UI-Smoke-Testing 的 Allure failed/broken 结果转换为禅道 Bug 草稿并提交，包含模块、失败 URL、错误信息、严重程度、优先级、正文截图和附件。适用于预览待提交缺陷、排查 Allure 到禅道的字段映射，以及维护产品、项目、模块、指派人、图片上传或保存校验逻辑。
---

# 禅道缺陷提交

## 事实来源

- `src/ei_ui_smoke/zentao.py`：解析 Allure、生成草稿、上传图片和提交禅道。
- `src/ei_ui_smoke/bug_priority.py`：严重程度与优先级的可执行规则。
- `tests/test_zentao.py`：失败筛选、字段提取、正文图片和附件命名的回归测试。
- `skills/bug-severity-priority/SKILL.md`：判级与人工复核边界。

复用这些实现，不另写一套 Allure JSON 解析或禅道提交脚本。

## 工作流

1. 确认输入是本次执行对应的 `allure-results-*` 目录，不要误用 HTML 报告目录。
2. 先运行 `--dry-run` 生成草稿并检查标题、模块、步骤、失败 URL、截图、严重程度、优先级和 `requires_review`。
3. 只处理状态为 `failed` 或 `broken` 的 `*-result.json`；忽略 passed、skipped 和未知状态。
   页面字段校验提示、HTTP 非 2xx、业务状态失败、未捕获到预期保存响应、保存后页面状态异常和页面功能断言失败都属于待提交范围，不得只收集肉眼可见的页面缺陷。
4. 标题层级读取 `parentSuite`、`suite`、`subSuite` 并去重；所属模块另按 `subSuite`、`suite`、`parentSuite`、`feature` 从最具体层级向上匹配，缺失时使用 `module_id`，最后才归为未分类模块。不要混用标题套件层级和禅道模块值。
5. 只读取约定的“失败页面 URL”和图片附件作为证据。错误信息取 `statusDetails.message`，不得把 Cookie、Token、请求头、storage state 或登录凭证写入正文和附件。
6. 用 `assess_bug()` 生成初始严重程度和优先级。`requires_review=true` 是复核提示，当前自动提交实现不会据此阻断；`severity=1`、`priority=1` 或归属不明的高风险问题仍必须人工确认，不得把自动建议描述为最终定级。
7. 用户明确授权正式提交后，再从环境变量读取禅道配置并执行提交。提交属于外部写操作，不能因用户只要求预览或排查而自动执行。
8. 提交后检查保存响应为成功，优先从响应的 `load` 地址（如 `bug-view-123.html`）解析 Bug ID，再回退到响应页面内容。禅道 21.7.6 的 Bug 列表主体由 iframe 动态加载，普通 HTTP GET 可能只有外壳，不能把“列表 HTML 未找到标题”直接当作未创建。任何映射、上传、保存或取 ID 失败都应停止并报告，不能宣称已提交。
9. Run Test 收尾时始终调用 `process_allure_failures` 并写入 `artifacts/zentao/zentao-drafts-<run>.json`。设置 `ZENTAO_AUTO_SUBMIT=true` 代表已明确授权自动提交；同时要求 URL、用户名和密码配置完整。未开启、缺配置或提交失败时，在完成弹窗中显示草稿数、原因和草稿路径，禁止静默跳过。
10. 自动提交成功后写入 `zentao-submitted-<run>.json`，记录标题和 Bug ID；不要写入账号、密码或会话信息。

## 过滤、分类与去重

- 过滤预期失败：Allure `statusDetails.known/muted`、`expected_failure=true` 或 `reportable=false` 表示预期结果，不生成产品缺陷。负向用例实际符合“保存被阻止/出现校验提示”时必须在 pytest 中通过，不能先失败再依赖上报层猜测。
- 分类后再提交：产品、校验、接口、数据、安全、性能、被测系统保存链路异常和可量化视觉异常设置 `reportable=true`。定位器/selector/Playwright 异常归为 `automation`；浏览器退出、登录态和测试配置问题归为 `environment` 且 `reportable=false`，只进入审计草稿。
- 分类以最终失败证据和运行上下文优先于用例名称或包装文案。页面入口本身连接失败、连接被拒绝或域名无法解析归为 `environment`；页面和会话正常、但某个保存操作出现“网络连接失败”或没有保存接口响应时归为 `api` 产品缺陷，尤其是同一批次相邻保存成功时，不得把特定输入触发的接口异常过滤成环境问题。
- 分类时同时传入失败消息、用例场景和失败 URL。仅页面/会话连接重置归为不可上报的 `environment`；`requestfailed` 指向业务写接口或点击保存后发生重置时归为可上报的 `api`。若该写接口失败发生在 HTML/脚本、XSS 或 CSRF 安全场景，优先归为 `security-permission`、保留请求失败摘要并标记人工复核。
- 分类器必须先排除已确认的自动化契约错误，再匹配“接口/响应”等泛化产品关键词。包含 `rapid_click_blocked_by_ui` 且证据表明第二次点击被 UI 阻止、仅一次业务响应的失败归为 `automation` 且不提交；“新增保存未完成：表单未关闭”或“弹窗未关闭”归为 `operation-result` 且可提交。
- 为每条记录保留 `failure_category`、`reportable`、`expected_failure`、`dedup_fingerprint`、`detection_source` 和 `occurrence_count`。
- 用“系统 + 模块/操作 + 用例规则 + 规范化错误”计算稳定 SHA-256 指纹。规范化时移除动态 Element ID、UUID、时间戳、自动化名称、业务长主键、内存地址和堆栈行号。指纹未命中时必须再按完整标题做第二层去重；同标题历史 Bug 仍存在则把新指纹关联到原 ID，不得重复新建或编辑原 Bug。
- 同一轮相同指纹只保留一条草稿并累计出现次数、合并截图。跨轮使用 `artifacts/zentao/zentao-dedup-index.json` 累计首次/最近出现时间和次数；已有 Bug ID 的指纹必须先验证禅道详情仍存在，再决定是否跳过。
- 自动提交必须取得非空 Bug ID 才写入去重索引。保存响应成功但无法取得 Bug ID 仍按上报失败处理，避免误认为已去重。
- 保存成功但 Bug ID 为空时禁止直接再次提交同一草稿。先在禅道仪表盘、指派给我的 Bug 或详情页按完整标题核查；确认已创建后补登记真实 Bug ID，确认未创建后才能重试。这样避免因动态列表解析失败制造重复缺陷。
- 禅道保存成功后若 `result.load` 和响应 HTML 都没有 Bug ID，复用同一已登录浏览器打开当前产品 Bug 列表，遍历页面及 iframe，按完整标题读取 `bug-view-<id>` 链接作为动态回退。动态列表仍未找到时才报告“未取得 Bug ID”，不得再次 POST 创建请求。
- 本地索引中的 `bug_id` 只是缓存。自动提交前必须登录禅道验证详情：Bug 仍存在则跳过；明确已删除或不存在则把旧 ID 记入 `deleted_bug_id`、清空当前绑定并重新提交；查询失败或页面结果不明确时停止提交，禁止为绕过去重直接清空 ID。若要覆盖人工创建或其他系统创建的 Bug，提交前还需查询禅道活动缺陷。
- 使用完整分类集合：`page-function`、`field-validation`、`api`、`data-closure`、`operation-result`、`state-transition`、`idempotency`、`concurrency`、`transaction-consistency`、`calculation`、`query`、`boundary`、`security-permission`、`file`、`interaction-feedback`、`recovery`、`compatibility`、`usability`、`audit-trail`、`external-dependency`、`performance-capacity`、`automation`、`environment`、`unknown`。
- `unknown` 必须设置 `reportable=false` 和 `requires_review=true`，写入待复核产物；不得自动提交或默认当作产品缺陷。
- 分别写入四类运行产物：`zentao-drafts-*`（可提交产品缺陷）、`zentao-review-*`（未知归属）、`automation-environment-*`（框架/环境）、`zentao-filtered-*`（预期失败）。
- 提交前查询当前产品禅道缺陷列表；同标题活动缺陷复用原 Bug ID。检测到已关闭/已解决缺陷再次出现时停止自动新建，报告“回归复现，需人工重新激活”。
- 人工修改已提交 Bug 的标题或模块后，同步更新 `zentao-dedup-index.json` 和对应 `zentao-submitted-*.json` 的标题记录；保留原指纹和 Bug ID，不为展示字段变化生成新指纹。

## 结构化证据

- 每条草稿必须包含独立 `evidence`：`page_message`、`failure_url`、`api_response`、`submitted_data`、`readback_result`、`screenshots`、`allure_result`。
- 优先读取同名 Allure 附件；缺少专用附件时，接口类错误把脱敏错误事实回退到 `api_response`，数据闭环错误回退到 `readback_result`。
- 禅道正文加入失败分类、稳定指纹、累计复现次数和已有结构化接口/提交/回读证据；仍不得记录 Cookie、Token、密码或完整敏感请求体。
- 每轮写入 `detection-coverage.json`，区分“证据驱动可识别”“必须设计显式场景”和“仅人工识别”，禁止把分类器支持等同于测试覆盖。

## 自动化识别边界

- 自动识别可量化视觉问题：元素遮挡、内容溢出/截断、不可点击、元素消失和有明确几何阈值的错位。
- 不承诺自动识别颜色是否协调、间距是否美观、交互是否顺手等纯样式或主观体验问题；这些由人工测试发现并录入，`detection_source=manual`。
- 不把“自动化无法识别”描述成产品通过。报告中明确区分自动覆盖范围和人工测试范围。
- 状态流转、幂等、并发、事务、计算、查询、边界、恢复、兼容、审计和外部依赖必须存在对应显式场景与断言后才能自动发现；上报层只负责接收证据、分类和去重，不凭错误关键词伪造检测结果。

## 草稿与证据

标题使用 `【第一层测试套件】-【第二层测试套件】-【子测试套件】测试用例失败，错误消息`，依次读取 Allure 的 `parentSuite`、`suite`、`subSuite`；缺失层级直接跳过，不输出空括号，相邻或跨层同名套件只保留一次，三层均缺失时才回退到识别出的业务模块。取错误消息的第一个非空行，清洗为单行并限制在 100 字符内，用中文逗号追加到“失败”之后；若以 `AssertionError:` 开头则去掉该类型前缀。冒号后为用例编号、字段代码、`options=`、`validation=` 等技术上下文时，标题在冒号前截断，只保留业务错误描述；完整错误和技术参数仍写入实际结果。正文保持三段：重现步骤、实际结果、预期结果；存在失败 URL 时把脱敏后的 URL 放入重现步骤。

失败截图既上传为正文内联图片，也以 `allure报错页面.png` 作为附件；多个截图使用递增后缀。不要依赖 Allure 随机 source 文件名作为禅道展示名。若命令行传入 `--screenshot`，它会替换草稿从 Allure 取得的截图集合。

Allure 自身仍遵循 `ui-smoke-test` 的附件边界，只保留“失败页面 URL”和“失败页面截图”。不要为了禅道提交向 Allure 重新加入控制台、网络或页面错误附件。

## 禅道字段映射

- 按配置的产品名称查询真实产品 ID；找不到产品时失败，不猜测 ID。
- 提 Bug 表单必须提供 UID，缺失时失败。
- 所属项目必须恰好有一个候选；不是唯一选项时停止并要求确认，禁止任取第一项。
- 所属模块按 Allure 的 `subSuite`、`suite`、`parentSuite`、`feature` 从最底层向上匹配禅道模块树，必须匹配路径末级名称；`action_case.module_name` 中的结构化路径用于补齐嵌套用例缺失的祖先层级。同名末级候选先按候选祖先的精确命中数消歧，再比较路径深度，禁止在相同深度时任取首项。不得用模糊包含关系随意选取无关子模块。无法匹配时使用 `ZENTAO_MODULE_FALLBACK`，默认回退到“功能开发”，仍无法匹配才使用根模块。
- 指派人必须按 `ZENTAO_ASSIGNEE` 匹配真实候选；不存在时失败。
- 已存在 Bug 是责任人工作边界。当前指派人不是宋佳慧时，自动化只在本地累计复现记录，不得编辑禅道中的标题、模块、重现步骤、附件、严重程度、优先级、状态或其他字段，也不得把 Bug 自动重新指派给宋佳慧。即使当前仍指派给宋佳慧，更新已有 Bug 也必须由独立、显式授权的更新流程执行；默认提交流程只负责新建和去重。
- 正文内容必须 HTML 转义后再生成段落；图片 URL 也必须转义，避免错误信息破坏表单 HTML。

不要把禅道内部产品、项目、模块或人员 ID 固化进 Skill 或代码；每次从当前禅道页面数据解析。

## 命令

先预览，不写入禅道：

```powershell
.\.venv\Scripts\python.exe -m ei_ui_smoke.zentao <allure-results-dir> --dry-run
```

正式提交前在当前进程设置环境变量，不写入仓库：

```powershell
$env:ZENTAO_URL = 'https://zentao.example/'
$env:ZENTAO_USERNAME = '<username>'
$env:ZENTAO_PASSWORD = '<password>'
$env:ZENTAO_PRODUCT = '<product>'
$env:ZENTAO_ASSIGNEE = '<assignee>'
.\.venv\Scripts\python.exe -m ei_ui_smoke.zentao <allure-results-dir>
```

需要观察浏览器交互时临时设置 `ZENTAO_HEADLESS=false`。不得记录或回显密码。

## Run Test 配置

- 自动提交要求 Windows 用户环境中存在 `ZENTAO_AUTO_SUBMIT=true`、`ZENTAO_URL`、`ZENTAO_USERNAME`、`ZENTAO_PASSWORD`；产品和指派人可用 `ZENTAO_PRODUCT`、`ZENTAO_ASSIGNEE` 覆盖默认值。
- 图形启动器可用“失败后录入禅道”复选框授权当前运行：初始化默认不勾选，不读取 `ZENTAO_AUTO_SUBMIT` 自动授权；执行时只把用户本轮勾选状态写入供上报器使用的环境副本。勾选后先校验 URL、用户名和密码；全部通过时不提交，只有经过分类过滤后仍可上报的 `failed`/`broken` 才提交。取消勾选时继续生成草稿，但禁止外部写入禅道。命令行或上报器直接调用仍以显式传入环境中的 `ZENTAO_AUTO_SUBMIT=true` 作为自动提交授权。
- `process_allure_failures(environment=...)` 必须区分 `None` 与显式空映射：只有 `None` 读取当前进程环境；传入 `{}` 表示无配置并禁止自动提交，不能用真值回退重新启用父进程中的禅道变量。
- `run_test.vbs` 必须从 `WScript.Shell.Environment("USER")` 显式复制这些值到 `Environment("PROCESS")` 后再启动 Python。不要只依赖 Explorer 继承环境；`setx` 后旧 Explorer 进程可能仍持有旧环境块。
- 配置写入用户环境后必须关闭旧 Run Test 窗口并重新启动。验证时只报告变量是否存在，不输出账号、密码或实际值。
- 不把凭据写入仓库、Skill、日志、草稿、Allure 附件、提交回执或 `auth-state.json`。

## 验证

修改 Allure 映射、正文、附件或提交字段后至少运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_zentao.py tests\test_bug_priority.py -q
```

正式提交逻辑的验证顺序是：dry-run 草稿正确、图片上传响应成功、Bug 保存响应成功、从 `result.load` 或响应内容取得非空 Bug ID、禅道详情页核对标题/所属模块/正文/附件、写入提交回执。单元测试通过不等于真实禅道提交已经成功；线上核查失败时先查现有 Bug，禁止盲目重提。
