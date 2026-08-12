from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class FixedType(IntEnum):
    DYNAMIC = 0
    FIXED = 1
    SEMI_FIXED = 2


@dataclass(slots=True)
class FieldDefinition:
    field_code: str
    field_name: str = ""
    field_type: str = "ElInput-TEXT"
    fixed_type: FixedType = FixedType.FIXED
    sort_order: int = 0
    required: bool = False
    readonly: bool = False
    locked: bool = False
    add_visible: bool = True
    edit_visible: bool = True
    view_visible: bool = True
    default_value: Any = None
    props: dict[str, Any] = field(default_factory=dict)
    linkage: dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"

    @property
    def is_runtime(self) -> bool:
        return self.fixed_type in (FixedType.DYNAMIC, FixedType.SEMI_FIXED)

    @property
    def extra_bindings(self) -> dict[str, str]:
        value = self.props.get("extraBindings", {})
        return value if isinstance(value, dict) else {}


@dataclass(slots=True)
class DomField:
    field_code: str
    label: str
    kind: str
    selector: str
    visible: bool = True
    required: bool = False
    readonly: bool = False
    qcc_remote: bool = False
    maxlength: int | None = None
    minimum: str | None = None
    maximum: str | None = None
    pattern: str = ""
    layout_profile: str = ""
    step: str | None = None


@dataclass(slots=True)
class ResolvedField:
    definition: FieldDefinition
    dom: DomField | None = None

    @property
    def status(self) -> str:
        return "matched" if self.dom else "configured_not_rendered"
