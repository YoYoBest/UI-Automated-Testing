---
name: bug-severity-priority
description: Assess software defects with separate severity and repair-priority levels, explain the evidence, and identify cases requiring human review. Use when triaging bugs, converting Allure or pytest failures into ZenTao issues, reviewing release risk, disputing a bug level, or implementing severity/priority automation in this repository.
---

# Bug 严重程度与优先级

## 判级流程

1. 读取标题、错误信息、模块、失败 URL、环境、影响范围、可绕行性和发布节点。
2. 先判断严重程度，再独立判断优先级；不要因为排期紧急而提高严重程度。
3. 输出 `severity`、`priority`、`reason` 和 `requires_review`。
4. 信息不足时使用 `3/3`，设置 `requires_review=true`，不要猜测生产影响或数据风险。
5. 在本仓库自动判级时以 `src/ei_ui_smoke/bug_priority.py` 为可执行事实标准，并用 `tests/test_bug_priority.py` 验证规则。

## 严重程度

| 等级 | 判定标准 | 典型证据 |
|---|---|---|
| `1 致命` | 系统不可用，或存在数据、资金、权限、安全风险 | 数据丢失、串户、越权、无法登录、资金错误 |
| `2 严重` | 核心业务闭环失败或重要数据不正确 | 新增/保存/提交/审批/支付失败，详情数据不一致，HTTP 500 |
| `3 一般` | 局部功能异常，未识别到核心或高风险影响 | 查询、筛选、非核心入口或普通自动化失败 |
| `4 轻微` | 只影响展示、文案或易用性 | 错别字、对齐、颜色、间距、提示不准确 |
| `5 建议` | 没有违反明确需求，仅为能力或体验改进 | 优化建议、增加辅助能力 |

同一问题命中多个等级时取最严重等级。数据安全、权限和资金证据优先于其他关键词。

已确认“点击保存后未捕获保存接口响应”属于核心保存闭环失败，自动初判为 `2/2`；若同时存在已证实的越权、数据泄露或安全绕过证据，再按 `1` 级安全风险处理，不以安全测试名称单独升到致命。

## 优先级

| 条件 | 优先级 |
|---|---|
| 任意等级问题明确阻塞生产、发布、验收或测试 | `1 紧急` |
| 其他 `1/2` 级问题 | `2 高` |
| 一般功能问题 | `3 中` |
| 轻微展示问题 | `4 低` |
| 优化建议 | `5 暂缓` |

不得只按严重程度机械复制优先级。客户验收时的文案错误可以是 `4/1`；低概率数据损坏可以是 `1/2`。

## 输出格式

```json
{
  "severity": 3,
  "priority": 3,
  "reason": "局部功能异常，未识别到致命或核心流程风险，按默认严重程度映射处理",
  "requires_review": true
}
```

## 复核边界

- `severity=1` 或 `priority=1` 必须人工确认影响范围并立即通知负责人。
- 缺少环境、影响范围或绕行方案时必须标记人工复核。
- 开发、测试和产品有争议时保留原始证据，由测试负责人或项目负责人裁决。
- 允许人工调整自动结果，但必须记录调整理由，不要静默覆盖。
- 严重程度判级只适用于产品缺陷。定位器、浏览器、测试配置、登录态和环境连接异常先归类为 `automation`/`environment`，不得直接创建产品 Bug。
- 纯颜色、间距、美观度和主观体验问题依赖人工判断；自动化只能对遮挡、溢出、截断、不可点击和带明确阈值的几何错位判级。
- `unknown` 归属不得直接判为产品 Bug；保留证据并强制人工复核。状态流转、幂等、并发、事务、计算、查询、文件、恢复、兼容、审计、外部依赖和容量问题只有在存在对应场景断言时才进入产品缺陷判级。
