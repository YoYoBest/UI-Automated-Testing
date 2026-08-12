# EI UI Smoke Testing

独立的 Python + Playwright 测试工程，参照 `ei-parent` 的动态表单、动态列表和运行时数据契约实现。源项目只作为只读配置和契约来源，测试方法全部位于本工程。

## 测试闭环

```text
ei-parent 本地 JSON + 运行时配置接口
                  ↓ 字段定义（fieldCode/fixedType/fieldType/required）
全局公共池 + 自动采集缓存 + 少量中央覆盖
                  ↓ probe/stable 数据策略
页面 DOM          ↓ 定位、输入、选择真实接口返回的下拉候选项
保存接口          ↓ 状态、响应、业务主键
运行时详情接口    ↓ fixedType=0/2 字段核对
详情页面 DOM      ↓ 固定字段和可见字段回显核对
```

字段定义以配置接口/JSON为准，DOM仅用于确认实际渲染并执行交互。业务数据不按页面拆文件，统一维护在 `data/` 中。

## 安装

```powershell
cd D:\Auto_Testing\UI-Smoke-Testing
python -m pip install -e .
python -m playwright install chromium
```

## 本地契约测试

```powershell
python -m pytest -q -m "not smoke"
```

## 中央数据文件

- [common_data.json](data/common_data.json)：手机号、信用代码、金额、企业名称等生成规则及公共候选池。
- [collected_data.json](data/collected_data.json)：详情接口自动采集结果，不手工维护。
- [overrides.json](data/overrides.json)：所有表单少量特殊覆盖，按 `formCode` 集中存放。
- [pages.json](data/pages.json)：所有表单的 URL、保存按钮、保存接口和详情 URL。
- [collection_sources.json](data/collection_sources.json)：列表/详情采集接口和允许采集的字段白名单。

公共数据池存生成规则而非单个固定值。例如手机号按合法号段生成，信用代码带校验位，金额限制在合规区间。同一个 `run_id` 可复现相同数据。

## 三种模式（默认：probe）

快速探测模式不要求业务数据：

```powershell
pytest tests/test_form_smoke.py --browser-smoke --data-mode probe -v
```

稳定冒烟模式按以下优先级取值：

```text
overrides.json 特殊覆盖
→ collected_data.json 已采集合法值
→ common_data.json 候选池/受约束生成器
→ 表单配置默认值
→ 按字段类型兜底
```

```powershell
pytest tests/test_form_smoke.py --browser-smoke --data-mode stable -v
```

标准自动化模式使用新生成的合法数据执行完整保存与回显，并要求当前页面所有可编辑字段都完成填写；可选字段无候选或交互失败也会阻断测试：

```powershell
pytest tests/test_form_smoke.py --browser-smoke --data-mode standard -v
```

## 已部署页面冒烟

### VBS 图形启动

双击项目根目录的 [run_test.vbs](run_test.vbs)，依次选择：

1. `formCode`。
2. 页面 URL；留空则读取 `data/pages.json`。
3. `ei-parent` 源码路径。
4. 数据模式：`1=probe`，`2=stable`。
5. 浏览器模式：可见或无头。
6. 登录态文件；留空则输入用户名和密码。

VBS把配置作为子进程环境变量传给 pytest，不会把用户名和密码写入JSON文件。

### PowerShell 启动

先设置环境变量：

```powershell
$env:EI_PARENT_ROOT = 'D:\Auto_Testing\Project_Purvar\SHZY\ei-parent'
$env:EI_FORM_CODE = 'FUND_BASICINFO'
$env:EI_STORAGE_STATE = 'auth-state.json'
$env:EI_DATA_MODE = 'stable'
pytest tests/test_form_smoke.py --browser-smoke -v
```

在 `pages.json` 的 `forms.FUND_BASICINFO` 中配置页面机械信息。业务字段值无需另建页面文件。

## 自动采集

先在 `collection_sources.json` 为表单配置列表、详情接口和字段白名单，然后在系统空闲时运行：

```powershell
$env:EI_BASE_URL = 'https://host/ei-view/#/'
$env:EI_STORAGE_STATE = 'auth-state.json'
python -m ei_ui_smoke.collector --form-code FUND_BASICINFO --base-url $env:EI_BASE_URL --limit 20
```

采集器只保存 `allowedFields` 白名单字段，自动去重，记录采集时间和来源业务主键，并通过临时文件原子更新 `collected_data.json`。CI运行测试时只读该缓存。

完整覆盖关系见 [EI_PARENT_COVERAGE.md](docs/EI_PARENT_COVERAGE.md)。

## 企查查本地验证服务

该服务只用于接口结构验证、前端联调和自动化测试，不替代正式业务后端。默认使用 Mock 数据，结果仅缓存在进程内存中，不写业务数据库或本地数据文件。

```powershell
$env:QCC_MODE = 'mock'
python -m ei_ui_smoke.qcc_proxy --port 8765
```

接口：

```text
GET http://127.0.0.1:8765/api/qcc/companies?keyword=北京汽车
GET http://127.0.0.1:8765/health
```

真实接口验证需要在当前终端设置凭证，禁止写入或提交 `.env`：

```powershell
$env:QCC_MODE = 'real'
$env:QCC_API_KEY = '<api-key>'
$env:QCC_SECRET_KEY = '<secret-key>'
python -m ei_ui_smoke.qcc_proxy --port 8765
```

下拉接口只返回 `keyNo`、`name`、`creditCode`、`status`、`selectable`。重复企业会被合并，注销、吊销等企业返回但标记为不可选择。缓存 TTL、超时和最大结果数可通过 `.env.example` 中的环境变量调整。

### 从图形启动器验证管理平台

所有浏览器冒烟模式都会全局拦截 `QccSelect` 发出的 `dataManager/entSearch` 请求。快速探测和稳定冒烟遇到“公司全称/企业全称/企业名称”远程下拉时，会先输入 `EI_QCC_KEYWORD`（默认“北京汽车”）再选择候选项。需要手工验证时，选择“企查查验证”后点击统一的“开始执行”按钮；浏览器会保持打开且不会自动保存。

自动识别新增会读取 `common_data.json` 的 `uploads.defaultFile`，在其他字段填写完成后，将该文件上传到当前新增对话框内所有可用的附件控件。表单需要不同附件时，仍可通过 `overrides.json` 的 `uploadFiles` 单独配置。

### 字段定位自检与 AI 修复闭环

模块新增测试会在保存前重新扫描页面实际可见控件并读取真实 DOM 值。默认自动重试两轮，用于处理联动后才出现或延迟渲染的字段；仍失败时不会点击保存，并写入：

```text
artifacts/field-diagnostics/<module-id>.json
artifacts/field-diagnostics/latest.json
```

诊断文件包含 `notLocated`（源码存在但页面扫描未定位）、`notFilled`（页面控件实际为空）、`fillFailed`（Playwright 操作异常）、预期字段、实际字段、selector、控件类型和页面 URL。可通过 `EI_FIELD_FILL_ATTEMPTS` 调整运行时重试次数。

页面或保存接口返回可明确映射到字段的格式提示时，会自动按约束重新生成并填写邮箱、手机号、长度、数值范围或唯一名称，修复过程记录在诊断文件的 `validationRepairs`。`EI_VALIDATION_REPAIR_ATTEMPTS` 控制单次字段修复轮数，`EI_VALIDATION_SAVE_ATTEMPTS` 控制修复后的保存轮数，默认均为 3。权限、流程、网络和无法明确映射字段的错误不会自动重试。

让 AI 修复时使用固定闭环：运行目标模块测试 -> 读取 `latest.json` 和失败截图 -> 修改 DOM 扫描或对应组件定位策略 -> 重跑同一模块 -> 再读诊断。只有测试通过且诊断 `status=passed` 时停止；达到约定轮数仍失败时，保留最后一份诊断并报告阻塞原因，不要求人工逐个指出字段。每次解决后还要判断根因是否可跨模块复用：公共框架、公共组件、重复工作流或稳定契约问题写入对应 Skill 并补测试；临时数据、环境故障和单页一次性 selector 不写入 Skill。

图形启动器默认使用 `QCC_BROWSER_MODE=backend`：将企业搜索转到已部署系统的 `/BPI/FUND/QCCSearchData`，由现有后端调用企查查，因此自动化项目不需要保存企查查密钥。设置 `QCC_BROWSER_MODE=mock` 可强制使用离线数据；只有绕过现有后端直接验证企查查时才使用 `QCC_BROWSER_MODE=real` 并提供 `QCC_API_KEY`、`QCC_SECRET_KEY`。独立 8765 服务的 `QCC_MODE` 不影响图形启动器。

管理平台新增表单只有在“注册状态=已注册、境内境外=境内”时才渲染企查查下拉；其他组合按业务源码设计显示普通文本输入框，不会发出企业搜索请求。

## Excel 通用字段参数化

通用字段测试采用两阶段执行。第一阶段打开目标模块新增表单，发现真实字段类型和约束并生成字段清单：

图形启动器中，这两阶段只在选择“标准自动化”时执行。先在“通用用例 Excel（仅标准自动化）”中选择规则表，再从联动的“用例页签”下拉框选择规则页；默认优先选择“新增”，不存在时选择工作簿首个页签。“快速探测”和“稳定冒烟”不会收集或执行通用字段用例。标准模式未选择有效 Excel 或有效页签时会在启动浏览器前提示并停止。

“模块用例 Excel”和“模块用例页签”用于模块自己的个性化场景。当前建设项目新增用例默认读取 `tests/Common_Test_Cases/建设项目_个性化用例.xlsx` 的“新增项目”页签；选择建设项目的新增类目标并使用标准自动化时，会在普通页面操作和通用字段检查之外追加该模块用例。模块用例留空时不追加，已配置但文件或页签无效时会在启动浏览器前提示。

模块用例也可独立从命令行执行：

```powershell
pytest tests/test_build_project_add_personalized.py --browser-smoke --data-mode standard `
  --module-cases-excel 'tests/Common_Test_Cases/建设项目_个性化用例.xlsx' `
  --module-cases-sheet '新增项目' -v
```

```powershell
$env:EI_FORM_URL = 'https://host/ei-view/#/module/list'
$env:EI_STORAGE_STATE = 'auth-state.json'
$env:EI_MODULE_ID = 'project_setup'
pytest tests/test_common_field_discovery.py --browser-smoke --data-mode standard --discover-common-fields `
  --common-fields-manifest artifacts/common-fields/project_setup.json -v
```

第二阶段在 pytest 收集期组合 Excel“新增”页规则与字段清单，再逐条执行参数化校验：

```powershell
pytest tests/test_common_field_validation.py --browser-smoke --data-mode standard `
  --common-cases-excel '新建文件夹/公共用例_宋佳慧_新增页签优化版.xlsx' `
  --common-fields-manifest artifacts/common-fields/project_setup.json -v
```

同一批用例共享一个浏览器会话，但每条用例重新打开新增表单。执行器使用 `standard` 数据策略多轮补齐合法基准值，确认其他必填字段完整后只覆盖当前目标字段，并点击一次保存。合法值必须捕获成功的保存接口和业务状态；异常值必须证明保存被校验阻止，或按规则被控件截断后以有效值成功保存。名称类字段会保留用例特征并合入本轮唯一标识，避免重跑时被历史数据的唯一性约束干扰。

参数化校验在启动器中的默认超时为 3600 秒，可用 `EI_COMMON_VALIDATION_TIMEOUT_SECONDS` 调整。

Run Test 会把本轮 Allure 中所有 `failed`/`broken` 结果写入 `artifacts/zentao/zentao-drafts-<run>.json`，包括页面校验提示、HTTP/业务接口失败、保存响应缺失和保存后状态异常。图形启动器勾选“失败后录入禅道”后，仅在本轮存在可上报失败时提交；未勾选时只生成本地草稿。使用前配置 `ZENTAO_URL`、`ZENTAO_USERNAME`、`ZENTAO_PASSWORD`；`ZENTAO_AUTO_SUBMIT=true` 控制复选框默认状态。提交结果和 Bug ID 写入同目录的 `zentao-submitted-<run>.json`。未配置或提交失败会在完成弹窗中明确提示，不会静默跳过。

上报前会过滤 Allure 已标记的预期失败，并把定位器、浏览器、登录态和环境配置异常归为非产品问题。相同错误按稳定指纹在同轮合并，并通过 `artifacts/zentao/zentao-dedup-index.json` 跨轮累计；已有 Bug ID 的错误不会重复提交。自动化只覆盖遮挡、溢出、截断、不可点击等可量化视觉异常，颜色、美观度和主观体验仍依赖人工测试。

每轮还会分别生成 `zentao-review-*`、`automation-environment-*`、`zentao-filtered-*` 和 `detection-coverage.json`。草稿包含结构化页面提示、失败 URL、接口响应、提交数据、回读结果、截图和 Allure 来源。状态流转、幂等、并发、事务、计算、查询、边界、恢复、兼容、审计和外部依赖等类别必须有显式测试场景才能被自动发现；分类支持不代表已经凭空覆盖业务场景。提交前会查询禅道活动缺陷，同标题活动 Bug 复用 ID，关闭缺陷再次出现则转人工处理回归复现。
