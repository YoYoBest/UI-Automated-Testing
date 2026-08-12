# Skills

本目录保存本仓库维护的 agent skills 源文件。每个 skill 使用一个独立目录，目录内必须包含 `SKILL.md`。

```text
skills/
├── code-self-review/
│   └── SKILL.md
├── excel-pytest-case-design/
│   ├── SKILL.md
│   └── references/
│       └── parameterization-standard.md
├── dynamic-form-runtime-fields/
│   └── SKILL.md
├── generic-module-crud-smoke/
│   └── SKILL.md
├── runtime-module-discovery/
│   └── SKILL.md
├── smoke-test-data-strategy/
│   └── SKILL.md
├── ui-smoke-test/
│   └── SKILL.md
└── vue-form-to-json/
    └── SKILL.md
```

## Clone 后能不能直接用

可以。clone 本仓库后，别人会拿到 `skills/*/SKILL.md` 这些源文件；本仓库的 `AGENTS.md` 和 `CLAUDE.md` 已经要求 Codex / Claude Code 在触发对应场景时直接读取项目内的 skill 文件。

这意味着团队协作时不需要每个人维护一份独立 skill 副本：

1. skill 源码只在仓库 `skills/<skill-name>/SKILL.md` 维护。
2. 同事 `git pull` 后拿到最新版本。
3. 在本项目内使用 Codex / Claude Code 时，由项目指令要求 agent 读取这份 skill。

需要注意：多数 agent 的"全局自动发现列表"仍然只扫描个人目录，例如 `~/.codex/skills` 或 `~/.claude/skills`；项目级目录 `.claude/skills/` 也可触发，但需要本人安装。所以 `/skill-name` 或 `$skill-name` 是否出现在 agent UI 的快捷触发列表，取决于个人是否安装了快捷入口。**在本项目内即使没安装，触发词也已经由 `AGENTS.md` / `CLAUDE.md` 兜底到项目内 `skills/`，自然语言"自检"/"按规范看看"等仍然有效。**

当前约定：

| 场景 | 入口 | 说明 |
|------|------|------|
| Codex 项目级使用 | `AGENTS.md` → `skills/<skill-name>/SKILL.md` | 默认，随仓库版本更新，无需安装 |
| Claude Code 项目级使用 | `CLAUDE.md` → `skills/<skill-name>/SKILL.md` | 默认，随仓库版本更新，无需安装 |
| `/skill-name`、`$skill-name` 快捷触发 | `.claude/skills/<name>` 或 `~/.claude/skills/<name>` / `~/.codex/skills/<name>` | 可选，需要本人自行安装，仓库不入库 |

仓库不入库 `.claude/skills/` 和 `.codex/skills/` 这两组项目内快捷入口。原因：跨平台 symlink 兼容性（尤其 Windows）和团队成员个人偏好都不统一，团队协作时只保证 `skills/` 作为唯一 SSOT、`AGENTS.md` / `CLAUDE.md` 作为兜底触发路径。

新增或修改 skill 后，通常需要重启 Codex / Claude 会话，自动发现列表才会刷新。

## 一键安装快捷入口（贴给 agent 让它自己干）

如果你想在 Claude Code 用 `/code-self-review`、在 Codex 用 `$code-self-review` 这类快捷触发，把下面这段提示词贴给你的 Claude Code 或 Codex 会话即可（在本仓库根目录运行）：

```text
请帮我把本仓库 skills/ 目录下的每个 skill 软链接到对应 agent 的快捷入口目录，便于我用 /skill-name 或 $skill-name 触发。具体要求：

1. 扫描当前工作目录下 ./skills/ 子目录，每个含有 SKILL.md 的子目录就是一个 skill。
2. 询问我要装到哪些目标位置（可多选）：
   - 项目级 Claude：./.claude/skills/
   - 项目级 Codex：./.codex/skills/
   - 全局 Claude：~/.claude/skills/
   - 全局 Codex：~/.codex/skills/
3. 创建软链接：
   - 项目级用相对路径（例如 ../../skills/<name>），方便跨机克隆。
   - 全局用绝对路径（指向当前仓库的 skills/<name>）。
   - macOS/Linux 用 ln -s；Windows 用 mklink /D（提示我可能需要管理员或开启 Developer Mode）。
4. 如果目标位置已存在同名条目，先 ls -la 显示当前指向，问我覆盖还是跳过，不要静默删除。
5. 完成后列出所有创建/已存在的链接，并提示我重启 agent 会话才能刷新 discovery。
```

或者更简单的全选版本：

```text
请把当前仓库 ./skills/ 下的所有 skill 软链接到 ~/.claude/skills/ 和 ~/.codex/skills/（用绝对路径），以及 ./.claude/skills/ 和 ./.codex/skills/（用相对路径）。已存在的链接先告诉我指向哪里再决定覆盖。Windows 改用 mklink /D。完成后列出最终清单。
```

## 关于 `.skill` 包

如果把 skill 打成 `.skill` 包，适合做一次性分发或导入，但它通常是一个发布快照。后续仓库里的 `SKILL.md` 更新后，使用者还需要重新打包、重新安装，否则本机 agent 仍然使用旧版本。

本仓库更推荐下面这种方式：

1. `skills/<skill-name>/SKILL.md` 作为唯一源码。
2. Git 管理源码，团队通过 `git pull` 获得更新。
3. 项目级 `AGENTS.md` / `CLAUDE.md` 要求 agent 读取仓库源码目录——这是默认路径，无需安装。
4. 个人目录或项目级的快捷入口（`~/.codex/skills`、`~/.claude/skills`、`.claude/skills/`、`.codex/skills/`）只用于让 `/` 或 `$` 触发更顺手，按需用上一节的提示词自行安装。

这样更新路径最短：仓库更新后，源码和项目指令一起更新；如果使用了软链接快捷入口，软链接也会自动指向新内容，只需要重启 agent 会话刷新 discovery。

## 当前 skills

| Skill | 用途 | 当前状态 |
|-------|------|----------|
| `code-self-review` | 按 `coding_rules.md` 做提交前规范自检 | 项目级可用 |
| `excel-pytest-case-design` | 设计、审阅并改造 Excel 用例，使简单字段规则可稳定驱动 pytest 参数化 | 项目级可用 |
| `bug-severity-priority` | 区分并评估 Bug 严重程度、修复优先级及人工复核要求 | 项目级可用 |
| `dynamic-form-runtime-fields` | 复用非固定字段、半固定字段的动态渲染和运行时保存/更新/查询改造流程 | 项目级可用 |
| `generic-module-crud-smoke` | 执行新增、保存、业务主键及列表/详情回显的通用闭环 | 项目级可用 |
| `runtime-module-discovery` | 合并 EI/FI 源码页面索引、运行时权限菜单、路由和详情树 | 项目级可用 |
| `smoke-test-data-strategy` | 维护 probe/stable 模式、公共数据池和自动采集策略 | 项目级可用 |
| `vue-form-to-json` | 将 Vue3 + Element Plus + PurvarCol 表单提取为 JSON 配置 | 项目级可用 |
| `ui-smoke-test` | 编排 UI 冒烟、失败分类并生成和打开 Allure 报告 | 项目级可用 |
| `zentao-bug-submission` | 将 Allure 失败证据、判级结果和截图可靠录入禅道 Bug | 项目级可用 |

## 维护规则

- skill 源文件只维护在 `skills/<skill-name>/SKILL.md`，不在 `.claude/skills/` 或 `.codex/skills/` 入库副本或符号链接。
- 个人安装的快捷入口请用软链接（参考上面的"一键安装快捷入口"），不要拷贝目录，否则仓库更新后会失去同步。
- `SKILL.md` frontmatter 必须包含 `name` 和 `description`。
- `name` 必须与目录名一致，便于 `/skill-name` 或 `$skill-name` 触发。
- `description` 写触发场景，不写执行流程摘要。
- 项目规范类 skill 不要复制整份规范，应该引用仓库内的 SSOT 文档。
