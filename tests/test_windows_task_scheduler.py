from __future__ import annotations

import subprocess
from pathlib import Path

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
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
    assert command[:2] == ["schtasks.exe", "/Create"]
    assert kwargs["shell"] is False
    assert "00:05" in command
