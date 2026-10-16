from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tenable_reports.infrastructure.libreoffice import (
    LibreOfficeError,
    LibreOfficeInstallation,
    OfficeRenderer,
    resolve_libreoffice,
)
from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)


EXPECTED_VERSION = "26.2.4.2"


def _platform(value: RuntimePlatform) -> RuntimePlatformInfo:
    return RuntimePlatformInfo(
        platform=value,
        system_name=value.value,
        architecture="x86_64",
    )


def _version_runner(commands: list[list[str]]):
    def run(command, **kwargs):
        commands.append([str(item) for item in command])
        return SimpleNamespace(
            returncode=0,
            stdout=f"LibreOffice {EXPECTED_VERSION} build-id\n",
            stderr="",
        )

    return run


def test_windows_explicit_libreoffice_precedes_portable_path(tmp_path: Path) -> None:
    explicit = tmp_path / "explicit" / "soffice.exe"
    portable = tmp_path / "portable" / "soffice.exe"
    explicit.parent.mkdir()
    portable.parent.mkdir()
    explicit.write_bytes(b"fixture")
    portable.write_bytes(b"fixture")
    commands: list[list[str]] = []

    installation = resolve_libreoffice(
        _platform(RuntimePlatform.WINDOWS),
        explicit_path=explicit,
        portable_path=portable,
        runner=_version_runner(commands),
        which=lambda _: None,
    )

    assert installation.executable == explicit.resolve()
    assert installation.version == EXPECTED_VERSION
    assert installation.source == "explicit"
    assert commands == [[str(explicit.resolve()), "--version"]]


def test_linux_libreoffice_is_resolved_from_path(tmp_path: Path) -> None:
    discovered = tmp_path / "bin" / "soffice"
    discovered.parent.mkdir()
    discovered.write_bytes(b"fixture")

    installation = resolve_libreoffice(
        _platform(RuntimePlatform.LINUX),
        runner=_version_runner([]),
        which=lambda name: str(discovered) if name == "soffice" else None,
    )

    assert installation.executable == discovered.resolve()
    assert installation.source == "path"


def test_libreoffice_version_mismatch_is_blocking_and_sanitized(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "private" / "soffice"
    executable.parent.mkdir()
    executable.write_bytes(b"fixture")

    with pytest.raises(LibreOfficeError) as error:
        resolve_libreoffice(
            _platform(RuntimePlatform.LINUX),
            explicit_path=executable,
            runner=lambda *args, **kwargs: SimpleNamespace(
                returncode=0,
                stdout="LibreOffice 25.8.1.1 build-id",
                stderr="",
            ),
            which=lambda _: None,
        )

    assert error.value.code == "LIBREOFFICE_VERSION_MISMATCH"
    assert str(tmp_path) not in str(error.value)


def test_libreoffice_version_probe_timeout_has_stable_code(tmp_path: Path) -> None:
    executable = tmp_path / "soffice"
    executable.write_bytes(b"fixture")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    with pytest.raises(LibreOfficeError) as error:
        resolve_libreoffice(
            _platform(RuntimePlatform.LINUX),
            explicit_path=executable,
            runner=timeout,
            which=lambda _: None,
        )

    assert error.value.code == "LIBREOFFICE_TIMEOUT"


def test_office_renderer_uses_isolated_profile_and_returns_pdf(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "soffice"
    executable.write_bytes(b"fixture")
    source = tmp_path / "report.docx"
    source.write_bytes(b"fixture")
    output = tmp_path / "pdf"
    observed: dict[str, object] = {}

    def render(command, **kwargs):
        observed["command"] = [str(item) for item in command]
        observed["kwargs"] = kwargs
        output.mkdir(parents=True, exist_ok=True)
        (output / "report.pdf").write_bytes(b"%PDF fixture")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    renderer = OfficeRenderer(
        LibreOfficeInstallation(
            executable=executable,
            version=EXPECTED_VERSION,
            source="explicit",
        ),
        runner=render,
    )

    rendered = renderer.render_docx(source, output)

    assert rendered == output / "report.pdf"
    command = observed["command"]
    assert any(
        str(item).startswith("-env:UserInstallation=file:")
        for item in command
    )
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 180


def test_office_renderer_timeout_is_recoverable_and_sanitized(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "soffice"
    executable.write_bytes(b"fixture")
    source = tmp_path / "report.docx"
    source.write_bytes(b"fixture")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs["timeout"])

    renderer = OfficeRenderer(
        LibreOfficeInstallation(executable, EXPECTED_VERSION, "explicit"),
        runner=timeout,
    )

    with pytest.raises(LibreOfficeError) as error:
        renderer.render_docx(source, tmp_path / "pdf")

    assert error.value.code == "LIBREOFFICE_RENDER_TIMEOUT"
    assert str(tmp_path) not in str(error.value)


def test_office_renderer_does_not_accept_a_stale_pdf_as_new_output(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "soffice"
    executable.write_bytes(b"fixture")
    source = tmp_path / "report.docx"
    source.write_bytes(b"fixture")
    output = tmp_path / "pdf"
    output.mkdir()
    stale = output / "report.pdf"
    stale.write_bytes(b"stale")
    renderer = OfficeRenderer(
        LibreOfficeInstallation(executable, EXPECTED_VERSION, "explicit"),
        runner=lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="",
            stderr="",
        ),
    )

    with pytest.raises(LibreOfficeError) as error:
        renderer.render_docx(source, output)

    assert error.value.code == "LIBREOFFICE_RENDER_FAILED"
    assert not stale.exists()
