"""Detecção centralizada e injetável da plataforma de execução."""

from __future__ import annotations

import platform as system_platform
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class RuntimePlatform(StrEnum):
    """Plataformas reconhecidas pelo pacote de implantação."""

    WINDOWS = "windows"
    LINUX = "linux"
    UNSUPPORTED = "unsupported"


class PlatformProbe(Protocol):
    """Fonte mínima de informações do sistema operacional."""

    def system(self) -> str: ...

    def machine(self) -> str: ...


@dataclass(frozen=True, slots=True)
class SystemPlatformProbe:
    """Consulta a plataforma real sem aceitar atalhos por variável de ambiente."""

    def system(self) -> str:
        return system_platform.system()

    def machine(self) -> str:
        return system_platform.machine()


@dataclass(frozen=True, slots=True)
class RuntimePlatformInfo:
    """Identidade normalizada do host atual."""

    platform: RuntimePlatform
    system_name: str
    architecture: str

    @property
    def supported(self) -> bool:
        return self.platform is not RuntimePlatform.UNSUPPORTED


_ARCHITECTURE_ALIASES = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "x86-64": "x86_64",
    "x86_64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
}


def _normalize_architecture(value: str) -> str:
    normalized = value.strip().casefold().replace("_", "-")
    aliased = _ARCHITECTURE_ALIASES.get(normalized)
    if aliased is not None:
        return aliased
    sanitized = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
    return sanitized or "unknown"


def detect_runtime_platform(probe: PlatformProbe | None = None) -> RuntimePlatformInfo:
    """Detecta Windows ou Linux exclusivamente por uma fonte explícita."""

    active_probe = probe or SystemPlatformProbe()
    system_name = active_probe.system().strip()
    detected = {
        "windows": RuntimePlatform.WINDOWS,
        "linux": RuntimePlatform.LINUX,
    }.get(system_name.casefold(), RuntimePlatform.UNSUPPORTED)
    return RuntimePlatformInfo(
        platform=detected,
        system_name=system_name,
        architecture=_normalize_architecture(active_probe.machine()),
    )
