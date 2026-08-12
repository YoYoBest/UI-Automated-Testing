import json
from pathlib import Path

from ei_ui_smoke.zentao import (
    _add_allure_error_attachments,
    _title_error_detail,
    _zentao_bug_page_exists,
    _classify_failure,
    _find_existing_bug,
    _bug_id_from_save_result,
    _bug_id_from_hrefs,
    _module_value,
    _steps_html,
    _suite_title_prefix,
    drafts_from_allure,
    process_allure_failures,
)


def test_drafts_from_allure_extracts_failure_and_image(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(b"png")
    (tmp_path / "failure-url.txt").write_text(
        "http://example.test/basicConfig/settings", encoding="utf-8"
    )
    result = {
        "name": "自动新增及详情核对",
        "status": "failed",
        "statusDetails": {"message": "AssertionError: 未完成新增"},
        "labels": [{"name": "story", "value": "基础管理"}],
        "parameters": [{"name": "module_id", "value": "'basicConfigSetting'"}],
        "attachments": [
            {"name": "失败页面 URL", "source": "failure-url.txt", "type": "text/plain"},
            {"name": "失败页面截图", "source": "shot.png", "type": "image/png"},
        ],
    }
    (tmp_path / "case-result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    drafts = drafts_from_allure(tmp_path)

    assert len(drafts) == 1
    assert drafts[0].module == "基础管理"
    assert drafts[0].title == "【基础管理】自动新增及详情核对失败，未完成新增"
    assert "未完成新增" in drafts[0].steps
    assert "4. 失败页面 URL：http://example.test/basicConfig/settings" in drafts[0].steps
    assert drafts[0].screenshots == (tmp_path / "shot.png",)
    assert (drafts[0].severity, drafts[0].priority) == (3, 3)
    assert drafts[0].assessment_reason


def test_draft_title_uses_business_module_before_page_story(tmp_path: Path) -> None:
    (tmp_path / "case-result.json").write_text(json.dumps({
        "name": "责任板块：固定码值",
        "status": "failed",
        "statusDetails": {"message": "AssertionError: 下拉框存在重复选项"},
        "labels": [
            {"name": "parentSuite", "value": "建设项目"},
            {"name": "feature", "value": "建设项目"},
            {"name": "suite", "value": "建设项目"},
            {"name": "subSuite", "value": "新增项目"},
            {"name": "story", "value": "新增项目"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    draft = drafts_from_allure(tmp_path)[0]

    assert draft.module == "建设项目"
    assert draft.title == "【建设项目】-【新增项目】责任板块：固定码值失败，下拉框存在重复选项"
    assert draft.module_candidates == ("新增项目", "建设项目")


def test_suite_title_prefix_skips_missing_levels_without_empty_brackets():
    labels = [
        {"name": "parentSuite", "value": "建设项目"},
        {"name": "subSuite", "value": "新增项目"},
    ]

    assert _suite_title_prefix(labels, "后备模块") == "【建设项目】-【新增项目】"


def test_module_value_matches_lowest_suite_to_exact_deepest_module_leaf():
    config = {"items": [
        {"text": "/功能开发", "value": "2847"},
        {"text": "/功能开发/建设项目", "value": "3015"},
        {"text": "/功能开发/建设项目/项目立项", "value": "3017"},
        {"text": "/其他分类/建设项目", "value": "9999"},
    ]}

    assert _module_value(config, ("新增项目", "建设项目")) == "3015"


def test_module_value_uses_ancestor_to_disambiguate_same_name_leaf():
    config = {"items": [
        {"text": "/功能开发/对外投资/投前管理/项目决策", "value": "3027"},
        {"text": "/功能开发/建设项目/投前项目/项目决策", "value": "3048"},
    ]}

    assert _module_value(config, ("新增", "项目决策", "建设项目")) == "3048"


def test_action_case_adds_module_ancestors_to_candidates(tmp_path: Path) -> None:
    (tmp_path / "case-result.json").write_text(json.dumps({
        "name": "test_selected_page_action[新增]",
        "status": "failed",
        "statusDetails": {"message": "AssertionError: 保存失败"},
        "labels": [
            {"name": "parentSuite", "value": "建设项目"},
            {"name": "suite", "value": "新增"},
            {"name": "subSuite", "value": "预算及资金来源明细"},
        ],
        "parameters": [{
            "name": "action_case",
            "value": repr({
                "module_name": "建设项目/建设项目/详情/投前管理/项目决策/新增",
            }),
        }],
    }, ensure_ascii=False), encoding="utf-8")

    draft = drafts_from_allure(tmp_path)[0]

    assert draft.module_candidates == (
        "预算及资金来源明细", "新增", "建设项目", "项目决策", "投前管理", "详情",
    )


def test_title_error_detail_is_single_line_and_bounded():
    detail = _title_error_detail("AssertionError: " + "错误" * 80 + "\nassert false")

    assert len(detail) == 100
    assert detail.endswith("…")
    assert "\n" not in detail


def test_title_error_detail_keeps_non_assertion_error_message():
    assert _title_error_detail("RuntimeError: 保存接口超时\nstack trace") == (
        "RuntimeError: 保存接口超时"
    )


def test_title_error_detail_removes_case_id_and_technical_parameters():
    assert _title_error_detail(
        "AssertionError: 点击保存后没有捕获保存接口响应："
        "projName-ADD-033; validation=none"
    ) == "点击保存后没有捕获保存接口响应"
    assert _title_error_detail(
        "AssertionError: 下拉框存在重复选项：belongSection, options=['A', 'B']"
    ) == "下拉框存在重复选项"


def test_drafts_from_allure_ignores_passed_results(tmp_path: Path) -> None:
    (tmp_path / "passed-result.json").write_text(
        json.dumps({"name": "ok", "status": "passed"}), encoding="utf-8"
    )
    assert drafts_from_allure(tmp_path) == []


def test_steps_html_embeds_uploaded_screenshots_inline() -> None:
    rendered = _steps_html("第一步\n实际结果", ["/zentao/file-read-123.png"])

    assert rendered == (
        "<p>第一步</p><p>实际结果</p>"
        '<p><img src="/zentao/file-read-123.png" alt="失败用例截图"></p>'
    )


def test_allure_error_screenshot_is_added_as_named_attachment(tmp_path: Path) -> None:
    screenshot = tmp_path / "random-source-name.png"
    screenshot.write_bytes(b"png")
    multipart: dict[str, object] = {}

    _add_allure_error_attachments(multipart, [screenshot])

    assert multipart["files[]"] == {
        "name": "allure报错页面.png",
        "mimeType": "image/png",
        "buffer": b"png",
    }


def test_validation_and_api_failures_are_both_zentao_drafts(tmp_path: Path) -> None:
    for index, message in enumerate((
        "AssertionError: 请输入潜在风险分析",
        "AssertionError: 保存接口失败：HTTP 500 /api/project/save",
    )):
        (tmp_path / f"case-{index}-result.json").write_text(
            json.dumps({
                "name": f"字段用例 {index}",
                "status": "failed",
                "statusDetails": {"message": message},
                "labels": [{"name": "story", "value": "新增项目"}],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

    drafts = drafts_from_allure(tmp_path)

    assert len(drafts) == 2
    assert any("请输入潜在风险分析" in draft.steps for draft in drafts)
    assert any("HTTP 500" in draft.steps for draft in drafts)


def test_process_allure_failures_writes_drafts_when_auto_submit_is_off(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-20260804_100000"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "保存接口校验",
        "status": "failed",
        "statusDetails": {"message": "保存接口失败：HTTP 500"},
    }, ensure_ascii=False), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})

    assert result.draft_count == 1
    assert result.draft_file and result.draft_file.is_file()
    assert "ZENTAO_AUTO_SUBMIT" in result.skipped_reason


def test_process_allure_failures_submits_every_draft_when_configured(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-20260804_100001"
    results.mkdir()
    for index, message in enumerate(("字段校验失败", "保存接口 HTTP 500")):
        (results / f"case-{index}-result.json").write_text(json.dumps({
            "name": f"失败 {index}", "status": "failed",
            "statusDetails": {"message": message},
        }, ensure_ascii=False), encoding="utf-8")
    submitted = []

    def fake_submitter(**kwargs):
        submitted.append(kwargs["draft"].title)
        return str(100 + len(submitted))

    result = process_allure_failures(
        results,
        tmp_path / "zentao",
        environment={
            "ZENTAO_AUTO_SUBMIT": "true",
            "ZENTAO_URL": "http://zentao.test",
            "ZENTAO_USERNAME": "user",
            "ZENTAO_PASSWORD": "password",
        },
        submitter=fake_submitter,
    )

    assert result.draft_count == 2
    assert result.submitted_ids == ("101", "102")
    assert len(submitted) == 2


def test_expected_failure_metadata_is_filtered(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-expected"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "必填校验", "status": "failed",
        "statusDetails": {"message": "请输入项目名称", "known": True},
    }, ensure_ascii=False), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})

    assert result.draft_count == 0
    assert result.filtered_count == 1
    assert "没有可上报产品缺陷" in result.summary()


def test_same_run_duplicate_failures_are_merged_by_fingerprint(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-duplicate"
    results.mkdir()
    for index, generated in enumerate((
        "UI自动化_20260804101010_1", "UI自动化_20260804101111_2",
    )):
        (results / f"case-{index}-result.json").write_text(json.dumps({
            "name": "新增保存", "status": "failed",
            "statusDetails": {
                "message": f"保存接口失败：HTTP 500，项目={generated}"
            },
            "labels": [{"name": "story", "value": "建设项目"}],
        }, ensure_ascii=False), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})
    payload = json.loads(result.draft_file.read_text(encoding="utf-8"))

    assert result.draft_count == 1
    assert result.duplicate_count == 1
    assert payload[0]["occurrence_count"] == 2
    assert payload[0]["failure_category"] == "api"


def test_automation_locator_failure_is_not_reportable_product_bug(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-automation"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "字段定位", "status": "broken",
        "statusDetails": {"message": "Locator.wait_for: waiting for locator('#name')"},
    }), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})
    payload = json.loads(
        (tmp_path / "zentao" / "automation-environment-automation.json")
        .read_text(encoding="utf-8")
    )

    assert result.draft_count == 0
    assert result.filtered_count == 1
    assert payload[0]["failure_category"] == "automation"
    assert payload[0]["reportable"] is False


def test_in_page_save_network_failure_is_reportable_api_bug(
    tmp_path: Path,
) -> None:
    results = tmp_path / "allure-results-network"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "项目名称：保存网络中断", "status": "failed",
        "statusDetails": {
            "message": "点击保存后没有捕获保存接口响应；validation=网络连接失败"
        },
    }, ensure_ascii=False), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})
    payload = json.loads(result.draft_file.read_text(encoding="utf-8"))

    assert result.draft_count == 1
    assert payload[0]["failure_category"] == "api"
    assert payload[0]["reportable"] is True


def test_security_save_connection_reset_is_reportable_security_bug(
    tmp_path: Path,
) -> None:
    results = tmp_path / "allure-results-security-reset"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "项目名称：HTML/脚本字符", "status": "failed",
        "statusDetails": {
            "message": (
                "点击保存后没有捕获保存接口响应；validation=网络连接失败；"
                "requestfailed=POST http://system.test/fi-service/projDecision/save: "
                "net::ERR_CONNECTION_RESET"
            )
        },
    }, ensure_ascii=False), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})
    payload = json.loads(result.draft_file.read_text(encoding="utf-8"))[0]

    assert payload["failure_category"] == "security-permission"
    assert payload["reportable"] is True
    assert payload["requires_review"] is True
    assert "ERR_CONNECTION_RESET" in payload["evidence"]["api_response"]


def test_plain_connection_reset_is_non_reportable_environment_failure():
    assert _classify_failure("net::ERR_CONNECTION_RESET") == (
        "environment", False, False,
    )


def test_browser_and_test_environment_failures_remain_non_reportable():
    for message in (
        "Target page, context or browser has been closed",
        "登录已过期，请重新登录",
        "无法启动 pytest：缺少配置",
    ):
        assert _classify_failure(message) == ("environment", False, False)


def test_rapid_click_success_outcome_is_not_reported_as_api_bug():
    message = (
        "AssertionError: assert 'rapid_click_blocked_by_ui' in {...}; "
        "one business response: /projAppInfo/add"
    )

    assert _classify_failure(message) == ("automation", False, False)


def test_form_remains_open_after_save_is_reportable_operation_failure():
    assert _classify_failure("新增保存未完成：表单未关闭") == (
        "operation-result", True, False,
    )


def test_structured_evidence_is_written_from_allure_attachments(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-evidence"
    results.mkdir()
    for name, content in (
        ("url.txt", "http://example.test/project"),
        ("response.txt", '{"code":500,"message":"failed"}'),
        ("submitted.txt", '{"projName":"automation"}'),
        ("readback.txt", '{"projName":"wrong"}'),
    ):
        (results / name).write_text(content, encoding="utf-8")
    (results / "case-result.json").write_text(json.dumps({
        "name": "详情回显", "status": "failed",
        "statusDetails": {"message": "详情回显数据不一致"},
        "attachments": [
            {"name": "失败页面 URL", "source": "url.txt", "type": "text/plain"},
            {"name": "接口响应", "source": "response.txt", "type": "text/plain"},
            {"name": "提交数据", "source": "submitted.txt", "type": "text/plain"},
            {"name": "回读结果", "source": "readback.txt", "type": "text/plain"},
        ],
    }, ensure_ascii=False), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})
    payload = json.loads(result.draft_file.read_text(encoding="utf-8"))[0]

    assert payload["evidence"]["failure_url"] == "http://example.test/project"
    assert '"code":500' in payload["evidence"]["api_response"]
    assert "projName" in payload["evidence"]["submitted_data"]
    assert "wrong" in payload["evidence"]["readback_result"]


def test_unknown_failure_requires_review_and_is_not_auto_submitted(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-review"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "未知场景", "status": "failed",
        "statusDetails": {"message": "AssertionError: unexpected value"},
    }), encoding="utf-8")

    result = process_allure_failures(results, tmp_path / "zentao", environment={})
    review = json.loads(
        (tmp_path / "zentao" / "zentao-review-review.json").read_text(encoding="utf-8")
    )

    assert result.draft_count == 0
    assert review[0]["failure_category"] == "unknown"
    assert review[0]["requires_review"] is True


def test_extended_failure_categories_are_classified():
    examples = {
        "审批后状态未更新": "state-transition",
        "连续点击造成重复提交": "idempotency",
        "多人编辑发生版本冲突": "concurrency",
        "主表成功但子表关联数据失败": "transaction-consistency",
        "金额合计精度错误": "calculation",
        "筛选分页结果错误": "query",
        "日期边界极值异常": "boundary",
        "附件上传失败": "file",
        "成功提示与实际结果不一致": "interaction-feedback",
        "刷新后数据丢失": "recovery",
        "不同浏览器兼容性异常": "compatibility",
        "按钮被遮挡不可点击": "usability",
        "修改人和操作日志错误": "audit-trail",
        "第三方服务异常未降级": "external-dependency",
        "列表加载超过阈值页面卡顿": "performance-capacity",
    }

    assert {message: _classify_failure(message)[0] for message in examples} == examples


def test_existing_zentao_bug_parser_distinguishes_active_and_closed():
    active = _find_existing_bug(
        '<a href="/bug-view-123.html">【项目】保存失败</a><span>active</span>',
        "【项目】保存失败",
    )
    closed = _find_existing_bug(
        '<tr class="status-closed" data-id="456"><td>【项目】删除失败</td></tr>',
        "【项目】删除失败",
    )

    assert active and (active.bug_id, active.status) == ("123", "active")
    assert closed and (closed.bug_id, closed.status) == ("456", "closed")


def test_zentao_deleted_bug_page_is_treated_as_missing_even_with_title():
    title = "【建设项目】-【新增项目】责任板块失败"

    assert _zentao_bug_page_exists(f"<h1>{title}</h1><span>已删除</span>", title, "123") is False
    assert _zentao_bug_page_exists(f"<h1>{title}</h1><span>激活</span>", title, "124") is True


def test_save_result_prefers_bug_id_from_load_url():
    result = {"result": "success", "load": "/zentao/bug-view-53464.html"}

    assert _bug_id_from_save_result(result, "<html></html>", "任意标题") == "53464"


def test_dynamic_bug_list_href_extracts_bug_id():
    assert _bug_id_from_hrefs(("/zentao/qa.html", "/zentao/bug-view-53523.html")) == "53523"
    assert _bug_id_from_hrefs(("/zentao/qa.html",)) == ""


def test_existing_bug_is_never_submitted_or_updated_again_on_next_run(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "zentao"
    environment = {
        "ZENTAO_AUTO_SUBMIT": "true",
        "ZENTAO_URL": "http://zentao.test",
        "ZENTAO_USERNAME": "user",
        "ZENTAO_PASSWORD": "password",
    }
    submitted = []

    def fake_submitter(**kwargs):
        submitted.append(kwargs["draft"].title)
        return "321"

    for stamp in ("first", "second"):
        results = tmp_path / f"allure-results-{stamp}"
        results.mkdir()
        (results / "case-result.json").write_text(json.dumps({
            "name": "保存接口", "status": "failed",
            "statusDetails": {"message": "保存接口失败：HTTP 500"},
            "labels": [{"name": "story", "value": "新增项目"}],
        }, ensure_ascii=False), encoding="utf-8")
        latest = process_allure_failures(
            results, artifact_dir, environment=environment, submitter=fake_submitter,
            bug_checker=lambda **kwargs: True,
        )

    assert submitted == ["【新增项目】保存接口失败，保存接口失败：HTTP 500"]
    assert latest.submitted_ids == ()
    assert "仍存在" in latest.skipped_reason


def test_deleted_cached_bug_is_cleared_and_submitted_again(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "zentao"
    environment = {
        "ZENTAO_AUTO_SUBMIT": "true", "ZENTAO_URL": "http://zentao.test",
        "ZENTAO_USERNAME": "user", "ZENTAO_PASSWORD": "password",
    }
    submitted = []

    def fake_submitter(**kwargs):
        submitted.append(kwargs["draft"].title)
        return "654"

    for stamp, checker in (
        ("first", lambda **kwargs: True),
        ("second", lambda **kwargs: False),
    ):
        results = tmp_path / f"allure-results-deleted-{stamp}"
        results.mkdir()
        (results / "case-result.json").write_text(json.dumps({
            "name": "保存接口", "status": "failed",
            "statusDetails": {"message": "保存接口失败：HTTP 500"},
            "labels": [{"name": "story", "value": "新增项目"}],
        }, ensure_ascii=False), encoding="utf-8")
        latest = process_allure_failures(
            results, artifact_dir, environment=environment,
            submitter=fake_submitter, bug_checker=checker,
        )

    index = json.loads((artifact_dir / "zentao-dedup-index.json").read_text(encoding="utf-8"))
    record = next(iter(index["fingerprints"].values()))
    assert submitted == ["【新增项目】保存接口失败，保存接口失败：HTTP 500"] * 2
    assert latest.submitted_ids == ("654",)
    assert record["deleted_bug_id"] == "654"
    assert record["bug_id"] == "654"


def test_changed_fingerprint_with_same_title_reuses_existing_bug(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "zentao"
    environment = {
        "ZENTAO_AUTO_SUBMIT": "true", "ZENTAO_URL": "http://zentao.test",
        "ZENTAO_USERNAME": "user", "ZENTAO_PASSWORD": "password",
    }
    submitted = []
    messages = (
        "点击保存后没有捕获保存接口响应：projName-ADD-014; validation=网络连接失败",
        "点击保存后没有捕获保存接口响应：projName-ADD-033; validation=HTTP 500",
    )
    for stamp, message in enumerate(messages, start=1):
        results = tmp_path / f"allure-results-title-{stamp}"
        results.mkdir()
        (results / "case-result.json").write_text(json.dumps({
            "name": "项目名称：HTML/脚本字符", "status": "failed",
            "statusDetails": {"message": message},
            "labels": [{"name": "story", "value": "新增项目"}],
        }, ensure_ascii=False), encoding="utf-8")
        latest = process_allure_failures(
            results, artifact_dir, environment=environment,
            submitter=lambda **kwargs: submitted.append(kwargs["draft"].title) or "700",
            bug_checker=lambda **kwargs: True,
        )

    index = json.loads((artifact_dir / "zentao-dedup-index.json").read_text(encoding="utf-8"))
    records = list(index["fingerprints"].values())
    assert len(records) == 2
    assert submitted == [records[0]["title"]]
    assert latest.submitted_ids == ()
    assert records[1]["bug_id"] == "700"
    assert records[1]["matched_by_title"] is True


def test_empty_bug_id_is_an_upload_failure_and_not_marked_submitted(tmp_path: Path) -> None:
    results = tmp_path / "allure-results-empty-id"
    results.mkdir()
    (results / "case-result.json").write_text(json.dumps({
        "name": "保存接口", "status": "failed",
        "statusDetails": {"message": "保存接口失败：HTTP 500"},
    }, ensure_ascii=False), encoding="utf-8")
    artifact_dir = tmp_path / "zentao"

    result = process_allure_failures(
        results, artifact_dir,
        environment={
            "ZENTAO_AUTO_SUBMIT": "true", "ZENTAO_URL": "http://zentao.test",
            "ZENTAO_USERNAME": "user", "ZENTAO_PASSWORD": "password",
        },
        submitter=lambda **kwargs: "",
    )
    index = json.loads((artifact_dir / "zentao-dedup-index.json").read_text(encoding="utf-8"))

    assert "未取得 Bug ID" in result.error
    assert all(not item["bug_id"] for item in index["fingerprints"].values())
