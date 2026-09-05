from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from tenable_reports.application.monthly_batch import (
    MonthlyBatchRequest,
    monthly_idempotency_key,
    run_monthly_batch,
)


class FakeApplication:
    def __init__(self) -> None:
        self.calls = []
        self.config = SimpleNamespace(
            raw=lambda: {"orchestration_id": "carteira-tenable"},
            list_clients=lambda: [
                {"client_id": "a", "enabled": True, "credentials_ready": True},
                {"client_id": "b", "enabled": False, "credentials_ready": True},
            ],
        )
        self.jobs = SimpleNamespace(
            repository=SimpleNamespace(list_batches=lambda limit=500: ()),
            wait_until_idle=lambda timeout: True,
            batch_family_snapshot=lambda batch_id: {
                "root_batch_id": batch_id,
                "counts": {"complete": 1, "failed": 0, "partial": 0},
            },
        )

    def enqueue_jobs(self, client_ids, request):
        self.calls.append((tuple(client_ids), dict(request)))
        return [{"batch_id": "00000000-0000-0000-0000-000000000123"}]


def test_monthly_request_uses_previous_calendar_month_per_client_timezone() -> None:
    request = MonthlyBatchRequest(reference_at="2026-09-01T00:05:00-03:00")
    period = request.period_for("America/Fortaleza")
    zone = ZoneInfo("America/Fortaleza")
    assert period.start_at.astimezone(zone) == datetime(2026, 8, 1, tzinfo=zone)
    assert period.end_at.astimezone(zone) == datetime(2026, 9, 1, tzinfo=zone)
    assert request.competence == "2026-08"


def test_monthly_key_is_stable_for_orchestration_and_competence() -> None:
    assert monthly_idempotency_key("carteira-tenable", "2026-08") == (
        "automatic-monthly:carteira-tenable:2026-08"
    )


def test_monthly_batch_enqueues_only_eligible_clients_with_durable_identity() -> None:
    app = FakeApplication()
    result = run_monthly_batch(
        MonthlyBatchRequest(reference_at="2026-09-01T00:05:00-03:00"),
        application=app,
        wait=False,
    )
    assert result.root_batch_id == "00000000-0000-0000-0000-000000000123"
    assert app.calls[0][0] == ("a",)
    request = app.calls[0][1]
    assert request["mode"] == "automatic"
    assert request["run_scope"] == "all"
    assert request["_batch_idempotency_key"] == (
        "automatic-monthly:carteira-tenable:2026-08"
    )
    assert request["_batch_competence"] == "2026-08"
    assert request["_batch_origin"] == "AUTOMATIC_MONTHLY"
