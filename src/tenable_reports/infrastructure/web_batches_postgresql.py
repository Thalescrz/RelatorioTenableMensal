"""PostgreSQL adapter for durable web batches and their audit events."""

from __future__ import annotations

from typing import Any, Sequence
from uuid import UUID

from tenable_reports.application.web_batches import (
    WebBatchRepository,
    assert_sanitized_payload,
)
from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _jsonb,
)


_BATCH_COLUMNS = """
    id, idempotency_key, kind, status, options, source_batch_id,
    requested_action, version, created_at, started_at, ended_at
"""

_JOB_COLUMNS = """
    id, batch_id, client_id, position, status, attempt_number, payload,
    retry_of_batch_job_id, worker_id, process_id, control_file,
    orchestration_run_id, logical_job_id, run_id, exit_code, error_code,
    error_message, created_at, started_at, ended_at
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _batch_from_row(row: Sequence[Any]) -> WebBatch:
    return WebBatch(
        id=UUID(str(row[0])),
        idempotency_key=str(row[1]),
        kind=str(row[2]),
        status=BatchStatus(str(row[3])),
        options=dict(row[4] or {}),
        source_batch_id=UUID(str(row[5])) if row[5] is not None else None,
        requested_action=(
            BatchAction(str(row[6])) if row[6] is not None else None
        ),
        version=int(row[7]),
        created_at=_iso(row[8]),
        started_at=_iso(row[9]),
        ended_at=_iso(row[10]),
    )


def _job_from_row(row: Sequence[Any]) -> WebBatchJob:
    return WebBatchJob(
        id=UUID(str(row[0])),
        batch_id=UUID(str(row[1])),
        client_id=str(row[2]),
        position=int(row[3]),
        status=BatchJobStatus(str(row[4])),
        attempt_number=int(row[5]),
        payload=dict(row[6] or {}),
        retry_of_batch_job_id=(
            UUID(str(row[7])) if row[7] is not None else None
        ),
        worker_id=str(row[8]) if row[8] is not None else None,
        process_id=int(row[9]) if row[9] is not None else None,
        control_file=str(row[10]) if row[10] is not None else None,
        orchestration_run_id=str(row[11]) if row[11] is not None else None,
        logical_job_id=str(row[12]) if row[12] is not None else None,
        run_id=str(row[13]) if row[13] is not None else None,
        exit_code=int(row[14]) if row[14] is not None else None,
        error_code=str(row[15]) if row[15] is not None else None,
        error_message=str(row[16]) if row[16] is not None else None,
        created_at=_iso(row[17]),
        started_at=_iso(row[18]),
        ended_at=_iso(row[19]),
    )


def _event_from_row(row: Sequence[Any]) -> WebBatchEvent:
    return WebBatchEvent(
        batch_id=UUID(str(row[0])),
        job_id=UUID(str(row[1])) if row[1] is not None else None,
        event_type=str(row[2]),
        payload=dict(row[3] or {}),
        created_at=_iso(row[4]),
    )


class PostgresWebBatchRepository(WebBatchRepository):
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        migrate: bool = True,
    ) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def create_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
    ) -> WebBatch:
        assert_sanitized_payload(batch.options, path="batch.options")
        for job in jobs:
            assert_sanitized_payload(job.payload, path="job.payload")
        if any(job.batch_id != batch.id for job in jobs):
            raise ValueError("Todos os trabalhos precisam pertencer ao lote criado.")
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batches (
                    id, idempotency_key, kind, status, options,
                    source_batch_id, requested_action
                ) values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (idempotency_key) do update set
                    idempotency_key = excluded.idempotency_key
                where {SCHEMA_NAME}.web_batches.id = excluded.id
                returning {_BATCH_COLUMNS}
                """,
                (
                    batch.id,
                    batch.idempotency_key,
                    batch.kind,
                    batch.status.value,
                    _jsonb(dict(batch.options)),
                    batch.source_batch_id,
                    batch.requested_action.value if batch.requested_action else None,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("Chave idempotente ja pertence a outro lote.")
            for job in jobs:
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.web_batch_jobs (
                        id, batch_id, client_id, position, status,
                        attempt_number, payload, retry_of_batch_job_id,
                        logical_job_id
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (id) do nothing
                    returning id
                    """,
                    (
                        job.id,
                        job.batch_id,
                        job.client_id,
                        job.position,
                        job.status.value,
                        job.attempt_number,
                        _jsonb(dict(job.payload)),
                        job.retry_of_batch_job_id,
                        job.logical_job_id,
                    ),
                ).fetchone()
        return _batch_from_row(row)

    def get_batch(self, batch_id: UUID) -> WebBatch | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"select {_BATCH_COLUMNS} from {SCHEMA_NAME}.web_batches where id = %s",
                (batch_id,),
            ).fetchone()
        return _batch_from_row(row) if row is not None else None

    def list_batches(self, *, limit: int = 50) -> tuple[WebBatch, ...]:
        normalized_limit = max(1, min(int(limit), 500))
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select {_BATCH_COLUMNS}
                from {SCHEMA_NAME}.web_batches
                order by created_at desc, id
                limit %s
                """,
                (normalized_limit,),
            ).fetchall()
        return tuple(_batch_from_row(row) for row in rows)

    def list_batch_jobs(self, batch_id: UUID) -> tuple[WebBatchJob, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select {_JOB_COLUMNS}
                from {SCHEMA_NAME}.web_batch_jobs
                where batch_id = %s
                order by position
                """,
                (batch_id,),
            ).fetchall()
        return tuple(_job_from_row(row) for row in rows)

    def claim_next_job(self, *, worker_id: str) -> WebBatchJob | None:
        normalized_worker = str(worker_id or "").strip()
        if not normalized_worker:
            raise ValueError("worker_id nao pode ser vazio.")
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                with next_job as (
                    select job.id
                    from {SCHEMA_NAME}.web_batch_jobs job
                    join {SCHEMA_NAME}.web_batches batch
                      on batch.id = job.batch_id
                    where job.status = 'QUEUED'
                      and batch.status in ('QUEUED', 'RUNNING')
                      and batch.requested_action is null
                    order by batch.created_at, job.position, job.id
                    for update skip locked
                    limit 1
                )
                update {SCHEMA_NAME}.web_batch_jobs job
                set status = 'RUNNING', worker_id = %s,
                    started_at = coalesce(job.started_at, now())
                from next_job
                where job.id = next_job.id
                returning {_JOB_COLUMNS}
                """,
                (normalized_worker,),
            ).fetchone()
            if row is None:
                return None
            job = _job_from_row(row)
            connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batches
                set status = 'RUNNING', started_at = coalesce(started_at, now()),
                    version = version + 1
                where id = %s and status in ('QUEUED', 'RUNNING')
                returning {_BATCH_COLUMNS}
                """,
                (job.batch_id,),
            ).fetchone()
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, job_id, event_type, payload
                ) values (%s, %s, %s, %s)
                returning id
                """,
                (job.batch_id, job.id, "JOB_STARTED", _jsonb({"worker_id": normalized_worker})),
            ).fetchone()
        return job

    def append_event(self, event: WebBatchEvent) -> None:
        assert_sanitized_payload(event.payload, path="event.payload")
        with self.database.connection() as connection:
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, job_id, event_type, actor,
                    idempotency_key, payload
                ) values (%s, %s, %s, %s, %s, %s)
                on conflict (idempotency_key) where idempotency_key is not null
                do nothing
                returning id
                """,
                (
                    event.batch_id,
                    event.job_id,
                    event.event_type,
                    event.actor,
                    event.idempotency_key,
                    _jsonb(dict(event.payload)),
                ),
            ).fetchone()

    def list_events(self, batch_id: UUID) -> tuple[WebBatchEvent, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select batch_id, job_id, event_type, payload, created_at
                from {SCHEMA_NAME}.web_batch_events
                where batch_id = %s
                order by id
                """,
                (batch_id,),
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)


__all__ = ["PostgresWebBatchRepository"]
