from __future__ import annotations

import re
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from .contracts import field_kind
from .models import FieldDefinition


@dataclass(slots=True)
class ValidationConstraint:
    kind: str
    min_length: int | None = None
    max_length: int | None = None
    minimum: float | None = None
    maximum: float | None = None
    integer: bool | None = None
    decimal_places: int | None = None
    pattern: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def parse_validation_message(message: str, field: FieldDefinition) -> ValidationConstraint | None:
    text = re.sub(r"\s+", "", message or "")
    props = field.props
    if any(token in text for token in ("无权限", "没有权限", "流程状态", "当前状态", "系统异常", "网络异常")):
        return None
    if any(token in text.lower() for token in ("邮箱", "email", "e-mail")):
        return ValidationConstraint("email", max_length=_int_or_none(props.get("maxlength")))
    if "只能包含" in text and "小写字母" in text and any(token in text for token in ("数字", "下划线")):
        return ValidationConstraint("slug", max_length=_int_or_none(props.get("maxlength")))
    if any(token in text for token in ("手机号", "手机号码", "联系电话")) and (
        "11" in text or "正确" in text or "格式" in text
    ):
        return ValidationConstraint("mobile", min_length=11, max_length=11)
    range_match = re.search(r"(?:长度|字符).{0,6}?(\d+).{0,6}?(?:至|到|~|-).{0,6}?(\d+)", text)
    if range_match:
        return ValidationConstraint("text", int(range_match.group(1)), int(range_match.group(2)))
    max_match = re.search(r"(?:长度|字符)?.{0,4}?(?:不能超过|最多|不大于)(\d+)(?:个?字符|位|字)", text)
    if max_match:
        return ValidationConstraint("text", max_length=int(max_match.group(1)))
    min_match = re.search(r"(?:长度|字符)?.{0,4}?(?:不能少于|至少|不小于)(\d+)(?:个?字符|位|字)", text)
    if min_match:
        return ValidationConstraint("text", min_length=int(min_match.group(1)))
    number_min = re.search(r"(?:必须)?(?:大于|不小于|至少|最小(?:值)?(?:为)?)(-?\d+(?:\.\d+)?)", text)
    number_max = re.search(r"(?:必须)?(?:小于|不大于|最多|最大(?:值)?(?:为)?)(-?\d+(?:\.\d+)?)", text)
    numeric_field = field_kind(field.field_type) in {"number", "amount", "percentage"}
    numeric_props = any(
        props.get(key) not in (None, "")
        for key in ("min", "max", "minimum", "maximum", "step", "precision")
    ) or str(props.get("type") or "").lower() == "number" or str(
        props.get("inputmode") or ""
    ).lower() in {"numeric", "decimal"}
    numeric_hint = any(token in text for token in (
        "数字", "数值", "整数", "小数", "正数", "负数", "大于", "小于", "不小于", "不大于",
    ))
    if any(token in text for token in ("已存在", "重复", "不能重复")):
        return ValidationConstraint("unique")
    if number_min or number_max or numeric_hint or numeric_field or numeric_props:
        minimum = float(number_min.group(1)) if number_min else _float_or_none(
            props.get("min") if props.get("min") not in (None, "") else props.get("minimum")
        )
        maximum = float(number_max.group(1)) if number_max else _float_or_none(
            props.get("max") if props.get("max") not in (None, "") else props.get("maximum")
        )
        if minimum is not None and "大于" in text and "不小于" not in text:
            minimum = _exclusive_step(minimum, props)
        if maximum is not None and "小于" in text and "不大于" not in text:
            maximum = _exclusive_step(maximum, props, descending=True)
        integer = any(token in text for token in ("整数", "整型"))
        inputmode = str(props.get("inputmode") or "").lower()
        step = _float_or_none(props.get("step"))
        if inputmode == "numeric" or step == 1:
            integer = True
        if any(token in text for token in ("正数", "正整数")):
            minimum = max(1 if integer else _positive_step(props), minimum or 0)
        elif any(token in text for token in ("非负", "不能为负", "不允许负")):
            minimum = max(0, minimum or 0)
        if any(token in text for token in ("负数", "负整数")) and "非负" not in text:
            maximum = min(-1 if integer else -_positive_step(props), maximum or 0)
        return ValidationConstraint(
            "number",
            minimum=minimum,
            maximum=maximum,
            integer=integer,
            decimal_places=_int_or_none(props.get("precision")),
        )
    pattern = str(props.get("pattern") or "").strip()
    if pattern and any(token in text for token in ("格式", "只能", "仅支持", "不符合")):
        return ValidationConstraint(
            "pattern",
            min_length=_int_or_none(props.get("minlength")),
            max_length=_int_or_none(props.get("maxlength")),
            pattern=pattern,
        )
    if any(token in text for token in ("必填", "不能为空", "请输入", "请选择")):
        return ValidationConstraint(field_kind(field.field_type) or "text")
    return None


def generate_repair_value(
    constraint: ValidationConstraint,
    field: FieldDefinition,
    current_value: Any,
    attempt: int,
    run_id: str,
) -> Any:
    suffix = f"_{run_id}_{attempt}"
    if constraint.kind == "email":
        local = f"ui_{run_id}_{attempt}"
        value = f"{local}@example.test"
        if constraint.max_length and len(value) > constraint.max_length:
            domain = "@e.test"
            value = local[: max(1, constraint.max_length - len(domain))] + domain
        return value
    if constraint.kind == "mobile":
        digits = re.sub(r"\D", "", str(run_id))[-8:].rjust(8, "0")
        return "139" + digits
    if constraint.kind == "slug":
        value = f"ui_{re.sub(r'[^a-z0-9]+', '_', str(run_id).lower()).strip('_')}_{attempt}"
        return value[: constraint.max_length] if constraint.max_length else value
    if constraint.kind == "number":
        low = constraint.minimum if constraint.minimum is not None else 1
        high = constraint.maximum
        value = min(low, high) if high is not None else low
        if constraint.integer:
            value = math.ceil(value)
        elif constraint.decimal_places is not None:
            value = round(value, constraint.decimal_places)
        return value
    if constraint.kind == "pattern":
        return _value_matching_pattern(constraint, field, attempt, run_id)
    if constraint.kind == "unique":
        return _unique_value_for_field(field, current_value, attempt, suffix)
    base = str(current_value or "UI自动化")
    if constraint.max_length is not None:
        base = base[: constraint.max_length]
    if constraint.min_length is not None and len(base) < constraint.min_length:
        base += "测" * (constraint.min_length - len(base))
    return base or f"UI自动化{attempt}"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _positive_step(props: dict[str, Any]) -> float:
    step = _float_or_none(props.get("step"))
    return step if step and step > 0 else 1


def _exclusive_step(value: float, props: dict[str, Any], *, descending: bool = False) -> float:
    step = _positive_step(props)
    return value - step if descending else value + step


def _value_matching_pattern(
    constraint: ValidationConstraint,
    field: FieldDefinition,
    attempt: int,
    run_id: str,
) -> str:
    marker = re.sub(r"\W+", "", str(run_id)) or "1"
    candidates = [
        str(attempt),
        marker,
        f"A{marker}{attempt}",
        f"a{marker.lower()}{attempt}",
        f"ui_{marker.lower()}_{attempt}",
        str(field.field_code or "value"),
    ]
    pattern = constraint.pattern or ".+"
    for candidate in candidates:
        value = candidate[: constraint.max_length] if constraint.max_length else candidate
        if constraint.min_length and len(value) < constraint.min_length:
            value += "1" * (constraint.min_length - len(value))
        try:
            if re.fullmatch(pattern, value):
                return value
        except re.error:
            break
    return ""


def _unique_value_for_field(
    field: FieldDefinition,
    current_value: Any,
    sequence: int,
    suffix: str,
) -> Any:
    props = field.props
    current = str(current_value or "").strip()
    dom_kind = str(props.get("domKind") or "").strip().lower()
    year_semantic = bool(
        dom_kind == "year"
        or (
            re.fullmatch(r"\d{4}", current)
            and re.search(r"year|年度|年份", f"{field.field_code} {field.field_name}", re.I)
        )
    )
    if year_semantic:
        base_year = int(current) if re.fullmatch(r"\d{4}", current) else date.today().year
        return str(min(9999, base_year + max(1, sequence)))

    kind = field_kind(field.field_type)
    if kind in {"date", "datetime"}:
        parsed = _parse_temporal_value(current, kind)
        if parsed is not None:
            shifted = parsed + timedelta(days=max(1, sequence))
            if kind == "datetime":
                return shifted.strftime("%Y-%m-%d %H:%M:%S")
            return shifted.date().isoformat() if isinstance(shifted, datetime) else shifted.isoformat()

    numeric = kind in {"number", "amount", "percentage", "slider", "rate"}
    numeric = numeric or dom_kind == "number"
    if numeric:
        try:
            base = Decimal(current or "0")
        except (InvalidOperation, ValueError):
            base = Decimal("0")
        try:
            step = Decimal(str(props.get("step") or "1"))
        except InvalidOperation:
            step = Decimal("1")
        if step <= 0:
            step = Decimal("1")
        candidate = base + step * max(1, sequence)
        maximum = _float_or_none(props.get("max"))
        minimum = _float_or_none(props.get("min"))
        if maximum is not None and candidate > Decimal(str(maximum)):
            candidate = base - step * max(1, sequence)
        if minimum is not None and candidate < Decimal(str(minimum)):
            candidate = Decimal(str(minimum)) + step * max(1, sequence)
        precision = _int_or_none(props.get("precision"))
        if precision is not None:
            candidate = candidate.quantize(Decimal("1").scaleb(-precision))
        return int(candidate) if candidate == candidate.to_integral_value() else float(candidate)

    base = str(current_value or field.field_name or field.field_code or "UI自动化")
    maximum = _int_or_none(props.get("maxlength"))
    if maximum is not None and len(base) + len(suffix) > maximum:
        base = base[: max(0, maximum - len(suffix))]
    return base + suffix


def _parse_temporal_value(value: str, kind: str):
    if not value:
        return None
    candidates = (
        ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")
        if kind == "datetime"
        else ("%Y-%m-%d",)
    )
    for pattern in candidates:
        try:
            parsed = datetime.strptime(value, pattern)
            return parsed if kind == "datetime" else parsed.date()
        except ValueError:
            continue
    return None
