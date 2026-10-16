from __future__ import annotations

import subprocess
from pathlib import Path

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.infrastructure.monthly_scheduler import (
    SchedulerBackend,
    SchedulerStatus,
)
from tenable_reports.infrastructure.systemd_user_timer_scheduler import (
    SystemdUserTimerScheduler,
)


ROOT = Path(__file__).resolve().parents[1]


class Runner:
    def __init__(self, *results) -> None:
        self.results = list(results)
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command, **kwargs):
        self.calls.append((list(command), dict(kwargs)))
        if not self.results:
            raise AssertionError(f"chamada inesperada: {command!r}")
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _scheduler(tmp_path: Path, runner: Runner) -> SystemdUserTimerScheduler:
    return SystemdUserTimerScheduler(
        project_root=ROOT,
        config_path=tmp_path / "configuração mensal" / "clients.json",
        unit_root=tmp_path / "systemd user",
        runner=runner,
    )


def test_apply_writes_units_and_uses_systemctl_argument_lists(tmp_path: Path) -> None:
    runner = Runner(_completed(), _completed())
    scheduler = _scheduler(tmp_path, runner)
    config = MonthlyScheduleConfig.from_mapping(
        {"enabled": True, "local_start_time": "01:25"}
    )

    state = scheduler.apply(config)

    service = (scheduler.unit_root / scheduler.service_name).read_text(encoding="utf-8")
    timer = (scheduler.unit_root / scheduler.timer_name).read_text(encoding="utf-8")
    assert "WorkingDirectory=\"" in service
    assert "run-monthly-batch" in service
    assert "configuração mensal/clients.json\"" in service
    assert "OnCalendar=*-*-01 01:25:00" in timer
    assert runner.calls[0][0] == ["systemctl", "--user", "daemon-reload"]
    assert runner.calls[1][0] == [
        "systemctl",
        "--user",
        "enable",
        "--now",
        scheduler.timer_name,
    ]
    assert all(call[1]["shell"] is False for call in runner.calls)
    assert state.backend is SchedulerBackend.SYSTEMD_USER_TIMER
    assert state.status is SchedulerStatus.SYNCHRONIZED


def test_apply_disabled_schedule_disables_timer_without_starting_it(tmp_path: Path) -> None:
    runner = Runner(_completed(), _completed())
    scheduler = _scheduler(tmp_path, runner)

    state = scheduler.apply(MonthlyScheduleConfig.from_mapping({"enabled": False}))

    assert runner.calls[1][0] == [
        "systemctl",
        "--user",
        "disable",
        "--now",
        scheduler.timer_name,
    ]
    assert state.status is SchedulerStatus.DISABLED


def test_query_reports_missing_unit_without_writing_files(tmp_path: Path) -> None:
    runner = Runner(
        _completed(stdout="LoadState=not-found\nUnitFileState=disabled\nActiveState=inactive\n")
    )
    scheduler = _scheduler(tmp_path, runner)

    state = scheduler.query(MonthlyScheduleConfig.from_mapping({}))

    assert state.status is SchedulerStatus.MISSING
    assert not scheduler.unit_root.exists()
    assert runner.calls[0][0] == [
        "systemctl",
        "--user",
        "show",
        scheduler.timer_name,
        "--property=LoadState",
        "--property=UnitFileState",
        "--property=ActiveState",
        "--no-pager",
    ]


def test_query_reports_divergent_installed_content(tmp_path: Path) -> None:
    runner = Runner(
        _completed(stdout="LoadState=loaded\nUnitFileState=enabled\nActiveState=active\n")
    )
    scheduler = _scheduler(tmp_path, runner)
    scheduler.unit_root.mkdir(parents=True)
    (scheduler.unit_root / scheduler.service_name).write_text("alterado", encoding="utf-8")
    (scheduler.unit_root / scheduler.timer_name).write_text("alterado", encoding="utf-8")

    state = scheduler.query(MonthlyScheduleConfig.from_mapping({}))

    assert state.status is SchedulerStatus.DIVERGENT


def test_query_reports_systemd_unavailable_when_binary_is_missing(tmp_path: Path) -> None:
    scheduler = _scheduler(tmp_path, Runner(FileNotFoundError("systemctl")))

    state = scheduler.query(MonthlyScheduleConfig.from_mapping({}))

    assert state.status is SchedulerStatus.UNAVAILABLE
    assert "systemd" in (state.message or "").lower()


def test_set_enabled_uses_enable_and_disable_without_shell(tmp_path: Path) -> None:
    runner = Runner(_completed(), _completed())
    scheduler = _scheduler(tmp_path, runner)
    config = MonthlyScheduleConfig.from_mapping({})

    enabled = scheduler.set_enabled(config, True)
    disabled = scheduler.set_enabled(config, False)

    assert runner.calls[0][0][2:4] == ["enable", "--now"]
    assert runner.calls[1][0][2:4] == ["disable", "--now"]
    assert enabled.status is SchedulerStatus.SYNCHRONIZED
    assert disabled.status is SchedulerStatus.DISABLED
    assert all(call[1]["shell"] is False for call in runner.calls)
