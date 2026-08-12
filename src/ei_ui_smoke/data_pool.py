from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .contracts import field_kind
from .models import FieldDefinition


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return {} if default is None else default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def _normalized(value: Any) -> str:
    return re.sub(r"[\s_\-]", "", str(value or "")).lower()


@dataclass(slots=True)
class GlobalDataPool:
    common: dict[str, Any]
    collected: dict[str, Any]
    overrides: dict[str, Any]
    unique_constraints: tuple["UniqueConstraintSpec", ...] = dataclass_field(
        default_factory=tuple
    )

    @classmethod
    def from_directory(cls, data_dir: Path) -> "GlobalDataPool":
        return cls(
            load_json(data_dir / "common_data.json"),
            load_json(data_dir / "collected_data.json", {"forms": {}}),
            load_json(data_dir / "overrides.json", {"forms": {}}),
            load_unique_constraints(data_dir / "unique_constraints.json"),
        )

    def semantic_for(self, field: FieldDefinition) -> str:
        values = (_normalized(field.field_code), _normalized(field.field_name))
        mappings = self.common.get("fieldMappings") or {}
        for semantic, aliases in mappings.items():
            for alias in aliases:
                needle = _normalized(alias)
                if needle and any(
                    needle == value or (len(needle) >= 5 and needle in value)
                    for value in values
                ):
                    return str(semantic)
        return ""

    def override_value(self, form_code: str, field_code: str) -> Any:
        form = ((self.overrides.get("forms") or {}).get(form_code) or {})
        values = form.get("values") if isinstance(form, dict) else {}
        return values[field_code] if isinstance(values, dict) and field_code in values else None

    def collected_value(self, form_code: str, field_code: str) -> Any:
        form = ((self.collected.get("forms") or {}).get(form_code) or {})
        field = ((form.get("fields") or {}).get(field_code) or {}) if isinstance(form, dict) else {}
        values = field.get("values") if isinstance(field, dict) else []
        return values[0] if isinstance(values, list) and values else None

    def candidate(self, semantic: str) -> Any:
        values = (self.common.get("candidatePools") or {}).get(semantic) or []
        return values[0] if isinstance(values, list) and values else None

    def default_upload_file(self) -> Path | None:
        value = str((self.common.get("uploads") or {}).get("defaultFile") or "").strip()
        if not value:
            return None
        path = Path(value).expanduser()
        return path.resolve() if path.is_file() else None


@dataclass(frozen=True, slots=True)
class UniqueConstraintSpec:
    form_code: str
    field_codes: tuple[str, ...]
    repair_field: str
    message_includes: tuple[str, ...] = ()
    alternate_repair_fields: tuple[str, ...] = ()
    list_url_includes: tuple[str, ...] = ()
    record_paths: tuple[str, ...] = ("data.records", "data.rows", "data")
    field_aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def matches_message(self, message: str) -> bool:
        text = re.sub(r"\s+", "", str(message or ""))
        return bool(self.message_includes) and all(
            re.sub(r"\s+", "", token) in text for token in self.message_includes
        )

    def aliases_for(self, field_code: str) -> tuple[str, ...]:
        aliases = next(
            (values for code, values in self.field_aliases if code == field_code),
            (),
        )
        return tuple(dict.fromkeys((*aliases, field_code)))


def load_unique_constraints(path: Path) -> tuple[UniqueConstraintSpec, ...]:
    raw = load_json(path, {"constraints": []})
    entries = raw.get("constraints") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path} 的 constraints 必须是数组")
    specs: list[UniqueConstraintSpec] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"{path} 的第 {index} 个唯一约束必须是对象")
        form_code = str(entry.get("formCode") or "").strip()
        field_codes = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in (entry.get("fieldCodes") or [])
                if str(value or "").strip()
            )
        )
        repair_field = str(entry.get("repairField") or "").strip()
        message_includes = tuple(
            str(value or "").strip()
            for value in (entry.get("messageIncludes") or [])
            if str(value or "").strip()
        )
        alternate_repair_fields = tuple(
            dict.fromkeys(
                str(value or "").strip()
                for value in (entry.get("alternateRepairFields") or [])
                if str(value or "").strip()
            )
        )
        list_url_includes = tuple(
            str(value or "").strip()
            for value in (entry.get("listUrlIncludes") or [])
            if str(value or "").strip()
        )
        record_paths = tuple(
            str(value or "").strip()
            for value in (entry.get("recordPaths") or [
                "data.records", "data.rows", "data",
            ])
            if str(value or "").strip()
        )
        raw_aliases = entry.get("fieldAliases") or {}
        if not isinstance(raw_aliases, dict):
            raise ValueError(f"{path} 的第 {index} 个唯一约束 fieldAliases 必须是对象")
        field_aliases = tuple(
            (
                str(code),
                tuple(
                    str(value or "").strip()
                    for value in (values if isinstance(values, list) else [values])
                    if str(value or "").strip()
                ),
            )
            for code, values in raw_aliases.items()
            if str(code or "").strip()
        )
        if (
            not form_code
            or not field_codes
            or repair_field not in field_codes
            or not message_includes
            or any(code not in field_codes for code in alternate_repair_fields)
            or any(code not in field_codes for code, _aliases in field_aliases)
        ):
            raise ValueError(f"{path} 的第 {index} 个唯一约束配置不完整")
        specs.append(UniqueConstraintSpec(
            form_code=form_code,
            field_codes=field_codes,
            repair_field=repair_field,
            message_includes=message_includes,
            alternate_repair_fields=alternate_repair_fields,
            list_url_includes=list_url_includes,
            record_paths=record_paths,
            field_aliases=field_aliases,
        ))
    return tuple(specs)


class ConstrainedGenerator:
    USCC_CHARS = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    USCC_WEIGHTS = (1, 3, 9, 27, 19, 26, 16, 17, 20, 29, 25, 13, 8, 24, 10, 30, 28)

    def __init__(self, common: dict[str, Any], run_id: str = ""):
        self.common = common
        self.run_id = run_id or datetime.now().strftime("%Y%m%d%H%M%S")
        self.random = random.Random(self.run_id)

    def generate(self, semantic: str, field: FieldDefinition, index: int) -> Any:
        config = (self.common.get("generators") or {}).get(semantic) or {}
        if semantic == "mobile":
            prefix = self.random.choice(config.get("prefixes") or ["139"])
            return prefix + f"{self.random.randrange(10000000, 99999999):08d}"
        if semantic == "email":
            return f"ui_{self.run_id}_{index}@{config.get('domain', 'example.test')}"
        if semantic == "creditCode":
            return self._credit_code(config)
        if semantic in {"amount", "percentage"}:
            return self._decimal_in_range(config)
        if semantic == "enterpriseName":
            return f"{config.get('prefix', 'UI自动化测试企业')}_{self.run_id}_{index}"
        if semantic == "businessIdentifier":
            digits = re.sub(r"\D", "", self.run_id) or "1"
            width = max(6, int(config.get("digits", 16)))
            return (digits + f"{index:02d}").ljust(width, "0")[-width:]
        return self.by_kind(field, index)

    def by_kind(self, field: FieldDefinition, index: int) -> Any:
        kind = field_kind(field.field_type)
        if kind in {"multi_select", "checkbox", "user_select"}:
            return []
        if kind in {"number", "slider", "rate"}:
            return 1
        if kind == "switch":
            return field.props.get("activeValue", True)
        if kind == "date":
            return date.today().isoformat()
        if kind == "datetime":
            return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if kind == "time":
            return datetime.now().strftime("%H:%M:%S")
        if kind in {"select", "radio", "org_select", "tree_select"}:
            return ""
        if kind in {"file", "image", "file_library", "formula", "ai_parse"}:
            return None
        prefix = ((self.common.get("generators") or {}).get("text") or {}).get("prefix", "UI自动化")
        return f"{prefix}_{self.run_id}_{index}"

    def _decimal_in_range(self, config: dict[str, Any]) -> float:
        low = Decimal(str(config.get("min", 1)))
        high = Decimal(str(config.get("max", 100)))
        scale = int(config.get("scale", 2))
        raw = low + (high - low) * Decimal(str(self.random.random()))
        quantum = Decimal("1").scaleb(-scale)
        return float(raw.quantize(quantum, rounding=ROUND_HALF_UP))

    def _credit_code(self, config: dict[str, Any]) -> str:
        authority = str(config.get("registrationAuthority", "91"))[:2]
        region = str(config.get("organizationCodePrefix", "320500"))[:6].ljust(6, "0")
        body = authority + region + "".join(self.random.choice(self.USCC_CHARS) for _ in range(9))
        total = sum(self.USCC_CHARS.index(char) * weight for char, weight in zip(body, self.USCC_WEIGHTS))
        return body + self.USCC_CHARS[(31 - total % 31) % 31]
