import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import allure
import pytest

from ei_ui_smoke.config import Settings
from ei_ui_smoke.failure_evidence import (
    capture_failure_evidence,
    consume_failure_evidence,
)
from ei_ui_smoke.qcc_browser import install_qcc_route
from ei_ui_smoke.qcc_proxy import QccSearchService, QccSettings
from ei_ui_smoke.workflow import (
    RoleSessionPool,
    RoleSessionSpec,
    WorkflowBuildContext,
    WorkflowConfigurationError,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowRunner,
    load_workflow_definition,
    page_scope_from_url,
    parse_role_session_specs,
    validate_role_session_specs,
    workflow_snapshot_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
_INDEPENDENT_ACTION_ENV = (
    "EI_ACTION",
    "EI_ACTIONS_JSON",
    "EI_ACTION_PATH",
    "EI_ACTION_PATHS_JSON",
)


@dataclass(frozen=True, slots=True)
class _ConfiguredWorkflow:
    definition: WorkflowDefinition = field(repr=False)
    role_specs: tuple[RoleSessionSpec, ...] = field(repr=False)
    data_mode: str
    run_id: str = field(repr=False)
    snapshot_path: Path = field(repr=False)
    headless: bool = field(repr=False)

    @property
    def workflow_id(self) -> str:
        return self.definition.workflow_id

    def __repr__(self) -> str:
        return self.workflow_id


def _workflow_run_id() -> str:
    run_id = os.getenv("EI_AUTOMATION_RUN_ID", "").strip() or uuid.uuid4().hex
    sequence = os.getenv("EI_AUTOMATION_TARGET_SEQUENCE", "").strip()
    return f"{run_id}-{sequence}" if sequence else run_id


def _load_configured_workflow(pytest_config) -> _ConfiguredWorkflow | None:
    workflow_id = os.getenv("EI_WORKFLOW_ID", "").strip()
    if not workflow_id:
        return None

    factory_spec = os.getenv("EI_WORKFLOW_FACTORY", "").strip()
    if not factory_spec:
        raise WorkflowConfigurationError(
            "EI_WORKFLOW_FACTORY is required when EI_WORKFLOW_ID is selected"
        )
    conflicting = [
        name for name in _INDEPENDENT_ACTION_ENV if os.getenv(name, "").strip()
    ]
    if conflicting:
        raise WorkflowConfigurationError(
            "业务流程入口不能继承独立动作调度变量：" + ", ".join(conflicting)
        )

    settings = Settings.from_env()
    data_mode = pytest_config.getoption("--data-mode") or settings.data_mode
    run_id = _workflow_run_id()
    build_context = WorkflowBuildContext(
        project_root=PROJECT_ROOT,
        workflow_id=workflow_id,
        run_id=run_id,
        data_mode=data_mode,
    )
    definition = load_workflow_definition(factory_spec, build_context)
    definition.steps_for(data_mode)
    role_specs = parse_role_session_specs(
        os.getenv("EI_WORKFLOW_ROLE_STATES_JSON", ""),
        base_dir=PROJECT_ROOT,
    )
    validate_role_session_specs(role_specs, definition.required_roles(data_mode))
    return _ConfiguredWorkflow(
        definition=definition,
        role_specs=role_specs,
        data_mode=data_mode,
        run_id=run_id,
        snapshot_path=workflow_snapshot_path(
            PROJECT_ROOT,
            run_id=run_id,
            workflow_id=definition.workflow_id,
        ),
        headless=settings.headless,
    )


def pytest_generate_tests(metafunc) -> None:
    if "workflow_case" not in metafunc.fixturenames:
        return
    try:
        workflow_case = _load_configured_workflow(metafunc.config)
    except WorkflowConfigurationError as exc:
        raise pytest.UsageError(str(exc)) from exc
    if workflow_case is None:
        metafunc.parametrize(
            "workflow_case",
            [
                pytest.param(
                    None,
                    marks=pytest.mark.skip(
                        reason="set EI_WORKFLOW_ID to run a configured business workflow"
                    ),
                    id="workflow-not-selected",
                )
            ],
        )
        return
    metafunc.parametrize(
        "workflow_case",
        [pytest.param(workflow_case, id=workflow_case.workflow_id)],
    )


def _failed_step_role(context: WorkflowContext) -> str:
    for record in reversed(context.step_records):
        if record.status == "failed":
            return record.role
    return ""


def _attach_workflow_failure(
    pool: RoleSessionPool, context: WorkflowContext
) -> None:
    failed_role = _failed_step_role(context)
    active_workflow_role = getattr(pool, "current_workflow_role", pool.current_role)
    if not failed_role or active_workflow_role != failed_role:
        return
    page = pool.current_page
    if page is None:
        return
    try:
        evidence = consume_failure_evidence(page)
        if evidence is None:
            capture_failure_evidence(page, "业务流程步骤失败")
            evidence = consume_failure_evidence(page)
        if evidence is None:
            return
        safe_url = page_scope_from_url(evidence.url)
        if not safe_url:
            return
        allure.attach(safe_url, "失败页面 URL", allure.attachment_type.TEXT)
        allure.attach(
            evidence.screenshot, "失败页面截图", allure.attachment_type.PNG
        )
    except Exception:
        pass


@pytest.mark.smoke
def test_selected_business_workflow(request, workflow_case):
    """Run one dependency-aware business workflow as one pytest/Allure item."""
    if not request.config.getoption("--browser-smoke"):
        pytest.skip("use --browser-smoke to run a deployed business workflow")

    assert isinstance(workflow_case, _ConfiguredWorkflow)
    workflow_context = WorkflowContext(
        workflow_id=workflow_case.workflow_id,
        run_id=workflow_case.run_id,
        data_mode=workflow_case.data_mode,
    )

    from playwright.sync_api import sync_playwright

    qcc_mode = os.getenv("QCC_BROWSER_MODE", "backend").strip().lower()
    qcc_settings = (
        QccSettings.from_env()
        if qcc_mode == "real"
        else QccSettings(mode=qcc_mode if qcc_mode == "mock" else "mock")
    )
    qcc_service = QccSearchService(qcc_settings)

    def initialize_context(context, _role):
        install_qcc_route(
            context,
            qcc_service,
            backend_mode=qcc_mode == "backend",
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=workflow_case.headless)
        with RoleSessionPool(
            browser,
            workflow_case.role_specs,
            context_initializer=initialize_context,
            owns_browser=True,
        ) as sessions:
            try:
                WorkflowRunner(
                    sessions, snapshot_path=workflow_case.snapshot_path
                ).run(
                    workflow_case.definition, workflow_context
                )
            except Exception:
                _attach_workflow_failure(sessions, workflow_context)
                raise

    assert workflow_context.status == "passed"
