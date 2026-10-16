from __future__ import annotations

import json
import zipfile
from pathlib import Path

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tools.build_offline_bundle import OfflineBundleError, build_offline_bundle
from tools.verify_offline_bundle import verify_offline_bundle


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = RuntimePlatformInfo(RuntimePlatform.WINDOWS, "Windows", "x86_64")


def _source_tree(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / "deploy" / "windows").mkdir(parents=True)
    (source / "scripts").mkdir()
    (source / "src" / "package").mkdir(parents=True)
    (source / "requirements" / "lock").mkdir(parents=True)
    for relative in (
        "deploy/runtime-manifest.json",
        "deploy/offline-bundle.schema.json",
        "deploy/windows/bundle-layout.json",
    ):
        target = source / relative
        target.write_bytes((ROOT / relative).read_bytes())
    (source / "scripts" / "setup.ps1").write_text("# offline\n", encoding="utf-8")
    (source / "scripts" / "setup.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\n",
        encoding="utf-8",
    )
    (source / "src" / "package" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "requirements" / "lock" / "windows-py314.txt").write_text(
        "example==1 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    (source / "credentials").mkdir()
    (source / "credentials" / "private.env").write_text(
        "PASSWORD=must-not-be-copied\n", encoding="utf-8"
    )
    (source / "orchestration").mkdir()
    (source / "orchestration" / "clients.json").write_text(
        '{"clients":[{"client_id":"private-client"}]}', encoding="utf-8"
    )
    (source / "orchestration" / "clients.example.json").write_text(
        '{"clients":[]}', encoding="utf-8"
    )
    return source


def _vendor_tree(tmp_path: Path) -> Path:
    vendor = tmp_path / "vendor-source"
    layout = json.loads(
        (ROOT / "deploy" / "windows" / "bundle-layout.json").read_text(
            encoding="utf-8"
        )
    )
    for relative in layout["required_files"]:
        path = vendor / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if relative.endswith("license-proof.json"):
            path.write_text(
                json.dumps({"redistribution_authorized": True}),
                encoding="utf-8",
            )
        else:
            path.write_bytes(f"synthetic:{relative}".encode("utf-8"))
    for pattern in layout["required_globs"]:
        if pattern == "wheelhouse/*.whl":
            path = vendor / "wheelhouse" / "dependency-1-py3-none-any.whl"
        elif pattern == "application/*.whl":
            path = vendor / "application" / "tenable_reports-0.1.0-py3-none-any.whl"
        else:
            raise AssertionError(pattern)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic wheel")
    return vendor


def test_offline_bundle_records_hashes_and_detects_tampering(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    vendor = _vendor_tree(tmp_path)
    destination = tmp_path / "release"

    result = build_offline_bundle(
        source_root=source,
        vendor_root=vendor,
        destination=destination,
        platform=WINDOWS,
        signature_verifier=lambda _path, _kind: True,
    )

    assert result.bundle_root.is_dir()
    assert result.archive_path.is_file()
    with zipfile.ZipFile(result.archive_path) as archive:
        shell_entry = archive.getinfo(
            f"{result.bundle_root.name}/scripts/setup.sh"
        )
    assert (shell_entry.external_attr >> 16) & 0o777 == 0o755
    assert not (result.bundle_root / "credentials" / "private.env").exists()
    assert not (result.bundle_root / "orchestration" / "clients.json").exists()
    assert (result.bundle_root / "orchestration" / "clients.example.json").is_file()
    verification = verify_offline_bundle(result.bundle_root)
    assert verification.ready
    payload = result.bundle_root / "vendor" / "python" / "python-3.14.5-amd64.exe"
    original = payload.read_bytes()
    payload.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    tampered = verify_offline_bundle(result.bundle_root)
    assert not tampered.ready
    assert "BUNDLE_FILE_HASH_MISMATCH" in {issue.code for issue in tampered.issues}


def test_bundle_build_fails_before_copy_when_payload_or_license_is_missing(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path)
    vendor = _vendor_tree(tmp_path)
    (vendor / "fonts" / "windows" / "license-proof.json").unlink()
    destination = tmp_path / "release"

    try:
        build_offline_bundle(
            source_root=source,
            vendor_root=vendor,
            destination=destination,
            platform=WINDOWS,
            signature_verifier=lambda _path, _kind: True,
        )
    except OfflineBundleError as exc:
        assert exc.code in {"REQUIRED_PAYLOAD_MISSING", "LICENSE_PROOF_REQUIRED"}
    else:
        raise AssertionError("bundle incompleto foi aceito")
    assert not destination.exists()


def test_bundle_build_rejects_failed_required_signature(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    vendor = _vendor_tree(tmp_path)

    try:
        build_offline_bundle(
            source_root=source,
            vendor_root=vendor,
            destination=tmp_path / "release",
            platform=WINDOWS,
            signature_verifier=lambda _path, _kind: False,
        )
    except OfflineBundleError as exc:
        assert exc.code == "PAYLOAD_SIGNATURE_INVALID"
    else:
        raise AssertionError("assinatura inválida foi aceita")


def test_bundle_build_rejects_font_payload_that_differs_from_approved_manifest(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path)
    font_manifest = (
        source / "src" / "tenable_reports" / "assets" / "fonts" / "font-manifest.json"
    )
    font_manifest.parent.mkdir(parents=True)
    font_manifest.write_bytes(
        (ROOT / "src" / "tenable_reports" / "assets" / "fonts" / "font-manifest.json").read_bytes()
    )
    vendor = _vendor_tree(tmp_path)

    try:
        build_offline_bundle(
            source_root=source,
            vendor_root=vendor,
            destination=tmp_path / "release",
            platform=WINDOWS,
            signature_verifier=lambda _path, _kind: True,
        )
    except OfflineBundleError as exc:
        assert exc.code == "FONT_PAYLOAD_HASH_MISMATCH"
    else:
        raise AssertionError("fonte divergente foi aceita")
