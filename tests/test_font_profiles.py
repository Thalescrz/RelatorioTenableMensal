from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tenable_reports.presentation.font_profiles import (
    FontFace,
    FontProfileError,
    resolve_font_profile,
)


def _runtime(platform: RuntimePlatform) -> RuntimePlatformInfo:
    return RuntimePlatformInfo(
        platform=platform,
        system_name=platform.value,
        architecture="x86_64",
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_windows_fonts(
    root: Path,
    *,
    marker: str = "fixture",
    omit: str | None = None,
    invalid_hash: str | None = None,
) -> None:
    definitions = {
        "document": {
            "family": "Calibri",
            "files": {
                "regular": "calibri.ttf",
                "bold": "calibrib.ttf",
                "italic": "calibrii.ttf",
            },
        },
        "alternate": {
            "family": "Arial",
            "files": {
                "regular": "arial.ttf",
                "bold": "arialbd.ttf",
                "italic": "ariali.ttf",
            },
        },
    }
    for definition in definitions.values():
        files = definition.pop("files")
        definition["faces"] = {}
        for style, filename in files.items():
            payload = f"{marker}:{filename}".encode()
            if filename != omit:
                (root / filename).write_bytes(payload)
            definition["faces"][style] = {
                "file": filename,
                "sha256": "0" * 64 if filename == invalid_hash else _sha256(payload),
            }
    (root / "font-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "windows-calibri-v1",
                "families": definitions,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_resolve_windows_profile_verifies_calibri_and_arial(tmp_path: Path) -> None:
    _write_windows_fonts(tmp_path)

    profile = resolve_font_profile(_runtime(RuntimePlatform.WINDOWS), windows_fonts=tmp_path)

    assert profile.profile_id == "windows-calibri-v1"
    assert profile.document_family == "Calibri"
    assert profile.alternate_family == "Arial"
    assert isinstance(profile.document, FontFace)
    assert profile.regular_path == tmp_path / "calibri.ttf"
    assert profile.bold_path == tmp_path / "calibrib.ttf"
    assert profile.italic_path == tmp_path / "calibrii.ttf"
    assert profile.alternate.regular == tmp_path / "arial.ttf"


def test_resolve_linux_profile_uses_packaged_verified_fonts() -> None:
    profile = resolve_font_profile(_runtime(RuntimePlatform.LINUX))

    assert profile.profile_id == "linux-carlito-v1"
    assert profile.document_family == "Carlito"
    assert profile.alternate_family == "Liberation Sans"
    assert profile.regular_path.name == "Carlito-Regular.ttf"
    assert profile.alternate.bold.name == "LiberationSans-Bold.ttf"
    assert all(
        path.is_file()
        for path in (
            profile.document.regular,
            profile.document.bold,
            profile.document.italic,
            profile.alternate.regular,
            profile.alternate.bold,
            profile.alternate.italic,
        )
    )


def test_missing_calibri_fails_closed_without_exposing_absolute_path(tmp_path: Path) -> None:
    _write_windows_fonts(tmp_path, omit="calibri.ttf")

    with pytest.raises(FontProfileError) as captured:
        resolve_font_profile(_runtime(RuntimePlatform.WINDOWS), windows_fonts=tmp_path)

    assert captured.value.code == "FONT_FILE_MISSING"
    assert captured.value.path == "calibri.ttf"
    assert str(tmp_path) not in str(captured.value)


def test_missing_arial_fails_closed(tmp_path: Path) -> None:
    _write_windows_fonts(tmp_path, omit="arial.ttf")

    with pytest.raises(FontProfileError) as captured:
        resolve_font_profile(_runtime(RuntimePlatform.WINDOWS), windows_fonts=tmp_path)

    assert captured.value.code == "FONT_FILE_MISSING"
    assert captured.value.path == "arial.ttf"


def test_divergent_hash_fails_closed(tmp_path: Path) -> None:
    _write_windows_fonts(tmp_path, invalid_hash="calibrib.ttf")

    with pytest.raises(FontProfileError) as captured:
        resolve_font_profile(_runtime(RuntimePlatform.WINDOWS), windows_fonts=tmp_path)

    assert captured.value.code == "FONT_HASH_MISMATCH"
    assert captured.value.path == "calibrib.ttf"


def test_concurrent_resolutions_do_not_share_font_state(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _write_windows_fonts(first, marker="first")
    _write_windows_fonts(second, marker="second")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                resolve_font_profile,
                _runtime(RuntimePlatform.WINDOWS),
                windows_fonts=root,
            )
            for root in (first, second)
        )
        profiles = tuple(future.result() for future in futures)

    assert profiles[0].regular_path.parent == first
    assert profiles[1].regular_path.parent == second
    assert profiles[0].regular_path.read_bytes() != profiles[1].regular_path.read_bytes()


def test_unsupported_platform_is_rejected() -> None:
    with pytest.raises(FontProfileError) as captured:
        resolve_font_profile(_runtime(RuntimePlatform.UNSUPPORTED))

    assert captured.value.code == "UNSUPPORTED_PLATFORM"
