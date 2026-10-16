from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
    detect_runtime_platform,
)


class RuntimeManifestStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RuntimeManifestIssue:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class RuntimeManifestResult:
    status: RuntimeManifestStatus
    platform: str
    issues: tuple[RuntimeManifestIssue, ...]

    @property
    def ready(self) -> bool:
        return self.status is RuntimeManifestStatus.READY

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "platform": self.platform,
            "issues": [
                {"code": issue.code, "detail": issue.detail}
                for issue in self.issues
            ],
        }


def platform_key(platform: RuntimePlatformInfo) -> str:
    return f"{platform.platform.value}-{platform.architecture}"


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise ValueError("unsafe relative path")
    return Path(*pure.parts)


def validate_runtime_manifest(
    root: Path,
    platform: RuntimePlatformInfo,
) -> RuntimeManifestResult:
    resolved_root = root.resolve()
    key = platform_key(platform)
    issues: list[RuntimeManifestIssue] = []
    if (
        platform.platform not in {RuntimePlatform.WINDOWS, RuntimePlatform.LINUX}
        or platform.architecture != "x86_64"
    ):
        return RuntimeManifestResult(
            RuntimeManifestStatus.BLOCKED,
            key,
            (
                RuntimeManifestIssue(
                    "RUNTIME_PLATFORM_UNSUPPORTED",
                    "A plataforma não pertence à matriz homologada.",
                ),
            ),
        )
    try:
        manifest = json.loads(
            (resolved_root / "deploy" / "runtime-manifest.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError):
        return RuntimeManifestResult(
            RuntimeManifestStatus.BLOCKED,
            key,
            (RuntimeManifestIssue("RUNTIME_MANIFEST_INVALID", "Manifesto ausente ou inválido."),),
        )
    if key not in manifest.get("platforms", []):
        issues.append(
            RuntimeManifestIssue(
                "RUNTIME_PLATFORM_UNSUPPORTED",
                "A plataforma não está declarada no manifesto.",
            )
        )
    layout_path = resolved_root / "deploy" / platform.platform.value / "bundle-layout.json"
    try:
        layout = json.loads(layout_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        issues.append(
            RuntimeManifestIssue("BUNDLE_LAYOUT_INVALID", "Layout da plataforma ausente ou inválido.")
        )
        layout = {}
    vendor_root = resolved_root / "vendor"
    for raw_path in layout.get("required_files", []):
        try:
            relative = _safe_relative(str(raw_path))
        except ValueError:
            issues.append(RuntimeManifestIssue("BUNDLE_LAYOUT_INVALID", "Layout contém caminho inseguro."))
            continue
        if not (vendor_root / relative).is_file():
            issues.append(
                RuntimeManifestIssue(
                    "REQUIRED_PAYLOAD_MISSING",
                    relative.as_posix(),
                )
            )
    for raw_pattern in layout.get("required_globs", []):
        try:
            pattern = _safe_relative(str(raw_pattern)).as_posix()
        except ValueError:
            issues.append(RuntimeManifestIssue("BUNDLE_LAYOUT_INVALID", "Layout contém glob inseguro."))
            continue
        if not any(path.is_file() for path in vendor_root.glob(pattern)):
            issues.append(RuntimeManifestIssue("REQUIRED_PAYLOAD_MISSING", pattern))
    status = RuntimeManifestStatus.BLOCKED if issues else RuntimeManifestStatus.READY
    return RuntimeManifestResult(status, key, tuple(issues))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    result = validate_runtime_manifest(arguments.root, detect_runtime_platform())
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
