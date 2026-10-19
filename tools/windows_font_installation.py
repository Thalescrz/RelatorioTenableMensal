from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable


class WindowsFontStatus(StrEnum):
    READY = "READY"
    BLOCKED = "BLOCKED"


class WindowsFontSource(StrEnum):
    SYSTEM = "SYSTEM"
    PAYLOAD = "PAYLOAD"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class WindowsFontIssue:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class WindowsFontFile:
    name: str
    source: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class WindowsFontPlan:
    status: WindowsFontStatus
    source: WindowsFontSource
    files: tuple[WindowsFontFile, ...]
    issues: tuple[WindowsFontIssue, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "source": self.source.value,
            "files": [item.name for item in self.files],
            "issues": [
                {"code": issue.code, "detail": issue.detail}
                for issue in self.issues
            ],
        }


RegistryWriter = Callable[[str, str], None]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_fonts(manifest_path: Path) -> tuple[tuple[str, str], ...]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        profile = manifest["profiles"]["windows-calibri-v1"]
        families = profile["families"]
        expected = []
        for family_name in ("document", "alternate"):
            faces = families[family_name]["faces"]
            for face_name in ("regular", "bold", "italic"):
                face = faces[face_name]
                expected.append((Path(str(face["file"])).name.casefold(), str(face["sha256"])))
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("FONT_MANIFEST_INVALID") from exc
    return tuple(expected)


def _matching_files(
    root: Path,
    expected: tuple[tuple[str, str], ...],
) -> tuple[bool, tuple[WindowsFontFile, ...], tuple[WindowsFontIssue, ...]]:
    files: list[WindowsFontFile] = []
    issues: list[WindowsFontIssue] = []
    for filename, expected_hash in expected:
        path = root / filename
        if not path.is_file():
            issues.append(WindowsFontIssue("FONT_FILE_MISSING", filename))
            continue
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            issues.append(WindowsFontIssue("FONT_FILE_HASH_MISMATCH", filename))
            continue
        files.append(WindowsFontFile(filename, path, expected_hash))
    return not issues, tuple(files), tuple(issues)


def _license_authorized(payload_root: Path) -> bool:
    try:
        proof = json.loads(
            (payload_root / "license-proof.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return proof.get("redistribution_authorized") is True


def plan_windows_fonts(
    *,
    manifest_path: Path,
    system_font_root: Path,
    payload_root: Path,
) -> WindowsFontPlan:
    expected = _expected_fonts(manifest_path)
    system_ready, _system_files, system_issues = _matching_files(
        system_font_root,
        expected,
    )
    if system_ready:
        return WindowsFontPlan(
            WindowsFontStatus.READY,
            WindowsFontSource.SYSTEM,
            (),
            (),
        )

    payload_ready, payload_files, payload_issues = _matching_files(
        payload_root,
        expected,
    )
    payload_exists = payload_root.is_dir() and any(payload_root.iterdir())
    if not payload_exists:
        return WindowsFontPlan(
            WindowsFontStatus.BLOCKED,
            WindowsFontSource.NONE,
            (),
            system_issues
            + (WindowsFontIssue("FONT_RECOVERY_PAYLOAD_MISSING", "windows-calibri-v1"),),
        )
    if not payload_ready:
        return WindowsFontPlan(
            WindowsFontStatus.BLOCKED,
            WindowsFontSource.NONE,
            (),
            system_issues
            + (WindowsFontIssue("FONT_PAYLOAD_INCOMPLETE", "windows-calibri-v1"),)
            + payload_issues,
        )
    if not _license_authorized(payload_root):
        return WindowsFontPlan(
            WindowsFontStatus.BLOCKED,
            WindowsFontSource.NONE,
            (),
            system_issues
            + (WindowsFontIssue("LICENSE_PROOF_REQUIRED", "windows-calibri-v1"),),
        )
    return WindowsFontPlan(
        WindowsFontStatus.READY,
        WindowsFontSource.PAYLOAD,
        payload_files,
        (),
    )


def install_windows_font_payload(
    plan: WindowsFontPlan,
    *,
    system_font_root: Path,
    register_font: RegistryWriter,
) -> None:
    if plan.status is not WindowsFontStatus.READY or plan.source is not WindowsFontSource.PAYLOAD:
        raise ValueError("FONT_PAYLOAD_NOT_READY")
    system_font_root.mkdir(parents=True, exist_ok=True)
    for item in plan.files:
        target = system_font_root / item.name
        shutil.copy2(item.source, target)
        if _sha256(target) != item.sha256:
            raise OSError("FONT_INSTALL_HASH_MISMATCH")
        register_font(f"Tenable Reports {item.name}", item.name)


def _windows_registry_writer(name: str, filename: str) -> None:
    import winreg

    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE,
        key_path,
        0,
        winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, filename)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--system-font-root", type=Path, required=True)
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    try:
        plan = plan_windows_fonts(
            manifest_path=arguments.manifest,
            system_font_root=arguments.system_font_root,
            payload_root=arguments.payload_root,
        )
    except ValueError as exc:
        print(json.dumps({"status": "BLOCKED", "issues": [{"code": str(exc)}]}))
        return 2
    if plan.status is WindowsFontStatus.BLOCKED:
        print(json.dumps(plan.to_dict(), ensure_ascii=False))
        return 2
    if plan.source is WindowsFontSource.PAYLOAD:
        if not arguments.apply:
            print(json.dumps(plan.to_dict(), ensure_ascii=False))
            return 0
        install_windows_font_payload(
            plan,
            system_font_root=arguments.system_font_root,
            register_font=_windows_registry_writer,
        )
    print(json.dumps(plan.to_dict(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
