from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.windows_font_installation import (
    WindowsFontSource,
    WindowsFontStatus,
    install_windows_font_payload,
    plan_windows_fonts,
)


FONT_FILES = (
    "calibri.ttf",
    "calibrib.ttf",
    "calibrii.ttf",
    "arial.ttf",
    "arialbd.ttf",
    "ariali.ttf",
)


def _write_manifest(root: Path, contents: dict[str, bytes]) -> Path:
    def face(filename: str) -> dict[str, str]:
        return {
            "file": filename,
            "sha256": hashlib.sha256(contents[filename]).hexdigest(),
        }

    manifest = {
        "schema_version": 1,
        "profiles": {
            "windows-calibri-v1": {
                "families": {
                    "document": {
                        "family": "Calibri",
                        "faces": {
                            "regular": face("calibri.ttf"),
                            "bold": face("calibrib.ttf"),
                            "italic": face("calibrii.ttf"),
                        },
                    },
                    "alternate": {
                        "family": "Arial",
                        "faces": {
                            "regular": face("arial.ttf"),
                            "bold": face("arialbd.ttf"),
                            "italic": face("ariali.ttf"),
                        },
                    },
                }
            }
        },
    }
    path = root / "font-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _font_contents() -> dict[str, bytes]:
    return {name: f"fixture:{name}".encode("utf-8") for name in FONT_FILES}


def _write_fonts(root: Path, contents: dict[str, bytes]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, payload in contents.items():
        (root / name).write_bytes(payload)


def test_windows_fonts_use_the_licensed_host_without_copying_payload(
    tmp_path: Path,
) -> None:
    contents = _font_contents()
    manifest = _write_manifest(tmp_path, contents)
    system_root = tmp_path / "Windows" / "Fonts"
    _write_fonts(system_root, contents)

    plan = plan_windows_fonts(
        manifest_path=manifest,
        system_font_root=system_root,
        payload_root=tmp_path / "vendor" / "fonts" / "windows",
    )

    assert plan.status is WindowsFontStatus.READY
    assert plan.source is WindowsFontSource.SYSTEM
    assert plan.files == ()
    assert plan.issues == ()


def test_windows_fonts_use_complete_authorized_payload_only_when_host_is_invalid(
    tmp_path: Path,
) -> None:
    contents = _font_contents()
    manifest = _write_manifest(tmp_path, contents)
    system_root = tmp_path / "Windows" / "Fonts"
    system_root.mkdir(parents=True)
    (system_root / "calibri.ttf").write_bytes(b"unexpected")
    payload_root = tmp_path / "vendor" / "fonts" / "windows"
    _write_fonts(payload_root, contents)
    (payload_root / "license-proof.json").write_text(
        json.dumps({"redistribution_authorized": True}),
        encoding="utf-8",
    )

    plan = plan_windows_fonts(
        manifest_path=manifest,
        system_font_root=system_root,
        payload_root=payload_root,
    )

    assert plan.status is WindowsFontStatus.READY
    assert plan.source is WindowsFontSource.PAYLOAD
    assert tuple(item.name for item in plan.files) == FONT_FILES

    registered: list[tuple[str, str]] = []
    install_windows_font_payload(
        plan,
        system_font_root=system_root,
        register_font=lambda name, filename: registered.append((name, filename)),
    )

    assert {
        name: (system_root / name).read_bytes() for name in FONT_FILES
    } == contents
    assert registered == [(f"Tenable Reports {name}", name) for name in FONT_FILES]


def test_windows_fonts_block_when_host_is_invalid_and_recovery_is_not_authorized(
    tmp_path: Path,
) -> None:
    contents = _font_contents()
    manifest = _write_manifest(tmp_path, contents)
    payload_root = tmp_path / "vendor" / "fonts" / "windows"
    _write_fonts(payload_root, contents)

    plan = plan_windows_fonts(
        manifest_path=manifest,
        system_font_root=tmp_path / "Windows" / "Fonts",
        payload_root=payload_root,
    )

    assert plan.status is WindowsFontStatus.BLOCKED
    assert plan.source is WindowsFontSource.NONE
    assert "LICENSE_PROOF_REQUIRED" in {issue.code for issue in plan.issues}


def test_windows_fonts_block_partial_recovery_payload(tmp_path: Path) -> None:
    contents = _font_contents()
    manifest = _write_manifest(tmp_path, contents)
    payload_root = tmp_path / "vendor" / "fonts" / "windows"
    _write_fonts(payload_root, {"calibri.ttf": contents["calibri.ttf"]})

    plan = plan_windows_fonts(
        manifest_path=manifest,
        system_font_root=tmp_path / "Windows" / "Fonts",
        payload_root=payload_root,
    )

    assert plan.status is WindowsFontStatus.BLOCKED
    assert "FONT_PAYLOAD_INCOMPLETE" in {issue.code for issue in plan.issues}
