from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from tenable_reports.application.monthly_batch import MonthlyBatchRequest, monthly_idempotency_key
from tenable_reports.config.monthly_schedule import MonthlyScheduleConfig
from tenable_reports.infrastructure.monthly_scheduler import SchedulerBackend


MONTHLY_SCHEDULE_CONFIRMATION = "SINCRONIZAR AUTOMACAO MENSAL"


def _next_execution(config: MonthlyScheduleConfig, *, now: datetime | None = None) -> datetime:
    zone = ZoneInfo("America/Fortaleza")
    current = (now or datetime.now(zone)).astimezone(zone)
    hour, minute = (int(part) for part in config.local_start_time.split(":"))
    candidate = current.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= current:
        following = (candidate.replace(day=28) + timedelta(days=4)).replace(day=1)
        candidate = following.replace(hour=hour, minute=minute)
    return candidate


class MonthlyScheduleService:
    def __init__(self, *, store: Any, scheduler: Any) -> None:
        self.store = store
        self.scheduler = scheduler

    def _preview(self, config: MonthlyScheduleConfig) -> dict[str, Any]:
        next_run = _next_execution(config)
        raw = self.store.raw()
        orchestration_id = str(raw.get("orchestration_id") or "carteira-tenable")
        competence = MonthlyBatchRequest(reference_at=next_run).competence
        clients = self.store.list_clients()
        eligible = config.eligible_client_ids(clients)
        return {
            "next_run_at": next_run.isoformat(),
            "competence": competence,
            "idempotency_key": monthly_idempotency_key(orchestration_id, competence),
            "eligible_client_ids": eligible,
            "eligible_client_count": len(eligible),
            "clients": [
                {
                    "client_id": row["client_id"],
                    "display_name": row.get("display_name") or row["client_id"],
                    "enabled": bool(row.get("enabled")),
                    "credentials_ready": bool(row.get("credentials_ready")),
                }
                for row in clients
            ],
        }

    @staticmethod
    def _scheduler_payload(state: Any) -> dict[str, Any]:
        mapping = state.to_mapping()
        payload: dict[str, Any] = {"scheduler": mapping}
        if mapping.get("backend") == SchedulerBackend.WINDOWS_TASK_SCHEDULER.value:
            payload["windows_task"] = mapping
        return payload

    def status(self) -> dict[str, Any]:
        config = self.store.monthly_schedule()
        state = self.scheduler.query(config)
        return {
            "config": config.to_mapping(),
            **self._scheduler_payload(state),
            "confirmation": MONTHLY_SCHEDULE_CONFIRMATION,
            **self._preview(config),
        }

    def validate(self) -> dict[str, Any]:
        config = self.store.monthly_schedule()
        return {"config": config.to_mapping(), **self._preview(config)}

    def save(self, values: Mapping[str, Any]) -> dict[str, Any]:
        config = MonthlyScheduleConfig.from_mapping(
            {**self.store.monthly_schedule().to_mapping(), **values}
        )
        if "selected_client_ids" in values and config.selected_client_ids is not None:
            known = {row["client_id"] for row in self.store.list_clients()}
            if any(client_id not in known for client_id in config.selected_client_ids):
                raise ValueError("A seleção contém clientes que não estão cadastrados.")
        self.store.save_monthly_schedule(config)
        return {"config": config.to_mapping(), **self._preview(config)}

    @staticmethod
    def _confirm(value: str) -> None:
        if str(value or "").strip() != MONTHLY_SCHEDULE_CONFIRMATION:
            raise ValueError(
                f'Digite exatamente "{MONTHLY_SCHEDULE_CONFIRMATION}" para confirmar.'
            )

    def apply(self, confirmation: str) -> dict[str, Any]:
        self._confirm(confirmation)
        config = self.store.monthly_schedule()
        state = self.scheduler.apply(config)
        return {
            "config": config.to_mapping(),
            **self._scheduler_payload(state),
            **self._preview(config),
        }

    def set_enabled(self, enabled: bool, confirmation: str) -> dict[str, Any]:
        self._confirm(confirmation)
        current = self.store.monthly_schedule()
        config = replace(current, enabled=bool(enabled))
        state = self.scheduler.set_enabled(config, bool(enabled))
        self.store.save_monthly_schedule(config)
        return {
            "config": config.to_mapping(),
            **self._scheduler_payload(state),
            **self._preview(config),
        }
