from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from tenable_reports.application.was_recovery import (
    WasFailureDetails,
    WasRecoveryCheckpoint,
    WasRecoveryDecision,
    WasRecoveryRecord,
    WasRecoveryStatus,
)
from tenable_reports.infrastructure.was_recovery_postgresql import (
    PostgresWasRecoveryRepository,
)


ROOT = Path(__file__).resolve().parents[1]


def _checkpoint(tmp_path: Path) -> WasRecoveryCheckpoint:
    return WasRecoveryCheckpoint(
        schema_version=1,
        run_id="run-1",
        client_id="client-a",
        tenant_id="tenant-a",
        execution_type="MANUAL",
        period={
            "start_at": "2026-07-01T03:00:00Z",
            "end_at": "2026-08-01T03:00:00Z",
            "timezone": "America/Fortaleza",
            "mode": "EXPLICIT_RANGE",
        },
        profile_path="clients/managed/client-a.json",
        output_root=str(tmp_path),
        include_output=False,
        was_status="UNAVAILABLE",
        was_failure=WasFailureDetails(
            code="WAS_COLLECTION_UNAVAILABLE",
            message="Falha sanitizada.",
            retryable=True,
            export_uuid="was-job",
            origin="created",
            remote_status="PROCESSING",
            total_chunks=1,
            safe_cancel_available=True,
        ),
    )


def _row(record: WasRecoveryRecord) -> tuple[object, ...]:
    return (
        record.run_id,
        record.client_id,
        record.tenant_id,
        record.status.value,
        record.checkpoint_path,
        record.checkpoint.to_dict(),
        record.decision.value if record.decision else None,
        record.idempotency_key,
        "2026-08-28T12:00:00Z",
        "2026-08-28T12:00:00Z",
        None,
    )


class _Cursor:
    def __init__(self, *, one=None, many=()) -> None:
        self.one = one
        self.many = tuple(many)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _Connection:
    def __init__(self, cursors) -> None:
        self.cursors = list(cursors)
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self.cursors.pop(0)


class _Database:
    def __init__(self, cursors) -> None:
        self.connection_value = _Connection(cursors)

    @contextmanager
    def connection(self):
        yield self.connection_value


def test_migration_creates_recovery_constraints_and_pending_index() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0008_was_recoveries.sql"
    ).read_text(encoding="utf-8")

    assert "create table if not exists tenable_reports.was_recoveries" in sql
    assert "was_recoveries_pending_idx" in sql
    assert "where status in ('WAITING_WAS_DECISION', 'RETRY_AVAILABLE')" in sql
    assert "was_recoveries_idempotency_uq" in sql
    assert "revoke all on table tenable_reports.was_recoveries from public" in sql


def test_repository_round_trips_pending_recovery(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path)
    record = WasRecoveryRecord(
        run_id=checkpoint.run_id,
        client_id=checkpoint.client_id,
        tenant_id=checkpoint.tenant_id,
        status=WasRecoveryStatus.WAITING_WAS_DECISION,
        checkpoint_path=str(tmp_path / "checkpoint.json"),
        checkpoint=checkpoint,
        created_at="2026-08-28T12:00:00Z",
        updated_at="2026-08-28T12:00:00Z",
    )
    database = _Database([
        _Cursor(one=_row(record)),
        _Cursor(one=_row(record)),
        _Cursor(many=(_row(record),)),
    ])
    repository = PostgresWasRecoveryRepository(database, migrate=False)

    assert repository.upsert(record) == record
    assert repository.get("run-1", client_id="client-a") == record
    assert repository.pending(client_id="client-a") == (record,)

    upsert_sql, _ = database.connection_value.calls[0]
    assert "on conflict (run_id) do update" in upsert_sql
    get_sql, get_params = database.connection_value.calls[1]
    assert "client_id = %s" in get_sql
    assert get_params == ("run-1", "client-a")


def test_record_decision_is_idempotent_for_same_key(tmp_path: Path) -> None:
    checkpoint = _checkpoint(tmp_path)
    decided = WasRecoveryRecord(
        run_id=checkpoint.run_id,
        client_id=checkpoint.client_id,
        tenant_id=checkpoint.tenant_id,
        status=WasRecoveryStatus.RETRYING_WAS,
        checkpoint_path=str(tmp_path / "checkpoint.json"),
        checkpoint=checkpoint,
        decision=WasRecoveryDecision.RETRY_WAS,
        idempotency_key="run-1:retry_was",
        created_at="2026-08-28T12:00:00Z",
        updated_at="2026-08-28T12:00:00Z",
    )
    database = _Database([
        _Cursor(one=_row(decided)),
        _Cursor(one=_row(decided)),
    ])
    repository = PostgresWasRecoveryRepository(database, migrate=False)

    first = repository.record_decision(
        "run-1",
        client_id="client-a",
        decision=WasRecoveryDecision.RETRY_WAS,
        idempotency_key="run-1:retry_was",
    )
    second = repository.record_decision(
        "run-1",
        client_id="client-a",
        decision=WasRecoveryDecision.RETRY_WAS,
        idempotency_key="run-1:retry_was",
    )

    assert first == second == decided
    for sql, params in database.connection_value.calls:
        assert "idempotency_key is null or idempotency_key = %s" in sql
        assert params[-1] == "run-1:retry_was"


def test_record_decision_rejects_empty_idempotency_key(tmp_path: Path) -> None:
    repository = PostgresWasRecoveryRepository(_Database([]), migrate=False)

    with pytest.raises(ValueError, match="idempotency_key"):
        repository.record_decision(
            "run-1",
            client_id="client-a",
            decision=WasRecoveryDecision.CONTINUE_WITHOUT_WAS,
            idempotency_key="",
        )
