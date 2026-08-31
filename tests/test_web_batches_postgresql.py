from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)
from tenable_reports.infrastructure.postgresql import PostgresDatabase
from tenable_reports.infrastructure.web_batches_postgresql import (
    PostgresWebBatchRepository,
)


ROOT = Path(__file__).resolve().parents[1]


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


def _batch_row(*, status: str = "QUEUED") -> tuple[object, ...]:
    return (
        UUID(int=1),
        "batch:create:one",
        "GENERATE_ALL",
        status,
        {"mode": "manual", "client_ids": ["client-a"]},
        None,
        None,
        0,
        "2026-08-31T12:00:00Z",
        None,
        None,
    )


def _job_row(*, status: str = "QUEUED") -> tuple[object, ...]:
    return (
        UUID(int=11),
        UUID(int=1),
        "client-a",
        1,
        status,
        1,
        {"mode": "manual"},
        None,
        "worker-one" if status == "RUNNING" else None,
        None,
        None,
        None,
        "logical-client-a",
        None,
        None,
        None,
        None,
        "2026-08-31T12:00:00Z",
        "2026-08-31T12:00:01Z" if status == "RUNNING" else None,
        None,
    )


def test_migration_creates_durable_batch_tables_constraints_and_indexes() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0009_web_batches.sql"
    ).read_text(encoding="utf-8")

    assert "create table if not exists tenable_reports.web_batches" in sql
    assert "create table if not exists tenable_reports.web_batch_jobs" in sql
    assert "create table if not exists tenable_reports.web_batch_events" in sql
    assert "web_batches_idempotency_uq" in sql
    assert "web_batch_jobs_active_client_uq" in sql
    assert "unique (batch_id, client_id)" in sql
    assert "'INTERRUPT_REQUESTED'" in sql
    assert "revoke all on table tenable_reports.web_batches from public" in sql
    assert "revoke all on table tenable_reports.web_batch_jobs from public" in sql
    assert "revoke all on table tenable_reports.web_batch_events from public" in sql


def test_repository_creates_batch_and_jobs_in_one_connection() -> None:
    database = _Database(
        [
            _Cursor(one=_batch_row()),
            _Cursor(one=(UUID(int=11),)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)
    batch = WebBatch(
        id=UUID(int=1),
        idempotency_key="batch:create:one",
        kind="GENERATE_ALL",
        status=BatchStatus.QUEUED,
        options={"mode": "manual", "client_ids": ["client-a"]},
        created_at="2026-08-31T12:00:00Z",
    )
    job = WebBatchJob(
        id=UUID(int=11),
        batch_id=batch.id,
        client_id="client-a",
        position=1,
        status=BatchJobStatus.QUEUED,
        attempt_number=1,
        payload={"mode": "manual"},
        logical_job_id="logical-client-a",
    )

    assert repository.create_batch(batch, (job,)) == batch

    batch_sql, batch_params = database.connection_value.calls[0]
    job_sql, job_params = database.connection_value.calls[1]
    assert "on conflict (idempotency_key) do update" in batch_sql
    assert batch_params[0] == batch.id
    assert "insert into tenable_reports.web_batch_jobs" in job_sql
    assert job_params[2:5] == ("client-a", 1, "QUEUED")


def test_repository_lists_jobs_in_original_position_order() -> None:
    second = list(_job_row())
    second[0] = UUID(int=12)
    second[2] = "client-b"
    second[3] = 2
    database = _Database([_Cursor(many=(_job_row(), tuple(second)))])
    repository = PostgresWebBatchRepository(database, migrate=False)

    jobs = repository.list_batch_jobs(UUID(int=1))

    assert tuple(job.client_id for job in jobs) == ("client-a", "client-b")
    sql, params = database.connection_value.calls[0]
    assert "order by position" in sql.lower()
    assert params == (UUID(int=1),)


def test_repository_claims_one_job_with_skip_locked_and_records_event() -> None:
    database = _Database(
        [
            _Cursor(one=_job_row(status="RUNNING")),
            _Cursor(one=_batch_row(status="RUNNING")),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    claimed = repository.claim_next_job(worker_id="worker-one")

    assert claimed is not None
    assert claimed.status is BatchJobStatus.RUNNING
    assert claimed.worker_id == "worker-one"
    claim_sql, claim_params = database.connection_value.calls[0]
    assert "for update skip locked" in claim_sql.lower()
    assert "update tenable_reports.web_batch_jobs" in claim_sql.lower()
    assert claim_params == ("worker-one",)
    event_sql, event_params = database.connection_value.calls[2]
    assert "insert into tenable_reports.web_batch_events" in event_sql
    assert event_params[1] == claimed.id
    assert event_params[2] == "JOB_STARTED"


def test_repository_appends_and_lists_immutable_events() -> None:
    database = _Database(
        [
            _Cursor(one=(1,)),
            _Cursor(
                many=(
                    (
                        UUID(int=1),
                        UUID(int=11),
                        "JOB_PROGRESS",
                        {"completed_chunks": 2, "total_chunks": 3},
                        "2026-08-31T12:05:00Z",
                    ),
                )
            ),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)
    event = WebBatchEvent(
        batch_id=UUID(int=1),
        job_id=UUID(int=11),
        event_type="JOB_PROGRESS",
        payload={"completed_chunks": 2, "total_chunks": 3},
    )

    repository.append_event(event)
    events = repository.list_events(UUID(int=1))

    assert len(events) == 1
    assert events[0].event_type == "JOB_PROGRESS"
    assert events[0].payload == {"completed_chunks": 2, "total_chunks": 3}
    list_sql, list_params = database.connection_value.calls[1]
    assert "order by id" in list_sql.lower()
    assert list_params == (UUID(int=1),)


@pytest.mark.parametrize(
    "unsafe_options",
    (
        {"access_key": "fixture-value"},
        {"nested": {"password": "fixture-value"}},
        {"items": [{"api_token": "fixture-value"}]},
    ),
)
def test_repository_rejects_credentials_before_persisting_batch(
    unsafe_options: dict[str, object],
) -> None:
    database = _Database([])
    repository = PostgresWebBatchRepository(database, migrate=False)
    batch = WebBatch(
        id=UUID(int=1),
        idempotency_key="batch:unsafe",
        kind="GENERATE_ONE",
        status=BatchStatus.QUEUED,
        options=unsafe_options,
    )

    with pytest.raises(ValueError, match="credencial"):
        repository.create_batch(batch, ())

    assert database.connection_value.calls == []


class _StatusConnection:
    def __init__(self) -> None:
        self.counted_tables: list[str] = []

    def execute(self, sql, params=None):
        normalized = " ".join(str(sql).split()).lower()
        if "select current_database()" in normalized:
            return _Cursor(one=("fixture-db", "fixture-user", "17.0"))
        if "schema_migrations" in normalized:
            return _Cursor(many=())
        if "select count(*) from tenable_reports." in normalized:
            table = normalized.rsplit(".", 1)[-1]
            self.counted_tables.append(table)
            return _Cursor(one=(0,))
        raise AssertionError(f"SQL inesperado no status: {sql}")


class _StatusDatabase(PostgresDatabase):
    def __init__(self) -> None:
        super().__init__(SimpleNamespace(safe_location="postgresql://fixture"))
        self.connection_value = _StatusConnection()

    @contextmanager
    def connection(self, *, autocommit: bool = False):
        yield self.connection_value


def test_database_status_includes_durable_batch_tables() -> None:
    database = _StatusDatabase()

    status = database.status()

    assert status["counts"]["web_batches"] == 0
    assert status["counts"]["web_batch_jobs"] == 0
    assert status["counts"]["web_batch_events"] == 0
    assert {
        "web_batches",
        "web_batch_jobs",
        "web_batch_events",
    }.issubset(set(database.connection_value.counted_tables))
