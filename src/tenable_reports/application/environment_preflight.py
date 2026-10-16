"""Diagnóstico sanitizado das capacidades necessárias à publicação oficial."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tenable_reports.config.database import DatabaseConfig
from tenable_reports.infrastructure.libreoffice import (
    LibreOfficeError,
    LibreOfficeInstallation,
    resolve_libreoffice,
)
from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
    detect_runtime_platform,
)
from tenable_reports.presentation.font_profiles import (
    FontProfile,
    FontProfileError,
    resolve_font_profile,
)


class CapabilityStatus(StrEnum):
    READY = "READY"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    code: str
    component: str
    status: CapabilityStatus
    message: str
    remediation: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "component": self.component,
            "status": self.status.value,
            "message": self.message,
            "remediation": self.remediation,
        }


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    platform: RuntimePlatformInfo
    platform_status: CapabilityStatus
    font_status: CapabilityStatus
    font_profile_id: str | None
    libreoffice_status: CapabilityStatus
    libreoffice: LibreOfficeInstallation | None
    postgresql_status: CapabilityStatus
    database_configured: bool
    storage_status: CapabilityStatus
    storage_roots_checked: int
    timezone_status: CapabilityStatus
    scheduler_status: CapabilityStatus
    issues: tuple[PreflightIssue, ...]

    @property
    def official_publication_ready(self) -> bool:
        return all(
            status is not CapabilityStatus.BLOCKED
            for status in (
                self.platform_status,
                self.font_status,
                self.libreoffice_status,
                self.postgresql_status,
                self.storage_status,
                self.timezone_status,
            )
        )

    @property
    def status(self) -> CapabilityStatus:
        statuses = (
            self.platform_status,
            self.font_status,
            self.libreoffice_status,
            self.postgresql_status,
            self.storage_status,
            self.timezone_status,
            self.scheduler_status,
        )
        if CapabilityStatus.BLOCKED in statuses:
            return CapabilityStatus.BLOCKED
        if CapabilityStatus.WARNING in statuses:
            return CapabilityStatus.WARNING
        return CapabilityStatus.READY

    def require_official_publication(self) -> None:
        if not self.official_publication_ready:
            raise EnvironmentPreflightError(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "official_publication_ready": self.official_publication_ready,
            "platform": {
                "id": self.platform.platform.value,
                "system": self.platform.system_name,
                "architecture": self.platform.architecture,
                "status": self.platform_status.value,
            },
            "font_profile": {
                "status": self.font_status.value,
                "id": self.font_profile_id,
            },
            "libreoffice": {
                "status": self.libreoffice_status.value,
                "version": (
                    self.libreoffice.version if self.libreoffice is not None else None
                ),
                "source": (
                    self.libreoffice.source if self.libreoffice is not None else None
                ),
                "executable": (
                    self.libreoffice.executable.name
                    if self.libreoffice is not None
                    else None
                ),
            },
            "postgresql": {
                "status": self.postgresql_status.value,
                "configured": self.database_configured,
            },
            "storage": {
                "status": self.storage_status.value,
                "roots_checked": self.storage_roots_checked,
            },
            "timezone": {"status": self.timezone_status.value},
            "scheduler": {"status": self.scheduler_status.value},
            "issues": [issue.to_dict() for issue in self.issues],
        }


class EnvironmentPreflightError(RuntimeError):
    def __init__(self, capabilities: RuntimeCapabilities) -> None:
        self.capabilities = capabilities
        self.issue_codes = tuple(
            issue.code
            for issue in capabilities.issues
            if issue.status is CapabilityStatus.BLOCKED
        )
        codes = ",".join(self.issue_codes) or "ENVIRONMENT_BLOCKED"
        super().__init__(f"ENVIRONMENT_PREFLIGHT_BLOCKED ({codes})")


def _default_database_probe(config: DatabaseConfig) -> None:
    from tenable_reports.infrastructure.postgresql import PostgresDatabase

    PostgresDatabase(config).status()


def _default_write_probe(root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".tenable-write-probe-",
            dir=root,
            delete=True,
        ) as probe:
            probe.write(b"ready")
            probe.flush()
    except OSError:
        return False
    return True


def _issue(
    code: str,
    component: str,
    status: CapabilityStatus,
    message: str,
    remediation: str,
) -> PreflightIssue:
    return PreflightIssue(code, component, status, message, remediation)


def run_environment_preflight(
    *,
    platform_info: RuntimePlatformInfo | None = None,
    persistent_roots: Sequence[str | Path] = (),
    database_config: DatabaseConfig | None = None,
    explicit_libreoffice: str | Path | None = None,
    portable_libreoffice: str | Path | None = None,
    font_probe: Callable[[RuntimePlatformInfo], FontProfile] = resolve_font_profile,
    office_probe: Callable[[RuntimePlatformInfo], LibreOfficeInstallation] | None = None,
    database_probe: Callable[[DatabaseConfig], None] = _default_database_probe,
    write_probe: Callable[[Path], bool] = _default_write_probe,
    scheduler_status: CapabilityStatus = CapabilityStatus.WARNING,
    diagnostic_only: bool = True,
) -> RuntimeCapabilities:
    """Avalia o host sem acessar a Tenable nem expor configuração sensível."""

    platform = platform_info or detect_runtime_platform()
    issues: list[PreflightIssue] = []
    profile: FontProfile | None = None
    office: LibreOfficeInstallation | None = None

    if platform.platform is RuntimePlatform.UNSUPPORTED:
        platform_status = CapabilityStatus.BLOCKED
        font_status = CapabilityStatus.BLOCKED
        office_status = CapabilityStatus.BLOCKED
        database_status = CapabilityStatus.BLOCKED
        issues.append(
            _issue(
                "UNSUPPORTED_PLATFORM",
                "platform",
                CapabilityStatus.BLOCKED,
                "O sistema operacional não possui pacote homologado.",
                "Use Windows ou Linux na arquitetura suportada.",
            )
        )
    else:
        platform_status = CapabilityStatus.READY
        try:
            profile = font_probe(platform)
            font_status = CapabilityStatus.READY
        except FontProfileError:
            font_status = CapabilityStatus.BLOCKED
            issues.append(
                _issue(
                    "FONT_PROFILE_INVALID",
                    "fonts",
                    CapabilityStatus.BLOCKED,
                    "O perfil tipográfico obrigatório não pôde ser validado.",
                    "Restaure os arquivos de fonte homologados e seus hashes.",
                )
            )

        active_office_probe = office_probe or (
            lambda detected: resolve_libreoffice(
                detected,
                explicit_path=explicit_libreoffice,
                portable_path=portable_libreoffice,
            )
        )
        try:
            office = active_office_probe(platform)
            office_status = CapabilityStatus.READY
        except LibreOfficeError as exc:
            office_status = CapabilityStatus.BLOCKED
            issues.append(
                _issue(
                    exc.code,
                    "libreoffice",
                    CapabilityStatus.BLOCKED,
                    "O renderizador de documentos não está homologado neste host.",
                    "Instale ou selecione o LibreOffice previsto no manifesto.",
                )
            )

        if database_config is None:
            database_status = CapabilityStatus.BLOCKED
            issues.append(
                _issue(
                    "POSTGRESQL_NOT_CONFIGURED",
                    "postgresql",
                    CapabilityStatus.BLOCKED,
                    "O armazenamento durável não está configurado.",
                    "Configure as variáveis PostgreSQL do ambiente.",
                )
            )
        else:
            try:
                database_probe(database_config)
                database_status = CapabilityStatus.READY
            except Exception:
                database_status = CapabilityStatus.BLOCKED
                issues.append(
                    _issue(
                        "POSTGRESQL_UNAVAILABLE",
                        "postgresql",
                        CapabilityStatus.BLOCKED,
                        "O armazenamento durável não está acessível.",
                        "Verifique o serviço e a configuração PostgreSQL.",
                    )
                )

    roots = tuple(Path(root) for root in persistent_roots)
    blocked_roots = sum(not write_probe(root) for root in roots)
    storage_status = (
        CapabilityStatus.BLOCKED if blocked_roots else CapabilityStatus.READY
    )
    if blocked_roots:
        issues.append(
            _issue(
                "STORAGE_NOT_WRITABLE",
                "storage",
                CapabilityStatus.BLOCKED,
                "Uma ou mais raízes persistentes não aceitam gravação.",
                "Corrija a existência, propriedade e permissão dos diretórios.",
            )
        )

    try:
        ZoneInfo("America/Fortaleza")
        timezone_status = CapabilityStatus.READY
    except ZoneInfoNotFoundError:
        timezone_status = CapabilityStatus.BLOCKED
        issues.append(
            _issue(
                "TIMEZONE_DATABASE_UNAVAILABLE",
                "timezone",
                CapabilityStatus.BLOCKED,
                "A base de fusos horários IANA não está disponível.",
                "Instale a dependência tzdata prevista no pacote.",
            )
        )

    if scheduler_status is CapabilityStatus.WARNING:
        issues.append(
            _issue(
                "SCHEDULER_UNAVAILABLE",
                "scheduler",
                CapabilityStatus.WARNING,
                "O agendamento automático ainda não foi validado.",
                "A geração manual permanece disponível.",
            )
        )

    capabilities = RuntimeCapabilities(
        platform=platform,
        platform_status=platform_status,
        font_status=font_status,
        font_profile_id=profile.profile_id if profile is not None else None,
        libreoffice_status=office_status,
        libreoffice=office,
        postgresql_status=database_status,
        database_configured=database_config is not None,
        storage_status=storage_status,
        storage_roots_checked=len(roots),
        timezone_status=timezone_status,
        scheduler_status=scheduler_status,
        issues=tuple(issues),
    )
    if not diagnostic_only:
        capabilities.require_official_publication()
    return capabilities


__all__ = [
    "CapabilityStatus",
    "EnvironmentPreflightError",
    "PreflightIssue",
    "RuntimeCapabilities",
    "run_environment_preflight",
]
