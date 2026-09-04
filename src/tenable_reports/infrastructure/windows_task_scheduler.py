from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig


class WindowsTaskStatus(StrEnum):
    MISSING = "MISSING"
    SYNCHRONIZED = "SYNCHRONIZED"
    DISABLED = "DISABLED"
    DIVERGENT = "DIVERGENT"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class WindowsTaskState:
    status: WindowsTaskStatus
    enabled: bool | None = None
    message: str | None = None
    command: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "enabled": self.enabled,
            "message": self.message,
            "command": self.command,
        }


class WindowsTaskScheduler:
    def __init__(self, *, project_root: Path, config_path: Path, runner=subprocess.run) -> None:
        self.project_root = project_root.resolve()
        self.config_path = config_path.resolve()
        self.runner = runner

    @property
    def launcher(self) -> Path:
        return (self.project_root / "scripts" / "run_monthly_orchestration.ps1").resolve()

    def task_command(self) -> str:
        return (
            'powershell.exe -NoProfile -ExecutionPolicy Bypass -File '
            f'"{self.launcher}" -Config "{self.config_path}"'
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

    def query(self, config: MonthlyScheduleConfig) -> WindowsTaskState:
        completed = self._run(["schtasks.exe", "/Query", "/TN", config.task_name, "/XML"])
        if completed.returncode != 0:
            combined = f"{completed.stdout}\n{completed.stderr}".strip()
            lowered = combined.lower()
            status = WindowsTaskStatus.MISSING if any(
                marker in lowered for marker in ("not found", "não foi possível encontrar", "nao foi possivel encontrar")
            ) else WindowsTaskStatus.ERROR
            return WindowsTaskState(status=status, message=combined[:500] or None)
        try:
            root = ET.fromstring(completed.stdout)
        except ET.ParseError as exc:
            return WindowsTaskState(status=WindowsTaskStatus.ERROR, message=str(exc))
        namespace = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        command = root.findtext(".//t:Actions/t:Exec/t:Command", default="", namespaces=namespace)
        arguments = root.findtext(".//t:Actions/t:Exec/t:Arguments", default="", namespaces=namespace)
        enabled_text = root.findtext(".//t:Settings/t:Enabled", default="true", namespaces=namespace)
        enabled = enabled_text.strip().lower() != "false"
        actual = f"{command} {arguments}".strip()
        expected_parts = (str(self.launcher).lower(), str(self.config_path).lower())
        if not all(part in actual.lower() for part in expected_parts):
            return WindowsTaskState(
                status=WindowsTaskStatus.DIVERGENT,
                enabled=enabled,
                command=actual,
                message="A tarefa existente aponta para outro comando ou configuração.",
            )
        return WindowsTaskState(
            status=WindowsTaskStatus.SYNCHRONIZED if enabled else WindowsTaskStatus.DISABLED,
            enabled=enabled,
            command=actual,
        )

    def apply(self, config: MonthlyScheduleConfig) -> WindowsTaskState:
        completed = self._run([
            "schtasks.exe", "/Create", "/TN", config.task_name,
            "/SC", "MONTHLY", "/D", "1", "/ST", config.local_start_time,
            "/TR", self.task_command(), "/F",
        ])
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "Falha ao criar tarefa.")[:500])
        if not config.enabled:
            self.set_enabled(config, False)
        return WindowsTaskState(
            status=WindowsTaskStatus.SYNCHRONIZED if config.enabled else WindowsTaskStatus.DISABLED,
            enabled=config.enabled,
            command=self.task_command(),
        )

    def set_enabled(self, config: MonthlyScheduleConfig, enabled: bool) -> WindowsTaskState:
        completed = self._run([
            "schtasks.exe", "/Change", "/TN", config.task_name,
            "/Enable" if enabled else "/Disable",
        ])
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "Falha ao alterar tarefa.")[:500])
        return WindowsTaskState(
            status=WindowsTaskStatus.SYNCHRONIZED if enabled else WindowsTaskStatus.DISABLED,
            enabled=enabled,
            command=self.task_command(),
        )
