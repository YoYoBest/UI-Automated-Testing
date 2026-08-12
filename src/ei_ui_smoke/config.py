from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Settings:
    source_root: Path
    base_url: str
    username: str = ""
    password: str = ""
    form_code: str = ""
    module_name: str = ""
    headless: bool = True
    data_mode: str = "probe"

    @classmethod
    def from_env(cls) -> "Settings":
        root = os.getenv(
            "EI_PARENT_ROOT",
            r"D:\Auto_Testing\Project_Purvar\SHZY\ei-parent",
        )
        return cls(
            source_root=Path(root),
            base_url=os.getenv("EI_BASE_URL", "").rstrip("/"),
            username=os.getenv("EI_USERNAME", ""),
            password=os.getenv("EI_PASSWORD", ""),
            form_code=os.getenv("EI_FORM_CODE", ""),
            module_name=os.getenv("EI_MODULE_NAME", ""),
            headless=os.getenv("EI_HEADLESS", "true").lower() in {"1", "true", "yes"},
            data_mode=os.getenv("EI_DATA_MODE", "probe").strip().lower(),
        )
