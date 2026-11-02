"""Resolução e execução isolada do LibreOffice homologado."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)


EXPECTED_LIBREOFFICE_VERSION = "26.2.4.2"
_VERSION_PATTERN = re.compile(r"LibreOffice\s+(\d+(?:\.\d+){3})", re.IGNORECASE)


class LibreOfficeError(RuntimeError):
    """Falha sanitizada e classificável da capacidade de renderização."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        suffix = f" ({detail})" if detail else ""
        super().__init__(f"{code}{suffix}")


@dataclass(frozen=True, slots=True)
class LibreOfficeInstallation:
    executable: Path
    version: str
    source: str

    def to_dict(self) -> dict[str, str]:
        return {
            "version": self.version,
            "source": self.source,
            "executable": self.executable.name,
        }


def _windows_candidates(portable_path: Path | None) -> tuple[tuple[Path, str], ...]:
    candidates: list[tuple[Path, str]] = []
    if portable_path is not None:
        candidates.append((portable_path, "portable"))
    candidates.extend(
        (
            (
                Path("vendor/libreoffice/program/soffice.exe"),
                "portable",
            ),
            (
                Path.cwd().parent
                / "LibreOfficePortable"
                / "App"
                / "libreoffice"
                / "program"
                / "soffice.exe",
                "portable",
            ),
        )
    )
    for variable in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        root = str(os.environ.get(variable) or "").strip()
        if root:
            candidates.append(
                (Path(root) / "LibreOffice" / "program" / "soffice.exe", "installed")
            )
    return tuple(candidates)


def _console_executable(path: Path, platform: RuntimePlatformInfo) -> Path:
    if platform.platform is RuntimePlatform.WINDOWS and path.suffix.casefold() == ".exe":
        console = path.with_suffix(".com")
        if console.is_file():
            return console
    return path


def _subprocess_options(platform: RuntimePlatformInfo) -> dict[str, Any]:
    options: dict[str, Any] = {"shell": False}
    if platform.platform is RuntimePlatform.WINDOWS:
        options["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return options


def _probe_version(
    executable: Path,
    *,
    platform: RuntimePlatformInfo,
    runner: Callable[..., Any],
    timeout_seconds: int,
) -> str:
    command = [_console_executable(executable, platform), "--version"]
    try:
        completed = runner(
            [str(item) for item in command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            **_subprocess_options(platform),
        )
    except subprocess.TimeoutExpired as exc:
        raise LibreOfficeError("LIBREOFFICE_TIMEOUT") from exc
    except OSError as exc:
        raise LibreOfficeError("LIBREOFFICE_EXECUTION_FAILED") from exc
    if int(getattr(completed, "returncode", 1)) != 0:
        raise LibreOfficeError("LIBREOFFICE_EXECUTION_FAILED")
    output = "\n".join(
        str(value or "")
        for value in (
            getattr(completed, "stdout", ""),
            getattr(completed, "stderr", ""),
        )
    )
    match = _VERSION_PATTERN.search(output)
    if match is None:
        raise LibreOfficeError("LIBREOFFICE_VERSION_UNKNOWN")
    return match.group(1)


def resolve_libreoffice(
    platform: RuntimePlatformInfo,
    *,
    explicit_path: str | Path | None = None,
    portable_path: str | Path | None = None,
    expected_version: str = EXPECTED_LIBREOFFICE_VERSION,
    runner: Callable[..., Any] = subprocess.run,
    which: Callable[[str], str | None] = shutil.which,
    timeout_seconds: int = 10,
) -> LibreOfficeInstallation:
    """Resolve a instalação por prioridade e exige a versão homologada exata."""

    if platform.platform is RuntimePlatform.UNSUPPORTED:
        raise LibreOfficeError("UNSUPPORTED_PLATFORM")
    candidates: list[tuple[Path, str]] = []
    if explicit_path is not None:
        candidates.append((Path(explicit_path).expanduser(), "explicit"))
    if platform.platform is RuntimePlatform.WINDOWS:
        candidates.extend(
            _windows_candidates(
                Path(portable_path).expanduser()
                if portable_path is not None
                else None
            )
        )
    elif platform.platform is RuntimePlatform.LINUX:
        discovered = which("soffice")
        if discovered:
            candidates.append((Path(discovered), "path"))

    selected: tuple[Path, str] | None = None
    seen: set[str] = set()
    for candidate, source in candidates:
        normalized = os.path.normcase(str(candidate.resolve()))
        if normalized in seen:
            continue
        seen.add(normalized)
        if candidate.is_file():
            selected = (candidate.resolve(), source)
            break
    if selected is None:
        raise LibreOfficeError("LIBREOFFICE_NOT_FOUND")

    executable, source = selected
    version = _probe_version(
        executable,
        platform=platform,
        runner=runner,
        timeout_seconds=timeout_seconds,
    )
    if version != expected_version:
        raise LibreOfficeError(
            "LIBREOFFICE_VERSION_MISMATCH",
            detail=f"expected={expected_version};detected={version}",
        )
    return LibreOfficeInstallation(
        executable=executable,
        version=version,
        source=source,
    )


@dataclass(slots=True)
class OfficeRenderer:
    installation: LibreOfficeInstallation
    runner: Callable[..., Any] = subprocess.run
    platform: RuntimePlatformInfo | None = None

    def render_docx(
        self,
        document_path: str | Path,
        output_dir: str | Path,
        *,
        timeout_seconds: int = 180,
    ) -> Path:
        """Converte um DOCX em PDF usando um perfil temporário por processo."""

        source = Path(document_path)
        if not source.is_file():
            raise LibreOfficeError("DOCUMENT_NOT_FOUND", detail=source.name)
        output = Path(output_dir)
        target = output / f"{source.stem}.pdf"
        try:
            output.mkdir(parents=True, exist_ok=True)
            target.unlink(missing_ok=True)
        except OSError as exc:
            raise LibreOfficeError(
                "LIBREOFFICE_RENDER_FAILED",
                detail=source.name,
            ) from exc
        active_platform = self.platform or RuntimePlatformInfo(
            platform=(
                RuntimePlatform.WINDOWS
                if os.name == "nt"
                else RuntimePlatform.LINUX
            ),
            system_name=os.name,
            architecture="unknown",
        )
        executable = _console_executable(
            self.installation.executable,
            active_platform,
        )
        try:
            with tempfile.TemporaryDirectory(
                prefix=".tenable-lo-",
                dir=output,
                ignore_cleanup_errors=True,
            ) as profile_directory:
                command = [
                    str(executable),
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nolockcheck",
                    "--nofirststartwizard",
                    f"-env:UserInstallation={Path(profile_directory).resolve().as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(output.resolve()),
                    str(source.resolve()),
                ]
                completed = self.runner(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                    **_subprocess_options(active_platform),
                )
        except subprocess.TimeoutExpired as exc:
            raise LibreOfficeError(
                "LIBREOFFICE_RENDER_TIMEOUT",
                detail=source.name,
            ) from exc
        except OSError as exc:
            raise LibreOfficeError(
                "LIBREOFFICE_RENDER_FAILED",
                detail=source.name,
            ) from exc
        if int(getattr(completed, "returncode", 1)) != 0 or not target.is_file():
            raise LibreOfficeError(
                "LIBREOFFICE_RENDER_FAILED",
                detail=source.name,
            )
        return target


__all__ = [
    "EXPECTED_LIBREOFFICE_VERSION",
    "LibreOfficeError",
    "LibreOfficeInstallation",
    "OfficeRenderer",
    "resolve_libreoffice",
]
