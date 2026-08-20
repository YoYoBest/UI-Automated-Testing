from __future__ import annotations

import re
from datetime import date as CalendarDate
from pathlib import Path
from typing import Any, TYPE_CHECKING

from .contracts import field_kind
from .models import FieldDefinition, ResolvedField

DATE_VALUE_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\D+(?P<month>\d{1,2})\D+(?P<day>\d{1,2})(?!\d)"
)

if TYPE_CHECKING:
    from playwright.sync_api import Locator, Page


FIELD_CONTROL_SELECTOR = (
    "input:not([type=hidden]),textarea,select,[contenteditable=true],"
    "[role=combobox],[role=radio],[role=checkbox],[role=switch],"
    ".el-select,.el-radio-group,.el-checkbox-group,.el-switch,"
    ".el-date-editor,.el-input-number"
)
FIELD_LABEL_SELECTORS = (
    ".el-form-item__label",
    ".ant-form-item-label",
    "label",
    ".purvar_form_item_title",
    ".purvar_form_label",
    "[class*='field-label']",
    "[class*='form_label']",
    "[class*='label']",
)
PROMPT_PREFIX_RE = re.compile(r"^\s*(?:请\s*)?(?:输入|填写|选择|上传|勾选)")


class FieldInteractor:
    def __init__(self, page: "Page", upload_files: dict[str, Path] | None = None):
        self.page = page
        self.upload_files = upload_files or {}

    def locate(self, field: ResolvedField, *, root=None) -> "Locator":
        code = field.definition.field_code
        selectors = [
            f'[data-field-code="{code}"] {FIELD_CONTROL_SELECTOR}',
            f'[field-code="{code}"] {FIELD_CONTROL_SELECTOR}',
            f'.el-form-item:has([name="{code}"]) [name="{code}"]', f'[name="{code}"]', f'#{code}',
        ]
        if field.dom and field.dom.selector:
            selectors.insert(0, field.dom.selector)
        if field.definition.field_name:
            for label in self._label_candidates(field.definition.field_name):
                literal = self._css_string(label)
                label_match = ",".join(
                    f'{selector}:text-is("{literal}")'
                    for selector in FIELD_LABEL_SELECTORS
                )
                selectors.extend([
                    f'.el-form-item:has({label_match}) {FIELD_CONTROL_SELECTOR}',
                    f'.ant-form-item:has({label_match}) {FIELD_CONTROL_SELECTOR}',
                    f'.purvar_form_item:has({label_match}) {FIELD_CONTROL_SELECTOR}',
                ])
        search_root = root if root is not None else self.page
        for selector in selectors:
            try:
                locator = search_root.locator(selector).first
                if locator.count() and locator.is_visible():
                    tag = locator.evaluate("el => el.tagName.toLowerCase()")
                    role = (locator.get_attribute("role") or "").lower()
                    if tag in {"input", "textarea", "select"} or role in {
                        "combobox", "radio", "checkbox", "switch",
                    } or locator.get_attribute("contenteditable") == "true":
                        return locator
                    control = locator.locator(
                        "input:not([type=hidden]),textarea,select,[contenteditable=true],"
                        "[role=combobox],[role=radio],[role=checkbox],[role=switch]"
                    ).first
                    if control.count() and control.is_visible():
                        return control
            except Exception:
                continue
        raise AssertionError(f"Field not rendered: {code} ({field.definition.field_name})")

    @staticmethod
    def _css_string(value: str) -> str:
        return (value or "").replace("\\", "\\\\").replace('"', '\\"')

    @staticmethod
    def _label_candidates(value: str) -> list[str]:
        normalized = re.sub(r"\s+", " ", value or "").strip()
        candidates = [normalized] if normalized else []
        stripped = PROMPT_PREFIX_RE.sub("", normalized).strip()
        if stripped and stripped not in candidates:
            candidates.append(stripped)
        return candidates

    def fill(
        self,
        field: ResolvedField,
        value: Any,
        *,
        root=None,
        raw_date_input: bool = False,
    ) -> Any:
        definition = field.definition
        if definition.readonly or definition.locked or value is None:
            return value
        kind = field_kind(definition.field_type)
        locator = self.locate(field) if root is None else self.locate(field, root=root)
        tag = locator.evaluate("el => el.tagName.toLowerCase()")
        role = (locator.get_attribute("role") or "").lower()
        classes = (locator.get_attribute("class") or "").lower()
        select_like = tag == "select" or role == "combobox" or "select__input" in classes
        if field.dom is not None and field.dom.kind == "year":
            return self._select_year(locator, value)
        if kind in {"date", "datetime"}:
            if raw_date_input:
                return self._fill_raw_date_input(locator, value, definition)
            return self._select_date(locator, value)
        if select_like and kind not in {"select", "multi_select", "user_select", "org_select", "tree_select"}:
            return self._select(locator, value)
        if kind in {"text", "textarea", "number", "time"}:
            if locator.get_attribute("readonly") is not None or not locator.is_editable():
                raise AssertionError(
                    f"Field is not editable: {definition.field_code} ({definition.field_name})"
                )
            locator.fill(str(value))
            # Browsers may enforce maxlength without raising.  The value used for
            # request matching and later readback must be the value that the
            # control retained, not the longer value that was requested.
            if kind in {"text", "textarea"}:
                try:
                    actual = str(locator.input_value())
                except Exception:
                    return value
                requested = str(value)
                if actual != requested:
                    maxlength = getattr(field.dom, "maxlength", None)
                    if maxlength is None:
                        maxlength = definition.props.get("maxlength")
                    try:
                        limit = int(maxlength)
                    except (TypeError, ValueError):
                        limit = None
                    if not (
                        limit is not None
                        and len(requested) > limit
                        and actual == requested[:limit]
                    ):
                        raise AssertionError(
                            "Text control did not retain the requested value: "
                            f"{definition.field_code}; requested_length={len(requested)}, "
                            f"actual_length={len(actual)}"
                        )
                return actual
            return value
        if kind in {"select", "multi_select", "user_select", "org_select", "tree_select"}:
            return self._select(locator, value)
        if kind in {"radio", "checkbox", "switch"}:
            self._check(locator)
            return value
        if kind in {"slider", "rate"}:
            locator.click()
            return value
        if kind in {"file", "image"}:
            path = self.upload_files.get(definition.field_code)
            if path:
                locator.set_input_files(str(path))
            return str(path) if path else None
        return value

    @staticmethod
    def is_valid_date_value(value: Any) -> bool:
        """Whether a value can be selected through a calendar picker."""
        try:
            FieldInteractor._parse_date_value(value)
        except AssertionError:
            return False
        return True

    @staticmethod
    def _fill_raw_date_input(locator: "Locator", value: Any, definition: FieldDefinition) -> str:
        """Type a date verbatim so negative cases reach the browser validator."""
        if locator.get_attribute("readonly") is not None or not locator.is_editable():
            raise AssertionError(
                f"Date field is not manually editable: {definition.field_code} "
                f"({definition.field_name})"
            )
        locator.fill(str(value))
        locator.press("Tab")
        try:
            return str(locator.input_value())
        except Exception:
            return str(value)

    def clear(self, field: ResolvedField, *, root=None) -> Any:
        locator = self.locate(field) if root is None else self.locate(field, root=root)
        if field.dom is not None and field.dom.kind == "year":
            picker = self._year_picker(locator)
            try:
                if not locator.input_value().strip():
                    return ""
            except Exception:
                pass
            picker.hover()
            clear = picker.locator(
                ".el-input__clear,.el-date-editor__clear,[class*='clear-icon'],"
                "[aria-label='clear']"
            ).first
            if not clear.count() or not clear.is_visible():
                raise AssertionError(
                    f"Year picker has no clear action: {field.definition.field_code}"
                )
            clear.click(force=True)
            for _ in range(20):
                if not locator.input_value().strip():
                    return ""
                self.page.wait_for_timeout(100)
            raise AssertionError(
                f"Year picker did not clear: {field.definition.field_code}"
            )
        if locator.is_editable():
            locator.fill("")
            return ""
        raise AssertionError(
            f"Field cannot be cleared: {field.definition.field_code} ({field.definition.field_name})"
        )

    def _select_year(self, locator: "Locator", value: Any) -> str:
        match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
        if not match:
            raise AssertionError(f"Invalid year value: {value!r}")
        target_year = match.group(1)
        picker = self._year_picker(locator)
        picker.click(force=True)
        panel = self.page.locator(
            ".el-picker-panel:visible:has(.el-year-table)"
        ).last
        try:
            panel.wait_for(state="visible", timeout=5_000)
        except Exception as exc:
            raise AssertionError("Year picker panel did not open") from exc

        for _ in range(30):
            cells = panel.locator(
                ".el-year-table td:not(.disabled):not(.is-disabled)"
            )
            visible_years: list[int] = []
            for index in range(cells.count()):
                cell = cells.nth(index)
                text = (cell.inner_text() or "").strip()
                year_match = re.search(r"(?<!\d)(\d{4})(?!\d)", text)
                if not cell.is_visible() or not year_match:
                    continue
                year = int(year_match.group(1))
                visible_years.append(year)
                if str(year) == target_year:
                    cell.click(force=True)
                    for _ in range(20):
                        actual = (locator.input_value() or "").strip()
                        actual_match = re.search(r"(?<!\d)(\d{4})(?!\d)", actual)
                        if actual_match and actual_match.group(1) == target_year:
                            return target_year
                        self.page.wait_for_timeout(100)
                    raise AssertionError(
                        f"Year picker selected {target_year} but did not update its value"
                    )

            if not visible_years:
                self.page.wait_for_timeout(150)
                continue
            direction = "d-arrow-left" if int(target_year) < min(visible_years) else "d-arrow-right"
            navigation = panel.locator(f"button.{direction},.{direction}").first
            if not navigation.count() or not navigation.is_visible():
                break
            navigation.click(force=True)
            self.page.wait_for_timeout(150)
        raise AssertionError(f"Year picker has no enabled year {target_year}")

    def _select_date(self, locator: "Locator", value: Any) -> str:
        target = self._parse_date_value(value)
        picker = self._date_picker(locator)
        picker.click(force=True)
        panel = self.page.locator(
            ".el-picker-panel:visible:has(.el-date-table)"
        ).last
        try:
            panel.wait_for(state="visible", timeout=5_000)
        except Exception as exc:
            raise AssertionError("Date picker panel did not open") from exc

        for _ in range(120):
            displayed = self._displayed_picker_month(panel)
            if displayed != (target.year, target.month):
                self._navigate_date_picker(panel, displayed, target)
                self.page.wait_for_timeout(100)
                continue
            cells = panel.locator(
                ".el-date-table td.available:not(.prev-month):not(.next-month):"
                "not(.disabled):not(.is-disabled)"
            )
            for index in range(cells.count()):
                cell = cells.nth(index)
                if not cell.is_visible():
                    continue
                day = (cell.locator("span").first.inner_text() or "").strip()
                if day != str(target.day):
                    continue
                cell.click(force=True)
                self._close_date_picker(panel)
                self._wait_for_date_value(locator, target)
                return str(value)
            raise AssertionError(
                f"Date picker has no enabled day {target.isoformat()}"
            )
        raise AssertionError(
            f"Date picker could not navigate to {target.isoformat()}"
        )

    @staticmethod
    def _parse_date_value(value: Any) -> CalendarDate:
        match = DATE_VALUE_RE.search(str(value or ""))
        if not match:
            raise AssertionError(f"Invalid date value: {value!r}")
        try:
            return CalendarDate(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            )
        except ValueError as exc:
            raise AssertionError(f"Invalid date value: {value!r}") from exc

    @staticmethod
    def _date_picker(locator: "Locator") -> "Locator":
        picker = locator.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-date-editor ')][1]"
        ).first
        if not picker.count() or not picker.is_visible():
            raise AssertionError("Date picker wrapper was not found")
        return picker

    @staticmethod
    def _displayed_picker_month(panel: "Locator") -> tuple[int, int]:
        labels = panel.locator(".el-date-picker__header-label")
        values = [
            (labels.nth(index).inner_text() or "").strip()
            for index in range(labels.count())
        ]
        year = next(
            (
                int(match.group()) for text in values
                if (match := re.search(r"(?<!\d)\d{4}(?!\d)", text))
            ),
            None,
        )
        month = next(
            (
                int(match.group()) for text in values
                if (match := re.search(r"(?<!\d)(?:1[0-2]|0?[1-9])(?!\d)", text))
            ),
            None,
        )
        if year is None or month is None:
            raise AssertionError("Date picker current month is not readable")
        return year, month

    def _navigate_date_picker(
        self,
        panel: "Locator",
        displayed: tuple[int, int],
        target: CalendarDate,
    ) -> None:
        direction = "arrow-left" if displayed > (target.year, target.month) else "arrow-right"
        navigation = panel.locator(f"button.{direction},.{direction}").first
        if not navigation.count() or not navigation.is_visible():
            raise AssertionError(
                f"Date picker cannot navigate from {displayed[0]:04d}-{displayed[1]:02d}"
            )
        navigation.click(force=True)

    def _wait_for_date_value(self, locator: "Locator", target: CalendarDate) -> None:
        for _ in range(20):
            actual = (locator.input_value() or "").strip()
            match = DATE_VALUE_RE.search(actual)
            if match and (
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
            ) == (target.year, target.month, target.day):
                return
            self.page.wait_for_timeout(100)
        raise AssertionError(
            f"Date picker selected {target.isoformat()} but did not update its value"
        )

    def _close_date_picker(self, panel: "Locator") -> None:
        if not panel.is_visible():
            return
        confirm = panel.locator(
            ".el-picker-panel__footer button:has-text('确定'),"
            ".el-picker-panel__footer button:has-text('OK')"
        ).last
        if confirm.count() and confirm.is_visible():
            confirm.click(force=True)
        for _ in range(10):
            if not panel.is_visible():
                return
            self.page.wait_for_timeout(50)
        self.page.keyboard.press("Escape")
        for _ in range(10):
            if not panel.is_visible():
                return
            self.page.wait_for_timeout(50)
        raise AssertionError("Date picker remained open after date selection")

    @staticmethod
    def _year_picker(locator: "Locator") -> "Locator":
        picker = locator.locator(
            "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),' el-date-editor ')][1]"
        ).first
        if not picker.count() or not picker.is_visible():
            raise AssertionError("Year picker wrapper was not found")
        return picker

    def _select(self, locator: "Locator", value: Any) -> Any:
        try:
            if locator.evaluate("el => el.tagName.toLowerCase()") == "select":
                locator.select_option(label=str(value)) if value not in (None, [], "") else locator.select_option(index=1)
                return value
        except Exception:
            pass
        click_target = locator
        try:
            wrapper = locator.locator(
                "xpath=ancestor::div[contains(concat(' ',normalize-space(@class),' '),' el-select ')][1]"
                "//div[contains(concat(' ',normalize-space(@class),' '),' el-select__wrapper ')]"
            ).first
            if wrapper.count() and wrapper.is_visible():
                click_target = wrapper
        except Exception:
            pass
        click_target.click(force=True)
        self.page.wait_for_timeout(250)
        try:
            if value not in (None, [], "") and locator.is_editable():
                locator.fill(str(value))
                self.page.wait_for_timeout(700)
        except Exception:
            pass
        try:
            locator.press("ArrowDown")
            self.page.wait_for_timeout(800)
            locator.press("Enter")
            self.page.wait_for_timeout(350)
            selected = locator.input_value().strip()
            if selected:
                return selected
        except Exception:
            pass
        if value not in (None, [], ""):
            option = self.page.get_by_text(str(value), exact=True).last
            if option.count() and option.is_visible():
                option.click(force=True)
                return value
        try:
            controls_id = locator.get_attribute("aria-controls") or ""
            if controls_id:
                owned_options = self.page.locator(
                    f'#{controls_id} .el-select-dropdown__item:not(.is-disabled),'
                    f'#{controls_id} [role="option"]:not([aria-disabled="true"])'
                )
                for index in range(owned_options.count()):
                    option = owned_options.nth(index)
                    text = (option.inner_text() or "").strip()
                    if option.is_visible() and text:
                        option.click(force=True)
                        return text
        except Exception:
            pass
        options = self.page.locator(
            ".el-popper:visible .el-select-dropdown__item:not(.is-disabled),"
            ".el-select-dropdown:visible .el-select-dropdown__item:not(.is-disabled),"
            ".ant-select-dropdown:visible .ant-select-item-option:not(.ant-select-item-option-disabled),"
            "[role=listbox]:visible [role=option]:not([aria-disabled=true]),"
            ".el-popper:visible .el-cascader-node:not(.is-disabled),"
            ".el-popper:visible .el-tree-node__content,"
            ".el-popover:visible .el-table__row,"
            ".el-dialog:visible .el-table__row"
        )
        for index in range(options.count()):
            option = options.nth(index)
            text = (option.inner_text() or "").strip()
            if option.is_visible() and text and not any(word in text for word in ("请选择", "暂无", "无数据", "加载")):
                option.click(force=True)
                self.page.wait_for_timeout(350)
                confirm = self.page.locator(
                    ".el-dialog:visible button:has-text('确定'),.el-popover:visible button:has-text('确定')"
                ).last
                if confirm.count() and confirm.is_visible():
                    confirm.click(force=True)
                return text
        raise AssertionError("No enabled option found")

    @staticmethod
    def _check(locator: "Locator") -> None:
        try:
            locator.check(force=True)
        except Exception:
            locator.click(force=True)
