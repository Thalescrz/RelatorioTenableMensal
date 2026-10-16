from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True, slots=True)
class BundleIssue:
    code: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class BundleVerification:
    ready: bool
    platform: str | None
    issues: tuple[BundleIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "READY" if self.ready else "BLOCKED",
            "platform": self.platform,
            "issues": [
                {"code": issue.code, "path": issue.path}
                for issue in self.issues
            ],
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_path(root: Path, raw: str) -> Path | None:
    pure = PurePosixPath(raw)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def verify_offline_bundle(bundle_root: Path) -> BundleVerification:
    root = bundle_root.resolve()
    issues: list[BundleIssue] = []
    manifest_path = root / "bundle-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return BundleVerification(
            False,
            None,
            (BundleIssue("BUNDLE_MANIFEST_INVALID", "bundle-manifest.json"),),
        )
    platform = str(manifest.get("platform") or "") or None
    if manifest.get("schema_version") != 1:
        issues.append(BundleIssue("BUNDLE_SCHEMA_UNSUPPORTED", "bundle-manifest.json"))
    declared: set[str] = set()
    for entry in manifest.get("files", []):
        raw = str(entry.get("path") or "")
        if raw in declared:
            issues.append(BundleIssue("BUNDLE_FILE_DUPLICATED", raw))
            continue
        declared.add(raw)
        path = _safe_path(root, raw)
        if path is None:
            issues.append(BundleIssue("BUNDLE_PATH_UNSAFE", raw))
            continue
        if not path.is_file() or path.is_symlink():
            issues.append(BundleIssue("BUNDLE_FILE_MISSING", raw))
            continue
        if path.stat().st_size != int(entry.get("size", -1)):
            issues.append(BundleIssue("BUNDLE_FILE_SIZE_MISMATCH", raw))
            continue
        if sha256_file(path) != str(entry.get("sha256") or ""):
            issues.append(BundleIssue("BUNDLE_FILE_HASH_MISMATCH", raw))
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "bundle-manifest.json"
    }
    for untracked in sorted(actual - declared):
        issues.append(BundleIssue("BUNDLE_FILE_UNTRACKED", untracked))
    for absent in sorted(declared - actual):
        if not any(issue.path == absent for issue in issues):
            issues.append(BundleIssue("BUNDLE_FILE_MISSING", absent))
    return BundleVerification(not issues, platform, tuple(issues))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    arguments = parser.parse_args()
    result = verify_offline_bundle(arguments.root)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0 if result.ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
