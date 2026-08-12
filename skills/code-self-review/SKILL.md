---
name: code-self-review
description: 提交代码前的自检 code review。基于 `coding_rules.md`（附录 B 检查清单），对未提交改动 / 指定文件 / 指定 PR 的 SQL、Java、Vue 文件逐项审查，输出按严重度（❌ 违规 / ⚠️ 警告 / ✅ 通过）分组的结构化报告并指明对应规范章节。当用户说「自检」「提交前 review」「code review」「检查规范」「按规范看看」「commit 之前看下」「PR 自查」「合规检查」「按 ezgo3 规范走一遍」「上 PR 前查一下」「pre-commit review」「self-review my changes」「check against spec」「lint by spec」时必须触发；即使没说 "skill"，只要意图是「拿规范对照我写的代码」「我自己先 review 一下」「这次改动有没有违反规范」也应主动触发。
---

# ezgo3 代码自检 Skill（Claude & Codex 通用）

> **SSOT：** 所有规范条款以仓库根目录的 `coding_rules.md` 为准。本 skill 的源文件位于仓库 `skills/code-self-review/SKILL.md`；若通过 `~/.claude/skills` 或 `~/.codex/skills` 的符号链接加载，先按链接真实目标回到本仓库，再读取该规范文件。本 skill 不复制规则，只给出**审查流程**、**检测方法**和**输出格式**。当规范文件与本 skill 冲突时以规范文件为准。

## 一、适用场景

- 写完一个功能 / 一段改动后，**提交前**自检
- 准备发 MR / PR 前，**先自己过一遍清单**
- 别人提的 review 意见，想**确认是否还有同类问题**
- review 历史代码块，按当前规范打分

不适用：
- 业务逻辑正确性审查（这不是规范类 review）
- 性能调优、安全漏洞挖掘（参考独立 skill）

## 二、跨 agent 使用方式

| Agent | 加载方式 |
|-------|---------|
| Claude Code | 通过 `Skill` 工具调用 `code-self-review`；本地通过 `~/.claude/skills/code-self-review` 符号链接加载同一份源文件 |
| Codex | 重启后从 `~/.codex/skills/code-self-review` 自动发现；若当前会话尚未刷新 skill 列表，则直接读取本文件并按流程执行 |
| Cursor / Gemini CLI / 其他 | 直接读取本文件并把"流程"段当本轮系统指令执行，无需 wrapper |
| 纯人工 | 把本文件当 checklist 读，照流程跑 |

> 本 skill 的所有动作只用**通用工具**（Read、`git`、`git grep`），不调用任何 agent 专属工具或额外二进制，因此跨 agent、跨 OS 等价。

**Shell 环境策略（cmd-first，重要）：**

公司大多数同事在 **Windows cmd / PowerShell** 中跑 Claude Code / Codex，**不带 Git Bash、WSL、不装 grep**。本 skill 因此以 **`git grep`** 为命令基线 —— 它内嵌在 git 自身，cmd 下直接 `git grep ...` 即可调用，不需要外部 shell。

| 环境 | 推荐工具 | 备注 |
|------|----------|------|
| **Windows cmd / PowerShell**（主战场） | `git grep -nE` | git for Windows 自带 PCRE2，支持 `\b` 等高级模式 |
| macOS / Linux / WSL / Git Bash | `git grep -nE` 或 `grep -rnE` | 二者等价，前者跨平台一致 |

**`grep -rnE` → `git grep -nE` 转写速记（agent 必看）：**

```
原写法                                  cmd 友好写法
─────────────────────────────────────  ─────────────────────────────────────
grep -rnE 'PATTERN' path/              git grep -nE 'PATTERN' -- 'path/*'
grep -rniE 'PATTERN' path/             git grep -niE 'PATTERN' -- 'path/*'
grep -rnE 'PATTERN' path/file.java     git grep -nE 'PATTERN' -- 'path/file.java'
grep -nE 'PATTERN' file                git grep -nE 'PATTERN' -- 'file'
```

规则要点：
- 用 `--` 分隔 pattern 与 pathspec
- 路径用 git pathspec 通配（`'src/**/*.java'`），不是 shell 通配
- 单引号包正则，避免 cmd 解析 `<>|&` 等
- `git grep` 默认只搜已跟踪文件 —— 未跟踪改动加 `--untracked`；扫整个 worktree（含被 .gitignore 排除的）加 `--no-index`

**做不了的能力（标 `[posix-shell]`，cmd 下跳过）：**

少数检测依赖 `awk` 统计行块、`while read` 循环处理文件（如 SQL 文件按 CREATE TABLE 分块、Java 嵌套深度统计）。这些项在 §六 表中显式标 `[posix-shell]`：
- cmd / PowerShell：**跳过该项**，在报告"待人工确认"列说明
- bash / WSL / Git Bash：正常执行

**禁止做的事：**
- 不要把 git grep 命令翻译成 PowerShell 的 `Select-String` — 正则方言不同（PCRE vs .NET regex），翻译易埋边界 bug
- 不要要求用户装额外工具（`rg`、`ag`、`grep` 单独包等）— 增加门槛违背"通用"原则

## 三、审查范围决策

按以下优先级确认要 review 的代码范围。**开工前先在一句话里告诉用户："本次审查范围是 X"**，避免后续误解。

1. **用户显式指定文件/目录** → 用 `git diff` 取这些路径的改动
2. **用户指定了 PR/MR 号或分支** → `git diff <base>...<head>`
3. **用户说"我刚改的"/"提交前"/默认** → `git status --porcelain` + `git diff HEAD`（含已暂存+未暂存），不含未跟踪文件除非用户明确要求
4. **想看完整文件而非 diff**（如新文件） → 取 `git diff` 中标 `A` 的文件全文

获取改动范围的标准命令：

```bash
git status --porcelain                  # 列出所有变化
git diff --name-only HEAD               # 当前所有改动文件名
git diff HEAD -- <file>                 # 单文件 diff
git diff <base>...<head>                # PR/分支差异
```

## 四、执行流程

每次 review 严格按以下 4 步走，不要跳步。每一步开始前在对话中报一句话进度。

### Step 1 — 抓改动清单

执行上一节的命令，得到所有变化的文件路径。然后按扩展名/路径**分类**：

| 类别 | 匹配规则 | 走哪份清单 |
|------|----------|------------|
| **SQL/DDL** | `*.sql`，`docs/**/sql/**`，迁移脚本 | §6.1 SQL 清单 |
| **Java - Controller** | `*/controller/*.java`，类含 `@RestController` | §6.2 Controller 清单 |
| **Java - Service/Mapper** | `*/service/**/*.java`、`*/mapper/**/*.java` | §6.3 Service/Mapper 清单 |
| **Java - Model/Request/VO** | `*/facade/model/**`、`*/facade/request/**`、`*/facade/vo/**` | §6.4 Model/Request/VO 清单 |
| **Vue 前端** | `*.vue`、`ei-view/src/**/*.ts`、`*.scss` | §6.5 前端清单 |
| **配置/POM** | `pom.xml`、`application*.yml`、`.env*` | §6.6 配置清单 |
| **Git 元数据** | 提交信息（`git log`） | §6.7 Commit 清单 |

> 一个文件可能同时匹配多类（如 Mapper.java 同时含 SQL 注解），按所有命中的清单都跑一遍。

### Step 2 — 读规范关键章节

打开 `coding_rules.md`，**只读与本次改动有关**的章节，不要全文加载，节省上下文：

| 改动类别 | 必读章节 | 可选参考（regex pattern 速查）|
|---------|---------|---------|
| SQL/DDL | 三、建表规范；附录 B 建表清单 | 附录 C.1 |
| Java | 五、依赖与分层；六、Java 编码规范；附录 B 后端清单 | 附录 C.2 / C.3 / C.4 |
| Vue | 九、前端规范；附录 B 前端清单 | 附录 C.5 |
| 配置 | 五、依赖与分层（POM 审批）；附录 A 硬编码 ID 红线 | 附录 C.6 / C.7 |
| Commit | 十、Git 提交规范 | — |

> 附录 C 是 AI MR review bot 用的「识别速查表」，self-review 跑 grep 时可拿它当 pattern 灵感。本 skill 的 §6.x grep 命令清单是 cmd/PowerShell 友好的 self-review 版本，附录 C 是给 LLM 看的 regex 风格，**两者覆盖范围有重叠但各有不在另一边的扩展**——例如附录 C 含 Phase 1 新加的 `R-DI-01` `R-UTIL-01` `R-MYBATIS-01` `R-FRONT-01` `R-ARCH-01` 等 pattern，本 skill §6.x 暂未同步。

**早退出条件：** 若 Step 1 分类后所有改动文件都不在 §6.0 映射表中（如纯 `.md` 文档、`.gitignore`、CI 脚本等），跳过 Step 3，直接按 §五 模板出一份只含 `⏭ [SKIP]` 段的简短报告并退出，结论为"可提交（无适用规约）"。**不要**为了凑内容自创规则去审查。

### Step 3 — 逐项核对（关键步骤）

对每个文件，按下面 §6 的清单逐条执行 grep 命令；标 `[human-only]` 的项跳过并在报告"待人工确认"列记一条。判定时遵守**三档严重度**：

| 标记 | 含义 | 例 |
|------|------|----|
| ❌ **违规** | 明确违反规范红线 / "禁止"条款 | `@Autowired` 出现在新代码；FastJSON 引入；硬编码 ID |
| ⚠️ **警告** | 不算红线但有更优做法 / 风险点 | 嵌套接近 3 层；未加 `@Valid`；Hutool 调用 |
| ✅ **通过** | 检查项已满足，仅在总览统计 | — |

**判定原则：**
- 拿不准时**降一档**（违规 → 警告），并在 evidence 里写"待人工确认"
- 同条规则同一文件多处违反，**合并为一条**，evidence 列出所有行号
- 历史代码（diff 之外）即便违规也**不报**，除非用户明确说"全文 review"

### Step 4 — 输出报告

按 §5 模板输出。报告完成后**询问用户**："要我按这份报告逐项修复吗？还是你自己改？"，不要擅自动手。

## 五、输出报告模板（强制）

**输出规则（违反即视为 skill 执行失败）：**
- 整份报告必须作为**一个完整的 Markdown 代码块**输出，前后不夹杂对话文字；之后再单独问"是否要我修复？"
- 每条违规/警告必须含 plain-text 标签 `[VIOLATION]`/`[WARNING]`，与 emoji 并列，便于 `grep -c '\[VIOLATION\]'` 解析
- "规范"字段必须指明总纲章节号（如"§六 Controller 规范"），不复制条款原文

模板：

````markdown
# 代码自检报告

**范围：** <一句话描述，如"develop 分支未暂存改动，共 12 个文件">
**规范版本：** coding_rules.md（2026-05-21，含附录 C）
**执行 agent：** <Claude Code / Codex / ...>

## 总览

| 严重度 | 数量 |
|--------|------|
| ❌ [VIOLATION] 违规 | N |
| ⚠️ [WARNING] 警告 | M |
| ✅ [PASS] 通过项 | K（详见末尾） |
| ⏭ [SKIP] 跳过 | S（如纯文档/CI 脚本等无适用规约的文件） |

**结论：** <可提交 / 需修复 N 项后方可提交 / 可提交（无适用规约）>

## ❌ [VIOLATION] 违规（必改）

### V1. <一句话标题，如「DI 使用 @Autowired，应改为 @Resource」>
- **文件：** `ei-service/src/main/java/.../XxxController.java:42`
- **规范：** 总纲 §六 基础 — DI 统一使用 `@Resource`
- **证据：**
  ```java
  @Autowired
  private XxxService xxxService;
  ```
- **修复建议：** 改为 `@Resource`，删除 `@Autowired` import。

### V2. ...

## ⚠️ [WARNING] 警告（建议改）

### W1. <标题>
- **文件：** `xxx.vue:120`
- **规范：** 总纲 §九 — `v-for` 应使用唯一 ID 而非 index 作 `:key`
- **证据：** `v-for="(item, idx) in list" :key="idx"`
- **修复建议：** 改用 `:key="item.id"`。

## ✅ [PASS] 通过项摘要

仅列**检查过且通过**的清单条目，每行一句（不需要 evidence）：

- 所有新建表含完整 10 个基础字段
- 所有 Controller 含 `@Scope("prototype")`
- 无 FastJSON 引用
- 无 `console.log` 残留
- 无硬编码 appId / 菜单 ID / 角色 ID / 岗位 ID / x-tenant-id
- ...

## 下一步

- 若结论为"可提交"：建议按 §十 Git 提交规范撰写 commit message
- 若结论为"需修复"：是否要我（agent）按上述违规项逐条修复？请确认。
````

## 六、按类别的检测命令清单

> 规则原文在总纲对应章节，本节只给**可机器执行的检测命令**与命中后的判定。命中即按严重度记一条，不复述规则。
> 标记说明：
> - `[human-only]`：无可靠机器检测方法 → agent 跳过并在报告"待人工确认"列记一条
> - `[posix-shell]`：依赖 awk / while read 等 POSIX 工具 → Windows cmd / PowerShell 跳过并标"待人工确认"；bash / WSL / Git Bash 正常执行
>
> **Windows cmd 用户**：本节用 `grep -rnE` 写法是为简洁，cmd 下统一替换为 `git grep -nE ... -- 'path/*'`（转写规则见 §二）。例如：
> ```
> grep -rnE 'PATTERN' ei-service/    →    git grep -nE 'PATTERN' -- 'ei-service/*'
> ```

### 6.1 SQL / DDL（总纲 §三 + 附录 B）

**先判 SQL 类型再选检测项**（避免对 ALTER / INSERT 误报）：

```bash
# 文件含 CREATE TABLE → 跑全套
git grep -nE '^\s*CREATE\s+TABLE' -- '文件.sql'   # 命中数 > 0 → 跑基础字段；否则跳过
```

逐字段循环判定基础字段（POSIX 模式）：`for col in id tenant_id ...; do ...; done` `[posix-shell]`
cmd 替代：用 git grep 逐列写 10 条命令，或让 agent 在内存里循环（不依赖 shell）。

| 检测 | 命令 / 模式 | 仅 CREATE TABLE | 命中判定 |
|------|-------------|----------------|---------|
| 大写字母（表/字段名） | `grep -nE '^\s*[`"]?[A-Z][a-zA-Z0-9_]*[`"]?\s+(bigint\|varchar\|int\|datetime)' 文件` | 否，所有 SQL | ❌ |
| 10 个基础字段顺序 | **仅对 `CREATE TABLE ... );` 块内**按序匹配 `id` → `tenant_id` → `create_organ` → `create_by` → `create_dt` → `update_by` → `update_dt` → `is_delete` → `row_version` → `sort_order`；ALTER / INSERT 文件**不跑此项** | **是** | 缺失或乱序 → ❌（详见 §三） |
| 表注释缺失 | `grep -nE 'CREATE TABLE.*\) ENGINE' 文件` 同行末是否含 `COMMENT='` | 是 | 缺 → ❌ |
| 字段注释缺失 | 字段行末未匹配 `COMMENT '`（含 ALTER ADD COLUMN） | 否 | 每缺一项合并为一条 → ⚠️ |
| 枚举字段注释含值含义 | 字段名含 `status`/`type`/`flag`/`is_` 且 `COMMENT '...'` 内不含 `0-`/`1-` 等映射 | 否 | ⚠️ |
| 禁用 `timestamp` | `grep -niE '\btimestamp\b' 文件` | 否 | ❌ |
| 禁用外键 | `grep -niE '\bforeign\s+key\b' 文件` | 否 | ❌ |
| 字符集/排序 | `grep -nE 'CHARSET=utf8mb4.*COLLATE=utf8mb4_general_ci' 文件` | 是 | 不匹配 → ❌ |
| 主键 `bigint` | `id` 行类型非 `bigint` | 是 | ❌ |
| 注释格式（中文冒号 + 字段名） | 枚举字段 `COMMENT` 不形如 `'状态：0-…'`（含中文冒号） | 否 | ⚠️ |

### 6.2 Java Controller（总纲 §六 Controller + 附录 B）

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| 继承 `CommonController` | `grep -nE 'class \w+Controller\s+extends\s+CommonController' 文件` | 不匹配 → ❌ |
| `@Scope("prototype")` | 类头部缺 `@Scope("prototype")` | ❌ |
| 类上误加 `@RequestMapping` | `[posix-shell]` `awk '/^class\|public class/{p=NR} /@RequestMapping/ && NR<=p+5' 文件`<br>cmd 替代：`git grep -nE '@RequestMapping' -- '*Controller.java'` 然后人工核对命中行是否在 class 上方 5 行内 | ❌ |
| `@Autowired` 残留 | `grep -nE '@Autowired\b' 文件` | ❌ |
| `Passport` 作为接口入参 | `grep -nE 'public\s+\S+\s+\w+\([^)]*Passport\b' 文件` | ❌ |
| 多接口共用 Request | 同一 Request 类被两个以上 `@RequestBody` 引用：先收集 Request 类名，再 `grep -rn "@RequestBody $name" service/`，计数 ≥ 2 | ⚠️ |
| 多接口共用 VO | 同上替换为 VO 类名，查 Controller 返回类型 | ⚠️ |
| 标准 CRUD URL（仅写操作） | 先确认方法名属于 `{add\|save\|insert\|update\|remove\|delete\|batch...}` 集合，再校验 `@(Post\|Get\|Put\|Delete)Mapping("/...")` 路径形如 `/{entity}/{该操作标准名}`。**业务性命名一律跳过**：列表查询 `list`/`listPage`/`pageList`/`getList`/`listAll`/`allList` 任选其一均合规；业务自定义接口（chart / export / statistics / import / getPreAddInfo / actualPayAmount / approval / reject / check 等）跳过不报 | 偏离 → ⚠️ |
| Controller 内 try-catch | `grep -nE '\btry\s*\{' 文件` | ⚠️（除非有明确第三方调用） |

### 6.3 Java Service / Mapper（总纲 §六 Service + Mapper）

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| ServiceImpl 继承 | `grep -nE 'extends\s+BaseServiceImpl<' 文件` | 不匹配 → ❌ |
| 写操作缺 `@Transactional` | 方法名匹配 `^(add\|create\|save\|insert\|update\|delete\|remove\|batch\w+)` 且其上方 3 行内无 `@Transactional` | ❌ |
| `@Transactional` 缺 `rollbackFor = Exception.class` | `grep -nE '@Transactional' 文件` 同行既不含 `Exception\.class` 也不含 `Throwable\.class`（Throwable 更严也接受） | ⚠️ |
| service 注入他人 Mapper | `grep -nE '@Resource[\s\S]{0,40}private\s+\w+Mapper' ServiceImpl 文件` 后比对 Mapper 名与当前类 Model 不一致 | ❌ |
| service 注入他人 Service（跨模块）| `grep -nE 'private\s+\w+Service\b' 文件` → 比对包路径 | ❌ |
| 循环内调 mapper | `[posix-shell]` awk 找 `for\|forEach\|stream` 块内含 `Mapper.` 调用<br>cmd 替代：`git grep -nE '\bfor\s*\(\|\.forEach\(\|\.stream\(' -- '*Impl.java'` 然后人工核对块内是否调 Mapper | ⚠️ |
| 嵌套深度 > 3 / 循环 > 2 | `[posix-shell]` `awk 'BEGIN{d=0} /\{/{d++; if(d>3) print FILENAME":"NR": depth="d} /\}/{d--}' 文件`<br>cmd 无可靠替代 → 跳过并标"待人工确认" | ⚠️ |
| 对象拷贝散写 set | 同一方法内连续 ≥ 3 个 `xxx.setYyy(src.getYyy())` 而未用 `BeanUtils.copyProperties` | ⚠️ |
| 手写雪花/UUID 当 ID | `grep -nE 'setId\((UUID\.\|System\.currentTime\|new SnowFlake\|new IdWorker)' 文件` 而非 `IdWorker.getId()` | ❌ |
| SQL 注解硬编码 | `grep -nE '@(Select\|Insert\|Update\|Delete)\(' 文件` | ⚠️（应入 XML） |
| 手动 Long↔String | `grep -nE 'String\.valueOf\([^)]*[Ii]d\)\|Long\.parseLong\([^)]*[Ii]d\)' 文件` | ❌ |
| FastJSON | `grep -rn 'com\.alibaba\.fastjson' 文件` | ❌ |
| Hutool 新引入 | `git diff 文件 \| grep '^+.*cn\.hutool'` | ⚠️ |
| 非标分页框架 | `grep -nE 'PageHelper\|PageInfo' 文件` | ⚠️（应用 `selectListPage` / `IPage`） |
| Mapper XML 关键字未大写 | `grep -nE '\bselect\b\|\bfrom\b\|\bwhere\b\|\bleft join\b' 文件.xml`（小写命中） | ⚠️ |
| Mapper XML JOIN/ON 跨行 | `[posix-shell]` `awk '/JOIN/{j=NR} j && NR==j+1 && /^\s*ON\b/' 文件.xml`<br>cmd 替代：肉眼检查（XML 文件少，可接受） | ⚠️ |

### 6.4 Java Model / Request / VO（总纲 §六 Entity）

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| Model 继承 base | `grep -nE 'class \w+Model\s+extends\s+(TenantBaseModel\|MybatisBaseModel)' facade/model/**/*.java` | 不匹配 → ❌ |
| Request 命名规约 | 文件名匹配 `Xxx(Create\|Update\|Save\|Search\|Find\|Delete\|BatchDelete)Request\.java`，类继承对应 base。`Save` 仅限 BPM 流程 / 草稿+提交合一场景（类含 `dealMark` 或 `runProcessParam` 字段，或 Controller 只暴露单一 `/xxx/save` 端点） | 不匹配 → ⚠️ |
| Request 缺 `@Accessors(chain=true)` | `grep -nE '@Accessors\(chain\s*=\s*true\)' Request 文件` | 缺 → ⚠️ |
| Request 缺 `@EqualsAndHashCode(callSuper = true)` | grep 同上 | 缺 → ⚠️ |
| 校验注解缺 message | `grep -nE '@(NotNull\|NotBlank\|NotEmpty)\b[^(]' 文件`（即 `@NotNull` 后无 `(`） | ⚠️ |
| VO 继承 `BaseVO` | `grep -nE 'class \w+VO\s+extends\s+BaseVO' facade/vo/**/*.java` | 不匹配 → ❌ |
| `@JsonSerialize` 手动 Long↔String | `grep -nE '@JsonSerialize.*ToStringSerializer' 文件` | ❌ |
| `FieldStrategy.ALWAYS` | `grep -nE 'FieldStrategy\.ALWAYS' 文件` | ❌（应改用 `updateByIdWithNull`） |

### 6.5 Vue 前端（总纲 §九 + 附录 B）

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| `<script setup lang="ts">` | `grep -nE '<script setup' 文件.vue` 不含 `lang="ts"` | ⚠️ |
| Options API 残留 | `grep -nE 'export default \{' 文件.vue` 后续 30 行含 `data\s*\(\)` 或 `methods\s*:` | ❌ |
| `<style>` 缺 `scoped` | `grep -nE '<style(?!.*scoped)' 文件.vue`（PCRE）或 `grep '<style' \| grep -v scoped` | ⚠️ |
| `v-for` 使用 index 为 key | `grep -nE 'v-for=.*\)' 文件.vue \| grep -E ':key="?(index\|idx\|i)"?'` | ❌ |
| `v-for` 缺 `:key` | 含 `v-for` 行后 200 字符内无 `:key` | ❌ |
| 同元素 `v-if` + `v-for` | `grep -nE 'v-(if\|for)=.*v-(for\|if)=' 文件.vue` | ⚠️ |
| `console.*` 残留 | `grep -nE '\bconsole\.(log\|debug\|info\|warn)\(' 文件` | ⚠️ |
| 组件内直接调 axios/fetch | `grep -nE '\baxios\.(get\|post\|put\|delete)\(\|\bfetch\(' src/views/**/*.vue` | ⚠️ |
| `prop` 缺 type/required/default | `defineProps` 内每个属性需含 `type` `required` `default` 三键，缺则 ⚠️ |
| `:deep()` 覆盖全局 Element 样式 | `grep -nE ':deep\(\.el-' 文件` | ⚠️ |
| 页面布局规范 | `[human-only]`：嵌套 `form_wrapper`、`PurvarSubTitle` 位置、30px 间距等需结合 DOM 层级目视审查 |

### 6.6 配置 / POM（总纲 §五 + 七）

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| 新增 POM 依赖 | `git diff pom.xml \| grep -E '^\+\s*<artifactId>'` | ⚠️（提醒走架构审批） |
| FastJSON 依赖 | `grep -rn 'fastjson' pom.xml` | ❌ |
| API 同步开关 | `grep -nE 'api-sync:' application*.yml` 必须含 `enabled: true` 与 `gateway-prefix: /ct-service` | 缺 → ⚠️ |
| 时区注解滥用 | `grep -nE '@JsonFormat.*timezone' src/main/java` | ⚠️ |

### 6.7 硬编码红线（总纲 §四 + §八 + §九 + 附录 A）⚠️ 高危专题

凡涉及"标识符 / 权限 / 租户"的硬编码全是 ❌ **违规**，必改。本节是高危 review 区。

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| 后端硬编码角色 ID | `grep -rnE '\b(roleId\|ROLE_ID)\b\s*[=:]\s*"?\d+' service/` | ❌（应用编码 roleCode） |
| 后端硬编码岗位 ID | `grep -rnE '\b(postId\|POST_ID\|positionId)\b\s*[=:]\s*"?\d+' service/` | ❌（应用 postCode） |
| 后端硬编码菜单 ID | `grep -rnE '\b(menuId\|MENU_ID)\b\s*[=:]\s*"?\d+' service/` | ❌（应用 menuCode 或 funcCode） |
| 后端硬编码 appId | `grep -rnE '\bappId\b\s*[=:]\s*"?\d+' service/` | ❌（应走配置） |
| 后端硬编码 tenantId | `grep -rnE '\b(tenantId\|TENANT_ID)\b\s*[=:]\s*"?\d+' service/` | ❌（应走 AuthUtil / 框架） |
| 后端硬编码数据权限范围 | `grep -rnE '\b(dataScope\|dataPerm\|permRange)\b\s*[=:]\s*"?\d+' service/` 或字符串字面量含特定组织/部门 ID | ❌（总纲 §八） |
| 后端流程 / BPM 硬编码 | `grep -rnE 'fixflow_apply_info\|flow.*[=:]\s*\d+' service/` 看是否含特定流程 ID | ❌ |
| 前端硬编码 appId | `grep -rnE '\bappId\b\s*[=:]\s*["'\'']?\d+' ei-view/src/ \| grep -v 'import.meta.env.VITE_APP_ID'` | ❌ |
| 前端硬编码 menuId / roleId / postId | `grep -rnE '\b(menuId\|roleId\|postId)\b\s*[=:]\s*["'\'']?\d+' ei-view/src/` | ❌ |
| 前端硬编码 x-tenant-id（请求头） | `grep -rnE 'x-tenant-id["'\'']?\s*[:=]\s*["'\'']?\d+' ei-view/src/` 或 axios interceptor 内 tenant-id 字面值 | ❌（必须从 `localStorage.getItem('x-tenant-id')` 取） |
| 前端路由静态硬编码 ID | `grep -rnE 'path:\s*["'\''][^"'\'']*\b\d{4,}\b' ei-view/src/router/` | ❌（路由 ID 须走 UIM `func_perm`） |
| 前端 constantRoutes 含业务路由 | `grep -n 'constantRoutes' ei-view/src/router/index.ts` 后查内容，**非 BPM** 业务模块路由不应在 constantRoutes 中。BPM 流程页（路径含 `Bpm`/`bpm` 或 `/process/` 下）永久豁免——工作流框架直跳，不进菜单 | ❌ |
| 前端 `VITE_APP_ID` 未在 `.env` 声明 | 用了 `import.meta.env.VITE_APP_ID` 但 `.env*` 文件无此键 | ⚠️ |

**反例（必改）：**

```java
// ❌ 后端
if (user.getRoleId().equals(1001L)) { ... }
List<Long> deptIds = Arrays.asList(2001L, 2002L);  // 数据权限范围硬编码

// ✅ 改为编码
if ("ADMIN".equals(user.getRoleCode())) { ... }
// 数据权限通过 AuthUtil + 注解或动态查询取得
```

```ts
// ❌ 前端
const appId = '10001';
axios.defaults.headers['x-tenant-id'] = '1001';
const menuId = 88;

// ✅ 改为
const appId = import.meta.env.VITE_APP_ID;
axios.defaults.headers['x-tenant-id'] = localStorage.getItem('x-tenant-id');
// 菜单 ID 由 UIM func_perm 动态返回，不硬编码
```

### 6.8 Git Commit 信息（总纲 §十）

仅在用户要求审查 commit 或准备 push 时执行（默认 `git log -n 5`）。

| 检测 | 命令 / 模式 | 命中判定 |
|------|-------------|---------|
| 格式合规 | 主题行匹配 `^(feat\|fix\|docs\|style\|refactor\|perf\|test\|chore\|revert\|build)\([^)]+\): .+ \[#[TB]\d+\]` | 不匹配 → ⚠️ |
| 模糊描述 | 主题含 `修改bug\|修复问题\|更新代码\|优化\s*$` 且无对象 | ⚠️ |
| 简体中文 | `[human-only]`：主体语言判断 |
| 缺禅道编号 | 主题末缺 `[#T...]` 或 `[#B...]` | ⚠️ |

## 七、常见违规高频清单（一键扫，cmd 友好）

以下命令使用 **`git grep`**，**Windows cmd / PowerShell / bash 全部直接可跑**，无需 grep/awk。仓库根目录执行，开场先扫一遍，命中后再回 §六 细分类。

```bash
# === 后端高频 ===
git grep -nE '@Autowired\b' -- 'ei-service/src/main/java/*'
git grep -nE 'com\.alibaba\.fastjson' -- 'ei-service/*'
git grep -nE '\bFieldStrategy\.ALWAYS\b' -- 'ei-service/*'
git grep -nE '@JsonSerialize.*ToStringSerializer' -- 'ei-service/*'
git grep -nE 'String\.valueOf\([^)]*[Ii]d\)' -- 'ei-service/*'
git grep -niE '\btimestamp\b' -- 'ei-service/src/main/resources/mybatis/*' 'docs/features/fund/sql/*'
git grep -niE '\bforeign\s+key\b' -- 'docs/*' 'ei-service/*'
git grep -nE '(PageHelper|PageInfo)' -- 'ei-service/*'

# === 前端高频 ===
git grep -nE '\bconsole\.(log|debug|info)\(' -- 'ei-view/src/*'
git grep -nE 'v-for=.*:key="?(index|idx|i)"?' -- 'ei-view/src/*'
git grep -nE 'export default \{' -- 'ei-view/src/**/*.vue'

# === 硬编码红线（§6.7 高危）===
git grep -nE '\b(roleId|postId|menuId|appId)\b\s*=\s*"?[0-9]+' -- 'ei-service/*'
git grep -nE '\b(roleId|postId|menuId|appId)\b\s*[=:]\s*["'\'']?[0-9]+' -- 'ei-view/src/*'
git grep -nE 'x-tenant-id["'\'']?\s*[:=]\s*["'\'']?[0-9]+' -- 'ei-view/src/*'
```

每条命令的预期是**零命中**；任何输出即对应严重度的违规候选。

**Windows cmd 注意：**
- `git grep` 单引号在 cmd 下行为正常，无需改成双引号
- 路径用正斜杠 `/`，git 自动适配
- pathspec 通配（`'**/*.java'`）由 git 解析，不依赖 cmd
- 若提示 `bad pathspec`，把 `*` 改成显式目录路径即可

**范围限定（只扫本次 diff，避免历史欠债噪音）：**

```bash
# 列出本次 diff 涉及的 Java 文件，传给 git grep 做 pathspec
git diff --name-only HEAD -- '*.java' > /tmp/diff_files.txt
git grep -nE '@Autowired\b' -- $(cat /tmp/diff_files.txt 2>/dev/null) 2>/dev/null
```

cmd 下没有 `$(...)`，用：

```cmd
git diff --name-only HEAD -- *.java > %TEMP%\diff.txt
for /f %f in (%TEMP%\diff.txt) do git grep -nE "@Autowired\b" -- "%f"
```

或更稳的办法：让 agent 在内存里维护文件清单（用 git diff 取一次，循环 grep 时 pathspec 数组传入），不依赖 shell 跨平台细节。

## 八、给 agent 的元规则

- **不要擅自修复**。先出报告，得到用户确认后再改。
- **不要扩大范围**。用户没说"全文 review"就别去查 diff 之外的旧代码。
- **遇到拿不准的规范条款 → 直接打开总纲对应章节核对**，不要按记忆判断。
- **遇到总纲未覆盖的场景** → 标 ⚠️ 并备注"规范未覆盖，建议人工确认"，不要自创规则。
- **输出严格按 §五 模板**，便于跨 agent / 跨次 review 做对比。
- **如果改动为空**（无 diff），直接告诉用户"无待审查改动"并退出，不要伪造报告。
