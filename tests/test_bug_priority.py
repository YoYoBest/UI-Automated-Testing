import pytest

from ei_ui_smoke.bug_priority import assess_bug


@pytest.mark.parametrize(
    ("message", "severity", "priority"),
    [
        ("生产环境发生越权访问，全部用户受影响", 1, 1),
        ("新增保存失败，详情接口未返回业务主键", 2, 2),
        ("点击保存后没有捕获保存接口响应", 2, 2),
        ("验收阻塞：审批提交失败", 2, 1),
        ("验收阻塞：页面按钮对齐和颜色不一致", 4, 1),
        ("筛选条件在特定场景无效", 3, 3),
        ("页面按钮对齐和颜色不一致", 4, 4),
        ("体验优化建议：增加批量选择", 5, 5),
    ],
)
def test_assess_bug_rules(message: str, severity: int, priority: int) -> None:
    result = assess_bug(message=message)
    assert result.severity == severity
    assert result.priority == priority
    assert result.reason


def test_current_page_access_failure_defaults_to_three() -> None:
    result = assess_bug(
        title="【基础管理】自动新增及详情核对失败",
        message="所选模块未完成新增及详情接口核对，page_access != add_and_detail_verified",
    )
    assert (result.severity, result.priority) == (3, 3)
    assert result.requires_review is True
