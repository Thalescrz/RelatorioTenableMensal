from __future__ import annotations

import subprocess
from pathlib import Path

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.infrastructure.monthly_scheduler import SchedulerBackend
from tenable_reports.infrastructure.windows_task_scheduler import (
    WindowsTaskScheduler,
    WindowsTaskStatus,
)


class Runner:
    def __init__(self, result: subprocess.CompletedProcess[str]) -> None:
        self.result = result
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), kwargs))
        return self.result


def test_missing_windows_task_is_reported_without_mutation(tmp_path: Path) -> None:
    runner = Runner(subprocess.CompletedProcess([], 1, "", "ERROR: not found"))
    scheduler = WindowsTaskScheduler(
        project_root=tmp_path,
        config_path=tmp_path / "orchestration" / "clients.json",
        runner=runner,
    )
    state = scheduler.query(MonthlyScheduleConfig.from_mapping({}))
    assert state.status is WindowsTaskStatus.MISSING
    assert len(runner.calls) == 1
    assert "/Query" in runner.calls[0][0]


def test_apply_uses_argument_list_and_never_shell(tmp_path: Path) -> None:
    runner = Runner(subprocess.CompletedProcess([], 0, "SUCCESS", ""))
    scheduler = WindowsTaskScheduler(
        project_root=tmp_path,
        config_path=tmp_path / "orchestration" / "clients.json",
        runner=runner,
    )
    scheduler.apply(MonthlyScheduleConfig.from_mapping({"enabled": True}))
    command, kwargs = runner.calls[0]
    assert command == [
        "schtasks.exe",
        "/Create",
        "/TN",
        "Relatorios Tenable - Mensal",
        "/SC",
        "MONTHLY",
        "/D",
        "1",
        "/ST",
        "00:05",
        "/TR",
        scheduler.task_command(),
        "/F",
    ]
    assert kwargs["shell"] is False
    assert "00:05" in command


def test_task_command_declares_project_root_and_absolute_config(tmp_path: Path) -> None:
    scheduler = WindowsTaskScheduler(
        project_root=tmp_path / "project",
        config_path=tmp_path / "project" / "orchestration" / "clients.json",
        runner=Runner(subprocess.CompletedProcess([], 0, "SUCCESS", "")),
    )

    command = scheduler.task_command()

    assert f'-ProjectRoot "{scheduler.project_root}"' in command
    assert f'-Config "{scheduler.config_path}"' in command


def test_windows_task_state_exposes_generic_backend_without_changing_legacy_status(
    tmp_path: Path,
) -> None:
    runner = Runner(subprocess.CompletedProcess([], 1, "", "ERROR: not found"))
    scheduler = WindowsTaskScheduler(
        project_root=tmp_path,
        config_path=tmp_path / "orchestration" / "clients.json",
        runner=runner,
    )

    mapping = scheduler.query(MonthlyScheduleConfig.from_mapping({})).to_mapping()

    assert mapping["backend"] == SchedulerBackend.WINDOWS_TASK_SCHEDULER.value
    assert mapping["status"] == WindowsTaskStatus.MISSING.value
