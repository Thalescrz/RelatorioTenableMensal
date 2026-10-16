from __future__ import annotations

import json
from pathlib import Path

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tools.validate_runtime_manifest import (
    RuntimeManifestStatus,
    validate_runtime_manifest,
)


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_manifest_pins_official_versions_and_platforms() -> None:
    manifest = json.loads(
        (ROOT / "deploy" / "runtime-manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["schema_version"] == 1
    assert manifest["python_version"] == "3.14.5"
    assert manifest["libreoffice_version"] == "26.2.4.2"
    assert manifest["postgresql_version"] == "18"
    assert manifest["platforms"] == ["windows-x86_64", "linux-x86_64"]
    assert manifest["font_profiles"] == {
        "windows-x86_64": "windows-calibri-v1",
        "linux-x86_64": "linux-carlito-v1",
    }
    assert manifest["payload_integrity"] == "bundle-manifest-sha256"


def test_runtime_manifest_rejects_missing_required_payload(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    for relative in (
        "runtime-manifest.json",
        "offline-bundle.schema.json",
        "windows/bundle-layout.json",
    ):
        source = ROOT / "deploy" / relative
        target = tmp_path / "deploy" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())

    result = validate_runtime_manifest(
        tmp_path,
        RuntimePlatformInfo(
            RuntimePlatform.WINDOWS,
            "Windows",
            "x86_64",
        ),
    )

    assert result.status is RuntimeManifestStatus.BLOCKED
    assert "REQUIRED_PAYLOAD_MISSING" in {issue.code for issue in result.issues}


def test_runtime_manifest_rejects_unsupported_architecture(tmp_path: Path) -> None:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "runtime-manifest.json").write_bytes(
        (ROOT / "deploy" / "runtime-manifest.json").read_bytes()
    )

    result = validate_runtime_manifest(
        tmp_path,
        RuntimePlatformInfo(RuntimePlatform.LINUX, "Linux", "arm64"),
    )

    assert result.status is RuntimeManifestStatus.BLOCKED
    assert result.issues[0].code == "RUNTIME_PLATFORM_UNSUPPORTED"
