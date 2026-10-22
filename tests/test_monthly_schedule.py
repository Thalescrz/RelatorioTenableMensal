from __future__ import annotations

from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.application.monthly_schedule import MonthlyScheduleService
from tenable_reports.infrastructure.monthly_scheduler import (
    ExternalScheduler,
    SchedulerBackend,
    SchedulerState,
    SchedulerStatus,
    select_monthly_scheduler,
)
from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tenable_reports.infrastructure.windows_task_scheduler import (
    WindowsTaskScheduler,
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


def test_windows_status_adds_generic_payload_and_preserves_legacy_payload() -> None:
    result = MonthlyScheduleService(store=_Store(), scheduler=_Scheduler()).status()

    assert result["scheduler"] == result["windows_task"]
    assert (
        result["scheduler"]["backend"]
        == SchedulerBackend.WINDOWS_TASK_SCHEDULER.value
    )


def test_apply_and_toggle_publish_both_windows_payloads_after_confirmation() -> None:
    store = _Store()
    scheduler = _Scheduler()
    service = MonthlyScheduleService(store=store, scheduler=scheduler)

    applied = service.apply("SINCRONIZAR AUTOMACAO MENSAL")
    enabled = service.set_enabled(True, "SINCRONIZAR AUTOMACAO MENSAL")
    disabled = service.set_enabled(False, "SINCRONIZAR AUTOMACAO MENSAL")

    assert applied["scheduler"] == applied["windows_task"]
    assert enabled["scheduler"] == enabled["windows_task"]
    assert disabled["scheduler"] == disabled["windows_task"]
    assert scheduler.calls == [
        ("apply", MonthlyScheduleConfig.from_mapping({})),
        ("set_enabled", True),
        ("set_enabled", False),
    ]
    assert store.config.enabled is False


class _SystemdScheduler(_Scheduler):
    def query(self, config):
        self.calls.append(("query", config))
        return SchedulerState(
            backend=SchedulerBackend.SYSTEMD_USER_TIMER,
            status=SchedulerStatus.MISSING,
        )


def test_non_windows_status_does_not_publish_windows_legacy_field() -> None:
    result = MonthlyScheduleService(
        store=_Store(), scheduler=_SystemdScheduler()
    ).status()

    assert result["scheduler"]["backend"] == SchedulerBackend.SYSTEMD_USER_TIMER.value
    assert "windows_task" not in result


def test_scheduler_selector_preserves_windows_backend(tmp_path) -> None:
    scheduler = select_monthly_scheduler(
        project_root=tmp_path,
        config_path=tmp_path / "orchestration" / "clients.json",
        platform_info=RuntimePlatformInfo(
            platform=RuntimePlatform.WINDOWS,
            system_name="Windows",
            architecture="x86_64",
        ),
    )

    assert isinstance(scheduler, WindowsTaskScheduler)


def test_scheduler_selector_uses_systemd_backend_on_linux(tmp_path) -> None:
    from tenable_reports.infrastructure.systemd_user_timer_scheduler import (
        SystemdUserTimerScheduler,
    )

    scheduler = select_monthly_scheduler(
        project_root=tmp_path,
        config_path=tmp_path / "orchestration" / "clients.json",
        platform_info=RuntimePlatformInfo(
            platform=RuntimePlatform.LINUX,
            system_name="Linux",
            architecture="x86_64",
        ),
    )

    assert isinstance(scheduler, SystemdUserTimerScheduler)


def test_external_scheduler_describes_command_but_never_mutates_host() -> None:
    scheduler = ExternalScheduler(command="comando-mensal-controlado")
    config = MonthlyScheduleConfig.from_mapping({"enabled": True})

    state = scheduler.query(config)

    assert state.backend is SchedulerBackend.EXTERNAL
    assert state.status is SchedulerStatus.SYNCHRONIZED
    assert state.command == "comando-mensal-controlado"
    try:
        scheduler.apply(config)
    except RuntimeError as exc:
        assert "extern" in str(exc).lower()
    else:
        raise AssertionError("agendador externo tentou aplicar alterações")
