"""Agendamento mensal por timer systemd da conta de serviço."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Mapping

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.infrastructure.monthly_scheduler import (
    SchedulerBackend,
    SchedulerState,
    SchedulerStatus,
)


SERVICE_NAME = "tenable-reports-monthly.service"
TIMER_NAME = "tenable-reports-monthly.timer"


def default_systemd_user_unit_root(
    environment: Mapping[str, str] | None = None,
) -> Path:
    values = environment if environment is not None else os.environ
    configured = str(values.get("XDG_CONFIG_HOME") or "").strip()
    config_root = Path(configured).expanduser() if configured else Path.home() / ".config"
    return (config_root / "systemd" / "user").resolve()


def _quoted(value: str) -> str:
    if any(character in value for character in ("\x00", "\n", "\r")):
        raise ValueError("Caminho inválido para unidade systemd.")
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("%", "%%")
    )
    return f'"{escaped}"'


def _unit_path(path: Path) -> str:
    return path.resolve().as_posix()


class SystemdUserTimerScheduler:
    backend = SchedulerBackend.SYSTEMD_USER_TIMER
    service_name = SERVICE_NAME
    timer_name = TIMER_NAME

    def __init__(
        self,
        *,
        project_root: Path,
        config_path: Path,
        unit_root: Path | None = None,
        runner=subprocess.run,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config_path = config_path.resolve()
        self.unit_root = (
            unit_root.resolve() if unit_root is not None else default_systemd_user_unit_root()
        )
        self.runner = runner

    @property
    def service_template(self) -> Path:
        return self.project_root / "deploy" / "linux" / f"{self.service_name}.template"

    @property
    def timer_template(self) -> Path:
        return self.project_root / "deploy" / "linux" / f"{self.timer_name}.template"

    @property
    def command(self) -> str:
        arguments = (
            self.project_root / ".venv" / "bin" / "python",
            Path("-m"),
            Path("tenable_reports"),
            Path("run-monthly-batch"),
            Path("--project-root"),
            self.project_root,
            Path("--config"),
            self.config_path,
            Path("--wait-timeout-seconds"),
            Path("108300"),
        )
        literal_arguments = {"-m", "tenable_reports", "run-monthly-batch", "--project-root", "--config", "--wait-timeout-seconds", "108300"}
        rendered = []
        for argument in arguments:
            value = str(argument) if str(argument) in literal_arguments else _unit_path(argument)
            rendered.append(_quoted(value))
        return " ".join(rendered)

    def _render_units(self, config: MonthlyScheduleConfig) -> tuple[str, str]:
        service_template = self.service_template.read_text(encoding="utf-8")
        timer_template = self.timer_template.read_text(encoding="utf-8")
        return (
            service_template.format(
                working_directory=_quoted(_unit_path(self.project_root)),
                exec_start=self.command,
            ),
            timer_template.format(
                on_calendar=f"*-*-01 {config.local_start_time}:00",
            ),
        )

    def _run(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        return self.runner(
            command,
            shell=False,
            timeout=30,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    @staticmethod
    def _message(completed: subprocess.CompletedProcess[str]) -> str:
        return (completed.stderr or completed.stdout or "Falha ao operar systemd.")[:500]

    def _state(
        self,
        status: SchedulerStatus,
        *,
        enabled: bool | None = None,
        message: str | None = None,
    ) -> SchedulerState:
        return SchedulerState(
            status=status,
            enabled=enabled,
            message=message,
            command=self.command,
            backend=self.backend,
        )

    def query(self, config: MonthlyScheduleConfig) -> SchedulerState:
        try:
            completed = self._run(
                [
                    "systemctl",
                    "--user",
                    "show",
                    self.timer_name,
                    "--property=LoadState",
                    "--property=UnitFileState",
                    "--property=ActiveState",
                    "--no-pager",
                ]
            )
        except FileNotFoundError:
            return self._state(
                SchedulerStatus.UNAVAILABLE,
                message="O executável systemctl do systemd não está disponível neste host.",
            )
        if completed.returncode != 0:
            message = self._message(completed)
            lowered = message.casefold()
            unavailable = any(
                marker in lowered
                for marker in (
                    "failed to connect to bus",
                    "not been booted with systemd",
                    "systemd não",
                    "systemd nao",
                )
            )
            return self._state(
                SchedulerStatus.UNAVAILABLE if unavailable else SchedulerStatus.ERROR,
                message=message,
            )
        properties = {}
        for line in completed.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                properties[key.strip()] = value.strip()
        if properties.get("LoadState") in {"not-found", "masked"}:
            return self._state(SchedulerStatus.MISSING, enabled=False)
        try:
            expected_service, expected_timer = self._render_units(config)
            installed_service = (self.unit_root / self.service_name).read_text(
                encoding="utf-8"
            )
            installed_timer = (self.unit_root / self.timer_name).read_text(
                encoding="utf-8"
            )
        except (FileNotFoundError, OSError, ValueError):
            return self._state(
                SchedulerStatus.DIVERGENT,
                message="As unidades instaladas não correspondem à configuração atual.",
            )
        if installed_service != expected_service or installed_timer != expected_timer:
            return self._state(
                SchedulerStatus.DIVERGENT,
                message="As unidades instaladas divergem da configuração atual.",
            )
        enabled = properties.get("UnitFileState") in {"enabled", "enabled-runtime"}
        active = properties.get("ActiveState") == "active"
        if enabled and active:
            return self._state(SchedulerStatus.SYNCHRONIZED, enabled=True)
        return self._state(SchedulerStatus.DISABLED, enabled=False)

    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_name = temporary.name
            Path(temporary_name).replace(path)
        finally:
            if temporary_name is not None:
                Path(temporary_name).unlink(missing_ok=True)

    def _change_enabled(self, enabled: bool) -> SchedulerState:
        action = "enable" if enabled else "disable"
        try:
            completed = self._run(
                ["systemctl", "--user", action, "--now", self.timer_name]
            )
        except FileNotFoundError as exc:
            raise RuntimeError("O executável systemctl não está disponível.") from exc
        if completed.returncode != 0:
            raise RuntimeError(self._message(completed))
        return self._state(
            SchedulerStatus.SYNCHRONIZED if enabled else SchedulerStatus.DISABLED,
            enabled=enabled,
        )

    def apply(self, config: MonthlyScheduleConfig) -> SchedulerState:
        service, timer = self._render_units(config)
        self._write_atomic(self.unit_root / self.service_name, service)
        self._write_atomic(self.unit_root / self.timer_name, timer)
        try:
            completed = self._run(["systemctl", "--user", "daemon-reload"])
        except FileNotFoundError as exc:
            raise RuntimeError("O executável systemctl não está disponível.") from exc
        if completed.returncode != 0:
            raise RuntimeError(self._message(completed))
        return self._change_enabled(config.enabled)

    def set_enabled(
        self, config: MonthlyScheduleConfig, enabled: bool
    ) -> SchedulerState:
        del config
        return self._change_enabled(enabled)


__all__ = [
    "SystemdUserTimerScheduler",
    "default_systemd_user_unit_root",
]
