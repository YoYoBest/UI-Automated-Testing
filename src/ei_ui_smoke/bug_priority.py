from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BugAssessment:
    severity: int
    priority: int
    reason: str
    requires_review: bool = False


_FATAL = (
    "数据丢失", "数据泄露", "数据损坏", "串户", "越权", "未授权", "资金错误",
    "无法登录", "系统不可用", "服务不可用", "data loss", "data leak",
    "unauthorized", "permission bypass", "security breach",
)
_SERIOUS = (
    "保存失败", "提交失败", "审批失败", "支付失败", "新增失败", "无法保存",
    "无法提交", "无法审批", "详情数据错误", "详情值不一致", "回显不一致",
    "接口返回业务失败", "保存接口响应", "保存接口无响应", "http 500",
    "internal server error",
)
_MINOR = (
    "错别字", "文案", "对齐", "颜色", "间距", "样式", "布局", "提示不准确",
    "拼写", "cosmetic", "alignment", "typo",
)
_SUGGESTION = ("优化建议", "体验优化", "建议增加", "建议支持", "enhancement", "suggestion")
_URGENT = (
    "生产环境", "线上环境", "发布阻塞", "阻塞发布", "验收阻塞", "阻塞验收",
    "测试阻塞", "阻塞测试", "全量用户", "全部用户", "production", "release blocker",
)


def assess_bug(*, title: str = "", message: str = "", module: str = "", failure_url: str = "") -> BugAssessment:
    evidence = " ".join((title, message, module, failure_url)).casefold()
    if any(keyword in evidence for keyword in _FATAL):
        severity = 1
        severity_reason = "涉及系统可用性、数据、资金、权限或安全风险"
    elif any(keyword in evidence for keyword in _SERIOUS):
        severity = 2
        severity_reason = "核心新增、保存、提交、审批或数据正确性流程失败"
    elif any(keyword in evidence for keyword in _SUGGESTION):
        severity = 5
        severity_reason = "属于体验或能力优化建议"
    elif any(keyword in evidence for keyword in _MINOR):
        severity = 4
        severity_reason = "仅影响文案、样式、布局或提示"
    else:
        severity = 3
        severity_reason = "局部功能异常，未识别到致命或核心流程风险"

    urgent = any(keyword in evidence for keyword in _URGENT)
    if urgent:
        priority = 1
        priority_reason = "且阻塞生产、发布、验收或测试"
    elif severity <= 2:
        priority = 2
        priority_reason = "需在当前版本优先修复"
    else:
        priority = severity
        priority_reason = "按默认严重程度映射处理"
    requires_review = severity == 3 and not any(
        keyword in evidence for keyword in (*_FATAL, *_SERIOUS, *_MINOR, *_SUGGESTION)
    )
    return BugAssessment(
        severity,
        priority,
        f"{severity_reason}，{priority_reason}",
        requires_review,
    )
