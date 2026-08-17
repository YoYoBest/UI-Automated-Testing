import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ei_ui_smoke.urls import detail_parent_url


_PARENT_RECORD_SELECTOR = (
    ".el-table__body-wrapper .el-table__row:visible,"
    ".el-table__row:visible,.project-card:visible,.fund-card:visible,"
    ".platform-card:visible,.mujijin-cardBox:visible,.list-item:visible"
)
_PARENT_LOADING_SELECTOR = (
    ".el-loading-mask:visible,.el-skeleton.is-loading:visible,"
    "[aria-busy='true']:visible"
)
_PARENT_PAGINATION_SELECTOR = ".table_pagination:visible,.el-pagination:visible"
_PARENT_PAGE_JUMP_SELECTOR = (
    "input[aria-label='页'],.el-pagination__editor input[type='number']"
)
_PROJECT_LIST_MORE_SELECTOR = ".search-more:visible"
_PROJECT_STATUS_FILTER_SELECTOR = (
    ".search-more-list .el-form-item:has-text('项目状态'):visible"
)
_PROJECT_LIST_QUERY_SELECTOR = ".search-button:visible"
_PROJECT_LIST_EMPTY_SELECTOR = ".fund-card-list:has-text('暂无建设项目数据'):visible"
_PROJECT_LIST_RECORD_SELECTOR = ".fund-card-list .mujijin-cardBox:visible"
_PROJECT_STATUS_CELL_SELECTOR = ".card-info-row .card-col"
# The status filter accepts one value per request. The first rendered result
# from the first non-empty status query is the fixed project-progress parent.
_PROJECT_PROGRESS_STATUS_CODES = {
    "项目决策": "20",
    "项目实施": "30",
    "项目竣工": "40",
    "已终止": "100",
}
_PROJECT_PROGRESS_ELIGIBLE_STATUSES = tuple(_PROJECT_PROGRESS_STATUS_CODES)
_PROJECT_PROGRESS_STATUS_LABEL = "项目决策及后续可新增状态"
_RECORD_DETAIL_ACTION_TEXTS = {
    "详情", "查看", "编辑", "修改", "删除", "移除", "清空", "新增", "添加", "新建",
    "保存", "确定", "取消", "关闭", "提交", "导出", "下载", "打印", "刷新",
}
_CHILD_RECORD_ACTION_PREFIXES = (
    "编辑", "修改", "查看", "立项准备", "入库申请", "跟进",
)


class ParentListNotReadyError(AssertionError):
    """The parent list cannot yet provide a stable record entry."""


class ParentListEmptyError(ParentListNotReadyError):
    """The parent list is ready but contains no usable business records."""


class ParentRecordIdentityUnavailableError(AssertionError):
    """The cached parent business identity is no longer present in the list."""


class DetailActionUnavailableError(AssertionError):
    """The selected detail module is rendered but has no usable requested action."""


@dataclass(frozen=True)
class ProjectProgressParentContext:
    """One verified project-detail route shared by a project-progress batch."""

    business_id: str
    detail_url: str


@dataclass(frozen=True)
class _ProjectStatusCandidates:
    candidates: object
    expected_business_id: str


def _is_project_progress_module(module_name: str) -> bool:
    normalized_module = _normalized_action_text(module_name)
    return any(label in normalized_module for label in ("项目进度", "实施进度"))


def _project_progress_context_path() -> Path:
    configured = os.getenv("EI_PROJECT_PROGRESS_PARENT_CONTEXT_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "artifacts" / "project-progress-parent-context.json"


def _project_progress_context_key(detail_url: str, module_name: str) -> str:
    run_id = os.getenv("EI_AUTOMATION_RUN_ID", "").strip()
    if not run_id or not _is_project_progress_module(module_name):
        return ""
    labels = detail_navigation_labels(module_name, "")
    progress_label = next(
        (label for label in labels if _is_project_progress_module(label)),
        "项目进度",
    )
    return "|".join((run_id, detail_parent_url(detail_url), progress_label))


def _load_project_progress_parent_context(
    detail_url: str, module_name: str,
) -> ProjectProgressParentContext | None:
    key = _project_progress_context_key(detail_url, module_name)
    path = _project_progress_context_path()
    if not key or not path.is_file():
        return None
    try:
        item = json.loads(path.read_text(encoding="utf-8")).get("contexts", {}).get(key, {})
        business_id = _normalized_action_text(str(item.get("business_id", "")))
        saved_url = str(item.get("detail_url", "")).strip()
    except (OSError, ValueError, AttributeError):
        return None
    if not business_id or not saved_url:
        return None
    return ProjectProgressParentContext(business_id, saved_url)


def _store_project_progress_parent_context(
    detail_url: str,
    module_name: str,
    context: ProjectProgressParentContext,
) -> None:
    key = _project_progress_context_key(detail_url, module_name)
    if not key or not context.business_id or not context.detail_url:
        return
    path = _project_progress_context_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, ValueError):
        payload = {}
    contexts = payload.get("contexts") if isinstance(payload, dict) else None
    if not isinstance(contexts, dict):
        contexts = {}
    contexts[key] = {
        "business_id": context.business_id,
        "detail_url": context.detail_url,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps({"contexts": contexts}, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)
    except OSError as exc:
        raise ParentListNotReadyError(
            "无法保存项目进度批次父项目上下文"
        ) from exc


def _project_progress_context_from_page(page, selected_identity) -> object | None:
    match = re.search(r"[?&](?:id|projId)=([^&#]+)", str(getattr(page, "url", "")), re.I)
    if not match:
        return selected_identity
    return ProjectProgressParentContext(match.group(1), str(page.url))


def _open_project_progress_parent_context(
    page,
    context: ProjectProgressParentContext,
    module_name: str,
    action: str,
    *,
    navigation_labels: list[str],
    provision_child_record: Callable[[], object] | None = None,
) -> ProjectProgressParentContext:
    page.goto(context.detail_url, wait_until="domcontentloaded")
    current_url = str(getattr(page, "url", ""))
    current_id = re.search(r"[?&](?:id|projId)=([^&#]+)", current_url, re.I)
    if (
        current_url.rstrip("/").endswith("#/404")
        or current_id is None
        or current_id.group(1) != context.business_id
    ):
        raise ParentRecordIdentityUnavailableError(
            "项目进度已固定父项目无法恢复到原详情页："
            f"expected_business_id={context.business_id}, url={current_url}"
        )
    try:
        navigate_detail_module(
            page, module_name, action, navigation_labels=navigation_labels,
        )
    except DetailActionUnavailableError:
        if not provision_child_record or not _detail_action_needs_child_record(action):
            raise
        seed_module_name = _module_name_for_seed_action(module_name, action)
        navigate_detail_module(
            page,
            seed_module_name,
            "新增",
            navigation_labels=detail_navigation_labels(seed_module_name, "新增"),
        )
        child = provision_child_record()
        if not _normalized_record_identity_values(child):
            raise AssertionError(
                "项目进度子记录前置创建未返回业务 ID 或自动化标识"
            )
        page.goto(context.detail_url, wait_until="domcontentloaded")
        recovered_id = re.search(
            r"[?&](?:id|projId)=([^&#]+)", str(getattr(page, "url", "")), re.I,
        )
        if recovered_id is None or recovered_id.group(1) != context.business_id:
            raise ParentRecordIdentityUnavailableError(
                "项目进度子记录前置创建后未回到固定父项目："
                f"expected_business_id={context.business_id}, url={page.url}"
            )
        navigate_detail_module(
            page, module_name, action, navigation_labels=navigation_labels,
        )
    return context


def _parent_list_loading(page) -> bool:
    try:
        return page.locator(_PARENT_LOADING_SELECTOR).count() > 0
    except Exception:
        return False


def _record_snapshot(candidates) -> tuple[str, ...]:
    try:
        values = candidates.evaluate_all(
            r"""elements => elements.map((element, index) => {
                const identity = [
                    'data-key', 'data-row-key', 'data-id', 'data-record-id', 'row-key'
                ].map((name) => element.getAttribute(name) || '').find(Boolean) || '';
                const text = (element.innerText || element.textContent || '')
                    .replace(/\s+/g, ' ').trim();
                return `${index}|${identity}|${text}`;
            })"""
        )
    except Exception:
        try:
            values = candidates.all_inner_texts()
        except Exception:
            values = []
    return tuple(
        re.sub(r"\s+", " ", str(value or "")).strip()
        for value in values
        if str(value or "").strip()
    )


def _wait_for_parent_records_ready(
    page,
    *,
    timeout: int = 30_000,
    poll_ms: int = 200,
    stable_polls: int = 3,
    different_from: tuple[str, ...] | None = None,
):
    """Wait until loading is gone and the visible record set stops changing."""
    deadline = time.monotonic() + max(1, timeout) / 1000
    previous: tuple[str, ...] = ()
    stable_count = 0
    loading = False
    visible_count = 0
    while time.monotonic() < deadline:
        loading = _parent_list_loading(page)
        candidates = page.locator(_PARENT_RECORD_SELECTOR)
        snapshot = _record_snapshot(candidates)
        visible_count = len(snapshot)
        if not loading and snapshot and snapshot != different_from:
            if snapshot == previous:
                stable_count += 1
            else:
                previous = snapshot
                stable_count = 1
            if stable_count >= max(1, stable_polls):
                return candidates
        else:
            previous = ()
            stable_count = 0
        page.wait_for_timeout(max(1, poll_ms))
    error_type = ParentListEmptyError if not loading and visible_count == 0 else ParentListNotReadyError
    raise error_type(
        "详情父列表未就绪："
        f"loading={loading}, visible_records={visible_count}, url={page.url}"
    )


def visible_action(
    page,
    action: str,
    timeout: int = 15_000,
    disabled_grace_ms: int = 1_500,
):
    exact = re.compile(rf"^\s*{re.escape(action)}\s*$")
    selector = "button:visible,a:visible,[role='button']:visible"
    if hasattr(page, "wait_for_timeout"):
        deadline = time.monotonic() + timeout / 1000
        disabled_since: float | None = None
        while time.monotonic() < deadline:
            candidates = page.locator(selector)
            matching_disabled = False
            try:
                for index in range(candidates.count()):
                    candidate = candidates.nth(index)
                    text = re.sub(r"\s+", "", candidate.inner_text())
                    enabled = (
                        candidate.is_enabled()
                        if hasattr(candidate, "is_enabled")
                        else True
                    )
                    if (
                        text == re.sub(r"\s+", "", action)
                        and candidate.is_visible()
                    ):
                        if enabled:
                            return candidate
                        matching_disabled = True
            except Exception:
                pass
            if matching_disabled:
                if disabled_since is None:
                    disabled_since = time.monotonic()
                elif (time.monotonic() - disabled_since) * 1000 >= max(
                    0, disabled_grace_ms
                ):
                    return None
            else:
                disabled_since = None
            page.wait_for_timeout(200)
        return None

    candidates = page.locator(selector).filter(has_text=exact)
    try:
        candidates.first.wait_for(state="visible", timeout=timeout)
    except Exception:
        return None
    for index in range(candidates.count()):
        candidate = candidates.nth(index)
        enabled = candidate.is_enabled() if hasattr(candidate, "is_enabled") else True
        if candidate.is_visible() and enabled:
            return candidate
    return None


_DETAIL_TARGET_LOADING_SELECTOR = (
    ".el-loading-mask:visible,.ant-spin-spinning:visible,"
    "[aria-busy='true']:visible,[data-loading='true']:visible"
)
_DETAIL_TARGET_CONTENT_SELECTOR = (
    ".component-box:visible,.detail-content:visible,.detail-main:visible,"
    ".el-tab-pane.is-active:visible,.vertical-menu:visible"
)


def _detail_target_snapshot(page) -> str:
    """Return visible target content only after the detail view has settled."""
    try:
        if page.locator(_DETAIL_TARGET_LOADING_SELECTOR).count():
            return ""
        target = page.locator(_DETAIL_TARGET_CONTENT_SELECTOR).last
        if not target.count() or not target.is_visible():
            return ""
        return re.sub(r"\s+", " ", target.inner_text()).strip()
    except Exception:
        return ""


def _wait_for_detail_action_or_stable_absence(
    page,
    action: str,
    *,
    timeout: int,
    stable_grace_ms: int = 5_000,
    poll_ms: int = 250,
):
    """Wait for a detail action, but stop once its already-stable page lacks it.

    A fixed full action timeout is useful while a detail tab is still rendering.
    It only becomes wasteful after the visible target has remained unchanged with
    no loading indicator.  Do not treat an empty snapshot as stable: that still
    needs the ordinary timeout because the page has not rendered enough evidence.
    """
    deadline = time.monotonic() + max(1, timeout) / 1000
    stable_since: float | None = None
    previous_snapshot = ""
    while time.monotonic() < deadline:
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        target = visible_action(page, action, timeout=min(1_000, remaining_ms))
        if target is not None:
            return target

        snapshot = _detail_target_snapshot(page)
        now = time.monotonic()
        if snapshot and snapshot == previous_snapshot:
            if stable_since is None:
                stable_since = now
            elif (now - stable_since) * 1000 >= max(0, stable_grace_ms):
                return None
        else:
            previous_snapshot = snapshot
            stable_since = now if snapshot else None
        if hasattr(page, "wait_for_timeout"):
            page.wait_for_timeout(max(1, poll_ms))
        else:
            break
    return None


def _normalized_action_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _locator_enabled(locator) -> bool:
    try:
        return locator.is_enabled()
    except Exception:
        return True


def _locator_visible(locator) -> bool:
    try:
        return bool(locator.count() and locator.is_visible())
    except Exception:
        return False


def _record_name_link_action(record):
    """Return a clickable business-name link inside a parent list record."""
    links = record.locator("a:visible,[role='link']:visible")
    for index in range(links.count()):
        link = links.nth(index)
        try:
            if not link.is_visible() or not _locator_enabled(link):
                continue
            labels = (
                link.inner_text(),
                link.get_attribute("title") or "",
                link.get_attribute("aria-label") or "",
            )
            normalized = [
                _normalized_action_text(label)
                for label in labels
                if _normalized_action_text(label)
            ]
            if not normalized:
                continue
            if any(label in _RECORD_DETAIL_ACTION_TEXTS for label in normalized):
                continue
            return link
        except Exception:
            continue
    return None


def _record_detail_entry(record):
    detail_action = record.locator(
        'button:has-text("详情"),a:has-text("详情"),'
        'button:has-text("查看"),a:has-text("查看")'
    ).first
    if _locator_visible(detail_action) and _locator_enabled(detail_action):
        return detail_action
    return _record_name_link_action(record)


def _record_business_id(record_identity) -> str:
    """Return the authoritative persisted ID, never a display marker."""
    if isinstance(record_identity, str | tuple | list | set):
        return ""
    return _normalized_action_text(
        str(getattr(record_identity, "business_id", "") or "")
    )


def _normalized_record_identity_values(record_identity) -> tuple[str, ...]:
    """Use a persisted business ID exclusively when one is available."""
    if record_identity is None:
        return ()
    if business_id := _record_business_id(record_identity):
        return (business_id,)
    if isinstance(record_identity, str):
        values = [record_identity]
    elif isinstance(record_identity, (tuple, list, set)):
        values = list(record_identity)
    else:
        values = list(getattr(record_identity, "record_markers", ()) or ())
    return tuple(dict.fromkeys(
        normalized for value in values
        if (normalized := _normalized_action_text(str(value or "")))
    ))


def _record_matches_identity(record, identities: tuple[str, ...]) -> bool:
    if not identities:
        return False
    try:
        attributes = (
            "data-key", "data-row-key", "data-id", "data-record-id", "row-key",
        )
        attribute_values = {
            _normalized_action_text(record.get_attribute(name) or "")
            for name in attributes
        }
        if any(identity in attribute_values for identity in identities):
            return True
        cells = record.locator("td,[role='cell']")
        values = cells.all_inner_texts() if cells.count() else record.inner_text().splitlines()
        rendered_values = {
            _normalized_action_text(value) for value in values
            if _normalized_action_text(value)
        }
        return any(
            value == identity or value.startswith(identity)
            for identity in identities
            for value in rendered_values
        )
    except Exception:
        return False


def _record_for_identity(candidates, identities: tuple[str, ...]):
    matches = [
        candidates.nth(index)
        for index in range(candidates.count())
        if _record_matches_identity(candidates.nth(index), identities)
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ParentRecordIdentityUnavailableError(
            "Parent list does not contain the automation-created record; "
            f"identities={identities!r}"
        )
    raise AssertionError(
        "Multiple parent records match the automation-created record; "
        f"identities={identities!r}, matches={len(matches)}"
    )


def _stable_identity_for_record(record, candidates) -> tuple[str, ...]:
    """Capture one value that uniquely identifies the selected row, never its locator."""
    attributes = (
        "data-key", "data-row-key", "data-id", "data-record-id", "row-key",
    )
    for name in attributes:
        try:
            value = _normalized_action_text(record.get_attribute(name) or "")
            if value and sum(
                _normalized_action_text(candidates.nth(index).get_attribute(name) or "")
                == value
                for index in range(candidates.count())
            ) == 1:
                return (value,)
        except Exception:
            continue

    rendered: list[str] = []
    link = _record_name_link_action(record)
    if link is not None:
        try:
            rendered.extend((
                link.inner_text(),
                link.get_attribute("title") or "",
                link.get_attribute("aria-label") or "",
            ))
        except Exception:
            pass
    try:
        cells = record.locator("td,[role='cell']")
        rendered.extend(cells.all_inner_texts() if cells.count() else ())
    except Exception:
        pass
    values = sorted(
        {
            normalized
            for value in rendered
            if (normalized := _normalized_action_text(value))
            and normalized not in _RECORD_DETAIL_ACTION_TEXTS
        },
        key=len,
        reverse=True,
    )
    for value in values:
        try:
            if sum(
                _record_matches_identity(candidates.nth(index), (value,))
                for index in range(candidates.count())
            ) == 1:
                return (value,)
        except Exception:
            continue
    return ()


def _parent_total_record_count(page) -> int | None:
    try:
        pagination = page.locator(_PARENT_PAGINATION_SELECTOR).first
        if not pagination.count() or not pagination.is_visible():
            return None
        match = re.search(r"共\s*(\d+)\s*条", pagination.inner_text())
        return int(match.group(1)) if match else None
    except Exception:
        return None


def _goto_parent_record_page(
    page, page_number: int, previous_snapshot: tuple[str, ...]
):
    if page_number <= 1:
        return page.locator(_PARENT_RECORD_SELECTOR)
    pagination = page.locator(_PARENT_PAGINATION_SELECTOR).first
    try:
        pagination.wait_for(state="visible", timeout=10_000)
        jump = pagination.locator(_PARENT_PAGE_JUMP_SELECTOR).first
        jump.fill(str(page_number))
        jump.press("Enter")
        pagination.locator(
            f"[aria-current='true'][aria-label='第 {page_number} 页']"
        ).first.wait_for(state="visible", timeout=20_000)
    except Exception as exc:
        raise ParentListNotReadyError(
            f"详情父列表无法跳转到第 {page_number} 页；url={page.url}"
        ) from exc
    return _wait_for_parent_records_ready(
        page,
        timeout=30_000,
        different_from=previous_snapshot,
    )


def _find_parent_record_by_identity(page, candidates, identities: tuple[str, ...]):
    """Find one persisted parent ID across the rendered parent-list pages."""
    try:
        return _record_for_identity(candidates, identities)
    except ParentRecordIdentityUnavailableError as first_error:
        total_records = _parent_total_record_count(page)
        page_size = candidates.count()
        if total_records is None or page_size <= 0:
            raise first_error

        page_count = (total_records + page_size - 1) // page_size
        previous_snapshot = _record_snapshot(candidates)
        for page_number in range(2, page_count + 1):
            candidates = _goto_parent_record_page(
                page, page_number, previous_snapshot,
            )
            previous_snapshot = _record_snapshot(candidates)
            try:
                return _record_for_identity(candidates, identities)
            except ParentRecordIdentityUnavailableError:
                continue
        raise ParentRecordIdentityUnavailableError(
            "详情父列表所有分页均未找到自动化创建记录；"
            f"identities={identities!r}, pages_checked={page_count}"
        ) from first_error


def _open_parent_list_record(page, record, candidates) -> object | None:
    """Open a known rendered record and retain a stable identity for later reuse."""
    selected_identity = _stable_identity_for_record(record, candidates) or None
    target = _record_detail_entry(record) or record
    before_url = page.url
    try:
        target.click(timeout=10_000)
    except Exception as exc:
        raise ParentListNotReadyError(
            "详情父列表记录在点击前持续刷新或被加载遮罩阻挡；"
            f"loading={_parent_list_loading(page)}, url={page.url}"
        ) from exc
    try:
        page.wait_for_function(
            "before => location.href !== before && !location.hash.endsWith('/404')",
            arg=before_url,
            timeout=20_000,
        )
    except Exception as exc:
        raise AssertionError(
            f"已点击父列表记录，但未进入有效详情页；当前地址：{page.url}"
        ) from exc
    assert not page.url.rstrip("/").endswith("#/404"), (
        f"详情记录上下文无效，页面跳转到 404：{page.url}"
    )
    return selected_identity


def _click_exact_text(locator, text: str, *, timeout: int = 10_000):
    exact = re.compile(rf"^\s*{re.escape(text)}\s*$")
    target = locator.locator(
        "button:visible,[role='button']:visible,label:visible,a:visible,span:visible"
    ).filter(has_text=exact).first
    try:
        target.wait_for(state="visible", timeout=timeout)
        target.click(timeout=timeout)
    except Exception as exc:
        raise ParentListNotReadyError(f"项目列表筛选控件不可用：{text}") from exc


def _wait_for_project_decision_results(
    page,
    *,
    status: str = _PROJECT_PROGRESS_STATUS_LABEL,
    expected_records: list[dict] | None = None,
    expect_empty: bool | None = None,
    timeout: int = 15_000,
):
    """Wait for a post-query filtered list, including its rendered empty state."""
    deadline = time.monotonic() + max(1, timeout) / 1000
    previous: tuple[str, ...] = ()
    stable_count = 0
    while time.monotonic() < deadline:
        candidates = page.locator(_PROJECT_LIST_RECORD_SELECTOR)
        snapshot = _record_snapshot(candidates)
        if not _parent_list_loading(page):
            statuses_match = bool(snapshot) and all(
                _project_status_from_card(candidates.nth(index)) == status
                for index in range(candidates.count())
            )
            records_match = expected_records is None or (
                len(expected_records) == candidates.count()
                and all(
                    _project_card_matches_response_record(
                        candidates.nth(index), expected_records[index], status,
                    )
                    for index in range(candidates.count())
                )
            )
            if snapshot and statuses_match and records_match:
                stable_count = stable_count + 1 if snapshot == previous else 1
                previous = snapshot
                if stable_count >= 2:
                    return candidates
            elif (
                expect_empty is not False
                and page.locator(_PROJECT_LIST_EMPTY_SELECTOR).count()
            ):
                raise ParentListEmptyError(f"项目状态“{status}”查询结果为空")
            else:
                previous = ()
                stable_count = 0
        else:
            previous = ()
            stable_count = 0
        page.wait_for_timeout(200)
    raise ParentListNotReadyError(f"项目状态“{status}”查询后列表未就绪")


def _request_json_payload(request) -> object:
    try:
        payload = request.post_data_json
        return payload() if callable(payload) else payload
    except Exception:
        raw = getattr(request, "post_data", "") or ""
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None


def _is_filtered_project_list_response(
    response, expected_status_code: str = "",
) -> bool:
    request = getattr(response, "request", None)
    if request is None:
        return False
    if str(getattr(request, "method", "")).upper() != "POST":
        return False
    if not str(getattr(response, "url", "")).split("?", 1)[0].endswith(
        "/fi-service/projInfo/listPage"
    ):
        return False
    payload = _request_json_payload(request)
    if not isinstance(payload, dict):
        return False
    actual_status = _normalized_action_text(str(payload.get("projStatus") or ""))
    expected_status = _normalized_action_text(expected_status_code)
    return bool(actual_status) and (
        not expected_status or actual_status == expected_status
    )


def _project_records_from_payload(payload: object) -> list[dict] | None:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return None
    for key in ("records", "rows", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    for key in ("data", "result"):
        if key not in payload:
            continue
        records = _project_records_from_payload(payload.get(key))
        if records is not None:
            return records
    return None


def _project_status_from_card(record) -> str:
    return _project_card_label_value(record, "项目状态")


def _project_card_label_value(record, label: str) -> str:
    try:
        cells = record.locator(_PROJECT_STATUS_CELL_SELECTOR)
        for index in range(cells.count()):
            text = _normalized_action_text(cells.nth(index).inner_text())
            match = re.fullmatch(rf"{re.escape(label)}[:：](.+)", text)
            if match:
                return match.group(1)
    except Exception:
        return ""
    return ""


def _project_name_from_card(record) -> str:
    try:
        names = record.locator(".card-name")
        if names.count() == 1:
            return _normalized_action_text(names.first.inner_text())
    except Exception:
        return ""
    return ""


def _project_card_matches_response_record(
    card, response_record: dict, status: str,
) -> bool:
    expected_name = _normalized_action_text(str(response_record.get("projName") or ""))
    if not expected_name or _project_name_from_card(card) != expected_name:
        return False
    if _project_status_from_card(card) != status:
        return False
    display_fields = {
        "项目类型": "projClassifyName",
        "责任板块": "belongSectionName",
        "实施主体公司": "inveName",
    }
    return all(
        not (expected := _normalized_action_text(str(response_record.get(code) or "")))
        or _project_card_label_value(card, label) == expected
        for label, code in display_fields.items()
    )


def _assert_project_list_business_success(payload: object, status: str) -> None:
    if not isinstance(payload, dict):
        raise ParentListNotReadyError(
            f"项目状态“{status}”查询响应不是业务 JSON 对象"
        )
    code = payload.get("code", payload.get("status"))
    success = payload.get("success")
    errors = payload.get("errors")
    has_errors = bool(errors) if isinstance(errors, (list, tuple, dict, str)) else False
    allowed_codes = {"0", "200", "success", "true"}
    if (
        success is False
        or has_errors
        or (code is not None and str(code).strip().lower() not in allowed_codes)
    ):
        message = _normalized_action_text(
            str(payload.get("message") or payload.get("msg") or "业务状态失败")
        )[:200]
        raise ParentListNotReadyError(
            f"项目状态“{status}”查询接口返回业务失败："
            f"code={code!r}, message={message!r}"
        )


def _capture_project_status_response(
    page, status: str, click: Callable[[], None],
) -> list[dict] | None:
    expected_status_code = _PROJECT_PROGRESS_STATUS_CODES.get(status, "")
    if not expected_status_code:
        raise ParentListNotReadyError(f"项目状态“{status}”缺少稳定状态码")
    expect_response = getattr(page, "expect_response", None)
    if not callable(expect_response):
        click()
        return None
    try:
        with expect_response(
            lambda response: _is_filtered_project_list_response(
                response, expected_status_code,
            ),
            timeout=15_000,
        ) as response_info:
            click()
        if not bool(getattr(response_info.value, "ok", False)):
            raise ParentListNotReadyError(
                f"项目状态“{status}”查询请求失败："
                f"HTTP {getattr(response_info.value, 'status', 0)}"
            )
        try:
            payload = response_info.value.json()
        except Exception as exc:
            raise ParentListNotReadyError(
                f"项目状态“{status}”查询响应不是有效 JSON"
            ) from exc
        _assert_project_list_business_success(payload, status)
        records = _project_records_from_payload(payload)
        if records is None:
            raise ParentListNotReadyError(
                f"项目状态“{status}”查询响应缺少项目记录集合"
            )
        mismatched = {
            _normalized_action_text(str(record.get("projStatusName") or ""))
            for record in records
            if record.get("projStatusName") not in (None, "")
            and _normalized_action_text(str(record.get("projStatusName"))) != status
        }
        if mismatched:
            raise ParentListNotReadyError(
                f"项目状态“{status}”查询返回了其他状态：{sorted(mismatched)!r}"
            )
        return records
    except ParentListNotReadyError:
        raise
    except Exception as exc:
        raise ParentListNotReadyError(
            f"项目状态“{status}”查询未捕获项目列表响应"
        ) from exc


def _click_project_status_filter(page, status_filter, status: str) -> list[dict] | None:
    """Select one status and wait for the list response emitted by that change."""
    return _capture_project_status_response(
        page,
        status,
        lambda: _click_exact_text(status_filter, status),
    )


def _click_project_list_query(page, status: str) -> list[dict] | None:
    """Reissue the current status query after a confirmed response/render race."""
    return _capture_project_status_response(
        page,
        status,
        lambda: _click_exact_text(
            page.locator(_PROJECT_LIST_QUERY_SELECTOR), "查询",
        ),
    )


def _project_progress_status_candidates(page, status: str):
    status_filter = page.locator(_PROJECT_STATUS_FILTER_SELECTOR).first
    if not _locator_visible(status_filter):
        _click_exact_text(page.locator(_PROJECT_LIST_MORE_SELECTOR), "···")
        try:
            status_filter.wait_for(state="visible", timeout=10_000)
        except Exception as exc:
            raise ParentListNotReadyError("项目列表未展示项目状态筛选条件") from exc
    records = _click_project_status_filter(page, status_filter, status)
    try:
        candidates = _wait_for_project_decision_results(
            page,
            status=status,
            expected_records=records,
            expect_empty=(not records) if records is not None else None,
        )
    except ParentListEmptyError:
        raise
    except ParentListNotReadyError:
        records = _click_project_list_query(page, status)
        candidates = _wait_for_project_decision_results(
            page,
            status=status,
            expected_records=records,
            expect_empty=(not records) if records is not None else None,
        )
    expected_business_id = ""
    if records:
        expected_business_id = _normalized_action_text(
            str(records[0].get("projId") or records[0].get("id") or "")
        )
        if not expected_business_id:
            raise ParentListNotReadyError(
                f"项目状态“{status}”查询首条记录缺少项目 ID"
            )
    return _ProjectStatusCandidates(candidates, expected_business_id)


def _enter_project_progress_decision_parent(page, detail_url: str) -> object | None:
    """Open the first project returned by the first non-empty status query."""
    page.goto(detail_parent_url(detail_url), wait_until="domcontentloaded")
    empty_statuses: list[str] = []
    for status in _PROJECT_PROGRESS_ELIGIBLE_STATUSES:
        try:
            status_result = _project_progress_status_candidates(page, status)
        except ParentListEmptyError:
            empty_statuses.append(status)
            continue
        candidates = status_result.candidates
        selected_identity = _open_parent_list_record(page, candidates.nth(0), candidates)
        context = _project_progress_context_from_page(page, selected_identity)
        if not isinstance(context, ProjectProgressParentContext):
            raise ParentListNotReadyError("项目详情地址未提供项目 ID，无法固定项目进度父项目")
        if (
            status_result.expected_business_id
            and context.business_id != status_result.expected_business_id
        ):
            raise ParentListNotReadyError(
                f"项目状态“{status}”查询首条 ID 与打开详情不一致："
                f"expected={status_result.expected_business_id}, actual={context.business_id}"
            )
        return context
    raise ParentListEmptyError(
        "未找到状态为“项目决策及后续状态”的项目："
        + "、".join(empty_statuses)
    )


def enter_detail_record(
    page, detail_url: str, record_index: int = 0, *, record_identity=None,
) -> object | None:
    """Enter a detail page through a real list record so route state/ID is present."""
    parent_url = detail_parent_url(detail_url)
    page.goto(parent_url, wait_until="domcontentloaded")
    candidates = _wait_for_parent_records_ready(page)

    identities = _normalized_record_identity_values(record_identity)
    if identities:
        record = _find_parent_record_by_identity(page, candidates, identities)
    else:
        page_size = candidates.count()
        total_records = _parent_total_record_count(page)
        if total_records is not None and record_index >= total_records:
            raise AssertionError(
                f"父列表只有 {total_records} 条可进入记录，"
                f"无法尝试第 {record_index + 1} 条"
            )
        page_number, local_index = divmod(record_index, page_size)
        page_number += 1
        if page_number > 1:
            candidates = _goto_parent_record_page(
                page,
                page_number,
                previous_snapshot=_record_snapshot(candidates),
            )
        candidate_count = candidates.count()
        if local_index >= candidate_count:
            available = total_records if total_records is not None else (
                (page_number - 1) * page_size + candidate_count
            )
            raise AssertionError(
                f"父列表只有 {available} 条可进入记录，"
                f"无法尝试第 {record_index + 1} 条"
            )
        record = candidates.nth(local_index)
    selected_identity = _open_parent_list_record(page, record, candidates)
    return record_identity if identities else selected_identity


def detail_navigation_labels(module_name: str, action: str) -> list[str]:
    parts = [part.strip() for part in module_name.split("/") if part.strip()]
    try:
        detail_index = parts.index("详情")
    except ValueError:
        return []
    labels = parts[detail_index + 1 :]
    if action in labels:
        labels = labels[: labels.index(action)]
    return labels


def navigate_detail_module(
    page, module_name: str, action: str, navigation_labels: list[str] | None = None,
    *, timeout: int = 20_000,
) -> None:
    labels = (
        navigation_labels
        if navigation_labels is not None
        else detail_navigation_labels(module_name, action)
    )
    if not labels:
        return

    top_label = labels[0]
    top_tab = page.locator(".detail-menu__item:visible").filter(
        has_text=re.compile(rf"^\s*{re.escape(top_label)}\s*$")
    ).first
    try:
        top_tab.wait_for(state="visible", timeout=timeout)
    except Exception as exc:
        if visible_action(page, action, timeout=1_000) is not None:
            return
        raise AssertionError(f"详情页未加载顶层页签：{top_label}") from exc
    top_tab.click()

    for label in labels[1:]:
        menu_item = page.locator(
            ".vertical-menu .item:visible,.vertical-menu .menu-item:visible"
        ).filter(has_text=re.compile(rf"^\s*{re.escape(label)}\s*$")).first
        try:
            menu_item.wait_for(state="visible", timeout=timeout)
        except Exception as exc:
            if visible_action(page, action, timeout=1_000) is not None:
                return
            raise AssertionError(
                f"详情页未加载目标子菜单：{' / '.join(labels)}"
            ) from exc
        menu_item.click()

    # "详情" is a navigation context, not a button rendered by the page.
    # Detail-page checks end after the requested module menu is active.
    if action == "详情":
        return

    target_action = _wait_for_detail_action_or_stable_absence(
        page, action, timeout=timeout
    )
    if target_action is None:
        active_items = page.locator(
            ".detail-menu__item--active:visible,.vertical-menu .active:visible"
        ).all_inner_texts()
        visible_buttons = page.locator("button:visible").all_inner_texts()
        component_text = page.locator(".component-box:visible").last.inner_text()[:500]
        raise DetailActionUnavailableError(
            f"已点击详情菜单“{' / '.join(labels)}”，但目标页面未渲染操作：{action}；"
            f"活动菜单={active_items!r}；可见按钮={visible_buttons!r}；"
            f"组件内容={component_text!r}"
        )


def _detail_action_needs_child_record(action: str) -> bool:
    return str(action or "").strip().startswith(_CHILD_RECORD_ACTION_PREFIXES)


def _detail_precondition_is_missing(failures: list[str]) -> bool:
    """Only provision after the rendered target subtable proves it has no rows."""
    return any("暂无数据" in failure for failure in failures)


def _module_name_for_seed_action(module_name: str, action: str) -> str:
    parts = [part.strip() for part in module_name.split("/") if part.strip()]
    if parts and parts[-1] == action:
        parts[-1] = "新增"
    return "/".join(parts)


def _open_provisioned_detail_module(
    page,
    detail_url: str,
    module_name: str,
    action: str,
    provisioned,
    *,
    navigation_labels: list[str],
    provision_child_record: Callable[[], object] | None,
) -> object:
    """Open one provisioned parent and seed a child only for row-level actions."""
    enter_detail_record(page, detail_url, record_identity=provisioned)
    try:
        navigate_detail_module(
            page, module_name, action, navigation_labels=navigation_labels,
        )
    except AssertionError:
        if not provision_child_record or not _detail_action_needs_child_record(action):
            raise
        seed_module_name = _module_name_for_seed_action(module_name, action)
        seed_labels = detail_navigation_labels(seed_module_name, "新增")
        navigate_detail_module(
            page, seed_module_name, "新增", navigation_labels=seed_labels,
        )
        child = provision_child_record()
        if not _normalized_record_identity_values(child):
            raise AssertionError(
                "详情子记录前置创建未返回业务 ID 或自动化标识，无法继续行级操作"
            )
        enter_detail_record(page, detail_url, record_identity=provisioned)
        navigate_detail_module(
            page, module_name, action, navigation_labels=navigation_labels,
        )
    return provisioned


def enter_available_detail_module(
    page,
    detail_url: str,
    module_name: str,
    action: str,
    max_records: int | None = None,
    *,
    record_identity=None,
    provision_record: Callable[[], object] | None = None,
    provision_child_record: Callable[[], object] | None = None,
) -> object | None:
    if max_records is None:
        try:
            max_records = max(
                1, int(os.getenv("EI_DETAIL_RECORD_SCAN_LIMIT", "25"))
            )
        except ValueError:
            max_records = 25
    navigation_labels = detail_navigation_labels(module_name, action)
    assert action not in navigation_labels, (
        f"自动化详情导航路径自检失败：操作“{action}”仍被识别为详情菜单；"
        f"导航计划={' / '.join(navigation_labels)}"
    )
    print(
        "DETAIL_NAVIGATION_SELF_CHECK "
        f"menus={' / '.join(navigation_labels) or '<detail-root>'} "
        f"operation={action}",
        flush=True,
    )
    if _is_project_progress_module(module_name):
        if isinstance(record_identity, ProjectProgressParentContext):
            return _open_project_progress_parent_context(
                page,
                record_identity,
                module_name,
                action,
                navigation_labels=navigation_labels,
                provision_child_record=provision_child_record,
            )
        selected_identity = _enter_project_progress_decision_parent(page, detail_url)
        if isinstance(selected_identity, ProjectProgressParentContext):
            return _open_project_progress_parent_context(
                page,
                selected_identity,
                module_name,
                action,
                navigation_labels=navigation_labels,
                provision_child_record=provision_child_record,
            )
        navigate_detail_module(
            page, module_name, action, navigation_labels=navigation_labels,
        )
        return selected_identity
    identities = _normalized_record_identity_values(record_identity)
    failures = []
    if identities:
        try:
            return _open_provisioned_detail_module(
                page,
                detail_url,
                module_name,
                action,
                record_identity,
                navigation_labels=navigation_labels,
                provision_child_record=provision_child_record,
            )
        except AssertionError:
            raise
    for record_index in range(max_records):
        selected_identity = None
        try:
            selected_identity = enter_detail_record(
                page, detail_url, record_index=record_index,
            )
            navigate_detail_module(
                page, module_name, action, navigation_labels=navigation_labels
            )
            return selected_identity
        except ParentListEmptyError:
            provisioner = provision_record
            if provisioner is None:
                raise
            provisioned = provisioner()
            provisioned_identities = _normalized_record_identity_values(provisioned)
            if not provisioned_identities:
                raise AssertionError(
                    "Detail-data provisioning did not return an ID or stable record marker"
                )
            return _open_provisioned_detail_module(
                page,
                detail_url,
                module_name,
                action,
                provisioned,
                navigation_labels=navigation_labels,
                provision_child_record=provision_child_record,
            )
        except ParentListNotReadyError as exc:
            failures.append(str(exc))
            break
        except AssertionError as exc:
            if "未进入有效详情页" in str(exc):
                try:
                    navigate_detail_module(
                        page,
                        module_name,
                        action,
                        navigation_labels=navigation_labels,
                        timeout=2_000,
                    )
                    return selected_identity
                except AssertionError:
                    pass
            failures.append(str(exc))
            if "父列表只有" in str(exc):
                break
    should_provision = _detail_precondition_is_missing(failures)
    provisioner = provision_record
    if provisioner is not None and should_provision:
        provisioned = provisioner()
        provisioned_identities = _normalized_record_identity_values(provisioned)
        if not provisioned_identities:
            raise AssertionError(
                "Detail-data provisioning did not return an ID or stable record marker"
            )
        return _open_provisioned_detail_module(
            page,
            detail_url,
            module_name,
            action,
            provisioned,
            navigation_labels=navigation_labels,
            provision_child_record=provision_child_record,
        )
    raise AssertionError(
        f"前 {min(max_records, len(failures))} 条业务记录均无法进入目标详情模块；"
        + "；".join(failures)
    )


def detail_context_preparer_from_env(
    provision_record: Callable[[], object] | None = None,
) -> Callable[[object], object | None] | None:
    """Build the common-form context hook for a detail-module test command."""
    if os.getenv("EI_REQUIRES_BUSINESS_ID", "").lower() != "true":
        return None
    detail_url = os.getenv("EI_FORM_URL", "").strip()
    module_name = os.getenv("EI_MODULE_NAME", "").strip()
    action = os.getenv("EI_ACTION", "").strip()
    if not detail_url or not module_name or not action:
        raise AssertionError(
            "详情模块通用用例缺少 EI_FORM_URL、EI_MODULE_NAME 或 EI_ACTION 上下文"
        )

    cached_parent_identity = _load_project_progress_parent_context(
        detail_url, module_name,
    )

    def provision_parent(page):
        nonlocal cached_parent_identity
        if cached_parent_identity is None:
            page.goto(detail_parent_url(detail_url), wait_until="domcontentloaded")
            cached_parent_identity = provision_record()
        return cached_parent_identity

    def prepare(page) -> object | None:
        nonlocal cached_parent_identity
        parent_provisioner = (
            (lambda: provision_parent(page)) if provision_record is not None else None
        )
        try:
            selected_identity = enter_available_detail_module(
                page,
                detail_url,
                module_name,
                action,
                record_identity=cached_parent_identity,
                provision_record=parent_provisioner,
                provision_child_record=provision_record,
            )
        except ParentRecordIdentityUnavailableError:
            if isinstance(cached_parent_identity, ProjectProgressParentContext):
                raise
            if cached_parent_identity is None:
                raise
            print(
                "DETAIL_PARENT_IDENTITY_INVALIDATED "
                f"identities={_normalized_record_identity_values(cached_parent_identity)!r}",
                flush=True,
            )
            cached_parent_identity = None
            selected_identity = enter_available_detail_module(
                page,
                detail_url,
                module_name,
                action,
                record_identity=None,
                provision_record=parent_provisioner,
                provision_child_record=provision_record,
            )
        if _normalized_record_identity_values(selected_identity):
            cached_parent_identity = selected_identity
            if isinstance(cached_parent_identity, ProjectProgressParentContext):
                _store_project_progress_parent_context(
                    detail_url, module_name, cached_parent_identity,
                )
            print(
                "DETAIL_PARENT_IDENTITY_CACHED "
                f"identities={_normalized_record_identity_values(cached_parent_identity)!r}",
                flush=True,
            )
        return selected_identity

    return prepare
