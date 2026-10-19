from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
if __package__:
    from tools.validate_runtime_manifest import platform_key
    from tools.verify_offline_bundle import sha256_file, verify_offline_bundle
else:
    from validate_runtime_manifest import platform_key
    from verify_offline_bundle import sha256_file, verify_offline_bundle


class OfflineBundleError(RuntimeError):
    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{f' ({detail})' if detail else ''}")


@dataclass(frozen=True, slots=True)
class OfflineBundleResult:
    bundle_root: Path
    archive_path: Path
    platform: str
    file_count: int


SignatureVerifier = Callable[[Path, Mapping[str, object]], bool]


_SOURCE_FILES = {
    ".gitattributes",
    ".gitignore",
    "AGENTS.md",
    "CONTEXTO.md",
    "DESIGN.md",
    "README.md",
    "pyproject.toml",
}
_SOURCE_DIRECTORIES = {
    ".agents",
    "clients",
    "deploy",
    "docs",
    "requirements",
    "scripts",
    "src",
    "templates",
    "tools",
}
_EXCLUDED_PARTS = {
    ".git",
    ".pytest_cache",
    ".tmp",
    ".venv",
    "__pycache__",
    "analysis_artifacts",
    "artifacts",
    "data",
    "outputs",
    "reports_generated",
}
_REQUIRED_TRACKED_SOURCE_FILES = {
    "deploy/offline-bundle.schema.json",
    "deploy/runtime-manifest.json",
    "orchestration/clients.example.json",
    "orchestration/document-control.example.json",
}
_PLATFORM_REQUIRED_TRACKED_SOURCE_FILES = {
    RuntimePlatform.WINDOWS: {
        "deploy/windows/bundle-layout.json",
        "requirements/lock/windows-py314.txt",
        "scripts/bootstrap_postgresql.ps1",
        "scripts/setup.ps1",
        "src/tenable_reports/assets/fonts/font-manifest.json",
        "tools/windows_font_installation.py",
    },
    RuntimePlatform.LINUX: {
        "deploy/linux/bundle-layout.json",
        "requirements/lock/linux-py314.txt",
        "scripts/bootstrap_postgresql.sh",
        "scripts/setup.sh",
    },
}


def _safe_relative(raw: str) -> Path:
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise OfflineBundleError("BUNDLE_LAYOUT_INVALID")
    return Path(*pure.parts)


def _load_layout(source_root: Path, platform: RuntimePlatformInfo) -> dict[str, object]:
    path = source_root / "deploy" / platform.platform.value / "bundle-layout.json"
    try:
        layout = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineBundleError("BUNDLE_LAYOUT_INVALID") from exc
    if layout.get("platform") != platform_key(platform):
        raise OfflineBundleError("BUNDLE_LAYOUT_PLATFORM_MISMATCH")
    return layout


def _validate_vendor(
    vendor_root: Path,
    layout: dict[str, object],
    signature_verifier: SignatureVerifier,
) -> None:
    for raw in layout.get("required_files", []):
        relative = _safe_relative(str(raw))
        if not (vendor_root / relative).is_file():
            raise OfflineBundleError(
                "REQUIRED_PAYLOAD_MISSING", detail=relative.as_posix()
            )
    for raw in layout.get("required_globs", []):
        pattern = _safe_relative(str(raw)).as_posix()
        if not any(path.is_file() for path in vendor_root.glob(pattern)):
            raise OfflineBundleError("REQUIRED_PAYLOAD_MISSING", detail=pattern)
    for group in layout.get("optional_payload_groups", []):
        files = tuple(
            _safe_relative(str(raw)) for raw in group.get("files", [])
        )
        license_relative = _safe_relative(str(group.get("license_proof") or ""))
        if not any(
            (vendor_root / relative).is_file()
            for relative in files + (license_relative,)
        ):
            continue
        missing = tuple(
            relative for relative in files if not (vendor_root / relative).is_file()
        )
        if missing:
            raise OfflineBundleError(
                "OPTIONAL_PAYLOAD_INCOMPLETE",
                detail=missing[0].as_posix(),
            )
        proof = vendor_root / license_relative
        try:
            authorized = bool(
                json.loads(proof.read_text(encoding="utf-8")).get(
                    "redistribution_authorized"
                )
            )
        except (OSError, json.JSONDecodeError):
            authorized = False
        if not authorized:
            raise OfflineBundleError("LICENSE_PROOF_REQUIRED")
    for requirement in layout.get("signature_requirements", []):
        relative = _safe_relative(str(requirement.get("path") or ""))
        if not signature_verifier(vendor_root / relative, requirement):
            raise OfflineBundleError(
                "PAYLOAD_SIGNATURE_INVALID", detail=relative.as_posix()
            )


def _default_signature_verifier(
    path: Path,
    requirement: Mapping[str, object],
) -> bool:
    kind = str(requirement.get("kind") or "")
    if kind == "authenticode" and os.name == "nt":
        expected_subject = str(requirement.get("signer_subject") or "").strip()
        expected_certificate_sha256 = str(
            requirement.get("certificate_sha256") or ""
        ).strip().upper()
        if not expected_subject or len(expected_certificate_sha256) != 64:
            return False
        powershell_environment = os.environ.copy()
        program_files = Path(
            powershell_environment.get("PROGRAMFILES", r"C:\Program Files")
        )
        windows_root = Path(
            powershell_environment.get("WINDIR", r"C:\Windows")
        )
        powershell_environment["PSModulePath"] = os.pathsep.join(
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
        completed = subprocess.run(
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
                str(path),
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            env=powershell_environment,
        )
        if completed.returncode != 0:
            return False
        try:
            signature = json.loads(completed.stdout)
        except (TypeError, json.JSONDecodeError):
            return False
        return (
            signature.get("Status") == "Valid"
            and str(signature.get("Subject") or "").strip() == expected_subject
            and str(signature.get("CertificateSha256") or "").strip().upper()
            == expected_certificate_sha256
        )
    if kind == "sigstore":
        bundle = path.with_name(path.name + ".sigstore")
        identity_domain = str(
            requirement.get("cert_identity_email_domain") or ""
        )
        issuer = str(requirement.get("cert_oidc_issuer") or "")
        if not bundle.is_file() or not identity_domain or not issuer:
            return False
        identity = _sigstore_email_identity(bundle, identity_domain)
        if not identity:
            return False
        completed = subprocess.run(
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
                identity,
                "--cert-oidc-issuer",
                issuer,
                str(path),
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        return completed.returncode == 0
    if kind == "detached":
        signature = path.with_name(path.name + ".asc")
        keyring_name = str(requirement.get("keyring") or "")
        expected_fingerprint = str(
            requirement.get("key_fingerprint") or ""
        ).strip().upper()
        keyring = path.parent / keyring_name
        if (
            not signature.is_file()
            or not keyring_name
            or not keyring.is_file()
            or len(expected_fingerprint) != 40
            or any(character not in "0123456789ABCDEF" for character in expected_fingerprint)
        ):
            return False
        completed = subprocess.run(
            [
                "gpgv",
                "--status-fd",
                "1",
                "--keyring",
                str(keyring),
                str(signature),
                str(path),
            ],
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if completed.returncode != 0:
            return False
        for line in completed.stdout.splitlines():
            fields = line.split()
            if len(fields) < 3 or fields[:2] != ["[GNUPG:]", "VALIDSIG"]:
                continue
            valid_fingerprints = {fields[2].upper(), fields[-1].upper()}
            if expected_fingerprint in valid_fingerprints:
                return True
        return False
    return False


def _sigstore_email_identity(bundle: Path, allowed_domain: str) -> str | None:
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID
    except ImportError:
        return None

    try:
        payload = json.loads(bundle.read_text(encoding="utf-8"))
        encoded = payload["verificationMaterial"]["certificate"]["rawBytes"]
        certificate = x509.load_der_x509_certificate(
            base64.b64decode(encoded, validate=True)
        )
        subject_alternative_names = certificate.extensions.get_extension_for_oid(
            ExtensionOID.SUBJECT_ALTERNATIVE_NAME
        ).value
        identities = subject_alternative_names.get_values_for_type(x509.RFC822Name)
    except (KeyError, TypeError, ValueError, x509.ExtensionNotFound):
        return None

    normalized_domain = allowed_domain.casefold().strip().lstrip("@")
    matches = [
        identity
        for identity in identities
        if identity.rpartition("@")[2].casefold() == normalized_domain
    ]
    return matches[0] if len(matches) == 1 else None


def _source_allowed(path: Path, source_root: Path) -> bool:
    relative = path.relative_to(source_root)
    if any(part in _EXCLUDED_PARTS for part in relative.parts):
        return False
    if path.is_symlink():
        return False
    if relative.parts[:2] == ("clients", "managed"):
        return False
    if relative.parts[0] == "orchestration":
        return path.name.endswith(".example.json")
    if relative.parts[0] == "credentials":
        return path.name.endswith(".env.example")
    if path.name.startswith(".env") and not path.name.endswith(".example"):
        return False
    return relative.parts[0] in _SOURCE_DIRECTORIES or relative.as_posix() in _SOURCE_FILES


def _validate_font_payloads(
    source_root: Path,
    vendor_root: Path,
    platform: RuntimePlatformInfo,
) -> None:
    font_manifest_path = (
        source_root
        / "src"
        / "tenable_reports"
        / "assets"
        / "fonts"
        / "font-manifest.json"
    )
    if not font_manifest_path.is_file():
        return
    runtime_manifest = json.loads(
        (source_root / "deploy" / "runtime-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    font_manifest = json.loads(font_manifest_path.read_text(encoding="utf-8"))
    key = platform_key(platform)
    profile_id = runtime_manifest["font_profiles"][key]
    profile = font_manifest["profiles"][profile_id]
    platform_directory = "windows" if platform.platform is RuntimePlatform.WINDOWS else "linux"
    if platform.platform is RuntimePlatform.WINDOWS:
        payload_root = vendor_root / "fonts" / platform_directory
        if not payload_root.is_dir() or not any(payload_root.iterdir()):
            return
    for family in profile["families"].values():
        for face in family["faces"].values():
            filename = Path(str(face["file"])).name
            payload = vendor_root / "fonts" / platform_directory / filename
            if not payload.is_file():
                raise OfflineBundleError(
                    "REQUIRED_PAYLOAD_MISSING",
                    detail=f"fonts/{platform_directory}/{filename}",
                )
            if sha256_file(payload) != str(face["sha256"]):
                raise OfflineBundleError(
                    "FONT_PAYLOAD_HASH_MISMATCH",
                    detail=filename,
                )


def _copy_source(
    source_root: Path,
    destination: Path,
    *,
    platform: RuntimePlatformInfo | None = None,
) -> None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source_root), "ls-files", "-z"],
            shell=False,
            capture_output=True,
            check=False,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise OfflineBundleError("SOURCE_GIT_INDEX_REQUIRED") from exc
    if completed.returncode != 0:
        raise OfflineBundleError("SOURCE_GIT_INDEX_REQUIRED")

    files: list[Path] = []
    tracked_paths: set[str] = set()
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        try:
            relative_raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OfflineBundleError("SOURCE_GIT_INDEX_INVALID") from exc
        relative = _safe_relative(PurePosixPath(relative_raw).as_posix())
        relative_posix = relative.as_posix()
        tracked_paths.add(relative_posix)
        path = source_root / relative
        if not _source_allowed(path, source_root):
            continue
        if not path.is_file():
            raise OfflineBundleError(
                "SOURCE_TRACKED_FILE_MISSING",
                detail=relative_posix,
            )
        files.append(path)

    required_paths = set(_REQUIRED_TRACKED_SOURCE_FILES)
    if platform is not None:
        required_paths.update(
            _PLATFORM_REQUIRED_TRACKED_SOURCE_FILES[platform.platform]
        )
    missing_required = sorted(required_paths - tracked_paths)
    if missing_required:
        raise OfflineBundleError(
            "SOURCE_GIT_INDEX_INCOMPLETE",
            detail=missing_required[0],
        )

    for path in sorted(files, key=lambda item: item.relative_to(source_root).as_posix()):
        target = destination / path.relative_to(source_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _manifest_files(bundle_root: Path) -> list[dict[str, object]]:
    return [
        {
            "path": path.relative_to(bundle_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in sorted(bundle_root.rglob("*"))
        if path.is_file() and path.name != "bundle-manifest.json"
    ]


def _write_bootstrap_checksums(bundle_root: Path) -> None:
    entries = []
    for path in sorted(bundle_root.rglob("*")):
        if not path.is_file() or path.name in {
            "bundle-manifest.json",
            "bundle-manifest.sha256",
        }:
            continue
        relative = path.relative_to(bundle_root).as_posix()
        entries.append(f"{sha256_file(path)}  {relative}")
    (bundle_root / "bundle-manifest.sha256").write_text(
        "\n".join(entries) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_reproducible_zip(bundle_root: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as archive:
        for path in sorted(bundle_root.rglob("*")):
            if not path.is_file():
                continue
            relative = Path(bundle_root.name) / path.relative_to(bundle_root)
            info = zipfile.ZipInfo(relative.as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.suffix.casefold() == ".sh" else 0o644
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, path.read_bytes())


def build_offline_bundle(
    *,
    source_root: Path,
    vendor_root: Path,
    destination: Path,
    platform: RuntimePlatformInfo,
    signature_verifier: SignatureVerifier = _default_signature_verifier,
) -> OfflineBundleResult:
    source = source_root.resolve()
    vendor = vendor_root.resolve()
    key = platform_key(platform)
    if key not in {"windows-x86_64", "linux-x86_64"}:
        raise OfflineBundleError("RUNTIME_PLATFORM_UNSUPPORTED")
    layout = _load_layout(source, platform)
    _validate_vendor(vendor, layout, signature_verifier)
    _validate_font_payloads(source, vendor, platform)
    release_root = destination.resolve()
    bundle_root = release_root / f"tenable-reports-{key}"
    archive_path = release_root / f"tenable-reports-{key}.zip"
    if release_root.exists():
        raise OfflineBundleError("DESTINATION_EXISTS")
    release_root.mkdir(parents=True)
    try:
        _copy_source(source, bundle_root, platform=platform)
        shutil.copytree(vendor, bundle_root / "vendor")
        _write_bootstrap_checksums(bundle_root)
        files = _manifest_files(bundle_root)
        manifest = {
            "schema_version": 1,
            "platform": key,
            "host_requirements": list(layout.get("host_requirements", [])),
            "files": files,
        }
        (bundle_root / "bundle-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        verification = verify_offline_bundle(bundle_root)
        if not verification.ready:
            raise OfflineBundleError("BUNDLE_VERIFICATION_FAILED")
        _write_reproducible_zip(bundle_root, archive_path)
    except Exception:
        shutil.rmtree(release_root, ignore_errors=True)
        raise
    return OfflineBundleResult(bundle_root, archive_path, key, len(files))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--vendor-root", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--platform", choices=("windows", "linux"), required=True)
    arguments = parser.parse_args()
    runtime_platform = (
        RuntimePlatform.WINDOWS
        if arguments.platform == "windows"
        else RuntimePlatform.LINUX
    )
    result = build_offline_bundle(
        source_root=arguments.source_root,
        vendor_root=arguments.vendor_root,
        destination=arguments.destination,
        platform=RuntimePlatformInfo(
            runtime_platform,
            arguments.platform,
            "x86_64",
        ),
    )
    print(result.archive_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
