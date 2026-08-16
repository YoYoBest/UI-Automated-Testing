"""UI-backed data prerequisites for project-progress Add operations."""

from __future__ import annotations

from pathlib import Path

from .data_pool import GlobalDataPool
from .data_strategy import create_data_strategy
from .dynamic_collections import load_dynamic_collection_specs
from .module_driver import ModuleSmokeDriver
from .source_form import discover_custom_form_fields
from .urls import detail_parent_url
from .config import Settings


_PARENT_COMPONENT = "buildProject/index"
_PARENT_FORM_CODE = "BUILD_PROJ_APP_INFO"
_DECISION_COMPONENT = "buildProject/before/projectDecision/DecisionList"
_DECISION_FORM_CODE = "BUILD_PROJ_DECISIONFOM"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _related_form_driver(page, *, component: str, form_code: str) -> ModuleSmokeDriver:
    root = _project_root()
    settings = Settings.from_env()
    pool = GlobalDataPool.from_directory(root / "data")
    return ModuleSmokeDriver(
        page,
        create_data_strategy("standard", pool, form_code),
        source_fields=discover_custom_form_fields(settings.source_root, component),
        default_upload_file=pool.default_upload_file(),
        dynamic_collections=load_dynamic_collection_specs(
            root / "data", form_code=form_code, component=component,
        ),
        automation_record_registry=(
            root / "artifacts" / "automation-record-registry.json"
        ),
    )


def project_decision_add_module(module_name: str) -> str:
    """Derive the fixed decision prerequisite under the current detail root."""
    parts = [part.strip() for part in module_name.split("/") if part.strip()]
    try:
        detail_index = parts.index("详情")
    except ValueError as exc:
        raise AssertionError(
            "项目进度详情路径缺少“详情”节点，无法建立项目决策前置"
        ) from exc
    return "/".join(parts[: detail_index + 1] + ["投前管理", "项目决策", "新增"])


def project_progress_parent_provisioner(page, detail_url: str, module_name: str):
    """Return a callback that creates and submits one approved-decision parent.

    The decision service auto-approves an initial submission by default.  A
    deployment that disables that behavior remains supported: the caller
    performs the final `projProgress/preAdd` verification and reports the
    unsatisfied approval prerequisite instead of assuming that a draft works.
    """
    def provision():
        from .detail_navigation import (
            detail_navigation_labels,
            enter_detail_record,
            navigate_detail_module,
        )

        page.goto(detail_parent_url(detail_url), wait_until="domcontentloaded")
        parent = _related_form_driver(
            page, component=_PARENT_COMPONENT, form_code=_PARENT_FORM_CODE,
        ).run(provision_only=True)
        if not getattr(parent, "business_id", ""):
            raise AssertionError("项目进度前置父项目创建未返回业务 ID")

        enter_detail_record(page, detail_url, record_identity=parent)
        decision_module = project_decision_add_module(module_name)
        navigate_detail_module(
            page,
            decision_module,
            "新增",
            navigation_labels=detail_navigation_labels(decision_module, "新增"),
        )
        try:
            decision = _related_form_driver(
                page,
                component=_DECISION_COMPONENT,
                form_code=_DECISION_FORM_CODE,
            ).run(provision_only=True, submit=True)
        except AssertionError as exc:
            raise AssertionError(
                "无法建立项目进度前置：项目决策创建或提交失败；"
                "需要项目决策审批通过后才能新增项目进度"
            ) from exc
        if not getattr(decision, "business_id", ""):
            raise AssertionError("项目进度前置项目决策提交未返回业务 ID")
        return parent

    return provision
