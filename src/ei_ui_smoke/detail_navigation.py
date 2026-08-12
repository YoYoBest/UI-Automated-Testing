import os
import re
import time
from collections.abc import Callable

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


def _normalized_record_identity_values(record_identity) -> tuple[str, ...]:
    if record_identity is None:
        return ()
    if isinstance(record_identity, str):
        values = [record_identity]
    elif isinstance(record_identity, (tuple, list, set)):
        values = list(record_identity)
    else:
        values = [getattr(record_identity, "business_id", "")]
        values.extend(getattr(record_identity, "record_markers", ()) or ())
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


def enter_detail_record(
    page, detail_url: str, record_index: int = 0, *, record_identity=None,
) -> object | None:
    """Enter a detail page through a real list record so route state/ID is present."""
    parent_url = detail_parent_url(detail_url)
    page.goto(parent_url, wait_until="domcontentloaded")
    candidates = _wait_for_parent_records_ready(page)

    identities = _normalized_record_identity_values(record_identity)
    if identities:
        record = _record_for_identity(candidates, identities)
        selected_identity = record_identity
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
        selected_identity = _stable_identity_for_record(record, candidates) or None
    target = _record_detail_entry(record) or record
    before_url = page.url
    try:
        target.click(timeout=10_000)
    except Exception as exc:
        raise ParentListNotReadyError(
            "详情父列表记录在点击前持续刷新或被加载遮罩阻挡："
            f"record_index={record_index + 1}, loading={_parent_list_loading(page)}, "
            f"url={page.url}"
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
        raise AssertionError(
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
    identities = _normalized_record_identity_values(record_identity)
    if identities:
        return _open_provisioned_detail_module(
            page,
            detail_url,
            module_name,
            action,
            record_identity,
            navigation_labels=navigation_labels,
            provision_child_record=provision_child_record,
        )

    failures = []
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
            if provision_record is None:
                raise
            provisioned = provision_record()
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
    if provision_record is not None and _detail_precondition_is_missing(failures):
        provisioned = provision_record()
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

    cached_parent_identity = None

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
            print(
                "DETAIL_PARENT_IDENTITY_CACHED "
                f"identities={_normalized_record_identity_values(cached_parent_identity)!r}",
                flush=True,
            )
        return selected_identity

    return prepare
