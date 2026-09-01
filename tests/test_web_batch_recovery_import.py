from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from tenable_reports.application.web_batch_recovery_import import (
    apply_web_batch_recovery,
    plan_web_batch_recovery,
)
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import BatchJobStatus, BatchStatus


def _snapshot() -> dict[str, object]:
    jobs = []
    for position, status in enumerate(
        ("complete", "failed", "running", "queued"),
        start=1,
    ):
        jobs.append(
            {
                "job_id": str(UUID(int=position)),
                "client_id": f"client-{position}",
                "status": status,
                "run_id": f"run-{position}" if status != "queued" else None,
                "vm_export_uuid": (
                    str(UUID(int=100 + position))
                    if status != "queued"
                    else None
                ),
                "remote_status": "FINISHED" if status == "complete" else None,
                "chunks_available": 2 if status == "complete" else 0,
                "partial_manifest_present": status == "running",
                "retry_action": "retry_incomplete" if status == "failed" else None,
                "note": "estado preservado",
            }
        )
    return {
        "schema_version": 1,
        "kind": "generate_all_recovery",
        "captured_at": "2026-08-31T16:15:00Z",
        "batch_created_at": "2026-08-31T16:03:28Z",
        "period": {
            "start_at": "2026-07-01T03:00:00Z",
            "end_at": "2026-08-01T03:00:00Z",
        },
        "summary": {
            "complete": 1,
            "failed": 1,
            "running": 1,
            "queued": 1,
        },
        "jobs": jobs,
    }


def _write_snapshot(tmp_path: Path, payload: dict[str, object]) -> Path:
    path = tmp_path / "recovery.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_recovery_plan_maps_legacy_states_and_pauses_batch(tmp_path: Path) -> None:
    plan = plan_web_batch_recovery(_write_snapshot(tmp_path, _snapshot()))

    assert plan.batch.kind == "RECOVERED"
    assert plan.batch.status is BatchStatus.PAUSED
    assert plan.counts == {
        "COMPLETE": 1,
        "FAILED": 1,
        "INTERRUPTED": 1,
        "QUEUED": 1,
    }
    assert tuple(job.status for job in plan.jobs) == (
        BatchJobStatus.COMPLETE,
        BatchJobStatus.FAILED,
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.QUEUED,
    )
    interrupted = plan.jobs[2]
    assert interrupted.error_code == "RECOVERY_SNAPSHOT_INTERRUPTED"
    assert interrupted.payload["partial_manifest_present"] is True
    assert interrupted.payload["vm_export_uuid"]

    failed = plan.jobs[1]
    assert failed.payload["mode"] == "manual"
    assert failed.payload["start_at"] == "2026-07-01T03:00:00Z"
    assert failed.payload["end_at"] == "2026-08-01T03:00:00Z"
    assert failed.payload["was_failure_policy"] == "retry_then_continue"


def test_recovery_plan_normalizes_legacy_us_batch_timestamp(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["batch_created_at"] = "08/31/2026 16:03:28"

    plan = plan_web_batch_recovery(_write_snapshot(tmp_path, snapshot))

    assert plan.batch.created_at == "2026-08-31T16:03:28"
    assert {job.created_at for job in plan.jobs} == {"2026-08-31T16:03:28"}
    assert plan.event.created_at == "2026-08-31T16:15:00+00:00"


def test_recovery_plan_rejects_invalid_timestamp(tmp_path: Path) -> None:
    snapshot = _snapshot()
    snapshot["batch_created_at"] = "31/31/2026 16:03:28"

    with pytest.raises(ValueError, match="batch_created_at"):
        plan_web_batch_recovery(_write_snapshot(tmp_path, snapshot))


def test_recovery_plan_rejects_invalid_schema_and_sensitive_fields(
    tmp_path: Path,
) -> None:
    invalid = _snapshot()
    invalid["schema_version"] = 99
    with pytest.raises(ValueError, match="schema_version"):
        plan_web_batch_recovery(_write_snapshot(tmp_path, invalid))

    sensitive = _snapshot()
    sensitive["access_key"] = "must-not-be-imported"
    with pytest.raises(ValueError, match="credencial"):
        plan_web_batch_recovery(_write_snapshot(tmp_path, sensitive))


def test_recovery_plan_rejects_duplicate_clients_and_inconsistent_summary(
    tmp_path: Path,
) -> None:
    duplicate = _snapshot()
    duplicate["jobs"][1]["client_id"] = duplicate["jobs"][0]["client_id"]
    with pytest.raises(ValueError, match="duplicado"):
        plan_web_batch_recovery(_write_snapshot(tmp_path, duplicate))

    inconsistent = _snapshot()
    inconsistent["summary"]["queued"] = 9
    with pytest.raises(ValueError, match="summary"):
        plan_web_batch_recovery(_write_snapshot(tmp_path, inconsistent))


def test_recovery_apply_is_idempotent_by_snapshot_hash(tmp_path: Path) -> None:
    plan = plan_web_batch_recovery(_write_snapshot(tmp_path, _snapshot()))
    repository = InMemoryWebBatchRepository()

    first = apply_web_batch_recovery(plan, repository)
    second = apply_web_batch_recovery(plan, repository)

    assert first.id == second.id == plan.batch.id
    assert len(repository.list_batches()) == 1
    assert len(repository.list_batch_jobs(plan.batch.id)) == 4
    recovery_events = [
        event
        for event in repository.list_events(plan.batch.id)
        if event.event_type == "RECOVERY_SNAPSHOT_IMPORTED"
    ]
    assert len(recovery_events) == 1
    assert recovery_events[0].payload == {
        "snapshot_sha256": plan.snapshot_sha256,
        "counts": plan.counts,
    }
    assert "client_ids" not in recovery_events[0].payload


def test_real_recovery_snapshot_can_be_planned_without_exposing_clients() -> None:
    path = Path(
        r"C:\Codex\RelatorioTenableMensalv2\data\manual\orchestration\recovery-gerar-todos-20260831T160328Z.json"
    )
    if not path.is_file():
        pytest.skip("Snapshot operacional não está disponível neste host.")

    plan = plan_web_batch_recovery(path)

    assert plan.batch.status is BatchStatus.PAUSED
    assert sum(plan.counts.values()) == 26
    assert plan.counts == {
        "COMPLETE": 7,
        "FAILED": 8,
        "INTERRUPTED": 1,
        "QUEUED": 10,
    }

def _recovered_batch_row(plan):
    return (
        plan.batch.id,
        plan.batch.idempotency_key,
        "RECOVERED",
        "PAUSED",
        dict(plan.batch.options),
        None,
        None,
        0,
        plan.batch.created_at,
        None,
        None,
    )


def test_postgresql_recovery_import_uses_one_transaction_and_audit_event(
    tmp_path: Path,
) -> None:
    from tests.test_web_batches_postgresql import _Cursor, _Database
    from tenable_reports.infrastructure.web_batches_postgresql import (
        PostgresWebBatchRepository,
    )

    plan = plan_web_batch_recovery(_write_snapshot(tmp_path, _snapshot()))
    database = _Database(
        [_Cursor(one=_recovered_batch_row(plan))]
        + [_Cursor(one=(job.id,)) for job in plan.jobs]
        + [_Cursor(one=(1,)), _Cursor(one=(2,))]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    imported = repository.import_recovery_batch(
        plan.batch,
        plan.jobs,
        plan.event,
    )

    assert imported.id == plan.batch.id
    calls = database.connection_value.calls
    assert len(calls) == 7
    assert "insert into tenable_reports.web_batches" in calls[0][0].lower()
    assert "created_at" in calls[0][0].lower()
    assert sum(
        "insert into tenable_reports.web_batch_jobs" in sql.lower()
        for sql, _params in calls
    ) == 4
    recovery_sql, recovery_params = calls[-1]
    assert "insert into tenable_reports.web_batch_events" in recovery_sql.lower()
    assert recovery_params[2] == "RECOVERY_SNAPSHOT_IMPORTED"
    assert recovery_params[4] == plan.event.idempotency_key


def test_postgresql_recovery_reapply_returns_existing_without_new_jobs(
    tmp_path: Path,
) -> None:
    from tests.test_web_batches_postgresql import _Cursor, _Database
    from tenable_reports.infrastructure.web_batches_postgresql import (
        PostgresWebBatchRepository,
    )

    plan = plan_web_batch_recovery(_write_snapshot(tmp_path, _snapshot()))
    database = _Database(
        [_Cursor(one=None), _Cursor(one=_recovered_batch_row(plan))]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    imported = repository.import_recovery_batch(
        plan.batch,
        plan.jobs,
        plan.event,
    )

    assert imported.id == plan.batch.id
    assert len(database.connection_value.calls) == 2
