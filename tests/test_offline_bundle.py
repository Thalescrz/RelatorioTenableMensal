from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tools.build_offline_bundle import (
    OfflineBundleError,
    _copy_source,
    _default_signature_verifier,
    build_offline_bundle,
)
from tools.verify_offline_bundle import verify_offline_bundle


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = RuntimePlatformInfo(RuntimePlatform.WINDOWS, "Windows", "x86_64")


def test_bundle_builder_can_run_directly_from_repository_root() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")

    completed = subprocess.run(
        [sys.executable, "tools/build_offline_bundle.py", "--help"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--vendor-root" in completed.stdout


def test_default_signature_verifier_uses_identity_from_bundle_limited_to_domain(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "Python-3.14.5.tar.xz"
    bundle = tmp_path / "Python-3.14.5.tar.xz.sigstore"
    artifact.write_bytes(b"official artifact")
    bundle.write_text("official verification material", encoding="utf-8")
    commands: list[list[str]] = []

    def run_verifier(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "OK: Python-3.14.5.tar.xz\n", "")

    monkeypatch.setattr(subprocess, "run", run_verifier)
    monkeypatch.setattr(
        "tools.build_offline_bundle._sigstore_email_identity",
        lambda _bundle, _domain: "artifact-signer@python.invalid",
    )

    verified = _default_signature_verifier(
        artifact,
        {
            "kind": "sigstore",
            "cert_identity_email_domain": "python.invalid",
            "cert_oidc_issuer": "https://github.com/login/oauth",
        },
    )

    assert verified
    assert commands == [
        [
            sys.executable,
            "-m",
            "sigstore",
            "verify",
            "identity",
            "--offline",
            "--bundle",
            str(bundle),
            "--cert-identity",
            "artifact-signer@python.invalid",
            "--cert-oidc-issuer",
            "https://github.com/login/oauth",
            str(artifact),
        ]
    ]


def test_default_signature_verifier_uses_pinned_detached_keyring(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "LibreOffice.tar.gz"
    signature = tmp_path / "LibreOffice.tar.gz.asc"
    keyring = tmp_path / "tdf-release-keyring.gpg"
    artifact.write_bytes(b"official artifact")
    signature.write_text("official detached signature", encoding="utf-8")
    keyring.write_bytes(b"pinned release keyring")
    commands: list[list[str]] = []

    def run_verifier(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            (
                "[GNUPG:] VALIDSIG "
                "C2839ECAD9408FBE9531C3E9F434A1EFAFEEAEA3 "
                "2026-10-01 0 4 0 1 10 00 "
                "C2839ECAD9408FBE9531C3E9F434A1EFAFEEAEA3\n"
            ),
            "Good signature",
        )

    monkeypatch.setattr(subprocess, "run", run_verifier)

    verified = _default_signature_verifier(
        artifact,
        {
            "kind": "detached",
            "keyring": "tdf-release-keyring.gpg",
            "key_fingerprint": "C2839ECAD9408FBE9531C3E9F434A1EFAFEEAEA3",
        },
    )

    assert verified
    assert commands == [
        [
            "gpgv",
            "--status-fd",
            "1",
            "--keyring",
            str(keyring),
            str(signature),
            str(artifact),
        ]
    ]


def test_default_signature_verifier_rejects_unexpected_detached_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "LibreOffice.tar.gz"
    signature = tmp_path / "LibreOffice.tar.gz.asc"
    keyring = tmp_path / "tdf-release-keyring.gpg"
    artifact.write_bytes(b"official artifact")
    signature.write_text("detached signature", encoding="utf-8")
    keyring.write_bytes(b"release keyring")

    def run_verifier(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            (
                "[GNUPG:] VALIDSIG "
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
                "2026-10-01 0 4 0 1 10 00 "
                "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
            ),
            "Good signature",
        )

    monkeypatch.setattr(subprocess, "run", run_verifier)

    assert not _default_signature_verifier(
        artifact,
        {
            "kind": "detached",
            "keyring": "tdf-release-keyring.gpg",
            "key_fingerprint": "C2839ECAD9408FBE9531C3E9F434A1EFAFEEAEA3",
        },
    )


def test_default_signature_verifier_binds_authenticode_path_as_parameter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "signed installer.exe"
    artifact.write_bytes(b"signed")
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []
    program_files = tmp_path / "Program Files"
    windows_root = tmp_path / "Windows"
    monkeypatch.setenv("PROGRAMFILES", str(program_files))
    monkeypatch.setenv("WINDIR", str(windows_root))
    monkeypatch.setenv("PSModulePath", "incompatible-powershell-modules")

    def run_verifier(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        environments.append(kwargs["env"])
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "Status": "Valid",
                    "Subject": "CN=Official Publisher, O=Official Publisher, C=US",
                    "CertificateSha256": "A" * 64,
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run_verifier)

    verified = _default_signature_verifier(
        artifact,
        {
            "kind": "authenticode",
            "signer_subject": "CN=Official Publisher, O=Official Publisher, C=US",
            "certificate_sha256": "A" * 64,
        },
    )

    assert verified
    assert commands == [
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "& { param([string] $ArtifactPath) "
                "$Signature = Get-AuthenticodeSignature -LiteralPath $ArtifactPath; "
                "$CertificateSha256 = if ($Signature.SignerCertificate) { "
                "$Signature.SignerCertificate.GetCertHashString("
                "[System.Security.Cryptography.HashAlgorithmName]::SHA256) "
                "} else { '' }; "
                "[pscustomobject]@{ Status = [string] $Signature.Status; "
                "Subject = [string] $Signature.SignerCertificate.Subject; "
                "CertificateSha256 = $CertificateSha256 } | "
                "ConvertTo-Json -Compress }"
            ),
            str(artifact),
        ]
    ]
    assert environments[0]["PSModulePath"] == os.pathsep.join(
        (
            str(program_files / "WindowsPowerShell" / "Modules"),
            str(
                windows_root
                / "system32"
                / "WindowsPowerShell"
                / "v1.0"
                / "Modules"
            ),
        )
    )


def test_default_signature_verifier_rejects_unexpected_authenticode_signer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact = tmp_path / "signed installer.exe"
    artifact.write_bytes(b"signed")

    def run_verifier(command: list[str], **_kwargs) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps(
                {
                    "Status": "Valid",
                    "Subject": "CN=Different Publisher, O=Different Publisher, C=US",
                    "CertificateSha256": "B" * 64,
                }
            ),
            "",
        )

    monkeypatch.setattr(subprocess, "run", run_verifier)

    assert not _default_signature_verifier(
        artifact,
        {
            "kind": "authenticode",
            "signer_subject": "CN=Official Publisher, O=Official Publisher, C=US",
            "certificate_sha256": "A" * 64,
        },
    )


def _initialize_git_source(source: Path) -> None:
    subprocess.run(
        ["git", "init", "--quiet", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "add", "--all"],
        check=True,
        capture_output=True,
        text=True,
    )


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
        "src/tenable_reports/assets/fonts/font-manifest.json",
        "tools/windows_font_installation.py",
    ):
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    (source / "scripts" / "setup.ps1").write_text("# offline\n", encoding="utf-8")
    (source / "scripts" / "bootstrap_postgresql.ps1").write_text(
        "# offline database bootstrap\n",
        encoding="utf-8",
    )
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
    (source / "orchestration" / "document-control.example.json").write_text(
        '{}', encoding="utf-8"
    )
    _initialize_git_source(source)
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


def test_source_copy_does_not_traverse_unrelated_large_directories(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = _source_tree(tmp_path)
    included = source / "src" / "package" / "module.py"
    excluded_root = source / "deployment-transfers"
    excluded = excluded_root / "payloads" / "large.bin"
    excluded.parent.mkdir(parents=True)
    excluded.write_bytes(b"must not be traversed")
    (source / ".gitignore").write_text(
        "deployment-transfers/\n*.db\n*.log\n*.egg-info/\n",
        encoding="utf-8",
    )
    ignored = (
        source / "src" / "package" / "cache.db",
        source / "src" / "package" / "release.log",
        source / "src" / "package.egg-info" / "PKG-INFO",
    )
    for path in ignored:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("must not be copied\n", encoding="utf-8")
    _initialize_git_source(source)
    destination = tmp_path / "bundle"
    original_scandir = os.scandir

    def guarded_scandir(path):
        candidate = Path(path)
        if candidate == excluded_root or excluded_root in candidate.parents:
            raise AssertionError("unrelated deployment payload tree was traversed")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", guarded_scandir)

    _copy_source(source, destination, platform=WINDOWS)

    assert (destination / "src" / "package" / "module.py").is_file()
    assert not (destination / "deployment-transfers").exists()
    for path in ignored:
        assert not (destination / path.relative_to(source)).exists()


def test_source_copy_rejects_empty_or_partial_git_index(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "deploy" / "windows").mkdir(parents=True)
    (source / "deploy" / "windows" / "bundle-layout.json").write_text(
        '{"platform":"windows-x86_64"}',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "init", "--quiet", str(source)],
        check=True,
        capture_output=True,
        text=True,
    )

    try:
        _copy_source(source, tmp_path / "bundle", platform=WINDOWS)
    except OfflineBundleError as exc:
        assert exc.code == "SOURCE_GIT_INDEX_INCOMPLETE"
    else:
        raise AssertionError("índice Git vazio foi aceito")


def test_source_copy_rejects_tracked_file_missing_from_worktree(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    missing = source / "scripts" / "setup.ps1"
    missing.unlink()

    try:
        _copy_source(source, tmp_path / "bundle", platform=WINDOWS)
    except OfflineBundleError as exc:
        assert exc.code == "SOURCE_TRACKED_FILE_MISSING"
        assert exc.detail == "scripts/setup.ps1"
    else:
        raise AssertionError("arquivo rastreado ausente foi aceito")


def _add_optional_windows_font_payload(vendor: Path) -> Path:
    font_root = vendor / "fonts" / "windows"
    font_root.mkdir(parents=True, exist_ok=True)
    for filename in (
        "calibri.ttf",
        "calibrib.ttf",
        "calibrii.ttf",
        "arial.ttf",
        "arialbd.ttf",
        "ariali.ttf",
    ):
        (font_root / filename).write_bytes(f"synthetic:{filename}".encode("utf-8"))
    (font_root / "license-proof.json").write_text(
        json.dumps({"redistribution_authorized": True}),
        encoding="utf-8",
    )
    return font_root


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
    _add_optional_windows_font_payload(vendor)
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


def test_windows_bundle_without_proprietary_fonts_declares_host_requirement(
    tmp_path: Path,
) -> None:
    source = _source_tree(tmp_path)
    vendor = _vendor_tree(tmp_path)
    font_root = vendor / "fonts" / "windows"
    assert not font_root.exists()

    result = build_offline_bundle(
        source_root=source,
        vendor_root=vendor,
        destination=tmp_path / "release",
        platform=WINDOWS,
        signature_verifier=lambda _path, _kind: True,
    )

    manifest = json.loads(
        (result.bundle_root / "bundle-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["host_requirements"] == ["windows-calibri-v1"]
    assert not (result.bundle_root / "vendor" / "fonts" / "windows").exists()


def test_windows_bundle_rejects_partial_optional_font_payload(tmp_path: Path) -> None:
    source = _source_tree(tmp_path)
    vendor = _vendor_tree(tmp_path)
    font_root = vendor / "fonts" / "windows"
    font_root.mkdir(parents=True)
    (font_root / "calibri.ttf").write_bytes(b"partial")

    try:
        build_offline_bundle(
            source_root=source,
            vendor_root=vendor,
            destination=tmp_path / "release",
            platform=WINDOWS,
            signature_verifier=lambda _path, _kind: True,
        )
    except OfflineBundleError as exc:
        assert exc.code == "OPTIONAL_PAYLOAD_INCOMPLETE"
    else:
        raise AssertionError("payload opcional parcial foi aceito")


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
    font_manifest.parent.mkdir(parents=True, exist_ok=True)
    font_manifest.write_bytes(
        (ROOT / "src" / "tenable_reports" / "assets" / "fonts" / "font-manifest.json").read_bytes()
    )
    vendor = _vendor_tree(tmp_path)
    _add_optional_windows_font_payload(vendor)

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
