import pytest

import ei_ui_smoke.common_field_executor as executor_module

from ei_ui_smoke.common_field_cases import (
    BoundCommonCase,
    BoundCommonTransaction,
    DiscoveredCommonField,
    FieldConstraints,
    REQUIRED_ERRORS_RECOVER,
    REQUIRED_ERRORS_TRIGGER,
)
from ei_ui_smoke.common_field_executor import (
    CommonFieldExecutionResult,
    CommonFieldExecutor,
    CommonFieldFormSession,
    FORM_CLOSE_SELECTOR,
)
from ei_ui_smoke.dynamic_collections import DynamicCollectionSpec
from ei_ui_smoke.dom import DOM_FIELD_SCRIPT
from ei_ui_smoke.interactions import FieldInteractor
from ei_ui_smoke.models import DomField, FieldDefinition, ResolvedField
from ei_ui_smoke.module_driver import FieldCompletionReport, ModuleSmokeResult
from tests.test_build_project_add_personalized import _field_label, _runtime_field


class _Page:
    url = "https://example.test/form"

    def __init__(self, events=None):
        self.events = events

    def screenshot(self, **kwargs):
        if self.events is not None:
            self.events.append(("screenshot", kwargs))
        return b"failure-png"

    @staticmethod
    def wait_for_timeout(_milliseconds):
        return None


class _Strategy:
    pass


def test_attachment_transaction_uploads_each_field_with_one_save(monkeypatch):
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.clear_failure_evidence",
        lambda _page: None,
    )
    cases = (
        BoundCommonCase(
            case_id="EDIT-004", field_key="approvalFile", field_label="批复文件",
            field_type="file", selector="#approval", scenario="附件正确性检查",
            input_value="合法附件", expected_type="accepted", expected_value="保存成功",
            priority="P1",
        ),
        BoundCommonCase(
            case_id="EDIT-004", field_key="dataFile", field_label="资料附件",
            field_type="file", selector="#data", scenario="附件正确性检查",
            input_value="合法附件", expected_type="accepted", expected_value="保存成功",
            priority="P1",
        ),
    )
    transaction = BoundCommonTransaction("TX-ATTACH", cases, "attachment_persistence")
    fields = {
        "approvalFile": DiscoveredCommonField(
            "approvalFile", "批复文件", "file", "file", "#approval", FieldConstraints()
        ),
        "dataFile": DiscoveredCommonField(
            "dataFile", "资料附件", "file", "file", "#data", FieldConstraints()
        ),
    }
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    events = []
    executor.driver = type(
        "Driver", (),
        {
            "_start_attachment_lifecycle_tracking": staticmethod(lambda: events.append("track-start") or object()),
            "_stop_attachment_lifecycle_tracking": staticmethod(lambda _tracker: events.append("track-stop")),
        },
    )()
    executor._run_in_form_session = lambda operation: operation(object())[0]
    executor._apply_case_branch_conditions = lambda *_args: None
    executor._fill_valid_baseline = lambda *_args, **_kwargs: {"name": "合法名称"}
    executor._wait_for_current_field = lambda case, _scope: fields[case.field_key]
    executor._attachment_names = lambda field, _scope: [f"old-{field.field_key}.txt"]
    executor._upload_edit_attachment = lambda field, _scope, _tracker: (
        events.append(f"upload:{field.field_key}") or f"{field.field_key}.txt"
    )
    executor._submit_case = lambda *args, **kwargs: (
        events.append("save") or CommonFieldExecutionResult(
            "EDIT-004", "approvalFile", "saved", "saved"
        )
    )
    executor._assert_attachment_persisted = lambda field, name, _before: events.append(
        f"readback:{field.field_key}:{name}"
    )
    executor._log_result = lambda case, result: events.append(
        f"report:{case.field_key}:{result.outcome}"
    )

    results = executor.execute_transaction(transaction)

    assert events == [
        "track-start", "upload:approvalFile", "upload:dataFile", "save",
        "readback:approvalFile:approvalFile.txt", "readback:dataFile:dataFile.txt",
        "report:approvalFile:saved", "report:dataFile:saved", "track-stop",
    ]
    assert [(result.field_key, result.outcome) for result in results] == [
        ("approvalFile", "saved"), ("dataFile", "saved"),
    ]
    assert all("shared_save=true" in result.observed for result in results)


def test_executor_passes_dynamic_collections_to_module_driver():
    spec = DynamicCollectionSpec(
        field_code="adjustmentItems",
        mode="selection",
        root_selector=".adjustment-type-form",
        create_selector=".el-checkbox",
        item_selector=".adjustment-type-item",
        min_rows=1,
        children=(),
    )

    executor = CommonFieldExecutor(
        _Page(), _Strategy(), dynamic_collections=[spec]
    )

    assert executor.driver.dynamic_collections == [spec]


class _Locator:
    def __init__(self, value="baseline"):
        self.value = value

    def input_value(self):
        return self.value


class _SessionScope:
    def __init__(self, name):
        self.name = name
        self.visible = True

    def element_handle(self):
        return self

    def is_visible(self):
        return self.visible


def _executor_for_form_session():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    events = []
    scopes = []

    def open_form():
        scope = _SessionScope(f"form-{len(scopes) + 1}")
        scopes.append(scope)
        events.append(("open", scope.name))
        return scope

    def close_form():
        events.append(("close", scopes[-1].name))
        scopes[-1].visible = False

    executor.open_fresh_add_form = open_form
    executor.close_form = close_form
    executor._dismiss_form_transients = lambda: events.append(("dismiss",))
    executor._stabilize_reusable_form = lambda scope: events.append(
        ("stabilize", scope.name)
    )
    executor._capture_failure_once = lambda _message: None
    return executor, events, scopes


def _case(expected_type="accepted"):
    return BoundCommonCase(
        case_id="ADD-001",
        field_key="name",
        field_label="名称",
        field_type="text",
        selector="#name",
        scenario="长度边界",
        input_value="new value",
        expected_type=expected_type,
        expected_value="",
        priority="P1",
    )


def test_edit_attachment_case_replaces_only_target_and_requires_readback():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    scope = object()
    field = DiscoveredCommonField(
        "file:report", "项目附件", "file", "file", "#report-file",
        FieldConstraints(),
    )
    case = BoundCommonCase(
        case_id="EDIT-004",
        field_key=field.field_key,
        field_label=field.label,
        field_type="file",
        selector=field.selector,
        scenario="修改附件正确性检查",
        input_value="重复上传附件",
        expected_type="accepted",
        expected_value="附件修改后保存成功并回显",
        priority="P1",
    )
    calls = []
    executor._fill_valid_baseline = lambda actual_scope, **kwargs: (
        calls.append(("baseline", actual_scope, kwargs)) or {"name": "AUTO_record"}
    )
    executor._attachment_names = lambda _field, _scope: ["old.pdf"]
    tracker = object()
    executor.driver = type("Driver", (), {
        "_start_attachment_lifecycle_tracking": lambda _self: (
            calls.append(("tracking-start",)) or tracker
        ),
        "_stop_attachment_lifecycle_tracking": lambda _self, actual: calls.append(
            ("tracking-stop", actual)
        ),
    })()
    executor._upload_edit_attachment = lambda actual_field, actual_scope, actual_tracker: (
        calls.append(("upload", actual_field.field_key, actual_scope, actual_tracker))
        or "new.pdf"
    )
    executor._submit_case = lambda *args, **kwargs: (
        calls.append(("save", args[4], args[5], args[6], kwargs))
        or CommonFieldExecutionResult("EDIT-004", field.field_key, "saved_verified_and_retained")
    )
    executor._assert_attachment_persisted = lambda actual_field, file_name, existing: calls.append(
        ("readback", actual_field.field_key, file_name, existing)
    )

    result = executor._execute_file_case(case, field, scope)

    assert calls == [
        ("baseline", scope, {"upload_attachments": False}),
        ("tracking-start",),
        ("upload", "file:report", scope, tracker),
        ("save", "new.pdf", "new.pdf", "old.pdf", {
            "required_codes": set(), "require_edit_and_detail": True,
            "attachment_lifecycle_tracker": tracker,
        }),
        ("readback", "file:report", "new.pdf", ["old.pdf"]),
        ("tracking-stop", tracker),
    ]
    assert result.outcome == "saved_verified_and_retained"
    assert result.observed == "uploaded=new.pdf; existing=old.pdf"


def test_edit_attachment_case_fails_when_reopened_form_lacks_unique_file():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    scope = object()
    field = DiscoveredCommonField(
        "file:report", "项目附件", "file", "file", "#report-file",
        FieldConstraints(),
    )
    case = BoundCommonCase(
        case_id="EDIT-004",
        field_key=field.field_key,
        field_label=field.label,
        field_type="file",
        selector=field.selector,
        scenario="修改附件正确性检查",
        input_value="重复上传附件",
        expected_type="accepted",
        expected_value="附件修改后保存成功并回显",
        priority="P1",
    )
    events = []
    executor._fill_valid_baseline = lambda *_args, **_kwargs: {}
    executor._attachment_names = lambda *_args: []
    tracker = object()
    executor.driver = type("Driver", (), {
        "_start_attachment_lifecycle_tracking": lambda _self: tracker,
        "_stop_attachment_lifecycle_tracking": lambda _self, _tracker: events.append(
            "tracking-stop"
        ),
    })()
    executor._upload_edit_attachment = lambda *_args: "EDIT-004_unique.pdf"
    executor._submit_case = lambda *_args, **_kwargs: (
        events.append("save")
        or CommonFieldExecutionResult(
            case.case_id, field.field_key, "saved_verified_and_retained"
        )
    )
    executor._assert_attachment_persisted = lambda *_args: (
        events.append("attachment-readback")
        or (_ for _ in ()).throw(
            AssertionError("保存后编辑页未回显替换附件")
        )
    )

    with pytest.raises(AssertionError, match="未回显替换附件"):
        executor._execute_file_case(case, field, scope)

    assert events == ["save", "attachment-readback", "tracking-stop"]


def test_submit_attachment_waits_for_lifecycle_before_saved_record_readback():
    events = []

    class Save:
        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

    class Page:
        def on(self, _event, _callback):
            pass

        def remove_listener(self, _event, _callback):
            pass

    class Scope:
        @staticmethod
        def element_handle(**_kwargs):
            return object()

    class Driver:
        @staticmethod
        def _save_button(_scope):
            return Save()

        @staticmethod
        def _collect_record_identity_markers(_submitted, **_kwargs):
            return ()

        @staticmethod
        def _wait_for_attachment_lifecycle(tracker, *, phase):
            events.append(("lifecycle", tracker, phase))

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = Driver()
    executor.page = Page()
    executor._click_case_save_with_business_repairs = lambda *_args, **_kwargs: (
        object(), False, ""
    )
    executor._verify_saved_record = lambda *_args, **_kwargs: events.append("readback")
    tracker = object()
    case = _case()

    result = executor._submit_case(
        case,
        Scope(),
        DiscoveredCommonField(
            "file:report", "项目附件", "file", "file", "#report-file",
            FieldConstraints(),
        ),
        {},
        "new.pdf",
        "new.pdf",
        "old.pdf",
        required_codes=set(),
        attachment_lifecycle_tracker=tracker,
    )

    assert result.outcome == "saved_verified_and_retained"
    assert events == [
        ("lifecycle", tracker, "EDIT-004 附件保存"),
        "readback",
    ]


def test_attachment_readback_accepts_server_renamed_new_file():
    assert CommonFieldExecutor._attachment_readback_matches(
        "EDIT-004_unique.jpg",
        ["old.jpg"],
        ["old.jpg", "1735609066303.jpg"],
    )


def test_attachment_readback_rejects_only_preexisting_same_suffix_file():
    assert not CommonFieldExecutor._attachment_readback_matches(
        "EDIT-004_unique.jpg",
        ["old.jpg"],
        ["old.jpg"],
    )


def test_edit_attachment_temp_cleanup_defers_windows_file_lock(monkeypatch):
    class LockedTemporary:
        def __init__(self):
            self.attempts = 0
            self.locked = True

        def unlink(self, missing_ok=False):
            assert missing_ok is True
            self.attempts += 1
            if self.locked:
                raise PermissionError(32, "file is still in use")

    temporary = LockedTemporary()
    deferred = []
    monkeypatch.setattr(executor_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        executor_module.atexit,
        "register",
        lambda callback, path: deferred.append((callback, path)),
    )

    CommonFieldExecutor._cleanup_temporary_upload(temporary)

    assert temporary.attempts == 5
    assert len(deferred) == 1
    callback, path = deferred[0]
    temporary.locked = False
    assert callback(path) is True


def test_choice_control_selector_prefers_visible_radio_component_roots():
    selector = CommonFieldExecutor._choice_control_selector("isGmoDecision", "radio")

    assert '[prop="isGmoDecision"] .el-radio-group' in selector
    assert '[prop="isGmoDecision"] .el-radio' in selector
    assert '[prop="isGmoDecision"] input[type="radio"]' in selector


def test_discover_merges_baseline_branch_fields(tmp_path):
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    scope = object()
    manifest = tmp_path / "fields.json"
    executor.open_fresh_add_form = lambda: scope
    executor.close_form = lambda: None
    executor.driver = type(
        "Driver", (), {"_prepare_implicit_required_nested_baselines": lambda self, _scope: {}}
    )()
    executor._wait_for_fields_stable = lambda _scope: [
        DomField("progressType", "项目进度", "select", "#progress", required=True)
    ]
    executor._discover_baseline_branch_fields = lambda _scope, _fields, _definitions: [
        DiscoveredCommonField(
            "progressDate", "开工时间", "date", "date", "#date", FieldConstraints()
        )
    ]
    executor._discover_form_commands = lambda _scope: []

    fields = executor.discover(manifest)

    assert [field.field_key for field in fields] == ["progressType", "progressDate"]
    assert "progressDate" in manifest.read_text(encoding="utf-8")


def test_baseline_branch_discovery_reveals_conditional_fields():
    class _SelectLocator:
        def __init__(self, events):
            self.events = events

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            self.events.append("open-select")

        def get_attribute(self, _name):
            return ""

    class _Option:
        def __init__(self, events, selected, label):
            self.events = events
            self.selected = selected
            self.label = label

        def is_visible(self):
            return True

        def inner_text(self, *args, **kwargs):
            return self.label

        def click(self, **_kwargs):
            self.events.append(("select-option", self.label))
            self.selected["value"] = self.label

    class _OptionList:
        def __init__(self, events, selected, labels):
            self.events = events
            self.selected = selected
            self.labels = labels

        def evaluate_all(self, _script):
            return list(self.labels)

        def count(self):
            return len(self.labels)

        def nth(self, index):
            return _Option(self.events, self.selected, self.labels[index])

    events = []
    selected = {"value": ""}
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page(events)
    executor.page.keyboard = type("Keyboard", (), {"press": lambda _self, key: events.append(("key", key))})()
    executor.driver = type(
        "Driver",
        (),
        {
            "_dom_field_has_value": lambda self, _dom, root=None: False,
        },
    )()
    branch_dom = {
        "已取得批复": [
            DomField("progressType", "项目进度", "select", "#progress", required=True),
            DomField("approvalDate", "批复时间", "date", "#approval-date", required=True),
        ],
        "已付款": [
            DomField("progressType", "项目进度", "select", "#progress", required=True),
            DomField(
                "paymentAmountTotal",
                "截至当前节点累计付款金额（万元）",
                "text",
                "#amount",
                required=True,
            ),
        ],
    }
    executor._choice_locator_by_field_label = lambda _field, _scope: _SelectLocator(events)
    executor._dom_for_discovered_field = lambda _field, _scope: DomField(
        "progressType", "项目进度", "select", "#progress", required=True
    )
    executor._owned_select_options = lambda _locator: _OptionList(
        events, selected, ["已取得批复", "已付款"]
    )
    executor._wait_for_fields_stable = lambda _scope, **_kwargs: branch_dom[
        selected["value"]
    ]

    fields = executor._discover_baseline_branch_fields(
        object(),
        [
            DiscoveredCommonField(
                "progressType",
                "项目进度",
                "select",
                "select",
                "#progress",
                FieldConstraints(required=True),
            )
        ],
        (),
    )

    assert ("select-option", "已取得批复") in events
    assert ("select-option", "已付款") in events
    assert {
        (field.field_key, field.branch_conditions)
        for field in fields
    } == {
        ("approvalDate", (("progressType", "已取得批复"),)),
        ("paymentAmountTotal", (("progressType", "已付款"),)),
    }
    assert next(
        field for field in fields if field.field_key == "paymentAmountTotal"
    ).field_type == "amount"


def _executor_for_save_result(monkeypatch, expected_type="accepted"):
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    locator = _Locator()
    field = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints()
    )
    driver = type("Driver", (), {})()
    driver._fill_dialog = lambda only_codes=None: {"otherRequired": "baseline"}
    driver._upload_default_attachments = lambda _scope: None
    driver._fill_failures = []
    driver.check_field_completion = lambda submitted, failures: type(
        "Report", (), {"message": lambda self: "ok"}
    )()
    driver._field_report_ok = lambda report: True
    executor.driver = driver
    executor.open_fresh_add_form = lambda: object()
    executor.close_calls = 0
    executor.close_form = lambda: setattr(
        executor, "close_calls", executor.close_calls + 1
    )
    executor._current_field = lambda _case, _scope: field
    executor._locator = lambda _field: locator
    executor._replace_value = lambda _field, value: setattr(locator, "value", value)
    executor.submissions = []
    executor._submit_case = lambda *args: (
        executor.submissions.append(args),
        type("Result", (), {
            "case_id": args[0].case_id,
            "field_key": args[0].field_key,
            "outcome": "saved_and_verified",
            "observed": str(args[5]),
        })(),
    )[1]
    return executor, _case(expected_type)


def test_form_session_reuses_one_form_for_consecutive_recoverable_field_errors():
    executor, events, scopes = _executor_for_form_session()
    used_scopes = []
    recovered_fields = []

    for field_key in ("projName", "shortIntro"):
        result = CommonFieldExecutionResult(
            "ADD-OVER", field_key, "save_blocked", "提示长度错误"
        )

        def operation(scope, *, result=result, field_key=field_key):
            used_scopes.append(scope)
            return (result,), lambda: recovered_fields.append(field_key)

        executor._run_in_form_session(operation)

    assert [event for event in events if event[0] == "open"] == [
        ("open", "form-1")
    ]
    assert used_scopes == [scopes[0], scopes[0]]
    assert recovered_fields == ["projName", "shortIntro"]

    executor.close_form_session()


def test_recoverable_form_checks_reuse_one_scope_and_return_operation_results():
    executor, events, scopes = _executor_for_form_session()
    used_scopes = []

    def check_name(scope):
        used_scopes.append(scope)
        return "name-checked"

    def check_description(scope):
        used_scopes.append(scope)
        return {"description": "checked"}

    first = executor.run_recoverable_form_check(
        "PERSONAL-NAME", "projName", check_name
    )
    second = executor.run_recoverable_form_check(
        "PERSONAL-DESCRIPTION", "shortIntro", check_description
    )

    assert first == "name-checked"
    assert second == {"description": "checked"}
    assert [event for event in events if event[0] == "open"] == [
        ("open", "form-1")
    ]
    assert used_scopes == [scopes[0], scopes[0]]

    executor.close_form_session()


def test_discovery_prepares_implicit_nested_baseline_before_field_snapshot(tmp_path):
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    events = []
    scope = object()

    class Driver:
        def _prepare_implicit_required_nested_baselines(self, current_scope):
            events.append(("prepare-nested", current_scope))
            return {"financeSources.*.amount": "100"}

    executor.driver = Driver()
    executor.open_fresh_add_form = lambda: scope
    executor.close_form = lambda: events.append(("close", scope))

    def stable_fields(current_scope):
        assert events == [("prepare-nested", scope)]
        assert current_scope is scope
        return [
            DomField(
                "financeSources.*.amount",
                "预算金额（万元）",
                "number",
                "#amount",
            )
        ]

    executor._wait_for_fields_stable = stable_fields
    executor._discover_form_commands = lambda _scope: []

    fields = executor.discover(tmp_path / "fields.json")

    assert [(field.field_key, field.field_type) for field in fields] == [
        ("financeSources.*.amount", "amount"),
    ]
    assert events[-1] == ("close", scope)


def test_page_required_transaction_prepares_nested_rows_before_scanning():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    events = []
    scope = object()

    class Driver:
        def _prepare_implicit_required_nested_baselines(self, current_scope):
            events.append(("prepare-nested", current_scope))
            return {"financeSources.*.amount": "100"}

    class Session:
        def acquire(self):
            events.append(("acquire", scope))
            return scope

        def finish(self, results, *, recover=None):
            events.append(("finish", tuple(result.field_key for result in results)))

        def invalidate(self, *, close):
            events.append(("invalidate", close))

    executor.page = _Page()
    executor.driver = Driver()
    executor._session = lambda: Session()
    executor._capture_failure_once = lambda _message: events.append(("capture", _message))

    def stable_fields(current_scope):
        assert events[:2] == [("acquire", scope), ("prepare-nested", scope)]
        assert current_scope is scope
        events.append(("scan", current_scope))
        return [
            DomField(
                "financeSources.*.sourceFrom",
                "资金来源",
                "select",
                "#source",
                required=True,
            ),
            DomField(
                "financeSources.*.amount",
                "预算金额",
                "number",
                "#amount",
                required=True,
            ),
        ]

    executor._wait_for_fields_stable = stable_fields
    executor._required_control_has_value = lambda _field: False
    executor._submit_case = lambda case, *_args, **_kwargs: CommonFieldExecutionResult(
        case.case_id, case.field_key, "save_blocked", "required"
    )
    executor._wait_for_required_error_snapshot = lambda fields: {
        field_key: "required" for field_key in fields
    }
    executor._required_message_is_correct = lambda _field, _message: True
    executor._log_result = lambda case, result: events.append(
        ("log", case.field_key, result.outcome)
    )

    cases = (
        BoundCommonCase(
            "ADD-REQ-1",
            "financeSources.*.sourceFrom",
            "资金来源",
            "required",
            "#source",
            "required",
            "",
            "field_error",
            "",
            "P1",
            source_row=1,
            scenario_code=REQUIRED_ERRORS_TRIGGER,
        ),
        BoundCommonCase(
            "ADD-REQ-2",
            "financeSources.*.sourceFrom",
            "资金来源",
            "required",
            "#source",
            "required",
            "",
            "field_error",
            "",
            "P1",
            source_row=2,
            scenario_code=REQUIRED_ERRORS_TRIGGER,
        ),
        BoundCommonCase(
            "ADD-REQ-3",
            "financeSources.*.amount",
            "预算金额",
            "required",
            "#amount",
            "required",
            "",
            "field_error",
            "",
            "P1",
            source_row=3,
            scenario_code=REQUIRED_ERRORS_TRIGGER,
        ),
    )

    results = executor._execute_page_required_transaction(
        BoundCommonTransaction("TX-REQ", cases)
    )

    assert [result.field_key for result in results] == [
        "financeSources.*.sourceFrom",
        "financeSources.*.sourceFrom",
        "financeSources.*.amount",
    ]
    assert events.count(("scan", scope)) == 2
    assert ("finish", tuple(result.field_key for result in results)) in events
    assert not any(event[0] == "capture" for event in events)


def test_page_required_transaction_discards_reused_linkage_branch_before_scanning():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    events = []
    scope = object()

    class Driver:
        def _prepare_implicit_required_nested_baselines(self, current_scope):
            events.append(("prepare", current_scope))
            return {}

    class Session:
        def __init__(self):
            self.active = object()

        def acquire(self):
            events.append(("acquire", scope))
            self.active = object()
            return scope

        def finish(self, results, *, recover=None):
            events.append(("finish", tuple(result.field_key for result in results)))

        def invalidate(self, *, close):
            events.append(("invalidate", close))
            self.active = None

    session = Session()
    executor.page = _Page()
    executor.driver = Driver()
    executor._session = lambda: session
    executor._capture_failure_once = lambda _message: events.append(("capture", _message))
    executor._wait_for_fields_stable = lambda current_scope: (
        events.append(("scan", current_scope))
        or [
            DomField(
                "progressType",
                "项目进度",
                "select",
                "#progress",
                required=True,
            )
        ]
    )
    executor._required_control_has_value = lambda _field: False
    executor._submit_case = lambda case, *_args, **_kwargs: CommonFieldExecutionResult(
        case.case_id, case.field_key, "save_blocked", "required"
    )
    executor._wait_for_required_error_snapshot = lambda fields: {
        field_key: "请选择" for field_key in fields
    }
    executor._required_message_is_correct = lambda _field, _message: True
    executor._log_result = lambda case, result: events.append(
        ("log", case.field_key, result.outcome)
    )
    case = BoundCommonCase(
        "ADD-REQ-1",
        "progressType",
        "项目进度",
        "required",
        "#progress",
        "空值校验",
        "",
        "field_error",
        "",
        "P1",
        source_row=1,
        scenario_code=REQUIRED_ERRORS_TRIGGER,
    )

    results = executor._execute_page_required_transaction(
        BoundCommonTransaction("TX-REQ", (case,))
    )

    assert results[0].outcome == "save_blocked"
    assert events[:4] == [
        ("invalidate", True),
        ("acquire", scope),
        ("prepare", scope),
        ("scan", scope),
    ]


def test_choice_frontend_assertions_do_not_terminate_form_session():
    results = [
        CommonFieldExecutionResult(
            "ADD-056", "projClassify", "choice_options_verified", "A; B"
        ),
        CommonFieldExecutionResult(
            "ADD-057", "projClassify", "choice_single_selection_verified", "B"
        ),
    ]

    assert not CommonFieldFormSession._has_terminal_effect(results)


def test_unique_record_identity_value_appends_per_save_token_and_respects_max_length():
    value = CommonFieldExecutor._unique_record_identity_value(
        "UI自动化_20260806224425_1", "S003"
    )

    assert value == "UI自动化_20260806224425_1_S003"
    assert CommonFieldExecutor._unique_record_identity_value(value, "S003") == value
    assert (
        CommonFieldExecutor._unique_record_identity_value(
            "UI自动化_1234567890", "S004", maximum=16
        )
        == "UI自动化_12345_S004"
    )


def test_record_identity_candidates_fall_back_only_to_automation_owned_text():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver", (), {"_is_record_identity_field": staticmethod(lambda _field: False)}
    )()
    fields = [
        DomField("riskSummary", "风险概况", "text", "#summary"),
        DomField("riskReason", "发生原因", "textarea", "#reason"),
    ]

    assert executor._record_identity_candidate_fields(fields, {
        "riskSummary": "UI自动化_风险_S1786300000123001",
        "riskReason": "普通业务描述",
    }) == [fields[0]]


def test_record_identity_candidates_prefer_real_identity_field():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver", (), {
            "_is_record_identity_field": staticmethod(
                lambda field: field.field_code == "projName"
            ),
        }
    )()
    fields = [
        DomField("projName", "项目名称", "text", "#name"),
        DomField("riskSummary", "风险概况", "text", "#summary"),
    ]

    assert executor._record_identity_candidate_fields(fields, {
        "projName": "项目名称",
        "riskSummary": "UI自动化_风险_S1786300000123001",
    }) == [fields[0]]


def test_edit_value_rule_replaces_only_the_last_character_with_nine():
    case = BoundCommonCase(
        case_id="EDIT-002",
        field_key="shortIntro",
        field_label="项目基本情况",
        field_type="textarea",
        selector="#intro",
        scenario="修改值正确性检查",
        input_value="__REPLACE_LAST_WITH_9__",
        expected_type="accepted",
        expected_value="详情页面字段值和编辑页面一致",
        priority="P0",
    )
    field = DiscoveredCommonField(
        "shortIntro", "项目基本情况", "textarea", "textarea", "#intro",
        FieldConstraints(max_length=2000),
    )

    assert CommonFieldExecutor._case_input_value(case, field, "原始内容8") == "原始内容9"
    assert CommonFieldExecutor._case_input_value(case, field, "") == "9"


def test_edit_select_case_changes_to_a_different_option_and_submits():
    selected = iter([["新建"], ["续建"]])
    submitted_calls = []

    class Option:
        def click(self, **_kwargs):
            return None

    class Options:
        labels = ["新建", "续建"]

        def __init__(self):
            self.items = [Option(), Option()]

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Item:
        def count(self):
            return 1

        def locator(self, _selector):
            return object()

    class Control:
        def locator(self, selector):
            assert "ancestor" in selector
            return Item()

        def click(self, **_kwargs):
            return None

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor._apply_case_branch_conditions = lambda *_args: None
    executor._fill_valid_baseline = lambda _scope: {"projClassify": "新建"}
    executor._choice_locator_for_current_field = lambda *_args: Control()
    executor._owned_select_options = lambda _locator: Options()
    executor._visible_texts = lambda options: list(options.labels)
    executor._unique_visible_texts = lambda _locator: next(selected)
    executor._refill_required_baseline_after_target_mutation = lambda *_args, **_kwargs: None
    executor._submit_case = lambda *args, **kwargs: (
        submitted_calls.append((args, kwargs))
        or CommonFieldExecutionResult("EDIT-003", "projClassify", "saved_verified_and_retained")
    )
    case = BoundCommonCase(
        "EDIT-003", "projClassify", "项目类型", "select", "#type",
        "修改选择框码值正确性检查", "随意变更码值", "accepted",
        "保存成功；查看详情，码值修改正确", "P0",
    )
    field = DiscoveredCommonField(
        "projClassify", "项目类型", "select", "select", "#type",
        FieldConstraints(required=True),
    )

    result = executor._execute_choice_case(case, field, object())

    assert result.outcome == "saved_verified_and_retained"
    assert submitted_calls[0][0][3]["projClassify"] == "续建"


def test_failed_request_summary_keeps_recent_business_request_evidence():
    assert CommonFieldExecutor._failed_request_summary([]) == "none"
    assert CommonFieldExecutor._failed_request_summary([
        "POST https://host/save/1: net::ERR_FAILED",
        "POST https://host/save/2: net::ERR_CONNECTION_RESET",
        "POST https://host/save/3: net::ERR_TIMED_OUT",
        "POST https://host/save/4: net::ERR_ABORTED",
    ]) == (
        "POST https://host/save/2: net::ERR_CONNECTION_RESET | "
        "POST https://host/save/3: net::ERR_TIMED_OUT | "
        "POST https://host/save/4: net::ERR_ABORTED"
    )


def test_mutation_request_summary_omits_query_and_reports_status():
    response = type("Response", (), {"status": 200})()
    request = type(
        "Request",
        (),
        {
            "method": "POST",
            "url": "https://host/ezgo/fi-service/projProgress/add?token=secret",
            "response": lambda _self: response,
        },
    )()

    assert CommonFieldExecutor._mutation_request_summary([request]) == (
        "POST /ezgo/fi-service/projProgress/add HTTP 200"
    )


def test_safe_content_rejection_requires_specific_message_or_healthy_probe():
    assert CommonFieldExecutor._is_explicit_safe_content_rejection(
        "安全策略：不允许 HTML 脚本内容"
    )
    assert not CommonFieldExecutor._is_explicit_safe_content_rejection("网络连接失败")
    assert not CommonFieldExecutor._is_explicit_safe_content_rejection("系统异常")


def test_safe_content_connection_reset_passes_only_after_harmless_probe():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    scope = object()
    handle = object()
    values = {"name": "AUTO_<script>alert(1)</script>"}
    executor._original_scope_is_visible = lambda actual_scope, actual_handle: (
        actual_scope is scope and actual_handle is handle
    )
    executor._scan_fields = lambda actual_scope: [
        DomField("name", "名称", "text", "#name")
    ]
    executor._replace_value = lambda field, value: values.__setitem__(field.field_key, value)
    executor._locator = lambda field: field.field_key
    executor._input_value = lambda field_key: values[field_key]
    click_calls = []
    response = object()
    executor._click_case_save_with_business_repairs = (
        lambda *args, **kwargs: (
            click_calls.append((args, kwargs)) or (response, False, "")
        )
    )
    executor.driver = type(
        "Driver",
        (),
        {
            "_collect_record_identity_markers": staticmethod(
                lambda submitted, scope=None: (submitted["name"],)
            )
        },
    )()
    verify_calls = []
    executor._verify_saved_record = lambda *args, **kwargs: verify_calls.append(
        (args, kwargs)
    )
    case = BoundCommonCase(
        "ADD-013", "name", "名称", "text", "#name", "HTML/脚本字符",
        "<script>alert(1)</script>", "safe_handling",
        "页面不执行脚本；内容被安全转义或按规则拒绝", "P0",
    )
    current = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints()
    )
    submitted = dict(values)

    result = executor._probe_safe_content_rejection(
        case,
        scope,
        object(),
        [],
        submitted,
        current,
        handle,
        ["POST https://example.test/save: net::ERR_CONNECTION_RESET"],
        required_codes={"name"},
        rendered_text_expectations=None,
        require_edit_and_detail=False,
    )

    assert result.outcome == "safe_content_rejected"
    assert "<script>" not in submitted["name"]
    assert click_calls[0][1]["expected_type"] == "accepted"
    assert len(verify_calls) == 1


def test_safe_content_other_network_failure_is_not_treated_as_rejection():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor._original_scope_is_visible = lambda _scope, _handle: True

    result = executor._probe_safe_content_rejection(
        _case("safe_handling"),
        object(),
        object(),
        [],
        {"name": "<script>alert(1)</script>"},
        DiscoveredCommonField(
            "name", "名称", "text", "text", "#name", FieldConstraints()
        ),
        object(),
        ["POST https://example.test/save: net::ERR_TIMED_OUT"],
        required_codes={"name"},
        rendered_text_expectations=None,
        require_edit_and_detail=False,
    )

    assert result is None


def test_safe_rejection_transaction_evidence_does_not_call_dangerous_input_persisted():
    case = _case("safe_handling")
    saved = CommonFieldExecutionResult(
        case.case_id,
        case.field_key,
        "safe_content_rejected",
        "危险内容被拒绝，无害探测保存成功",
    )

    observed = CommonFieldExecutor._transaction_result_observed(
        saved,
        case,
        "<script>alert(1)</script>",
        "TX-001",
    )

    assert "危险内容被拒绝" in observed
    assert "retained_probe_record=true" in observed
    assert "persisted='<script>" not in observed


def test_record_identity_token_is_unique_across_retained_form_sessions(monkeypatch):
    class Scope:
        def __init__(self):
            self.attributes = {}

        def get_attribute(self, name):
            return self.attributes.get(name, "")

        def evaluate(self, _script, value):
            self.attributes["data-ei-common-record-token"] = value

    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.time.time_ns", lambda: 1_786_300_000_123_000_000
    )
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    first_scope = Scope()
    second_scope = Scope()

    first = executor._record_identity_token(first_scope)
    second = executor._record_identity_token(second_scope)

    assert first == "S1786300000123001"
    assert executor._record_identity_token(first_scope) == first
    assert second == "S1786300000123002"
    assert first != second


def test_fixed_select_options_are_verified_without_submitting():
    class Keyboard:
        def __init__(self):
            self.pressed = []

        def press(self, key):
            self.pressed.append(key)

    class Page(_Page):
        def __init__(self):
            super().__init__()
            self.keyboard = Keyboard()

    class Item:
        def __init__(self, text):
            self.text = text

        def is_visible(self):
            return True

        def inner_text(self):
            return self.text

        def click(self, **_kwargs):
            return None

    class Options:
        def __init__(self, texts):
            self.items = [Item(text) for text in texts]
            self.first = self.items[0]
            self.last = self.items[-1]

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Locator:
        def __init__(self, *, visible=True, count=1):
            self._visible = visible
            self._count = count
            self.first = self

        def count(self):
            return self._count

        def is_visible(self):
            return self._visible

        def click(self, **_kwargs):
            return None

        def locator(self, _selector):
            return Locator(visible=False, count=0)

    class Scope:
        def __init__(self):
            self.control = Locator()

        def locator(self, selector):
            if selector == "#project-type":
                return self.control
            return Locator(visible=False, count=0)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._fill_valid_baseline = lambda _scope: {"projName": "AUTO_001"}
    wrong_locator = Locator()
    executor._runtime_choice_locator = lambda *_args, **_kwargs: wrong_locator
    executor._owned_select_options = (
        lambda locator: Options(["误命中"])
        if locator is wrong_locator
        else Options(["新建", "续建"])
    )
    executor._submit_case = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("fixed option checks must not submit the business form")
    )
    case = BoundCommonCase(
        case_id="ADD-056",
        field_key="projClassify",
        field_label="项目类型",
        field_type="select",
        selector="#project-type",
        scenario="固定码值",
        input_value="",
        expected_type="accepted",
        expected_value="码值、名称和顺序与需求一致",
        priority="P1",
    )
    field = DiscoveredCommonField(
        "projClassify",
        "项目类型",
        "select",
        "select",
        "#project-type",
        FieldConstraints(),
    )

    result = executor._execute_choice_case(case, field, Scope())

    assert result.outcome == "choice_options_verified"
    assert result.observed == "新建; 续建"
    assert executor.page.keyboard.pressed == ["Escape"]


def test_choice_locator_rejects_selector_from_different_field_label():
    class Driver:
        @staticmethod
        def _normalize_label(value):
            text = str(value or "").replace(" ", "").replace("：", "").replace(":", "")
            for prefix in ("请输入", "请选择"):
                if text.startswith(prefix):
                    return text[len(prefix):]
            return text

    class Item:
        def __init__(self, text, control=None):
            self.text = text
            self.control = control

        @staticmethod
        def count():
            return 1

        def inner_text(self):
            return self.text

        def locator(self, selector):
            if selector.startswith(".el-select"):
                return LocatorSet([self.control] if self.control is not None else [])
            return LocatorSet([])

    class Control:
        def __init__(self, label):
            self.label = label
            self.first = self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        def locator(self, selector):
            if selector.startswith("xpath=ancestor::"):
                return Item(self.label)
            return LocatorSet([])

    class LocatorSet:
        def __init__(self, items):
            self.items = items
            self.first = items[0] if items else self

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

        @staticmethod
        def is_visible():
            return False

    class Scope:
        def __init__(self, wrong, correct):
            self.wrong = wrong
            self.correct = correct

        def locator(self, selector):
            if selector == "#stale-project-type":
                return LocatorSet([self.wrong])
            if selector.startswith(".el-form-item"):
                return LocatorSet([
                    Item("项目类型", self.wrong),
                    Item("责任板块", self.correct),
                ])
            if selector.startswith(".el-col"):
                return LocatorSet([])
            return LocatorSet([])

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = Driver()
    wrong = Control("项目类型")
    correct = Control("责任板块")
    field = DiscoveredCommonField(
        "belongSection",
        "请选择责任板块",
        "select",
        "select",
        "#stale-project-type",
        FieldConstraints(),
    )
    case = BoundCommonCase(
        case_id="ADD-055",
        field_key="belongSection",
        field_label="责任板块",
        field_type="select",
        selector="#stale-project-type",
        scenario="固定码值",
        input_value="",
        expected_type="accepted",
        expected_value="",
        priority="P1",
    )

    locator = executor._choice_locator_for_current_field(case, field, Scope(wrong, correct))

    assert locator is correct


def test_choice_target_mutation_refills_other_required_dom_empty_fields():
    class Driver:
        def __init__(self):
            self.project_type_has_value = False
            self.fill_calls = []
            self.upload_calls = []

        def _dom_field_has_value(self, field, *, root=None):
            if field.field_code == "projClassify":
                return self.project_type_has_value
            if field.field_code == "isGmoDecision":
                return False
            if field.field_code == "inveId":
                return False
            return True

        def _fill_dialog(self, only_codes=None):
            self.fill_calls.append(set(only_codes or set()))
            self.project_type_has_value = True
            return {"projClassify": "新建"}

        def _upload_default_attachments(self, scope):
            self.upload_calls.append(scope)

        @staticmethod
        def _prepare_implicit_required_nested_baselines(_scope):
            return {}

    scope = object()
    driver = Driver()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor.driver = driver
    executor._scan_fields = lambda _scope: [
        DomField("projClassify", "项目类型", "select", "#project-type", required=True),
        DomField("isGmoDecision", "是否需总经办决策", "radio", "#decision", required=True),
        DomField("inveId", "实施主体公司", "select", "#company", required=False),
    ]
    submitted = {"projClassify": "旧项目类型", "isGmoDecision": "是", "inveId": ""}

    executor._refill_required_baseline_after_target_mutation(
        scope, submitted, exclude_codes={"inveId"},
    )

    assert driver.fill_calls == [{"projclassify"}]
    assert driver.upload_calls == [scope]
    assert submitted["projClassify"] == "新建"
    assert submitted["inveId"] == ""


def test_command_required_baseline_refills_radio_when_submitted_value_is_stale():
    class Driver:
        def __init__(self):
            self.radio_selected = False
            self.fill_calls = []
            self.upload_calls = []

        def _dom_field_has_value(self, field, *, root=None):
            assert root is scope
            return self.radio_selected if field.field_code == "riskType" else True

        def _fill_dialog(self, only_codes=None):
            self.fill_calls.append(set(only_codes or set()))
            self.radio_selected = True
            return {"riskType": "已注册"}

        def _upload_default_attachments(self, actual_scope):
            self.upload_calls.append(actual_scope)

        @staticmethod
        def _prepare_implicit_required_nested_baselines(_scope):
            return {}

    scope = object()
    driver = Driver()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor.driver = driver
    executor._scan_fields = lambda _scope: [
        DomField("riskType", "风险类型", "radio", "#risk-type", required=True),
    ]
    submitted = {"riskType": "旧的单选语义值"}

    executor._ensure_command_required_baseline(scope, submitted)

    assert driver.fill_calls == [{"risktype"}]
    assert driver.upload_calls == [scope]
    assert submitted["riskType"] == "已注册"


def test_owned_select_options_reads_nested_aria_controls_from_wrapper():
    class Options:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    class InnerControl:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def get_attribute(name):
            return "dropdown-2" if name == "aria-controls" else ""

    class Wrapper:
        @staticmethod
        def get_attribute(_name):
            return ""

        @staticmethod
        def locator(selector):
            assert selector == "[aria-controls]"
            return InnerControl()

    class Page:
        def __init__(self):
            self.selectors = []

        def locator(self, selector):
            self.selectors.append(selector)
            return Options(2 if selector.startswith('[id="dropdown-2"]:visible ') else 0)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    owned = executor._owned_select_options(Wrapper())

    assert owned.count() == 2
    assert executor.page.selectors[0].startswith('[id="dropdown-2"]:visible ')


def test_owned_select_options_reads_ancestor_wrapper_aria_controls_from_input():
    class Options:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    class Empty:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

    class InnerControl:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def get_attribute(name):
            return "dropdown-3" if name == "aria-controls" else ""

    class Owner:
        @staticmethod
        def count():
            return 1

        @staticmethod
        def locator(selector):
            assert selector == "[aria-controls]"
            return InnerControl()

    class Input:
        @staticmethod
        def get_attribute(_name):
            return ""

        @staticmethod
        def locator(selector):
            if selector == "[aria-controls]":
                return Empty()
            assert selector.startswith("xpath=ancestor-or-self::")
            return Owner()

    class Page:
        def __init__(self):
            self.selectors = []

        def locator(self, selector):
            self.selectors.append(selector)
            return Options(3 if selector.startswith('[id="dropdown-3"]:visible ') else 0)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    owned = executor._owned_select_options(Input())

    assert owned.count() == 3
    assert executor.page.selectors[0].startswith('[id="dropdown-3"]:visible ')


def test_owned_select_options_ignores_hidden_owned_popper_before_fallback():
    class Options:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    class Control:
        @staticmethod
        def get_attribute(name):
            return "dropdown-old" if name == "aria-controls" else ""

    class Page:
        def __init__(self):
            self.selectors = []

        def locator(self, selector):
            self.selectors.append(selector)
            if selector.startswith('[id="dropdown-old"]:visible '):
                return Options(0)
            if selector.startswith(".el-popper:visible "):
                return Options(4)
            return Options(0)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    owned = executor._owned_select_options(Control())

    assert owned.count() == 4
    assert executor.page.selectors[0].startswith('[id="dropdown-old"]:visible ')
    assert executor.page.selectors[1].startswith(".el-popper:visible ")


def test_visible_texts_reads_current_locator_nodes_in_one_dom_pass():
    class Locator:
        def evaluate_all(self, _script):
            return ["  新建  ", "", "改扩建"]

        def count(self):
            raise AssertionError("不应逐个 nth 读取可能重绘的候选节点")

    assert CommonFieldExecutor._visible_texts(Locator()) == ["新建", "改扩建"]


def test_recoverable_form_check_exception_closes_scope_and_next_check_reopens():
    executor, events, scopes = _executor_for_form_session()
    used_scopes = []
    original_error = RuntimeError("personalized assertion failed")

    def failing_check(scope):
        used_scopes.append(scope)
        raise original_error

    with pytest.raises(RuntimeError) as raised:
        executor.run_recoverable_form_check(
            "PERSONAL-FAIL", "projName", failing_check
        )

    assert raised.value is original_error

    def next_check(scope):
        used_scopes.append(scope)
        return "next-checked"

    assert executor.run_recoverable_form_check(
        "PERSONAL-NEXT", "shortIntro", next_check
    ) == "next-checked"
    assert [event for event in events if event[0] == "open"] == [
        ("open", "form-1"),
        ("open", "form-2"),
    ]
    assert [event for event in events if event[0] == "close"] == [
        ("close", "form-1")
    ]
    assert used_scopes == scopes

    executor.close_form_session()


def test_recoverable_form_check_pytest_failure_closes_scope_and_next_check_reopens():
    executor, events, scopes = _executor_for_form_session()
    used_scopes = []

    def failing_check(scope):
        used_scopes.append(scope)
        pytest.fail("personalized pytest failure", pytrace=False)

    with pytest.raises(pytest.fail.Exception) as raised:
        executor.run_recoverable_form_check(
            "PERSONAL-PYTEST-FAIL", "projName", failing_check
        )

    assert type(raised.value) is pytest.fail.Exception
    assert str(raised.value) == "personalized pytest failure"

    def next_check(scope):
        used_scopes.append(scope)
        return "next-checked"

    assert executor.run_recoverable_form_check(
        "PERSONAL-AFTER-PYTEST-FAIL", "shortIntro", next_check
    ) == "next-checked"
    assert [event for event in events if event[0] == "open"] == [
        ("open", "form-1"),
        ("open", "form-2"),
    ]
    assert [event for event in events if event[0] == "close"] == [
        ("close", "form-1")
    ]
    assert used_scopes == scopes

    executor.close_form_session()


def test_form_session_acquire_discards_and_reopens_when_reuse_stabilization_fails():
    executor, events, scopes = _executor_for_form_session()
    session = executor._session()
    first_scope = session.acquire()

    def reject_stale_form(scope):
        events.append(("stabilize", scope.name))
        raise AssertionError("form is not stable")

    executor._stabilize_reusable_form = reject_stale_form

    second_scope = session.acquire()

    assert second_scope is scopes[1]
    assert second_scope is not first_scope
    assert [
        event for event in events if event[0] in {"open", "stabilize", "close"}
    ] == [
        ("open", "form-1"),
        ("stabilize", "form-1"),
        ("close", "form-1"),
        ("open", "form-2"),
    ]

    executor.close_form_session()


def test_stabilize_reusable_form_rejects_popper_still_visible_after_escape():
    class Locator:
        def __init__(self, visible):
            self.visible = visible

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

    class Keyboard:
        def __init__(self):
            self.presses = []

        def press(self, key):
            self.presses.append(key)

    class Page:
        def __init__(self):
            self.keyboard = Keyboard()
            self.popper = Locator(True)
            self.confirmation = Locator(False)

        def locator(self, selector):
            if "el-popper" in selector:
                return self.popper
            return self.confirmation

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    stable_waits = []
    executor._wait_for_fields_stable = lambda *_args, **_kwargs: stable_waits.append(
        "waited"
    )

    with pytest.raises(AssertionError):
        executor._stabilize_reusable_form(object())

    assert executor.page.keyboard.presses == ["Escape"]
    assert stable_waits == []


def test_stabilize_reusable_form_rejects_confirmation_appearing_while_waiting():
    class Locator:
        def __init__(self, visible=False):
            self.visible = visible

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

    class Page:
        keyboard = type("Keyboard", (), {"press": lambda _self, _key: None})()

        def __init__(self):
            self.popper = Locator()
            self.confirmation = Locator()

        def locator(self, selector):
            if "el-popper" in selector:
                return self.popper
            return self.confirmation

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    def wait_for_fields(*_args, **_kwargs):
        executor.page.confirmation.visible = True

    executor._wait_for_fields_stable = wait_for_fields

    with pytest.raises(AssertionError):
        executor._stabilize_reusable_form(object())


def test_blocked_field_recovery_restores_value_and_clears_target_error():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    case = _case("field_error")
    field = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints()
    )
    locator = _Locator("invalid")
    state = {"error": True}
    executor._current_field = lambda _case, _scope: field
    executor._locator = lambda _field: locator

    def replace(_field, value):
        locator.value = value
        state["error"] = False

    executor._replace_value = replace
    executor._target_form_errors = lambda _field: type(
        "Errors", (), {"count": lambda self: int(state["error"])},
    )()

    executor._restore_target_after_validation(case, object(), "baseline")

    assert locator.value == "baseline"
    assert not state["error"]


def test_form_session_reopens_after_saved_terminal_result():
    executor, events, scopes = _executor_for_form_session()
    used_scopes = []
    saved = CommonFieldExecutionResult(
        "ADD-VALID", "projName", "saved_verified_and_retained", "saved"
    )
    blocked = CommonFieldExecutionResult(
        "ADD-OVER", "shortIntro", "save_blocked", "提示长度错误"
    )

    executor._run_in_form_session(
        lambda scope: (used_scopes.append(scope) or (saved,), None)
    )
    executor._run_in_form_session(
        lambda scope: (used_scopes.append(scope) or (blocked,), lambda: None)
    )

    assert [event for event in events if event[0] == "open"] == [
        ("open", "form-1"),
        ("open", "form-2"),
    ]
    assert used_scopes == scopes

    executor.close_form_session()


def test_form_session_closes_and_reopens_when_recovery_fails():
    executor, events, scopes = _executor_for_form_session()
    used_scopes = []
    blocked = CommonFieldExecutionResult(
        "ADD-OVER", "projName", "save_blocked", "提示长度错误"
    )

    def failed_recovery_operation(scope):
        used_scopes.append(scope)

        def recover():
            raise AssertionError("field could not be restored")

        return (blocked,), recover

    executor._run_in_form_session(failed_recovery_operation)
    executor._run_in_form_session(
        lambda scope: (used_scopes.append(scope) or (blocked,), lambda: None)
    )

    assert [event for event in events if event[0] == "open"] == [
        ("open", "form-1"),
        ("open", "form-2"),
    ]
    assert [event for event in events if event[0] == "close"] == [
        ("close", "form-1")
    ]
    assert used_scopes == scopes

    executor.close_form_session()


def test_shared_executor_rebinds_after_browser_page_recovery():
    old_page = object()
    new_page = object()
    closed = []
    old_session = type("Session", (), {"close": lambda self: closed.append(True)})()
    interactor = type("Interactor", (), {"page": old_page})()
    driver = type(
        "Driver", (), {
            "page": old_page,
            "interactor": interactor,
            "_common_form_scope": object(),
        },
    )()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = old_page
    executor.driver = driver
    executor._form_session = old_session

    executor.bind_page(new_page)

    assert closed == [True]
    assert executor.page is new_page
    assert driver.page is new_page
    assert interactor.page is new_page
    assert driver._common_form_scope is None
    assert isinstance(executor._form_session, CommonFieldFormSession)


def test_pin_form_scope_returns_unique_stable_locator():
    pinned = type(
        "Pinned", (), {
            "count": lambda self: 1,
            "is_visible": lambda self: True,
        },
    )()
    selectors = []

    class Scope:
        def evaluate(self, _script, marker):
            self.marker = marker

    class Page:
        def locator(self, selector):
            selectors.append(selector)
            return type("Candidates", (), {"first": pinned})()

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    scope = Scope()

    assert executor._pin_form_scope(scope) is pinned
    assert scope.marker in selectors[0]


def test_hidden_pinned_form_cleanup_does_not_close_another_dialog():
    class HiddenScope:
        def count(self):
            return 1

        def is_visible(self):
            return False

    class Page:
        def locator(self, _selector):
            raise AssertionError("must not fall back to another visible dialog")

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._form_scope_to_close = HiddenScope()

    executor.close_form()


def test_field_interactor_uses_active_form_root_instead_of_page_first_match():
    class CurrentControl:
        def __init__(self):
            self.fill_calls = []

        def count(self):
            return 1

        def is_visible(self):
            return True

        def evaluate(self, _script):
            return "input"

        def get_attribute(self, _name):
            return None

        def is_editable(self):
            return True

        def fill(self, value):
            self.fill_calls.append(value)

    current = CurrentControl()

    class Page:
        def locator(self, _selector):
            raise AssertionError("page-wide lookup must not inspect a stale dialog")

    class Root:
        def __init__(self):
            self.selectors = []

        def locator(self, selector):
            self.selectors.append(selector)
            return type("Candidates", (), {"first": current})()

    root = Root()
    interactor = FieldInteractor(Page())
    field = ResolvedField(
        FieldDefinition("name", "名称", "TEXT"),
        DomField("name", "名称", "text", "#name"),
    )

    assert interactor.fill(field, "active-value", root=root) == "active-value"
    assert root.selectors[0] == "#name"
    assert current.fill_calls == ["active-value"]


def test_open_add_form_uses_shared_actionable_add_entry():
    class EmptyDialogs:
        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Page:
        def locator(self, _selector):
            return EmptyDialogs()

    class Add:
        def __init__(self):
            self.clicks = 0

        def click(self):
            self.clicks += 1

    add = Add()
    scope = object()
    driver = type(
        "Driver",
        (),
        {
            "_wait_for_add_button": lambda _self: add,
            "_wait_for_form_ready": lambda _self, actual: None,
        },
    )()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = driver
    executor._wait_for_new_form_scope = lambda _generation: scope

    assert executor.open_add_form(require_new=True) is scope
    assert add.clicks == 1


def test_open_add_form_uses_configured_common_form_action(monkeypatch):
    events = []

    class Action:
        def click(self):
            events.append(("click", "编辑"))

    class Page:
        pass

    scope = object()
    driver = type(
        "Driver",
        (),
        {
            "_wait_for_form_scope": lambda _self: scope,
            "_wait_for_form_ready": lambda _self, actual: events.append(
                ("ready", actual)
            ),
        },
    )()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = driver
    monkeypatch.setenv("EI_COMMON_FORM_ACTION", "编辑")
    monkeypatch.setattr(
        executor_module,
        "visible_action",
        lambda page, action, timeout=15_000: (
            events.append(("visible_action", action, timeout)) or Action()
        ),
    )

    assert executor.open_add_form(require_new=True) is scope
    assert events == [
        ("visible_action", "编辑", 15_000),
        ("click", "编辑"),
        ("ready", scope),
    ]


def test_open_fresh_add_form_returns_to_entry_after_retained_save():
    events = []

    class Page:
        url = "https://example.test/projects/detail/1001"

        def goto(self, url, *, wait_until):
            events.append(("goto", url, wait_until))
            self.url = url

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.entry_url = "https://example.test/projects"
    executor.close_form = lambda: events.append(("close",))
    executor.open_add_form = lambda *, require_new: (
        events.append(("open", require_new)) or "scope"
    )

    assert executor.open_fresh_add_form() == "scope"
    assert events == [
        ("close",),
        ("goto", "https://example.test/projects", "domcontentloaded"),
        ("open", True),
    ]


def test_open_fresh_add_form_prepares_detail_context_instead_of_entry_navigation():
    events = []

    class Page:
        url = "https://example.test/projects/detail/1001"

        def goto(self, url, *, wait_until):
            events.append(("unexpected-goto", url, wait_until))

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.entry_url = "https://example.test/projects"
    executor.prepare_form_context = lambda page: events.append(
        ("prepare", page.url)
    )
    executor.close_form = lambda: events.append(("close",))
    executor.open_add_form = lambda *, require_new: (
        events.append(("open", require_new)) or "scope"
    )

    assert executor.open_fresh_add_form() == "scope"
    assert executor.open_fresh_add_form() == "scope"
    assert events == [
        ("close",),
        ("prepare", "https://example.test/projects/detail/1001"),
        ("open", True),
        ("close",),
        ("prepare", "https://example.test/projects/detail/1001"),
        ("open", True),
    ]


def test_source_field_identity_replaces_element_plus_generated_ids(monkeypatch):
    executor = CommonFieldExecutor(
        _Page(),
        _Strategy(),
        source_fields=[("projName", "项目名称", False)],
    )
    generated = DomField(
        field_code="el-id-123-4",
        label="项目名称",
        kind="text",
        selector="#el-id-123-4",
    )
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.scan_dom_fields",
        lambda _page, _scope=None: [generated],
    )

    mapped = executor._scan_fields()

    assert mapped[0].field_code == "projName"
    assert mapped[0].label == "项目名称"
    assert mapped[0].selector == "#el-id-123-4"


def test_generated_radio_identity_uses_clean_business_label(monkeypatch):
    executor = CommonFieldExecutor(
        _Page(),
        _Strategy(),
        source_fields=[("isGmoDecision", "是否需总经办决策", False)],
    )
    generated = DomField(
        field_code="el-id-437-53",
        label="是否需总经办决策",
        kind="radio",
        selector='[name="el-id-437-53"]',
        required=True,
    )
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.scan_dom_fields",
        lambda _page, _scope=None: [generated],
    )

    mapped = executor._scan_fields()

    assert mapped[0].field_code == "isGmoDecision"
    assert mapped[0].label == "是否需总经办决策"
    assert mapped[0].kind == "radio"


def test_scan_fields_rejects_source_order_for_mismatched_generated_identity(monkeypatch):
    executor = CommonFieldExecutor(
        _Page(),
        _Strategy(),
        source_fields=[("isGmoDecision", "是否需总经办决策", False)],
    )
    generated_textarea = DomField(
        field_code="el-id-6681-62",
        label="请输入项目基本情况",
        kind="textarea",
        selector="#el-id-6681-62",
    )
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.scan_dom_fields",
        lambda _page, _scope=None: [generated_textarea],
    )

    mapped = executor._scan_fields()

    assert mapped[0].field_code == "el-id-6681-62"
    assert mapped[0].label == "请输入项目基本情况"
    assert mapped[0].kind == "textarea"


def test_same_runtime_field_accepts_normalized_prompt_label():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver", (), {"_normalize_label": staticmethod(lambda value: value.removeprefix("请输入"))}
    )()
    item = DomField(
        "el-id-762-57", "请输入项目名称", "text", "#el-id-762-57"
    )
    field = DiscoveredCommonField(
        "projName", "项目名称", "text", "text", "#old", FieldConstraints()
    )

    assert executor._same_runtime_field(item, field)


def test_locator_falls_back_to_original_selector_after_choice_label_changes():
    captured = {}

    class Interactor:
        @staticmethod
        def locate(resolved, root=None):
            captured["dom"] = resolved.dom
            captured["root"] = root
            return "locator"

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver",
        (),
        {
            "_normalize_label": staticmethod(lambda value: value),
            "interactor": Interactor(),
        },
    )()
    executor._scan_fields = lambda _scope=None: []
    field = DiscoveredCommonField(
        "projClassify", "请选择项目类型", "select", "select",
        "#el-id-762-58", FieldConstraints(required=True),
    )

    assert executor._locator(field) == "locator"
    assert captured["dom"].field_code == "projClassify"
    assert captured["dom"].selector == "#el-id-762-58"


def test_required_recovery_accepts_submitted_choice_semantic_value():
    field = DiscoveredCommonField(
        "projClassify", "请选择项目类型", "select", "select",
        "#el-id-762-58", FieldConstraints(required=True),
    )

    assert CommonFieldExecutor._submitted_choice_has_value(
        field, {"projClassify": "新建"}
    )
    assert not CommonFieldExecutor._submitted_choice_has_value(
        field, {"projClassify": ""}
    )


def test_current_field_rejects_same_key_with_wrong_runtime_type():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor._scan_fields = lambda _scope=None: [
        DomField(
            "isGmoDecision",
            "请输入项目基本情况",
            "textarea",
            "#shortIntro",
        )
    ]
    case = BoundCommonCase(
        "ADD-053",
        "isGmoDecision",
        "是否需总经办决策",
        "radio",
        '[name="el-id-2915-53"]',
        "互斥选择",
        "",
        "accepted",
        "",
        "P1",
    )

    with pytest.raises(AssertionError, match="字段身份错配"):
        executor._current_field(case, object())


def test_current_field_uses_stable_prop_radio_group_when_scan_identity_mismatches():
    class _CountLocator:
        def __init__(self, count):
            self._count = count

        def count(self):
            return self._count

    class _Scope:
        def locator(self, selector):
            if '[prop="isGmoDecision"] input[type="radio"]' in selector:
                return _CountLocator(2)
            return _CountLocator(0)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor._scan_fields = lambda _scope=None: [
        DomField(
            "isGmoDecision",
            "请输入项目基本情况",
            "textarea",
            "#shortIntro",
        )
    ]
    case = BoundCommonCase(
        "ADD-053",
        "isGmoDecision",
        "是否需总经办决策",
        "radio",
        '[name="el-id-2915-53"]',
        "互斥选择",
        "",
        "accepted",
        "",
        "P1",
    )

    field = executor._current_field(case, _Scope())

    assert field.field_key == "isGmoDecision"
    assert field.label == "是否需总经办决策"
    assert field.field_type == "radio"
    assert field.kind == "radio"


def test_wait_for_current_file_field_allows_late_edit_form_hydration():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    scans = []

    def scan(_scope):
        scans.append(len(scans) + 1)
        if len(scans) < 3:
            return []
        return [
            DomField(
                "file:风险报告",
                "风险报告",
                "file",
                "#risk-report-file",
            )
        ]

    executor._scan_fields = scan
    case = BoundCommonCase(
        case_id="EDIT-004",
        field_key="file:风险报告",
        field_label="风险报告",
        field_type="file",
        selector="#risk-report-file",
        scenario="修改附件正确性检查",
        input_value="重复上传附件",
        expected_type="accepted",
        expected_value="附件修改后保存成功并回显",
        priority="P1",
    )

    field = executor._wait_for_current_field(case, object(), timeout_ms=500)

    assert scans == [1, 2, 3]
    assert field.field_key == "file:风险报告"
    assert field.kind == "file"


def test_dom_scanner_uses_independent_label_and_never_edits_option_text():
    independent = DOM_FIELD_SCRIPT.index("const independentLabels")
    code_identity = DOM_FIELD_SCRIPT.index("const codeOf")
    label_logic = DOM_FIELD_SCRIPT[independent:code_identity]

    assert ".el-form-item__label" in label_logic
    assert ".purvar_form_item_title" in label_logic
    assert "!node.closest" in label_logic
    assert "Compatibility fallback" not in DOM_FIELD_SCRIPT
    assert "optionLabels" not in DOM_FIELD_SCRIPT
    assert ".split(optionText)" not in DOM_FIELD_SCRIPT


def test_dom_scanner_discovers_hidden_file_input_through_visible_upload_owner():
    assert "const visibleControl = (el)" in DOM_FIELD_SCRIPT
    assert "el.type !== 'file'" in DOM_FIELD_SCRIPT
    assert "el.parentElement?.closest('[class*=\"upload\"]')" in DOM_FIELD_SCRIPT
    assert "visible(upload || el.parentElement)" in DOM_FIELD_SCRIPT
    assert "type === 'file' && code && !code.startsWith('el-id-')" in DOM_FIELD_SCRIPT
    assert "genericFileName" in DOM_FIELD_SCRIPT
    assert "`file:${label}`" in DOM_FIELD_SCRIPT
    assert ".purvar_form_item:has-text(${JSON.stringify(label)})" in DOM_FIELD_SCRIPT
    assert '[field-code="${CSS.escape(code)}"] input[type="file"]' in DOM_FIELD_SCRIPT


def test_field_snapshot_waits_for_late_rendered_required_attachment(monkeypatch):
    name = DomField("name", "名称", "text", "#name", required=True)
    attachment = DomField(
        "requiredFile", "必填附件", "file", "#required-file", required=True
    )
    snapshots = [
        [name], [name], [name, attachment], [name, attachment], [name, attachment]
    ]
    scan_index = [0]
    clock = iter(index / 20 for index in range(100))
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.time.monotonic", lambda: next(clock)
    )

    class Page:
        @staticmethod
        def wait_for_timeout(_milliseconds):
            return None

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    def scan(_scope):
        index = min(scan_index[0], len(snapshots) - 1)
        scan_index[0] += 1
        return snapshots[index]

    executor._scan_fields = scan

    fields = executor._wait_for_fields_stable(
        object(), timeout_ms=1000, stable_ms=200
    )

    assert [field.field_code for field in fields] == ["name", "requiredFile"]


def test_valid_baseline_includes_implicit_required_nested_rows():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    captured = {}

    class Report:
        ok = True

        @staticmethod
        def message():
            return ""

    class Driver:
        _fill_failures = []

        @staticmethod
        def _fill_dialog(only_codes=None):
            assert only_codes is None
            return {"matterName": "AUTO_001"}

        @staticmethod
        def _upload_default_attachments(_scope):
            return 1

        @staticmethod
        def _prepare_implicit_required_nested_baselines(_scope):
            return {"预算及资金来源明细.amount": "1"}

        @staticmethod
        def check_field_completion(submitted, fill_failed):
            captured["submitted"] = dict(submitted)
            captured["fill_failed"] = list(fill_failed)
            return Report()

        @staticmethod
        def _field_report_ok(report):
            return report.ok

    executor.driver = Driver()
    executor._ensure_unique_record_identity = lambda _scope, _submitted: None

    submitted = executor._fill_valid_baseline(object())

    assert submitted == {
        "matterName": "AUTO_001",
        "预算及资金来源明细.amount": "1",
    }
    assert captured["submitted"] == submitted
    assert captured["fill_failed"] == []


def test_valid_baseline_without_optional_upload_still_uploads_required_files():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    required_file = DomField(
        "requiredReport", "必填报告", "file", "#required-report", required=True
    )
    optional_file = DomField(
        "approvalFile", "批复文件", "file", "#approval-file", required=False
    )
    uploaded = []

    class Driver:
        _fill_failures = []
        _optional_fill_failures = []
        data_strategy = type("Strategy", (), {"strict_field_validation": True})()

        @staticmethod
        def _fill_dialog(only_codes=None):
            assert only_codes is None
            return {"progressDesc": "已完成"}

        @staticmethod
        def _upload_default_attachments(_scope):
            raise AssertionError("optional attachments must stay untouched")

        @staticmethod
        def _prepare_implicit_required_nested_baselines(_scope):
            return {}

        @staticmethod
        def check_field_completion(_submitted, _fill_failed):
            return FieldCompletionReport(
                [], [], [], optional_not_filled=["批复文件 (approvalFile)"]
            )

        @staticmethod
        def _field_report_ok(report):
            return report.ok and not (
                report.optional_not_filled or report.optional_fill_failed
            )

        _field_display = staticmethod(
            lambda code, label: f"{label} ({code})" if label != code else label
        )

    executor.driver = Driver()
    executor._wait_for_fields_stable = lambda _scope: [required_file, optional_file]
    executor._required_control_has_value = lambda field: (
        field.field_key in uploaded
    )
    executor._upload_required_attachment = lambda _scope, field: uploaded.append(
        field.field_key
    )
    executor._ensure_unique_record_identity = lambda _scope, _submitted: None

    submitted = executor._fill_valid_baseline(
        object(), upload_attachments=False
    )

    assert submitted == {"progressDesc": "已完成"}
    assert uploaded == ["requiredReport"]


def test_personalized_case_requires_only_business_field_name():
    assert _field_label({"用例ID": "LX-001", "字段/控件": "项目类型"}) == "项目类型"
    assert _field_label({"用例ID": "LX-001", "字段/控件": "项目类型|projType|select"}) == "项目类型"


def test_personalized_field_code_and_control_type_are_discovered_at_runtime():
    field = DiscoveredCommonField(
        "projType", "项目类型", "select", "select", "#projType", FieldConstraints()
    )
    executor = type("Executor", (), {})()
    executor.driver = type("Driver", (), {"source_fields": [("projType", "项目类型", False)]})()
    executor._scan_fields = lambda _scope: [
        DomField("projType", "项目类型", "select", "#projType")
    ]

    resolved = _runtime_field(executor, object(), "项目类型")

    assert resolved.field_key == field.field_key
    assert resolved.field_type == field.field_type


def test_common_executor_reuses_year_interactor_for_replace_and_clear():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    field = DiscoveredCommonField(
        "assetYear", "年度", "year", "year", "#asset-year", FieldConstraints(required=True)
    )
    dom = DomField("assetYear", "年度", "year", "#asset-year", required=True)
    calls = []

    class _YearLocator:
        @staticmethod
        def input_value():
            return "2026"

    class _Interactor:
        @staticmethod
        def locate(resolved):
            calls.append(("locate", resolved.definition.field_code, resolved.dom.kind))
            return _YearLocator()

        @staticmethod
        def fill(resolved, value):
            calls.append(("fill", resolved.definition.field_code, resolved.dom.kind, value))
            return str(value)

        @staticmethod
        def clear(resolved):
            calls.append(("clear", resolved.definition.field_code, resolved.dom.kind))
            return ""

    executor.driver = type("Driver", (), {"interactor": _Interactor()})()
    executor._scan_fields = lambda _scope=None: [dom]

    executor._replace_value(field, 2026)
    executor._clear_required_control(field)

    assert ("fill", "assetYear", "year", 2026) in calls
    assert ("clear", "assetYear", "year") in calls


def test_common_executor_reuses_date_interactor_for_replace():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    field = DiscoveredCommonField(
        "riskOccurredDate", "发生时间", "date", "date", "#risk-date", FieldConstraints()
    )
    dom = DomField("riskOccurredDate", "发生时间", "date", "#risk-date")
    calls = []

    class _Locator:
        @staticmethod
        def input_value():
            return "2026-08-11"

    class _Interactor:
        @staticmethod
        def locate(resolved):
            calls.append(("locate", resolved.definition.field_code, resolved.dom.kind))
            return _Locator()

        @staticmethod
        def fill(resolved, value):
            calls.append(("fill", resolved.definition.field_code, resolved.dom.kind, value))
            return value

    executor.driver = type("Driver", (), {"interactor": _Interactor()})()
    executor._scan_fields = lambda _scope=None: [dom]

    executor._replace_value(field, "2026-08-12")

    assert ("fill", "riskOccurredDate", "date", "2026-08-12") in calls


def test_required_file_is_retried_after_other_required_controls_are_restored():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    text = DiscoveredCommonField("name", "名称", "text", "text", "#name", FieldConstraints(required=True))
    attachment = DiscoveredCommonField(
        "riskReport", "风险报告", "file", "file", "#risk-report", FieldConstraints(required=True)
    )
    case = BoundCommonCase(
        case_id="ADD-002", field_key="riskReport", field_label="风险报告",
        field_type="required", selector="#risk-report", scenario="空值提交",
        input_value="", expected_type="field_error", expected_value="必填",
        priority="P0", source_row=1, scenario_code=REQUIRED_ERRORS_TRIGGER,
    )
    filled = []
    executor.driver = type(
        "Driver",
        (),
        {"_fill_dialog": staticmethod(lambda *, only_codes: filled.append(only_codes) or {"name": "有效名称"})},
    )()
    executor._required_control_has_value = lambda field: field.field_key == "name"
    executor._blur_required_control = lambda _field: None
    executor._target_required_error_text = lambda _field: ""
    executor._submit_case = lambda *_args, **_kwargs: CommonFieldExecutionResult(
        "ADD-002", "riskReport", "save_blocked", "请上传风险报告"
    )

    outcome, observed = executor._isolate_required_file_validation(
        object(), case, attachment, {"name": text, "riskReport": attachment}
    )

    assert filled == [{"name"}]
    assert outcome == "save_blocked"
    assert "全局必填提示" in observed


def test_cancel_dirty_form_can_fall_back_to_date_value():
    field = DiscoveredCommonField(
        "riskOccurredDate", "发生时间", "date", "date", "#risk-date", FieldConstraints()
    )
    dom = DomField("riskOccurredDate", "发生时间", "date", "#risk-date")

    class _Locator:
        value = "2026-08-11"

        def input_value(self):
            return self.value

    locator = _Locator()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor._scan_fields = lambda _scope=None: [dom]
    executor._locator = lambda _field: locator
    executor._replace_value = lambda _field, value: setattr(locator, "value", value)
    executor._fill_valid_baseline = lambda _scope: {}

    submitted = executor._modify_writable_text_field(object(), marker="AUTO_UNSAVED")

    assert submitted == {"riskOccurredDate": "2026-08-12"}


def test_accepted_case_fills_baseline_and_submits(monkeypatch):
    executor, case = _executor_for_save_result(monkeypatch)

    result = executor.execute(case)

    assert result.outcome == "saved_and_verified"
    assert executor.submissions[0][3] == {
        "otherRequired": "baseline", "name": "AUTO_baseline_new value"
    }
    assert executor.close_calls == 1


def test_control_rejected_error_still_submits_effective_value(monkeypatch):
    executor, case = _executor_for_save_result(monkeypatch, "field_error")
    locator = executor._locator(None)
    executor._replace_value = lambda _field, _value: setattr(locator, "value", "truncated")

    result = executor.execute(case)

    assert result.outcome == "saved_and_verified"
    assert executor.submissions[0][3]["name"] == "truncated"
    assert executor.close_calls == 1


def test_execution_error_closes_shared_form_before_next_case(monkeypatch):
    executor, case = _executor_for_save_result(monkeypatch)
    events = []
    executor.page = _Page(events)
    executor.close_form = lambda: events.append(("close", {}))
    executor._replace_value = lambda _field, _value: (_ for _ in ()).throw(
        AssertionError("interaction failed")
    )

    try:
        executor.execute(case)
    except AssertionError as exc:
        assert str(exc) == "interaction failed"
    else:
        raise AssertionError("expected execution failure")

    assert events == [
        ("screenshot", {"full_page": False}),
        ("close", {}),
    ]


def test_failure_evidence_is_not_overwritten_by_a_later_failure():
    events = []
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page(events)

    executor._capture_failure_once("详情回读失败")
    executor.page.url = "https://example.test/projects"
    executor._capture_failure_once("清理后的外层异常")

    assert events == [("screenshot", {"full_page": False})]


def test_form_close_failure_does_not_mask_primary_execution_failure(capsys):
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.close_form = lambda: (_ for _ in ()).throw(
        AssertionError("close failed")
    )

    executor._close_form_after_execution(AssertionError("save failed"))

    assert "close failed" in capsys.readouterr().out


def test_form_close_failure_still_fails_after_successful_execution():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.close_form = lambda: (_ for _ in ()).throw(
        AssertionError("close failed")
    )

    with pytest.raises(AssertionError, match="close failed"):
        executor._close_form_after_execution(None)


def test_unexpected_saved_negative_case_is_retained_before_failure():
    events = []
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page(events)

    with pytest.raises(AssertionError, match="异常值被保存接口接受"):
        executor._fail_saved_case("异常值被保存接口接受")

    assert events == [("screenshot", {"full_page": False})]


def test_transaction_opens_baselines_submits_and_closes_once():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    cases = (
        _transaction_case("SUMMARY", "summary", "text", "合并摘要"),
        _transaction_case("PURPOSE", "purpose", "textarea", "合并目的"),
    )
    fields = {
        case.field_key: DiscoveredCommonField(
            case.field_key,
            case.field_label,
            case.field_type,
            "textarea" if case.field_type == "textarea" else "text",
            case.selector,
            FieldConstraints(),
        )
        for case in cases
    }
    locators = {key: _Locator(f"baseline-{key}") for key in fields}
    calls = {"open": 0, "baseline": 0, "submit": 0, "close": 0}
    submitted_calls = []
    required_code_calls = []

    def open_form():
        calls["open"] += 1
        return object()

    def fill_baseline(_scope, **_kwargs):
        calls["baseline"] += 1
        return {"recordName": "AUTO_001"}

    def submit(
        case,
        scope,
        field,
        submitted,
        requested,
        actual,
        before,
        *,
        required_codes=None,
        rendered_text_expectations=None,
    ):
        calls["submit"] += 1
        submitted_calls.append(dict(submitted))
        required_code_calls.append(set(required_codes or ()))
        return CommonFieldExecutionResult(
            case.case_id, field.field_key, "saved_verified_and_retained", actual
        )

    executor.open_fresh_add_form = open_form
    executor.close_form = lambda: calls.__setitem__("close", calls["close"] + 1)
    executor._fill_valid_baseline = fill_baseline
    executor._current_field = lambda case, _scope: fields[case.field_key]
    executor._locator = lambda field: locators[field.field_key]
    executor._replace_value = lambda field, value: setattr(
        locators[field.field_key], "value", value
    )
    executor._submit_case = submit

    results = executor.execute_transaction(BoundCommonTransaction("TX-001", cases))

    assert calls == {"open": 1, "baseline": 1, "submit": 1, "close": 1}
    assert submitted_calls == [{
        "recordName": "AUTO_001",
        "summary": "合并摘要",
        "purpose": "合并目的",
    }]
    assert required_code_calls == [{"summary", "purpose"}]
    assert [(result.field_key, result.outcome, result.observed) for result in results] == [
        (
            "summary",
            "saved_verified_and_retained",
            "persisted='合并摘要'; transaction=TX-001; retained=true",
        ),
        (
            "purpose",
            "saved_verified_and_retained",
            "persisted='合并目的'; transaction=TX-001; retained=true",
        ),
    ]


def test_probe_transaction_reuses_one_form_and_persists_one_representative_per_field(
    monkeypatch,
):
    class EventPage(_Page):
        def __init__(self):
            super().__init__()
            self.listeners = {}

        def on(self, event, listener):
            self.listeners.setdefault(event, []).append(listener)

        def remove_listener(self, event, listener):
            self.listeners.get(event, []).remove(listener)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = EventPage()
    cases = (
        BoundCommonCase(
            "NAME-LOW", "summary", "摘要", "text", "#summary",
            "长度下边界", "a" * 49, "accepted", "保存成功", "P1",
        ),
        BoundCommonCase(
            "NAME-MAX", "summary", "摘要", "text", "#summary",
            "长度边界", "b" * 50, "accepted", "保存成功", "P0",
        ),
        BoundCommonCase(
            "AMOUNT-INTEGER", "amount", "金额", "amount", "#amount",
            "合法整数", "100", "accepted", "保存成功", "P0",
        ),
    )
    fields = {
        "summary": DiscoveredCommonField(
            "summary", "摘要", "text", "text", "#summary",
            FieldConstraints(max_length=50),
        ),
        "amount": DiscoveredCommonField(
            "amount", "金额", "amount", "number", "#amount",
            FieldConstraints(),
        ),
    }
    locators = {
        "summary": _Locator("baseline-summary"),
        "amount": _Locator("10"),
    }
    calls = {"open": 0, "baseline": 0, "submit": 0, "close": 0}
    replacements = []
    submit_details = {}
    scope = object()

    monkeypatch.setenv("EI_COMMON_PROBE_SETTLE_MS", "0")
    monkeypatch.setenv("EI_COMMON_PROBE_MUTATION_QUIET_MS", "100")

    def open_form():
        calls["open"] += 1
        return scope

    def close_form():
        calls["close"] += 1

    def run_in_form_session(operation):
        current_scope = open_form()
        try:
            results, _recover = operation(current_scope)
            return tuple(results)
        finally:
            close_form()

    def fill_baseline(actual_scope, *, upload_attachments=True):
        assert actual_scope is scope
        assert upload_attachments is False
        calls["baseline"] += 1
        return {"recordName": "AUTO_001"}

    def replace_value(field, value):
        replacements.append((field.field_key, value))
        locators[field.field_key].value = str(value)

    def submit(
        case, actual_scope, field, submitted, requested, actual, before,
        *, required_codes=None, **_kwargs,
    ):
        calls["submit"] += 1
        submit_details.update(
            submitted=dict(submitted),
            required_codes=set(required_codes or ()),
            case=case,
            field=field,
            scope=actual_scope,
        )
        return CommonFieldExecutionResult(
            case.case_id, field.field_key, "saved_verified_and_retained", str(actual)
        )

    executor._run_in_form_session = run_in_form_session
    executor._apply_case_branch_conditions = lambda _case, _scope: False
    executor._fill_valid_baseline = fill_baseline
    executor._current_field = lambda case, _scope: fields[case.field_key]
    executor._locator = lambda field: locators[field.field_key]
    executor._replace_value = replace_value
    executor._visible_error_text = lambda _scope, _field: ""
    executor._submit_case = submit
    executor._log_result = lambda _case, _result: None

    results = executor.execute_transaction(
        BoundCommonTransaction("TX-PROBE", cases, "probe_persistence")
    )

    assert calls == {"open": 1, "baseline": 1, "submit": 1, "close": 1}
    assert replacements == [
        ("summary", "a" * 49),
        ("summary", "baseline-summary"),
        ("summary", "b" * 50),
        ("summary", "baseline-summary"),
        ("amount", "100"),
        ("amount", "10"),
        ("summary", "b" * 50),
        ("amount", "100"),
    ]
    assert submit_details["submitted"] == {
        "recordName": "AUTO_001", "summary": "b" * 50, "amount": "100",
    }
    assert submit_details["required_codes"] == {"summary", "amount"}
    assert [result.outcome for result in results] == [
        "form_probe_passed",
        "saved_verified_and_retained",
        "saved_verified_and_retained",
    ]
    assert [result.field_key for result in results] == [
        "summary", "summary", "amount",
    ]


@pytest.mark.parametrize(
    ("accepted_write_number", "expected_phase"),
    [
        (1, "表单探测期间"),
        (2, "代表值写入期间"),
    ],
)
def test_probe_transaction_rejects_blur_triggered_business_mutation_without_query_leak(
    monkeypatch, accepted_write_number, expected_phase,
):
    class Request:
        method = "POST"
        url = "https://example.test/api/project/save?token=secret"
        post_data_json = {}

    class EventPage(_Page):
        def __init__(self):
            super().__init__()
            self.listeners = {}

        def on(self, event, listener):
            self.listeners.setdefault(event, []).append(listener)

        def remove_listener(self, event, listener):
            self.listeners.get(event, []).remove(listener)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = EventPage()
    executor.driver = type(
        "Driver", (), {"_request_payload": staticmethod(lambda request: request.post_data_json)}
    )()
    case = BoundCommonCase(
        "BOUNDARY", "summary", "摘要", "text", "#summary",
        "长度边界", "accepted", "accepted", "保存成功", "P0",
    )
    field = DiscoveredCommonField(
        "summary", "摘要", "text", "text", "#summary", FieldConstraints(),
    )
    locator = _Locator("baseline")
    accepted_writes = 0

    monkeypatch.setenv("EI_COMMON_PROBE_SETTLE_MS", "0")
    monkeypatch.setenv("EI_COMMON_PROBE_MUTATION_QUIET_MS", "100")
    executor._run_in_form_session = lambda operation: tuple(operation(object())[0])
    executor._apply_case_branch_conditions = lambda _case, _scope: False
    executor._fill_valid_baseline = lambda _scope, **_kwargs: {}
    executor._current_field = lambda _case, _scope: field
    executor._locator = lambda _field: locator
    executor._visible_error_text = lambda _scope, _field: ""

    def replace_value(_field, value):
        nonlocal accepted_writes
        locator.value = str(value)
        if value == "accepted":
            accepted_writes += 1
        if value == "accepted" and accepted_writes == accepted_write_number:
            for listener in executor.page.listeners.get("request", []):
                listener(Request())

    executor._replace_value = replace_value

    with pytest.raises(AssertionError) as exc_info:
        executor.execute_transaction(
            BoundCommonTransaction("TX-PROBE", (case,), "probe_persistence")
        )

    message = str(exc_info.value)
    assert expected_phase in message
    assert "POST /api/project/save" in message
    assert "token=secret" not in message


def test_field_error_transaction_submits_distinct_fields_once_when_control_truncates():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    cases = (
        BoundCommonCase(
            case_id="ADD-021",
            field_key="summary",
            field_label="项目基本情况",
            field_type="textarea",
            selector="#summary",
            scenario="超过长度",
            input_value="测" * 101,
            expected_type="field_error",
            expected_value="最多 100 个字符",
            priority="P1",
        ),
        BoundCommonCase(
            case_id="ADD-021",
            field_key="purpose",
            field_label="项目建设目的",
            field_type="textarea",
            selector="#purpose",
            scenario="超过长度",
            input_value="建" * 101,
            expected_type="field_error",
            expected_value="最多 100 个字符",
            priority="P1",
        ),
    )
    fields = {
        case.field_key: DiscoveredCommonField(
            case.field_key,
            case.field_label,
            case.field_type,
            "textarea",
            case.selector,
            FieldConstraints(max_length=100),
        )
        for case in cases
    }
    locators = {key: _Locator("baseline") for key in fields}
    calls = {"open": 0, "baseline": 0, "submit": 0, "close": 0}
    submitted_calls = []
    required_code_calls = []

    def open_form():
        calls["open"] += 1
        return object()

    def fill_baseline(_scope, **_kwargs):
        calls["baseline"] += 1
        return {"recordName": "AUTO_001"}

    def replace_value(field, value):
        locators[field.field_key].value = str(value)[:100]

    def submit(
        case,
        scope,
        field,
        submitted,
        requested,
        actual,
        before,
        *,
        required_codes=None,
        rendered_text_expectations=None,
    ):
        calls["submit"] += 1
        submitted_calls.append(dict(submitted))
        required_code_calls.append(set(required_codes or ()))
        return CommonFieldExecutionResult(
            case.case_id,
            field.field_key,
            "truncated_saved_verified_and_retained",
            "ok",
        )

    executor.open_fresh_add_form = open_form
    executor.close_form = lambda: calls.__setitem__("close", calls["close"] + 1)
    executor._fill_valid_baseline = fill_baseline
    executor._current_field = lambda case, _scope: fields[case.field_key]
    executor._locator = lambda field: locators[field.field_key]
    executor._replace_value = replace_value
    executor._submit_case = submit

    results = executor.execute_transaction(BoundCommonTransaction("TX-001", cases))

    assert calls == {"open": 1, "baseline": 1, "submit": 1, "close": 1}
    assert submitted_calls == [{
        "recordName": "AUTO_001",
        "summary": "测" * 100,
        "purpose": "建" * 100,
    }]
    assert required_code_calls == [{"summary", "purpose"}]
    assert [result.field_key for result in results] == ["summary", "purpose"]
    assert {result.outcome for result in results} == {
        "truncated_saved_verified_and_retained"
    }
    assert all("transaction=TX-001" in result.observed for result in results)


@pytest.mark.parametrize(
    ("field_type", "expected_type", "value", "required"),
    [
        ("textarea", "accepted", "第一行\n第二行", True),
        ("textarea", "accepted", "第一行\n  第二行", True),
        ("textarea", "accepted", "A  B", True),
        ("textarea", "accepted", "普通文本", False),
        ("text", "accepted", "A  B", False),
        ("textarea", "field_error", "第一行\n第二行", False),
    ],
)
def test_rendered_whitespace_check_is_derived_from_runtime_value(
    field_type, expected_type, value, required,
):
    case = BoundCommonCase(
        case_id="CASE",
        field_key="summary",
        field_label="项目基本情况",
        field_type=field_type,
        selector="#summary",
        scenario="任意可复用场景名称",
        input_value=value,
        expected_type=expected_type,
        expected_value="",
        priority="P1",
    )

    assert CommonFieldExecutor._requires_rendered_whitespace_check(case, value) is required


def test_transaction_failure_captures_evidence_and_closes_once():
    events = []
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page(events)
    cases = (
        _transaction_case("SUMMARY", "summary", "text", "合并摘要"),
        _transaction_case("PURPOSE", "purpose", "textarea", "合并目的"),
    )
    fields = {
        case.field_key: DiscoveredCommonField(
            case.field_key,
            case.field_label,
            case.field_type,
            "textarea" if case.field_type == "textarea" else "text",
            case.selector,
            FieldConstraints(),
        )
        for case in cases
    }
    locators = {key: _Locator() for key in fields}
    executor.open_fresh_add_form = lambda: object()
    executor.close_form = lambda: events.append(("close", {}))
    executor._fill_valid_baseline = lambda _scope, **_kwargs: {
        "recordName": "AUTO_001"
    }
    executor._wait_for_command_form_completion = lambda _scope, _handle: None
    executor._current_field = lambda case, _scope: fields[case.field_key]
    executor._locator = lambda field: locators[field.field_key]
    executor._replace_value = lambda _field, _value: (_ for _ in ()).throw(
        AssertionError("transaction interaction failed")
    )

    with pytest.raises(AssertionError, match="transaction interaction failed"):
        executor.execute_transaction(BoundCommonTransaction("TX-001", cases))

    assert events == [
        ("screenshot", {"full_page": False}),
        ("close", {}),
    ]


def test_singleton_transaction_delegates_to_existing_execute():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    case = _transaction_case("NEGATIVE", "summary", "text", "bad")
    expected = CommonFieldExecutionResult(
        case.case_id, case.field_key, "save_blocked", "长度错误"
    )
    calls = []
    executor.execute = lambda current: (calls.append(current), expected)[1]

    results = executor.execute_transaction(
        BoundCommonTransaction("TX-001", (case,))
    )

    assert calls == [case]
    assert results == (expected,)


def test_transaction_report_items_reuse_one_cached_physical_execution():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    cases = (
        _transaction_case("ADD-011", "name", "text", "合法名称"),
        _transaction_case("ADD-023", "summary", "textarea", "合法摘要"),
    )
    transaction = BoundCommonTransaction("TX-001", cases)
    expected = tuple(
        CommonFieldExecutionResult(
            case.case_id, case.field_key, "saved_verified_and_retained", "ok"
        )
        for case in cases
    )
    calls = []
    executor.execute_transaction = lambda current: (
        calls.append(current) or expected
    )
    cache = {}

    first = executor.execute_transaction_once(transaction, cache)
    second = executor.execute_transaction_once(transaction, cache)

    assert first == second == expected
    assert calls == [transaction]


def test_transaction_execution_failure_is_cached_without_second_save():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    case = _transaction_case("ADD-011", "name", "text", "合法名称")
    transaction = BoundCommonTransaction("TX-001", (case,))
    calls = []

    def fail(current):
        calls.append(current)
        raise AssertionError("共享保存失败")

    executor.execute_transaction = fail
    cache = {}

    for _attempt in range(2):
        with pytest.raises(AssertionError, match="共享保存失败"):
            executor.execute_transaction_once(transaction, cache)

    assert calls == [transaction]


def test_probe_transaction_attributes_failure_to_triggering_case_and_blocks_remaining():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (), {
            "on": lambda _self, _event, _listener: None,
            "remove_listener": lambda _self, _event, _listener: None,
            "wait_for_timeout": lambda _self, _milliseconds: None,
        }
    )()
    cases = (
        _transaction_case("ADD-011", "name", "text", "长度边界"),
        _transaction_case("ADD-011", "amount", "amount", "长度边界"),
        _transaction_case("ADD-011", "summary", "textarea", "长度边界"),
    )
    fields = {
        "name": DiscoveredCommonField("name", "名称", "text", "text", "#name", FieldConstraints()),
        "amount": DiscoveredCommonField("amount", "金额", "amount", "number", "#amount", FieldConstraints()),
        "summary": DiscoveredCommonField("summary", "摘要", "textarea", "textarea", "#summary", FieldConstraints()),
    }
    values = {"name": "before", "amount": "10", "summary": "before"}
    executor._run_in_form_session = lambda operation: operation(object())[0]
    executor._apply_case_branch_conditions = lambda _case, _scope: False
    executor._fill_valid_baseline = lambda _scope, **_kwargs: {}
    executor._current_field = lambda case, _scope: fields[case.field_key]
    executor._locator = lambda field: type("Locator", (), {"input_value": lambda _self: values[field.field_key]})()
    executor._input_value = lambda locator: locator.input_value()
    executor._case_input_value = lambda case, _field, _before: f"value-{case.field_key}"
    executor._replace_value = lambda field, value: values.__setitem__(field.field_key, str(value))
    executor._visible_error_text = lambda _scope, field: "超过长度" if field.field_key == "amount" else ""
    executor._log_result = lambda _case, _result: None

    results = executor.execute_transaction(
        BoundCommonTransaction("TX-001", cases, "probe_persistence")
    )

    assert [result.outcome for result in results] == [
        "form_probe_passed", "execution_failed", "blocked_by_transaction_failure",
    ]
    assert "field=amount" in results[1].observed
    assert "root_case=amount-ADD-011" in results[2].observed


def test_submit_case_verifies_saved_record_when_form_remains_visible(monkeypatch):
    class EmptyLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        @staticmethod
        def element_handle():
            return Scope()

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def locator(_selector):
            return EmptyLocator()

    class Response:
        ok = True
        status = 200
        url = "https://example.test/project/save"

        @staticmethod
        def json():
            return {"code": 0, "data": {"id": "1001"}}

    class Page:
        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

        @staticmethod
        def locator(_selector):
            return EmptyLocator()

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    class SaveButton:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        def click(self):
            self.page.listeners["response"](Response())

    class Driver:
        def __init__(self, page):
            self.page = page

        def _save_button(self, _scope):
            return SaveButton(self.page)

        @staticmethod
        def _collect_record_identity_markers(_submitted, scope=None):
            return ("AUTO_001",)

        @staticmethod
        def _find_save_response(responses, _submitted):
            return responses[-1] if responses else None

        @staticmethod
        def _assert_business_success(_body):
            return None

    page = Page()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = page
    executor.driver = Driver(page)
    executor._visible_error_text = lambda _scope, _field: ""
    verify_calls = []
    executor._verify_saved_record = lambda *args, **kwargs: verify_calls.append(
        (args, kwargs)
    )
    field = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints()
    )
    case = _case("accepted")
    monkeypatch.setenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "0")

    result = executor._submit_case(
        case,
        Scope(),
        field,
        {"recordName": "AUTO_001", "name": "合法名称"},
        "合法名称",
        "合法名称",
        "旧值",
    )

    assert result.outcome == "saved_verified_and_retained"
    assert len(verify_calls) == 1


def test_submit_case_accepts_truncated_value_when_retained_form_verifies(monkeypatch):
    class EmptyLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        @staticmethod
        def element_handle():
            return Scope()

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def locator(_selector):
            return EmptyLocator()

    class Response:
        ok = True
        status = 200
        url = "https://example.test/project/save"

        @staticmethod
        def json():
            return {"code": 0, "data": {"id": "1001"}}

    class Page:
        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

        @staticmethod
        def locator(_selector):
            return EmptyLocator()

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    class SaveButton:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        def click(self):
            self.page.listeners["response"](Response())

    class Driver:
        def __init__(self, page):
            self.page = page

        def _save_button(self, _scope):
            return SaveButton(self.page)

        @staticmethod
        def _collect_record_identity_markers(_submitted, scope=None):
            return ("AUTO_001",)

        @staticmethod
        def _find_save_response(responses, _submitted):
            return responses[-1] if responses else None

        @staticmethod
        def _assert_business_success(_body):
            return None

    page = Page()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = page
    executor.driver = Driver(page)
    executor._visible_error_text = lambda _scope, _field: ""
    verify_calls = []
    executor._verify_saved_record = lambda *args, **kwargs: verify_calls.append(
        (args, kwargs)
    )
    field = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints(max_length=100)
    )
    case = _case("field_error")
    monkeypatch.setenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "0")

    result = executor._submit_case(
        case,
        Scope(),
        field,
        {"recordName": "AUTO_001", "name": "测" * 100},
        "测" * 101,
        "测" * 100,
        "旧值",
    )

    assert result.outcome == "truncated_saved_verified_and_retained"
    assert len(verify_calls) == 1


def test_submit_case_repairs_business_duplicate_and_retries(monkeypatch):
    class EmptyLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        @staticmethod
        def element_handle():
            return Scope()

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def locator(_selector):
            return EmptyLocator()

    class Response:
        ok = True
        status = 200
        url = "https://example.test/project/save"

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    class Page:
        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

        @staticmethod
        def locator(_selector):
            return EmptyLocator()

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    class SaveButton:
        def __init__(self, page):
            self.page = page
            self.clicks = 0

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        def click(self):
            self.clicks += 1
            body = (
                {"code": 500, "message": "事项名称已存在，请修改后再保存"}
                if self.clicks == 1
                else {"code": 0, "data": {"id": "1001"}}
            )
            self.page.listeners["response"](Response(body))

    class Driver:
        def __init__(self, page):
            self.save = SaveButton(page)
            self.repair_messages = []

        def _save_button(self, _scope):
            return self.save

        @staticmethod
        def _collect_record_identity_markers(submitted, scope=None):
            return (submitted["matterName"],)

        @staticmethod
        def _find_save_response(responses, _submitted):
            return responses[-1] if responses else None

        @staticmethod
        def _assert_business_success(body):
            if isinstance(body, dict) and body.get("code") not in (0, "0", 200, "200"):
                raise AssertionError(body["message"])

        def _repair_business_validation_message(self, message, submitted, attempt):
            self.repair_messages.append((message, submitted["matterName"], attempt))
            submitted["matterName"] = "AUTO_项目决策_S002"
            return {"matterName": "AUTO_项目决策_S002"}

    page = Page()
    driver = Driver(page)
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = page
    executor.driver = driver
    executor._visible_error_text = lambda _scope, _field: ""
    verify_calls = []
    executor._verify_saved_record = lambda *args, **kwargs: verify_calls.append(
        (args, kwargs)
    )
    field = DiscoveredCommonField(
        "buildPeriodMonth", "建设周期（月）", "number", "number", "#month", FieldConstraints()
    )
    case = BoundCommonCase(
        case_id="ADD-024",
        field_key="buildPeriodMonth",
        field_label="建设周期（月）",
        field_type="number",
        selector="#month",
        scenario="整数输入",
        input_value="123",
        expected_type="accepted",
        expected_value="保存成功",
        priority="P1",
    )
    submitted = {"matterName": "AUTO_项目决策_S001", "buildPeriodMonth": "123"}
    monkeypatch.setenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "0")

    result = executor._submit_case(
        case,
        Scope(),
        field,
        submitted,
        "123",
        "123",
        "",
    )

    assert result.outcome == "saved_verified_and_retained"
    assert driver.save.clicks == 2
    assert submitted["matterName"] == "AUTO_项目决策_S002"
    assert driver.repair_messages == [
        ("事项名称已存在，请修改后再保存", "AUTO_项目决策_S001", 1)
    ]
    assert len(verify_calls) == 1


def test_submit_command_response_matching_accepts_submit_endpoint():
    class Request:
        method = "POST"

        @staticmethod
        def post_data_json():
            return {"matterName": "AUTO_项目决策"}

    class Response:
        request = Request()
        url = "https://host/fi-service/projDecision/submit"

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.driver = type(
        "Driver",
        (),
        {
            "_payload_scalar_values": staticmethod(lambda payload: {"AUTO_项目决策"}),
            "_request_payload": staticmethod(lambda request: request.post_data_json()),
        },
    )()

    matches = executor._matching_business_responses(
        [Response()], {"matterName": "AUTO_项目决策"}
    )

    assert len(matches) == 1
    assert matches[0].url.endswith("/submit")


def test_save_submit_command_readback_only_requires_record_identity_fields():
    required = CommonFieldExecutor._command_readback_required_codes(
        {
            "matterName": "AUTO_事项名称",
            "buildContent": "建设内容",
            "buildScale": "建设规模",
        },
        ("AUTO_事项名称",),
    )

    assert required == {"matterName"}


def test_command_readback_prefers_record_name_over_relationship_marker():
    required = CommonFieldExecutor._command_readback_required_codes(
        {
            "projName": "AUTO_项目",
            "inveId": "company-1",
            "buildContent": "建设内容",
        },
        ("AUTO_项目", "company-1"),
    )

    assert required == {"projName"}


def test_submit_required_validation_blocks_terminal_form_reuse():
    assert CommonFieldFormSession._has_terminal_effect(
        (
            CommonFieldExecutionResult(
                "ADD-072", "__submit_command", "save_blocked", "请输入建设内容"
            ),
        )
    )


def test_edit_submit_required_validation_clears_prefilled_fields_before_submit():
    class EmptyConfirmations:
        @staticmethod
        def count():
            return 0

    class Scope:
        @staticmethod
        def element_handle():
            return object()

    class Page:
        url = "https://example.test/edit/1"

        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

        @staticmethod
        def locator(_selector):
            return EmptyConfirmations()

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    field = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._wait_for_fields_stable = lambda _scope: []
    executor._required_control_has_value = lambda _field: values[_field.field_key] != ""
    executor._clear_required_control = lambda current: values.__setitem__(
        current.field_key, ""
    )
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._wait_for_required_error_snapshot = lambda targets: {
        key: "请输入名称" for key in targets
    }
    executor._original_scope_is_visible = lambda _scope, _handle: True
    executor._business_mutation_requests = lambda _requests, _modified: []
    values = {"name": "已有名称"}
    clicked = []
    button = type("Button", (), {"click": lambda self: clicked.append(True)})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda _fields: [field]
    try:
        result = executor._execute_submit_required_validation(
            case, Scope(), button
        )
    finally:
        executor_module.discover_common_fields = original_discover

    assert values == {"name": ""}
    assert clicked == [True]
    assert result.outcome == "save_blocked"
    assert result.observed == "名称: 请输入名称"


def test_edit_submit_required_validation_rejects_business_mutation_request():
    field = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    request = type(
        "Request", (),
        {"method": "POST", "url": "https://example.test/project/update"},
    )()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (),
        {
            "url": "https://example.test/edit/1",
            "on": lambda self, _event, listener: setattr(self, "listener", listener),
            "remove_listener": lambda self, _event, _listener: None,
            "wait_for_timeout": lambda self, _timeout: None,
        },
    )()
    executor._wait_for_fields_stable = lambda _scope: []
    executor._required_control_has_value = lambda _field: False
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._wait_for_required_error_snapshot = lambda _targets: {"name": "请输入名称"}
    executor._business_mutation_requests = lambda requests, _modified: (
        [request] if requests else []
    )
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    button = type(
        "Button", (),
        {"click": lambda self: executor.page.listener(request)},
    )()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda _fields: [field]
    try:
        with pytest.raises(AssertionError, match="必填校验未阻止业务提交请求"):
            executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover


def test_edit_submit_required_validation_catches_mutation_while_clearing_field():
    field = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    request = type(
        "Request", (),
        {"method": "PUT", "url": "https://example.test/project/update"},
    )()

    class Page:
        url = "https://example.test/edit/1"

        def on(self, _event, listener):
            self.listener = listener

        @staticmethod
        def remove_listener(_event, _listener):
            return None

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    values = {"name": "已有名称"}
    page = Page()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = page
    executor._wait_for_fields_stable = lambda _scope: []
    executor._required_control_has_value = lambda current: bool(
        values[current.field_key]
    )

    def clear_with_auto_save(current):
        values[current.field_key] = ""
        page.listener(request)

    executor._clear_required_control = clear_with_auto_save
    executor._business_mutation_requests = lambda requests, _modified: list(requests)
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    clicked = []
    button = type("Button", (), {"click": lambda self: clicked.append(True)})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda _fields: [field]
    try:
        with pytest.raises(AssertionError, match="清空必填字段时触发了业务提交请求"):
            executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover

    assert clicked == []


def test_edit_submit_required_validation_ignores_conditionally_inactive_field():
    active = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    conditional = DiscoveredCommonField(
        "fundsPlan", "资金筹措方案", "required", "text", "#fundsPlan",
        FieldConstraints(required=True),
    )
    inactive = DiscoveredCommonField(
        "fundsPlan", "资金筹措方案", "text", "text", "#fundsPlan",
        FieldConstraints(required=False),
    )
    scans = iter(([active, conditional], [active, inactive]))
    values = {"name": "已有名称", "fundsPlan": "已有方案"}
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (),
        {
            "url": "https://example.test/edit/1",
            "on": lambda self, _event, listener: setattr(self, "listener", listener),
            "remove_listener": lambda self, _event, _listener: None,
            "wait_for_timeout": lambda self, _timeout: None,
        },
    )()
    executor._wait_for_fields_stable = lambda _scope: next(scans)
    executor._required_control_has_value = lambda field: values[field.field_key] != ""
    executor._clear_required_control = lambda field: values.__setitem__(
        field.field_key, ""
    )
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._wait_for_required_error_snapshot = lambda _targets: {
        "name": "请输入名称",
        "fundsPlan": "",
    }
    executor._business_mutation_requests = lambda _requests, _modified: []
    executor._original_scope_is_visible = lambda _scope, _handle: True
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    button = type("Button", (), {"click": lambda self: None})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda fields: fields
    try:
        result = executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover

    assert values == {"name": "", "fundsPlan": ""}
    assert result.outcome == "save_blocked"
    assert result.observed == "名称: 请输入名称"


def test_edit_submit_required_validation_maps_wildcard_to_inactive_runtime_row():
    active = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    conditional = DiscoveredCommonField(
        "financeSources.*.fundsPlan", "资金筹措方案", "required", "textarea",
        "#fundsPlan", FieldConstraints(required=True),
    )
    inactive = DiscoveredCommonField(
        "financeSources.0.fundsPlan", "资金筹措方案", "textarea", "textarea",
        "#fundsPlan-0", FieldConstraints(required=False),
    )
    scans = iter(([active, conditional], [active, inactive]))
    values = {
        "name": "已有名称",
        "financeSources.*.fundsPlan": "已有方案",
    }
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (),
        {
            "url": "https://example.test/edit/1",
            "on": lambda self, _event, listener: setattr(self, "listener", listener),
            "remove_listener": lambda self, _event, _listener: None,
            "wait_for_timeout": lambda self, _timeout: None,
        },
    )()
    executor._wait_for_fields_stable = lambda _scope: next(scans)
    executor._required_control_has_value = lambda field: bool(
        values[field.field_key]
    )
    executor._clear_required_control = lambda field: values.__setitem__(
        field.field_key, ""
    )
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._wait_for_required_error_snapshot = lambda _targets: {
        "name": "请输入名称"
    }
    executor._business_mutation_requests = lambda _requests, _modified: []
    executor._original_scope_is_visible = lambda _scope, _handle: True
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    button = type("Button", (), {"click": lambda self: None})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda fields: fields
    try:
        result = executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover

    assert result.outcome == "save_blocked"
    assert result.observed == "名称: 请输入名称"


def test_edit_submit_required_validation_rejects_missing_post_submit_field():
    active = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    conditional = DiscoveredCommonField(
        "fundsPlan", "资金筹措方案", "required", "textarea", "#fundsPlan",
        FieldConstraints(required=True),
    )
    scans = iter(([active, conditional], [active]))
    values = {"name": "已有名称", "fundsPlan": "已有方案"}
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (),
        {
            "url": "https://example.test/edit/1",
            "on": lambda self, _event, listener: setattr(self, "listener", listener),
            "remove_listener": lambda self, _event, _listener: None,
            "wait_for_timeout": lambda self, _timeout: None,
        },
    )()
    executor._wait_for_fields_stable = lambda _scope: next(scans)
    executor._required_control_has_value = lambda field: bool(
        values[field.field_key]
    )
    executor._clear_required_control = lambda field: values.__setitem__(
        field.field_key, ""
    )
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._business_mutation_requests = lambda _requests, _modified: []
    executor._original_scope_is_visible = lambda _scope, _handle: True
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    button = type("Button", (), {"click": lambda self: None})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda fields: fields
    try:
        with pytest.raises(
            AssertionError, match="fundsPlan:提交后字段已从运行时扫描中消失"
        ):
            executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover


def test_edit_submit_required_validation_checks_each_active_dynamic_row():
    active = DiscoveredCommonField(
        "name", "名称", "required", "text", "#name",
        FieldConstraints(required=True),
    )
    conditional = DiscoveredCommonField(
        "financeSources.*.fundsPlan", "资金筹措方案", "required", "textarea",
        "#fundsPlan", FieldConstraints(required=True),
    )
    active_row = DiscoveredCommonField(
        "financeSources.0.fundsPlan", "资金筹措方案", "required", "textarea",
        "#fundsPlan-0", FieldConstraints(required=True),
    )
    inactive_row = DiscoveredCommonField(
        "financeSources.1.fundsPlan", "资金筹措方案", "textarea", "textarea",
        "#fundsPlan-1", FieldConstraints(required=False),
    )
    scans = iter(([active, conditional], [active, active_row, inactive_row]))
    values = {
        "name": "已有名称",
        "financeSources.*.fundsPlan": "已有方案",
    }
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (),
        {
            "url": "https://example.test/edit/1",
            "on": lambda self, _event, listener: setattr(self, "listener", listener),
            "remove_listener": lambda self, _event, _listener: None,
            "wait_for_timeout": lambda self, _timeout: None,
        },
    )()
    executor._wait_for_fields_stable = lambda _scope: next(scans)
    executor._required_control_has_value = lambda field: bool(
        values[field.field_key]
    )
    executor._clear_required_control = lambda field: values.__setitem__(
        field.field_key, ""
    )
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._wait_for_required_error_snapshot = lambda _targets: {
        "name": "请输入名称",
        "financeSources.0.fundsPlan": "请输入资金筹措方案",
    }
    executor._business_mutation_requests = lambda _requests, _modified: []
    executor._original_scope_is_visible = lambda _scope, _handle: True
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    button = type("Button", (), {"click": lambda self: None})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda fields: fields
    try:
        result = executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover

    assert result.outcome == "save_blocked"
    assert "请输入名称" in result.observed
    assert "请输入资金筹措方案" in result.observed


def test_edit_submit_required_validation_requires_one_still_active_field():
    conditional = DiscoveredCommonField(
        "fundsPlan", "资金筹措方案", "required", "text", "#fundsPlan",
        FieldConstraints(required=True),
    )
    inactive = DiscoveredCommonField(
        "fundsPlan", "资金筹措方案", "text", "text", "#fundsPlan",
        FieldConstraints(required=False),
    )
    scans = iter(([conditional], [inactive]))
    values = {"fundsPlan": "已有方案"}
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (),
        {
            "url": "https://example.test/edit/1",
            "on": lambda self, _event, listener: setattr(self, "listener", listener),
            "remove_listener": lambda self, _event, _listener: None,
            "wait_for_timeout": lambda self, _timeout: None,
        },
    )()
    executor._wait_for_fields_stable = lambda _scope: next(scans)
    executor._required_control_has_value = lambda field: values[field.field_key] != ""
    executor._clear_required_control = lambda field: values.__setitem__(
        field.field_key, ""
    )
    executor._visible_confirmation_handles = lambda: ()
    executor._confirm_submission_if_present = lambda _handles: False
    executor._wait_for_required_error_snapshot = lambda _targets: {"fundsPlan": ""}
    executor._business_mutation_requests = lambda _requests, _modified: []
    executor._original_scope_is_visible = lambda _scope, _handle: True
    scope = type("Scope", (), {"element_handle": lambda self: object()})()
    button = type("Button", (), {"click": lambda self: None})()
    case = _transaction_case(
        "EDIT-010", "__submit_command", "submit_command", ""
    )

    original_discover = executor_module.discover_common_fields
    executor_module.discover_common_fields = lambda fields: fields
    try:
        with pytest.raises(
            AssertionError,
            match="没有仍处于运行时必填状态的可编辑字段",
        ):
            executor._execute_submit_required_validation(case, scope, button)
    finally:
        executor_module.discover_common_fields = original_discover


def test_verify_saved_record_receives_all_transaction_values_once():
    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)

    page = Page()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = page
    calls = {"verify": [], "delete": []}
    verified = ModuleSmokeResult(
        mode="add_and_detail_verified",
        business_id="1001",
        submitted={"summary": "合并摘要", "purpose": "合并目的"},
        record_markers=("AUTO_001",),
    )

    class Driver:
        def verify_saved_record(
            self,
            responses,
            save_response,
            submitted,
            markers,
            *,
            provision_only=False,
            required_codes=None,
            rendered_text_expectations=None,
            require_edit_and_detail=False,
        ):
            calls["verify"].append(
                (
                    responses,
                    save_response,
                    dict(submitted),
                    markers,
                    provision_only,
                    set(required_codes or ()),
                    dict(rendered_text_expectations or {}),
                    require_edit_and_detail,
                )
            )
            return verified

        def delete_created_record(self, result):
            calls["delete"].append(result)
            return ModuleSmokeResult(mode="add_and_delete_verified")

    executor.driver = Driver()
    responses = [object()]
    save_response = object()
    submitted = {
        "recordName": "AUTO_001",
        "summary": "合并摘要",
        "purpose": "合并目的",
    }

    result = executor._verify_saved_record(
        responses,
        save_response,
        submitted,
        ("AUTO_001",),
        required_codes={"summary", "purpose"},
        rendered_text_expectations={
            "purpose": ("项目建设目的", "第一行\n  第二行"),
        },
    )

    assert result is verified
    assert page.waits == [300]
    assert calls["verify"] == [
        (
            responses,
            save_response,
            submitted,
            ("AUTO_001",),
            False,
            {"summary", "purpose"},
            {"purpose": ("项目建设目的", "第一行\n  第二行")},
            False,
        )
    ]
    assert calls["delete"] == []


def test_add_069_forwards_all_submitted_fields_to_edit_and_detail_readback():
    class Page:
        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 300

    captured = {}

    class Driver:
        @staticmethod
        def verify_saved_record(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return ModuleSmokeResult(mode="add_edit_and_detail_verified")

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = Driver()
    submitted = {
        "projName": "AUTO_项目",
        "projClassify": "新建项目",
        "amount": "100.00",
    }

    result = executor._verify_saved_record(
        [object()],
        object(),
        submitted,
        ("AUTO_项目",),
        required_codes=set(submitted),
        require_edit_and_detail=True,
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert captured["kwargs"] == {
        "required_codes": set(submitted),
        "require_edit_and_detail": True,
    }


def test_attachment_readback_forwards_explicit_empty_field_set():
    class Page:
        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 300

    captured = {}

    class Driver:
        @staticmethod
        def verify_saved_record(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return ModuleSmokeResult(mode="add_edit_and_detail_verified")

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = Driver()

    result = executor._verify_saved_record(
        [object()],
        object(),
        {},
        (),
        required_codes=set(),
        require_edit_and_detail=True,
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert captured["kwargs"] == {
        "required_codes": set(),
        "require_edit_and_detail": True,
    }


def test_edit_action_preserves_current_detail_context_for_saved_record(
    monkeypatch,
):
    class Page:
        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 300

    captured = {}

    class Driver:
        @staticmethod
        def verify_saved_record(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return ModuleSmokeResult(mode="add_edit_and_detail_verified")

    monkeypatch.setenv("EI_COMMON_FORM_ACTION", "编辑")
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = Driver()

    result = executor._verify_saved_record(
        [object()],
        object(),
        {"projName": "AUTO_项目"},
        ("AUTO_项目",),
        required_codes={"projName"},
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert captured["kwargs"] == {
        "required_codes": {"projName"},
        "saved_from_current_detail_edit": True,
    }


def test_terminal_submit_does_not_require_reentering_edit_from_edit_action(
    monkeypatch,
):
    class Page:
        @staticmethod
        def wait_for_timeout(timeout):
            assert timeout == 300

    captured = {}

    class Driver:
        @staticmethod
        def verify_saved_record(*args, **kwargs):
            captured["kwargs"] = kwargs
            return ModuleSmokeResult(mode="add_and_list_verified")

    monkeypatch.setenv("EI_COMMON_FORM_ACTION", "编辑")
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = Driver()

    result = executor._verify_saved_record(
        [object()],
        object(),
        {"projName": "AUTO_项目"},
        ("AUTO_项目",),
        required_codes={"projName"},
        terminal_operation=True,
    )

    assert result.mode == "add_and_list_verified"
    assert captured["kwargs"] == {"required_codes": {"projName"}}


def test_add_069_case_contract_requires_both_edit_and_detail():
    case = BoundCommonCase(
        case_id="ADD-069",
        field_key="__save_command",
        field_label="保存",
        field_type="save_command",
        selector="button:has-text('保存')",
        scenario="输入值正确性检查",
        input_value=None,
        expected_type="accepted",
        expected_value="编辑页面和详情页面的字段值均与新增页面一致",
        priority="P0",
    )

    assert CommonFieldExecutor._requires_edit_and_detail_readback(case)


def test_verify_failure_retains_the_saved_record():
    class Page:
        @staticmethod
        def wait_for_timeout(_timeout):
            pass

        @staticmethod
        def screenshot(**_kwargs):
            return b"verification-failure"

    deleted = []

    class Driver:
        @staticmethod
        def verify_saved_record(*_args, **_kwargs):
            raise AssertionError("详情目标字段不一致")

        @staticmethod
        def delete_created_record(result):
            deleted.append(result)
            return ModuleSmokeResult(mode="add_and_delete_verified")

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = Driver()

    with pytest.raises(AssertionError, match="详情目标字段不一致"):
        executor._verify_saved_record(
            [],
            object(),
            {"recordName": "AUTO_001", "summary": "合并摘要"},
            ("AUTO_001",),
            required_codes={"summary"},
        )

    assert deleted == []


@pytest.mark.parametrize(
    ("field_key", "field_type", "terminal_operation"),
    [
        ("__save_command", "save_command", False),
        ("__submit_command", "submit_command", True),
    ],
)
def test_command_verifies_and_retains_created_record_once(
    field_key, field_type, terminal_operation,
):
    class Button:
        def __init__(self):
            self.clicks = 0

        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            self.clicks += 1

    class EmptyDialog:
        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Scope:
        def __init__(self, button):
            self.button = button

        def locator(self, selector):
            assert selector == "button:visible"
            return self.button

        def is_visible(self):
            return True

    class Page:
        url = "https://example.test/projects"

        def __init__(self):
            self.listeners = []

        def locator(self, _selector):
            return EmptyDialog()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        def wait_for_timeout(self, _timeout):
            pass

    class Response:
        ok = True
        url = "https://example.test/project/save"

        @staticmethod
        def json():
            return {"code": 0}

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_collect_record_identity_markers": lambda _self, _submitted, scope=None: (
                "AUTO_001",
            ),
            "_assert_business_success": lambda _self, _body: None,
        },
    )()
    executor._fill_valid_baseline = lambda _scope: {"recordName": "AUTO_001"}
    executor._wait_for_command_form_completion = lambda _scope, _handle: None
    response = Response()
    executor._matching_business_responses = lambda _responses, _submitted: [response]
    verify_calls = []
    verify_options = []
    executor._verify_saved_record = lambda *args, **kwargs: (
        verify_calls.append(args), verify_options.append(kwargs)
    )
    button = Button()
    case = _transaction_case(
        "COMMAND", field_key, field_type, None
    )

    result = executor._execute_command_case(case, Scope(button))

    assert result.outcome == "command_verified"
    assert button.clicks == 1
    assert len(verify_calls) == 1
    assert verify_calls[0][2] == {"recordName": "AUTO_001"}
    assert verify_calls[0][3] == ("AUTO_001",)
    assert verify_options == [
        {
            "required_codes": {"recordName"},
            "require_edit_and_detail": False,
            "terminal_operation": terminal_operation,
        }
    ]
    assert executor.page.listeners == []


def test_command_save_mutation_uses_a_persistable_marker():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    observed = {}
    executor._modify_writable_text_field = lambda scope, *, marker: (
        observed.update(scope=scope, marker=marker) or {"projectName": "AUTO_SAVE_001"}
    )
    scope = object()

    assert executor._modify_form_for_command_save(scope) == {
        "projectName": "AUTO_SAVE_001"
    }
    assert observed == {"scope": scope, "marker": "AUTO_SAVE"}


def test_save_command_waits_for_response_after_inline_form_hides():
    class Button:
        def __init__(self, scope):
            self.scope = scope

        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        def click(self, **_kwargs):
            self.scope.visible = False

    class EmptyDialog:
        @property
        def last(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        def __init__(self):
            self.visible = True
            self.button = Button(self)

        def locator(self, selector):
            assert selector == "button:visible"
            return self.button

        def is_visible(self):
            return self.visible

    class Response:
        ok = True
        url = "https://example.test/fi-service/projAppInfo/update"

        @staticmethod
        def json():
            return {"code": 0, "data": {"id": "1001"}}

    class Page:
        def __init__(self):
            self.listeners = []
            self.response_sent = False
            self.waits = []

        def locator(self, _selector):
            return EmptyDialog()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)
            if not self.response_sent:
                self.response_sent = True
                for event, listener in tuple(self.listeners):
                    if event == "response":
                        listener(Response())

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_collect_record_identity_markers": (
                lambda _self, _submitted, scope=None: ("AUTO_001",)
            ),
            "_assert_business_success": lambda _self, _body: None,
        },
    )()
    executor._fill_valid_baseline = lambda _scope: {"projName": "AUTO_001"}
    executor._matching_business_responses = lambda responses, _submitted: list(responses)
    executor._wait_for_command_form_completion = lambda _scope, _handle: True
    executor._verify_saved_record = lambda *_args, **_kwargs: None
    scope = Scope()
    case = _transaction_case(
        "COMMAND", "__save_command", "save_command", None
    )

    result = executor._execute_command_case(case, scope)

    assert result.outcome == "command_verified"
    assert executor.page.waits == [200]
    assert executor.page.listeners == []


def test_save_command_reports_unchanged_visible_validation_when_no_request(monkeypatch):
    clock = [0.0]

    class Button:
        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def click(**_kwargs):
            return None

    class EmptyDialog:
        @property
        def last(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        @staticmethod
        def locator(selector):
            assert selector == "button:visible"
            return Button()

        @staticmethod
        def is_visible():
            return True

    class Page:
        def __init__(self):
            self.listeners = []

        @staticmethod
        def locator(_selector):
            return EmptyDialog()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        @staticmethod
        def wait_for_timeout(_timeout):
            clock[0] += 31.0

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_collect_record_identity_markers": staticmethod(
                lambda _submitted, scope=None: ("AUTO_001",)
            ),
        },
    )()
    executor._fill_valid_baseline = lambda _scope: {
        "recordName": "AUTO_001",
        "riskType": "旧值",
    }
    executor._ensure_command_required_baseline = lambda _scope, _submitted: None
    executor._visible_command_error_text = lambda _scope: "请选择风险类型"
    executor._matching_business_responses = lambda _responses, _submitted: []
    executor._original_scope_is_visible = lambda _scope, _handle: True
    monkeypatch.setattr(executor_module.time, "monotonic", lambda: clock[0])
    case = BoundCommonCase(
        case_id="VIEW-002",
        field_key="__save_command",
        field_label="保存",
        field_type="save_command",
        selector="",
        scenario="保存反馈",
        input_value="",
        expected_type="accepted",
        expected_value="保存成功",
        priority="P1",
    )

    with pytest.raises(
        AssertionError,
        match="点击保存后被页面校验阻止：请选择风险类型",
    ):
        executor._execute_command_case(case, Scope())

    assert executor.page.listeners == []


def test_ordinary_field_save_waits_for_response_after_form_hides(monkeypatch):
    class Scope:
        visible = True

    class Button:
        @staticmethod
        def click():
            scope.visible = False

    class Response:
        ok = True
        url = "https://example.test/fi-service/projProgress/add"

        @staticmethod
        def json():
            return {"code": 0, "data": {"id": "1001"}}

    class Page:
        def __init__(self):
            self.waits = []
            self.response_sent = False

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)
            if not self.response_sent:
                self.response_sent = True
                responses.append(Response())

    scope = Scope()
    responses = []
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_find_save_response": staticmethod(
                lambda observed, _submitted: observed[-1] if observed else None
            ),
            "_assert_business_success": staticmethod(lambda _body: None),
        },
    )()
    executor._original_scope_is_visible = (
        lambda actual_scope, _handle: actual_scope.visible
    )
    executor._wait_for_command_form_completion = lambda _scope, _handle: True
    monkeypatch.setenv("EI_COMMON_COMMAND_FORM_SETTLE_MS", "1000")

    response, scope_visible, message = executor._click_case_save_with_business_repairs(
        scope,
        Button(),
        responses,
        {"paymentAmountTotal": "1234567.89"},
        DiscoveredCommonField(
            "paymentAmountTotal",
            "截至当前节点累计付款金额（万元）",
            "amount",
            "number",
            "#amount",
            FieldConstraints(),
        ),
        object(),
        expected_type="accepted",
    )

    assert response is responses[0]
    assert scope_visible is False
    assert message == ""
    assert executor.page.waits == [200]


def test_ordinary_field_save_retries_once_when_no_business_request_is_dispatched(monkeypatch):
    clock = [0.0]

    class Scope:
        visible = True

    class Response:
        ok = True
        url = "https://example.test/fi-service/projProgress/add"

        @staticmethod
        def json():
            return {"code": 0, "data": {"id": "1001"}}

    class Button:
        def __init__(self):
            self.clicks = 0

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        def click(self):
            self.clicks += 1
            if self.clicks == 2:
                requests_started[0] = True
                responses.append(Response())

    class Page:
        @staticmethod
        def wait_for_timeout(timeout):
            clock[0] += timeout / 1000

    scope = Scope()
    responses = []
    requests_started = [False]
    button = Button()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_find_save_response": staticmethod(
                lambda observed, _submitted: observed[-1] if observed else None
            ),
            "_save_button": staticmethod(lambda _scope: button),
            "_assert_business_success": staticmethod(lambda _body: None),
        },
    )()
    executor._original_scope_is_visible = lambda *_args: True
    executor._wait_for_command_form_completion = lambda *_args: False
    monkeypatch.setattr(executor_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setenv("EI_COMMON_SAVE_DISPATCH_TIMEOUT_MS", "0")

    response, scope_visible, message = executor._click_case_save_with_business_repairs(
        scope,
        button,
        responses,
        {"paymentAmountTotal": "1234567.89"},
        DiscoveredCommonField(
            "paymentAmountTotal",
            "截至当前节点累计付款金额（万元）",
            "amount",
            "number",
            "#amount",
            FieldConstraints(),
        ),
        object(),
        expected_type="accepted",
        business_request_started=lambda: requests_started[0],
    )

    assert button.clicks == 2
    assert response is responses[0]
    assert scope_visible is True
    assert message == ""


def test_save_command_repairs_duplicate_business_response_and_retries():
    class Button:
        def __init__(self):
            self.clicks = 0

        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        def click(self, **_kwargs):
            self.clicks += 1

    class EmptyDialog:
        @property
        def last(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        def __init__(self, button):
            self.button = button

        def locator(self, selector):
            assert selector == "button:visible"
            return self.button

        @staticmethod
        def is_visible():
            return True

    class Page:
        def __init__(self):
            self.listeners = []

        def locator(self, _selector):
            return EmptyDialog()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    class Response:
        ok = True
        url = "https://example.test/project/save"

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    button = Button()
    duplicate = Response({"status": "1", "msg": "事项名称已存在，请修改后再保存"})
    success = Response({"status": "0", "data": {"id": "record-1"}})
    submitted = {"matterName": "AUTO_001"}
    repaired_values = []
    marker_values = []

    class Driver:
        def _collect_record_identity_markers(self, current, scope=None):
            marker_values.append(dict(current))
            return (current["matterName"],)

        @staticmethod
        def _assert_business_success(body):
            if body.get("status") != "0":
                raise AssertionError("保存接口返回业务失败")

        @staticmethod
        def _repair_business_validation_message(message, current, attempt):
            assert "已存在" in message
            repaired = {"matterName": f"{current['matterName']}_唯一_{attempt}"}
            repaired_values.append(repaired)
            return repaired

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = Driver()
    executor._fill_valid_baseline = lambda _scope: dict(submitted)
    executor._wait_for_command_form_completion = lambda _scope, _handle: True
    executor._original_scope_is_visible = lambda _scope, _handle: True
    executor._matching_business_responses = (
        lambda _responses, current: [duplicate]
        if current["matterName"] == "AUTO_001" else [success]
    )
    verify_calls = []
    executor._verify_saved_record = lambda *args, **kwargs: verify_calls.append((args, kwargs))
    case = _transaction_case(
        "COMMAND", "__save_command", "save_command", None
    )

    result = executor._execute_command_case(case, Scope(button))

    assert result.outcome == "command_verified"
    assert button.clicks == 2
    assert repaired_values == [{"matterName": "AUTO_001_唯一_1"}]
    assert marker_values[-1] == {"matterName": "AUTO_001_唯一_1"}
    assert verify_calls[0][0][2] == {"matterName": "AUTO_001_唯一_1"}
    assert verify_calls[0][0][3] == ("AUTO_001_唯一_1",)


def test_save_confirmation_cancel_fails_when_page_directly_saves_without_confirm():
    class Button:
        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            return None

    class EmptyDialog:
        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Scope:
        def locator(self, selector):
            assert selector == "button:visible"
            return Button()

        def is_visible(self):
            return True

    class Page:
        def __init__(self):
            self.listeners = []

        def locator(self, _selector):
            return EmptyDialog()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        def wait_for_timeout(self, _timeout):
            return None

    class Response:
        ok = True
        url = "https://example.test/project/save"

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_collect_record_identity_markers": lambda _self, _submitted, scope=None: (
                "AUTO_001",
            ),
        },
    )()
    executor._fill_valid_baseline = lambda _scope: {"recordName": "AUTO_001"}
    executor._wait_for_command_form_completion = lambda _scope, _handle: None
    executor._matching_business_responses = lambda _responses, _submitted: [Response()]
    verify_calls = []
    executor._verify_saved_record = lambda *args, **_kwargs: verify_calls.append(args)
    executor._capture_failure_once = lambda _message: None
    case = BoundCommonCase(
        case_id="ADD-066",
        field_key="__save_command",
        field_label="保存",
        field_type="save_command",
        selector="",
        scenario="二次确认取消",
        input_value="",
        expected_type="accepted",
        expected_value="确认框关闭；数据未保存",
        priority="P1",
    )

    with pytest.raises(AssertionError, match="期望二次确认取消.*已直接保存"):
        executor._execute_command_case(case, Scope())

    assert verify_calls
    assert executor.page.listeners == []


def test_save_command_waits_for_original_form_to_close_before_readback(monkeypatch):
    class Handle:
        def __init__(self):
            self.checks = 0

        def is_visible(self):
            self.checks += 1
            return self.checks < 3

    class EmptyLoading:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Page:
        def __init__(self):
            self.waits = []

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)

        @staticmethod
        def locator(_selector):
            return EmptyLoading()

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    handle = Handle()

    form_hidden = executor._wait_for_command_form_completion(object(), handle)

    assert form_hidden is True
    assert executor.page.waits == [100, 100, 500]


def test_save_command_allows_retained_form_after_settle_timeout(monkeypatch):
    class VisibleHandle:
        @staticmethod
        def is_visible():
            return True

    class EmptyLoading:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type("Page", (), {
        "wait_for_timeout": lambda self, timeout: None,
        "locator": lambda self, selector: EmptyLoading(),
    })()
    monkeypatch.setenv("EI_COMMON_FORM_CLOSE_TIMEOUT_MS", "0")

    assert executor._wait_for_command_form_completion(object(), VisibleHandle()) is False


def test_discovers_visible_form_close_command():
    class Locator:
        def __init__(self, count=0):
            self._count = count

        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return Locator()

        def count(self):
            return self._count

        def is_visible(self):
            return bool(self._count)

    class Scope:
        def locator(self, selector):
            if selector == "button:visible":
                return Locator()
            assert selector == FORM_CLOSE_SELECTOR
            return Locator(1)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)

    commands = executor._discover_form_commands(Scope())

    assert [(command.field_key, command.field_type) for command in commands] == [
        ("__close_command", "close_command")
    ]


def test_discovers_and_verifies_visible_dialog_title():
    class Title:
        def count(self):
            return 1

        def is_visible(self):
            return True

        def inner_text(self):
            return "编辑项目立项"

        def text_content(self):
            return "编辑项目立项"

    class EmptyButtons:
        def filter(self, **_kwargs):
            return self

        def count(self):
            return 0

    class Scope:
        def locator(self, selector):
            if selector == "button:visible":
                return EmptyButtons()
            raise AssertionError(selector)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor._form_close_button = lambda _scope: None
    executor._form_title = lambda _scope: Title()
    scope = Scope()

    commands = executor._discover_form_commands(scope)
    case = BoundCommonCase(
        "EDIT-001", "__dialog_title", "对话框名称", "dialog_title", "",
        "对话框名称检查", None, "accepted", "显示编辑模块名", "P1",
    )
    result = executor._execute_dialog_title_case(case, scope)

    assert [(field.field_key, field.field_type) for field in commands] == [
        ("__dialog_title", "dialog_title")
    ]
    assert result.outcome == "dialog_title_verified"
    assert result.observed == "编辑项目立项"


def test_optional_clear_includes_populated_values_and_attachments():
    state = {"remark": True, "meetingFile": True, "requiredName": True}
    runtime = [
        DomField("remark", "备注", "text", "#remark"),
        DomField("meetingFile", "会议纪要", "file", "#file"),
        DomField("requiredName", "名称", "text", "#name", required=True),
    ]

    class Driver:
        @staticmethod
        def _dom_field_has_value(dom, root=None):
            assert root is scope
            return state[dom.field_code]

    scope = object()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor.driver = Driver()
    executor._wait_for_fields_stable = lambda _scope: runtime
    executor._dom_for_discovered_field = lambda field, _scope=None: next(
        dom for dom in runtime if dom.field_code == field.field_key
    )
    executor._clear_required_control = lambda field: state.__setitem__(
        field.field_key, False
    )
    executor._clear_optional_attachment = lambda field, _scope: state.__setitem__(
        field.field_key, False
    )
    submitted = {
        "remark": "原备注",
        "meetingFile": "已有附件",
        "requiredName": "项目名称",
    }

    cleared = executor._clear_optional_field_values(scope, submitted)

    assert cleared == {"remark", "meetingFile"}
    assert submitted["remark"] == ""
    assert submitted["meetingFile"] == ""
    assert state["meetingFile"] is False


def test_optional_clear_falls_back_to_attachment_removal_when_no_value_field_exists():
    state = {"meetingFile": True}
    runtime = [DomField("meetingFile", "会议纪要", "file", "#file")]

    class Driver:
        @staticmethod
        def _dom_field_has_value(dom, root=None):
            assert root is scope
            return state[dom.field_code]

    scope = object()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor.driver = Driver()
    executor._wait_for_fields_stable = lambda _scope: runtime
    executor._dom_for_discovered_field = lambda field, _scope=None: runtime[0]
    executor._clear_optional_attachment = lambda _field, _scope: state.__setitem__(
        "meetingFile", False
    )
    submitted = {"meetingFile": "已有附件"}

    assert executor._clear_optional_field_values(scope, submitted) == {"meetingFile"}
    assert submitted["meetingFile"] == ""


def test_optional_attachment_clear_supports_file_upload_close_icon():
    state = {"deleted": False, "hovered": False}

    class Remove:
        def __init__(self):
            self.force_calls = []

        @property
        def first(self):
            return self

        def count(self):
            return 0 if state["deleted"] else 1

        def click(self, force=False, timeout=None):
            assert state["hovered"] is True
            self.force_calls.append(force)
            state["deleted"] = True

    remove = Remove()

    class Empty:
        @property
        def first(self):
            return self

        def count(self):
            return 0

    class Row:
        def hover(self, timeout=None):
            state["hovered"] = True

        def locator(self, selector):
            assert ".el-icon--close" in selector
            return remove

    class Rows:
        def count(self):
            return 0 if state["deleted"] else 1

        def nth(self, index):
            assert index == 0
            return Row()

    class Owner:
        @property
        def first(self):
            return self

        def locator(self, selector):
            if selector == ".el-upload-list__item:visible,.ant-upload-list-item:visible":
                return Rows()
            assert ".el-icon--close:visible" in selector
            return Empty()

    class Input:
        @property
        def first(self):
            return self

        def count(self):
            return 1

        def locator(self, selector):
            assert selector.startswith("xpath=ancestor::")
            return Owner()

    class Scope:
        def locator(self, selector):
            assert selector == "#meeting-file"
            return Input()

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor._confirm_attachment_removal = lambda: None
    executor._attachment_names = lambda *_args: (
        [] if state["deleted"] else ["meeting.pdf"]
    )
    field = DiscoveredCommonField(
        "meetingFile", "会议纪要", "file", "file", "#meeting-file", FieldConstraints()
    )

    executor._clear_optional_attachment(field, Scope())

    assert state == {"deleted": True, "hovered": True}
    assert remove.force_calls == [False]


def test_discovers_inline_edit_commands_from_nearest_page_host():
    class Buttons:
        def __init__(self, labels=()):
            self.labels = tuple(labels)

        @property
        def first(self):
            return self

        def filter(self, *, has_text):
            pattern = has_text.pattern
            labels = [label for label in self.labels if has_text.search(label)]
            return Buttons(labels)

        def count(self):
            return len(self.labels)

        def is_visible(self):
            return bool(self.labels)

    class InlineHost:
        @property
        def first(self):
            return self

        def locator(self, selector):
            assert selector == "button:visible"
            return Buttons(("保存", "取消编辑"))

        def count(self):
            return 1

        @staticmethod
        def is_visible():
            return True

    host = InlineHost()

    class FormScope:
        def locator(self, selector):
            if selector == "button:visible":
                return Buttons()
            if selector.startswith("xpath=ancestor::"):
                return host
            return Buttons()

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)

    commands = executor._discover_form_commands(FormScope())

    assert [(command.field_key, command.field_type) for command in commands] == [
        ("__save_command", "save_command"),
        ("__cancel_command", "cancel_command"),
    ]


def test_close_command_closes_original_form_without_saving():
    class Handle:
        def __init__(self):
            self.visible = True

        def wait_for_element_state(self, state, timeout):
            assert state == "hidden"
            assert timeout == 3000
            assert not self.visible

        def is_visible(self):
            return self.visible

    handle = Handle()

    class Button:
        clicks = 0

        @property
        def last(self):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            self.clicks += 1
            handle.visible = False

    button = Button()

    class Scope:
        def locator(self, selector):
            assert selector == FORM_CLOSE_SELECTOR
            return button

        def element_handle(self):
            return handle

    class EmptyConfirm:
        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Page:
        def __init__(self):
            self.listeners = []

        def locator(self, _selector):
            return EmptyConfirm()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        def wait_for_timeout(self, _timeout):
            pass

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    modified_calls = []
    executor._modify_form_for_abandonment = lambda scope: (
        modified_calls.append(scope) or {"projectName": "AUTO_UNSAVED_001"}
    )
    case = _transaction_case(
        "CLOSE", "__close_command", "close_command", None
    )

    result = executor._execute_command_case(case, Scope())

    assert result.outcome == "close_verified"
    assert result.observed == (
        "modified=projectName; form closed without save"
    )
    assert len(modified_calls) == 1
    assert button.clicks == 1
    assert executor.page.listeners == []


@pytest.mark.parametrize(
    ("field_type", "field_key"),
    [
        ("cancel_command", "__cancel_command"),
        ("close_command", "__close_command"),
    ],
)
def test_abandonment_commands_snapshot_existing_confirmations_before_click(
    field_type, field_key,
):
    events = []

    class Handle:
        visible = True

        def is_visible(self):
            return self.visible

        def wait_for_element_state(self, state, *, timeout):
            assert state == "hidden"
            assert timeout == 3_000
            assert not self.visible

    handle = Handle()

    class Button:
        @property
        def last(self):
            return self

        def filter(self, **_kwargs):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            events.append("click")
            handle.visible = False

    button = Button()

    class Scope:
        def locator(self, selector):
            assert selector in {FORM_CLOSE_SELECTOR, "button:visible"}
            return button

        def element_handle(self):
            return handle

    scope = Scope()

    class Page:
        def __init__(self):
            self.listeners = []

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._modify_form_for_abandonment = lambda _scope: {
        "projectName": "AUTO_UNSAVED_001"
    }
    business_confirmation_handle = object()

    def snapshot_confirmations():
        events.append("snapshot")
        return (business_confirmation_handle,)

    confirmation_calls = []

    def confirm_leave(
        actual_scope,
        actual_handle,
        *,
        ignored_confirmation_handles=(),
        **_kwargs,
    ):
        events.append("confirm")
        confirmation_calls.append(
            (actual_scope, actual_handle, tuple(ignored_confirmation_handles))
        )
        return False

    executor._visible_confirmation_handles = snapshot_confirmations
    executor._confirm_form_cancellation_if_present = confirm_leave
    case = _transaction_case("ABANDON", field_key, field_type, None)

    result = executor._execute_command_case(case, scope)

    assert result.outcome in {"cancel_verified", "close_verified"}
    assert events == ["snapshot", "click", "confirm"]
    assert confirmation_calls == [
        (scope, handle, (business_confirmation_handle,))
    ]
    assert executor.page.listeners == []


def test_close_command_listens_before_dirty_field_blur_can_save():
    class Request:
        method = "POST"
        url = "https://example.test/api/projects"
        post_data_json = {"projectName": "AUTO_UNSAVED_001"}

    class Handle:
        visible = True

        def wait_for_element_state(self, state, timeout):
            assert state == "hidden"
            assert timeout == 3000
            assert not self.visible

        def is_visible(self):
            return self.visible

    handle = Handle()

    class Button:
        @property
        def last(self):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            handle.visible = False

    button = Button()

    class Scope:
        def locator(self, selector):
            assert selector == FORM_CLOSE_SELECTOR
            return button

        def element_handle(self):
            return handle

        def is_visible(self):
            return handle.visible

    class EmptyConfirm:
        @property
        def last(self):
            return self

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Page:
        def __init__(self):
            self.listeners = []

        def locator(self, _selector):
            return EmptyConfirm()

        def on(self, event, listener):
            self.listeners.append((event, listener))

        def remove_listener(self, event, listener):
            self.listeners.remove((event, listener))

        def wait_for_timeout(self, _timeout):
            pass

        def emit_request(self, request):
            for event, listener in tuple(self.listeners):
                if event == "request":
                    listener(request)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    def modify(_scope):
        executor.page.emit_request(Request())
        return {"projectName": "AUTO_UNSAVED_001"}

    executor._modify_form_for_abandonment = modify
    case = _transaction_case("CLOSE", "__close_command", "close_command", None)

    with pytest.raises(AssertionError, match="产生业务保存请求"):
        executor._execute_command_case(case, Scope())

    assert executor.page.listeners == []


def test_close_button_prefers_current_form_header_over_ambiguous_fallbacks():
    class Locator:
        def __init__(self, count, name):
            self._count = count
            self.name = name

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def count(self):
            return self._count

    class Scope:
        def __init__(self):
            self.queries = []

        def locator(self, selector):
            self.queries.append(selector)
            return Locator(1, "header")

    scope = Scope()

    close = CommonFieldExecutor._form_close_button(scope, strict=True)

    assert close.name == "header"
    assert scope.queries == [FORM_CLOSE_SELECTOR]


def test_close_button_rejects_multiple_aria_fallbacks():
    class Locator:
        def __init__(self, count):
            self._count = count

        @property
        def first(self):
            return self

        @property
        def last(self):
            return self

        def count(self):
            return self._count

    class Scope:
        def __init__(self):
            self.calls = 0

        def locator(self, _selector):
            self.calls += 1
            return Locator(0 if self.calls == 1 else 2)

    with pytest.raises(AssertionError, match="多个关闭图标候选"):
        CommonFieldExecutor._form_close_button(Scope(), strict=True)


def test_close_command_modifies_a_text_field_before_abandoning_form():
    class Input:
        value = ""

        def input_value(self):
            return self.value

    control = Input()
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor._scan_fields = lambda _scope=None: [
        DomField("projectName", "项目名称", "text", "#project-name")
    ]
    executor._locator = lambda _field: control
    executor._replace_value = lambda _field, value: setattr(control, "value", value)

    modified = executor._modify_form_for_abandonment(object())

    assert modified == {"projectName": control.value}
    assert control.value.startswith("AUTO_UNSAVED_")


def test_cancel_confirmation_waits_for_async_confirm_leave_button():
    class Handle:
        visible = True

        def is_visible(self):
            return self.visible

    handle = Handle()

    class Accept:
        @property
        def last(self):
            return self

        def filter(self, *, has_text):
            assert has_text.search("确认离开")
            return self

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self):
            handle.visible = False

    class Confirm:
        def __init__(self, page):
            self.page = page

        @property
        def last(self):
            return self

        def count(self):
            return int(self.page.ticks >= 2)

        def is_visible(self):
            return self.page.ticks >= 2

        def locator(self, selector):
            assert selector == "button:visible"
            return Accept()

    class Page:
        ticks = 0

        def locator(self, _selector):
            return Confirm(self)

        def wait_for_timeout(self, timeout):
            assert timeout == 100
            self.ticks += 1

    class Scope:
        def is_visible(self):
            return handle.visible

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    clicked = executor._confirm_form_cancellation_if_present(Scope(), handle)

    assert clicked
    assert not handle.visible
    assert executor.page.ticks == 2


def test_close_form_ignores_confirmation_visible_before_close_and_accepts_new_leave_confirmation():
    class State:
        dialog_visible = True
        ticks = 0

    state = State()

    class Button:
        def __init__(self, label, on_click):
            self.label = label
            self.on_click = on_click
            self.clicks = 0

        @property
        def last(self):
            return self

        def filter(self, *, has_text):
            return self if has_text.search(self.label) else EmptyButton()

        def count(self):
            return 1

        def is_visible(self):
            return True

        def click(self, **_kwargs):
            self.clicks += 1
            self.on_click()

    class EmptyButton(Button):
        def __init__(self):
            super().__init__("", lambda: None)

        def count(self):
            return 0

        def is_visible(self):
            return False

    class Confirmation:
        def __init__(self, name, button, *, visible):
            self.name = name
            self.button = button
            self.visible = visible

        @property
        def last(self):
            return self

        def count(self):
            return int(self.visible)

        def is_visible(self):
            return self.visible

        def element_handle(self):
            return self

        def locator(self, selector):
            assert selector == "button:visible"
            return self.button

    business_button = Button("确定", lambda: setattr(business_confirm, "visible", False))
    business_confirm = Confirmation(
        "business", business_button, visible=True
    )
    leave_button = Button(
        "确认离开", lambda: setattr(state, "dialog_visible", False)
    )
    leave_confirm = Confirmation("leave", leave_button, visible=False)

    class DynamicConfirmation:
        @property
        def current(self):
            visible = [
                item for item in (business_confirm, leave_confirm) if item.visible
            ]
            return visible[-1] if visible else None

        def count(self):
            return int(self.current is not None)

        def is_visible(self):
            return self.current is not None

        def locator(self, selector):
            return self.current.locator(selector)

        def element_handle(self):
            return self.current

    class Confirmations:
        @property
        def last(self):
            return DynamicConfirmation()

        def _visible(self):
            return [
                item for item in (business_confirm, leave_confirm) if item.visible
            ]

        def count(self):
            return len(self._visible())

        def nth(self, index):
            return self._visible()[index]

        def all(self):
            return self._visible()

    close_button = Button("取消", lambda: None)

    class DialogHandle:
        def is_visible(self):
            return state.dialog_visible

        def wait_for_element_state(self, expected, *, timeout):
            assert expected == "hidden"
            assert timeout == 5_000
            if state.dialog_visible:
                raise TimeoutError("dialog is still visible")

    dialog_handle = DialogHandle()

    class Dialog:
        @property
        def last(self):
            return self

        def count(self):
            return 1

        def is_visible(self):
            return state.dialog_visible

        def element_handle(self):
            return dialog_handle

        def locator(self, selector):
            assert "button:has-text('取消')" in selector
            return close_button

    class Page:
        def locator(self, selector):
            if "message-box" in selector or "alertdialog" in selector:
                return Confirmations()
            return Dialog()

        def wait_for_timeout(self, timeout):
            assert timeout == 100
            state.ticks += 1
            leave_confirm.visible = True

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    executor.close_form()

    assert close_button.clicks == 1
    assert business_button.clicks == 0
    assert leave_button.clicks == 1
    assert state.ticks >= 1
    assert not state.dialog_visible


def test_close_command_detects_rest_save_request_from_unsaved_marker_payload():
    class Request:
        method = "POST"
        url = "https://example.test/api/projects"
        post_data_json = {"projectName": "AUTO_UNSAVED_001"}

    mutations = CommonFieldExecutor._business_mutation_requests(
        [Request()], {"projectName": "AUTO_UNSAVED_001"}
    )

    assert len(mutations) == 1


def _transaction_case(
    case_id: str,
    field_key: str,
    field_type: str,
    input_value,
) -> BoundCommonCase:
    return BoundCommonCase(
        case_id=case_id,
        field_key=field_key,
        field_label=field_key,
        field_type=field_type,
        selector=f"#{field_key}",
        scenario="合法输入",
        input_value=input_value,
        expected_type="accepted",
        expected_value="保存成功",
        priority="P1",
    )


def test_unique_name_boundary_keeps_declared_length():
    case = _case()
    field = DiscoveredCommonField(
        "projName", "项目名称", "text", "text", "#name",
        FieldConstraints(max_length=100),
    )
    case = BoundCommonCase(
        case_id="ADD-030", field_key="projName", field_label="项目名称",
        field_type="text", selector="#name", scenario="长度边界",
        input_value="测" * 100, expected_type="accepted", expected_value="", priority="P0",
    )

    value = CommonFieldExecutor._case_input_value(case, field, "UI自动化_唯一值")

    assert len(value) == 100
    assert value.startswith("UI自动化_唯一值")


def test_unique_name_lower_boundary_from_replaced_template_keeps_case_length():
    case = BoundCommonCase(
        case_id="ADD-027", field_key="projName", field_label="项目名称",
        field_type="text", selector="#name", scenario="长度下边界",
        input_value="测" * 49, expected_type="accepted", expected_value="", priority="P1",
    )
    field = DiscoveredCommonField(
        "projName", "项目名称", "text", "text", "#name",
        FieldConstraints(max_length=50),
    )

    value = CommonFieldExecutor._case_input_value(case, field, "UI自动化_唯一值")

    assert len(value) == 49
    assert value.startswith("UI自动化_唯一值")


def test_unique_name_value_preserves_leading_and_trailing_spaces():
    case = BoundCommonCase(
        case_id="ADD-034", field_key="projName", field_label="项目名称",
        field_type="text", selector="#name", scenario="前后空格",
        input_value="  测试内容  ", expected_type="accepted", expected_value="", priority="P1",
    )
    field = DiscoveredCommonField(
        "projName", "项目名称", "text", "text", "#name", FieldConstraints(max_length=100)
    )

    value = CommonFieldExecutor._case_input_value(case, field, "UI自动化_唯一值")

    assert value.startswith("  UI自动化_唯一值_测试内容")
    assert value.endswith("  ")


def test_common_punctuation_value_is_bounded_by_runtime_maxlength():
    case = BoundCommonCase(
        case_id="ADD-011",
        field_key="buildScale",
        field_label="建设规模",
        field_type="text",
        selector="#buildScale",
        scenario="中英文及常用标点",
        input_value="中文ABC 123，。-()!@#$%^&*()_+-=[]{}|;':\",./<>?~`；《》【】￥……",
        expected_type="accepted",
        expected_value="合法字符可保存且显示完整",
        priority="P1",
    )
    field = DiscoveredCommonField(
        "buildScale", "建设规模", "text", "text", "#buildScale",
        FieldConstraints(max_length=50),
    )

    value = CommonFieldExecutor._case_input_value(case, field, "UI自动化_唯一值")

    assert len(value) == 50
    assert value == case.input_value[:50]


def test_identity_over_length_value_keeps_unique_token_before_control_truncation():
    case = BoundCommonCase(
        case_id="ADD-018",
        field_key="matterName",
        field_label="事项名称",
        field_type="text",
        selector="#matterName",
        scenario="超过长度",
        input_value="测" * 51,
        expected_type="field_error",
        expected_value="第 51 个字符不可录入",
        priority="P0",
    )
    field = DiscoveredCommonField(
        "matterName", "事项名称", "text", "text", "#matterName",
        FieldConstraints(max_length=50),
    )

    value = CommonFieldExecutor._case_input_value(
        case,
        field,
        "UI自动化_20260807104425_项目决策事项名称很长很长很长很长_S123",
    )

    assert len(value) == 51
    assert value.startswith("UI自动化")
    assert "_S123" in value[:50]


def test_original_scope_visibility_ignores_new_dynamic_locator_match():
    class DynamicScope:
        def is_visible(self):
            return True

    class ClosedOriginalDialog:
        def is_visible(self):
            return False

    assert not CommonFieldExecutor._original_scope_is_visible(
        DynamicScope(), ClosedOriginalDialog()
    )


def test_original_scope_visibility_keeps_genuinely_open_dialog_visible():
    class DynamicScope:
        def is_visible(self):
            return False

    class OpenOriginalDialog:
        def is_visible(self):
            return True

    assert CommonFieldExecutor._original_scope_is_visible(
        DynamicScope(), OpenOriginalDialog()
    )


def test_visible_error_text_includes_page_level_error_message():
    class Errors:
        def __init__(self, texts=()):
            self.texts = list(texts)

        def count(self):
            return len(self.texts)

        def all_inner_texts(self):
            return self.texts

    class Page:
        def locator(self, selector):
            assert ".el-message--error:visible" in selector
            return Errors(["网络连接失败", "网络连接失败"])

    class Scope:
        def locator(self, selector):
            return Errors()

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._scan_fields = lambda _scope: []

    assert executor._visible_error_text(Scope(), _case()) == "网络连接失败"


def test_visible_error_text_uses_page_message_after_pinned_scope_detaches():
    class Errors:
        def all_inner_texts(self):
            return ["保存成功"]

    class Page:
        def locator(self, selector):
            assert selector == executor_module.PAGE_ERROR_SELECTOR
            return Errors()

    class DetachedScope:
        def is_visible(self):
            return False

        def locator(self, _selector):
            raise AssertionError("detached scope must not be queried")

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._scan_fields = lambda _scope: (_ for _ in ()).throw(
        AssertionError("detached scope must not be scanned")
    )

    assert executor._visible_error_text(DetachedScope(), _case()) == "保存成功"


def test_visible_error_text_does_not_query_success_notifications():
    queried = []

    class Errors:
        def count(self):
            return 0

        def all_inner_texts(self):
            return []

    class Page:
        def locator(self, selector):
            queried.append(selector)
            return Errors()

    class Scope:
        def locator(self, _selector):
            return Errors()

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor._scan_fields = lambda _scope: []

    assert executor._visible_error_text(Scope(), _case()) == ""
    assert queried
    assert all(".el-message:visible" not in selector for selector in queried)
    assert all(".el-notification:visible" not in selector for selector in queried)


def test_wait_for_page_errors_to_clear_ignores_previous_save_message():
    waits = []
    messages = iter(["有必填项未填写/填写格式有误，请检查", "", ""])
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (), {"wait_for_timeout": lambda _self, milliseconds: waits.append(milliseconds)}
    )()
    executor._visible_page_error_text = lambda: next(messages)

    executor._wait_for_page_errors_to_clear()

    assert waits == [100]


def test_required_case_rejects_unrelated_global_validation_message():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    field = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints(required=True)
    )
    case = BoundCommonCase(
        case_id="ADD-001", field_key="name", field_label="名称",
        field_type="required", selector="#name", scenario="空值提交",
        input_value="", expected_type="field_error", expected_value="必填",
        priority="P0",
    )
    driver = type("Driver", (), {})()
    driver._fill_dialog = lambda only_codes=None: {"other": "valid"}
    driver._upload_default_attachments = lambda _scope: None
    executor.driver = driver
    executor._scan_fields = lambda _scope: [
        DomField("name", "名称", "text", "#name", required=True),
        DomField("other", "其他", "text", "#other", required=True),
    ]
    executor._submit_case = lambda *_args: CommonFieldExecutionResult(
        "ADD-001", "name", "save_blocked", "有必填项未填写"
    )
    executor._required_control_value = lambda _field: ""
    executor._target_required_error_text = lambda _field: ""

    with pytest.raises(AssertionError, match="目标必填字段没有出现可关联的校验提示"):
        executor._execute_required_case(case, object())


def test_required_case_reports_target_field_validation_message():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    field = DiscoveredCommonField(
        "name", "名称", "text", "text", "#name", FieldConstraints(required=True)
    )
    case = BoundCommonCase(
        case_id="ADD-001", field_key="name", field_label="名称",
        field_type="required", selector="#name", scenario="空值提交",
        input_value="", expected_type="field_error", expected_value="必填",
        priority="P0",
    )
    driver = type("Driver", (), {})()
    driver._fill_dialog = lambda only_codes=None: {"other": "valid"}
    driver._upload_default_attachments = lambda _scope: None
    executor.driver = driver
    executor._scan_fields = lambda _scope: [
        DomField("name", "名称", "text", "#name", required=True),
        DomField("other", "其他", "text", "#other", required=True),
    ]
    executor._submit_case = lambda *_args: CommonFieldExecutionResult(
        "ADD-001", "name", "save_blocked", "有必填项未填写"
    )
    executor._required_control_value = lambda _field: ""
    executor._target_required_error_text = lambda _field: "名称不能为空"

    result = executor._execute_required_case(case, object())

    assert result.outcome == "save_blocked"
    assert result.observed == "名称不能为空"


def test_add_001_checks_all_required_fields_with_one_save_and_no_upload(monkeypatch):
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.clear_failure_evidence",
        lambda _page: None,
    )
    cases = (
        BoundCommonCase(
            case_id="ADD-001", field_key="name", field_label="名称",
            field_type="required", selector="#name", scenario="空值提交",
            input_value="", expected_type="field_error", expected_value="必填",
            priority="P0",
        ),
        BoundCommonCase(
            case_id="ADD-001", field_key="requiredFile", field_label="必填附件",
            field_type="required", selector="#required-file", scenario="空值提交",
            input_value="", expected_type="field_error", expected_value="必填",
            priority="P0",
        ),
        BoundCommonCase(
            case_id="ADD-001", field_key="purpose", field_label="用途",
            field_type="required", selector="#purpose", scenario="空值提交",
            input_value="", expected_type="field_error", expected_value="必填",
            priority="P0",
        ),
    )
    transaction = BoundCommonTransaction("TX-001", cases)
    fields = [
        DomField("name", "名称", "text", "#name", required=True),
        DomField(
            "requiredFile", "必填附件", "file", "#required-file", required=True
        ),
        DomField("purpose", "用途", "textarea", "#purpose", required=True),
    ]
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor.driver = type(
        "Driver",
        (),
        {
            "_upload_default_attachments": staticmethod(
                lambda _scope: (_ for _ in ()).throw(
                    AssertionError("ADD-001 must not upload attachments")
                )
            )
        },
    )()
    events = []
    executor.open_fresh_add_form = lambda: events.append("open") or object()
    executor.close_form = lambda: events.append("close")
    executor._scan_fields = lambda _scope=None: fields
    executor._wait_for_fields_stable = lambda _scope: fields
    executor._required_control_has_value = lambda _field: False
    executor._target_required_error_text = lambda field: {
        "name": "请输入名称",
        "requiredFile": "请上传必填附件",
        "purpose": "",
    }[field.field_key]
    executor._capture_failure_once = lambda _message: None
    executor._log_result = lambda case, result: events.append(
        (case.field_key, result.outcome)
    )
    submit_calls = []

    def submit_once(*_args, required_codes=None, **_kwargs):
        submit_calls.append(set(required_codes or ()))
        return CommonFieldExecutionResult(
            "ADD-001", "name", "save_blocked", "有必填项未填写"
        )

    executor._submit_case = submit_once

    results = executor.execute_transaction(transaction)

    assert events[0] == "open"
    assert events[-1] == "close"
    assert submit_calls == [{"name", "requiredFile", "purpose"}]
    assert [result.outcome for result in results] == [
        "save_blocked",
        "save_blocked",
        "required_error_missing",
    ]
    assert [result.field_key for result in results] == [
        "name", "requiredFile", "purpose",
    ]


def test_required_file_accepts_explicit_global_required_block_without_field_error(monkeypatch):
    monkeypatch.setattr(
        "ei_ui_smoke.common_field_executor.clear_failure_evidence",
        lambda _page: None,
    )
    case = BoundCommonCase(
        case_id="ADD-002", field_key="requiredFile", field_label="开工报告",
        field_type="required", selector="#required-file", scenario="空值提交",
        input_value="", expected_type="field_error", expected_value="必填",
        priority="P0", source_row=2, scenario_code=REQUIRED_ERRORS_TRIGGER,
    )
    transaction = BoundCommonTransaction("TX-FILE-REQUIRED", (case,))
    field = DomField(
        "requiredFile", "开工报告", "file", "#required-file", required=True
    )
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = _Page()
    executor.driver = type(
        "Driver",
        (),
        {"_prepare_implicit_required_nested_baselines": staticmethod(lambda _scope: {})},
    )()
    executor.open_fresh_add_form = lambda: object()
    executor.close_form = lambda: None
    executor._scan_fields = lambda _scope=None: [field]
    executor._wait_for_fields_stable = lambda _scope: [field]
    executor._required_control_has_value = lambda _field: False
    executor._wait_for_required_error_snapshot = lambda _fields: {"requiredFile": ""}
    executor._submit_case = lambda *_args, **_kwargs: CommonFieldExecutionResult(
        "ADD-002",
        "requiredFile",
        "save_blocked",
        "有必填项未填写/填写格式有误，请检查",
    )
    executor._capture_failure_once = lambda _message: None
    executor._log_result = lambda *_args: None

    result = executor.execute_transaction(transaction)[0]

    assert result.outcome == "save_blocked"
    assert "全局必填提示" in result.observed


def test_required_file_global_block_recovery_uploads_each_file_without_peer_field_errors():
    class EmptyErrors:
        @staticmethod
        def count():
            return 0

    class Handle:
        @staticmethod
        def is_visible():
            return True

    class Scope:
        @staticmethod
        def element_handle():
            return Handle()

    class Page:
        url = "https://example.test/form"

        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

        @staticmethod
        def wait_for_timeout(_milliseconds):
            return None

        @staticmethod
        def locator(_selector):
            return EmptyErrors()

    fields = {
        key: DiscoveredCommonField(
            key, label, "file", "file", f"#{key}", FieldConstraints(required=True)
        )
        for key, label in (("approvalFile", "批复文件"), ("dataFile", "资料附件"))
    }
    cases = [
        BoundCommonCase(
            case_id="ADD-003", field_key=key, field_label=field.label,
            field_type="required", selector=field.selector,
            scenario="逐项恢复必填附件", input_value="合法附件",
            expected_type="accepted", expected_value="提示消失", priority="P1",
            source_row=index, scenario_code=REQUIRED_ERRORS_RECOVER,
        )
        for index, (key, field) in enumerate(fields.items(), start=3)
    ]
    values = {key: False for key in fields}
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type(
        "Driver",
        (),
        {"_fill_dialog": staticmethod(lambda only_codes=None: {})},
    )()
    executor._required_control_has_value = lambda field: values[field.field_key]
    executor._upload_required_attachment = (
        lambda _scope, field: values.__setitem__(field.field_key, True)
    )
    executor._blur_required_control = lambda _field: None
    executor._target_form_errors = lambda _field: EmptyErrors()
    results = {}
    global_evidence = "附件为空且保存被全局必填提示阻止：有必填项未填写"

    executor._recover_required_fields(
        Scope(),
        cases,
        fields,
        {},
        {key: ("save_blocked", global_evidence) for key in fields},
        results,
    )

    assert all(values.values())
    assert [
        results[(case.case_id, case.field_key, case.source_row)].outcome
        for case in cases
    ] == ["validation_recovered", "validation_recovered"]


def test_required_recovery_allows_pending_prompt_to_recalculate_before_its_turn():
    class EmptyErrors:
        @staticmethod
        def count():
            return 0

    class Handle:
        @staticmethod
        def is_visible():
            return True

    class Scope:
        @staticmethod
        def element_handle():
            return Handle()

    class Page:
        url = "https://example.test/form"

        @staticmethod
        def on(_event, _listener):
            return None

        @staticmethod
        def remove_listener(_event, _listener):
            return None

        @staticmethod
        def wait_for_timeout(_milliseconds):
            return None

    fields = {
        key: DiscoveredCommonField(
            key, label, "text", "text", f"#{key}", FieldConstraints(required=True)
        )
        for key, label in (("riskType", "风险类型"), ("riskOccurredDate", "发生时间"))
    }
    cases = [
        BoundCommonCase(
            case_id="ADD-003", field_key=key, field_label=field.label,
            field_type="required", selector=field.selector, scenario="逐项恢复",
            input_value="有效值", expected_type="accepted", expected_value="提示消失",
            priority="P1", source_row=index, scenario_code=REQUIRED_ERRORS_RECOVER,
        )
        for index, (key, field) in enumerate(fields.items(), start=1)
    ]
    values = {key: False for key in fields}

    def fill_dialog(*, only_codes):
        requested = next(iter(only_codes))
        key = next(key for key in values if key.lower() == requested)
        values[key] = True
        return {key: "有效值"}

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    executor.driver = type("Driver", (), {"_fill_dialog": staticmethod(fill_dialog)})()
    executor._required_control_has_value = lambda field: values[field.field_key]
    executor._blur_required_control = lambda _field: None
    executor._target_form_errors = lambda _field: EmptyErrors()
    executor._original_scope_is_visible = lambda _scope, _handle: True
    results = {}

    executor._recover_required_fields(
        Scope(),
        cases,
        fields,
        {},
        {key: ("save_blocked", "必填") for key in fields},
        results,
    )

    assert all(values.values())
    assert [
        results[(case.case_id, case.field_key, case.source_row)].outcome
        for case in cases
    ] == ["validation_recovered", "validation_recovered"]


def test_required_batch_detects_field_whose_condition_was_disabled_by_clear():
    required = DiscoveredCommonField(
        "amount", "预算金额", "number", "number", "#amount",
        FieldConstraints(required=True),
    )
    conditional = DiscoveredCommonField(
        "plan", "资金筹措方案", "textarea", "textarea", "#plan",
        FieldConstraints(required=True),
    )
    current_conditional = DiscoveredCommonField(
        "plan", "资金筹措方案", "textarea", "textarea", "#plan",
        FieldConstraints(required=False),
    )

    detected = CommonFieldExecutor._conditionally_inactive_required_keys(
        {"amount": required, "plan": conditional},
        {"amount": "请输入预算金额", "plan": ""},
        {"amount": required, "plan": current_conditional},
    )

    assert detected == {"plan"}


def test_conditional_required_field_is_retried_on_valid_baseline():
    case = BoundCommonCase(
        case_id="ADD-001", field_key="plan", field_label="资金筹措方案",
        field_type="required", selector="#plan", scenario="空值提交",
        input_value="", expected_type="field_error", expected_value="必填",
        priority="P0", source_row=2,
        scenario_code=REQUIRED_ERRORS_TRIGGER,
    )
    field = DiscoveredCommonField(
        "plan", "资金筹措方案", "textarea", "textarea", "#plan",
        FieldConstraints(required=True),
    )
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    state = {"value": True}
    events = []
    executor._restore_valid_form = lambda _scope: (
        state.__setitem__("value", True), events.append("restore")
    )
    executor._current_field = lambda _case, _scope: field
    executor._required_control_has_value = lambda _field: state["value"]
    executor._clear_required_control = lambda _field: (
        state.__setitem__("value", False), events.append("clear")
    )

    def submit(*_args, required_codes=None, **_kwargs):
        events.append(("submit", required_codes))
        return CommonFieldExecutionResult(
            case.case_id, case.field_key, "save_blocked", "请输入资金筹措方案"
        )

    executor._submit_case = submit
    executor._wait_for_required_error_snapshot = lambda _fields: {
        "plan": "请输入资金筹措方案"
    }
    results = {}

    executor._execute_conditional_required_fields(
        object(), {"plan"}, [case], [], results,
    )

    assert events.count("clear") == 1
    assert ("submit", {"plan"}) in events
    assert results[(case.case_id, case.field_key, case.source_row)].outcome == "save_blocked"


def test_required_recovery_fills_each_field_in_same_form_without_save_request():
    class Errors:
        def __init__(self, field_key):
            self.field_key = field_key

        def count(self):
            return int(errors[self.field_key])

    class Handle:
        def is_visible(self):
            return True

    class Scope:
        def element_handle(self):
            return Handle()

    class Page:
        url = "https://example.test/form"

        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            assert hasattr(listener, "__dict__")
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

        def wait_for_timeout(self, _milliseconds):
            return None

    fields = {
        "name": DiscoveredCommonField(
            "name", "名称", "text", "text", "#name", FieldConstraints(required=True)
        ),
        "type": DiscoveredCommonField(
            "type", "类型", "select", "select", "#type",
            FieldConstraints(required=True),
        ),
    }
    cases = [
        BoundCommonCase(
            case_id="ANY-RECOVER", field_key=field_key,
            field_label=field.label, field_type="required", selector=field.selector,
            scenario="同一表单内逐项消除必填提示", input_value="合法值",
            expected_type="accepted", expected_value="提示消失", priority="P1",
            source_row=3, scenario_code=REQUIRED_ERRORS_RECOVER,
        )
        for field_key, field in fields.items()
    ]
    errors = {"name": True, "type": True}
    values = {"name": False, "type": False}
    fill_order = []
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    def fill(only_codes=None):
        field_key = next(iter(only_codes))
        fill_order.append(field_key)
        values[field_key] = True
        return {field_key: f"valid-{field_key}"}

    executor.driver = type(
        "Driver", (), {
            "_fill_dialog": staticmethod(fill),
            "_upload_default_attachments": staticmethod(lambda _scope: None),
        }
    )()
    executor._required_control_has_value = lambda field: values[field.field_key]
    executor._target_form_errors = lambda field: Errors(field.field_key)
    executor._locator = lambda field: object()
    executor._blur_required_control = lambda field: errors.__setitem__(field.field_key, False)
    results = {}

    executor._recover_required_fields(
        Scope(), cases, fields, {},
        {field_key: ("save_blocked", "必填") for field_key in fields},
        results,
    )

    assert fill_order == ["name", "type"]
    assert errors == {"name": False, "type": False}
    assert [results[(case.case_id, case.field_key, case.source_row)].outcome for case in cases] == [
        "validation_recovered", "validation_recovered",
    ]


def test_required_recovery_keeps_unclearable_default_value_as_skipped():
    class Handle:
        @staticmethod
        def is_visible():
            return True

    class Scope:
        @staticmethod
        def element_handle():
            return Handle()

    class Page:
        url = "https://example.test/form"

        def __init__(self):
            self.listeners = {}

        def on(self, event, listener):
            self.listeners[event] = listener

        def remove_listener(self, event, listener):
            assert self.listeners.pop(event) is listener

    case = BoundCommonCase(
        case_id="ADD-001", field_key="isGmoDecision", field_label="是否决策",
        field_type="required", selector="#is-gmo-decision", scenario="必填恢复",
        input_value="", expected_type="accepted", expected_value="", priority="P1",
        source_row=1, scenario_code=REQUIRED_ERRORS_RECOVER,
    )
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()
    results = {}

    executor._recover_required_fields(
        Scope(),
        [case],
        {},
        {case.field_key: "控件存在非空默认值且页面没有可用清空操作"},
        {},
        results,
    )

    assert results[(case.case_id, case.field_key, case.source_row)].outcome == (
        "required_default_value_skipped"
    )


def test_required_file_recovery_does_not_require_a_visible_blur_target():
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor._locator = lambda _field: (_ for _ in ()).throw(
        AssertionError("hidden file input must not be located for blur")
    )
    field = DiscoveredCommonField(
        "file:report", "立项报告", "file", "file", "#hidden-file",
        FieldConstraints(required=True),
    )

    executor._blur_required_control(field)


def test_required_file_recovery_scopes_upload_to_target_form_item():
    class Locator:
        def __init__(self, name):
            self.name = name

        @property
        def first(self):
            return self

        def count(self):
            return 1

        def locator(self, selector):
            if self.name == "scope":
                assert selector == "#report-file"
                return Locator("file-input")
            assert "el-form-item" in selector
            return Locator("target-owner")

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type("Page", (), {})()
    uploaded = []
    executor.driver = type(
        "Driver", (), {
            "_upload_default_attachments": lambda _self, owner: uploaded.append(owner.name) or 1,
        }
    )()
    executor._required_control_has_value = lambda _field: True
    field = DiscoveredCommonField(
        "file:report", "立项报告", "file", "file", "#report-file",
        FieldConstraints(required=True),
    )

    executor._upload_required_attachment(Locator("scope"), field)

    assert uploaded == ["target-owner"]


def test_required_error_snapshot_waits_for_late_field_messages(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(executor_module.time, "monotonic", lambda: clock[0])
    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = type(
        "Page", (), {
            "wait_for_timeout": lambda _self, milliseconds: clock.__setitem__(
                0, clock[0] + milliseconds / 1000
            ),
        }
    )()
    field = DiscoveredCommonField(
        "name", "项目名称", "text", "text", "#name",
        FieldConstraints(required=True),
    )
    executor._target_required_error_text = lambda _field: (
        "请输入项目名称" if clock[0] >= 0.3 else ""
    )

    snapshot = executor._wait_for_required_error_snapshot(
        {"name": field}, timeout_seconds=2,
        minimum_observation_seconds=0.5, quiet_seconds=0.2,
    )

    assert snapshot == {"name": "请输入项目名称"}
    assert clock[0] >= 0.5


def test_matching_business_responses_excludes_related_save_endpoints():
    class Request:
        method = "POST"

        def __init__(self, payload):
            self.payload = payload

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self.request = Request(payload)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    driver = type("Driver", (), {})()
    driver._request_payload = lambda request: request.payload
    driver._payload_scalar_values = lambda payload: {
        str(value) for value in payload.values()
    }
    executor.driver = driver
    responses = [
        Response("https://example.test/attachment/save", {"fileId": "f1"}),
        Response("https://example.test/project/save", {"projName": "AUTO_1", "type": "new"}),
        Response("https://example.test/relation/save", {"type": "new"}),
    ]

    matches = executor._matching_business_responses(
        responses, {"projName": "AUTO_1", "type": "new"}
    )

    assert [response.url for response in matches] == [
        "https://example.test/project/save"
    ]


def test_matching_business_responses_ignores_attachment_save_even_with_overlap():
    class Request:
        method = "POST"

        def __init__(self, payload):
            self.payload = payload

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self.request = Request(payload)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    driver = type("Driver", (), {})()
    driver._request_payload = lambda request: request.payload
    driver._payload_scalar_values = lambda payload: {
        str(value) for value in payload.values() if value not in (None, "")
    }
    executor.driver = driver

    matches = executor._matching_business_responses(
        [
            Response(
                "https://example.test/ezgo/foundation/commFile/saveFileBatch",
                {"fileName": "AUTO_1"},
            ),
            Response(
                "https://example.test/fi-service/projectExecution/save",
                {"name": "AUTO_1"},
            ),
        ],
        {"name": "AUTO_1"},
    )

    assert [response.url for response in matches] == [
        "https://example.test/fi-service/projectExecution/save"
    ]


def test_matching_business_responses_accepts_single_update_endpoint_without_payload_overlap():
    class Request:
        method = "PUT"

        def __init__(self, payload):
            self.payload = payload

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self.request = Request(payload)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    driver = type("Driver", (), {})()
    driver._request_payload = lambda request: request.payload
    driver._payload_scalar_values = lambda payload: {
        str(value) for value in payload.values() if value not in (None, "")
    }
    executor.driver = driver
    response = Response(
        "https://example.test/fi-service/projAppInfo/update",
        {"normalizedProjName": "SERVER_VALUE"},
    )

    assert executor._matching_business_responses(
        [response], {"projName": "AUTO_1"}
    ) == [response]


def test_matching_business_responses_uses_stable_keys_for_multiple_normalized_updates():
    class Request:
        method = "PUT"

        def __init__(self, payload):
            self.payload = payload

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self.request = Request(payload)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    driver = type("Driver", (), {})()
    driver._request_payload = lambda request: request.payload
    driver._payload_scalar_values = lambda payload: {
        str(value) for value in payload.values() if value not in (None, "")
    }
    executor.driver = driver
    business = Response(
        "https://example.test/fi-service/projAppInfo/update",
        {"projName": "SERVER_NORMALIZED", "inveId": "company-1"},
    )
    dynamic = Response(
        "https://example.test/fi-service/formData/update",
        {"formCode": "BUILD_PROJ_APP_INFO", "dataJson": "{}"},
    )

    assert executor._matching_business_responses(
        [business, dynamic],
        {"projName": "AUTO_1", "inveId": "company-name"},
    ) == [business]


def test_matching_business_responses_rejects_ambiguous_endpoints_without_payload_overlap():
    class Request:
        method = "POST"

        def __init__(self, payload):
            self.payload = payload

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self.request = Request(payload)

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    driver = type("Driver", (), {})()
    driver._request_payload = lambda request: request.payload
    driver._payload_scalar_values = lambda payload: set(payload.values())
    executor.driver = driver

    assert executor._matching_business_responses(
        [
            Response("https://example.test/project/save", {"server": "A"}),
            Response("https://example.test/relation/save", {"server": "B"}),
        ],
        {"projName": "AUTO_1"},
    ) == []


def test_business_mutation_requests_ignore_attachment_list_and_save_requests():
    class Request:
        method = "POST"

        def __init__(self, url, payload):
            self.url = url
            self.post_data_json = payload

    requests = [
        Request(
            "https://example.test/ezgo/foundation/commFile/selectCommFileList",
            {"moduleDataId": "2086"},
        ),
        Request(
            "https://example.test/ezgo/foundation/commFile/saveFileBatch",
            {"fileName": "AUTO_1"},
        ),
        Request(
            "https://example.test/fi-service/projectExecution/save",
            {"name": "AUTO_1"},
        ),
    ]

    mutations = CommonFieldExecutor._business_mutation_requests(
        requests, {"name": "AUTO_1"}
    )

    assert [request.url for request in mutations] == [
        "https://example.test/fi-service/projectExecution/save"
    ]


def test_command_network_diagnostics_is_sanitized_to_transport_metadata():
    class Request:
        method = "POST"
        url = "https://example.test/fi-service/projAppInfo/update?token=secret"

    class Response:
        request = Request()
        status = 200

    detail = CommonFieldExecutor._command_network_diagnostics(
        [Request()], [Response()], [Request()]
    )

    assert "POST /fi-service/projAppInfo/update" in detail
    assert "HTTP 200" in detail
    assert "token=secret" not in detail


def test_matching_business_responses_counts_same_primary_request_twice():
    class Request:
        method = "POST"

        def __init__(self, payload):
            self.payload = payload

    class Response:
        url = "https://example.test/project/save"

        def __init__(self):
            self.request = Request({"projName": "AUTO_1"})

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    driver = type("Driver", (), {})()
    driver._request_payload = lambda request: request.payload
    driver._payload_scalar_values = lambda payload: set(payload.values())
    executor.driver = driver

    matches = executor._matching_business_responses(
        [Response(), Response()], {"projName": "AUTO_1"}
    )

    assert len(matches) == 2


def test_wait_for_new_form_scope_ignores_marked_closing_dialog():
    class Dialog:
        def __init__(self, marker):
            self.marker = marker

        def is_visible(self):
            return True

        def get_attribute(self, name):
            assert name == "data-ei-form-generation"
            return self.marker

    class Dialogs:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    old = Dialog("old-generation")
    new = Dialog(None)

    class Page:
        def locator(self, _selector):
            return Dialogs([old, new])

        def wait_for_timeout(self, _timeout):
            pass

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    assert executor._wait_for_new_form_scope("old-generation") is new


def test_wait_for_new_form_scope_rejects_only_marked_old_dialog():
    class Dialog:
        def is_visible(self):
            return True

        def get_attribute(self, _name):
            return "old-generation"

    class Dialogs:
        def count(self):
            return 1

        def nth(self, _index):
            return Dialog()

    class Page:
        def locator(self, _selector):
            return Dialogs()

        def wait_for_timeout(self, _timeout):
            pass

    executor = CommonFieldExecutor.__new__(CommonFieldExecutor)
    executor.page = Page()

    try:
        executor._wait_for_new_form_scope("old-generation", timeout=1)
    except AssertionError as exc:
        assert "没有出现新的表单实例" in str(exc)
    else:
        raise AssertionError("expected stale Dialog rejection")
