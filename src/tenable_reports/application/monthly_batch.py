from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tenable_reports.domain.reporting import ReportingPeriod, previous_calendar_month


def monthly_idempotency_key(orchestration_id: str, competence: str) -> str:
    normalized_orchestration = str(orchestration_id or "").strip()
    normalized_competence = str(competence or "").strip()
    if not normalized_orchestration or not normalized_competence:
        raise ValueError("Orquestração e competência são obrigatórias.")
    return f"automatic-monthly:{normalized_orchestration}:{normalized_competence}"


@dataclass(frozen=True, slots=True)
class MonthlyBatchRequest:
    reference_at: str | datetime | None = None
    timezone: str = "America/Fortaleza"

    def period_for(self, timezone_name: str) -> ReportingPeriod:
        return previous_calendar_month(
            timezone_name=timezone_name,
            reference_at=self.reference_at,
        )

    @property
    def competence(self) -> str:
        return self.period_for(self.timezone).period_id


@dataclass(frozen=True, slots=True)
class MonthlyBatchResult:
    root_batch_id: str
    idempotency_key: str
    competence: str
    reused: bool
    snapshot: dict[str, Any] | None = None


def run_monthly_batch(
    request: MonthlyBatchRequest,
    *,
    application: Any,
    wait: bool = True,
    wait_timeout_seconds: float = 108_300,
) -> MonthlyBatchResult:
    raw = application.config.raw()
    orchestration_id = str(raw.get("orchestration_id") or "carteira-tenable")
    key = monthly_idempotency_key(orchestration_id, request.competence)
    repository = getattr(application.jobs, "repository", None)
    existing = next(
        (
            batch
            for batch in (repository.list_batches(limit=500) if repository else ())
            if batch.idempotency_key == key
        ),
        None,
    )
    reused = existing is not None
    if existing is not None:
        batch_id = str(existing.root_batch_id or existing.id)
    else:
        clients = [
            row["client_id"]
            for row in application.config.list_clients()
            if row.get("enabled") and row.get("credentials_ready")
        ]
        if not clients:
            raise ValueError("Nenhum cliente ativo com credenciais prontas.")
        created = application.enqueue_jobs(
            clients,
            {
                "mode": "automatic",
                "run_scope": "all",
                "reference_at": (
                    request.reference_at.isoformat()
                    if isinstance(request.reference_at, datetime)
                    else request.reference_at
                ),
                "_batch_idempotency_key": key,
                "_batch_origin": "AUTOMATIC_MONTHLY",
                "_batch_competence": request.competence,
            },
        )
        if not created:
            raise RuntimeError("O lote mensal não criou trabalhos.")
        batch_id = str(created[0]["batch_id"])
    if wait and not application.jobs.wait_until_idle(timeout=wait_timeout_seconds):
        raise TimeoutError("O lote mensal permanece ativo após a janela coordenada.")
    snapshot_fn = getattr(application.jobs, "batch_family_snapshot", None)
    snapshot = snapshot_fn(batch_id) if callable(snapshot_fn) else None
    return MonthlyBatchResult(
        root_batch_id=batch_id,
        idempotency_key=key,
        competence=request.competence,
        reused=reused,
        snapshot=snapshot,
    )
