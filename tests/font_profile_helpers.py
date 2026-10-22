from __future__ import annotations

import re
import zipfile
from pathlib import Path

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tenable_reports.presentation.font_profiles import (
    FontProfile,
    resolve_font_profile,
)


def linux_font_profile() -> FontProfile:
    return resolve_font_profile(
        RuntimePlatformInfo(
            platform=RuntimePlatform.LINUX,
            system_name="Linux",
            architecture="x86_64",
        )
    )


def assert_docx_uses_linux_fonts(path: Path) -> None:
    values: set[str] = set()
    with zipfile.ZipFile(path) as package:
        for name in package.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            xml = package.read(name).decode("utf-8")
            values.update(
                re.findall(
                    r'w:(?:ascii|hAnsi|eastAsia|cs)="([^"]+)"',
                    xml,
                )
            )
    assert "Carlito" in values
    assert values.isdisjoint({"Calibri", "Arial", "Times New Roman"})
