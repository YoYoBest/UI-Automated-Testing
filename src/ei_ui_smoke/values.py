from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

from .contracts import field_kind
from .models import FieldDefinition


class ValueFactory:
    """Business data wins; configuration defaults and deterministic fallbacks follow."""

    def __init__(self, overrides: Mapping[str, Any] | None = None, run_id: str | None = None):
        self.overrides = dict(overrides or {})
        self.run_id = run_id or datetime.now().strftime("%m%d%H%M%S")

    def value_for(self, field: FieldDefinition, index: int = 1) -> Any:
        if field.field_code in self.overrides:
            return self.overrides[field.field_code]
        if field.default_value is not None:
            return field.default_value
        if "defaultValue" in field.props:
            return field.props["defaultValue"]
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
        if kind in {"file", "image", "file_library", "formula"}:
            return None
        return f"AUTO_{self.run_id}_{index}"
