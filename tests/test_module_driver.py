import pytest

import ei_ui_smoke.module_driver as module_driver_module
from ei_ui_smoke.interactions import FieldInteractor
from ei_ui_smoke.module_driver import (
    ADD_BUTTON, EDITABLE_FORM_CONTROL, INLINE_FORM, DynamicFieldContractError,
    FieldCompletionReport, ModuleSmokeDriver, ModuleSmokeResult, RecordNotDeletableError,
)
from ei_ui_smoke.dynamic_collections import DynamicCollectionChild, DynamicCollectionSpec
from ei_ui_smoke.models import DomField, FieldDefinition, ResolvedField
from ei_ui_smoke.dom import DOM_FIELD_SCRIPT, scan_dom_fields


class Request:
    method = "POST"

    def __init__(self, resource_type="xhr", payload=None):
        self.resource_type = resource_type
        self.post_data_json = payload


class MultipartRequest:
    """Mimic Playwright's non-JSON multipart request behavior."""

    url = "https://host/foundation/oss/endpoint/put-file-attach"
    post_data = "--multipart payload--"

    @property
    def post_data_json(self):
        raise RuntimeError("POST data is not a valid JSON object")


def test_request_business_id_matching_tolerates_multipart_payloads():
    assert not ModuleSmokeDriver._request_contains_business_id(
        MultipartRequest(), "2086649094884339713"
    )


def test_standard_mode_requires_optional_editable_fields_to_be_exercised():
    report = FieldCompletionReport([], [], [], optional_not_filled=["备注 (remark)"])
    driver = object.__new__(ModuleSmokeDriver)
    driver.data_strategy = type("Standard", (), {"strict_field_validation": True})()

    assert not driver._field_report_ok(report)
    assert "备注" in report.message()

    driver.data_strategy = object()
    assert driver._field_report_ok(report)


class Response:
    ok = True

    def __init__(self, url, resource_type="xhr", content_type="application/json", payload=None):
        self.url = url
        self.request = Request(resource_type, payload)
        self.headers = {"content-type": content_type}


class JsonResponse(Response):
    def __init__(self, url, body, **kwargs):
        super().__init__(url, **kwargs)
        self.body = body

    def json(self):
        return self.body


class ApiResponse:
    def __init__(
        self,
        url,
        body=None,
        *,
        ok=True,
        status=200,
        content_type="application/json",
        json_error=None,
    ):
        self.url = url
        self.body = body
        self.ok = ok
        self.status = status
        self.headers = {"content-type": content_type}
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.body


class ApiRequestContext:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return self.responses.pop(0)


class RequestPage:
    def __init__(self, responses, *, url="https://host/records"):
        self.request = ApiRequestContext(responses)
        self.url = url


class ReadyLocator:
    def __init__(self, *, count=1, visible=True, wait_error=None):
        self.first = self
        self._count = count
        self._visible = visible
        self._wait_error = wait_error
        self.wait_calls = []

    def count(self):
        return self._count

    def is_visible(self):
        return self._visible

    def wait_for(self, **kwargs):
        self.wait_calls.append(kwargs)
        if self._wait_error:
            raise self._wait_error


class ReadyScope:
    def __init__(self, loading, controls):
        self.loading = loading
        self.controls = controls

    def locator(self, selector):
        return self.loading if "loading" in selector or "aria-busy" in selector else self.controls


class ValueLocator(ReadyLocator):
    def __init__(self, value):
        super().__init__()
        self._value = value

    def input_value(self):
        return self._value


class ValuePage:
    def __init__(self, values):
        self.values = values

    def locator(self, selector):
        return ValueLocator(self.values[selector])


class FormValueScope(ValuePage):
    pass


class IdentityLocator(ReadyLocator):
    def __init__(self, value, *, nested=False):
        super().__init__()
        self.value = value
        self.nested = nested

    def evaluate(self, script):
        return "" if self.nested else self.value


class IdentityPage:
    def __init__(self, values):
        self.values = values

    def locator(self, selector):
        return self.values[selector]


class DeleteCells:
    def __init__(self, texts):
        self.texts = texts

    def all_inner_texts(self):
        return self.texts


class RecordCommand:
    def __init__(self, label, **attributes):
        self.label = label
        self.attributes = attributes

    def inner_text(self):
        return self.label

    def get_attribute(self, name):
        return self.attributes.get(name)


class RecordCommands:
    def __init__(self, commands):
        self.commands = commands

    def count(self):
        return len(self.commands)

    def nth(self, index):
        return self.commands[index]


class DeleteRow:
    def __init__(self, cell_texts, *, commands=(), **attributes):
        self.cell_texts = cell_texts
        self.commands = list(commands)
        self.attributes = attributes

    def get_attribute(self, name):
        return self.attributes.get(name)

    def locator(self, selector):
        if selector == "td,[role='cell']":
            return DeleteCells(self.cell_texts)
        if "button[" in selector or "[role='button']" in selector:
            return RecordCommands(self.commands)
        raise AssertionError(f"unexpected selector: {selector}")


class DeleteRows:
    def __init__(self, rows):
        self.rows = rows

    def count(self):
        return len(self.rows)

    def nth(self, index):
        return self.rows[index]


class DeleteActionCells:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class RecordCells(DeleteCells):
    def count(self):
        return len(self.texts)


class RecordRow(DeleteRow):
    def is_visible(self):
        return True

    def inner_text(self):
        return "\n".join(self.cell_texts)

    def locator(self, selector):
        if selector == "td,[role='cell']":
            return RecordCells(self.cell_texts)
        return super().locator(selector)


class RecordPage:
    def __init__(self, rows, *, url="https://host/projects"):
        self.rows = DeleteRows(rows)
        self.url = url

    def locator(self, selector):
        if "loading" in selector or "busy" in selector:
            return type("EmptyLocator", (), {
                "count": staticmethod(lambda: 0),
                "first": None,
            })()
        if ".el-table__row" in selector:
            return self.rows
        raise AssertionError(f"unexpected locator: {selector}")


class FileInput:
    def __init__(self, has_value=False):
        self.has_value = has_value
        self.uploads = []

    def set_input_files(self, path):
        self.uploads.append(path)
        self.has_value = True


class FileInputs:
    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class FileDialog:
    def __init__(self, items):
        self.items = FileInputs(items)

    def locator(self, selector):
        assert 'input[type="file"]' in selector
        return self.items


class NoCrudPage:
    def locator(self, selector):
        raise AssertionError("page-access mode must not click an add-like button")


class AddEntry:
    def __init__(self, *, visible=True, enabled=True):
        self.visible = visible
        self.enabled = enabled
        self.clicks = 0
        self.wait_calls = []

    def count(self):
        return 1

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def wait_for(self, **kwargs):
        self.wait_calls.append(kwargs)
        if not self.visible or not self.enabled:
            raise TimeoutError("no actionable add entry")

    def click(self):
        self.clicks += 1


class MissingAddEntry(AddEntry):
    def __init__(self):
        super().__init__(visible=False, enabled=False)

    def count(self):
        return 0


class AddEntryCollection:
    def __init__(self, selector, entries):
        self.selector = selector
        self.entries = entries

    @property
    def first(self):
        candidates = list(self.entries)
        if ":visible" in self.selector:
            candidates = [entry for entry in candidates if entry.visible]
        if ":enabled" in self.selector:
            candidates = [entry for entry in candidates if entry.enabled]
        return candidates[0] if candidates else MissingAddEntry()


class AddEntryPage:
    def __init__(self, entries):
        self.entries = entries
        self.selectors = []

    def locator(self, selector):
        self.selectors.append(selector)
        return AddEntryCollection(selector, self.entries)


def test_selects_last_save_like_response():
    responses = [Response("https://host/query"), Response("https://host/api/fund/add")]
    assert ModuleSmokeDriver._find_save_response(responses).url.endswith("/add")


@pytest.mark.parametrize("operation", ["update", "edit", "modify"])
def test_save_response_accepts_edit_style_mutation_endpoints(operation):
    response = Response(
        f"https://host/fi-service/projAppInfo/{operation}",
        payload={"projName": "AUTO_project"},
    )

    assert ModuleSmokeDriver._find_save_response(
        [response], {"projName": "AUTO_project"}
    ) is response


def test_save_response_prefers_request_containing_submitted_form_values():
    business = Response(
        "https://host/api/resource/add",
        payload={"projName": "AUTO_project", "ownershipStructureList": [{"stockName": "AUTO_stock"}]},
    )
    attachment = Response(
        "https://host/api/file/save",
        payload={"fileList": [], "functionType": "XMGG", "moduleDataId": "123"},
    )

    selected = ModuleSmokeDriver._find_save_response(
        [business, attachment],
        {"projName": "AUTO_project", "股权结构.stockName": "AUTO_stock"},
    )

    assert selected is business


def test_save_response_ignores_attachment_batch_save_even_when_last():
    business = Response(
        "https://host/fi-service/projectExecution/save",
        payload={"progressType": "取得开工批复", "progressDate": "2026-08-01"},
    )
    attachment = Response(
        "https://host/ezgo/foundation/commFile/saveFileBatch",
        payload={"moduleDataId": "2086", "fileList": []},
    )

    selected = ModuleSmokeDriver._find_save_response(
        [business, attachment],
        {"progressType": "取得开工批复", "progressDate": "2026-08-01"},
    )

    assert selected is business


def test_save_response_returns_none_for_attachment_only_save():
    attachment = Response(
        "https://host/ezgo/foundation/commFile/saveFileBatch",
        payload={"moduleDataId": "2086", "fileList": []},
    )

    assert ModuleSmokeDriver._find_save_response(
        [attachment], {"progressType": "取得开工批复"}
    ) is None


def test_page_access_mode_does_not_guess_crud_capability_from_button_text(monkeypatch):
    monkeypatch.delenv("EI_REQUIRE_ADD", raising=False)
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = NoCrudPage()

    assert driver.run().mode == "page_access"


def test_provision_only_forces_add_flow_when_current_detail_action_is_edit(monkeypatch):
    """A detail parent provisioner must not inherit Edit's no-add setting."""
    monkeypatch.delenv("EI_REQUIRE_ADD", raising=False)
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = NoCrudPage()
    called = []

    def wait_for_add():
        called.append("add")
        raise RuntimeError("stop after proving the add flow was entered")

    driver._wait_for_add_button = wait_for_add

    with pytest.raises(RuntimeError, match="add flow"):
        driver.run(provision_only=True)

    assert called == ["add"]


def test_add_entry_skips_hidden_or_disabled_stale_buttons():
    hidden = AddEntry(visible=False)
    disabled = AddEntry(enabled=False)
    actionable = AddEntry()
    page = AddEntryPage([hidden, disabled, actionable])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    selected = driver._wait_for_add_button(timeout=25)

    assert selected is actionable
    assert selected.wait_calls == [{"state": "visible", "timeout": 25}]
    assert page.selectors == [ADD_BUTTON]
    assert all(":visible:enabled" in branch for branch in ADD_BUTTON.split(","))
    assert "创建" in ADD_BUTTON


def test_required_add_without_actionable_entry_fails_instead_of_page_access(monkeypatch):
    monkeypatch.setenv("EI_REQUIRE_ADD", "true")
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = AddEntryPage([])

    with pytest.raises(AssertionError, match="没有找到可见且可用的新增/添加/新建/创建入口"):
        driver.run()


def test_nested_verification_uses_shared_actionable_add_entry():
    driver = object.__new__(ModuleSmokeDriver)
    add = AddEntry()
    scope = object()
    driver._wait_for_add_button = lambda: add
    driver._wait_for_form_scope = lambda: scope
    driver._wait_for_form_ready = lambda actual: None
    driver._prepare_nested_operation = lambda actual: None

    result = driver.verify_nested_operation()

    assert add.clicks == 1
    assert result.mode == "nested_action_verified"


def test_rejects_business_failure_even_when_http_succeeds():
    try:
        ModuleSmokeDriver._assert_business_success({"code": 500, "message": "参数错误"})
    except AssertionError as exc:
        assert "参数错误" in str(exc)
    else:
        raise AssertionError("business failure was accepted")


def test_rejects_nonempty_error_list_even_for_legacy_success_status():
    try:
        ModuleSmokeDriver._assert_business_success({
            "status": "1", "msg": "编码错误", "errors": [{"message": "编码错误"}],
        })
    except AssertionError as exc:
        assert "编码错误" in str(exc)
    else:
        raise AssertionError("response errors were accepted")


def test_waits_for_loading_mask_to_hide_before_form_controls():
    driver = object.__new__(ModuleSmokeDriver)
    loading = ReadyLocator()
    controls = ReadyLocator()

    driver._wait_for_form_ready(ReadyScope(loading, controls))

    assert loading.wait_calls == [{"state": "hidden", "timeout": 15_000}]
    assert controls.wait_calls == [{"state": "visible", "timeout": 15_000}]


def test_form_readiness_timeout_has_precise_failure_message():
    driver = object.__new__(ModuleSmokeDriver)
    scope = ReadyScope(ReadyLocator(count=0), ReadyLocator(wait_error=TimeoutError()))

    try:
        driver._wait_for_form_ready(scope)
    except AssertionError as exc:
        assert str(exc) == "新增表单已打开，但表单控件在 15 秒内未加载完成"
    else:
        raise AssertionError("unready form was accepted")


def test_detail_values_are_compared_with_submitted_values():
    ModuleSmokeDriver._assert_detail_values(
        {"code": 200, "data": {"name": "本次新增", "amount": 10}},
        {"name": "本次新增", "amount": 10},
        required_codes={"name", "amount"},
    )


def test_detail_values_accept_numeric_canonicalization_for_semantic_fields():
    ModuleSmokeDriver._assert_detail_values(
        {"code": 200, "data": {"buildPeriodMonth": "1"}},
        {"buildPeriodMonth": "001"},
        required_codes={"buildPeriodMonth"},
    )


def test_readback_numeric_canonicalization_does_not_apply_to_identifiers():
    assert not ModuleSmokeDriver._readback_values_match(
        "001", ["1"], field_code="serialCode"
    )


def test_empty_readback_matches_missing_null_or_empty_values_only():
    assert ModuleSmokeDriver._readback_values_match(
        "", [], field_code="inveId"
    )
    assert ModuleSmokeDriver._readback_values_match(
        "", [None, ""], field_code="inveId"
    )
    assert not ModuleSmokeDriver._readback_values_match(
        "", ["company-1"], field_code="inveId"
    )


def test_detail_values_verify_cleared_optional_field_on_associated_record():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": {
                "id": "record-1",
                "projName": "AUTO_项目立项_S001",
                "inveId": None,
            }
        },
        {"projName": "AUTO_项目立项_S001", "inveId": ""},
        required_codes={"projName", "inveId"},
        business_id="record-1",
    )


def test_record_identity_markers_fall_back_to_submitted_values_when_scope_stale(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()

    def stale_scan(_page, _scope=None):
        raise TimeoutError("form scope was rerendered")

    monkeypatch.setattr(module_driver_module, "scan_dom_fields", stale_scan)

    markers = driver._collect_record_identity_markers(
        {"matterName": "AUTO_项目决策_S001_整数输入"},
        scope=object(),
    )

    assert markers == ("AUTO_项目决策_S001_整数输入", "AUTO_项目决策_S001")


def test_detail_values_read_nested_wildcard_table_fields_from_same_record():
    ModuleSmokeDriver._assert_detail_values(
        {
            "code": 200,
            "data": {
                "id": "record-1",
                "projName": "本次新增",
                "financeSources": [
                    {"sourceFrom": "自有资金", "amount": "100"},
                ],
            },
        },
        {"financeSources.*.amount": "100"},
        required_codes={"financeSources.*.amount"},
        business_id="record-1",
    )


def test_detail_values_do_not_mix_nested_wildcard_fields_across_records():
    with pytest.raises(AssertionError) as exc_info:
        ModuleSmokeDriver._assert_detail_values(
            {
                "data": [
                    {
                        "id": "record-1",
                        "projName": "本次新增",
                        "financeSources": [{"amount": "99"}],
                    },
                    {
                        "id": "record-2",
                        "projName": "其他记录",
                        "financeSources": [{"amount": "100"}],
                    },
                ]
            },
            {"financeSources.*.amount": "100"},
            required_codes={"financeSources.*.amount"},
            business_id="record-1",
        )

    message = str(exc_info.value)
    assert "financeSources.*.amount" in message
    assert "actual=['99']" in message


def test_detail_values_allow_partial_api_fields_before_page_readback():
    ModuleSmokeDriver._assert_detail_values(
        {"data": {"name": "本次新增"}},
        {"name": "本次新增", "amount": 10},
        required_codes={"name", "amount"},
    )


def test_required_detail_values_cannot_be_combined_across_records():
    try:
        ModuleSmokeDriver._assert_detail_values(
            {
                "data": [
                    {"id": "record-1", "name": "本次新增", "amount": 99},
                    {"id": "record-2", "name": "其他记录", "amount": 10},
                ]
            },
            {"name": "本次新增", "amount": 10},
            required_codes={"name", "amount"},
            business_id="record-1",
        )
    except AssertionError as exc:
        message = str(exc)
        assert "amount" in message
        assert "actual=[99]" in message
    else:
        raise AssertionError("required values from different records were combined")


def test_detail_values_reject_required_code_missing_from_submission():
    try:
        ModuleSmokeDriver._assert_detail_values(
            {"data": {"name": "本次新增", "amount": 10}},
            {"name": "本次新增"},
            required_codes={"amount"},
        )
    except AssertionError as exc:
        assert str(exc) == "指定回读字段未出现在本次提交值：amount"
    else:
        raise AssertionError("required field absent from submission was accepted")


def test_detail_values_default_mode_keeps_partial_comparison_compatibility():
    ModuleSmokeDriver._assert_detail_values(
        {"data": {"name": "本次新增"}},
        {"name": "本次新增", "amount": 10},
    )


def test_detail_values_default_mode_scopes_collection_to_saved_business_id():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": [
                {"id": "record-1", "progressType": "1", "name": "本次新增"},
                {"id": "record-2", "progressType": "99", "name": "其他记录"},
            ]
        },
        {"progressType": "取得开工批复", "name": "本次新增"},
        business_id="record-1",
        submitted_payload={"progressType": "1", "name": "本次新增"},
    )


def test_detail_values_accept_saved_request_code_for_associated_record():
    ModuleSmokeDriver._assert_detail_values(
        {"data": {"id": "record-1", "progressType": "1"}},
        {"progressType": "取得开工批复"},
        required_codes={"progressType"},
        business_id="record-1",
        submitted_payload={"progressType": "1"},
    )


def test_detail_values_accept_unaliased_choice_code_from_saved_request():
    ModuleSmokeDriver._assert_detail_values(
        {"data": {"id": "record-1", "riskType": "1"}},
        {"riskType": "重大安全事故"},
        required_codes={"riskType"},
        business_id="record-1",
        submitted_payload={"riskType": "1"},
    )


def test_detail_values_reject_unaliased_choice_code_different_from_saved_request():
    with pytest.raises(AssertionError, match="riskType"):
        ModuleSmokeDriver._assert_detail_values(
            {"data": {"id": "record-1", "riskType": "2"}},
            {"riskType": "重大安全事故"},
            required_codes={"riskType"},
            business_id="record-1",
            submitted_payload={"riskType": "1"},
        )


def test_detail_values_reject_response_code_different_from_saved_request():
    with pytest.raises(AssertionError, match="progressType"):
        ModuleSmokeDriver._assert_detail_values(
            {"data": {"id": "record-1", "progressType": "2"}},
            {"progressType": "取得开工批复"},
            required_codes={"progressType"},
            business_id="record-1",
            submitted_payload={"progressType": "1"},
        )


def test_detail_values_do_not_hide_wrong_display_name_behind_matching_code():
    with pytest.raises(AssertionError, match="progressType"):
        ModuleSmokeDriver._assert_detail_values(
            {
                "data": {
                    "id": "record-1",
                    "progressType": "1",
                    "progressTypeName": "取得竣工验收",
                }
            },
            {"progressType": "取得开工批复"},
            required_codes={"progressType"},
            business_id="record-1",
            submitted_payload={"progressType": "1"},
        )


def test_verify_saved_record_passes_required_codes_to_detail_assertion(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    detail = JsonResponse(
        "https://host/detail/record-1",
        {"code": 200, "data": {"name": "本次新增"}},
    )
    captured = {}
    monkeypatch.setattr(driver, "_assert_business_success", lambda body, operation="新增": None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None)
    monkeypatch.setattr(driver, "_open_detail", lambda markers, business_id: None)
    monkeypatch.setattr(
        driver,
        "_find_detail_response",
        lambda responses, save_response, business_id: detail,
    )
    monkeypatch.setattr(
        driver,
        "_assert_detail_values",
        lambda payload, submitted, *, required_codes=None, **identity: captured.update(
            required_codes=required_codes,
            identity=identity,
        ),
    )

    driver.verify_saved_record(
        [save, detail],
        save,
        {"name": "本次新增"},
        ("本次新增",),
        required_codes={"name"},
    )

    assert captured["required_codes"] == {"name"}
    assert captured["identity"]["business_id"] == "record-1"
    assert captured["identity"]["record_markers"] == ("本次新增",)
    assert captured["identity"]["detail_request"] is detail.request


def test_verify_saved_record_uses_json_response_body_identity_before_dom_lookup(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/parent/detail"})()
    driver.source_fields = []
    save = JsonResponse(
        "https://host/api/progress/add",
        {"code": 200, "data": {"id": "child-1"}},
    )
    child_list = JsonResponse(
        "https://host/api/progress/list",
        {
            "code": 200,
            "data": {
                "records": [
                    {"id": "child-1", "progressDesc": "已完成现场施工"}
                ]
            },
        },
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None
    )
    monkeypatch.setattr(
        driver, "_try_current_page_list_readback", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        driver,
        "_open_detail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("associated JSON response must be checked before DOM lookup")
        ),
    )

    result = driver.verify_saved_record(
        [save, child_list],
        save,
        {"progressDesc": "已完成现场施工"},
        (),
        required_codes={"progressDesc"},
    )

    assert result.mode == "add_and_detail_verified"
    assert result.business_id == "child-1"
    assert result.detail_url == child_list.url


def test_verify_saved_record_passes_required_codes_to_edit_form_assertion(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    events = []
    driver.page = type("Page", (), {
        "wait_for_timeout": lambda self, timeout: events.append(("wait", timeout)),
    })()
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    captured = {}
    edit = type("Edit", (), {
        "click": lambda self: events.append(("edit",)),
    })()
    monkeypatch.setattr(driver, "_assert_business_success", lambda body, operation="新增": None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_open_form", lambda: None)
    monkeypatch.setattr(driver, "_open_detail", lambda markers, business_id: None)
    monkeypatch.setattr(driver, "_current_detail_edit_button", lambda markers: edit)
    monkeypatch.setattr(
        driver,
        "_find_detail_response",
        lambda responses, save_response, business_id: None,
    )
    monkeypatch.setattr(
        driver,
        "_assert_open_form_values",
        lambda submitted, *, required_codes=None: captured.update(
            required_codes=required_codes
        ),
    )

    driver.verify_saved_record(
        [save],
        save,
        {"name": "本次新增"},
        ("本次新增",),
        required_codes={"name"},
    )

    assert captured == {"required_codes": {"name"}}
    assert events == [("edit",), ("wait", 1_500)]


def test_verify_saved_record_accepts_current_list_readback(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow(
            ["AUTO_项目_001", "第一行\n  第二行"],
            **{"data-row-key": "record-1"},
        )
    ])
    driver.source_fields = [
        ("projName", "项目名称", False),
        ("riskAnalysis", "请输入潜在风险分析", False),
    ]
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    monkeypatch.setattr(driver, "_assert_business_success", lambda body, operation="新增": None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None)
    monkeypatch.setattr(
        driver,
        "_open_detail",
        lambda *_args: (_ for _ in ()).throw(AssertionError("不应再打开详情")),
    )

    result = driver.verify_saved_record(
        [save],
        save,
        {"projName": "AUTO_项目_001", "riskAnalysis": "第一行\n  第二行"},
        ("AUTO_项目_001",),
        required_codes={"projName", "riskAnalysis"},
    )

    assert result.mode == "add_and_list_verified"
    assert result.detail_url == "https://host/projects"
    assert result.record_identity_payload == {"code": 200, "data": {"id": "record-1"}}


def test_successful_automation_create_is_registered_before_readback_failure(
    monkeypatch, tmp_path,
):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/projects/detail/record-1"})()
    driver.source_fields = [("name", "名称", False)]
    driver._collection_submission_codes = set()
    driver._submitted_display_values = {}
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    save = JsonResponse(
        "https://host/api/projects/add",
        {"code": 200, "data": {"id": "record-1", "name": "AUTO_项目"}},
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        driver,
        "_try_current_page_list_readback",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        driver, "_find_associated_detail_response", lambda *_args: None
    )
    monkeypatch.setattr(
        driver, "_can_uniquely_locate_current_record", lambda *_args: True
    )
    monkeypatch.setattr(
        driver,
        "_open_detail",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("保存后回读失败")
        ),
    )

    with pytest.raises(AssertionError, match="保存后回读失败"):
        driver.verify_saved_record(
            [save],
            save,
            {"name": "AUTO_项目"},
            ("AUTO_项目",),
            created_by_automation=True,
            automation_registry_scope="https://host/projects?tab=active",
        )

    registry = module_driver_module.json.loads(
        driver.automation_record_registry.read_text(encoding="utf-8")
    )
    assert registry["records"] == [
        {
            "business_id": "record-1",
            "page_scope": "https://host/projects",
            "record_markers": ["AUTO_项目"],
            "submitted": {"name": "AUTO_项目"},
            "record_identity_payload": {
                "id": "record-1",
                "name": "AUTO_项目",
            },
        }
    ]


def test_edit_save_with_business_id_is_not_registered(monkeypatch, tmp_path):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/projects/detail/record-1"})()
    driver.source_fields = [("name", "名称", False)]
    driver._collection_submission_codes = set()
    driver._submitted_display_values = {}
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    save = JsonResponse(
        "https://host/api/projects/update",
        {"code": 200, "data": {"id": "record-1", "name": "普通业务记录"}},
    )
    expected = ModuleSmokeResult(mode="add_edit_and_detail_verified")
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        driver,
        "_verify_saved_record_in_edit_and_detail",
        lambda *_args, **_kwargs: expected,
    )

    result = driver.verify_saved_record(
        [save],
        save,
        {"name": "普通业务记录"},
        (),
        saved_from_current_detail_edit=True,
    )

    assert result is expected
    assert not driver.automation_record_registry.exists()


def test_registry_keeps_same_business_id_in_different_page_scopes(tmp_path):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/projects"})()
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver._submitted_display_values = {}
    result = ModuleSmokeResult(
        mode="automation_create_succeeded",
        business_id="record-1",
        submitted={"name": "AUTO_项目"},
        record_markers=("AUTO_项目",),
        record_identity_payload={"data": {"id": "record-1", "name": "AUTO_项目"}},
    )

    driver._remember_automation_owned_record(
        result, page_scope="https://host/projects"
    )
    driver._remember_automation_owned_record(
        result, page_scope="https://host/investors"
    )

    registry = module_driver_module.json.loads(
        driver.automation_record_registry.read_text(encoding="utf-8")
    )
    assert [record["page_scope"] for record in registry["records"]] == [
        "https://host/investors",
        "https://host/projects",
    ]


def test_forget_registered_record_only_removes_current_page_scope(tmp_path):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/projects"})()
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver.automation_record_registry.write_text(
        '{"records":['
        '{"business_id":"record-1","page_scope":"https://host/projects"},'
        '{"business_id":"record-1","page_scope":"https://host/investors"}'
        "]}",
        encoding="utf-8",
    )

    driver._forget_automation_owned_record("record-1")

    registry = module_driver_module.json.loads(
        driver.automation_record_registry.read_text(encoding="utf-8")
    )
    assert registry["records"] == [
        {"business_id": "record-1", "page_scope": "https://host/investors"}
    ]


def test_verify_saved_record_preserves_parent_detail_when_edit_response_id_is_child(
    monkeypatch,
):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type(
        "Page",
        (),
        {"url": "https://host/fi-view/#/buildProjects/detail?id=parent-1"},
    )()
    save = JsonResponse(
        "https://host/fi-service/projectStage/update",
        {"code": 200, "data": {"id": "child-1"}},
    )
    expected = module_driver_module.ModuleSmokeResult(
        mode="add_edit_and_detail_verified",
        business_id="child-1",
    )
    captured = {}
    monkeypatch.setattr(
        driver, "_assert_business_success", lambda body, operation="保存": None
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None
    )
    monkeypatch.setattr(
        driver,
        "_verify_saved_record_in_edit_and_detail",
        lambda responses, save_response, submitted, markers, business_id, **options: (
            captured.update(
                business_id=business_id,
                page_url=driver.page.url,
                required_codes=options["required_codes"],
            )
            or expected
        ),
    )
    monkeypatch.setattr(
        driver,
        "_open_detail",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("编辑保存后不应返回父列表查找 child-id")
        ),
    )

    result = driver.verify_saved_record(
        [save],
        save,
        {"projName": "AUTO_项目"},
        ("AUTO_项目",),
        required_codes={"projName"},
        saved_from_current_detail_edit=True,
    )

    assert result is expected
    assert captured == {
        "business_id": "child-1",
        "page_url": "https://host/fi-view/#/buildProjects/detail?id=parent-1",
        "required_codes": {"projName"},
    }


def test_verify_saved_record_allows_empty_edit_response_only_for_detail_readback(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    save = JsonResponse(
        "https://host/fi-service/projectStage/update",
        {"status": "0", "msg": "操作成功", "data": None, "errors": []},
    )
    expected = module_driver_module.ModuleSmokeResult(
        mode="add_edit_and_detail_verified",
        business_id="",
    )
    captured = {}
    monkeypatch.setattr(
        driver, "_assert_business_success", lambda body, operation="保存": None
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None
    )
    monkeypatch.setattr(
        driver,
        "_verify_saved_record_in_edit_and_detail",
        lambda responses, save_response, submitted, markers, business_id, **options: (
            captured.update(
                business_id=business_id,
                markers=markers,
                required_codes=options["required_codes"],
            )
            or expected
        ),
    )

    result = driver.verify_saved_record(
        [save],
        save,
        {"file": "AUTO_EDIT_ATTACHMENT.pdf"},
        (),
        required_codes={"file"},
        saved_from_current_detail_edit=True,
    )

    assert result is expected
    assert captured == {
        "business_id": "",
        "markers": (),
        "required_codes": {"file"},
    }


def test_verify_saved_record_preserves_explicit_empty_readback_set_for_attachment(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    save = JsonResponse(
        "https://host/fi-service/projectStage/update",
        {"status": "0", "msg": "操作成功", "data": None, "errors": []},
    )
    captured = {}
    monkeypatch.setattr(
        driver, "_assert_business_success", lambda body, operation="保存": None
    )
    monkeypatch.setattr(
        driver,
        "_assert_nested_values_in_payload",
        lambda payload, **_kwargs: None,
    )
    monkeypatch.setattr(
        driver,
        "_verify_saved_record_in_edit_and_detail",
        lambda responses, save_response, submitted, markers, business_id, **options: (
            captured.update(required_codes=options["required_codes"])
            or module_driver_module.ModuleSmokeResult(
                mode="add_edit_and_detail_verified"
            )
        ),
    )

    driver.verify_saved_record(
        [save],
        save,
        {},
        (),
        required_codes=set(),
        saved_from_current_detail_edit=True,
    )

    assert captured == {"required_codes": set()}


def test_verify_saved_record_filters_unmapped_generated_required_codes_at_entry(
    monkeypatch,
):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("matterName", "事项名称", False)]
    save = JsonResponse(
        "https://host/fi-service/projectStage/update",
        {"status": "0", "msg": "操作成功", "data": None, "errors": []},
    )
    captured = {}
    monkeypatch.setattr(
        driver, "_assert_business_success", lambda body, operation="保存": None
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None
    )
    monkeypatch.setattr(
        driver,
        "_verify_saved_record_in_edit_and_detail",
        lambda responses, save_response, submitted, markers, business_id, **options: (
            captured.update(required_codes=options["required_codes"])
            or module_driver_module.ModuleSmokeResult(
                mode="add_edit_and_detail_verified"
            )
        ),
    )

    driver.verify_saved_record(
        [save],
        save,
        {"matterName": "AUTO_事项", "el-id-123-45": "临时值"},
        (),
        required_codes={"matterName", "el-id-123-45"},
        saved_from_current_detail_edit=True,
    )

    assert captured == {"required_codes": {"matterName"}}


def test_verify_saved_record_rejects_empty_response_without_edit_detail_context(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    save = JsonResponse(
        "https://host/fi-service/projectStage/update",
        {"status": "0", "msg": "操作成功", "data": None, "errors": []},
    )
    monkeypatch.setattr(
        driver, "_assert_business_success", lambda body, operation="保存": None
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None
    )

    with pytest.raises(AssertionError, match="保存接口未返回业务主键"):
        driver.verify_saved_record([save], save, {"file": "attachment.pdf"}, ())


def test_readback_label_strips_prompt_prefixes():
    assert ModuleSmokeDriver._readback_label("请输入潜在风险分析") == "潜在风险分析"
    assert ModuleSmokeDriver._readback_label("请填写项目基本情况：") == "项目基本情况"


class RenderedDetailPage:
    def __init__(self, evidence):
        self.evidence = evidence
        self.calls = []

    def evaluate(self, script, items):
        self.calls.append((script, items))
        return self.evidence


class DelayedRenderedDetailPage(RenderedDetailPage):
    def __init__(self, evidence_sequence):
        super().__init__(evidence_sequence[-1])
        self.evidence_sequence = list(evidence_sequence)
        self.waits = []

    def evaluate(self, script, items):
        self.calls.append((script, items))
        if len(self.evidence_sequence) > 1:
            return self.evidence_sequence.pop(0)
        return self.evidence_sequence[0]

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


def test_rendered_detail_text_accepts_exact_visible_whitespace():
    page = RenderedDetailPage([{
        "code": "summary",
        "label": "项目基本情况",
        "matched": True,
        "labelFound": True,
        "actual": "第一行\n  第二行",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "summary": ("项目基本情况", "第一行\n  第二行"),
    })

    assert page.calls[0][1] == [{
        "code": "summary",
        "label": "项目基本情况",
        "expected": "第一行\n  第二行",
    }]
    assert "innerText" in page.calls[0][0]


def test_rendered_detail_text_waits_for_async_detail_render():
    page = DelayedRenderedDetailPage([
        [{
            "code": "name",
            "label": "项目名称",
            "matched": False,
            "labelFound": False,
            "actual": "",
        }],
        [{
            "code": "name",
            "label": "项目名称",
            "matched": True,
            "labelFound": True,
            "actual": "AUTO_项目",
        }],
    ])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "name": ("项目名称", "AUTO_项目"),
    }, all_fields=True)

    assert len(page.calls) == 2
    assert page.waits == [200]


def test_rendered_detail_text_supports_inline_label_value_nodes():
    page = RenderedDetailPage([{
        "code": "projectType",
        "label": "项目类型",
        "matched": True,
        "labelFound": True,
        "actual": "新建",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "projectType": ("项目类型", "新建"),
    })

    script = page.calls[0][0]
    assert "inlineValues" in script
    assert "span.box-2" in script
    assert ".purvar_form_item > .el-col:first-child span" in script


def test_rendered_detail_text_maps_synthetic_file_key_to_visible_label():
    page = RenderedDetailPage([{
        "code": "file:立项会议纪要",
        "label": "立项会议纪要",
        "matched": True,
        "labelFound": True,
        "actual": "meeting.jpg",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "file:立项会议纪要": ("file:立项会议纪要", "meeting.jpg"),
    })

    assert page.calls[0][1] == [{
        "code": "file:立项会议纪要",
        "label": "立项会议纪要",
        "expected": "meeting.jpg",
    }]


def test_rendered_detail_text_prefers_textarea_value_over_character_counter():
    page = RenderedDetailPage([{
        "code": "buildContent",
        "label": "建设内容",
        "matched": True,
        "labelFound": True,
        "actual": "首行\n  第二行",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "buildContent": ("建设内容", "首行\n  第二行"),
    })

    script = page.calls[0][0]
    assert ".el-textarea__count" in script
    assert ".purvar-col__content" in script
    assert script.index("textarea[readonly]") < script.index("'.el-form-item__content'")


def test_rendered_detail_text_reads_table_headers_from_same_column_cells():
    page = RenderedDetailPage([{
        "code": "financeSources.*.fundsPlan",
        "label": "资金筹措方案",
        "matched": True,
        "labelFound": True,
        "actual": "自有资金",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "financeSources.*.fundsPlan": ("资金筹措方案", "自有资金"),
    })

    script = page.calls[0][0]
    assert "tableColumnCells" in script
    assert "labelCell.tagName !== 'TH'" in script
    assert "closest('.el-table,.ant-table')" in script
    assert "|| labelCell.closest('table')" in script
    assert "rowCell?.tagName === 'TH'" in script
    assert "cell.querySelectorAll" in script
    assert "textarea,input:not([type=\"hidden\"])" in script
    assert "operationOnly" in script


def test_rendered_detail_text_accepts_numeric_thousands_display_format():
    page = RenderedDetailPage([{
        "code": "financeSources.*.amount",
        "label": "预算金额（万元）",
        "matched": False,
        "labelFound": True,
        "actual": "135,024.03",
        "values": ["135,024.03"],
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    driver._assert_rendered_detail_text({
        "financeSources.*.amount": ("预算金额（万元）", "135024.03"),
    }, all_fields=True)

    assert len(page.calls) == 1


def test_rendered_detail_text_keeps_identifier_comparison_exact():
    page = RenderedDetailPage([{
        "code": "projectCode",
        "label": "项目编号",
        "matched": False,
        "labelFound": True,
        "actual": "1",
        "values": ["1"],
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    with pytest.raises(AssertionError, match="projectCode"):
        driver._assert_rendered_detail_text({
            "projectCode": ("项目编号", "001"),
        }, all_fields=True)


def test_submitted_detail_expectations_use_display_alias_from_detail_payload():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("inveId", "实施主体公司", False)]

    expectations = driver._submitted_detail_expectations(
        {"inveId": "2049424803010793474"},
        {"inveId"},
        detail_payload={
            "data": {
                "inveId": "2049424803010793474",
                "inveName": "盛和资源控股股份有限公司",
            }
        },
    )

    assert expectations == {
        "inveId": ("实施主体公司", "盛和资源控股股份有限公司")
    }


def test_submitted_detail_expectations_use_value_bound_form_display_without_detail_payload(
    monkeypatch,
):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()
    driver.source_fields = [("inveId", "实施主体公司", False)]
    driver._submitted_display_values = {}
    scope = object()
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [
            DomField("inveId", "实施主体公司", "select", "#investor")
        ],
    )
    monkeypatch.setattr(
        driver,
        "_open_form_field_values",
        lambda dom, root: [
            "2049424803010793474",
            "盛和资源控股股份有限公司",
        ],
    )

    captured = driver._capture_submitted_display_values(
        {"inveId": "2049424803010793474"}, scope
    )
    expectations = driver._submitted_detail_expectations(
        {"inveId": "2049424803010793474"},
        {"inveId"},
    )
    changed_expectations = driver._submitted_detail_expectations(
        {"inveId": "different-id"},
        {"inveId"},
    )

    assert captured == {"inveId": "盛和资源控股股份有限公司"}
    assert expectations == {
        "inveId": ("实施主体公司", "盛和资源控股股份有限公司")
    }
    assert changed_expectations == {"inveId": ("实施主体公司", "different-id")}


def test_nested_submitted_detail_expectations_use_display_alias():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("financeSources.*.sourceFrom", "资金来源", False),
    ]

    expectations = driver._submitted_detail_expectations(
        {"financeSources.*.sourceFrom": "自有资金"},
        {"financeSources.*.sourceFrom"},
        detail_payload={
            "data": {
                "financeSources": [{
                    "sourceFrom": "1",
                    "sourceFromName": "自有资金",
                }],
            },
        },
    )

    assert expectations == {
        "financeSources.*.sourceFrom": ("资金来源", "自有资金")
    }


def test_rendered_detail_text_rejects_css_collapsed_whitespace():
    page = RenderedDetailPage([{
        "code": "summary",
        "label": "项目基本情况",
        "matched": False,
        "labelFound": True,
        "actual": "第一行 第二行",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    with pytest.raises(AssertionError, match="没有按原格式显示") as exc:
        driver._assert_rendered_detail_text({
            "summary": ("项目基本情况", "第一行\n  第二行"),
        })

    assert "rendered='第一行 第二行'" in str(exc.value)


def test_rendered_detail_text_requires_an_associated_detail_label():
    page = RenderedDetailPage([{
        "code": "summary",
        "label": "项目基本情况",
        "matched": False,
        "labelFound": False,
        "actual": "",
    }])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    with pytest.raises(AssertionError, match="未找到字段标签"):
        driver._assert_rendered_detail_text({
            "summary": ("项目基本情况", "第一行\n  第二行"),
        })


def test_verify_saved_record_checks_rendered_text_after_detail_payload(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    detail = JsonResponse(
        "https://host/detail/record-1",
        {
            "code": 200,
            "data": {"id": "record-1", "summary": "第一行\n  第二行"},
        },
    )
    rendered_calls = []
    monkeypatch.setattr(driver, "_assert_business_success", lambda body, operation="新增": None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None)
    monkeypatch.setattr(driver, "_open_detail", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        driver,
        "_find_detail_response",
        lambda responses, save_response, business_id: detail,
    )
    monkeypatch.setattr(driver, "_assert_detail_values", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        driver,
        "_assert_rendered_detail_text",
        lambda expectations: rendered_calls.append(expectations),
    )
    expectations = {"summary": ("项目基本情况", "第一行\n  第二行")}

    driver.verify_saved_record(
        [save, detail],
        save,
        {"summary": "第一行\n  第二行"},
        ("AUTO_001",),
        required_codes={"summary"},
        rendered_text_expectations=expectations,
    )

    assert rendered_calls == [expectations]


def test_edit_form_values_verify_pages_without_detail_request(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = ValuePage({"#code": "category_code", "#name": "Category name"})
    driver.source_fields = [("code", "分类编码", False), ("name", "分类名称", False)]
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: object())
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda scope: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page: [
            DomField("code", "分类编码", "text", "#code"),
            DomField("name", "分类名称", "text", "#name"),
        ],
    )

    driver._assert_open_form_values(
        {"code": "category_code", "name": "Category name"},
        required_codes={"code", "name"},
    )


def test_edit_form_values_require_every_transaction_target(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = ValuePage({"#name": "Category name"})
    driver.source_fields = [("name", "分类名称", False)]
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: object())
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda scope: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page: [DomField("name", "分类名称", "text", "#name")],
    )

    try:
        driver._assert_open_form_values(
            {"code": "category_code", "name": "Category name"},
            required_codes={"code", "name"},
        )
    except AssertionError as exc:
        assert str(exc) == "编辑表单缺少或无法比较指定回读字段：code"
    else:
        raise AssertionError("missing required edit-form field was accepted")


def test_edit_form_values_allow_explicit_empty_delegated_readback(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    scope = object()
    events = []
    monkeypatch.setattr(
        driver,
        "_wait_for_readback_form_scope",
        lambda: events.append("scope") or scope,
    )
    monkeypatch.setattr(
        driver,
        "_wait_for_form_ready",
        lambda actual: events.append(("ready", actual)),
    )
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("delegated readback must not scan ordinary fields")
        ),
    )

    driver._assert_open_form_values({}, required_codes=set())

    assert events == ["scope", ("ready", scope)]


def test_edit_form_values_without_delegation_still_require_comparable_fields(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    scope = object()
    driver.page = object()
    monkeypatch.setattr(driver, "_wait_for_readback_form_scope", lambda: scope)
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda actual: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda *_args, **_kwargs: [],
    )

    with pytest.raises(AssertionError, match="没有可核对的提交字段"):
        driver._assert_open_form_values({}, required_codes=None)


def test_edit_form_values_reject_mismatched_echo(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = ValuePage({"#name": "Wrong name"})
    driver.source_fields = [("name", "分类名称", False)]
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: object())
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda scope: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page: [DomField("name", "分类名称", "text", "#name")],
    )

    try:
        driver._assert_open_form_values({"name": "Expected name"})
    except AssertionError as exc:
        assert "name" in str(exc)
    else:
        raise AssertionError("mismatched edit-form value was accepted")


def test_edit_form_values_compare_select_radio_and_year_controls(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    scope = FormValueScope({
        "#classify": "新建项目",
        "#region": "境内",
        "#year": "2026",
    })
    driver.page = ValuePage({})
    driver.source_fields = [
        ("projClassify", "项目分类", False),
        ("isRegion", "境内外", False),
        ("buildYear", "建设年份", False),
    ]
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: scope)
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda actual: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [
            DomField("projClassify", "项目分类", "select", "#classify"),
            DomField("isRegion", "境内外", "radio", "#region"),
            DomField("buildYear", "建设年份", "year", "#year"),
        ],
    )

    driver._assert_open_form_values(
        {
            "projClassify": "01",
            "isRegion": "境内",
            "buildYear": "2026",
        },
        required_codes={"projClassify", "isRegion", "buildYear"},
        display_values={"projClassify": "新建项目"},
    )


def test_edit_form_values_only_compare_explicit_command_readback_fields(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    scope = FormValueScope({
        "#name": "AUTO_project",
        "#investor": "investment company",
    })
    driver.page = ValuePage({})
    driver.source_fields = [
        ("projName", "project name", False),
        ("inveId", "investment company", False),
    ]
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: scope)
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda actual: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [
            DomField("projName", "project name", "text", "#name"),
            DomField("inveId", "investment company", "select", "#investor"),
        ],
    )

    driver._assert_open_form_values(
        {"projName": "AUTO_project", "inveId": "2049424803010793474"},
        required_codes={"projName"},
    )


def test_fieldless_detail_response_defers_to_rendered_readback(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    response = object()
    monkeypatch.setattr(
        driver,
        "_assert_detail_response_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("详情接口未返回任何本次提交字段，无法核对保存结果")
        ),
    )

    assert driver._detail_response_readback_or_fallback(
        response,
        {"projName": "AUTO_project"},
        required_codes={"projName"},
        business_id="record-1",
        record_markers=("AUTO_project",),
        save_payload={},
    ) is None


def test_partial_associated_list_defers_when_target_fields_are_missing(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    response = JsonResponse(
        "https://host/api/project/list",
        {"code": 200, "data": [{"id": "record-1", "name": "本次新增"}]},
    )
    monkeypatch.setattr(
        driver, "_assert_nested_values_in_payload", lambda *args, **kwargs: None
    )

    assert driver._detail_response_readback_or_fallback(
        response,
        {"name": "本次新增", "buildContent": "建设内容"},
        required_codes={"name", "buildContent"},
        business_id="record-1",
        record_markers=("本次新增",),
        save_payload={},
    ) is None


def test_same_resource_detail_url_keeps_resource_prefix_and_exact_id():
    assert ModuleSmokeDriver._same_resource_detail_url(
        "https://host/fi-service/risk/add?draft=true",
        "record/1",
    ) == "https://host/fi-service/risk/detail/record%2F1"


def test_same_resource_detail_request_accepts_successful_json_with_exact_id():
    driver = object.__new__(ModuleSmokeDriver)
    api_response = ApiResponse(
        "https://host/fi-service/risk/detail/record-1",
        {"code": 200, "data": {"id": "record-1", "name": "AUTO_risk"}},
    )
    driver.page = RequestPage([api_response])
    save = JsonResponse(
        "https://host/fi-service/risk/add",
        {"code": 200, "data": {"id": "record-1"}},
    )

    result = driver._request_same_resource_detail_response(save, "record-1")

    assert result is not None
    assert result.json()["data"]["id"] == "record-1"
    assert driver.page.request.urls == [
        "https://host/fi-service/risk/detail/record-1"
    ]
    assert ModuleSmokeDriver._request_contains_business_id(
        result.request, "record-1"
    )


@pytest.mark.parametrize(
    "response",
    [
        ApiResponse(
            "https://host/fi-service/risk/detail/record-1",
            {"code": 404},
            ok=False,
            status=404,
        ),
        ApiResponse(
            "https://host/fi-service/risk/detail/record-1",
            "not json",
            content_type="text/html",
        ),
        ApiResponse(
            "https://host/fi-service/risk/detail/record-1",
            None,
            json_error=ValueError("invalid JSON"),
        ),
        ApiResponse(
            "https://host/fi-service/risk/detail/record-1",
            {"code": 200, "data": {"name": "missing id"}},
        ),
        ApiResponse(
            "https://host/fi-service/risk/detail/record-1",
            {"code": 200, "data": {"id": "record-2"}},
        ),
    ],
)
def test_same_resource_detail_request_rejects_invalid_or_unassociated_response(response):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RequestPage([response])
    save = JsonResponse(
        "https://host/fi-service/risk/add",
        {"code": 200, "data": {"id": "record-1"}},
    )

    assert driver._request_same_resource_detail_response(save, "record-1") is None


def test_saved_record_uses_same_resource_detail_only_after_exact_dom_miss(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    api_response = ApiResponse(
        "https://host/fi-service/risk/detail/record-1",
        {
            "code": 200,
            "data": {
                "id": "record-1",
                "name": "AUTO_risk",
                "riskReason": "已保存",
            },
        },
    )
    driver.page = RequestPage([api_response])
    driver.source_fields = [
        ("name", "名称", False),
        ("riskReason", "风险原因", False),
    ]
    driver._nested_evidence = []
    save = JsonResponse(
        "https://host/fi-service/risk/add",
        {"code": 200, "data": {"id": "record-1"}},
    )
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_try_current_page_list_readback", lambda *args, **kwargs: False)
    monkeypatch.setattr(driver, "_find_associated_detail_response", lambda *args: None)
    monkeypatch.setattr(driver, "_can_uniquely_locate_current_record", lambda *args: False)

    result = driver.verify_saved_record(
        [save],
        save,
        {"name": "AUTO_risk", "riskReason": "已保存"},
        ("AUTO_risk",),
        required_codes={"name", "riskReason"},
    )

    assert result.mode == "add_and_detail_verified"
    assert result.detail_url.endswith("/risk/detail/record-1")


def test_saved_record_same_resource_detail_rejects_field_mismatch(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RequestPage([
        ApiResponse(
            "https://host/fi-service/risk/detail/record-1",
            {
                "code": 200,
                "data": {"id": "record-1", "name": "wrong record"},
            },
        )
    ])
    driver.source_fields = [("name", "名称", False)]
    driver._nested_evidence = []
    save = JsonResponse(
        "https://host/fi-service/risk/add",
        {"code": 200, "data": {"id": "record-1"}},
    )
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_try_current_page_list_readback", lambda *args, **kwargs: False)
    monkeypatch.setattr(driver, "_find_associated_detail_response", lambda *args: None)
    monkeypatch.setattr(driver, "_can_uniquely_locate_current_record", lambda *args: False)

    with pytest.raises(AssertionError, match="详情接口数据与本次提交不一致"):
        driver.verify_saved_record(
            [save],
            save,
            {"name": "AUTO_risk"},
            ("AUTO_risk",),
            required_codes={"name"},
        )


def test_saved_record_uses_partial_associated_list_to_open_exact_record(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/parent/detail?id=parent-1"})()
    driver._nested_evidence = []
    save = JsonResponse(
        "https://host/api/risk/add",
        {"code": 200, "data": {"id": "child-1"}},
    )
    child_list = JsonResponse(
        "https://host/api/risk/listPage",
        {
            "code": 200,
            "data": {
                "records": [
                    {"id": "child-1", "riskSummary": "本次风险概况"}
                ]
            },
        },
    )
    opened = []
    monkeypatch.setattr(driver, "_assert_business_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_try_current_page_list_readback", lambda *args, **kwargs: False)
    monkeypatch.setattr(driver, "_find_associated_detail_response", lambda *args: child_list)
    monkeypatch.setattr(driver, "_detail_response_readback_or_fallback", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        driver,
        "_open_detail",
        lambda markers, business_id, **kwargs: opened.append(
            (markers, business_id, kwargs.get("response_payload"))
        ),
    )
    monkeypatch.setattr(driver, "_find_detail_response", lambda *args: None)
    monkeypatch.setattr(driver, "_open_current_detail_edit_for_readback", lambda markers: True)
    monkeypatch.setattr(driver, "_assert_open_form_values", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_assert_nested_values_in_open_form", lambda: None)
    monkeypatch.setattr(driver, "_assert_rendered_detail_text", lambda *args, **kwargs: None)

    result = driver.verify_saved_record(
        [save, child_list],
        save,
        {"riskSummary": "本次风险概况", "riskReason": "接口列表未返回"},
        (),
        required_codes={"riskSummary", "riskReason"},
    )

    assert result.mode == "add_and_edit_form_verified"
    assert opened == [([], "child-1", child_list.json())]


def test_partial_associated_list_defers_when_nested_rows_are_absent(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    response = object()
    monkeypatch.setattr(
        driver,
        "_assert_detail_response_readback",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("详情响应没有持久化嵌套行字段：资金明细 / 金额=1")
        ),
    )

    assert driver._detail_response_readback_or_fallback(
        response,
        {"financeSources.*.amount": "1"},
        required_codes={"financeSources.*.amount"},
        business_id="record-1",
        record_markers=("本次新增",),
        save_payload={},
    ) is None


def test_edit_form_values_reject_missing_non_text_field(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    scope = FormValueScope({"#name": "AUTO_项目"})
    driver.page = ValuePage({})
    driver.source_fields = [("projName", "项目名称", False)]
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: scope)
    monkeypatch.setattr(driver, "_wait_for_form_ready", lambda actual: None)
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [DomField("projName", "项目名称", "text", "#name")],
    )

    with pytest.raises(AssertionError, match="projClassify"):
        driver._assert_open_form_values(
            {"projName": "AUTO_项目", "projClassify": "新建项目"},
            required_codes={"projName", "projClassify"},
        )


def test_saved_record_double_readback_runs_detail_then_edit(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {"url": "https://host/projects"})()
    driver.source_fields = [("name", "项目名称", False), ("amount", "金额", False)]
    driver._nested_evidence = []
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    detail = JsonResponse(
        "https://host/detail/record-1",
        {"code": 200, "data": {"id": "record-1", "name": "AUTO_项目", "amount": "100"}},
    )
    events = []
    monkeypatch.setattr(
        driver, "_open_record_action",
        lambda markers, business_id, **options: events.append(("open", options["action_names"])),
    )
    monkeypatch.setattr(driver, "_current_detail_edit_button", lambda markers: None)
    monkeypatch.setattr(
        driver, "_find_detail_response", lambda responses, save_response, business_id: detail,
    )
    monkeypatch.setattr(driver, "_assert_business_success", lambda body, operation="新增": None)
    monkeypatch.setattr(driver, "_assert_detail_values", lambda *args, **kwargs: events.append(("detail-api", kwargs["required_codes"])))
    monkeypatch.setattr(driver, "_assert_nested_values_in_payload", lambda payload, **_kwargs: None)
    monkeypatch.setattr(
        driver, "_assert_rendered_detail_text",
        lambda expectations, *, all_fields=False: events.append(("detail-page", set(expectations), all_fields)),
    )
    monkeypatch.setattr(driver, "_return_to_record_list", lambda url: events.append(("list", url)))
    monkeypatch.setattr(
        driver, "_assert_open_form_values",
        lambda submitted, *, required_codes=None, display_values=None: events.append(
            ("edit-page", required_codes)
        ),
    )
    monkeypatch.setattr(driver, "_assert_nested_values_in_open_form", lambda: None)

    result = driver._verify_saved_record_in_edit_and_detail(
        [save, detail],
        save,
        {"name": "AUTO_项目", "amount": "100"},
        ("AUTO_项目",),
        "record-1",
        required_codes={"name", "amount"},
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert events == [
        ("detail-api", {"name", "amount"}),
        ("open", ("查看", "详情")),
        ("detail-page", {"name", "amount"}, True),
        ("list", "https://host/projects"),
        ("open", ("编辑",)),
        ("edit-page", {"name", "amount"}),
    ]


def test_saved_record_on_detail_route_can_use_embedded_record_row(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "url": "https://host/fi-view/#/buildProjects/detail?id=parent-1",
        "wait_for_timeout": lambda self, timeout: events.append(("wait", timeout)),
    })()
    driver.source_fields = [("name", "项目名称", False), ("amount", "金额", False)]
    driver._nested_evidence = []
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    events = []
    monkeypatch.setattr(driver, "_current_detail_edit_button", lambda markers: None)
    monkeypatch.setattr(driver, "_detail_route_has_current_record", lambda business_id, markers: True)
    monkeypatch.setattr(
        driver,
        "_open_record_action",
        lambda markers, business_id, **options: events.append(
            ("open", options["action_names"], options["allow_row_click"])
        ),
    )
    monkeypatch.setattr(driver, "_find_detail_response", lambda *args: None)
    monkeypatch.setattr(
        driver,
        "_assert_rendered_detail_text",
        lambda expectations, *, all_fields=False: events.append(("detail-page", set(expectations), all_fields)),
    )
    monkeypatch.setattr(driver, "_return_to_record_list", lambda url: events.append(("list", url)))
    monkeypatch.setattr(
        driver,
        "_assert_open_form_values",
        lambda submitted, *, required_codes=None, display_values=None: events.append(
            ("edit-page", required_codes)
        ),
    )
    monkeypatch.setattr(driver, "_assert_nested_values_in_open_form", lambda: None)

    result = driver._verify_saved_record_in_edit_and_detail(
        [save],
        save,
        {"name": "AUTO_项目", "amount": "100"},
        ("AUTO_项目",),
        "record-1",
        required_codes={"name", "amount"},
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert events == [
        ("open", ("查看", "详情"), True),
        ("detail-page", {"name", "amount"}, True),
        ("list", "https://host/fi-view/#/buildProjects/detail?id=parent-1"),
        ("open", ("编辑",), False),
        ("edit-page", {"name", "amount"}),
    ]


def test_double_readback_uses_partial_associated_list_to_open_embedded_edit(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "url": "https://host/fi-view/#/buildProjects/detail?id=parent-1",
        "wait_for_timeout": lambda self, timeout: None,
    })()
    driver.source_fields = [
        ("riskSummary", "风险概况", False),
        ("riskReason", "发生原因", False),
    ]
    driver._nested_evidence = []
    save = JsonResponse(
        "https://host/api/risk/add",
        {"code": 200, "data": {"id": "child-1"}},
    )
    child_list = JsonResponse(
        "https://host/api/risk/listPage",
        {
            "code": 200,
            "data": {
                "records": [
                    {"id": "child-1", "riskSummary": "本次风险概况"}
                ]
            },
        },
    )
    container = object()
    events = []
    monkeypatch.setattr(driver, "_find_associated_detail_response", lambda *args: child_list)
    monkeypatch.setattr(driver, "_detail_response_readback_or_fallback", lambda *args, **kwargs: None)
    monkeypatch.setattr(driver, "_current_detail_edit_button", lambda markers: None)
    monkeypatch.setattr(driver, "_detail_route_has_current_record", lambda *args: False)
    monkeypatch.setattr(
        driver,
        "_open_record_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("DOM 没有子记录 ID")),
    )
    monkeypatch.setattr(
        driver,
        "_find_response_associated_record_container",
        lambda payload, business_id, **options: (
            events.append((
                "associate", payload, business_id, options["display_field_codes"],
            )) or (container, "响应关联")
        ),
    )
    monkeypatch.setattr(
        driver,
        "_open_record_container_action",
        lambda actual, identity, markers, **kwargs: events.append(
            ("open-embedded", actual, identity, kwargs["action_names"])
        ),
    )
    monkeypatch.setattr(driver, "_find_detail_response", lambda *args: None)
    monkeypatch.setattr(driver, "_assert_open_form_values", lambda *args, **kwargs: events.append(("edit-readback", kwargs["required_codes"])))
    monkeypatch.setattr(driver, "_assert_nested_values_in_open_form", lambda: None)

    result = driver._verify_saved_record_in_edit_and_detail(
        [save, child_list],
        save,
        {"riskSummary": "本次风险概况", "riskReason": "发生原因内容"},
        (),
        "child-1",
        required_codes={"riskSummary", "riskReason"},
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert events == [
        (
            "associate",
            child_list.json(),
            "child-1",
            ("riskSummary", "riskReason"),
        ),
        ("open-embedded", container, "响应关联", ("编辑", "修改")),
        ("edit-readback", {"riskSummary", "riskReason"}),
    ]


def test_inline_form_scope_includes_detail_base_info_edit_form():
    assert ".base-info-page:visible .el-form:visible" in INLINE_FORM


def test_form_scope_prefers_inline_candidate_with_editable_controls():
    class Controls:
        def __init__(self, count):
            self._count = count
            self.first = self

        def count(self):
            return self._count

        def is_visible(self):
            return bool(self._count)

    class Scope:
        def __init__(self, name, editable_count):
            self.name = name
            self.editable_count = editable_count

        def is_visible(self):
            return True

        def locator(self, selector):
            assert selector == EDITABLE_FORM_CONTROL
            return Controls(self.editable_count)

    class Candidates:
        def __init__(self, scopes):
            self.scopes = scopes
            self.last = scopes[-1]

        def count(self):
            return len(self.scopes)

        def nth(self, index):
            return self.scopes[index]

    class HiddenDialog:
        @property
        def last(self):
            return self

        def wait_for(self, **_kwargs):
            raise TimeoutError("dialog is not visible")

    editable = Scope("real-edit-form", 4)
    attachment_or_readonly = Scope("attachment-form", 0)
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "locator": lambda self, selector: (
            HiddenDialog()
            if selector == module_driver_module.DIALOG
            else Candidates([editable, attachment_or_readonly])
        ),
        "wait_for_timeout": lambda self, timeout: None,
    })()

    assert driver._wait_for_form_scope() is editable


def test_form_scope_rejects_visible_dialog_without_editable_controls():
    class Controls:
        first = None

        @staticmethod
        def count():
            return 0

    class Dialog:
        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def locator(selector):
            assert selector == EDITABLE_FORM_CONTROL
            return Controls()

    class Candidates:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "locator": lambda self, selector: Candidates(
            [Dialog()] if selector == module_driver_module.DIALOG else []
        ),
        "wait_for_timeout": lambda self, _timeout: None,
    })()

    with pytest.raises(AssertionError, match="\u65b0\u589e\u5f39\u7a97\u5df2\u51fa\u73b0"):
        driver._wait_for_form_scope(timeout=0)


def test_readback_form_scope_prefers_candidate_with_editable_controls(monkeypatch):
    class Controls:
        def __init__(self, count):
            self._count = count

        @property
        def first(self):
            return self

        def count(self):
            return self._count

        def is_visible(self):
            return bool(self._count)

    class Scope:
        def __init__(self, editable_count):
            self.editable_count = editable_count

        def locator(self, selector):
            assert selector == EDITABLE_FORM_CONTROL
            return Controls(self.editable_count)

    class Candidates:
        def __init__(self, scopes):
            self.scopes = scopes

        def count(self):
            return len(self.scopes)

        def nth(self, index):
            return self.scopes[index]

    readonly = Scope(0)
    editable = Scope(9)
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "locator": lambda self, selector: Candidates([editable, readonly]),
        "wait_for_timeout": lambda self, timeout: None,
    })()
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: readonly)

    assert driver._wait_for_readback_form_scope() is editable


class _ReorderingControls:
    def __init__(self, count):
        self._count = count
        self.first = self

    def count(self):
        return self._count

    def is_visible(self):
        return bool(self._count)


class _ReorderingScope:
    def __init__(self, page, name, editable_count):
        self.page = page
        self.name = name
        self.editable_count = editable_count
        self.first = self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def locator(self, selector):
        assert selector == EDITABLE_FORM_CONTROL
        return _ReorderingControls(self.editable_count)

    def evaluate(self, _script, payload):
        self.page.markers[payload["marker"]] = self
        if not self.page.reordered:
            self.page.scopes.reverse()
            self.page.reordered = True


class _PositionalScope:
    def __init__(self, page, index):
        self.page = page
        self.index = index

    @property
    def current(self):
        return self.page.scopes[self.index]

    def is_visible(self):
        return self.current.is_visible()

    def locator(self, selector):
        return self.current.locator(selector)

    def evaluate(self, script, payload):
        return self.current.evaluate(script, payload)


class _ReorderingCandidates:
    def __init__(self, page):
        self.page = page
        self.last = _PositionalScope(page, len(page.scopes) - 1)

    def count(self):
        return len(self.page.scopes)

    def nth(self, index):
        return _PositionalScope(self.page, index)


class _HiddenDialog:
    @property
    def last(self):
        return self

    def wait_for(self, **_kwargs):
        raise TimeoutError("dialog is not visible")


class _ReorderingPage:
    def __init__(self, definitions):
        self.markers = {}
        self.reordered = False
        self.scopes = [
            _ReorderingScope(self, name, editable_count)
            for name, editable_count in definitions
        ]

    def locator(self, selector):
        if selector == module_driver_module.DIALOG:
            return _HiddenDialog()
        if selector.startswith('[data-ei-module-form-scope="'):
            marker = selector.split('"')[1]
            return self.markers[marker]
        return _ReorderingCandidates(self)

    def wait_for_timeout(self, _timeout):
        pass


def test_form_scope_pins_inline_dom_instance_before_vue_reorders_candidates():
    page = _ReorderingPage([("editable", 3), ("readonly", 0)])
    editable = page.scopes[0]
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    scope = driver._wait_for_form_scope()

    assert page.reordered
    assert scope is editable
    assert scope.name == "editable"


def test_readback_scope_pins_dom_instance_before_vue_reorders_candidates(monkeypatch):
    page = _ReorderingPage([("readonly", 0), ("editable", 4)])
    readonly, editable = page.scopes
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page
    monkeypatch.setattr(driver, "_wait_for_form_scope", lambda: readonly)

    scope = driver._wait_for_readback_form_scope()

    assert page.reordered
    assert scope is editable
    assert scope.locator(EDITABLE_FORM_CONTROL).count() == 4


def test_saved_record_uses_current_detail_page_and_its_edit_button(monkeypatch):
    class EditButton:
        @staticmethod
        def click():
            events.append(("click-current-edit",))

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "url": "https://host/projects/record-1",
        "wait_for_timeout": lambda self, timeout: events.append(("wait", timeout)),
    })()
    driver.source_fields = [("name", "项目名称", False), ("amount", "金额", False)]
    driver._nested_evidence = []
    save = JsonResponse("https://host/add", {"code": 200, "data": {"id": "record-1"}})
    events = []
    monkeypatch.setattr(driver, "_current_detail_edit_button", lambda markers: EditButton())
    monkeypatch.setattr(
        driver, "_open_record_action",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应返回列表找详情")),
    )
    monkeypatch.setattr(driver, "_find_detail_response", lambda *args: None)
    monkeypatch.setattr(
        driver, "_assert_rendered_detail_text",
        lambda expectations, *, all_fields=False: events.append(("detail-page", set(expectations), all_fields)),
    )
    monkeypatch.setattr(
        driver, "_return_to_record_list",
        lambda url: (_ for _ in ()).throw(AssertionError("不应返回列表找编辑")),
    )
    monkeypatch.setattr(
        driver, "_assert_open_form_values",
        lambda submitted, *, required_codes=None, display_values=None: events.append(
            ("edit-page", required_codes)
        ),
    )
    monkeypatch.setattr(driver, "_assert_nested_values_in_open_form", lambda: None)

    result = driver._verify_saved_record_in_edit_and_detail(
        [save],
        save,
        {"name": "AUTO_项目", "amount": "100"},
        ("AUTO_项目",),
        "record-1",
        required_codes={"name", "amount"},
    )

    assert result.mode == "add_edit_and_detail_verified"
    assert result.detail_url == "https://host/projects/record-1"
    assert events == [
        ("detail-page", {"name", "amount"}, True),
        ("click-current-edit",),
        ("wait", 1_500),
        ("edit-page", {"name", "amount"}),
    ]


def test_current_detail_edit_requires_marker_and_edit_outside_list_rows():
    class Element:
        def __init__(self, *, outside_list, enabled=True):
            self.outside_list = outside_list
            self.enabled = enabled

        @staticmethod
        def is_visible():
            return True

        def is_enabled(self):
            return self.enabled

        def evaluate(self, _script):
            return self.outside_list

    class Collection:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

        def filter(self, **_kwargs):
            return self

    detail_edit = Element(outside_list=True)
    page = type("Page", (), {
        "url": "https://host/projects",
        "locator": lambda self, selector: Collection([detail_edit]),
        "get_by_text": lambda self, text, exact: Collection([Element(outside_list=True)]),
    })()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    assert driver._current_detail_edit_button(["AUTO_项目"]) is detail_edit

    page.get_by_text = lambda text, exact: Collection([Element(outside_list=False)])
    assert driver._current_detail_edit_button(["AUTO_项目"]) is None


def test_current_detail_edit_accepts_single_unmarked_edit_on_detail_route():
    class Element:
        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def evaluate(_script):
            return True

    class Collection:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

        def filter(self, **_kwargs):
            return self

    edit = Element()
    page = type("Page", (), {
        "url": "https://host/fi-view/#/buildProjects/detail?id=1",
        "locator": lambda self, selector: Collection([edit]),
        "get_by_text": lambda self, text, exact: Collection([]),
    })()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page

    assert driver._current_detail_edit_button(["AUTO_项目"]) is edit


def test_open_record_action_uses_record_name_link_as_detail_entry(monkeypatch):
    class EmptyAction:
        first = None

        def __init__(self):
            self.first = self

        @staticmethod
        def count():
            return 0

    class RecordNameLink:
        def __init__(self):
            self.clicks = 0

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def inner_text():
            return "AUTO_项目_S001-事项名称"

        @staticmethod
        def get_attribute(name):
            return None

        def click(self):
            self.clicks += 1

    class Actions:
        def __init__(self, action):
            self.action = action

        @staticmethod
        def count():
            return 1

        def nth(self, index):
            assert index == 0
            return self.action

    class Container:
        def __init__(self, link):
            self.link = link

        @staticmethod
        def get_by_role(role, name, exact):
            return EmptyAction()

        def locator(self, selector):
            assert "a:visible" in selector
            return Actions(self.link)

        @staticmethod
        def get_attribute(name):
            return ""

    link = RecordNameLink()
    container = Container(link)
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "wait_for_timeout": lambda self, timeout: None,
    })()
    monkeypatch.setattr(
        driver,
        "_find_unique_record_container",
        lambda business_id, markers: (container, "AUTO_项目_S001"),
    )

    driver._open_record_action(
        ["AUTO_项目_S001"],
        "record-1",
        action_names=("查看", "详情"),
        allow_row_click=True,
    )

    assert link.clicks == 1


def test_stable_readback_required_codes_skip_generated_runtime_ids():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("matterName", "事项名称", False)]
    submitted = {
        "matterName": "AUTO_项目",
        "el-id-123-45": "1",
        "预算及资金来源明细子表.el-id-123-46": "自有资金",
        "buildContent": "建设内容",
    }

    assert driver._stable_readback_required_codes(
        submitted,
        set(submitted),
    ) == {"matterName", "buildContent"}


def test_stable_readback_required_codes_skip_generated_ids_even_in_source_fields():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("el-id-123-45", "临时字段", False)]

    assert driver._stable_readback_required_codes(
        {"el-id-123-45": "临时值"},
        {"el-id-123-45"},
    ) == set()


def test_detail_route_detection_distinguishes_list_and_detail_urls():
    assert ModuleSmokeDriver._url_is_detail_page(
        "http://host/fi-view/#/buildProjects/detail?id=record-1"
    )
    assert not ModuleSmokeDriver._url_is_detail_page(
        "http://host/fi-view/#/buildProject"
    )


def test_detail_values_accept_display_names_for_code_and_id_fields():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": [{
                "projClassify": "1",
                "projClassifyName": "new project",
                "belongSection": "1",
                "belongSectionName": "document section",
                "inveId": "2049424803010793474",
                "inveName": "investment company",
            }]
        },
        {
            "projClassify": "new project",
            "belongSection": "document section",
            "inveId": "investment company",
        },
    )


def test_investor_detail_accepts_display_names_for_stored_codes():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": {
                "investorType": "1",
                "investorTypeName": "国有企业",
                "company": "2049424803010793474",
                "companyName": "盛和资源控股股份有限公司",
            }
        },
        {"investorType": "国有企业", "company": "盛和资源控股股份有限公司"},
    )


def test_administrator_detail_accepts_status_and_organization_names():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": {
                "registrationStatus": "1",
                "registrationStatusName": "已登记",
                "orgId": "2049",
                "orgName": "盛和资源控股股份有限公司",
            }
        },
        {"registrationStatus": "已登记", "orgId": "盛和资源控股股份有限公司"},
    )


def test_detail_skips_display_value_when_api_returns_only_numeric_id():
    ModuleSmokeDriver._assert_detail_values(
        {"data": {"orgId": "2049", "mcName": "管理人A"}},
        {"orgId": "组织机构A", "mcName": "管理人A"},
    )


def test_detail_rejects_required_display_value_when_api_returns_only_numeric_id():
    try:
        ModuleSmokeDriver._assert_detail_values(
            {"data": {"orgId": "2049", "mcName": "管理人A"}},
            {"orgId": "组织机构A", "mcName": "管理人A"},
            required_codes={"orgId"},
        )
    except AssertionError as exc:
        message = str(exc)
        assert "orgId" in message
        assert "无法比较" in message
    else:
        raise AssertionError("incomparable required detail field was accepted")


def test_file_type_detail_accepts_visible_labels_for_stored_codes():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": {
                "itemType": "1",
                "isRequired": True,
                "editAble": "1",
                "needIntelligent": "0",
            }
        },
        {
            "itemType": "功能",
            "isRequired": "是",
            "editAble": "只读",
            "needIntelligent": "否",
        },
    )


def test_detail_values_accept_generic_boolean_code_values():
    ModuleSmokeDriver._assert_detail_values(
        {"data": {"isGmoDecision": "1", "hasAttachment": False}},
        {"isGmoDecision": "是", "hasAttachment": "否"},
    )


def test_detail_response_falls_back_to_latest_detail_when_runtime_id_differs():
    save = Response("https://host/add")
    detail = Response("https://host/buildProject/detail?projId=business-project-id")

    assert ModuleSmokeDriver._find_detail_response(
        [save, detail], save, "runtime-form-data-key"
    ) is detail


def test_detail_response_accepts_generic_json_endpoint_when_body_contains_business_id():
    save = JsonResponse(
        "https://host/api/progress/add",
        {"code": 200, "data": {"id": "child-1"}},
    )
    child_list = JsonResponse(
        "https://host/api/progress/list",
        {"code": 200, "data": {"records": [{"id": "child-1"}]}},
    )

    assert ModuleSmokeDriver._find_detail_response(
        [save, child_list], save, "child-1"
    ) is child_list


def test_response_record_values_uniquely_associate_dom_row_without_row_id():
    driver = object.__new__(ModuleSmokeDriver)
    expected = RecordRow(["取得开工批复", "管理员", "2026-08-10", "编辑", "删除"])
    driver.page = RecordPage([
        RecordRow(["取得竣工验收", "管理员", "2026-08-10", "编辑", "删除"]),
        expected,
    ])

    row, evidence = driver._find_response_associated_record_container(
        {
            "data": {
                "records": [
                    {
                        "id": "child-1",
                        "progressTypeName": "取得开工批复",
                        "createUserName": "管理员",
                        "createDate": "2026-08-10",
                    }
                ]
            }
        },
        "child-1",
    )

    assert row is expected
    assert "取得开工批复" in evidence


def test_edit_response_association_uses_source_display_fields_not_hidden_technical_values():
    driver = object.__new__(ModuleSmokeDriver)
    target = RecordRow(["江西板块", "2041", "325,644.32", "编辑", "删除"])
    driver.page = RecordPage([
        target,
        RecordRow(["四川板块", "2040", "610,389.41", "编辑", "删除"]),
    ])

    row, evidence = driver._find_response_associated_record_container(
        {
            "data": [{
                "id": "net-1",
                "belongSection": "2",
                "belongSectionName": "江西板块",
                "assetYear": "2041",
                "netAssetAmount": 325644.32,
                "rowVersion": 17,
                "updateByName": "管理员",
            }],
        },
        "net-1",
        display_field_codes=("belongSection", "assetYear", "netAssetAmount"),
    )

    assert row is target
    assert "江西板块" in evidence
    assert "2041" in evidence
    assert "325644.32" in evidence
    evidence_values = {
        value.strip() for value in evidence.partition("=")[2].split(",")
    }
    assert "2" not in evidence_values
    assert "17" not in evidence_values
    assert "管理员" not in evidence_values


def test_default_response_association_remains_strict_for_delete_evidence():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow(["江西板块", "2041", "325,644.32", "编辑", "删除"]),
    ])

    with pytest.raises(AssertionError, match="未完整匹配"):
        driver._find_response_associated_record_container(
            {
                "data": [{
                    "id": "net-1",
                    "belongSection": "2",
                    "belongSectionName": "江西板块",
                    "assetYear": "2041",
                    "netAssetAmount": 325644.32,
                    "rowVersion": 17,
                }],
            },
            "net-1",
        )


def test_edit_response_association_still_requires_two_visible_display_fields():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow(["江西板块", "编辑", "删除"])])

    with pytest.raises(AssertionError, match="至少两个稳定展示字段"):
        driver._find_response_associated_record_container(
            {
                "data": [{
                    "id": "net-1",
                    "belongSection": "2",
                    "belongSectionName": "江西板块",
                    "assetYear": "2041",
                    "netAssetAmount": 325644.32,
                }],
            },
            "net-1",
            display_field_codes=("belongSection", "assetYear", "netAssetAmount"),
        )


def test_edit_response_association_without_source_fields_uses_strict_fallback():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []

    assert driver._source_response_association_codes() is None


def test_response_record_values_reject_ambiguous_dom_rows():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow(["取得开工批复", "管理员", "2026-08-10"]),
        RecordRow(["取得开工批复", "管理员", "2026-08-10"]),
    ])

    with pytest.raises(AssertionError, match="匹配到多条"):
        driver._find_response_associated_record_container(
            {
                "data": {
                    "records": [
                        {
                            "id": "child-1",
                            "progressTypeName": "取得开工批复",
                            "createUserName": "管理员",
                            "createDate": "2026-08-10",
                        }
                    ]
                }
            },
            "child-1",
        )


def test_response_record_prefers_unique_command_business_id_over_duplicate_values():
    driver = object.__new__(ModuleSmokeDriver)
    duplicate = RecordRow(
        ["取得开工批复", "管理员", "2026-08-10"],
        commands=[RecordCommand("编辑", **{"data-record-id": "child-2"})],
    )
    target = RecordRow(
        ["取得开工批复", "管理员", "2026-08-10"],
        commands=[RecordCommand("编辑", **{"data-record-id": "child-1"})],
    )
    driver.page = RecordPage([duplicate, target])

    row, evidence = driver._find_response_associated_record_container(
        {
            "data": {
                "records": [
                    {
                        "id": "child-1",
                        "progressTypeName": "取得开工批复",
                        "createUserName": "管理员",
                        "createDate": "2026-08-10",
                    }
                ]
            }
        },
        "child-1",
    )

    assert row is target
    assert evidence == "操作节点业务 ID=child-1"


def test_response_record_rejects_duplicate_command_business_ids():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow(
            ["取得开工批复"],
            commands=[RecordCommand("编辑", **{"data-record-id": "child-1"})],
        ),
        RecordRow(
            ["取得开工批复"],
            commands=[RecordCommand("删除", **{"data-record-id": "child-1"})],
        ),
    ])

    with pytest.raises(AssertionError, match="操作节点匹配到多条"):
        driver._find_response_associated_record_container(
            {"data": {"records": [{"id": "child-1"}]}},
            "child-1",
        )


def test_response_record_rejects_conflicting_row_and_command_business_ids():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow(
            ["取得开工批复"],
            commands=[RecordCommand("编辑", **{"data-record-id": "child-1"})],
            **{"data-row-key": "child-2"},
        ),
    ])

    with pytest.raises(AssertionError, match="业务 ID 冲突"):
        driver._find_response_associated_record_container(
            {"data": {"records": [{"id": "child-1"}]}},
            "child-1",
        )


def test_response_record_rejects_duplicate_values_without_page_identity():
    driver = object.__new__(ModuleSmokeDriver)
    duplicate = RecordRow(["取得开工批复", "管理员", "2026-08-10"])
    target = RecordRow(["取得开工批复", "管理员", "2026-08-10"])
    driver.page = RecordPage([duplicate, target])

    with pytest.raises(AssertionError, match="无法唯一操作"):
        driver._find_response_associated_record_container(
            {
                "data": {
                    "records": [{"id": "child-1", "progressTypeName": "取得开工批复"}]
                }
            },
            "child-1",
        )


def test_response_record_requires_two_stable_display_fields_without_dom_id():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow(["Target event"])])

    with pytest.raises(AssertionError, match="至少两个稳定展示字段"):
        driver._find_response_associated_record_container(
            {"data": {"records": [{"id": "child-1", "eventName": "Target event"}]}},
            "child-1",
        )


def test_response_record_uses_two_display_fields_for_unique_row_without_dom_id():
    driver = object.__new__(ModuleSmokeDriver)
    target = RecordRow(["Target event", "Alice"])
    driver.page = RecordPage([RecordRow(["Target event", "Bob"]), target])

    row, evidence = driver._find_response_associated_record_container(
        {
            "data": {
                "records": [{
                    "id": "child-1", "eventName": "Target event", "ownerName": "Alice",
                }]
            }
        },
        "child-1",
    )

    assert row is target
    assert evidence == "响应关联字段=Target event, Alice"


def test_response_record_accepts_thousands_separated_numeric_display_value():
    driver = object.__new__(ModuleSmokeDriver)
    target = RecordRow(["Approved budget", "135,024.03"])
    driver.page = RecordPage([RecordRow(["Approved budget", "135,024.04"]), target])

    row, _evidence = driver._find_response_associated_record_container(
        {
            "data": {
                "records": [{
                    "id": "child-1", "eventName": "Approved budget", "amount": 135024.03,
                }]
            }
        },
        "child-1",
    )

    assert row is target


def test_response_record_rejects_duplicate_full_display_evidence_without_dom_id():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow(["Target event", "Alice"]),
        RecordRow(["Target event", "Alice"]),
    ])

    with pytest.raises(AssertionError, match="匹配到多条"):
        driver._find_response_associated_record_container(
            {
                "data": {
                    "records": [{
                        "id": "child-1", "eventName": "Target event", "ownerName": "Alice",
                    }]
                }
            },
            "child-1",
        )


def test_detail_response_ignores_css_file_with_detail_in_filename():
    save = Response("https://host/add")
    api = Response("https://host/api/buildProject/detail?id=1")
    css = Response(
        "https://host/fi-view/css/detail.hash.css",
        resource_type="stylesheet",
        content_type="text/css",
    )

    assert ModuleSmokeDriver._find_detail_response(
        [save, api, css], save, "runtime-form-data-key"
    ) is api


def test_build_project_application_list_is_accepted_as_detail_data():
    response = Response("https://host/fi-service/projAppInfo/listPage")

    assert ModuleSmokeDriver._find_detail_response(
        [response], None, "runtime-project-id"
    ) is response


def test_file_type_detail_endpoint_is_accepted_as_detail_data():
    response = Response("https://host/foundation/commFileType/detailFileType/10001")

    assert ModuleSmokeDriver._find_detail_response(
        [response], None, "10001"
    ) is response


def test_mcname_is_recognized_as_qcc_company_field():
    assert ModuleSmokeDriver._is_company_remote("mcName", "el-id-949-68")


def test_entity_already_exists_prefers_only_remote_company_field():
    fields = [
        ("mcName", "管理人名称", True),
        ("shortName", "管理人简称", False),
        ("mcNo", "管理人编码", False),
    ]

    assert ModuleSmokeDriver._duplicate_field_candidates(
        fields,
        {"mcName": "企业A", "shortName": "简称A", "mcNo": "CODE_A"},
    ) == [("mcName", "管理人名称", True)]


def test_source_field_is_matched_by_business_code_instead_of_position():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("email", "邮箱", False), ("investorType", "投资人类型", False)]
    dom = DomField("investorType", "", "select", "[prop=investorType]")

    assert driver._source_for_dom(dom, 1) == ("investorType", "投资人类型", False)


def test_source_field_is_matched_by_visible_label_before_position():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("email", "联系邮箱", False), ("remark", "备注", False)]
    dom = DomField("el-id-1", "备注：", "textarea", "#el-id-1")

    assert driver._source_for_dom(dom, 1) == ("remark", "备注", False)


def test_generated_name_field_uses_semantic_label_before_position_fallback():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("shortName", "企业简称", False)]
    dom = DomField("el-id-1", "项目名称", "text", "#el-id-1")

    assert driver._source_for_dom(dom, 1) == ("projName", "项目名称", False)


def test_runtime_identity_rejects_generated_field_source_order_mismatch():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("projName", "项目名称", False),
        ("projClassify", "项目类型", False),
        ("belongSection", "责任板块", False),
        ("inveId", "实施主体公司", False),
    ]
    unlabeled_select = DomField("el-id-58", "", "select", "#el-id-58")
    option_label_radio = DomField("el-id-61", "是", "radio", "#el-id-61")

    assert driver._runtime_identity_for_dom(unlabeled_select, 2)[0] == "el-id-58"
    assert driver._runtime_identity_for_dom(option_label_radio, 3)[0] == "el-id-61"


def test_element_generated_ids_are_not_business_field_codes():
    assert ModuleSmokeDriver._is_generated_identifier("el-id-5188-55")
    assert not ModuleSmokeDriver._is_generated_identifier("email")


def test_dom_scanner_requires_a_prop_bearing_form_item():
    assert ".el-form-item[prop],.ant-form-item[prop]" in DOM_FIELD_SCRIPT


def test_dom_scanner_business_prop_wins_over_generated_element_name():
    prop_position = DOM_FIELD_SCRIPT.index("propItem?.getAttribute('prop')")
    generated_name_position = DOM_FIELD_SCRIPT.index("!name.startsWith('el-id-')")
    assert prop_position < generated_name_position


def test_dom_scanner_reads_component_placeholder_and_aria_label():
    assert "const componentPlaceholder = (el)" in DOM_FIELD_SCRIPT
    assert ".el-select__placeholder" in DOM_FIELD_SCRIPT
    assert "const ariaLabel = (el)" in DOM_FIELD_SCRIPT
    assert "aria-labelledby" in DOM_FIELD_SCRIPT
    assert "optionLabelSelector" in DOM_FIELD_SCRIPT


def test_dom_scanner_recognizes_semantic_numeric_text_inputs():
    assert "['numeric', 'decimal'].includes(inputmode)" in DOM_FIELD_SCRIPT
    assert "cls.includes('input-number')" in DOM_FIELD_SCRIPT
    assert "比例|百分比|金额|出资额|数量" in DOM_FIELD_SCRIPT
    assert "kindOf(el, label, code)" in DOM_FIELD_SCRIPT


def test_dom_scanner_recognizes_period_and_return_rate_as_numeric_labels():
    assert "周期[（(]?(?:年|月|天)" in DOM_FIELD_SCRIPT
    assert "回报率" in DOM_FIELD_SCRIPT


def test_dom_scanner_promotes_text_period_code_before_baseline_generation():
    class Page:
        @staticmethod
        def evaluate(_script, _root):
            return [{
                "field_code": "buildPeriodMonth",
                "label": "请输入建设周期",
                "kind": "text",
                "selector": "#period",
                "required": True,
            }]

    field = scan_dom_fields(Page())[0]

    assert field.kind == "number"


def test_generated_numeric_placeholders_recover_source_identity_when_unit_differs():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("buildPeriodMonth", "建设周期（月）", False),
        ("expectedReturnRate", "预计回报率（%）", False),
        ("financeSources.*.amount", "预算金额（万元）", False),
    ]

    period = driver._runtime_identity_for_dom(
        DomField("el-id-1", "请输入建设周期", "text", "#period"),
        1,
    )
    rate = driver._runtime_identity_for_dom(
        DomField("el-id-2", "请输入预计回报率", "text", "#rate"),
        2,
    )
    amount = driver._runtime_identity_for_dom(
        DomField("el-id-3", "请输入预算金额", "text", "#amount"),
        3,
    )

    assert (period[0], period[1]) == ("buildPeriodMonth", "建设周期（月）")
    assert (rate[0], rate[1]) == ("expectedReturnRate", "预计回报率（%）")
    assert (amount[0], amount[1]) == ("financeSources.*.amount", "预算金额（万元）")


def test_dynamic_table_runtime_prop_matches_source_wildcard_code():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("financeSources.*.amount", "预算金额（万元）", False)]
    dom = DomField(
        "financeSources.0.amount",
        "预算金额（万元）",
        "number",
        "#amount",
    )

    assert driver._source_for_dom(dom, 1) == (
        "financeSources.*.amount",
        "预算金额（万元）",
        False,
    )


def test_dom_scanner_keeps_non_numeric_build_text_fields_as_text():
    class Page:
        @staticmethod
        def evaluate(_script, _root):
            return [
                {
                    "field_code": "buildScale",
                    "label": "请输入建设规模",
                    "kind": "text",
                    "selector": "#scale",
                },
                {
                    "field_code": "buildContent",
                    "label": "请输入建设内容",
                    "kind": "text",
                    "selector": "#content",
                },
            ]

    fields = scan_dom_fields(Page())

    assert [field.kind for field in fields] == ["text", "text"]


def test_dom_scanner_classifies_combobox_before_numeric_label_heuristics():
    combobox_position = DOM_FIELD_SCRIPT.index("role === 'combobox'")
    numeric_label_position = DOM_FIELD_SCRIPT.index("比例|百分比|金额|出资额|数量")
    assert combobox_position < numeric_label_position


def test_dom_scanner_classifies_year_picker_before_combobox():
    year_picker_position = DOM_FIELD_SCRIPT.index("date-editor--year")
    combobox_position = DOM_FIELD_SCRIPT.index("role === 'combobox'")

    assert year_picker_position < combobox_position
    assert "'select','multi_select','year','radio','checkbox'" in DOM_FIELD_SCRIPT
    assert "el.closest(componentControlSelector)" in DOM_FIELD_SCRIPT


def test_dom_scanner_collects_visible_choice_component_roots():
    assert "componentControlSelector" in DOM_FIELD_SCRIPT
    assert ".el-radio-group,.el-radio" in DOM_FIELD_SCRIPT
    assert ".el-checkbox-group,.el-checkbox" in DOM_FIELD_SCRIPT
    assert "componentControlSelector" in DOM_FIELD_SCRIPT.split("querySelectorAll", 1)[1]
    assert "cls.includes('radio')" in DOM_FIELD_SCRIPT
    assert "cls.includes('checkbox')" in DOM_FIELD_SCRIPT
    assert "selectorOf(el, code, label, kind)" in DOM_FIELD_SCRIPT
    assert '[prop="${CSS.escape(code)}"] .el-radio-group' in DOM_FIELD_SCRIPT
    assert '[prop="${CSS.escape(code)}"] .el-select' in DOM_FIELD_SCRIPT


class _YearRouteInput:
    def evaluate(self, _script):
        return "input"

    def get_attribute(self, name):
        return {"role": "combobox", "readonly": "", "class": "el-input__inner"}.get(name)


class _YearInput(_YearRouteInput):
    def __init__(self, value=""):
        self.value = value

    def input_value(self):
        return self.value


class _YearCell:
    def __init__(self, year, target, *, updates_value=True):
        self.year = str(year)
        self.target = target
        self.updates_value = updates_value
        self.clicked = False

    def inner_text(self):
        return self.year

    def is_visible(self):
        return True

    def click(self, **_kwargs):
        self.clicked = True
        if self.updates_value:
            self.target.value = self.year


class _YearLocatorList:
    def __init__(self, items=()):
        self.items = list(items)

    @property
    def first(self):
        return self.items[0] if self.items else _MissingYearLocator()

    def count(self):
        return len(self.items)

    def nth(self, index):
        return self.items[index]


class _MissingYearLocator:
    def count(self):
        return 0

    def is_visible(self):
        return False


class _YearPanel:
    def __init__(self, cells):
        self.cells = _YearLocatorList(cells)

    @property
    def last(self):
        return self

    def wait_for(self, **_kwargs):
        return None

    def locator(self, selector):
        return self.cells if "el-year-table" in selector else _YearLocatorList()


class _ClearYearAction:
    def __init__(self, target):
        self.target = target
        self.clicked = False

    @property
    def first(self):
        return self

    def count(self):
        return 1

    def is_visible(self):
        return True

    def click(self, **_kwargs):
        self.clicked = True
        self.target.value = ""


class _YearWrapper:
    def __init__(self, target):
        self.target = target
        self.clear_action = _ClearYearAction(target)
        self.clicked = False

    def click(self, **_kwargs):
        self.clicked = True

    def hover(self):
        return None

    def locator(self, _selector):
        return self.clear_action


class _YearPage:
    def __init__(self, target, *, updates_value=True):
        self.cells = [
            _YearCell(year, target, updates_value=updates_value)
            for year in range(2020, 2030)
        ]
        self.panel = _YearPanel(self.cells)

    def locator(self, _selector):
        return self.panel

    def wait_for_timeout(self, _milliseconds):
        return None


def _year_resolved_field():
    return ResolvedField(
        FieldDefinition("assetYear", "年度", "DATE"),
        DomField("assetYear", "年度", "year", "#asset-year", required=True),
    )


def test_field_interactor_routes_year_dom_kind_before_combobox(monkeypatch):
    locator = _YearRouteInput()
    interactor = FieldInteractor(object())
    monkeypatch.setattr(interactor, "locate", lambda _field: locator)
    monkeypatch.setattr(interactor, "_select_year", lambda _locator, value: str(value))
    monkeypatch.setattr(
        interactor,
        "_select",
        lambda *_args: (_ for _ in ()).throw(AssertionError("ordinary select used")),
    )

    assert interactor.fill(_year_resolved_field(), 2026) == "2026"


def test_field_interactor_selects_visible_year_and_verifies_readback(monkeypatch):
    locator = _YearInput()
    page = _YearPage(locator)
    wrapper = _YearWrapper(locator)
    interactor = FieldInteractor(page)
    monkeypatch.setattr(interactor, "_year_picker", lambda _locator: wrapper)

    assert interactor._select_year(locator, "2026-08-05") == "2026"
    assert page.cells[6].clicked
    assert locator.input_value() == "2026"


def test_field_interactor_rejects_year_click_without_value_update(monkeypatch):
    locator = _YearInput()
    page = _YearPage(locator, updates_value=False)
    interactor = FieldInteractor(page)
    monkeypatch.setattr(interactor, "_year_picker", lambda _locator: _YearWrapper(locator))

    with pytest.raises(AssertionError, match="did not update its value"):
        interactor._select_year(locator, 2026)


def test_field_interactor_clears_year_picker_and_verifies_empty(monkeypatch):
    locator = _YearInput("2026")
    wrapper = _YearWrapper(locator)
    interactor = FieldInteractor(_YearPage(locator))
    monkeypatch.setattr(interactor, "locate", lambda _field: locator)
    monkeypatch.setattr(interactor, "_year_picker", lambda _locator: wrapper)

    assert interactor.clear(_year_resolved_field()) == ""
    assert wrapper.clear_action.clicked


def test_dom_scanner_deduplicates_component_internal_controls():
    assert "const seenOwners = new WeakSet()" in DOM_FIELD_SCRIPT
    assert "el.closest(componentControlSelector) || el" in DOM_FIELD_SCRIPT


def test_dom_scanner_marks_disabled_component_owners_readonly():
    disabled_start = DOM_FIELD_SCRIPT.index("const disabledStateSelector")
    controls_start = DOM_FIELD_SCRIPT.index("const controls", disabled_start)
    disabled_logic = DOM_FIELD_SCRIPT[disabled_start:controls_start]

    assert ".is-disabled" in disabled_logic
    assert '[aria-disabled="true"]' in disabled_logic
    assert "const formItem = owner.closest" in disabled_logic
    assert "node.closest(disabledStateSelector)" in disabled_logic
    assert "readonly: disabledOf(el)" in DOM_FIELD_SCRIPT


def test_dom_scanner_uses_purvar_direct_label_column_before_selected_value():
    direct_label = DOM_FIELD_SCRIPT.index("const directLabelColumn")
    independent_labels = DOM_FIELD_SCRIPT.index("const independentLabels")

    assert direct_label < independent_labels
    assert "querySelector(':scope > .el-col:first-child')" in DOM_FIELD_SCRIPT
    assert "!directLabelColumn.contains(el)" in DOM_FIELD_SCRIPT
    assert "if (directLabel) return directLabel" in DOM_FIELD_SCRIPT


def test_dom_scanner_deduplicates_radio_and_checkbox_by_group_owner():
    owner_start = DOM_FIELD_SCRIPT.index("const componentOwner")
    owner_end = DOM_FIELD_SCRIPT.index("const controls", owner_start)
    owner_logic = DOM_FIELD_SCRIPT[owner_start:owner_end]

    assert "el.closest('.el-radio-group,.el-checkbox-group')" in owner_logic
    assert "const owner = componentOwner(el)" in DOM_FIELD_SCRIPT


def test_dom_scanner_uses_table_header_as_dynamic_row_field_label():
    assert "el.closest('td')" in DOM_FIELD_SCRIPT
    assert "headerRows.at(-1)?.cells?.[cell.cellIndex]" in DOM_FIELD_SCRIPT
    assert "headerText !== '操作'" in DOM_FIELD_SCRIPT
    assert "_column_\\d+$" in DOM_FIELD_SCRIPT
    assert "cell?.closest('.el-table,.ant-table')" in DOM_FIELD_SCRIPT


def test_select_value_check_includes_element_plus_selected_item():
    source = ModuleSmokeDriver._dom_field_has_value.__code__.co_consts
    assert any("el-select__selected-item" in value for value in source if isinstance(value, str))
    assert any(".el-select__wrapper" in value for value in source if isinstance(value, str))
    assert any("querySelectorAll('.el-select__placeholder,[placeholder]')" in value for value in source if isinstance(value, str))
    assert any("querySelectorAll" in value for value in source if isinstance(value, str))
    assert any("Array.from(selected).some" in value for value in source if isinstance(value, str))


def test_field_value_check_uses_active_form_root():
    class ActiveControl:
        def count(self):
            return 1

        def is_visible(self):
            return True

        def evaluate(self, _script):
            return True

    control = ActiveControl()

    class Root:
        def locator(self, selector):
            assert selector == "#name"
            return type("Candidates", (), {"first": control})()

    class Page:
        def locator(self, _selector):
            raise AssertionError("page-wide lookup must not inspect a stale dialog")

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    assert driver._dom_field_has_value(
        DomField("name", "名称", "text", "#name"), root=Root()
    )


def test_fill_dialog_preserves_fields_that_already_have_values(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("name", "名称", False)]
    driver.page = object()
    driver._dom_field_has_value = lambda field, **_kwargs: True
    driver.data_strategy = type("Strategy", (), {
        "value_for": lambda self, field, index: (_ for _ in ()).throw(
            AssertionError("existing field value must not be regenerated")
        )
    })()
    rendered = [DomField("name", "名称", "text", "#name", required=True)]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered)

    assert driver._fill_dialog() == {}


def test_fill_dialog_preserves_generated_radio_with_stable_checked_identity(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("isGmoDecision", "是否需总经办决策", False)]
    driver.page = object()
    driver._common_form_scope = None
    seen = []
    driver._dom_field_has_value = (
        lambda field, **kwargs:
        seen.append(kwargs) or kwargs.get("field_code") == "isGmoDecision"
    )
    driver._radio_group = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("existing checked radio must not be clicked again")
    )
    driver.data_strategy = type("Strategy", (), {
        "value_for": lambda self, field, index: (_ for _ in ()).throw(
            AssertionError("existing radio value must not be regenerated")
        )
    })()
    rendered = [
        DomField("el-id-4643-66", "是", "radio", "#el-id-4643-66", required=True)
    ]
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered
    )

    assert driver._fill_dialog() == {}
    assert seen[0]["field_code"] == "isGmoDecision"
    assert seen[0]["field_label"] == "是否需总经办决策"


def test_fill_dialog_routes_required_year_picker_to_interactor(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("assetYear", "年度", False)]
    driver.page = object()
    state = {"value": ""}
    driver._dom_field_has_value = lambda _field, **_kwargs: bool(state["value"])
    driver.data_strategy = type(
        "Strategy", (), {"value_for": lambda self, field, index: "2026"}
    )()
    calls = []

    class _Interactor:
        @staticmethod
        def fill(field, value):
            calls.append((field.definition.field_code, field.dom.kind, value))
            state["value"] = value
            return value

    driver.interactor = _Interactor()
    driver._select_by_label = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        AssertionError("year picker must not use ordinary select options")
    )
    rendered = [
        DomField("assetYear", "年度", "year", "#asset-year", required=True)
    ]
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered
    )

    submitted = driver._fill_dialog()
    report = driver.check_field_completion(submitted, driver._fill_failures)

    assert submitted == {"assetYear": "2026"}
    assert calls == [("assetYear", "year", "2026")]
    assert report.ok


def test_prefilled_generated_name_is_kept_as_record_marker(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    driver.page = IdentityPage({
        "#matter": IdentityLocator("UI自动化_20260805115347_1"),
    })
    rendered = [
        DomField(
            "el-id-1", "请输入事项名称", "text", "#matter", required=True,
        )
    ]
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: rendered,
    )

    assert driver._source_for_dom(rendered[0], 1)[0] == "matterName"
    assert driver._collect_record_identity_markers({}, scope=object()) == (
        "UI自动化_20260805115347_1",
    )


def test_record_markers_include_submitted_names_but_exclude_nested_names(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    driver.page = IdentityPage({
        "#nested": IdentityLocator("AUTO_明细名称", nested=True),
    })
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [
            DomField("name", "明细名称", "text", "#nested", required=True),
        ],
    )

    assert driver._collect_record_identity_markers(
        {"matterName": "AUTO_事项名称"}, scope=object(),
    ) == ("AUTO_事项名称",)


def test_record_markers_fall_back_to_submitted_automation_value(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    driver.page = IdentityPage({})
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [],
    )

    assert driver._collect_record_identity_markers(
        {"riskSummary": "UI自动化_风险_S1786300000123001"}, scope=object(),
    ) == (
        "UI自动化_风险_S1786300000123001",
    )


def test_record_markers_keep_real_identity_over_automation_fallback(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    driver.page = IdentityPage({})
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [],
    )

    assert driver._collect_record_identity_markers({
        "projName": "项目名称",
        "riskSummary": "UI自动化_风险_S1786300000123001",
    }, scope=object()) == ("项目名称",)


def test_record_container_rejects_duplicate_automation_fallback_markers():
    marker = "UI自动化_风险_S1786300000123001"
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow([marker]), RecordRow([marker])])

    with pytest.raises(AssertionError, match="精确匹配到 2 条记录"):
        driver._find_unique_record_container("", [marker], allow_search=False)


def test_record_markers_include_stable_save_token_prefix():
    assert ModuleSmokeDriver._submitted_identity_values({
        "projName": "UI自动化_20260807152428_1_S001_中A 12，。-()",
    }) == [
        "UI自动化_20260807152428_1_S001_中A 12，。-()",
        "UI自动化_20260807152428_1_S001",
    ]


def test_record_markers_use_latest_save_token_after_historical_token():
    full_value = (
        "UI自动化_20260809140037_1_S012-第2次决策_"
        "S001_<script>alert(1)</script>"
    )

    assert ModuleSmokeDriver._submitted_identity_values({
        "matterName": full_value,
    }) == [
        full_value,
        "UI自动化_20260809140037_1_S012-第2次决策_S001",
    ]


def test_record_container_matches_latest_save_token_not_historical_token():
    historical = "UI自动化_20260809140037_1_S012"
    current = f"{historical}-第2次决策_S001"
    old_row = RecordRow([historical])
    current_row = RecordRow([current])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([old_row, current_row])
    markers = ModuleSmokeDriver._record_identity_marker_variants([
        f"{current}_<script>alert(1)</script>",
    ])

    row, identity = driver._find_unique_record_container(
        "", markers, allow_search=False,
    )

    assert row is current_row
    assert identity == current
    assert historical not in markers


def test_record_container_accepts_latest_save_token_as_cell_prefix():
    marker = "UI自动化_20260809140037_1_S012-第2次决策_S001"
    row = RecordRow([f"{marker}_列表展示后缀"])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([row])
    markers = ModuleSmokeDriver._record_identity_marker_variants([
        f"{marker}_完整提交值",
    ])

    matched, identity = driver._find_unique_record_container(
        "", markers, allow_search=False,
    )

    assert matched is row
    assert identity == marker


def test_record_container_prefers_exact_full_marker_over_shared_prefix_rows():
    prefix = "AUTO_SAVE_d5812710_S1786345875925002"
    full_marker = f"{prefix}-第25次决策"
    exact_row = RecordRow([full_marker])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow([f"{prefix}-第23次决策"]),
        exact_row,
        RecordRow([f"{prefix}-第24次决策"]),
    ])

    matched, identity = driver._find_unique_record_container(
        "", [full_marker, prefix], allow_search=False,
    )

    assert matched is exact_row
    assert identity == full_marker


def test_record_container_requires_unique_latest_save_token_prefix():
    marker = "UI自动化_20260809140037_1_S012-第2次决策_S001"
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([
        RecordRow([f"{marker}_记录一"]),
        RecordRow([f"{marker}_记录二"]),
    ])

    with pytest.raises(AssertionError, match="稳定前缀匹配到 2 条记录"):
        driver._find_unique_record_container(
            "", [marker], allow_search=False,
        )


def test_record_container_does_not_accept_marker_as_cell_substring():
    marker = "UI自动化_20260809140037_1_S012-第2次决策_S001"
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow([f"历史记录 {marker} 后缀"])])

    with pytest.raises(AssertionError, match="无法精确定位"):
        driver._find_unique_record_container(
            "", [marker], allow_search=False,
        )


def test_record_container_keeps_ordinary_business_marker_exact():
    marker = "普通项目名称"
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow([f"{marker}_列表后缀"])])

    with pytest.raises(AssertionError, match="无法精确定位"):
        driver._find_unique_record_container(
            "", [marker], allow_search=False,
        )


def test_find_unique_record_container_searches_by_stable_marker():
    marker = "UI自动化_20260807152428_1_S001"

    class Empty:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Items:
        def __init__(self, items):
            self.items = items

        def count(self):
            return len(self.items)

        def nth(self, index):
            return self.items[index]

    class Card:
        @staticmethod
        def get_attribute(_name):
            return ""

        @staticmethod
        def locator(_selector):
            return Empty()

        @staticmethod
        def inner_text():
            return marker

    class Input:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_editable():
            return True

        @staticmethod
        def evaluate(_script):
            return False

        @staticmethod
        def get_attribute(name):
            return "请输入项目名称" if name == "placeholder" else ""

        def fill(self, value):
            self.page.keyword = value

        def press(self, _key):
            self.page.searched = True

    class Button:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def evaluate(_script):
            return False

        def click(self):
            self.page.searched = True

    class Page:
        def __init__(self):
            self.searched = False
            self.keyword = ""

        def locator(self, selector):
            if selector.startswith(".el-table__row"):
                return Items([Card()] if self.searched else [])
            if selector.startswith("input:visible"):
                return Items([Input(self)])
            if selector.startswith("button:visible"):
                return Items([Button(self)])
            return Empty()

        @staticmethod
        def wait_for_timeout(_timeout):
            return None

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    row, identity = driver._find_unique_record_container("", [marker])

    assert isinstance(row, Card)
    assert identity == marker
    assert driver.page.keyword == marker


def test_open_form_field_values_reads_checked_radio_group():
    class Control:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def evaluate(_script, dom_kind=None):
            assert dom_kind == "radio"
            return ["1", "是"]

    class Scope:
        @staticmethod
        def locator(selector):
            assert selector == "#decision-group"
            return Control()

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()
    dom = DomField(
        "isGmoDecision",
        "是否需总经办决策",
        "radio",
        "#decision-group",
        required=True,
    )

    assert driver._open_form_field_values(dom, Scope()) == ["1", "是"]


def test_cleanup_accepts_only_automation_owned_record_markers():
    assert ModuleSmokeDriver._automation_owned_markers([
        "既有项目",
        "AUTO_20260805_1",
        "UI自动化_20260805_2",
        "UI自动化测试企业_20260805_3",
        "AUTO_20260805_1",
    ]) == [
        "AUTO_20260805_1",
        "UI自动化_20260805_2",
        "UI自动化测试企业_20260805_3",
    ]


def test_delete_row_marker_does_not_match_longer_cell_text():
    driver = object.__new__(ModuleSmokeDriver)
    rows = DeleteRows([DeleteRow(["AUTO_1234", "草稿"])])

    try:
        driver._find_unique_delete_row("", ["AUTO_123"], rows=rows)
    except AssertionError as exc:
        assert "未找到单元格文本精确匹配" in str(exc)
    else:
        raise AssertionError("substring marker selected another record")


def test_delete_row_rejects_duplicate_exact_markers():
    driver = object.__new__(ModuleSmokeDriver)
    rows = DeleteRows([
        DeleteRow(["AUTO_123", "草稿"]),
        DeleteRow([" AUTO_123\n", "已提交"]),
    ])

    try:
        driver._find_unique_delete_row("", ["AUTO_123"], rows=rows)
    except AssertionError as exc:
        assert "精确匹配到 2 条记录" in str(exc)
    else:
        raise AssertionError("duplicate exact markers selected an arbitrary row")


def test_delete_row_prefers_exact_business_id_attribute():
    driver = object.__new__(ModuleSmokeDriver)
    id_row = DeleteRow(["AUTO_other"], **{"data-row-key": "record-1"})
    marker_row = DeleteRow(["AUTO_123"])

    selected, identity = driver._find_unique_delete_row(
        "record-1",
        ["AUTO_123"],
        rows=DeleteRows([marker_row, id_row]),
    )

    assert selected is id_row
    assert identity == "record-1"


def test_delete_row_accepts_exact_business_id_on_delete_command():
    driver = object.__new__(ModuleSmokeDriver)
    target = DeleteRow(
        ["AUTO_target"],
        commands=[RecordCommand("删除", **{"data-record-id": "record-1"})],
    )
    other = DeleteRow(
        ["AUTO_other"],
        commands=[RecordCommand("删除", **{"data-record-id": "record-2"})],
    )

    selected, identity = driver._find_unique_delete_row(
        "record-1",
        [],
        rows=DeleteRows([other, target]),
    )

    assert selected is target
    assert identity == "record-1"


def test_delete_row_rejects_conflicting_row_and_command_business_ids():
    driver = object.__new__(ModuleSmokeDriver)
    conflict = DeleteRow(
        ["AUTO_target"],
        commands=[RecordCommand("删除", **{"data-record-id": "record-1"})],
        **{"data-row-key": "record-2"},
    )

    with pytest.raises(AssertionError, match="业务 ID 冲突"):
        driver._find_unique_delete_row(
            "record-1",
            [],
            rows=DeleteRows([conflict]),
        )


def test_reusable_delete_record_rejects_unregistered_automation_marker_and_id():
    class EmptyLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "locator": lambda _self, selector: (
            EmptyLocator()
            if "loading" in selector or "busy" in selector
            else DeleteRows([
                DeleteRow(["普通业务数据"], **{"data-row-key": "business-1"}),
                DeleteRow(["AUTO_without_id"]),
                DeleteRow(["AUTO_delete_me"], **{"data-row-key": "auto-1"}),
            ])
        ),
    })()

    assert driver.find_reusable_automation_delete_record() is None


def test_reusable_delete_record_rejects_registry_entry_from_different_scope(
    tmp_path,
):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage(
        [
            RecordRow(
                ["AUTO_delete_me", "删除"],
                **{"data-row-key": "auto-1"},
            )
        ],
        url="https://host/projects",
    )
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver.automation_record_registry.write_text(
        '{"records":[{"business_id":"auto-1",'
        '"page_scope":"https://host/investors",'
        '"record_markers":["AUTO_delete_me"],'
        '"submitted":{"name":"AUTO_delete_me"},'
        '"record_identity_payload":{"id":"auto-1",'
        '"name":"AUTO_delete_me"}}]}',
        encoding="utf-8",
    )

    assert driver.find_reusable_automation_delete_record() is None


def test_reusable_delete_record_accepts_registered_automation_marker_and_id(
    monkeypatch, tmp_path,
):
    class EmptyLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "url": "https://host/projects",
        "locator": lambda _self, selector: (
            EmptyLocator()
            if "loading" in selector or "busy" in selector
            else DeleteRows([
                DeleteRow(["普通业务数据"], **{"data-row-key": "business-1"}),
                DeleteRow(["AUTO_delete_me"], **{"data-row-key": "auto-1"}),
            ])
        ),
    })()
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver.automation_record_registry.write_text(
        '{"records":[{"business_id":"auto-1",'
        '"page_scope":"https://host/projects",'
        '"record_markers":["AUTO_delete_me"],'
        '"submitted":{"name":"AUTO_delete_me"},'
        '"record_identity_payload":{"id":"auto-1",'
        '"name":"AUTO_delete_me"}}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(driver, "_pin_delete_row", lambda row, *_args, **_kwargs: row)

    result = driver.find_reusable_automation_delete_record()

    assert result is not None
    assert result.business_id == "auto-1"
    assert result.record_markers == ("AUTO_delete_me",)


def test_registered_marker_without_row_id_still_requires_two_display_values(
    tmp_path,
):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow(["AUTO_delete_me", "删除"])])
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver.automation_record_registry.write_text(
        '{"records":[{"business_id":"auto-1",'
        '"page_scope":"https://host/projects",'
        '"record_markers":["AUTO_delete_me"],'
        '"submitted":{"name":"AUTO_delete_me"},'
        '"record_identity_payload":{"id":"auto-1",'
        '"name":"AUTO_delete_me"}}]}',
        encoding="utf-8",
    )

    assert driver.find_reusable_automation_delete_record() is None


def test_reusable_delete_record_ignores_business_rows_and_unaddressable_automation_rows():
    class EmptyLocator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type("Page", (), {
        "locator": lambda _self, selector: (
            EmptyLocator()
            if "loading" in selector or "busy" in selector
            else DeleteRows([
                DeleteRow(["普通业务数据"], **{"data-row-key": "business-1"}),
                DeleteRow(["UI自动化_无业务主键"]),
            ])
        ),
    })()

    assert driver.find_reusable_automation_delete_record() is None


def test_reusable_delete_record_uses_registered_evidence_when_list_hides_id(monkeypatch, tmp_path):
    target = RecordRow(["四川板块", "2031", "123.45", "编辑", "删除"])
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow(["四川板块", "2032", "123.45"]), target])
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver.automation_record_registry.write_text(
        '{"records":[{"business_id":"auto-1","page_scope":"https://host/projects",'
        '"record_markers":[],"submitted":{"belongSection":"四川板块",'
        '"assetYear":"2031","netAssetAmount":123.45},"record_identity_payload":'
        '{"data":{"records":[{"id":"auto-1","belongSectionName":"四川板块",'
        '"assetYear":"2031","netAssetAmount":123.45}]}}}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(driver, "_pin_delete_row", lambda row, *_args, **_kwargs: row)

    result = driver.find_reusable_automation_delete_record()

    assert result is not None
    assert result.business_id == "auto-1"


def test_reusable_delete_record_rejects_unregistered_business_row_when_list_hides_id(tmp_path):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = RecordPage([RecordRow(["四川板块", "2031", "123.45", "编辑", "删除"])])
    driver.automation_record_registry = tmp_path / "automation-record-registry.json"
    driver.automation_record_registry.write_text('{"records":[]}', encoding="utf-8")

    assert driver.find_reusable_automation_delete_record() is None


def test_delete_row_rejects_duplicate_display_values_without_page_identity():
    driver = object.__new__(ModuleSmokeDriver)
    duplicate = DeleteRow(["取得开工批复", "管理员", "2026-08-12"])
    target = DeleteRow(["取得开工批复", "管理员", "2026-08-12"])

    with pytest.raises(AssertionError, match="无法唯一定位"):
        driver._find_unique_delete_row(
            "record-1", [], rows=DeleteRows([duplicate, target])
        )


def test_delete_row_accepts_unique_combination_of_saved_display_values():
    driver = object.__new__(ModuleSmokeDriver)
    target = DeleteRow(["重大安全事故", "2026-08-01"])
    other = DeleteRow(["重大安全事故", "2026-08-02"])

    selected, identity = driver._find_unique_delete_row(
        "record-1",
        [],
        fallback_values=["重大安全事故", "2026-08-01"],
        rows=DeleteRows([other, target]),
    )

    assert selected is target
    assert identity == "保存字段组合"


def test_delete_confirmation_allows_hidden_row_id_only_for_unique_saved_values(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()
    captured = {}

    class PinnedRow:
        def get_by_role(self, *_args, **_kwargs):
            return type("Delete", (), {
                "first": type("Delete", (), {
                    "count": staticmethod(lambda: 1),
                    "is_visible": staticmethod(lambda: True),
                    "is_enabled": staticmethod(lambda: True),
                    "click": staticmethod(lambda: None),
                })(),
            })()

    monkeypatch.setattr(
        driver, "_find_unique_delete_row", lambda *_args, **_kwargs: (object(), "保存字段组合")
    )
    monkeypatch.setattr(
        driver, "_pin_delete_row", lambda _row, _id, **kwargs: captured.update(kwargs) or PinnedRow()
    )
    monkeypatch.setattr(
        driver, "_automation_owned_markers", lambda values: list(values))
    monkeypatch.setattr(
        driver, "_delete_display_identity_values", lambda *_args: ["四川板块", "2031", "123.45"])

    # Only the pinning policy is under test; use a pre-opened confirmation path.
    driver._open_delete_confirmation = ModuleSmokeDriver._open_delete_confirmation.__get__(driver)
    driver._automation_owned_markers = lambda values: list(values)
    driver._delete_display_identity_values = lambda *_args: ["四川板块", "2031", "123.45"]

    class Rows:
        @property
        def first(self):
            return self

        @staticmethod
        def wait_for(**_kwargs):
            return None

    class Locator:
        @property
        def last(self):
            return self

        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    driver.page = type("Page", (), {
        "locator": lambda _self, selector: Rows() if ".el-table__row" in selector else Locator(),
    })()

    with pytest.raises(AttributeError):
        driver._open_delete_confirmation(ModuleSmokeResult(
            mode="add_provisioned", business_id="record-1",
            submitted={"section": "四川板块"},
        ))

    assert captured["allow_missing_id"] is True


def test_delete_request_guard_aborts_a_different_record_id():
    class Intercept:
        def __init__(self):
            self.aborted = False
            self.continued = False

        def abort(self):
            self.aborted = True

        def continue_(self):
            self.continued = True

    class Request:
        method = "DELETE"

        def __init__(self, record_id):
            self.url = f"https://host/api/projDecision/delete/{record_id}?trace=ignored"

    class Page:
        def route(self, pattern, handler):
            assert pattern == "**/*"
            self.handler = handler

        def unroute(self, pattern, handler):
            assert pattern == "**/*"
            assert handler is self.handler
            self.removed = True

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    blocked = driver._install_delete_request_guard("target-id")
    intercept = Intercept()
    driver.page.handler(intercept, Request("other-id"))

    assert intercept.aborted and not intercept.continued
    assert blocked == ["/api/projDecision/delete/other-id"]
    assert driver.page.removed


def test_delete_request_guard_allows_the_target_record_id():
    class Intercept:
        aborted = False
        continued = False

        def abort(self):
            self.aborted = True

        def continue_(self):
            self.continued = True

    class Request:
        method = "DELETE"
        url = "https://host/api/projDecision/delete/target-id"

    class Page:
        def route(self, _pattern, handler):
            self.handler = handler

        def unroute(self, _pattern, _handler):
            self.removed = True

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()
    intercept = Intercept()

    assert driver._install_delete_request_guard("target-id") == []
    driver.page.handler(intercept, Request())

    assert intercept.continued and not intercept.aborted
    assert driver.page.removed


@pytest.mark.parametrize("absence_verified", [False, True])
def test_delete_registry_entry_is_removed_only_after_absence_is_verified(
    monkeypatch, absence_verified,
):
    events = []

    class Button:
        @property
        def last(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return True

        def click(self):
            events.append("confirm")

    class Confirm:
        @staticmethod
        def get_by_role(*_args, **_kwargs):
            return Button()

        @staticmethod
        def wait_for(**_kwargs):
            events.append("confirmed")

    delete_response = JsonResponse(
        "https://host/api/projects/delete/record-1",
        {"code": 200},
    )
    delete_response.request.method = "DELETE"
    delete_response.request.url = delete_response.url

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type(
        "Page",
        (),
        {"wait_for_timeout": lambda _self, _timeout: events.append("settled")},
    )()

    def open_confirmation(_result, responses):
        responses.append(delete_response)
        return {"name": "AUTO_项目"}, ["AUTO_项目"], [], Confirm()

    def verify_absent(*_args, **_kwargs):
        events.append("verify_absent")
        if not absence_verified:
            raise AssertionError("记录仍然存在")

    monkeypatch.setattr(driver, "_open_delete_confirmation", open_confirmation)
    monkeypatch.setattr(driver, "_install_delete_request_guard", lambda _id: [])
    monkeypatch.setattr(
        driver, "_refresh_list_after_delete", lambda: events.append("refresh")
    )
    monkeypatch.setattr(driver, "_wait_for_deleted_record_absent", verify_absent)
    monkeypatch.setattr(
        driver,
        "_forget_automation_owned_record",
        lambda _id: events.append("forget_registry"),
    )

    result = ModuleSmokeResult(
        mode="delete_reusable_record",
        business_id="record-1",
        record_markers=("AUTO_项目",),
    )
    if absence_verified:
        assert driver.delete_created_record(result).mode == "add_and_delete_verified"
        assert events[-2:] == ["verify_absent", "forget_registry"]
    else:
        with pytest.raises(AssertionError, match="记录仍然存在"):
            driver.delete_created_record(result)
        assert events[-1] == "verify_absent"
        assert "forget_registry" not in events


def test_delete_request_business_id_uses_the_path_value():
    request = type("Request", (), {
        "url": "https://host/api/projDecision/delete/record-1?ignored=true"
    })()

    assert ModuleSmokeDriver._delete_request_business_id(request) == "record-1"


def test_delete_verification_rechecks_identity_not_original_row_position():
    class Page:
        def __init__(self):
            self.rows = [
                DeleteRow(["AUTO_target"]),
                DeleteRow(["AUTO_remaining"]),
            ]
            self.waits = []

        def locator(self, selector):
            assert selector == ".el-table__row:visible"
            return DeleteRows(self.rows)

        def wait_for_timeout(self, timeout):
            self.waits.append(timeout)
            self.rows = [DeleteRow(["AUTO_remaining"])]

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    driver._wait_for_deleted_record_absent("", ["AUTO_target"], timeout=1_000)

    assert driver.page.waits == [300]


def test_delete_verification_refreshes_list_before_checking_identity():
    class RefreshButton:
        def __init__(self, page):
            self.page = page

        @staticmethod
        def inner_text():
            return "刷新"

        @staticmethod
        def is_visible():
            return True

        @staticmethod
        def is_enabled():
            return True

        @staticmethod
        def evaluate(_script):
            return True

        def click(self):
            self.page.refreshes += 1
            self.page.rows = [DeleteRow(["AUTO_remaining"])]

    class Buttons:
        def __init__(self, page):
            self.button = RefreshButton(page)

        @staticmethod
        def count():
            return 1

        def nth(self, _index):
            return self.button

    class HiddenLoading:
        first = None

        def __init__(self):
            self.first = self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Page:
        def __init__(self):
            self.rows = [DeleteRow(["AUTO_target"])]
            self.refreshes = 0

        def locator(self, selector):
            if selector == ".el-table__row:visible":
                return DeleteRows(self.rows)
            if selector == "button:visible,a:visible,[role='button']:visible":
                return Buttons(self)
            return HiddenLoading()

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    driver._refresh_list_after_delete()
    driver._wait_for_deleted_record_absent("", ["AUTO_target"], timeout=0)

    assert driver.page.refreshes == 1


def test_delete_verification_fails_when_identity_still_visible():
    class Page:
        def locator(self, selector):
            assert selector == ".el-table__row:visible"
            return DeleteRows([DeleteRow(["AUTO_target"])])

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    try:
        driver._wait_for_deleted_record_absent("", ["AUTO_target"], timeout=0)
    except AssertionError as exc:
        assert "删除接口成功但本次记录仍在列表中" in str(exc)
    else:
        raise AssertionError("visible automation record was accepted as deleted")


def test_delete_verification_prefers_child_id_absence_in_refreshed_list_response():
    class Page:
        def locator(self, selector):
            assert selector == ".el-table__row:visible"
            return DeleteRows([
                DeleteRow(["重大安全事故", "2026-08-01"]),
            ])

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()
    deleted = JsonResponse(
        "https://host/api/projRiskEvent/delete",
        {"code": 200},
    )
    refreshed = JsonResponse(
        "https://host/api/projRiskEvent/listPage",
        {
            "code": 200,
            "data": {
                "records": [{
                    "id": "another-record",
                    "riskTypeName": "重大安全事故",
                    "riskOccurredDate": "2026-08-01",
                }]
            },
        },
    )

    driver._wait_for_deleted_record_absent(
        "deleted-record",
        [],
        fallback_values=["重大安全事故", "2026-08-01"],
        responses=[deleted, refreshed],
        after_response=deleted,
        timeout=0,
    )


def test_delete_verification_accepts_direct_data_list_without_deleted_id():
    class Page:
        def locator(self, selector):
            assert selector == ".el-table__row:visible"
            return DeleteRows([
                DeleteRow(["\u91cd\u5927\u5b89\u5168\u4e8b\u6545", "2026-08-01"]),
            ])

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()
    deleted = JsonResponse("https://host/api/projRiskEvent/delete", {"code": 200})
    refreshed = JsonResponse(
        "https://host/api/projRiskEvent/listPage",
        {"code": 200, "data": [{"id": "another-record"}]},
    )

    driver._wait_for_deleted_record_absent(
        "deleted-record",
        [],
        fallback_values=["\u91cd\u5927\u5b89\u5168\u4e8b\u6545", "2026-08-01"],
        responses=[deleted, refreshed],
        after_response=deleted,
        timeout=0,
    )


def test_delete_verification_reports_missing_id_evidence_not_visible_record():
    class Page:
        def locator(self, selector):
            assert selector == ".el-table__row:visible"
            return DeleteRows([
                DeleteRow(["\u91cd\u5927\u5b89\u5168\u4e8b\u6545", "2026-08-01"]),
                DeleteRow(["\u91cd\u5927\u5b89\u5168\u4e8b\u6545", "2026-08-01"]),
            ])

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()
    deleted = JsonResponse("https://host/api/projRiskEvent/delete", {"code": 200})

    with pytest.raises(AssertionError, match="\u672a\u63d0\u4f9b\u53ef\u7528\u7684\u4e1a\u52a1 ID \u56de\u8bfb"):
        driver._wait_for_deleted_record_absent(
            "deleted-record",
            [],
            fallback_values=["\u91cd\u5927\u5b89\u5168\u4e8b\u6545", "2026-08-01"],
            responses=[deleted],
            after_response=deleted,
            timeout=0,
        )


def test_delete_list_response_presence_is_scoped_after_delete_and_to_same_resource():
    deleted = JsonResponse(
        "https://host/api/projRiskEvent/delete/deleted-record",
        {"code": 200},
    )
    unrelated = JsonResponse(
        "https://host/api/attachments/listPage",
        {"data": {"records": []}},
    )
    refreshed = JsonResponse(
        "https://host/api/projRiskEvent/listPage",
        {"data": {"records": [{"id": "deleted-record"}]}},
    )

    assert ModuleSmokeDriver._latest_collection_record_presence(
        [unrelated, deleted, unrelated, refreshed],
        after_response=deleted,
        business_id="deleted-record",
    ) is True


def test_delete_verification_does_not_accept_dom_absence_when_list_api_keeps_id():
    class Page:
        def locator(self, selector):
            assert selector == ".el-table__row:visible"
            return DeleteRows([])

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()
    deleted = JsonResponse(
        "https://host/api/projRiskEvent/delete",
        {"code": 200},
    )
    refreshed = JsonResponse(
        "https://host/api/projRiskEvent/listPage",
        {"data": {"records": [{"id": "deleted-record"}]}},
    )

    with pytest.raises(AssertionError, match="列表接口仍返回本次业务 ID"):
        driver._wait_for_deleted_record_absent(
            "deleted-record",
            [],
            fallback_values=["重大安全事故", "2026-08-01"],
            responses=[deleted, refreshed],
            after_response=deleted,
            timeout=0,
        )


def test_fill_dialog_scopes_dynamic_nested_row_fields(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("parentField", "父表单字段", False),
        ("ownershipStructureList.*.stockName", "股东名称", False),
    ]
    driver.page = type("Page", (), {"wait_for_timeout": lambda self, timeout: None})()
    driver._dom_field_has_value = lambda field, **_kwargs: False
    driver.data_strategy = type("Strategy", (), {
        "value_for": lambda self, field, index: f"nested-{field.field_name}"
    })()
    driver.interactor = type("Interactor", (), {
        "fill": lambda self, resolved, value, **_kwargs: value
    })()
    row = object()
    rendered = [DomField("el-id-1", "股东名称", "text", "#stock-name", required=True)]
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: rendered if root is row else [],
    )

    submitted = driver._fill_dialog(dom_scope=row)

    assert submitted == {
        "ownershipStructureList.*.stockName": "nested-股东名称"
    }
    assert driver._fill_failures == []


def test_dynamic_collection_contract_requires_mode_path_and_child_fields():
    driver = object.__new__(ModuleSmokeDriver)

    class Scope:
        @staticmethod
        def evaluate(_script):
            return [{
                "fieldCode": "riskItems",
                "path": "items",
                "mode": "selection",
                "minRows": 1,
                "childFields": ["description"],
            }]

    assert driver._dynamic_collection_contracts(Scope()) == [{
        "fieldCode": "riskItems",
        "path": "items",
        "mode": "selection",
        "minRows": 1,
        "childFields": ["description"],
    }]

    class InvalidScope:
        @staticmethod
        def evaluate(_script):
            return [{
                "fieldCode": "riskItems",
                "path": "items",
                "mode": "selection",
                "minRows": 1,
                "childFields": [],
            }]

    with pytest.raises(AssertionError, match="动态字段契约缺失：riskItems"):
        driver._dynamic_collection_contracts(InvalidScope())


def test_fill_dialog_prepares_dynamic_collection_before_regular_field_scan(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    collection_scope = type("Scope", (), {
        "evaluate": staticmethod(lambda _script: []),
    })()
    driver._form_scope_for_collections = collection_scope
    driver._collection_submission_codes = set()
    driver.dynamic_collections = []
    driver.page = type("Page", (), {"wait_for_timeout": lambda self, timeout: None})()
    driver._prepare_dynamic_collection_baselines = lambda scope: {
        "items.0.description": "AUTO_集合说明",
        "riskItems": "1",
    }
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields", lambda page, root=None: []
    )

    assert driver._fill_dialog() == {
        "items.0.description": "AUTO_集合说明",
        "riskItems": "1",
    }


def test_fill_dialog_prepares_configured_collection_before_regular_field_scan(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    driver.dynamic_collections = [object()]
    collection_scope = type("Scope", (), {
        "evaluate": staticmethod(lambda _script: []),
    })()
    driver._form_scope_for_collections = collection_scope
    driver._collection_submission_codes = set()
    driver.page = type("Page", (), {"wait_for_timeout": lambda self, timeout: None})()
    driver._prepare_configured_dynamic_collections = lambda scope: {
        "items.0.description": "AUTO_manifest_description",
        "riskItems": "selection",
    }
    driver._prepare_dynamic_collection_baselines = lambda scope: {}
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields", lambda page, root=None: []
    )

    assert driver._fill_dialog() == {
        "items.0.description": "AUTO_manifest_description",
        "riskItems": "selection",
    }


def test_configured_collection_reports_missing_root_as_contract_error():
    class MissingRoot:
        first = None

        def __init__(self):
            self.first = self

        @staticmethod
        def count():
            return 0

        @staticmethod
        def is_visible():
            return False

    class Scope:
        @staticmethod
        def locator(_selector):
            return MissingRoot()

    driver = object.__new__(ModuleSmokeDriver)
    driver.dynamic_collections = [DynamicCollectionSpec(
        field_code="riskItems",
        mode="selection",
        root_selector="[field-code='riskItems']",
        create_selector=".choice",
        item_selector=".row",
        min_rows=1,
        children=(DynamicCollectionChild("items.{index}.description", "textarea"),),
    )]

    with pytest.raises(DynamicFieldContractError, match="riskItems"):
        driver._prepare_configured_dynamic_collections(Scope())


def test_configured_collection_activates_visible_wrapper_not_native_input():
    events = []

    class Trigger:
        def scroll_into_view_if_needed(self):
            events.append("scroll")

        def click(self, *, force):
            events.append(("click", force))

    spec = DynamicCollectionSpec(
        field_code="adjustmentItems",
        mode="selection",
        root_selector=".adjustment-type-form",
        create_selector=".el-checkbox",
        item_selector=".adjustment-type-item",
        min_rows=1,
        children=(),
    )

    ModuleSmokeDriver._activate_configured_collection_trigger(Trigger(), spec)

    assert events == ["scroll", ("click", True)]


def test_configured_collection_trigger_failure_is_a_contract_error():
    class Trigger:
        @staticmethod
        def scroll_into_view_if_needed():
            return None

        @staticmethod
        def click(**_kwargs):
            raise RuntimeError("native input is clipped")

    spec = DynamicCollectionSpec(
        field_code="adjustmentItems",
        mode="selection",
        root_selector=".adjustment-type-form",
        create_selector=".el-checkbox",
        item_selector=".adjustment-type-item",
        min_rows=1,
        children=(),
    )

    with pytest.raises(DynamicFieldContractError, match="adjustmentItems"):
        ModuleSmokeDriver._activate_configured_collection_trigger(Trigger(), spec)


def test_declared_unique_constraint_updates_year_control_before_save(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()
    driver.source_fields = [("assetYear", "年度", False)]
    driver._validation_repairs = []
    observed = []

    class Strategy:
        @staticmethod
        def declared_unique_repair_fields(submitted):
            assert submitted["belongSection"] == "1"
            return ("assetYear",)

        @staticmethod
        def allocate_unique_value(definition, current):
            assert definition.props["domKind"] == "year"
            assert current == "2026"
            return "2027", {"kind": "unique", "sequence": 1}

    class Interactor:
        @staticmethod
        def fill(resolved, value, *, root=None):
            observed.append((resolved.definition.field_code, value, root))
            return value

    scope = object()
    driver.data_strategy = Strategy()
    driver.interactor = Interactor()
    driver._source_for_dom = lambda dom, index: (
        "assetYear", "年度", False, "assetYear", "年度", True
    )
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: [
            DomField("assetYear", "年度", "year", "#asset-year", required=True)
        ],
    )

    prepared = driver._prepare_declared_unique_values(
        scope,
        {"belongSection": "1", "assetYear": "2026"},
    )

    assert prepared == {"assetYear": "2027"}
    assert observed == [("assetYear", "2027", scope)]
    assert driver._validation_repairs[0]["source"] == "declaredUniqueConstraint"


def test_declared_unique_constraint_does_not_override_target_field(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()
    driver.data_strategy = type("Strategy", (), {
        "declared_unique_repair_fields": lambda self, submitted: ("assetYear",),
        "allocate_unique_value": lambda self, definition, current: ("2027", {}),
    })()

    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page, root=None: (_ for _ in ()).throw(
            AssertionError("excluded field must not scan or change the control")
        ),
    )

    assert driver._prepare_declared_unique_values(
        object(),
        {"belongSection": "1", "assetYear": "2026"},
        exclude_codes={"assetYear"},
    ) == {}


def test_default_readback_uses_collection_child_paths_not_wrapper_field():
    driver = object.__new__(ModuleSmokeDriver)
    driver._collection_submission_codes = {"riskItems"}

    assert driver._default_readback_required_codes({
        "matterName": "AUTO_事项",
        "riskItems": "1",
        "items.0.description": "AUTO_集合说明",
    }) == {"matterName", "items.0.description"}


def test_nested_runtime_identity_uses_stable_code_or_unique_business_label():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("matterName", "事项名称", False),
        ("financeSources.*.sourceFrom", "资金来源", False),
        ("financeSources.*.amount", "预算金额（万元）", False),
        ("financeSources.*.fundsPlan", "资金筹措方案", False),
    ]

    runtime_code = driver._runtime_identity_for_nested_dom(
        DomField(
            "financeSources.0.amount",
            "预算金额（万元）",
            "number",
            "#amount",
        ),
        1,
    )
    generated_code = driver._runtime_identity_for_nested_dom(
        DomField("el-id-2", "资金筹措方案", "textarea", "#plan"),
        2,
    )

    assert runtime_code[:2] == (
        "financeSources.*.amount",
        "预算金额（万元）",
    )
    assert generated_code[:2] == (
        "financeSources.*.fundsPlan",
        "资金筹措方案",
    )
    assert runtime_code[-1] is True
    assert generated_code[-1] is True


def test_nested_runtime_identity_rejects_ambiguous_label_mapping():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("financeSources.*.remark", "说明", False),
        ("ownershipStructureList.*.remark", "说明", False),
    ]

    identity = driver._runtime_identity_for_nested_dom(
        DomField("el-id-3", "说明", "text", "#remark"),
        1,
    )

    assert identity[0] == "el-id-3"
    assert identity[-1] is False


def test_common_form_scope_drives_fill_completion_and_retry_scans(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("name", "名称", False)]
    driver.page = type("Page", (), {"wait_for_timeout": lambda self, timeout: None})()
    form_scope = object()
    driver._common_form_scope = form_scope
    state = {"filled": False}
    driver._dom_field_has_value = lambda field, **_kwargs: state["filled"]
    driver.data_strategy = type(
        "Strategy", (), {"value_for": lambda self, field, index: "AUTO_名称"},
    )()
    roots = []

    class Interactor:
        def fill(self, resolved, value, *, root=None):
            roots.append(root)
            state["filled"] = True
            return value

    driver.interactor = Interactor()
    rendered = [
        DomField("el-id-1", "名称", "text", "#name", required=True)
    ]
    scan_roots = []

    def scan(_page, root=None):
        scan_roots.append(root)
        return rendered

    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", scan)

    submitted = driver._fill_dialog()
    report = driver.check_field_completion(submitted, driver._fill_failures)
    retry = driver._retry_field_codes(submitted)

    assert submitted == {"name": "AUTO_名称"}
    assert report.ok
    assert retry == set()
    assert roots == [form_scope]
    assert scan_roots and all(root is form_scope for root in scan_roots)


def test_nested_values_must_exist_in_save_and_detail_payloads():
    driver = object.__new__(ModuleSmokeDriver)
    driver._nested_evidence = [
        {"section": "股权结构", "field": "股东名称", "value": "AUTO_股东"},
        {"section": "股权结构", "field": "持股比例", "value": "12"},
    ]

    driver._assert_nested_values_in_payload({
        "ownershipStructureList": [{"stockName": "AUTO_股东", "stockPercent": "12"}]
    }, stage="保存请求")

    try:
        driver._assert_nested_values_in_payload({
            "ownershipStructureList": []
        }, stage="详情响应")
    except AssertionError as exc:
        assert "详情响应没有持久化嵌套行字段" in str(exc)
        assert "AUTO_股东" in str(exc)
    else:
        raise AssertionError("empty nested detail data must fail")


def test_detail_assertion_ignores_non_target_nested_baseline_fields():
    payload = {
        "data": {
            "id": "record-1",
            "matterName": "AUTO_事项",
            "financeSources": [{
                "sourceFrom": "1",
                "fundsPlan": "本轮筹措方案",
            }],
        }
    }
    submitted = {
        "matterName": "AUTO_事项",
        "financeSources.*.sourceFrom": "自有资金",
        "financeSources.*.fundsPlan": "本轮筹措方案",
    }

    ModuleSmokeDriver._assert_detail_values(
        payload,
        submitted,
        required_codes={"matterName", "financeSources.*.fundsPlan"},
        business_id="record-1",
    )


def test_nested_detail_assertion_ignores_non_target_support_fields():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("financeSources.*.amount", "预算金额（万元）", True),
        ("financeSources.*.fundsPlan", "资金筹措方案", True),
    ]
    driver._nested_evidence = [
        {
            "section": "预算及资金来源明细子表",
            "field": "预算金额（万元）",
            "code": "financeSources.*.amount",
            "submitted_key": "financeSources.*.amount",
            "value": "616016.0",
        },
        {
            "section": "预算及资金来源明细子表",
            "field": "资金筹措方案",
            "code": "financeSources.*.fundsPlan",
            "submitted_key": "financeSources.*.fundsPlan",
            "value": "本轮筹措方案",
        },
    ]

    driver._assert_nested_values_in_payload(
        {"data": {"financeSources": [{"fundsPlan": "本轮筹措方案"}]}},
        stage="详情响应",
        required_codes={"financeSources.*.fundsPlan"},
    )


def test_nested_payload_assertion_canonicalizes_semantic_numeric_values():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("financeSources.*.amount", "预算金额（万元）", True),
    ]
    driver._nested_evidence = [{
        "section": "预算及资金来源明细子表",
        "field": "预算金额（万元）",
        "code": "financeSources.*.amount",
        "submitted_key": "financeSources.*.amount",
        "value": "616016.0",
    }]

    driver._assert_nested_values_in_payload(
        {"data": {"financeSources": [{"amount": "616,016"}]}},
        stage="详情响应",
        required_codes={"financeSources.*.amount"},
    )


class NestedOpenFormValues:
    def __init__(self, values):
        self.values = values

    def evaluate_all(self, script):
        assert "el.value" in script
        return self.values


class NestedOpenFormSection:
    def __init__(self, values):
        self.values = values
        self.last = self

    def wait_for(self, **kwargs):
        assert kwargs == {"state": "visible", "timeout": 10_000}

    def locator(self, selector):
        if selector.startswith("xpath=ancestor::"):
            return self
        assert selector == "input,textarea"
        return NestedOpenFormValues(self.values)


class NestedOpenFormPage:
    def __init__(self, values_by_section):
        self.values_by_section = values_by_section

    def get_by_text(self, text, *, exact):
        assert exact is True
        return NestedOpenFormSection(self.values_by_section[text])


def test_nested_open_form_canonicalizes_semantic_numeric_values():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = NestedOpenFormPage({
        "预算及资金来源明细子表": ["538,875"],
    })
    driver._nested_evidence = [{
        "section": "预算及资金来源明细子表",
        "field": "预算金额（万元）",
        "code": "financeSources.*.amount",
        "submitted_key": "financeSources.*.amount",
        "value": "538875.0",
    }]

    driver._assert_nested_values_in_open_form()


def test_nested_open_form_keeps_non_numeric_text_comparison_strict():
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = NestedOpenFormPage({
        "预算及资金来源明细子表": ["本轮筹措方案追加"],
    })
    driver._nested_evidence = [{
        "section": "预算及资金来源明细子表",
        "field": "资金筹措方案",
        "code": "financeSources.*.fundsPlan",
        "submitted_key": "financeSources.*.fundsPlan",
        "value": "本轮筹措方案",
    }]

    with pytest.raises(AssertionError, match="保存后编辑表单没有回显嵌套行字段"):
        driver._assert_nested_values_in_open_form()


def test_detail_accepts_nested_display_value_when_save_and_detail_codes_match():
    ModuleSmokeDriver._assert_detail_values(
        {
            "data": {
                "id": "record-1",
                "financeSources": [{"sourceFrom": "1"}],
            }
        },
        {"financeSources.*.sourceFrom": "自有资金"},
        required_codes={"financeSources.*.sourceFrom"},
        business_id="record-1",
        submitted_payload={"financeSources": [{"sourceFrom": "1"}]},
    )


def test_detail_rejects_nested_display_value_when_saved_code_differs():
    with pytest.raises(AssertionError, match="无法比较"):
        ModuleSmokeDriver._assert_detail_values(
            {
                "data": {
                    "id": "record-1",
                    "financeSources": [{"sourceFrom": "2"}],
                }
            },
            {"financeSources.*.sourceFrom": "自有资金"},
            required_codes={"financeSources.*.sourceFrom"},
            business_id="record-1",
            submitted_payload={"financeSources": [{"sourceFrom": "1"}]},
        )


def test_nested_payload_assertion_uses_current_submitted_value_over_baseline():
    driver = object.__new__(ModuleSmokeDriver)
    driver._nested_evidence = [{
        "section": "budget",
        "field": "fundsPlan",
        "code": "financeSources.*.fundsPlan",
        "submitted_key": "budget.financeSources.*.fundsPlan",
        "value": "OLD_BASELINE",
    }]

    driver._assert_nested_values_in_payload(
        {"financeSources": [{"fundsPlan": "NEW_CASE_VALUE"}]},
        stage="save request",
        submitted={"financeSources.*.fundsPlan": "NEW_CASE_VALUE"},
    )

    assert driver._nested_evidence[0]["value"] == "NEW_CASE_VALUE"


def test_nested_payload_assertion_refreshes_retained_support_value_from_save():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("financeSources.*.amount", "预算金额（万元）", True),
        ("financeSources.*.fundsPlan", "资金筹措方案", True),
    ]
    driver._nested_evidence = [{
        "section": "预算及资金来源明细子表",
        "field": "预算金额（万元）",
        "code": "financeSources.*.amount",
        "submitted_key": "financeSources.*.amount",
        "value": "OLD_BASELINE",
    }]

    driver._assert_nested_values_in_payload(
        {"financeSources": [{"amount": "906678.38"}]},
        stage="save request",
        submitted={"financeSources.*.fundsPlan": "本轮目标值"},
        synchronize_support=True,
    )

    assert driver._nested_evidence[0]["value"] == "906678.38"


def test_nested_payload_assertion_recovers_generated_baseline_identity():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("financeSources.*.fundsPlan", "资金筹措方案", False),
    ]
    driver._nested_evidence = [{
        "section": "预算及资金来源明细子表",
        "field": "资金筹措方案",
        "code": "el-id-3793-97",
        "submitted_key": "预算及资金来源明细子表.el-id-3793-97",
        "value": "OLD_BASELINE",
    }]

    driver._assert_nested_values_in_payload(
        {"financeSources": [{"fundsPlan": "NEW_CASE_VALUE"}]},
        stage="保存请求",
        submitted={"financeSources.*.fundsPlan": "NEW_CASE_VALUE"},
    )

    assert driver._nested_evidence == [{
        "section": "预算及资金来源明细子表",
        "field": "资金筹措方案",
        "code": "financeSources.*.fundsPlan",
        "submitted_key": "financeSources.*.fundsPlan",
        "value": "NEW_CASE_VALUE",
    }]


def test_nested_evidence_reset_clears_previous_form_state():
    driver = object.__new__(ModuleSmokeDriver)
    driver._nested_evidence = [{
        "section": "budget",
        "field": "fundsPlan",
        "value": "OLD_BASELINE",
    }]

    driver._reset_nested_evidence()

    assert driver._nested_evidence == []


def test_field_completion_report_uses_actual_visible_control_values(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("name", "名称", False), ("missingCode", "未渲染字段", False)]
    rendered = [
        DomField("name", "名称", "text", "#name"),
        DomField("amount", "金额", "number", "#amount", required=True),
    ]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered)
    driver.page = object()
    driver._dom_field_has_value = lambda field, **_kwargs: field.field_code == "name"

    report = driver.check_field_completion({"name": "测试"})

    assert report.not_located == []
    assert driver._expected_not_rendered == ["未渲染字段 (missingCode)"]
    assert report.not_filled == ["金额 (amount)"]
    assert "字段未成功输入" in report.message()


def test_field_completion_does_not_map_option_text_by_source_order(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("projName", "项目名称", False),
        ("projClassify", "项目类型", False),
        ("belongSection", "责任板块", False),
        ("inveId", "实施主体公司", False),
    ]
    rendered = [
        DomField("el-id-58", "", "select", "#type", required=True),
        DomField("el-id-61", "是", "radio", "#decision", required=True),
    ]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page, root=None: rendered)
    driver.page = object()
    driver._common_form_scope = None
    driver._dom_field_has_value = lambda field, **_kwargs: False

    report = driver.check_field_completion({})

    assert "项目类型 (projClassify)" not in report.not_filled
    assert "责任板块 (belongSection)" not in report.not_filled
    assert "el-id-58" in report.not_filled
    assert "是 (el-id-61)" in report.not_filled


def test_generated_boolean_radio_can_recover_source_identity_by_semantics():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("projName", "项目名称", False),
        ("projClassify", "项目类型", False),
        ("belongSection", "责任板块", False),
        ("inveId", "实施主体公司", False),
        ("isGmoDecision", "是否需总经办决策", False),
    ]

    generated = DomField("el-id-762-935", "", "radio", "#el-id-762-935", required=True)

    code, label, _qcc, source_code, source_label, identity_safe = (
        driver._runtime_identity_for_dom(generated, 5)
    )

    assert identity_safe
    assert code == "isGmoDecision"
    assert label == "是否需总经办决策"
    assert source_code == "isGmoDecision"
    assert source_label == "是否需总经办决策"


def test_generated_radio_option_text_uses_unique_boolean_source_when_order_shifted():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("projName", "项目名称", False),
        ("projClassify", "项目类型", False),
        ("belongSection", "责任板块", False),
        ("inveId", "实施主体公司", False),
        ("shortIntro", "项目基本情况", False),
        ("isGmoDecision", "是否需总经办决策", False),
        ("buildTarget", "项目建设目的", False),
    ]

    generated = DomField(
        "el-id-762-53", "是", "radio", "#el-id-762-53", required=True
    )

    code, label, _qcc, source_code, source_label, identity_safe = (
        driver._runtime_identity_for_dom(generated, 5)
    )

    assert identity_safe
    assert code == "isGmoDecision"
    assert label == "是否需总经办决策"
    assert source_code == "isGmoDecision"
    assert source_label == "是否需总经办决策"


def test_generated_radio_option_text_without_unique_source_does_not_use_position():
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [
        ("isBoardDecision", "是否经过董事会", False),
        ("isGmoDecision", "是否需总经办决策", False),
    ]

    generated = DomField(
        "el-id-762-53", "是", "radio", "#el-id-762-53", required=True
    )

    code, label, _qcc, source_code, source_label, identity_safe = (
        driver._runtime_identity_for_dom(generated, 1)
    )

    assert not identity_safe
    assert code == "el-id-762-53"
    assert label == "是"
    assert source_code == "el-id-762-53"
    assert source_label == "是"


def test_radio_group_fallback_drills_into_real_radio_controls():
    class Locator:
        def __init__(self, name, count=1):
            self.name = name
            self._count = count

        def count(self):
            return self._count

        def locator(self, selector):
            assert selector == 'input[type="radio"],[role="radio"]'
            return Locator("nested-radio", 2)

    class Root:
        def locator(self, selector):
            if selector.startswith('[prop="isGmoDecision"]'):
                return Locator("stable-prop", 0)
            assert selector == "#radio-group"
            return Locator("group", 1)

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Root()

    assert driver._radio_group("isGmoDecision", "#radio-group").name == "nested-radio"


def test_radio_group_uses_source_label_container_when_prop_missing():
    class Locator:
        def __init__(self, name, count=0):
            self.name = name
            self._count = count

        def count(self):
            return self._count

        def locator(self, selector):
            return Locator(f"{self.name} nested {selector}", 0)

    class Root:
        def locator(self, selector):
            if selector.startswith('[prop="isGmoDecision"]'):
                return Locator("stable-prop", 0)
            if '是否需总经办决策' in selector:
                return Locator("labelled-radio", 2)
            if selector == "#stale-generated-id":
                return Locator("stale", 0)
            return Locator(selector, 0)

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Root()
    driver.source_fields = [("isGmoDecision", "是否需总经办决策", False)]

    assert driver._radio_group("isGmoDecision", "#stale-generated-id").name == "labelled-radio"


def test_hidden_conditional_source_field_is_not_reported_as_unlocated(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("conditional", "条件字段", False)]
    driver.page = object()
    driver._source_field_is_visible = lambda label: False
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: [])

    assert driver.check_field_completion({}).ok


def test_optional_unfilled_field_is_reported_but_does_not_block(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("relationId", "关联文件", False)]
    driver.page = object()
    driver._optional_fill_failures = ["关联文件 (relationId): 没有可选业务数据"]
    driver._dom_field_has_value = lambda field, **_kwargs: False
    monkeypatch.setattr(
        "ei_ui_smoke.module_driver.scan_dom_fields",
        lambda page: [DomField("relationId", "关联文件", "select", "#relation")],
    )

    report = driver.check_field_completion({})

    assert report.ok
    assert report.optional_not_filled == ["关联文件 (relationId)"]
    assert report.optional_fill_failed == ["关联文件 (relationId): 没有可选业务数据"]


def test_optional_relationship_selects_are_not_arbitrarily_populated():
    assert {"parentid", "relationid"} == ModuleSmokeDriver.OPTIONAL_RELATIONSHIP_FIELDS


def test_retry_targets_only_empty_and_failed_fields(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("name", "名称", False), ("email", "邮箱", False)]
    driver.page = object()
    driver._fill_failures = ["邮箱 (email): timeout"]
    rendered = [
        DomField("name", "名称", "text", "#name", required=True),
        DomField("email", "邮箱", "text", "#email", required=True),
    ]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered)
    driver._dom_field_has_value = lambda field, **_kwargs: field.field_code == "name"

    assert driver._retry_field_codes({"name": "已成功", "email": "旧值"}) == {"email"}


def test_field_with_page_default_is_not_reported_as_empty(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    rendered = [DomField("status", "状态", "select", "#status")]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered)
    driver.page = object()
    driver._dom_field_has_value = lambda field, **_kwargs: True

    report = driver.check_field_completion({})

    assert report.ok


def test_custom_select_accepts_confirmed_semantic_value(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("company", "关联组织机构", False)]
    rendered = [DomField("company", "关联组织机构", "select", "#company")]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered)
    driver.page = object()
    driver._dom_field_has_value = lambda field, **_kwargs: False

    report = driver.check_field_completion({"company": "组织机构A"})

    assert report.ok


def test_compound_field_keeps_primary_value_instead_of_child_display_value():
    submitted = {"amount": 100}
    submitted.setdefault("amount", "人民币")

    assert submitted["amount"] == 100


def test_default_attachment_skips_existing_file_and_does_not_upload_again(tmp_path):
    attachment = tmp_path / "attachment.jpg"
    attachment.write_bytes(b"image")
    existing = FileInput(has_value=True)
    empty = FileInput()
    driver = object.__new__(ModuleSmokeDriver)
    driver.default_upload_file = attachment
    driver.page = type("Page", (), {"wait_for_timeout": lambda self, timeout: None})()
    driver._file_input_has_value = lambda item: item.has_value
    dialog = FileDialog([existing, empty])

    assert driver._upload_default_attachments(dialog) == 1
    assert existing.uploads == []
    assert len(empty.uploads) == 1
    assert driver._upload_default_attachments(dialog) == 0
    assert len(empty.uploads) == 1


def test_field_completion_uses_the_same_existing_attachment_evidence():
    class Locator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            return False

    locator = Locator()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type(
        "Page", (), {"locator": lambda _self, selector: locator}
    )()
    checked = []
    driver._file_input_has_value = lambda item: checked.append(item) or True
    field = DomField(
        "file:report", "立项报告", "file", "#report", required=True
    )

    assert driver._dom_field_has_value(field)
    assert checked == [locator]

    driver._file_input_has_value = lambda _item: False
    assert not driver._dom_field_has_value(field)


def test_field_completion_accepts_hidden_checked_choice_input():
    class Locator:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def is_visible():
            raise AssertionError("hidden choice value must be checked before visibility")

        @staticmethod
        def evaluate(script):
            assert "input:checked" in script
            return True

    locator = Locator()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = type(
        "Page", (), {"locator": lambda _self, selector: locator}
    )()
    field = DomField(
        "isGmoDecision", "是否需总经办决策", "radio", "#hidden-radio",
        required=True,
    )

    assert driver._dom_field_has_value(field)


def test_choice_value_check_uses_stable_container_when_generated_selector_is_stale():
    class EmptyChoice:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 0

    class CheckedNode:
        @staticmethod
        def count():
            return 1

    class FieldContainer:
        @property
        def first(self):
            return self

        @staticmethod
        def count():
            return 1

        @staticmethod
        def locator(selector):
            assert "input:checked" in selector
            return CheckedNode()

    class Root:
        def __init__(self):
            self.selectors = []

        def locator(self, selector):
            self.selectors.append(selector)
            if selector == "#el-id-4643-66":
                return EmptyChoice()
            if "isGmoDecision" in selector or "是否需总经办决策" in selector:
                return FieldContainer()
            return EmptyChoice()

    root = Root()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = object()
    field = DomField(
        "el-id-4643-66", "是", "radio", "#el-id-4643-66", required=True,
    )

    assert driver._dom_field_has_value(
        field, root=root,
        field_code="isGmoDecision", field_label="是否需总经办决策",
    )
    assert "#el-id-4643-66" in root.selectors
    assert any("isGmoDecision" in selector for selector in root.selectors)


def test_attachment_with_ui_success_does_not_require_immediate_network_request(tmp_path):
    attachment = tmp_path / "attachment.jpg"
    attachment.write_bytes(b"image")

    class EventPage:
        def __init__(self):
            self.listeners = {}

        def on(self, event, callback):
            self.listeners[event] = callback

        def remove_listener(self, event, callback):
            self.listeners.pop(event, None)

        def wait_for_timeout(self, timeout):
            return None

    driver = object.__new__(ModuleSmokeDriver)
    driver.default_upload_file = attachment
    driver.page = EventPage()
    driver._file_input_has_value = lambda item: item.has_value
    dialog = FileDialog([FileInput()])

    assert driver._upload_default_attachments(dialog) == 1
    assert driver.last_attachment_report.status == "completed"
    assert driver.last_attachment_report.requests_observed == 0


def test_attachment_value_check_requires_file_or_explicit_success_state():
    source = ModuleSmokeDriver._file_input_has_value.__code__.co_consts
    script = " ".join(value for value in source if isinstance(value, str))

    assert "el.files && el.files.length > 0" in script
    assert "el-upload-list__item.is-success" in script
    assert "ant-upload-list-item.ant-upload-list-item-done" in script
    assert "el-upload-list__item-name" not in script
    assert "ant-upload-list-item-name" not in script
    assert '[class*="upload-list"] [class*="file-name"]' not in script


def test_attachment_upload_waits_for_component_success_state():
    source = ModuleSmokeDriver._wait_for_file_upload.__code__.co_consts
    scripts = " ".join(value for value in source if isinstance(value, str))
    assert "el-upload-list__item.is-uploading" in scripts
    assert "el-upload-list__item.is-success" in scripts
    assert "ant-upload-list-item-done" in scripts
    assert "upload-error" in scripts


def test_attachment_upload_wait_is_scoped_to_the_new_file_name():
    handle = object()

    class Input:
        @staticmethod
        def element_handle():
            return handle

        @staticmethod
        def evaluate(_script):
            return False

    class Page:
        def wait_for_function(self, script, *, arg, timeout):
            assert arg == {"input": handle, "fileName": "new-file.jpg"}
            assert "targetRows" in script
            assert "rowNames(row).includes(fileName)" in script
            assert timeout == 30_000

    driver = object.__new__(ModuleSmokeDriver)
    driver.page = Page()

    driver._wait_for_file_upload(Input(), "new-file.jpg")


def test_attachment_upload_waits_for_all_network_requests_to_finish():
    assert ModuleSmokeDriver._is_attachment_lifecycle_request(
        type("Request", (), {
            "method": "POST",
            "url": "https://host/foundation/oss/endpoint/put-file-attach",
        })()
    )
    assert ModuleSmokeDriver._is_attachment_lifecycle_request(
        type("Request", (), {
            "method": "GET",
            "url": "https://host/foundation/oss/endpoint/file-lists",
        })()
    )
    assert ModuleSmokeDriver._is_attachment_lifecycle_request(
        type("Request", (), {
            "method": "POST",
            "url": "https://host/ezgo-uim/tenantForm/updateFormData",
        })()
    )
    assert not ModuleSmokeDriver._is_attachment_lifecycle_request(
        type("Request", (), {
            "method": "POST",
            "url": "https://host/fi-service/projAppInfo/update",
        })()
    )


def test_attachment_lifecycle_waits_for_delayed_request_completion():
    class Response:
        status = 200
        ok = True
        url = "https://host/foundation/oss/endpoint/merge-group-files"

        @staticmethod
        def json():
            return {"code": 200}

    class Request:
        method = "POST"
        url = Response.url
        response = Response()

    class Page:
        def __init__(self):
            self.listeners = {}
            self.waits = 0

        def on(self, event, callback):
            self.listeners[event] = callback

        def remove_listener(self, event, callback):
            assert self.listeners[event] is callback
            self.listeners.pop(event)

        def wait_for_timeout(self, _timeout):
            self.waits += 1
            if self.waits == 1:
                self.listeners["requestfinished"](request)

    page = Page()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page
    tracker = driver._start_attachment_lifecycle_tracking()
    request = Request()
    page.listeners["request"](request)

    driver._wait_for_attachment_lifecycle(
        tracker, phase="附件保存", quiet_ms=0,
    )
    driver._stop_attachment_lifecycle_tracking(tracker)

    assert page.waits == 1
    assert not tracker.pending
    assert tracker.validated_responses == {id(request.response)}
    assert page.listeners == {}


def test_attachment_lifecycle_request_failure_fails_case():
    class Request:
        method = "POST"
        url = "https://host/foundation/oss/endpoint/put-file-attach?token=secret"
        response = None
        failure = "net::ERR_CONNECTION_RESET"

    class Page:
        def __init__(self):
            self.listeners = {}

        def on(self, event, callback):
            self.listeners[event] = callback

        def remove_listener(self, event, _callback):
            self.listeners.pop(event)

        @staticmethod
        def wait_for_timeout(_timeout):
            pass

    page = Page()
    driver = object.__new__(ModuleSmokeDriver)
    driver.page = page
    tracker = driver._start_attachment_lifecycle_tracking()
    request = Request()
    page.listeners["request"](request)
    page.listeners["requestfailed"](request)

    with pytest.raises(AssertionError, match="ERR_CONNECTION_RESET") as exc:
        driver._wait_for_attachment_lifecycle(
            tracker, phase="附件上传", quiet_ms=0,
        )

    assert "token=secret" not in str(exc.value)


def test_field_completion_report_includes_fill_exception(monkeypatch):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = []
    driver.page = object()
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: [])

    report = driver.check_field_completion({}, ["名称 (name): timeout"])

    assert not report.ok
    assert "名称 (name): timeout" in report.message()


def test_writes_machine_readable_field_diagnostics(monkeypatch, tmp_path):
    driver = object.__new__(ModuleSmokeDriver)
    driver.source_fields = [("name", "名称", False)]
    driver.page = type("Page", (), {"url": "https://host/form?token=secret"})()
    driver._dom_field_has_value = lambda field, **_kwargs: False
    rendered = [DomField("name", "名称", "text", "#name", required=True)]
    monkeypatch.setattr("ei_ui_smoke.module_driver.scan_dom_fields", lambda page: rendered)
    monkeypatch.setenv("EI_FIELD_DIAGNOSTICS_DIR", str(tmp_path))
    monkeypatch.setenv("EI_MODULE_ID", "fund/basic")

    target = driver.write_field_diagnostics(
        driver.check_field_completion({}), {}, attempts=2
    )

    content = target.read_text(encoding="utf-8")
    assert target.name == "fund_basic.json"
    assert '"status": "needs_repair"' in content
    assert '"pageUrl": "https://host/form"' in content
    assert '"selector": "#name"' in content
    assert '"attachment"' in content
    assert (tmp_path / "latest.json").is_file()
