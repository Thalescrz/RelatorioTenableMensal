from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from tenable_reports.application.web_batches import BatchJobResult

from tenable_reports.domain.web_batches import (
    BatchJobPhase,
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
        self.connection_calls = 0

    @contextmanager
    def connection(self):
        self.connection_calls += 1
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


def _job_row(
    *,
    status: str = "QUEUED",
    phase: str = "LEGACY",
    checkpoint_path: str | None = None,
) -> tuple[object, ...]:
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
        phase,
        checkpoint_path,
        "2026-08-31T12:00:01Z" if phase == "REMOTE_RUNNING" else None,
        "2026-08-31T12:10:00Z" if phase == "READY_FOR_BUILD" else None,
        "2026-08-31T12:10:01Z" if phase == "BUILD_RUNNING" else None,
        None,
        None,
        None,
        None,
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


def test_phase_migration_preserves_legacy_rows_and_adds_claim_index() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0011_web_batch_job_phases.sql"
    ).read_text(encoding="utf-8")

    assert "add column if not exists phase" in sql.lower()
    assert "default 'LEGACY'" in sql
    assert "REMOTE_QUEUED" in sql
    assert "REMOTE_RUNNING" in sql
    assert "REMOTE_WAITING_DECISION" in sql
    assert "READY_FOR_BUILD" in sql
    assert "BUILD_RUNNING" in sql
    assert "TERMINAL" in sql
    assert "collection_checkpoint_path" in sql
    assert "remote_started_at" in sql
    assert "remote_ended_at" in sql
    assert "build_started_at" in sql
    assert "web_batch_jobs_phase_status_created_idx" in sql


def test_vm_recovery_migration_adds_durable_remote_fields() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0012_vm_export_recovery.sql"
    ).read_text(encoding="utf-8")
    for field in (
        "vm_export_uuid",
        "vm_resume_manifest_path",
        "remote_export_started_at",
        "remote_status_at",
        "remote_progress_at",
    ):
        assert f"add column if not exists {field}" in sql.lower()


def test_partial_component_status_migration_extends_job_constraint() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0013_partial_component_status.sql"
    ).read_text(encoding="utf-8")

    assert "drop constraint if exists web_batch_jobs_status_check" in sql
    assert "'PARTIALLY_COMPLETE'" in sql


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
    assert job_params[9] == "LEGACY"


def test_repository_persists_and_returns_manual_selection_options() -> None:
    options = {
        "selected_client_ids": ["client-a"],
        "excluded_client_ids": ["client-b"],
        "analyst_snapshot_by_client": {
            "client-a": {
                "analyst_id": "analyst-1",
                "display_name": "Analista Um",
                "active": True,
            }
        },
        "selection_filter_snapshot": {
            "analyst_id": "analyst-1",
            "query": "",
            "unassigned": False,
        },
    }
    returned_row = list(_batch_row())
    returned_row[4] = options
    database = _Database([_Cursor(one=tuple(returned_row))])
    repository = PostgresWebBatchRepository(database, migrate=False)
    batch = WebBatch(
        id=UUID(int=1),
        idempotency_key="batch:create:one",
        kind="GENERATE_ALL",
        status=BatchStatus.QUEUED,
        options=options,
        created_at="2026-08-31T12:00:00Z",
    )

    returned = repository.create_batch(batch, ())

    _batch_sql, batch_params = database.connection_value.calls[0]
    assert batch_params[4].obj == options
    assert returned.options == options


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


def test_repository_lists_jobs_for_batches_in_one_query() -> None:
    batch_a = UUID(int=1)
    batch_b = UUID(int=2)
    unknown = UUID(int=3)
    second = list(_job_row())
    second[0] = UUID(int=12)
    second[2] = "client-b"
    second[3] = 2
    other = list(_job_row())
    other[0] = UUID(int=21)
    other[1] = batch_b
    other[2] = "client-c"
    database = _Database([_Cursor(many=(_job_row(), tuple(second), tuple(other)))])
    repository = PostgresWebBatchRepository(database, migrate=False)

    jobs_by_batch = repository.list_batch_jobs_for_batches(
        (batch_a, batch_b, unknown)
    )

    assert tuple(job.position for job in jobs_by_batch[batch_a]) == (1, 2)
    assert tuple(job.client_id for job in jobs_by_batch[batch_b]) == ("client-c",)
    assert jobs_by_batch[unknown] == ()
    assert database.connection_calls == 1
    assert len(database.connection_value.calls) == 1
    sql, params = database.connection_value.calls[0]
    assert "where batch_id = any(%s)" in " ".join(sql.lower().split())
    assert "order by batch_id, position, id" in " ".join(sql.lower().split())
    assert params == ([batch_a, batch_b, unknown],)


def test_repository_bulk_job_list_skips_connection_for_empty_ids() -> None:
    database = _Database([])
    repository = PostgresWebBatchRepository(database, migrate=False)

    assert repository.list_batch_jobs_for_batches(()) == {}
    assert database.connection_calls == 0


def test_repository_replaces_expired_vm_uuid_and_resets_remote_budget() -> None:
    replacement_uuid = "00000000-0000-0000-0000-000000000703"
    returned_row = list(_job_row(status="RUNNING", phase="REMOTE_RUNNING"))
    returned_row[25] = replacement_uuid
    returned_row[26] = "C:/fixture/replacement.partial.json"
    returned_row[27] = "2026-09-03T23:00:00Z"
    returned_row[28] = None
    returned_row[29] = None
    database = _Database([_Cursor(one=tuple(returned_row))])
    repository = PostgresWebBatchRepository(database, migrate=False)

    replaced = repository.record_vm_export_replacement(
        UUID(int=11),
        previous_export_uuid="00000000-0000-0000-0000-000000000702",
        replacement_export_uuid=replacement_uuid,
        resume_manifest_path="C:/fixture/replacement.partial.json",
        origin="created",
        observed_at="2026-09-03T23:00:00Z",
    )

    sql, params = database.connection_value.calls[0]
    normalized_sql = " ".join(sql.lower().split())
    assert "set vm_export_uuid = %s" in normalized_sql
    assert "remote_export_started_at = %s" in normalized_sql
    assert "remote_status_at = null" in normalized_sql
    assert "remote_progress_at = null" in normalized_sql
    assert "and vm_export_uuid = %s" in normalized_sql
    assert params[-2:] == (
        UUID(int=11),
        "00000000-0000-0000-0000-000000000702",
    )
    assert replaced.vm_export_uuid == replacement_uuid
    assert replaced.remote_export_started_at == "2026-09-03T23:00:00Z"
    assert replaced.remote_status_at is None
    assert replaced.remote_progress_at is None


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
    assert "returning job.id, job.batch_id" in " ".join(
        claim_sql.lower().split()
    )
    assert claim_params == (["LEGACY"], "worker-one")
    event_sql, event_params = database.connection_value.calls[2]
    assert "insert into tenable_reports.web_batch_events" in event_sql
    assert event_params[1] == claimed.id
    assert event_params[2] == "JOB_STARTED"


def test_repository_claims_only_requested_remote_phase() -> None:
    database = _Database(
        [
            _Cursor(
                one=_job_row(status="RUNNING", phase="REMOTE_RUNNING")
            ),
            _Cursor(one=_batch_row(status="RUNNING")),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    claimed = repository.claim_next_job(
        worker_id="remote-worker",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
    )

    assert claimed is not None
    assert claimed.phase is BatchJobPhase.REMOTE_RUNNING
    claim_sql, claim_params = database.connection_value.calls[0]
    assert "job.phase = any(%s)" in " ".join(claim_sql.lower().split())
    assert "'REMOTE_RUNNING'" in claim_sql
    assert claim_params == (["REMOTE_QUEUED"], "remote-worker")
    _, event_params = database.connection_value.calls[2]
    assert event_params[2] == "JOB_STARTED"


def test_repository_claims_ready_job_for_build_and_records_build_event() -> None:
    database = _Database(
        [
            _Cursor(one=_job_row(status="RUNNING", phase="BUILD_RUNNING")),
            _Cursor(one=_batch_row(status="RUNNING")),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    claimed = repository.claim_next_job(
        worker_id="build-worker",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
    )

    assert claimed is not None
    assert claimed.phase is BatchJobPhase.BUILD_RUNNING
    _, event_params = database.connection_value.calls[2]
    assert event_params[2] == "BUILD_STARTED"


def test_repository_advances_collection_ready_in_one_transaction(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "collection-checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    database = _Database(
        [
            _Cursor(
                one=_job_row(
                    status="QUEUED",
                    phase="READY_FOR_BUILD",
                    checkpoint_path=str(checkpoint.resolve()),
                )
            ),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    advanced = repository.advance_job_phase(
        UUID(int=11),
        expected_phase=BatchJobPhase.REMOTE_RUNNING,
        requested_phase=BatchJobPhase.READY_FOR_BUILD,
        collection_checkpoint_path=checkpoint,
    )

    assert advanced.status is BatchJobStatus.QUEUED
    assert advanced.phase is BatchJobPhase.READY_FOR_BUILD
    assert advanced.collection_checkpoint_path == str(checkpoint.resolve())
    advance_sql, advance_params = database.connection_value.calls[0]
    assert "status = 'QUEUED'" in advance_sql
    assert "remote_ended_at = now()" in advance_sql
    assert advance_params == (
        "READY_FOR_BUILD",
        str(checkpoint.resolve()),
        UUID(int=11),
        "REMOTE_RUNNING",
    )
    _, event_params = database.connection_value.calls[1]
    assert event_params[2] == "COLLECTION_READY"


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
                        "worker-local",
                        "event:progress:fixture",
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
    assert events[0].actor == "worker-local"
    assert events[0].idempotency_key == "event:progress:fixture"
    list_sql, list_params = database.connection_value.calls[1]
    assert "order by id" in list_sql.lower()
    assert list_params == (UUID(int=1),)


def test_repository_lists_events_for_batches_in_one_query() -> None:
    batch_a = UUID(int=1)
    batch_b = UUID(int=2)
    unknown = UUID(int=3)
    rows = (
        (
            batch_a,
            UUID(int=11),
            "JOB_STARTED",
            "worker-local",
            None,
            {},
            "2026-08-31T12:00:00Z",
        ),
        (
            batch_b,
            UUID(int=21),
            "JOB_PROGRESS",
            "worker-local",
            None,
            {"completed_chunks": 1},
            "2026-08-31T12:01:00Z",
        ),
    )
    database = _Database([_Cursor(many=rows)])
    repository = PostgresWebBatchRepository(database, migrate=False)

    events_by_batch = repository.list_events_for_batches(
        (batch_a, batch_b, unknown)
    )

    assert tuple(event.event_type for event in events_by_batch[batch_a]) == (
        "JOB_STARTED",
    )
    assert tuple(event.event_type for event in events_by_batch[batch_b]) == (
        "JOB_PROGRESS",
    )
    assert events_by_batch[unknown] == ()
    assert database.connection_calls == 1
    assert len(database.connection_value.calls) == 1
    sql, params = database.connection_value.calls[0]
    assert "where batch_id = any(%s)" in " ".join(sql.lower().split())
    assert "order by batch_id, created_at, id" in " ".join(sql.lower().split())
    assert params == ([batch_a, batch_b, unknown],)


def test_repository_bulk_event_list_skips_connection_for_empty_ids() -> None:
    database = _Database([])
    repository = PostgresWebBatchRepository(database, migrate=False)

    assert repository.list_events_for_batches(()) == {}
    assert database.connection_calls == 0


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


def test_repository_completes_job_and_derives_terminal_batch() -> None:
    database = _Database(
        [
            _Cursor(one=_job_row(status="COMPLETE", phase="TERMINAL")),
            _Cursor(one=_batch_row(status="COMPLETE")),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    repository.complete_job(
        UUID(int=11),
        BatchJobResult(
            status=BatchJobStatus.COMPLETE,
            exit_code=0,
            payload={"run_id": "run-fixture"},
        ),
    )

    job_sql, job_params = database.connection_value.calls[0]
    assert "update tenable_reports.web_batch_jobs" in job_sql.lower()
    assert "payload || %s" in job_sql.lower()
    assert "phase = 'TERMINAL'" in job_sql
    assert job_params[0] == "COMPLETE"
    batch_sql, _ = database.connection_value.calls[1]
    assert "complete_with_failures" in batch_sql.lower()
    assert "complete_with_warnings" in batch_sql.lower()
    event_sql, event_params = database.connection_value.calls[2]
    assert "insert into tenable_reports.web_batch_events" in event_sql.lower()
    assert event_params[2] == "JOB_FINISHED"


def test_repository_reconciles_jobs_owned_by_inactive_workers() -> None:
    database = _Database(
        [
            _Cursor(many=(_job_row(status="INTERRUPTED"),)),
            _Cursor(one=_batch_row(status="PAUSED")),
            _Cursor(one=(1,)),
            _Cursor(many=()),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    reconciled = repository.reconcile_abandoned_jobs(
        active_worker_ids={"new-worker"}
    )

    assert reconciled == 1
    reconcile_sql, reconcile_params = database.connection_value.calls[0]
    assert "else 'interrupted'" in reconcile_sql.lower()
    assert "interrupt_requested" in reconcile_sql.lower()
    assert "worker_id <> all" in reconcile_sql.lower()
    assert reconcile_params == (["new-worker"],)
    event_sql, event_params = database.connection_value.calls[2]
    assert event_params[2] == "JOB_RECOVERED_AS_INTERRUPTED"


def test_repository_finishes_abandoned_stop_requested_batch() -> None:
    database = _Database(
        [
            _Cursor(many=(_job_row(status="INTERRUPTED"),)),
            _Cursor(one=_batch_row(status="STOPPED")),
            _Cursor(one=(1,)),
            _Cursor(many=()),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    reconciled = repository.reconcile_abandoned_jobs(active_worker_ids=set())

    assert reconciled == 1
    reconcile_sql, _ = database.connection_value.calls[0]
    assert "interrupt_requested" in reconcile_sql.lower()
    batch_sql, _ = database.connection_value.calls[1]
    normalized_sql = " ".join(batch_sql.lower().split())
    assert "when status = 'stop_requested' then 'stopped'" in normalized_sql
    assert "ended_at" in normalized_sql


def test_repository_requeues_abandoned_remote_and_build_jobs(
    tmp_path: Path,
) -> None:
    checkpoint = tmp_path / "collection-checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    remote_row = _job_row(status="QUEUED", phase="REMOTE_QUEUED")
    build_row = list(
        _job_row(
            status="QUEUED",
            phase="READY_FOR_BUILD",
            checkpoint_path=str(checkpoint.resolve()),
        )
    )
    build_row[0] = UUID(int=12)
    build_row[2] = "client-b"
    build_row[3] = 2
    database = _Database(
        [
            _Cursor(many=(remote_row, tuple(build_row))),
            _Cursor(one=(1,)),
            _Cursor(one=(1,)),
            _Cursor(many=()),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    reconciled = repository.reconcile_abandoned_jobs(
        active_worker_ids={"current-worker"}
    )

    assert reconciled == 2
    reconcile_sql, _ = database.connection_value.calls[0]
    assert "REMOTE_RUNNING" in reconcile_sql
    assert "BUILD_RUNNING" in reconcile_sql
    assert "REMOTE_QUEUED" in reconcile_sql
    assert "READY_FOR_BUILD" in reconcile_sql
    first_event = database.connection_value.calls[1][1]
    second_event = database.connection_value.calls[2][1]
    assert first_event[2] == "JOB_REQUEUED_AFTER_RESTART"
    assert second_event[2] == "JOB_REQUEUED_AFTER_RESTART"


def test_repository_pauses_preexisting_queued_batches_on_startup() -> None:
    database = _Database(
        [
            _Cursor(many=()),
            _Cursor(many=((UUID(int=1),),)),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    reconciled = repository.reconcile_abandoned_jobs(active_worker_ids=set())

    assert reconciled == 0
    pause_sql, _ = database.connection_value.calls[1]
    assert "set status = 'paused'" in pause_sql.lower()
    assert "batch.status = 'queued'" in pause_sql.lower()
    event_sql, event_params = database.connection_value.calls[2]
    assert "insert into tenable_reports.web_batch_events" in event_sql.lower()
    assert event_params[1] == "BATCH_RECOVERED_PAUSED"
