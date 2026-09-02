from __future__ import annotations

from contextlib import contextmanager
from uuid import UUID

from tenable_reports.application.web_batches import BatchJobResult
from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobStatus,
    BatchStatus,
)
from tenable_reports.infrastructure.web_batches_postgresql import (
    PostgresWebBatchRepository,
)


def _batch_row(
    *,
    status: str,
    requested_action: str | None = None,
    version: int = 0,
) -> tuple[object, ...]:
    return (
        UUID(int=1),
        "batch:controls:postgresql",
        "GENERATE_ALL",
        status,
        {"mode": "manual"},
        None,
        requested_action,
        version,
        "2026-08-31T12:00:00Z",
        "2026-08-31T12:00:01Z",
        None,
    )


def _job_row(*, status: str) -> tuple[object, ...]:
    return (
        UUID(int=11),
        UUID(int=1),
        "client-a",
        1,
        status,
        1,
        {"mode": "manual"},
        None,
        "worker-one",
        None,
        "C:/control/job.json",
        None,
        "logical-client-a",
        None,
        130,
        "INTERRUPTED_BY_USER",
        None,
        "2026-08-31T12:00:00Z",
        "2026-08-31T12:00:01Z",
        "2026-08-31T12:10:00Z",
        (
            "TERMINAL"
            if status in {
                "COMPLETE",
                "COMPLETE_WITH_WARNINGS",
                "FAILED",
                "INTERRUPTED",
                "CANCELLED_BY_USER",
            }
            else "LEGACY"
        ),
        None,
        None,
        None,
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


def test_postgresql_pause_waits_for_an_active_job() -> None:
    database = _Database(
        [
            _Cursor(one=_batch_row(status="RUNNING")),
            _Cursor(one=(True,)),
            _Cursor(
                one=_batch_row(
                    status="PAUSE_REQUESTED",
                    requested_action="PAUSE",
                    version=1,
                )
            ),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    batch = repository.request_action(
        UUID(int=1),
        BatchAction.PAUSE,
        actor="analista-local",
        reason="validacao operacional",
        idempotency_key="action:pause:fixture",
    )

    assert batch.status is BatchStatus.PAUSE_REQUESTED
    assert batch.requested_action is BatchAction.PAUSE
    lock_sql, _ = database.connection_value.calls[0]
    active_sql, _ = database.connection_value.calls[1]
    update_sql, update_params = database.connection_value.calls[2]
    assert "for update" in lock_sql.lower()
    assert "waiting_was_decision" in active_sql.lower()
    assert update_params[:2] == ("PAUSE_REQUESTED", "PAUSE")
    assert "version = version + 1" in update_sql.lower()
    event_sql, event_params = database.connection_value.calls[3]
    assert "actor" in event_sql.lower()
    assert "idempotency_key" in event_sql.lower()
    assert event_params[2] == "analista-local"
    assert event_params[3] == "action:pause:fixture"
    assert event_params[4].obj["reason"] == "validacao operacional"


def test_postgresql_resume_releases_only_preexisting_queued_jobs() -> None:
    database = _Database(
        [
            _Cursor(
                one=_batch_row(
                    status="PAUSED",
                    requested_action="PAUSE",
                )
            ),
            _Cursor(one=(True,)),
            _Cursor(one=_batch_row(status="RUNNING", version=1)),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    batch = repository.request_action(UUID(int=1), BatchAction.RESUME)

    assert batch.status is BatchStatus.RUNNING
    assert batch.requested_action is None
    queued_sql, _ = database.connection_value.calls[1]
    update_sql, update_params = database.connection_value.calls[2]
    assert "status = 'queued'" in queued_sql.lower()
    assert update_params[:2] == ("RUNNING", None)
    assert "web_batch_jobs" not in update_sql.lower()


def test_postgresql_stop_marks_active_and_queued_jobs_atomically() -> None:
    database = _Database(
        [
            _Cursor(one=_batch_row(status="RUNNING")),
            _Cursor(
                many=(
                    (UUID(int=11), "INTERRUPT_REQUESTED"),
                    (UUID(int=12), "CANCELLED_BY_USER"),
                )
            ),
            _Cursor(
                one=_batch_row(
                    status="STOP_REQUESTED",
                    requested_action="STOP",
                    version=1,
                )
            ),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    batch = repository.request_action(UUID(int=1), BatchAction.STOP)

    assert batch.status is BatchStatus.STOP_REQUESTED
    jobs_sql, jobs_params = database.connection_value.calls[1]
    assert "interrupt_requested" in jobs_sql.lower()
    assert "cancelled_by_user" in jobs_sql.lower()
    assert "returning id, status" in jobs_sql.lower()
    assert jobs_params == (UUID(int=1),)
    batch_sql, batch_params = database.connection_value.calls[2]
    assert batch_params[:2] == ("STOP_REQUESTED", "STOP")
    assert "ended_at" in batch_sql.lower()


def test_postgresql_stop_finishes_when_only_was_decision_is_pending() -> None:
    database = _Database(
        [
            _Cursor(one=_batch_row(status="PAUSED")),
            _Cursor(many=((UUID(int=11), "CANCELLED_BY_USER"),)),
            _Cursor(
                one=_batch_row(
                    status="STOPPED",
                    requested_action="STOP",
                    version=1,
                )
            ),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    batch = repository.request_action(UUID(int=1), BatchAction.STOP)

    assert batch.status is BatchStatus.STOPPED
    jobs_sql, _ = database.connection_value.calls[1]
    normalized_sql = " ".join(jobs_sql.lower().split())
    assert "status in ('queued', 'waiting_was_decision')" in normalized_sql
    assert "then 'cancelled_by_user'" in normalized_sql
    assert "then 'terminal'" in normalized_sql


def test_completing_interrupted_job_finishes_stop_requested_batch() -> None:
    database = _Database(
        [
            _Cursor(one=_job_row(status="INTERRUPTED")),
            _Cursor(
                one=_batch_row(
                    status="STOPPED",
                    requested_action="STOP",
                    version=2,
                )
            ),
            _Cursor(one=(1,)),
        ]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    repository.complete_job(
        UUID(int=11),
        BatchJobResult(
            status=BatchJobStatus.INTERRUPTED,
            exit_code=130,
            error_code="INTERRUPTED_BY_USER",
        ),
    )

    batch_sql, _ = database.connection_value.calls[1]
    normalized = " ".join(batch_sql.lower().split())
    assert "when batch.status = 'stop_requested' then 'stopped'" in normalized
    assert "when batch.status = 'pause_requested'" in normalized
    assert "when batch.status = 'stop_requested' then now()" in normalized

def test_postgresql_lists_active_client_conflicts_for_derived_batch() -> None:
    database = _Database(
        [_Cursor(many=(("client-b",), ("client-a",)))]
    )
    repository = PostgresWebBatchRepository(database, migrate=False)

    conflicts = repository.active_client_conflicts(
        ("client-a", "client-b", "client-c"),
        excluding_batch_id=UUID(int=1),
    )

    assert conflicts == ("client-a", "client-b")
    sql, params = database.connection_value.calls[0]
    normalized = " ".join(sql.lower().split())
    assert "select distinct client_id" in normalized
    assert "interrupt_requested" in normalized
    assert "batch_id <> %s" in normalized
    assert params == (
        ["client-a", "client-b", "client-c"],
        UUID(int=1),
    )

def test_postgresql_records_local_process_id_and_event() -> None:
    running_row = list(_job_row(status="RUNNING"))
    running_row[9] = 4242
    database = _Database([_Cursor(one=tuple(running_row)), _Cursor(one=(1,))])
    repository = PostgresWebBatchRepository(database, migrate=False)

    job = repository.record_job_process(
        UUID(int=11),
        4242,
        control_file="C:/control/job.json",
    )

    assert job.process_id == 4242
    update_sql, update_params = database.connection_value.calls[0]
    assert "set process_id = %s" in update_sql.lower()
    assert update_params == (4242, "C:/control/job.json", UUID(int=11))
    event_sql, event_params = database.connection_value.calls[1]
    assert "JOB_PROCESS_STARTED" == event_params[2]
    assert event_params[3].obj["process_id"] == 4242
