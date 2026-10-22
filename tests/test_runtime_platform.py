from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
    detect_runtime_platform,
)


class StubPlatformProbe:
    def __init__(self, system_name: str, architecture: str) -> None:
        self._system_name = system_name
        self._architecture = architecture

    def system(self) -> str:
        return self._system_name

    def machine(self) -> str:
        return self._architecture


@pytest.mark.parametrize(
    ("system_name", "architecture", "expected_platform", "expected_architecture"),
    [
        ("Windows", "AMD64", RuntimePlatform.WINDOWS, "x86_64"),
        ("windows", "x64", RuntimePlatform.WINDOWS, "x86_64"),
        ("Linux", "x86_64", RuntimePlatform.LINUX, "x86_64"),
        ("linux", "aarch64", RuntimePlatform.LINUX, "arm64"),
    ],
)
def test_detect_runtime_platform_normalizes_supported_hosts(
    system_name: str,
    architecture: str,
    expected_platform: RuntimePlatform,
    expected_architecture: str,
) -> None:
    result = detect_runtime_platform(StubPlatformProbe(system_name, architecture))

    assert result.platform is expected_platform
    assert result.architecture == expected_architecture
    assert result.system_name == system_name
    assert result.supported is True


def test_detect_runtime_platform_preserves_sanitized_unknown_architecture() -> None:
    result = detect_runtime_platform(StubPlatformProbe("Linux", " RISC/V 64 $$ "))

    assert result.platform is RuntimePlatform.LINUX
    assert result.architecture == "risc-v-64"


def test_detect_runtime_platform_marks_unknown_system_as_unsupported() -> None:
    result = detect_runtime_platform(StubPlatformProbe("FreeBSD", "amd64"))

    assert result == RuntimePlatformInfo(
        platform=RuntimePlatform.UNSUPPORTED,
        system_name="FreeBSD",
        architecture="x86_64",
    )
    assert result.supported is False


def test_environment_variables_cannot_spoof_operating_system(monkeypatch) -> None:
    monkeypatch.setenv("OS", "Windows_NT")
    monkeypatch.setenv("OSTYPE", "msys")

    result = detect_runtime_platform(StubPlatformProbe("Linux", "x86_64"))

    assert result.platform is RuntimePlatform.LINUX


def test_runtime_platform_info_is_immutable() -> None:
    result = detect_runtime_platform(StubPlatformProbe("Windows", "AMD64"))

    with pytest.raises(FrozenInstanceError):
        result.architecture = "arm64"  # type: ignore[misc]
