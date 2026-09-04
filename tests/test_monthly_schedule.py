from __future__ import annotations

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.application.monthly_schedule import MonthlyScheduleService
from tenable_reports.infrastructure.windows_task_scheduler import (
    WindowsTaskState,
    WindowsTaskStatus,
)


def test_monthly_schedule_defaults_are_safe_and_inactive() -> None:
    config = MonthlyScheduleConfig.from_mapping({})
    assert config.enabled is False
    assert config.day_of_month == 1
    assert config.local_start_time == "00:05"
    assert config.task_name == "Relatorios Tenable - Mensal"


def test_monthly_schedule_rejects_day_other_than_first() -> None:
    try:
        MonthlyScheduleConfig.from_mapping({"day_of_month": 2})
    except ValueError as exc:
        assert "dia 1" in str(exc)
    else:
        raise AssertionError("configuração inválida aceita")


def test_monthly_schedule_round_trip() -> None:
    config = MonthlyScheduleConfig.from_mapping(
        {"enabled": True, "local_start_time": "01:30"}
    )
    assert MonthlyScheduleConfig.from_mapping(config.to_mapping()) == config


class _Store:
    def __init__(self) -> None:
        self.config = MonthlyScheduleConfig.from_mapping({})

    def monthly_schedule(self):
        return self.config

    def save_monthly_schedule(self, config):
        self.config = config

    def raw(self):
        return {"orchestration_id": "carteira-tenable"}

    def list_clients(self):
        return [{"client_id": "a", "enabled": True, "credentials_ready": True}]


class _Scheduler:
    def __init__(self) -> None:
        self.calls = []

    def query(self, config):
        self.calls.append(("query", config))
        return WindowsTaskState(WindowsTaskStatus.MISSING)

    def apply(self, config):
        self.calls.append(("apply", config))
        return WindowsTaskState(WindowsTaskStatus.SYNCHRONIZED, enabled=True)

    def set_enabled(self, config, enabled):
        self.calls.append(("set_enabled", enabled))
        return WindowsTaskState(
            WindowsTaskStatus.SYNCHRONIZED if enabled else WindowsTaskStatus.DISABLED,
            enabled=enabled,
        )


def test_save_does_not_invoke_windows_or_create_batch() -> None:
    store = _Store()
    scheduler = _Scheduler()
    service = MonthlyScheduleService(store=store, scheduler=scheduler)
    result = service.save({"enabled": True, "local_start_time": "00:05"})
    assert scheduler.calls == []
    assert result["eligible_client_ids"] == ["a"]


def test_apply_requires_explicit_confirmation() -> None:
    service = MonthlyScheduleService(store=_Store(), scheduler=_Scheduler())
    try:
        service.apply("incorreto")
    except ValueError as exc:
        assert "SINCRONIZAR AUTOMACAO MENSAL" in str(exc)
    else:
        raise AssertionError("aplicação sem confirmação aceita")
