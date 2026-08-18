from __future__ import annotations

import argparse
import ast
import hashlib
import html
import json
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

from .bug_priority import assess_bug


DETECTION_COVERAGE = {
    "evidence_driven": [
        "page-function", "field-validation", "api", "data-closure",
        "operation-result", "security-permission", "performance-capacity",
        "usability", "file", "interaction-feedback",
    ],
    "requires_explicit_scenario": [
        "state-transition", "idempotency", "concurrency", "transaction-consistency",
        "calculation", "query", "boundary", "recovery", "compatibility",
        "audit-trail", "external-dependency",
    ],
    "manual_only": [
        "颜色是否协调", "间距是否美观", "交互是否顺手", "其他主观体验",
    ],
}


@dataclass(frozen=True)
class BugEvidence:
    page_message: str = ""
    failure_url: str = ""
    api_response: str = ""
    submitted_data: str = ""
    readback_result: str = ""
    screenshots: tuple[Path, ...] = ()
    allure_result: str = ""


@dataclass(frozen=True)
class BugDraft:
    title: str
    module: str
    steps: str
    module_candidates: tuple[str, ...] = ()
    screenshots: tuple[Path, ...] = ()
    severity: int = 3
    priority: int = 3
    assessment_reason: str = ""
    requires_review: bool = False
    failure_category: str = "product"
    reportable: bool = True
    expected_failure: bool = False
    dedup_fingerprint: str = ""
    detection_source: str = "automation"
    occurrence_count: int = 1
    evidence: BugEvidence = BugEvidence()


@dataclass(frozen=True)
class ZentaoRunResult:
    draft_count: int
    draft_file: Path | None = None
    submitted_ids: tuple[str, ...] = ()
    skipped_reason: str = ""
    error: str = ""
    filtered_count: int = 0
    duplicate_count: int = 0

    def summary(self) -> str:
        if not self.draft_count:
            if self.filtered_count:
                return f"禅道：本轮没有可上报产品缺陷，已分流/过滤 {self.filtered_count} 条。"
            return "禅道：本轮没有失败项。"
        lines = [f"禅道：发现 {self.draft_count} 条待提交缺陷。"]
        if self.filtered_count:
            lines.append(f"已分流/过滤 {self.filtered_count} 条预期失败、待复核或自动化环境异常。")
        if self.duplicate_count:
            lines.append(f"已合并 {self.duplicate_count} 条相同错误。")
        if self.submitted_ids:
            lines.append(
                f"已提交 {len(self.submitted_ids)} 条，Bug ID："
                + ", ".join(item or "未返回" for item in self.submitted_ids)
            )
        if self.skipped_reason:
            lines.append("未提交：" + self.skipped_reason)
        if self.error:
            lines.append("上报失败：" + self.error)
        if self.draft_file:
            lines.append(f"草稿：{self.draft_file}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ExistingBug:
    bug_id: str
    status: str


def _value(items: Iterable[dict[str, Any]], name: str) -> str:
    for item in items:
        if item.get("name") == name:
            return str(item.get("value", "")).strip("'\"")
    return ""


def _action_module_candidates(parameters: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    raw = _value(parameters, "action_case")
    if not raw:
        return ()
    try:
        action_case = ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        return ()
    if not isinstance(action_case, dict):
        return ()
    module_name = action_case.get("module_name")
    if not isinstance(module_name, str):
        return ()
    return tuple(
        dict.fromkeys(
            segment.strip()
            for segment in reversed(module_name.split("/"))
            if segment.strip()
        )
    )


def _attachment_text(results_dir: Path, attachments: Iterable[dict[str, Any]], name: str) -> str:
    for attachment in attachments:
        if str(attachment.get("name", "")).strip().casefold() != name.casefold():
            continue
        source = results_dir / str(attachment.get("source", ""))
        if source.is_file():
            return source.read_text(encoding="utf-8-sig").strip()
    return ""


def _attachment_text_any(
    results_dir: Path, attachments: Iterable[dict[str, Any]], names: Iterable[str]
) -> str:
    for name in names:
        value = _attachment_text(results_dir, attachments, name)
        if value:
            return value[:4000]
    return ""


def _metadata_truthy(items: Iterable[dict[str, Any]], name: str) -> bool:
    return _value(items, name).strip().lower() in {"1", "true", "yes", "expected"}


def _is_expected_failure(result: dict[str, Any]) -> bool:
    details = result.get("statusDetails") or {}
    labels = result.get("labels") or []
    parameters = result.get("parameters") or []
    return bool(details.get("known") or details.get("muted")) or any(
        _metadata_truthy(items, "expected_failure")
        for items in (labels, parameters)
    ) or any(
        _value(items, "reportable").strip().lower() in {"0", "false", "no"}
        for items in (labels, parameters)
    )


def _classify_failure(
    message: str,
    *,
    test_name: str = "",
    failure_url: str = "",
    classification: str = "",
) -> tuple[str, bool, bool]:
    """Classify the final failure using the operation and scenario context."""
    normalized_classification = classification.strip().lower()
    lowered = " ".join(
        (message, test_name, failure_url, normalized_classification)
    ).lower()
    framework_contract_tokens = (
        "分支基线未完成",
        "字段身份",
        "自动化基线",
        "重渲染定位",
        "重渲染后定位",
        "重渲染后字段定位",
        "重渲染后控件定位",
    )
    source_branch_discovery_failed = (
        "源码确认" in lowered and "分支发现失败" in lowered
    )
    detail_adapter_failed = (
        any(token in lowered for token in ("详情接口", "详情回读"))
        and any(
            token in lowered
            for token in (
                "字段映射失败",
                "字段映射不完整",
                "字段无法映射",
                "适配失败",
                "适配错误",
            )
        )
    )
    if (
        normalized_classification == "automation_detail_adapter"
        or "classification=automation_detail_adapter" in lowered
        or source_branch_discovery_failed
        or detail_adapter_failed
        or any(token in lowered for token in framework_contract_tokens)
    ):
        return "automation", False, False
    system_connectivity_tokens = (
        "err_connection", "err_name_not_resolved",
        "network connection failed", "connection refused",
    )
    environment_tokens = (
        "浏览器已关闭", "browser has been closed",
        "登录已过期", "storage state", "无法启动 pytest", "缺少配置",
    )
    automation_tokens = (
        "locator.", "waiting for locator", "无法定位字段", "selector", "playwright",
        "element is not attached", "strict mode violation", "rapid_click_blocked_by_ui",
    )
    if any(token in lowered for token in environment_tokens):
        return "environment", False, False
    if any(token in lowered for token in automation_tokens):
        return "automation", False, False
    request_failed_for_mutation = (
        "requestfailed" in lowered
        and any(token in lowered for token in ("/save", "/add", "/create", "/insert", "/update", "/delete", "/submit"))
    )
    explicit_mutation_api_failure = request_failed_for_mutation or any(
        token in lowered
        for token in ("点击保存后", "保存接口", "提交接口")
    ) or (
        any(token in lowered for token in ("保存", "新增", "提交", "更新", "删除"))
        and any(
            token in lowered
            for token in (
                "http 4", "http 5", "http：4", "http：5",
                "业务码失败", "业务状态失败", "业务失败",
            )
        )
    )
    business_mutation_failed = explicit_mutation_api_failure or "新增保存" in lowered
    security_scenario = any(
        token in lowered
        for token in ("<script", "html/脚本", "脚本字符", "xss", "csrf")
    )
    if business_mutation_failed and security_scenario:
        return "security-permission", True, True
    if any(token in lowered for token in system_connectivity_tokens):
        if business_mutation_failed:
            return "api", True, True
        return "environment", False, False
    if explicit_mutation_api_failure:
        return "api", True, False
    detail_persistence_mismatch = (
        any(
            token in lowered
            for token in (
                "精确详情", "详情回读", "详情响应", "详情接口", "详情数据",
            )
        )
        and any(
            token in lowered
            for token in ("不一致", "不匹配", "未保存", "保存值错误")
        )
    )
    if detail_persistence_mismatch:
        return "data-closure", True, False
    categories = (
        ("security-permission", ("越权", "权限与页面", "隐藏按钮", "敏感数据", "脚本被执行", "xss", "csrf")),
        ("state-transition", ("状态流转", "状态未更新", "审批状态", "撤回", "驳回")),
        ("idempotency", ("重复新增", "重复提交", "重复扣减", "幂等")),
        ("concurrency", ("并发", "版本冲突", "编辑覆盖", "乐观锁", "重复编号")),
        ("transaction-consistency", ("主表", "子表", "事务", "部分成功", "关联数据失败")),
        ("calculation", ("合计", "精度", "舍入", "单位换算", "计算错误", "比例错误", "金额错误")),
        ("query", ("搜索", "筛选", "排序", "分页", "重置", "导出结果")),
        ("boundary", ("空数据", "大数据量", "特殊字符", "日期边界", "极值", "长度边界")),
        ("file", ("上传失败", "下载失败", "预览失败", "附件", "文件格式", "文件大小")),
        ("interaction-feedback", ("成功提示", "错误信息错误", "提示与实际", "无法定位字段提示")),
        ("recovery", ("刷新后", "返回后", "重新登录", "数据丢失", "脏数据")),
        ("compatibility", ("分辨率", "缩放比例", "浏览器兼容", "兼容性")),
        ("usability", ("遮挡", "溢出", "截断", "不可点击", "焦点", "滚动异常", "错位")),
        ("audit-trail", ("创建人", "修改人", "操作日志", "审计", "修改时间")),
        ("external-dependency", ("第三方", "外部依赖", "未降级", "依赖服务")),
        ("performance-capacity", ("页面卡", "请求超时", "响应超时", "性能", "超过阈值", "容量")),
        ("data-closure", (
            "回显", "列表未出现", "数据不一致", "主键缺失", "重复选项",
        )),
        ("api", (
            "网络连接失败", "业务接口失败", "接口响应业务失败",
            "接口响应失败", "接口无响应", "接口异常",
        )),
        ("operation-result", (
            "弹窗未关闭", "表单未关闭", "新增保存未完成",
            "删除未生效", "确认操作未执行", "保存无结果",
        )),
        ("field-validation", ("校验", "必填", "请输入", "validation", "最多", "唯一性", "联动")),
        ("page-function", ("按钮缺失", "按钮未显示", "页面功能", "未完成新增", "无法操作")),
    )
    for category, tokens in categories:
        if any(token in lowered for token in tokens):
            return category, True, False
    if (
        re.search(r"\bhttp\s*[45]\d{2}\b", lowered)
        or any(
            token in lowered
            for token in ("response failed", "response error", "business code failed")
        )
    ):
        return "api", True, False
    return "unknown", False, True


def _normalize_failure_text(value: str) -> str:
    normalized = value.lower()
    normalized = re.sub(r"ui自动化[_-][^\s'\";,]+", "<generated>", normalized)
    normalized = re.sub(r"el-id-\d+-\d+", "el-id-<dynamic>", normalized)
    normalized = re.sub(r"0x[0-9a-f]+", "<address>", normalized)
    normalized = re.sub(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
        "<uuid>", normalized,
    )
    normalized = re.sub(r"\b\d{14,}\b", "<id>", normalized)
    normalized = re.sub(r"\.py:\d+", ".py:<line>", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _title_error_detail(message: str, *, limit: int = 100) -> str:
    first_line = next(
        (line.strip() for line in message.splitlines() if line.strip()), ""
    )
    detail = re.sub(r"^AssertionError:\s*", "", first_line)
    detail = re.sub(r"\s+", " ", detail).strip()
    technical_suffix = re.search(r"[：:]\s*([^：:]+)$", detail)
    if technical_suffix and re.search(
        r"(?:\b[A-Za-z_][\w.-]*-[A-Z]+-\d+\b|options\s*=|validation\s*=)",
        technical_suffix.group(1),
        flags=re.IGNORECASE,
    ):
        detail = detail[:technical_suffix.start()].rstrip()
    if len(detail) > limit:
        detail = detail[: limit - 1].rstrip() + "…"
    return detail


def _suite_title_prefix(labels: list[dict[str, Any]], fallback: str) -> str:
    suites = [
        _value(labels, "parentSuite"),
        _value(labels, "suite"),
        _value(labels, "subSuite"),
    ]
    populated = []
    seen = set()
    for suite in suites:
        if suite and suite not in seen:
            populated.append(f"【{suite}】")
            seen.add(suite)
    return "-".join(populated) if populated else f"【{fallback}】"


def _failure_fingerprint(
    *, module: str, module_id: str, test_name: str, message: str, failure_url: str,
) -> str:
    system = urlsplit(failure_url).netloc.lower() if failure_url else ""
    payload = "|".join((
        system,
        module_id or module,
        test_name,
        _normalize_failure_text(message),
    ))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def drafts_from_allure(results_dir: Path) -> list[BugDraft]:
    drafts: list[BugDraft] = []
    for result_file in sorted(results_dir.glob("*-result.json")):
        result = json.loads(result_file.read_text(encoding="utf-8-sig"))
        if result.get("status") not in {"failed", "broken"}:
            continue
        if _is_expected_failure(result):
            continue
        labels = result.get("labels") or []
        parameters = result.get("parameters") or []
        module = (
            _value(labels, "feature")
            or _value(labels, "parentSuite")
            or _value(labels, "suite")
            or _value(labels, "story")
            or _value(labels, "subSuite")
        )
        module_id = _value(parameters, "module_id")
        action_module_candidates = _action_module_candidates(parameters)
        test_name = str(result.get("name") or "UI 冒烟测试")
        message = str((result.get("statusDetails") or {}).get("message") or "测试执行失败")
        attachments = result.get("attachments") or []
        failure_url = _attachment_text(results_dir, attachments, "失败页面 URL")
        display = module or module_id or "未分类模块"
        error_detail = _title_error_detail(message)
        title = f"{_suite_title_prefix(labels, display)}{test_name}失败"
        if error_detail:
            title += f"，{error_detail}"
        category, reportable, category_requires_review = _classify_failure(
            message,
            test_name=test_name,
            failure_url=failure_url,
            classification=(
                _value(labels, "classification")
                or _value(parameters, "classification")
            ),
        )
        fingerprint = _failure_fingerprint(
            module=display,
            module_id=module_id,
            test_name=test_name,
            message=message,
            failure_url=failure_url,
        )
        assessment = assess_bug(
            title=title,
            message=message,
            module=display,
            failure_url=failure_url,
        )
        url_step = f"4. 失败页面 URL：{failure_url}\n" if failure_url else ""
        steps = (
            "【重现步骤】\n"
            f"1. 进入“{display}”模块。\n"
            "2. 按 UI 冒烟用例执行页面访问、新增保存及详情核对。\n"
            "3. 观察实际执行结果。\n"
            f"{url_step}\n"
            "【实际结果】\n"
            f"{message}\n\n"
            "【预期结果】\n"
            "模块可正常完成新增保存，且详情接口回显与提交数据一致。"
        )
        screenshots = tuple(
            results_dir / attachment["source"]
            for attachment in attachments
            if str(attachment.get("type", "")).startswith("image/")
            and (results_dir / str(attachment.get("source", ""))).is_file()
        )
        evidence = BugEvidence(
            page_message=_attachment_text_any(
                results_dir, attachments, ("页面提示", "校验信息", "失败信息")
            ) or message[:4000],
            failure_url=failure_url,
            api_response=_attachment_text_any(
                results_dir, attachments, ("接口响应", "保存接口响应", "业务响应")
            ) or (message[:4000] if category in {"api", "security-permission"} else ""),
            submitted_data=_attachment_text_any(
                results_dir, attachments, ("提交数据", "保存请求摘要")
            ),
            readback_result=_attachment_text_any(
                results_dir, attachments, ("回读结果", "详情回显", "列表回显")
            ) or (message[:4000] if category == "data-closure" else ""),
            screenshots=screenshots,
            allure_result=result_file.name,
        )
        drafts.append(BugDraft(
            title=title,
            module=module or module_id,
            steps=steps,
            module_candidates=tuple(dict.fromkeys(filter(None, (
                _value(labels, "subSuite"),
                _value(labels, "suite"),
                _value(labels, "parentSuite"),
                _value(labels, "feature"),
                *action_module_candidates,
                module or module_id,
            )))),
            screenshots=screenshots,
            severity=assessment.severity,
            priority=assessment.priority,
            assessment_reason=assessment.reason,
            requires_review=assessment.requires_review or category_requires_review,
            failure_category=category,
            reportable=reportable,
            dedup_fingerprint=fingerprint,
            detection_source=(
                _value(labels, "detection_source")
                or _value(parameters, "detection_source")
                or "automation"
            ),
            evidence=evidence,
        ))
    unique: dict[str, BugDraft] = {}
    for draft in drafts:
        existing = unique.get(draft.dedup_fingerprint)
        if existing is None:
            unique[draft.dedup_fingerprint] = draft
            continue
        unique[draft.dedup_fingerprint] = replace(
            existing,
            occurrence_count=existing.occurrence_count + 1,
            screenshots=tuple(dict.fromkeys((*existing.screenshots, *draft.screenshots))),
            evidence=replace(
                existing.evidence,
                screenshots=tuple(dict.fromkeys((
                    *existing.evidence.screenshots, *draft.evidence.screenshots
                ))),
            ),
        )
    return list(unique.values())


def _draft_payload(draft: BugDraft) -> dict[str, Any]:
    return {
        "title": draft.title,
        "module": draft.module,
        "module_candidates": list(draft.module_candidates),
        "severity": draft.severity,
        "priority": draft.priority,
        "assessment_reason": draft.assessment_reason,
        "requires_review": draft.requires_review,
        "failure_category": draft.failure_category,
        "reportable": draft.reportable,
        "expected_failure": draft.expected_failure,
        "dedup_fingerprint": draft.dedup_fingerprint,
        "detection_source": draft.detection_source,
        "occurrence_count": draft.occurrence_count,
        "evidence": {
            "page_message": draft.evidence.page_message,
            "failure_url": draft.evidence.failure_url,
            "api_response": draft.evidence.api_response,
            "submitted_data": draft.evidence.submitted_data,
            "readback_result": draft.evidence.readback_result,
            "screenshots": [str(path) for path in draft.evidence.screenshots],
            "allure_result": draft.evidence.allure_result,
        },
        "steps": draft.steps,
        "screenshots": [str(path) for path in draft.screenshots],
    }


def process_allure_failures(
    results_dir: Path,
    artifact_dir: Path,
    *,
    environment: Mapping[str, str] | None = None,
    submitter: Callable[..., str] | None = None,
    bug_checker: Callable[..., bool] | None = None,
) -> ZentaoRunResult:
    """Create auditable, classified drafts and optionally submit unique defects."""
    result_files = list(results_dir.glob("*-result.json"))
    raw_failures = []
    expected_count = 0
    for result_file in result_files:
        result = json.loads(result_file.read_text(encoding="utf-8-sig"))
        if result.get("status") not in {"failed", "broken"}:
            continue
        raw_failures.append(result)
        expected_count += int(_is_expected_failure(result))
    drafts = drafts_from_allure(results_dir)
    reportable_drafts = [draft for draft in drafts if draft.reportable]
    non_reportable_count = len(drafts) - len(reportable_drafts)
    duplicate_count = max(0, len(raw_failures) - expected_count - len(drafts))
    if not raw_failures:
        return ZentaoRunResult(0)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    stamp = results_dir.name.removeprefix("allure-results-")
    index_file = artifact_dir / "zentao-dedup-index.json"
    try:
        index = json.loads(index_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        index = {"fingerprints": {}}
    fingerprints = index.setdefault("fingerprints", {})
    for draft in drafts:
        record = fingerprints.setdefault(draft.dedup_fingerprint, {
            "title": draft.title,
            "first_seen": stamp,
            "occurrence_count": 0,
            "bug_id": "",
        })
        if record["occurrence_count"]:
            duplicate_count += 1
        record["last_seen"] = stamp
        record["occurrence_count"] += draft.occurrence_count
        record["failure_category"] = draft.failure_category
        record["reportable"] = draft.reportable
    index_file.write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    draft_file = artifact_dir / f"zentao-drafts-{stamp}.json"
    draft_file.write_text(
        json.dumps(
            [_draft_payload(draft) for draft in reportable_drafts],
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    diagnostics = [
        draft for draft in drafts
        if draft.failure_category in {"automation", "environment"}
    ]
    review_drafts = [
        draft for draft in drafts if draft.failure_category == "unknown"
    ]
    (artifact_dir / f"automation-environment-{stamp}.json").write_text(
        json.dumps([_draft_payload(draft) for draft in diagnostics], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_dir / f"zentao-review-{stamp}.json").write_text(
        json.dumps([_draft_payload(draft) for draft in review_drafts], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    expected_items = [
        {
            "name": result.get("name", ""),
            "status": result.get("status", ""),
            "reason": "expected_failure",
        }
        for result in raw_failures if _is_expected_failure(result)
    ]
    (artifact_dir / f"zentao-filtered-{stamp}.json").write_text(
        json.dumps(expected_items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (artifact_dir / "detection-coverage.json").write_text(
        json.dumps(DETECTION_COVERAGE, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    env = dict(os.environ if environment is None else environment)
    filtered_count = expected_count + non_reportable_count
    if not reportable_drafts:
        return ZentaoRunResult(
            0, draft_file,
            skipped_reason="本轮失败均为预期失败、自动化或环境异常",
            filtered_count=filtered_count,
            duplicate_count=duplicate_count,
        )
    if env.get("ZENTAO_AUTO_SUBMIT", "false").strip().lower() != "true":
        return ZentaoRunResult(
            len(reportable_drafts), draft_file,
            skipped_reason="未开启 ZENTAO_AUTO_SUBMIT=true",
            filtered_count=filtered_count,
            duplicate_count=duplicate_count,
        )
    required = ("ZENTAO_URL", "ZENTAO_USERNAME", "ZENTAO_PASSWORD")
    missing = [name for name in required if not env.get(name)]
    if missing:
        return ZentaoRunResult(
            len(reportable_drafts), draft_file,
            error="缺少配置：" + ", ".join(missing),
            filtered_count=filtered_count,
            duplicate_count=duplicate_count,
        )
    check_bug = bug_checker or zentao_bug_exists
    try:
        for draft in reportable_drafts:
            record = fingerprints[draft.dedup_fingerprint]
            same_title_records = sorted(
                (
                    candidate for candidate in fingerprints.values()
                    if candidate is not record
                    and candidate.get("title") == draft.title
                    and candidate.get("bug_id")
                ),
                key=lambda candidate: str(
                    candidate.get("submitted_at") or candidate.get("last_seen") or ""
                ),
                reverse=True,
            )
            candidates = ([record] if record.get("bug_id") else []) + same_title_records
            for candidate in candidates:
                bug_id = str(candidate.get("bug_id") or "")
                exists = check_bug(
                    base_url=env["ZENTAO_URL"], username=env["ZENTAO_USERNAME"],
                    password=env["ZENTAO_PASSWORD"], bug_id=bug_id,
                    title=draft.title,
                    headless=env.get("ZENTAO_HEADLESS", "true").lower() != "false",
                )
                if exists:
                    record["bug_id"] = bug_id
                    record["matched_by_title"] = candidate is not record
                    break
                candidate["deleted_bug_id"] = bug_id
                candidate["bug_id"] = ""
                candidate.pop("submitted_at", None)
        index_file.write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:
        return ZentaoRunResult(
            len(reportable_drafts), draft_file,
            error=f"无法确认禅道原 Bug 是否存在，为防止重复提交已停止：{exc}",
            filtered_count=filtered_count, duplicate_count=duplicate_count,
        )
    eligible = [
        draft for draft in reportable_drafts
        if not fingerprints[draft.dedup_fingerprint].get("bug_id")
    ]
    already_submitted = len(reportable_drafts) - len(eligible)
    duplicate_count += already_submitted
    if not eligible:
        # Existing Bugs are ownership boundaries: never edit their fields here,
        # especially after they have been reassigned to another handler.
        return ZentaoRunResult(
            len(reportable_drafts), draft_file,
            skipped_reason="相同错误对应的禅道 Bug 仍存在，本轮仅累计复现次数",
            filtered_count=filtered_count, duplicate_count=duplicate_count,
        )
    submit = submitter or submit_bug
    submitted_ids = []
    try:
        for draft in eligible:
            bug_id = submit(
                base_url=env["ZENTAO_URL"],
                username=env["ZENTAO_USERNAME"],
                password=env["ZENTAO_PASSWORD"],
                product=env.get("ZENTAO_PRODUCT", "盛和产品"),
                assignee=env.get("ZENTAO_ASSIGNEE", "宋佳慧"),
                draft=draft,
                headless=env.get("ZENTAO_HEADLESS", "true").lower() != "false",
            )
            if not bug_id:
                raise RuntimeError(f"禅道已返回保存成功但未取得 Bug ID：{draft.title}")
            submitted_ids.append(bug_id)
            fingerprints[draft.dedup_fingerprint]["bug_id"] = bug_id
            fingerprints[draft.dedup_fingerprint]["submitted_at"] = stamp
            index_file.write_text(
                json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except Exception as exc:
        return ZentaoRunResult(
            len(reportable_drafts), draft_file, tuple(submitted_ids), error=str(exc),
            filtered_count=filtered_count, duplicate_count=duplicate_count,
        )
    receipt = artifact_dir / f"zentao-submitted-{stamp}.json"
    receipt.write_text(
        json.dumps({
            "results": str(results_dir),
            "bugs": [
                {"title": draft.title, "bug_id": bug_id,
                 "dedup_fingerprint": draft.dedup_fingerprint}
                for draft, bug_id in zip(eligible, submitted_ids)
            ],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ZentaoRunResult(
        len(reportable_drafts), draft_file, tuple(submitted_ids),
        filtered_count=filtered_count, duplicate_count=duplicate_count,
    )


def _picker_config(form_html: str, name: str) -> dict[str, Any]:
    for encoded in re.findall(r'zui-create-picker="([^"]+)"', form_html):
        config = json.loads(html.unescape(encoded))
        if config.get("name") == name:
            return config
    raise RuntimeError(f"禅道表单缺少字段：{name}")


def _item_value(config: dict[str, Any], text: str, *, fallback: str = "") -> str:
    items = config.get("items") or []
    for item in items:
        if str(item.get("value")) == text or text in str(item.get("text", "")):
            return str(item["value"])
    return fallback


def _module_value(config: dict[str, Any], candidates: Iterable[str]) -> str:
    items = config.get("items") or []
    ordered_candidates = tuple(dict.fromkeys(filter(None, candidates)))
    for candidate in ordered_candidates:
        matches = []
        for picker_order, item in enumerate(items):
            path = str(item.get("text", "")).strip("/")
            segments = tuple(filter(None, path.split("/")))
            leaf = segments[-1] if segments else ""
            if leaf == candidate:
                ancestors = set(segments[:-1])
                ancestor_matches = sum(
                    other != candidate and other in ancestors
                    for other in ordered_candidates
                )
                matches.append((ancestor_matches, len(segments), -picker_order, str(item["value"])))
        if matches:
            return max(matches)[-1]
    return ""


def _find_product_id(payload: dict[str, Any], product: str) -> str:
    data = payload.get("data") or {}
    for group_name in ("my", "other", "closed"):
        for group in data.get(group_name) or []:
            for item in group.get("items") or []:
                if item.get("text") == product:
                    return str(item["id"])
    raise RuntimeError(f"禅道中未找到产品：{product}")


def _find_existing_bug(browse_html: str, title: str) -> ExistingBug | None:
    positions = [
        browse_html.find(title), browse_html.find(html.escape(title))
    ]
    position = next((item for item in positions if item >= 0), -1)
    if position < 0:
        return None
    nearby = browse_html[max(0, position - 2500):position + 1000]
    id_match = re.search(
        r"bug-view-(\d+)|data-id=[\"'](\d+)[\"']|&quot;id&quot;:(\d+)", nearby
    )
    if not id_match:
        return None
    bug_id = next(group for group in id_match.groups() if group)
    lowered = nearby.lower()
    status = "closed" if any(token in lowered for token in (
        "status-closed", "status-resolved", ">closed<", ">resolved<", "已关闭", "已解决",
    )) else "active"
    return ExistingBug(bug_id, status)


def _bug_id_from_save_result(result: dict[str, Any], response_html: str, title: str) -> str:
    load = str(result.get("load", ""))
    load_match = re.search(r"bug-view-(\d+)", load)
    if load_match:
        return load_match.group(1)
    existing = _find_existing_bug(response_html, title)
    return existing.bug_id if existing else ""


def _bug_id_from_hrefs(hrefs: Iterable[str]) -> str:
    for href in hrefs:
        match = re.search(r"bug-view-(\d+)", str(href))
        if match:
            return match.group(1)
    return ""


def _bug_id_from_dynamic_list(page, browse_url: str, title: str) -> str:
    page.goto(browse_url, wait_until="domcontentloaded")
    for _ in range(10):
        hrefs = []
        for frame in page.frames:
            links = frame.get_by_role("link", name=title, exact=True)
            hrefs.extend(
                links.nth(index).get_attribute("href") or ""
                for index in range(links.count())
            )
        bug_id = _bug_id_from_hrefs(hrefs)
        if bug_id:
            return bug_id
        page.wait_for_timeout(300)
    return ""


def _steps_html(steps: str, image_urls: Iterable[str] = ()) -> str:
    paragraphs = "<p>" + html.escape(steps).replace("\n", "</p><p>") + "</p>"
    images = "".join(
        f'<p><img src="{html.escape(url, quote=True)}" alt="失败用例截图"></p>'
        for url in image_urls
    )
    return paragraphs + images


def _steps_with_evidence(draft: BugDraft) -> str:
    evidence = draft.evidence
    items = []
    if evidence.api_response:
        items.append("接口响应：" + evidence.api_response)
    if evidence.submitted_data:
        items.append("提交数据：" + evidence.submitted_data)
    if evidence.readback_result:
        items.append("回读结果：" + evidence.readback_result)
    items.extend((
        "失败分类：" + draft.failure_category,
        "自动化指纹：" + draft.dedup_fingerprint,
        f"累计复现次数：{draft.occurrence_count}",
    ))
    return draft.steps + "\n\n【结构化证据】\n" + "\n".join(items)


def _upload_inline_images(page, root: str, create_url: str, uid: str, screenshots: Iterable[Path]) -> list[str]:
    urls: list[str] = []
    for screenshot in screenshots:
        response = page.request.post(
            f"{root}/file-ajaxUpload-{uid}.html",
            multipart={
                "imgFile": {
                    "name": screenshot.name,
                    "mimeType": "image/png",
                    "buffer": screenshot.read_bytes(),
                },
                "labels[]": screenshot.name,
            },
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": create_url},
        )
        result = response.json()
        if result.get("error") != 0 or not result.get("url"):
            raise RuntimeError(f"禅道正文图片上传失败：{result}")
        urls.append(str(result["url"]))
    return urls


def _add_allure_error_attachments(multipart: dict[str, Any], screenshots: Iterable[Path]) -> None:
    for index, screenshot in enumerate(screenshots):
        suffix = screenshot.suffix.lower() or ".png"
        label = "allure报错页面" if index == 0 else f"allure报错页面-{index + 1}"
        key = "files[]" if index == 0 else f"files[{index}]"
        multipart[key] = {
            "name": f"{label}{suffix}",
            "mimeType": "image/png",
            "buffer": screenshot.read_bytes(),
        }


def zentao_bug_exists(
    *, base_url: str, username: str, password: str, bug_id: str,
    title: str, headless: bool = True,
) -> bool:
    """Return False only when ZenTao clearly confirms the cached Bug is gone."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        if page.locator('input[name="account"], input#account').count():
            page.locator('input[name="account"]').fill(username)
            page.locator('input[name="password"]').fill(password)
            page.locator('#submit').click()
            page.wait_for_load_state("networkidle")
        if page.locator('input[name="account"], input#account').count():
            raise RuntimeError("禅道登录失败")

        parsed_url = urlsplit(base_url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        web_root = parsed_url.path.split("/user-", 1)[0].rstrip("/")
        response = page.request.get(f"{origin}{web_root}/bug-view-{bug_id}.html")
        body = response.text()
        context.close()
        browser.close()
    if response.status == 404:
        return False
    if not response.ok:
        raise RuntimeError(f"查询 Bug #{bug_id} 失败：HTTP {response.status}")
    return _zentao_bug_page_exists(body, title, bug_id)


def _zentao_bug_page_exists(body: str, title: str, bug_id: str = "") -> bool:
    lowered = body.lower()
    deleted_tokens = ("已删除", "status-deleted", ">deleted<")
    if any(token in lowered for token in deleted_tokens):
        return False
    if title in body or html.escape(title) in body:
        return True
    missing_tokens = ("bug不存在", "bug 不存在", "缺陷不存在", "not found", "does not exist")
    if any(token in lowered for token in missing_tokens):
        return False
    raise RuntimeError(f"Bug #{bug_id} 详情页未返回标题，也未明确提示不存在")


def submit_bug(
    *,
    base_url: str,
    username: str,
    password: str,
    product: str,
    assignee: str,
    draft: BugDraft,
    headless: bool = True,
) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()
        page.goto(base_url, wait_until="domcontentloaded")
        if page.locator('input[name="account"], input#account').count():
            page.locator('input[name="account"]').fill(username)
            page.locator('input[name="password"]').fill(password)
            page.locator('#submit').click()
            page.wait_for_load_state("networkidle")

        parsed_url = urlsplit(base_url)
        origin = f"{parsed_url.scheme}://{parsed_url.netloc}"
        web_root = parsed_url.path.split("/user-", 1)[0].rstrip("/")
        root = origin + web_root
        product_payload = page.request.get(
            f"{root}/product-ajaxGetDropMenu-0-qa-index.html"
        ).json()
        product_id = _find_product_id(product_payload, product)
        browse_url = f"{root}/bug-browse-{product_id}-0-all-0.html"
        browse_response = page.request.get(browse_url)
        if browse_response.ok:
            existing = _find_existing_bug(browse_response.text(), draft.title)
            if existing and existing.status == "active":
                context.close()
                browser.close()
                return existing.bug_id
            if existing:
                raise RuntimeError(
                    f"检测到已关闭缺陷 #{existing.bug_id} 回归复现，需人工重新激活：{draft.title}"
                )
        create_url = f"{root}/bug-create-{product_id}-0-moduleID=0.html"
        form_html = page.request.get(create_url).text()
        uid_match = re.search(r'name="uid" value="([^"]+)"', form_html)
        if not uid_match:
            raise RuntimeError("禅道提 Bug 表单缺少 UID")
        project_config = _picker_config(form_html, "project")
        projects = project_config.get("items") or []
        if len(projects) != 1:
            raise RuntimeError(f"所属项目不是唯一选项：{len(projects)}")
        module_config = _picker_config(form_html, "module")
        module_value = _module_value(
            module_config, draft.module_candidates or (draft.module,)
        )
        if not module_value:
            module_value = _item_value(
                module_config, os.getenv("ZENTAO_MODULE_FALLBACK", "功能开发"), fallback="0"
            )
        assigned_value = _item_value(_picker_config(form_html, "assignedTo"), assignee)
        if not assigned_value:
            raise RuntimeError(f"指派人不存在：{assignee}")
        uid = uid_match.group(1)
        image_urls = _upload_inline_images(page, root, create_url, uid, draft.screenshots)

        multipart: dict[str, Any] = {
            "product": product_id,
            "project": str(projects[0]["value"]),
            "module": module_value,
            "openedBuild[]": "trunk",
            "assignedTo": assigned_value,
            "title": draft.title,
            "type": "codeerror",
            "severity": str(draft.severity),
            "pri": str(draft.priority),
            "steps": _steps_html(_steps_with_evidence(draft), image_urls),
            "uid": uid,
            "fromCase": "0",
            "caseVersion": "0",
            "result": "0",
            "testtask": "0",
            "fileList": "[]",
            "execution": "0",
            "plan": "0",
        }
        _add_allure_error_attachments(multipart, draft.screenshots)
        response = page.request.post(
            create_url,
            multipart=multipart,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": create_url},
        )
        result = response.json()
        if result.get("result") != "success":
            raise RuntimeError(f"禅道保存失败：{result}")
        response_html = page.request.get(origin + result["load"]).text()
        bug_id = _bug_id_from_save_result(result, response_html, draft.title)
        if not bug_id:
            bug_id = _bug_id_from_dynamic_list(page, browse_url, draft.title)
        context.close()
        browser.close()
        return bug_id


def main() -> int:
    parser = argparse.ArgumentParser(description="将 Allure 失败项录入禅道")
    parser.add_argument("results", type=Path)
    parser.add_argument("--screenshot", action="append", type=Path, default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    drafts = drafts_from_allure(args.results)
    if args.screenshot:
        drafts = [replace(d, screenshots=tuple(args.screenshot)) for d in drafts]
    if args.dry_run:
        print(json.dumps([_draft_payload(d) for d in drafts], ensure_ascii=False, indent=2))
        return 0
    required = ("ZENTAO_URL", "ZENTAO_USERNAME", "ZENTAO_PASSWORD")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        parser.error(f"缺少环境变量：{', '.join(missing)}")
    for draft in drafts:
        bug_id = submit_bug(
            base_url=os.environ["ZENTAO_URL"],
            username=os.environ["ZENTAO_USERNAME"],
            password=os.environ["ZENTAO_PASSWORD"],
            product=os.getenv("ZENTAO_PRODUCT", "盛和产品"),
            assignee=os.getenv("ZENTAO_ASSIGNEE", "宋佳慧"),
            draft=draft,
            headless=os.getenv("ZENTAO_HEADLESS", "true").lower() != "false",
        )
        if not bug_id:
            raise RuntimeError(f"禅道已返回保存成功但未取得 Bug ID：{draft.title}")
        print(f"已提交：{draft.title} {bug_id}".rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
