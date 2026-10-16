"""Contrato comum e seleção do agendador mensal por plataforma."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
    detect_runtime_platform,
)


class SchedulerBackend(StrEnum):
    WINDOWS_TASK_SCHEDULER = "WINDOWS_TASK_SCHEDULER"
    SYSTEMD_USER_TIMER = "SYSTEMD_USER_TIMER"
    EXTERNAL = "EXTERNAL"


class SchedulerStatus(StrEnum):
    MISSING = "MISSING"
    SYNCHRONIZED = "SYNCHRONIZED"
    DISABLED = "DISABLED"
    DIVERGENT = "DIVERGENT"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class SchedulerState:
    status: SchedulerStatus
    enabled: bool | None = None
    message: str | None = None
    command: str | None = None
    backend: SchedulerBackend = SchedulerBackend.EXTERNAL

    def to_mapping(self) -> dict[str, object]:
        return {
            "backend": self.backend.value,
            "status": self.status.value,
            "enabled": self.enabled,
            "message": self.message,
            "command": self.command,
        }


@runtime_checkable
class MonthlyScheduler(Protocol):
    backend: SchedulerBackend

    def query(self, config: MonthlyScheduleConfig) -> SchedulerState: ...

    def apply(self, config: MonthlyScheduleConfig) -> SchedulerState: ...

    def set_enabled(
        self, config: MonthlyScheduleConfig, enabled: bool
    ) -> SchedulerState: ...


class UnavailableMonthlyScheduler:
    """Backend explícito para uma plataforma sem adaptador instalável."""

    def __init__(self, *, backend: SchedulerBackend, message: str) -> None:
        self.backend = backend
        self.message = message

    def query(self, config: MonthlyScheduleConfig) -> SchedulerState:
        del config
        return SchedulerState(
            status=SchedulerStatus.UNAVAILABLE,
            backend=self.backend,
            message=self.message,
        )

    def apply(self, config: MonthlyScheduleConfig) -> SchedulerState:
        del config
        raise RuntimeError(self.message)

    def set_enabled(
        self, config: MonthlyScheduleConfig, enabled: bool
    ) -> SchedulerState:
        del config, enabled
        raise RuntimeError(self.message)


class ExternalScheduler:
    """Descreve uma agenda deliberadamente gerenciada fora da aplicação."""

    backend = SchedulerBackend.EXTERNAL

    def __init__(self, *, command: str) -> None:
        self.command = command

    def query(self, config: MonthlyScheduleConfig) -> SchedulerState:
        return SchedulerState(
            status=(
                SchedulerStatus.SYNCHRONIZED
                if config.enabled
                else SchedulerStatus.DISABLED
            ),
            enabled=config.enabled,
            backend=self.backend,
            message="O ciclo mensal é gerenciado por um agendador externo.",
            command=self.command,
        )

    @staticmethod
    def _read_only() -> SchedulerState:
        raise RuntimeError(
            "O agendador externo é somente informativo; altere-o fora da aplicação."
        )

    def apply(self, config: MonthlyScheduleConfig) -> SchedulerState:
        del config
        return self._read_only()

    def set_enabled(
        self, config: MonthlyScheduleConfig, enabled: bool
    ) -> SchedulerState:
        del config, enabled
        return self._read_only()


def select_monthly_scheduler(
    *,
    project_root: Path,
    config_path: Path,
    platform_info: RuntimePlatformInfo | None = None,
) -> MonthlyScheduler:
    """Seleciona o backend sem recorrer a heurísticas ou comandos shell."""

    platform = platform_info or detect_runtime_platform()
    if platform.platform is RuntimePlatform.WINDOWS:
        from tenable_reports.infrastructure.windows_task_scheduler import (
            WindowsTaskScheduler,
        )

        return WindowsTaskScheduler(
            project_root=project_root,
            config_path=config_path,
        )
    if platform.platform is RuntimePlatform.LINUX:
        try:
            from tenable_reports.infrastructure.systemd_user_timer_scheduler import (
                SystemdUserTimerScheduler,
            )
        except ModuleNotFoundError as exc:
            if exc.name != (
                "tenable_reports.infrastructure.systemd_user_timer_scheduler"
            ):
                raise
            return UnavailableMonthlyScheduler(
                backend=SchedulerBackend.SYSTEMD_USER_TIMER,
                message="O backend systemd de usuário não está disponível.",
            )
        return SystemdUserTimerScheduler(
            project_root=project_root,
            config_path=config_path,
        )
    return UnavailableMonthlyScheduler(
        backend=SchedulerBackend.EXTERNAL,
        message="A plataforma atual exige um agendador externo configurado pelo operador.",
    )


__all__ = [
    "MonthlyScheduler",
    "ExternalScheduler",
    "SchedulerBackend",
    "SchedulerState",
    "SchedulerStatus",
    "UnavailableMonthlyScheduler",
    "select_monthly_scheduler",
]
