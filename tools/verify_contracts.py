from __future__ import annotations

import os
import re
from pathlib import Path

from ei_ui_smoke.contracts import build_runtime_data, field_kind, normalize_field, remove_runtime_fields


def main() -> None:
    source_root = Path(os.getenv("EI_PARENT_ROOT", r"D:\Auto_Testing\Project_Purvar\SHZY\ei-parent"))
    source = (source_root / "ei-view/src/utils/dynamicFormField.ts").read_text(encoding="utf-8")
    blocks = re.findall(r"(?:CONFIG_FIELD_TYPES|LEGACY_FIELD_TYPES) = \[(.*?)\] as const", source, re.S)
    source_types = [item for block in blocks for item in re.findall(r'"([^"]+)"', block)]
    missing = [item for item in source_types if field_kind(item) == "unknown"]
    if missing:
        raise AssertionError(f"Unmapped ei-parent field types: {missing}")

    fields = [
        normalize_field({"fieldCode": "fixed", "fixedType": 1}, "verify"),
        normalize_field({"fieldCode": "runtime", "fixedType": 0, "viewVisible": 1}, "verify"),
    ]
    assert build_runtime_data({"fixed": 1, "runtime": ["a", "b"]}, fields)["runtime"] == "a,b"
    assert remove_runtime_fields({"fixed": 1, "runtime": 2}, fields) == {"fixed": 1}
    print(f"FIELD_TYPE_COVERAGE=PASS ({len(set(source_types))} types)")
    print("CONTRACTS=PASS")


if __name__ == "__main__":
    main()

