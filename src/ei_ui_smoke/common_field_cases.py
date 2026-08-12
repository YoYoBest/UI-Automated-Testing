from __future__ import annotations

import json
import hashlib
import posixpath
import re
import zipfile
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from .contracts import field_kind
from .dom import is_semantic_numeric_field
from .models import DomField, FieldDefinition


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


@dataclass(frozen=True, slots=True)
class CommonFieldRule:
    case_id: str
    field_type: str
    scenario: str
    input_spec: Any
    expected_type: str
    expected_value: str = ""
    priority: str = "P1"
    source_row: int = 0
    required_max_length: int | None = None
    required_layout: str = ""
    scenario_code: str = ""


@dataclass(frozen=True, slots=True)
class FieldConstraints:
    required: bool = False
    max_length: int | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None
    precision: int | None = None
    max_digits: int | None = None
    step: Decimal | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredCommonField:
    field_key: str
    label: str
    field_type: str
    kind: str
    selector: str
    constraints: FieldConstraints
    readonly: bool = False
    source: str = "dom"
    layout_profile: str = ""
    branch_conditions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class BoundCommonCase:
    case_id: str
    field_key: str
    field_label: str
    field_type: str
    selector: str
    scenario: str
    input_value: Any
    expected_type: str
    expected_value: str
    priority: str
    source_row: int = 0
    scenario_code: str = ""
    branch_conditions: tuple[tuple[str, str], ...] = ()
    constraints: FieldConstraints = FieldConstraints()

    @property
    def pytest_id(self) -> str:
        branch_suffix = _branch_pytest_suffix(self.branch_conditions)
        return f"{self.field_key}{branch_suffix}-{self.case_id}"


@dataclass(frozen=True, slots=True)
class BoundCommonTransaction:
    transaction_id: str
    cases: tuple[BoundCommonCase, ...]
    execution_mode: str = "persistence"

    @property
    def pytest_id(self) -> str:
        if len(self.cases) == 1:
            return self.cases[0].pytest_id
        case_ids = {case.case_id for case in self.cases}
        if len(case_ids) == 1 and all(
            case.field_type == "required" for case in self.cases
        ):
            return f"{self.cases[0].case_id}-all-required"
        return self.transaction_id


@dataclass(frozen=True, slots=True)
class BoundCommonReportItem:
    """One Allure item backed by a possibly shared physical transaction."""

    transaction: BoundCommonTransaction
    case_index: int

    @property
    def case(self) -> BoundCommonCase:
        return self.transaction.cases[self.case_index]

    @property
    def pytest_id(self) -> str:
        return self.case.pytest_id


MERGEABLE_PERSISTENCE_FIELD_TYPES = {
    "text", "textarea", "number",
}

REVERSIBLE_PROBE_FIELD_TYPES = {
    "text", "textarea", "number", "amount", "percentage",
}

REVERSIBLE_PROBE_SCENARIOS = re.compile(
    r"^(?:长度下边界|长度边界|最大长度边界|最大总长度边界|"
    r"合法整数|整数输入|合法.{0,8}位?小数|.{1,8}位小数|最小值|最大值)$"
)

TEXT_ENTRY_COMPATIBILITY_SCENARIOS = {
    "中英文及常用标点",
    "HTML/脚本字符",
    "修改值正确性检查",
}

REQUIRED_ERRORS_TRIGGER = "required_errors_trigger"
REQUIRED_ERRORS_RECOVER = "required_errors_recover"
REQUIRED_VALIDATION_SCENARIOS = {
    REQUIRED_ERRORS_TRIGGER,
    REQUIRED_ERRORS_RECOVER,
}


FIELD_TYPE_ALIASES = {
    "金额": "amount",
    "数字": "number",
    "百分比": "percentage",
    "比例": "percentage",
    "字符": "text",
    "文本": "text",
    "单行文本": "text",
    "整行文本": "text",
    "半行文本": "text",
    "半行文本框": "text",
    "输入框": "text",
    "文本域": "textarea",
    "业务字段文本域": "textarea",
    "手机": "phone",
    "手机号": "phone",
    "联系方式": "contact",
    "邮箱": "email",
    "日期": "date",
    "时间": "datetime",
    "年份": "year",
    "密码": "password",
    "身份证号": "id_card",
    "单选框": "radio",
    "单选下拉框": "select",
    "选择框": "select",
    "附件": "file",
    "必填字段": "required",
    "保存按钮": "save_command",
    "提交按钮": "submit_command",
    "取消按钮": "cancel_command",
    "取消/关闭按钮": "cancel_command",
    "关闭图标X": "close_command",
    "对话框名称": "dialog_title",
}

AMOUNT_LABEL = re.compile(r"金额|出资额|注册资本|实缴|认缴|预算|价格|单价|总价|额度|估值")
PERCENTAGE_LABEL = re.compile(r"百分比|比例|占比|利率|费率|税率|收益率")
PERCENTAGE_CODE = re.compile(r"(?:rate|ratio|percent|percentage)$", re.IGNORECASE)
PHONE_LABEL = re.compile(r"手机|手机号")
CONTACT_LABEL = re.compile(r"联系方式|联系电话|电话")
EMAIL_LABEL = re.compile(r"邮箱|电子邮件|email", re.I)
PASSWORD_LABEL = re.compile(r"密码")
ID_CARD_LABEL = re.compile(r"身份证")
ENABLED_APPLICABILITY_VALUES = {"是", "y", "yes", "true", "1", "启用", "执行"}


def load_common_field_rules(
    workbook_path: Path,
    sheet_name: str = "新增",
    case_ids: Iterable[str] | None = None,
) -> list[CommonFieldRule]:
    """Load executable type-level rules from the human-readable common-case sheet."""
    rows = read_xlsx_records(workbook_path, sheet_name)
    filter_by_case_id = case_ids is not None
    selected_case_ids = {_text(case_id) for case_id in case_ids or () if _text(case_id)}
    if filter_by_case_id and not selected_case_ids:
        raise ValueError(f"{sheet_name} 未选择任何用例编号")
    discovered_case_ids: set[str] = set()
    result: list[CommonFieldRule] = []
    seen: dict[tuple[str, str, int | None, str, str], int] = {}
    for row_number, row in enumerate(rows, start=2):
        case_id = _text(row.get("用例ID") or row.get("序号"))
        if case_id:
            discovered_case_ids.add(case_id)
        if filter_by_case_id and case_id not in selected_case_ids:
            continue
        raw_control = _text(row.get("字段/控件") or row.get("检查点"))
        control = _normalize_control(raw_control)
        field_type = FIELD_TYPE_ALIASES.get(control, "")
        if not case_id or not field_type:
            continue
        if not _row_enabled(row):
            continue
        scenario = _text(row.get("测试场景") or row.get("场景"))
        if not scenario:
            continue
        required_max_length = _control_length(raw_control)
        required_layout = _control_layout(raw_control)
        signature = (case_id, field_type, required_max_length, scenario, required_layout)
        if signature in seen:
            raise ValueError(
                f"Duplicate common case definition in {sheet_name}: {case_id} "
                f"(rows {seen[signature]} and {row_number})"
            )
        seen[signature] = row_number
        raw_input = row.get("测试数据")
        expected = _text(row.get("预期结果"))
        scenario_code = _text(row.get("场景编码") or row.get("自动化场景"))
        result.append(
            CommonFieldRule(
                case_id=case_id,
                field_type=field_type,
                scenario=scenario,
                input_spec=_normalize_input_spec(scenario, raw_input),
                expected_type=_expected_type(scenario, expected),
                expected_value=expected,
                priority=_text(row.get("优先级")) or "P1",
                source_row=row_number,
                required_max_length=required_max_length,
                required_layout=required_layout,
                scenario_code=scenario_code,
            )
        )
    missing = selected_case_ids - discovered_case_ids
    if missing:
        raise ValueError(
            f"Unknown case IDs in {sheet_name}: {', '.join(sorted(missing))}"
        )
    return result


def discover_common_fields(
    dom_fields: Iterable[DomField],
    definitions: Iterable[FieldDefinition] = (),
) -> list[DiscoveredCommonField]:
    definitions_list = list(definitions)
    by_code = {field.field_code: field for field in definitions_list if field.field_code}
    result: list[DiscoveredCommonField] = []
    seen: set[str] = set()
    for dom in dom_fields:
        definition = by_code.get(dom.field_code) or _definition_by_label(definitions_list, dom.label)
        field_key = (definition.field_code if definition else dom.field_code).strip()
        if not field_key or field_key.startswith("el-id-") or field_key in seen:
            continue
        seen.add(field_key)
        label = (definition.field_name if definition and definition.field_name else dom.label).strip()
        kind = _effective_common_kind(dom, definition, field_key, label)
        common_type = classify_common_field_type(label, kind, field_key)
        if not common_type:
            continue
        result.append(
            DiscoveredCommonField(
                field_key=field_key,
                label=label or field_key,
                field_type=common_type,
                kind=kind,
                selector=dom.selector,
                constraints=_constraints(dom, definition, common_type),
                readonly=bool(dom.readonly or (definition and (definition.readonly or definition.locked))),
                source=definition.source if definition else "dom",
                layout_profile=dom.layout_profile,
            )
        )
    return result


def _effective_common_kind(
    dom: DomField,
    definition: FieldDefinition | None,
    field_key: str,
    label: str,
) -> str:
    """Merge source metadata with the actual rendered control kind.

    Source definitions often describe semantic numeric inputs as ElInput-TEXT.
    They may enrich labels and constraints, but must not downgrade a runtime or
    stable-code semantic number such as buildPeriodMonth back to plain text.
    """

    dom_kind = dom.kind or "unknown"
    source_kind = field_kind(definition.field_type) if definition else "unknown"
    effective_label = label or dom.label
    if is_semantic_numeric_field(field_key, effective_label):
        if dom_kind == "text":
            dom_kind = "number"
        if source_kind == "text":
            source_kind = "number"
    if dom_kind == source_kind:
        return dom_kind
    if dom_kind == "unknown":
        return source_kind
    if source_kind == "unknown":
        return dom_kind
    if dom_kind == "text":
        return source_kind
    if source_kind == "text":
        return dom_kind
    return dom_kind


def classify_common_field_type(label: str, kind: str, field_code: str = "") -> str:
    normalized = re.sub(r"\s+", "", label or "")
    if kind in {"select", "multi_select", "radio", "checkbox", "file"}:
        return kind
    if kind == "textarea":
        return "textarea"
    if kind in {"number", "text"}:
        if PERCENTAGE_LABEL.search(normalized) or PERCENTAGE_CODE.search(field_code or ""):
            return "percentage"
        if AMOUNT_LABEL.search(normalized):
            return "amount"
    if kind == "number":
        return "number"
    if kind == "date":
        return "date"
    if kind == "year":
        return "year"
    if kind == "datetime":
        return "datetime"
    if kind != "text":
        return ""
    if PHONE_LABEL.search(normalized):
        return "phone"
    if EMAIL_LABEL.search(normalized):
        return "email"
    if CONTACT_LABEL.search(normalized):
        return "contact"
    if PASSWORD_LABEL.search(normalized):
        return "password"
    if ID_CARD_LABEL.search(normalized):
        return "id_card"
    return "text"


def bind_common_rules(
    fields: Iterable[DiscoveredCommonField],
    rules: Iterable[CommonFieldRule],
) -> list[BoundCommonCase]:
    rules_by_type: dict[str, list[CommonFieldRule]] = {}
    for rule in rules:
        rules_by_type.setdefault(rule.field_type, []).append(rule)
    result: list[BoundCommonCase] = []
    for field in fields:
        if field.readonly:
            continue
        applicable_rules = [*rules_by_type.get(field.field_type, [])]
        # Commands and dialog metadata use reserved keys. They are executable only
        # through an explicit matching workbook control rule, never as form fields.
        if _is_synthetic_common_field(field):
            applicable_rules = [*rules_by_type.get(field.field_type, [])]
        elif field.field_type == "textarea":
            applicable_rules.extend(
                rule
                for rule in rules_by_type.get("text", [])
                if rule.scenario in TEXT_ENTRY_COMPATIBILITY_SCENARIOS
            )
        if not _is_synthetic_common_field(field) and field.constraints.required:
            applicable_rules.extend(rules_by_type.get("required", []))
        for rule in applicable_rules:
            if _command_rule_requires_confirmation(rule) and not _command_field_supports_confirmation(field):
                continue
            if "清空非必填值" in rule.scenario and field.constraints.required:
                continue
            if (
                rule.required_max_length is not None
                and field.constraints.max_length != rule.required_max_length
            ):
                continue
            if rule.required_layout and field.layout_profile != rule.required_layout:
                continue
            if _requires_proven_integer_control(rule) and not _has_proven_integer_control(field):
                continue
            if rule.field_type == "required":
                if "仅输入空格" in rule.scenario and field.kind not in {"text", "textarea", "number"}:
                    continue
                if "输入后清空" in rule.scenario and field.kind == "radio":
                    continue
            value, applicable = resolve_rule_value(rule.input_spec, field.constraints)
            if not applicable:
                continue
            result.append(
                BoundCommonCase(
                    case_id=rule.case_id,
                    field_key=field.field_key,
                    field_label=field.label,
                    field_type=("required" if rule.field_type == "required" else field.field_type),
                    selector=field.selector,
                    scenario=rule.scenario,
                    input_value=value,
                    expected_type=rule.expected_type,
                    expected_value=rule.expected_value,
                    priority=rule.priority,
                    source_row=rule.source_row,
                    scenario_code=rule.scenario_code,
                    branch_conditions=field.branch_conditions,
                    constraints=field.constraints,
                )
            )
    pytest_ids: dict[str, int] = {}
    for case in result:
        previous_row = pytest_ids.get(case.pytest_id)
        if previous_row is not None:
            raise ValueError(
                f"Ambiguous common case binding: {case.pytest_id} "
                f"(rows {previous_row} and {case.source_row})"
            )
        pytest_ids[case.pytest_id] = case.source_row
    return result


def _is_synthetic_common_field(field: DiscoveredCommonField) -> bool:
    """Return whether a discovery-only command or metadata entry is internal."""
    return field.field_key.startswith("__")


def plan_common_case_transactions(
    cases: Iterable[BoundCommonCase],
) -> list[BoundCommonTransaction]:
    """Plan reusable form sessions without assigning two values to one field."""
    cases = list(cases)
    groups: list[list[BoundCommonCase]] = []
    group_modes: list[str] = []
    mergeable_group_indexes: dict[tuple[Any, ...], list[int]] = {}
    attachment_group_indexes: dict[tuple[Any, ...], list[int]] = {}
    required_flow_indexes: dict[tuple[tuple[str, str], ...], int] = {}
    probe_group_indexes: dict[tuple[Any, ...], int] = {}

    def new_group(mode: str) -> int:
        index = len(groups)
        groups.append([])
        group_modes.append(mode)
        return index

    for case in cases:
        branch_key = branch_condition_key(case.branch_conditions)
        if _is_reversible_probe_case(case):
            # A probe shares a physical form only when every control accepts the
            # same boundary semantics.  In particular, numeric maxlength counts
            # the decimal separator while max_digits does not.
            probe_key = (
                _transaction_field_family(case),
                _transaction_scenario_family(case),
                _constraint_fingerprint(case.constraints),
                branch_key,
            )
            probe_group_index = probe_group_indexes.get(probe_key)
            if probe_group_index is None:
                probe_group_index = new_group("probe_persistence")
                probe_group_indexes[probe_key] = probe_group_index
            groups[probe_group_index].append(case)
            continue
        if required_validation_scenario(case):
            required_flow_index = required_flow_indexes.get(branch_key)
            if required_flow_index is None:
                required_flow_index = new_group("required_validation")
                required_flow_indexes[branch_key] = required_flow_index
            groups[required_flow_index].append(case)
            continue
        accepted_persistence = (
            case.expected_type in {"accepted", "safe_handling"}
            and (
                "拒绝" not in case.expected_value
                or case.scenario in TEXT_ENTRY_COMPATIBILITY_SCENARIOS
            )
        )
        field_validation = case.expected_type == "field_error"
        if _is_mergeable_attachment_case(case):
            # Attachment mutation is expensive because upload, Save, and edit
            # readback each have asynchronous lifecycle work.  Fields in the
            # same scenario/branch can share one physical Save, while report
            # items remain indexed per workbook binding.
            merge_key = (
                "attachment",
                _transaction_scenario_family(case),
                case.expected_type,
                branch_key,
            )
            candidate_indexes = attachment_group_indexes.setdefault(merge_key, [])
            target_index = next(
                (
                    index
                    for index in candidate_indexes
                    if all(existing.field_key != case.field_key for existing in groups[index])
                ),
                None,
            )
            if target_index is None:
                target_index = new_group("attachment_persistence")
                candidate_indexes.append(target_index)
            groups[target_index].append(case)
            continue
        if (
            case.field_type in MERGEABLE_PERSISTENCE_FIELD_TYPES
            and (accepted_persistence or field_validation)
        ):
            # Positive values for distinct fields can share one physical Save,
            # but only when they come from the same scenario family. Mixing
            # unrelated accepted rules makes one field's validation failure
            # appear as failures for the other report items in the transaction.
            # Negative values can share one physical Save only within the same
            # scenario family and compatible rendered field family, preserving
            # field-level attribution while avoiding one form per field.
            merge_key = (
                (
                    "accepted",
                    _transaction_field_family(case),
                    _transaction_scenario_family(case),
                    case.expected_type,
                    branch_key,
                )
                if accepted_persistence
                else (
                    "field_validation",
                    _transaction_field_family(case),
                    _transaction_scenario_family(case),
                    case.expected_type,
                    branch_key,
                )
            )
            candidate_indexes = mergeable_group_indexes.setdefault(merge_key, [])
            target_index = next(
                (
                    index
                    for index in candidate_indexes
                    if all(existing.field_key != case.field_key for existing in groups[index])
                ),
                None,
            )
            if target_index is None:
                target_index = new_group("persistence")
                candidate_indexes.append(target_index)
            groups[target_index].append(case)
        else:
            target_index = new_group("persistence")
            groups[target_index].append(case)
    for required_flow_index in required_flow_indexes.values():
        flow = groups[required_flow_index]
        groups[required_flow_index] = [
            *(
                case for case in flow
                if required_validation_scenario(case) == REQUIRED_ERRORS_TRIGGER
            ),
            *(
                case for case in flow
                if required_validation_scenario(case) == REQUIRED_ERRORS_RECOVER
            ),
        ]
    return [
        BoundCommonTransaction(
            f"TX-{index:03d}", tuple(group), group_modes[index - 1]
        )
        for index, group in enumerate(groups, 1)
    ]


def _is_reversible_probe_case(case: BoundCommonCase) -> bool:
    """Use one final representative Save for reversible boundary inputs."""
    scenario = re.sub(r"\s+", "", _text(case.scenario))
    return bool(
        case.expected_type == "accepted"
        and case.field_type in REVERSIBLE_PROBE_FIELD_TYPES
        and REVERSIBLE_PROBE_SCENARIOS.fullmatch(scenario)
    )


def _is_mergeable_attachment_case(case: BoundCommonCase) -> bool:
    """Return whether an attachment case may share one upload/save lifecycle."""
    return (
        case.field_type == "file"
        and case.expected_type in {"accepted", "safe_handling"}
        and "拒绝" not in case.expected_value
    )


def _transaction_field_family(case: BoundCommonCase) -> str:
    if case.field_type in {"text", "textarea"}:
        return "textual"
    return case.field_type


def _constraint_fingerprint(constraints: FieldConstraints) -> tuple[Any, ...]:
    """Return the input constraints that determine a reversible probe value."""
    return (
        constraints.max_length,
        str(constraints.minimum) if constraints.minimum is not None else None,
        str(constraints.maximum) if constraints.maximum is not None else None,
        constraints.precision,
        constraints.max_digits,
        str(constraints.step) if constraints.step is not None else None,
    )


def _transaction_scenario_family(case: BoundCommonCase) -> str:
    """Normalize workbook wording into a stable transaction grouping family."""
    if case.scenario_code:
        return f"code:{case.scenario_code}"
    scenario = re.sub(r"\s+", "", _text(case.scenario))
    if not scenario:
        return ""
    if "HTML" in scenario or "脚本" in scenario:
        return "text:html-script"
    if "中英文" in scenario and "标点" in scenario:
        return "text:common-punctuation"
    if "换行" in scenario and ("空格" in scenario or "缩进" in scenario):
        return "text:whitespace-format"
    if "前后空格" in scenario:
        return "text:edge-spaces"
    if "长度下边界" in scenario:
        return "length:lower-boundary"
    if "超过长度" in scenario:
        return "length:over-boundary"
    if "长度边界" in scenario:
        return "length:boundary"
    return scenario


def expand_common_case_report_items(
    transactions: Iterable[BoundCommonTransaction],
) -> list[BoundCommonReportItem]:
    """Expand every logical field for reporting without adding executions."""
    result: list[BoundCommonReportItem] = []
    for transaction in transactions:
        result.extend(
            BoundCommonReportItem(transaction, index)
            for index in range(len(transaction.cases))
        )
    return result


def required_validation_scenario(case: BoundCommonCase) -> str:
    """Resolve required-field workflow semantics without depending on case IDs."""
    if case.field_type != "required":
        return ""
    if case.scenario_code in REQUIRED_VALIDATION_SCENARIOS:
        return case.scenario_code
    if case.input_value == "" and "空值" in case.scenario:
        return REQUIRED_ERRORS_TRIGGER
    if "提示恢复" in case.scenario or (
        "必填提示" in case.scenario
        and any(word in case.scenario for word in ("消除", "消失"))
    ):
        return REQUIRED_ERRORS_RECOVER
    return ""


def branch_condition_key(
    branch_conditions: Iterable[tuple[Any, Any]] | None,
) -> tuple[tuple[str, str], ...]:
    """Return a stable, immutable identity for mutually exclusive form branches."""

    result: list[tuple[str, str]] = []
    for raw_key, raw_value in branch_conditions or ():
        key = _text(raw_key)
        value = _text(raw_value)
        if key and value:
            result.append((key, value))
    return tuple(result)


def _branch_pytest_suffix(
    branch_conditions: Iterable[tuple[Any, Any]] | None,
) -> str:
    key = branch_condition_key(branch_conditions)
    if not key:
        return ""
    raw = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    readable = "_".join(f"{field}={value}" for field, value in key)
    safe = re.sub(r"[^\w\u4e00-\u9fff=.-]+", "_", readable).strip("_")
    if len(safe) > 48:
        safe = safe[:48].rstrip("_")
    return f"@{safe or 'branch'}-{digest}"


def _branch_conditions_from_raw(raw: Any) -> tuple[tuple[str, str], ...]:
    if isinstance(raw, dict):
        return branch_condition_key(raw.items())
    if not isinstance(raw, list | tuple):
        return ()
    pairs: list[tuple[Any, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            pairs.extend(item.items())
        elif isinstance(item, list | tuple) and len(item) >= 2:
            pairs.append((item[0], item[1]))
    return branch_condition_key(pairs)


def resolve_rule_value(spec: Any, constraints: FieldConstraints) -> tuple[Any, bool]:
    if not isinstance(spec, str) or not spec.startswith("__"):
        return spec, True
    if spec == "__EMPTY__":
        return "", constraints.required
    if spec == "__SPACE__":
        return " ", constraints.required
    if spec == "__REPLACE_LAST_WITH_9__":
        return spec, True
    if spec in {"__MAX_LENGTH_MINUS_1__", "__MAX_LENGTH__", "__MAX_LENGTH_PLUS_1__"}:
        if constraints.max_length is None:
            return None, False
        delta = {"__MAX_LENGTH_MINUS_1__": -1, "__MAX_LENGTH__": 0, "__MAX_LENGTH_PLUS_1__": 1}[spec]
        return "测" * max(0, constraints.max_length + delta), True
    if spec in {"__MAX_DIGITS__", "__MAX_DIGITS_PLUS_1__"}:
        if constraints.max_digits is None:
            return None, False
        digits = constraints.max_digits + (1 if spec == "__MAX_DIGITS_PLUS_1__" else 0)
        if constraints.precision:
            # max_digits counts only digits, whereas maxlength counts every
            # rendered character, including the decimal separator.  A value that
            # violates maxlength is truncated by the control before the intended
            # max_digits assertion can be made, so accepted boundaries use their
            # strict intersection.
            integer_digits = max(1, digits - constraints.precision)
            if spec == "__MAX_DIGITS__" and constraints.max_length is not None:
                integer_digits = min(
                    integer_digits,
                    max(1, constraints.max_length - constraints.precision - 1),
                )
            return f"{'9' * integer_digits}.{'9' * constraints.precision}", True
        return "9" * digits, True
    if spec == "__MIN_VALUE__":
        return _number_value(constraints.minimum), constraints.minimum is not None
    if spec == "__MAX_VALUE__":
        return _number_value(constraints.maximum), constraints.maximum is not None
    if spec in {"__BELOW_MIN__", "__ABOVE_MAX__"}:
        boundary = constraints.minimum if spec == "__BELOW_MIN__" else constraints.maximum
        if boundary is None:
            return None, False
        step = Decimal(1).scaleb(-(constraints.precision or 0))
        value = boundary - step if spec == "__BELOW_MIN__" else boundary + step
        return _number_value(value), True
    if spec == "__OVER_PRECISION__":
        if constraints.precision is None:
            return None, False
        return f"1.{('1' * constraints.precision)}1", True
    if spec.startswith("__REPEAT__:"):
        _, char, length = spec.split(":", 2)
        return char * int(length), True
    return None, False


def save_field_manifest(path: Path, fields: Iterable[DiscoveredCommonField]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for field in fields:
        item = asdict(field)
        constraints = item["constraints"]
        for key in ("minimum", "maximum", "step"):
            if constraints[key] is not None:
                constraints[key] = str(constraints[key])
        payload.append(item)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_field_manifest(path: Path) -> list[DiscoveredCommonField]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    result: list[DiscoveredCommonField] = []
    for raw in data:
        values = dict(raw.get("constraints") or {})
        values["minimum"] = _decimal(values.get("minimum"))
        values["maximum"] = _decimal(values.get("maximum"))
        values["step"] = _decimal(values.get("step"))
        field_key = str(raw["field_key"])
        label = str(raw.get("label") or field_key)
        kind = str(raw.get("kind") or "text")
        field_type = str(raw["field_type"])
        if not field_key.startswith("__"):
            if kind == "text" and field_type == "text" and is_semantic_numeric_field(
                field_key, label
            ):
                kind = "number"
            field_type = classify_common_field_type(label, kind, field_key) or field_type
        result.append(
            DiscoveredCommonField(
                field_key=field_key,
                label=label,
                field_type=field_type,
                kind=kind,
                selector=str(raw.get("selector") or ""),
                constraints=FieldConstraints(**values),
                readonly=bool(raw.get("readonly")),
                source=str(raw.get("source") or "manifest"),
                layout_profile=str(raw.get("layout_profile") or ""),
                branch_conditions=_branch_conditions_from_raw(
                    raw.get("branch_conditions")
                ),
            )
        )
    return result


def discover_page_common_fields(
    page,
    definitions: Iterable[FieldDefinition] = (),
    *,
    root=None,
    manifest_path: Path | None = None,
) -> list[DiscoveredCommonField]:
    """Scan an opened form and optionally persist collection-time field metadata."""
    from .dom import scan_dom_fields

    fields = discover_common_fields(scan_dom_fields(page, root), definitions)
    if manifest_path is not None:
        save_field_manifest(manifest_path, fields)
    return fields


def load_bound_common_cases(
    workbook_path: Path,
    manifest_path: Path,
    *,
    sheet_name: str = "新增",
    case_ids: Iterable[str] | None = None,
) -> list[BoundCommonCase]:
    """Build pytest-ready parameters from a discovery manifest and Excel rules."""
    return bind_common_rules(
        load_field_manifest(manifest_path),
        load_common_field_rules(workbook_path, sheet_name, case_ids),
    )


def plan_common_field_report_items(
    workbook_path: Path,
    manifest_path: Path,
    *,
    sheet_name: str = "新增",
    case_ids: Iterable[str] | None = None,
) -> list[BoundCommonReportItem]:
    """Build the exact parametrized report items from one fresh manifest."""
    cases = load_bound_common_cases(
        workbook_path, manifest_path, sheet_name=sheet_name, case_ids=case_ids,
    )
    return expand_common_case_report_items(plan_common_case_transactions(cases))


def count_common_field_report_items(
    workbook_path: Path,
    manifest_path: Path,
    *,
    sheet_name: str = "新增",
    case_ids: Iterable[str] | None = None,
) -> int:
    """Return the pytest item count, including its one-item empty-plan skip."""
    return max(1, len(plan_common_field_report_items(
        workbook_path, manifest_path, sheet_name=sheet_name, case_ids=case_ids,
    )))


def build_common_case_coverage(
    workbook_path: Path,
    manifest_path: Path,
    sheet_name: str = "新增",
    case_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    filter_by_case_id = case_ids is not None
    selected_case_ids = {_text(case_id) for case_id in case_ids or () if _text(case_id)}
    rows = read_xlsx_records(workbook_path, sheet_name)
    rules = load_common_field_rules(
        workbook_path,
        sheet_name,
        selected_case_ids if filter_by_case_id else None,
    )
    fields = load_field_manifest(manifest_path)
    bound = bind_common_rules(fields, rules)
    transactions = plan_common_case_transactions(bound)
    bindings: dict[int, list[str]] = {}
    transaction_bindings: dict[int, list[str]] = {}
    for case in bound:
        bindings.setdefault(case.source_row, []).append(_case_field_identity(case))
    for transaction in transactions:
        for case in transaction.cases:
            transaction_bindings.setdefault(case.source_row, []).append(
                transaction.transaction_id
            )
    recognized = {rule.source_row: rule for rule in rules}
    items = []
    for row_number, row in enumerate(rows, start=2):
        case_id = _text(row.get("用例ID") or row.get("序号"))
        if filter_by_case_id and case_id not in selected_case_ids:
            continue
        control = _text(row.get("字段/控件") or row.get("检查点"))
        scenario = _text(row.get("测试场景") or row.get("场景"))
        if row_number in bindings:
            status = "executed"
            reason = f"bound to {', '.join(bindings[row_number])}"
        elif not _row_enabled(row):
            status = "not_applicable"
            reason = f"case applicability is {_text(row.get('是否适用')) or 'not enabled'}"
        elif row_number in recognized:
            status = "not_applicable"
            rule = recognized[row_number]
            if _command_rule_requires_confirmation(rule):
                candidates = [
                    field for field in fields
                    if field.field_type == rule.field_type and not field.readonly
                ]
                if candidates and not any(
                    _command_field_supports_confirmation(field) for field in candidates
                ):
                    reason = (
                        f"rendered {rule.field_type} command has no secondary "
                        "confirmation capability"
                    )
                else:
                    reason = f"no rendered {rule.field_type} field"
            else:
                reason = f"no rendered {rule.field_type} field"
        else:
            status = "unsupported"
            reason = f"no executor for control type: {control or 'unknown'}"
        items.append({
            "case_id": case_id,
            "control": control,
            "scenario": scenario,
            "source_row": row_number,
            "status": status,
            "reason": reason,
            "bound_fields": bindings.get(row_number, []),
            "transaction_ids": list(dict.fromkeys(transaction_bindings.get(row_number, []))),
        })
    counts = {
        status: sum(item["status"] == status for item in items)
        for status in ("executed", "not_applicable", "unsupported")
    }
    return {
        "sheet": sheet_name,
        "template_cases": len(items),
        "bound_instances": len(bound),
        "transaction_count": len(transactions),
        "counts": counts,
        "items": items,
    }


def _case_field_identity(case: BoundCommonCase) -> str:
    if not case.branch_conditions:
        return case.field_key
    conditions = " & ".join(
        f"{field}={value}" for field, value in branch_condition_key(case.branch_conditions)
    )
    return f"{case.field_key} [{conditions}]"


def save_common_case_coverage(path: Path, coverage: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def common_case_coverage_path(
    manifest_path: Path,
    sheet_name: str,
    case_ids: Iterable[str] | None,
) -> Path:
    safe_sheet = re.sub(r'[^\w\u4e00-\u9fff-]+', "_", sheet_name).strip("_") or "sheet"
    identity = json.dumps(list(case_ids or ()), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return manifest_path.with_name(
        f"{manifest_path.stem}_{safe_sheet}_{digest}_coverage.json"
    )


def read_xlsx_records(path: Path, sheet_name: str) -> list[dict[str, Any]]:
    """Read a simple rectangular XLSX sheet using OOXML and the standard library."""
    with zipfile.ZipFile(path) as archive:
        worksheet_path = _worksheet_path(archive, sheet_name)
        shared = _shared_strings(archive)
        root = ET.fromstring(archive.read(worksheet_path))
    rows: list[list[Any]] = []
    for row in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
        values: dict[int, Any] = {}
        for cell in row.findall(f"{{{MAIN_NS}}}c"):
            reference = cell.attrib.get("r", "")
            index = _column_index(reference)
            values[index] = _cell_value(cell, shared)
        if values:
            rows.append([values.get(index) for index in range(max(values) + 1)])
    if not rows:
        return []
    headers = [_text(value) for value in rows[0]]
    return [
        {header: row[index] if index < len(row) else None for index, header in enumerate(headers) if header}
        for row in rows[1:]
        if any(value not in (None, "") for value in row)
    ]


def list_xlsx_sheets(path: Path) -> list[str]:
    """Return worksheet names in workbook order without loading cell data."""
    try:
        with zipfile.ZipFile(path) as archive:
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    except FileNotFoundError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise ValueError(f"无法读取 Excel 工作簿：{path}") from exc
    sheets = [
        sheet.attrib["name"]
        for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet")
        if sheet.attrib.get("name")
    ]
    if not sheets:
        raise ValueError(f"Excel 工作簿中没有可用页签：{path}")
    return sheets


def list_xlsx_case_ids(path: Path) -> list[tuple[str, str]]:
    """Return unique (sheet, case ID) pairs in workbook order."""
    result: list[tuple[str, str]] = []
    for sheet_name in list_xlsx_sheets(path):
        for row in read_xlsx_records(path, sheet_name):
            case_id = _text(row.get("用例ID") or row.get("序号"))
            if not case_id:
                continue
            reference = (sheet_name, case_id)
            if reference not in result:
                result.append(reference)
    if not result:
        raise ValueError(f"Excel 工作簿中没有‘用例ID/序号’：{path}")
    return result


def case_selection_label(sheet_name: str, case_id: str) -> str:
    return f"{case_id}（{sheet_name}）"


def group_case_selections_by_sheet(
    references: Iterable[tuple[str, str]],
    selected_labels: Iterable[str],
) -> list[tuple[str, list[str]]]:
    """Resolve selected UI labels to sheets while preserving workbook order."""
    selected = {_text(label) for label in selected_labels if _text(label)}
    grouped: dict[str, list[str]] = {}
    found: set[str] = set()
    for sheet_name, case_id in references:
        label = case_selection_label(sheet_name, case_id)
        if label not in selected:
            continue
        found.add(label)
        grouped.setdefault(sheet_name, []).append(case_id)
    missing = selected - found
    if missing:
        raise ValueError(f"未找到用例编号选择项：{', '.join(sorted(missing))}")
    return list(grouped.items())


def _worksheet_path(archive: zipfile.ZipFile, sheet_name: str) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relation_id = ""
    for sheet in workbook.findall(f".//{{{MAIN_NS}}}sheet"):
        if sheet.attrib.get("name") == sheet_name:
            relation_id = sheet.attrib.get(f"{{{REL_NS}}}id", "")
            break
    if not relation_id:
        raise KeyError(f"Worksheet not found: {sheet_name}")
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    for relation in rels.findall(f"{{{PKG_REL_NS}}}Relationship"):
        if relation.attrib.get("Id") == relation_id:
            target = relation.attrib["Target"].replace("\\", "/")
            if target.startswith("/"):
                return target.lstrip("/")
            if target.startswith("xl/"):
                return target
            return posixpath.normpath(posixpath.join("xl", target))
    raise KeyError(f"Worksheet relationship not found: {sheet_name}")


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")) for item in root]


def _cell_value(cell: ET.Element, shared: list[str]) -> Any:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
    value_node = cell.find(f"{{{MAIN_NS}}}v")
    if value_node is None:
        return None
    value = value_node.text or ""
    if cell_type == "s":
        return shared[int(value)]
    if cell_type == "b":
        return value == "1"
    if cell_type in {"str", "e"}:
        return value
    try:
        number = Decimal(value)
        return int(number) if number == number.to_integral() else float(number)
    except Exception:
        return value


def _column_index(reference: str) -> int:
    letters = re.match(r"[A-Z]+", reference.upper())
    result = 0
    for character in letters.group(0) if letters else "A":
        result = result * 26 + ord(character) - 64
    return result - 1


def _definition_by_label(
    definitions: Iterable[FieldDefinition], label: str
) -> FieldDefinition | None:
    normalized = re.sub(r"[\s:*：]", "", label or "")
    return next(
        (
            field
            for field in definitions
            if re.sub(r"[\s:*：]", "", field.field_name or "") == normalized
        ),
        None,
    )


def _constraints(
    dom: DomField,
    definition: FieldDefinition | None,
    common_type: str,
) -> FieldConstraints:
    props = definition.props if definition else {}
    required = bool(dom.required or (definition and definition.required))
    max_length = dom.maxlength or _integer(props.get("maxlength") or props.get("maxLength"))
    minimum = _decimal(dom.minimum if dom.minimum not in (None, "") else props.get("min"))
    maximum = _decimal(dom.maximum if dom.maximum not in (None, "") else props.get("max"))
    step = _decimal(dom.step if dom.step not in (None, "") else props.get("step"))
    precision = _integer(props.get("precision"))
    max_digits = _integer(props.get("maxDigits") or props.get("max_digits"))
    if common_type == "percentage":
        # Percentage semantics identify the field family, not its numeric range.
        # Bind boundary cases only to constraints proven by DOM/source metadata.
        pass
    elif common_type == "amount":
        minimum = Decimal("0") if minimum is None else minimum
        precision = 6 if precision is None else precision
        max_digits = 20 if max_digits is None else max_digits
    return FieldConstraints(required, max_length, minimum, maximum, precision, max_digits, step)


def _normalize_control(value: str) -> str:
    value = re.sub(r"[（(]\d+字[）)]", "", value)
    value = value.replace("字段合法性校验", "")
    return re.sub(r"\s+", "", value).strip()


def _control_length(value: str) -> int | None:
    match = re.search(r"[（(](\d+)字[）)]", value)
    return int(match.group(1)) if match else None


def _control_layout(value: str) -> str:
    normalized = re.sub(r"\s+", "", value)
    if "半行" in normalized:
        return "half"
    if "整行" in normalized:
        return "full"
    return ""


def _normalize_input_spec(scenario: str, value: Any) -> Any:
    if "修改值正确性检查" in scenario:
        return "__REPLACE_LAST_WITH_9__"
    if "空值" in scenario or "空值提交" in scenario or "输入后清空" in scenario:
        return "__EMPTY__"
    if "仅输入空格" in scenario:
        return "__SPACE__"
    if "长度下边界" in scenario:
        return "__MAX_LENGTH_MINUS_1__"
    if "超过总长度" in scenario:
        return "__MAX_DIGITS_PLUS_1__"
    if "最大总长度边界" in scenario:
        return "__MAX_DIGITS__"
    if "超过长度" in scenario:
        return "__MAX_LENGTH_PLUS_1__"
    if "长度边界" in scenario or "最大长度边界" in scenario:
        return "__MAX_LENGTH__"
    if "低于最小值" in scenario:
        return "__BELOW_MIN__"
    if "超过最大值" in scenario:
        return "__ABOVE_MAX__"
    if scenario == "最小值":
        return "__MIN_VALUE__"
    if scenario == "最大值":
        return "__MAX_VALUE__"
    if "超过小数" in scenario or "超过九位小数" in scenario:
        return "__OVER_PRECISION__"
    if "前导零" in scenario:
        return "001"
    text = _text(value)
    if not text:
        return None
    if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        number = Decimal(text)
        return int(number) if number == number.to_integral() else float(number)
    length = re.fullmatch(r"(\d+)\s*个字符", text)
    if length:
        return f"__REPEAT__:测:{length.group(1)}"
    return text


def _expected_type(scenario: str, expected: str) -> str:
    text = f"{scenario} {expected}"
    if (
        ("HTML" in scenario or "脚本" in scenario)
        and "不执行脚本" in expected
        and ("安全转义" in expected or "拒绝" in expected)
    ):
        return "safe_handling"
    if any(word in text for word in ("超过", "非法", "不足", "缺少", "负数", "空值", "格式错误")):
        return "field_error"
    if any(word in expected for word in ("被阻止", "不允许", "提示", "不可录入")):
        return "field_error"
    return "accepted"


def _requires_proven_integer_control(rule: CommonFieldRule) -> bool:
    scenario = re.sub(r"\s+", "", rule.scenario or "")
    return rule.field_type == "number" and "小数" in scenario and "整数框" in scenario


def _has_proven_integer_control(field: DiscoveredCommonField) -> bool:
    constraints = field.constraints
    return constraints.precision == 0 or constraints.step == Decimal("1")


def _row_enabled(row: dict[str, Any]) -> bool:
    if "是否适用" not in row:
        return True
    status = _text(row.get("是否适用"))
    if not status:
        return True
    return status.lower() in ENABLED_APPLICABILITY_VALUES or status in ENABLED_APPLICABILITY_VALUES


def _command_rule_requires_confirmation(rule: CommonFieldRule) -> bool:
    return rule.field_type in {"save_command", "submit_command"} and "二次确认" in rule.scenario

def _command_rule_requires_optional_clear(rule: CommonFieldRule) -> bool:
    return rule.field_type in {"save_command", "submit_command"} and "非必填" in rule.scenario and "清空" in rule.scenario


def _command_field_supports_confirmation(field: DiscoveredCommonField) -> bool:
    marker = " ".join(
        str(value)
        for value in (field.kind, field.selector, field.source)
        if value
    ).lower()
    return any(
        token in marker
        for token in (
            "confirm", "confirmation", "alertdialog", "message-box",
            "二次确认", "确认框",
        )
    )


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _number_value(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    return int(value) if value == value.to_integral() else float(value)
