"""PostgreSQL adapter for durable web batches and their audit events."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

from tenable_reports.application.web_batches import (
    BatchJobResult,
    WebBatchRepository,
    assert_sanitized_payload,
    normalize_claim_phases,
    validate_collection_checkpoint_path,
)
from tenable_reports.domain.web_batches import (
    BATCH_JOB_TERMINAL_STATUSES,
    BatchAction,
    BatchJobPhase,
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
    error_message, created_at, started_at, ended_at, phase,
    collection_checkpoint_path, remote_started_at, remote_ended_at,
    build_started_at
"""


_JOB_COLUMNS_AS_JOB = ", ".join(
    f"job.{column.strip()}"
    for column in _JOB_COLUMNS.split(",")
    if column.strip()
)


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
        phase=BatchJobPhase(str(row[20])),
        collection_checkpoint_path=(
            str(row[21]) if row[21] is not None else None
        ),
        remote_started_at=_iso(row[22]),
        remote_ended_at=_iso(row[23]),
        build_started_at=_iso(row[24]),
    )


def _event_from_row(row: Sequence[Any]) -> WebBatchEvent:
    return WebBatchEvent(
        batch_id=UUID(str(row[0])),
        job_id=UUID(str(row[1])) if row[1] is not None else None,
        event_type=str(row[2]),
        actor=str(row[3]) if row[3] is not None else None,
        idempotency_key=str(row[4]) if row[4] is not None else None,
        payload=dict(row[5] or {}),
        created_at=_iso(row[6]),
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
                        logical_job_id, phase, collection_checkpoint_path,
                        remote_started_at, remote_ended_at, build_started_at
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
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
                        job.phase.value,
                        job.collection_checkpoint_path,
                        job.remote_started_at,
                        job.remote_ended_at,
                        job.build_started_at,
                    ),
                ).fetchone()
        return _batch_from_row(row)

    def import_recovery_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
        event: WebBatchEvent,
    ) -> WebBatch:
        """Persist one recovered batch, its jobs and audit in one transaction."""

        assert_sanitized_payload(batch.options, path="batch.options")
        assert_sanitized_payload(event.payload, path="event.payload")
        if event.batch_id != batch.id:
            raise ValueError("O evento de recuperacao pertence a outro lote.")
        if any(job.batch_id != batch.id for job in jobs):
            raise ValueError("Todos os trabalhos precisam pertencer ao lote recuperado.")
        for job in jobs:
            assert_sanitized_payload(job.payload, path="job.payload")

        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batches (
                    id, idempotency_key, kind, status, options,
                    source_batch_id, requested_action, created_at,
                    started_at, ended_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (idempotency_key) do nothing
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
                    batch.created_at,
                    batch.started_at,
                    batch.ended_at,
                ),
            ).fetchone()
            if row is None:
                existing = connection.execute(
                    f"""
                    select {_BATCH_COLUMNS}
                    from {SCHEMA_NAME}.web_batches
                    where idempotency_key = %s
                    """,
                    (batch.idempotency_key,),
                ).fetchone()
                if existing is None or UUID(str(existing[0])) != batch.id:
                    raise ValueError("Chave idempotente ja pertence a outro lote.")
                return _batch_from_row(existing)

            for job in jobs:
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.web_batch_jobs (
                        id, batch_id, client_id, position, status,
                        attempt_number, payload, retry_of_batch_job_id,
                        logical_job_id, run_id, exit_code, error_code,
                        error_message, created_at, started_at, ended_at
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
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
                        job.run_id,
                        job.exit_code,
                        job.error_code,
                        job.error_message,
                        job.created_at,
                        job.started_at,
                        job.ended_at,
                    ),
                ).fetchone()
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, event_type, actor, idempotency_key,
                    payload, created_at
                ) values (%s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    batch.id,
                    "BATCH_CREATED",
                    event.actor,
                    f"{event.idempotency_key}:batch-created",
                    _jsonb({"job_count": len(jobs), "kind": batch.kind}),
                    batch.created_at,
                ),
            ).fetchone()
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, job_id, event_type, actor,
                    idempotency_key, payload, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s)
                returning id
                """,
                (
                    event.batch_id,
                    event.job_id,
                    event.event_type,
                    event.actor,
                    event.idempotency_key,
                    _jsonb(dict(event.payload)),
                    event.created_at,
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

    def record_job_process(
        self,
        job_id: UUID,
        process_id: int,
        *,
        control_file: str | None = None,
    ) -> WebBatchJob:
        normalized_process_id = int(process_id)
        if normalized_process_id <= 0:
            raise ValueError("process_id deve ser positivo.")
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_jobs
                set process_id = %s,
                    control_file = coalesce(%s, control_file)
                where id = %s
                returning {_JOB_COLUMNS}
                """,
                (normalized_process_id, control_file, job_id),
            ).fetchone()
            if row is None:
                raise KeyError("Trabalho de lote nao encontrado.")
            job = _job_from_row(row)
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, job_id, event_type, payload
                ) values (%s, %s, %s, %s)
                returning id
                """,
                (
                    job.batch_id,
                    job.id,
                    "JOB_PROCESS_STARTED",
                    _jsonb({"process_id": normalized_process_id}),
                ),
            ).fetchone()
        return job

    def request_action(
        self,
        batch_id: UUID,
        action: BatchAction,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatch:
        normalized_actor = str(actor or "").strip()[:200] or None
        normalized_reason = str(reason or "").strip()[:500]
        normalized_key = str(idempotency_key or "").strip()[:200] or None
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select {_BATCH_COLUMNS}
                from {SCHEMA_NAME}.web_batches
                where id = %s
                for update
                """,
                (batch_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Lote nao encontrado.")
            batch = _batch_from_row(row)

            if action is BatchAction.PAUSE:
                if batch.status in {
                    BatchStatus.PAUSE_REQUESTED,
                    BatchStatus.PAUSED,
                }:
                    return batch
                if batch.status not in {
                    BatchStatus.QUEUED,
                    BatchStatus.RUNNING,
                }:
                    raise ValueError("O lote nao pode ser pausado neste estado.")
                active_row = connection.execute(
                    f"""
                    select exists (
                        select 1
                        from {SCHEMA_NAME}.web_batch_jobs
                        where batch_id = %s
                          and status in ('RUNNING', 'WAITING_WAS_DECISION')
                    )
                    """,
                    (batch_id,),
                ).fetchone()
                has_active = bool(active_row and active_row[0])
                next_status = (
                    BatchStatus.PAUSE_REQUESTED
                    if has_active
                    else BatchStatus.PAUSED
                )
            elif action is BatchAction.RESUME:
                if (
                    batch.status is BatchStatus.RUNNING
                    and batch.requested_action is None
                ):
                    return batch
                if batch.status is not BatchStatus.PAUSED:
                    raise ValueError("O lote nao pode ser retomado neste estado.")
                queued_row = connection.execute(
                    f"""
                    select exists (
                        select 1
                        from {SCHEMA_NAME}.web_batch_jobs
                        where batch_id = %s and status = 'QUEUED'
                    )
                    """,
                    (batch_id,),
                ).fetchone()
                if not bool(queued_row and queued_row[0]):
                    raise ValueError(
                        "O lote nao possui trabalhos pendentes para retomar."
                    )
                next_status = BatchStatus.RUNNING
            elif action is BatchAction.STOP:
                if batch.status in {
                    BatchStatus.STOP_REQUESTED,
                    BatchStatus.STOPPED,
                }:
                    return batch
                if batch.status not in {
                    BatchStatus.QUEUED,
                    BatchStatus.RUNNING,
                    BatchStatus.PAUSE_REQUESTED,
                    BatchStatus.PAUSED,
                }:
                    raise ValueError("O lote nao pode ser parado neste estado.")
                changed_jobs = connection.execute(
                    f"""
                    update {SCHEMA_NAME}.web_batch_jobs
                    set status = case
                            when status in ('QUEUED', 'WAITING_WAS_DECISION')
                                then 'CANCELLED_BY_USER'
                            else 'INTERRUPT_REQUESTED'
                        end,
                        phase = case
                            when status in ('QUEUED', 'WAITING_WAS_DECISION')
                                then 'TERMINAL'
                            else phase
                        end,
                        worker_id = case
                            when status in ('QUEUED', 'WAITING_WAS_DECISION')
                                then null
                            else worker_id
                        end,
                        process_id = case
                            when status in ('QUEUED', 'WAITING_WAS_DECISION')
                                then null
                            else process_id
                        end,
                        control_file = case
                            when status in ('QUEUED', 'WAITING_WAS_DECISION')
                                then null
                            else control_file
                        end,
                        ended_at = case
                            when status in ('QUEUED', 'WAITING_WAS_DECISION')
                                then now()
                            else ended_at
                        end
                    where batch_id = %s
                      and status in (
                          'QUEUED', 'RUNNING', 'WAITING_WAS_DECISION',
                          'INTERRUPT_REQUESTED'
                      )
                    returning id, status
                    """,
                    (batch_id,),
                ).fetchall()
                has_active = any(
                    str(changed[1]) == BatchJobStatus.INTERRUPT_REQUESTED.value
                    for changed in changed_jobs
                )
                next_status = (
                    BatchStatus.STOP_REQUESTED
                    if has_active
                    else BatchStatus.STOPPED
                )
            else:
                raise ValueError(
                    f"Acao de lote ainda nao suportada: {action}."
                )

            requested_action = (
                None if action is BatchAction.RESUME else action.value
            )
            updated_row = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batches
                set status = %s, requested_action = %s,
                    ended_at = case
                        when %s = 'STOPPED' then now()
                        else ended_at
                    end,
                    version = version + 1
                where id = %s
                returning {_BATCH_COLUMNS}
                """,
                (
                    next_status.value,
                    requested_action,
                    next_status.value,
                    batch_id,
                ),
            ).fetchone()
            if updated_row is None:
                raise KeyError("Lote nao encontrado.")
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, event_type, actor, idempotency_key, payload
                ) values (%s, %s, %s, %s, %s)
                on conflict (idempotency_key) where idempotency_key is not null
                do nothing
                returning id
                """,
                (
                    batch_id,
                    "BATCH_ACTION_APPLIED",
                    normalized_actor,
                    normalized_key,
                    _jsonb(
                        {
                            "action": action.value,
                            "status": next_status.value,
                            "reason": normalized_reason,
                        }
                    ),
                ),
            ).fetchone()
        return _batch_from_row(updated_row)

    def active_client_conflicts(
        self,
        client_ids: Sequence[str],
        *,
        excluding_batch_id: UUID,
    ) -> tuple[str, ...]:
        normalized = sorted(
            {
                str(client_id).strip()
                for client_id in client_ids
                if str(client_id).strip()
            }
        )
        if not normalized:
            return ()
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select distinct client_id
                from {SCHEMA_NAME}.web_batch_jobs
                where client_id = any(%s)
                  and batch_id <> %s
                  and status in (
                      'QUEUED', 'RUNNING', 'WAITING_WAS_DECISION',
                      'INTERRUPT_REQUESTED'
                  )
                order by client_id
                """,
                (normalized, excluding_batch_id),
            ).fetchall()
        return tuple(sorted(str(row[0]) for row in rows))

    def claim_next_job(
        self,
        *,
        worker_id: str,
        phases: Sequence[BatchJobPhase] | None = None,
    ) -> WebBatchJob | None:
        normalized_worker = str(worker_id or "").strip()
        if not normalized_worker:
            raise ValueError("worker_id nao pode ser vazio.")
        normalized_phases = normalize_claim_phases(phases)
        phase_values = [phase.value for phase in normalized_phases]
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                with next_job as (
                    select job.id
                    from {SCHEMA_NAME}.web_batch_jobs job
                    join {SCHEMA_NAME}.web_batches batch
                      on batch.id = job.batch_id
                    where job.status = 'QUEUED'
                      and job.phase = any(%s)
                      and batch.status in ('QUEUED', 'RUNNING')
                      and batch.requested_action is null
                    order by batch.created_at, job.position, job.id
                    for update skip locked
                    limit 1
                )
                update {SCHEMA_NAME}.web_batch_jobs job
                set status = 'RUNNING', worker_id = %s,
                    phase = case
                        when job.phase = 'REMOTE_QUEUED' then 'REMOTE_RUNNING'
                        when job.phase = 'READY_FOR_BUILD' then 'BUILD_RUNNING'
                        else job.phase
                    end,
                    started_at = coalesce(job.started_at, now()),
                    remote_started_at = case
                        when job.phase = 'REMOTE_QUEUED'
                            then coalesce(job.remote_started_at, now())
                        else job.remote_started_at
                    end,
                    build_started_at = case
                        when job.phase = 'READY_FOR_BUILD'
                            then coalesce(job.build_started_at, now())
                        else job.build_started_at
                    end
                from next_job
                where job.id = next_job.id
                returning {_JOB_COLUMNS_AS_JOB}
                """,
                (phase_values, normalized_worker),
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
                (
                    job.batch_id,
                    job.id,
                    (
                        "BUILD_STARTED"
                        if job.phase is BatchJobPhase.BUILD_RUNNING
                        else "JOB_STARTED"
                    ),
                    _jsonb({"worker_id": normalized_worker}),
                ),
            ).fetchone()
        return job

    def advance_job_phase(
        self,
        job_id: UUID,
        *,
        expected_phase: BatchJobPhase,
        requested_phase: BatchJobPhase,
        collection_checkpoint_path: str | Path | None = None,
    ) -> WebBatchJob:
        if (
            expected_phase is not BatchJobPhase.REMOTE_RUNNING
            or requested_phase is not BatchJobPhase.READY_FOR_BUILD
        ):
            raise ValueError("Transicao de fase de trabalho invalida.")
        checkpoint_path = validate_collection_checkpoint_path(
            collection_checkpoint_path
        )
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_jobs
                set status = 'QUEUED', phase = %s,
                    collection_checkpoint_path = %s,
                    remote_ended_at = now(), worker_id = null,
                    process_id = null, ended_at = null
                where id = %s and status = 'RUNNING' and phase = %s
                returning {_JOB_COLUMNS}
                """,
                (
                    requested_phase.value,
                    checkpoint_path,
                    job_id,
                    expected_phase.value,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("O trabalho nao esta na fase remota esperada.")
            job = _job_from_row(row)
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.web_batch_events (
                    batch_id, job_id, event_type, payload
                ) values (%s, %s, %s, %s)
                returning id
                """,
                (
                    job.batch_id,
                    job.id,
                    "COLLECTION_READY",
                    _jsonb({"phase": requested_phase.value}),
                ),
            ).fetchone()
        return job

    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None:
        assert_sanitized_payload(result.payload, path="job.result")
        if result.status in BATCH_JOB_TERMINAL_STATUSES:
            phase_sql = "'TERMINAL'"
        elif result.status is BatchJobStatus.WAITING_WAS_DECISION:
            phase_sql = (
                "case when phase = 'REMOTE_RUNNING' "
                "then 'REMOTE_WAITING_DECISION' else phase end"
            )
        else:
            phase_sql = "phase"
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_jobs
                set status = %s, phase = {phase_sql},
                    exit_code = %s, error_code = %s,
                    error_message = %s, payload = payload || %s,
                    ended_at = case
                        when %s = 'WAITING_WAS_DECISION' then null
                        else now()
                    end
                where id = %s
                returning {_JOB_COLUMNS}
                """,
                (
                    result.status.value,
                    result.exit_code,
                    result.error_code,
                    result.error_message,
                    _jsonb(dict(result.payload)),
                    result.status.value,
                    job_id,
                ),
            ).fetchone()
            if row is None:
                raise KeyError("Trabalho de lote nao encontrado.")
            job = _job_from_row(row)
            connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batches batch
                set status = case
                        when batch.status = 'STOP_REQUESTED' then 'STOPPED'
                        when batch.status = 'PAUSE_REQUESTED'
                             and exists (
                                select 1
                                from {SCHEMA_NAME}.web_batch_jobs child
                                where child.batch_id = batch.id
                                  and child.status = 'QUEUED'
                             ) then 'PAUSED'
                        when exists (
                            select 1 from {SCHEMA_NAME}.web_batch_jobs child
                            where child.batch_id = batch.id
                              and child.status in ('WAITING_WAS_DECISION', 'INTERRUPTED')
                        ) then 'PAUSED'
                        when exists (
                            select 1 from {SCHEMA_NAME}.web_batch_jobs child
                            where child.batch_id = batch.id
                              and child.status in ('QUEUED', 'RUNNING', 'INTERRUPT_REQUESTED')
                        ) then 'RUNNING'
                        when exists (
                            select 1 from {SCHEMA_NAME}.web_batch_jobs child
                            where child.batch_id = batch.id and child.status = 'FAILED'
                        ) then 'COMPLETE_WITH_FAILURES'
                        when exists (
                            select 1 from {SCHEMA_NAME}.web_batch_jobs child
                            where child.batch_id = batch.id
                              and child.status = 'COMPLETE_WITH_WARNINGS'
                        ) then 'COMPLETE_WITH_WARNINGS'
                        else 'COMPLETE'
                    end,
                    ended_at = case
                        when batch.status = 'STOP_REQUESTED' then now()
                        when exists (
                            select 1 from {SCHEMA_NAME}.web_batch_jobs child
                            where child.batch_id = batch.id
                              and child.status in (
                                  'QUEUED', 'RUNNING', 'WAITING_WAS_DECISION',
                                  'INTERRUPT_REQUESTED', 'INTERRUPTED'
                              )
                        ) then null
                        else now()
                    end,
                    version = version + 1
                where id = %s
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
                (
                    job.batch_id,
                    job.id,
                    "JOB_FINISHED",
                    _jsonb({"status": result.status.value}),
                ),
            ).fetchone()

    def reconcile_abandoned_jobs(
        self,
        *,
        active_worker_ids: set[str],
    ) -> int:
        active_workers = sorted(
            str(worker).strip()
            for worker in active_worker_ids
            if str(worker).strip()
        )
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_jobs
                set status = case
                        when status = 'INTERRUPT_REQUESTED' then 'INTERRUPTED'
                        when phase in ('REMOTE_RUNNING', 'BUILD_RUNNING')
                            then 'QUEUED'
                        else 'INTERRUPTED'
                    end,
                    phase = case
                        when status = 'INTERRUPT_REQUESTED' then 'TERMINAL'
                        when phase = 'REMOTE_RUNNING' then 'REMOTE_QUEUED'
                        when phase = 'BUILD_RUNNING' then 'READY_FOR_BUILD'
                        else phase
                    end,
                    worker_id = null,
                    process_id = null, control_file = null,
                    ended_at = case
                        when status = 'INTERRUPT_REQUESTED' or phase = 'LEGACY'
                            then now()
                        else null
                    end,
                    error_code = case
                        when status = 'INTERRUPT_REQUESTED'
                            then 'INTERRUPTED_BY_USER'
                        when phase = 'LEGACY' then 'LOCAL_WORKER_RESTARTED'
                        else null
                    end,
                    error_message = case
                        when status = 'INTERRUPT_REQUESTED'
                            then 'Execucao local interrompida por solicitacao do usuario.'
                        when phase = 'LEGACY'
                            then 'Execucao local interrompida por reinicio.'
                        else null
                    end
                where status in ('RUNNING', 'INTERRUPT_REQUESTED')
                  and (
                      status = 'INTERRUPT_REQUESTED'
                      or phase in ('LEGACY', 'REMOTE_RUNNING', 'BUILD_RUNNING')
                  )
                  and (worker_id is null or worker_id <> all(%s))
                returning {_JOB_COLUMNS}
                """,
                (active_workers,),
            ).fetchall()
            jobs = tuple(_job_from_row(row) for row in rows)
            for job in jobs:
                if job.phase in {
                    BatchJobPhase.REMOTE_QUEUED,
                    BatchJobPhase.READY_FOR_BUILD,
                }:
                    connection.execute(
                        f"""
                        insert into {SCHEMA_NAME}.web_batch_events (
                            batch_id, job_id, event_type, payload
                        ) values (%s, %s, %s, %s)
                        returning id
                        """,
                        (
                            job.batch_id,
                            job.id,
                            "JOB_REQUEUED_AFTER_RESTART",
                            _jsonb({"phase": job.phase.value}),
                        ),
                    ).fetchone()
                    continue
                connection.execute(
                    f"""
                    update {SCHEMA_NAME}.web_batches
                    set status = case
                            when status = 'STOP_REQUESTED' then 'STOPPED'
                            else 'PAUSED'
                        end,
                        ended_at = case
                            when status = 'STOP_REQUESTED' then now()
                            else ended_at
                        end,
                        version = version + 1
                    where id = %s
                      and status not in (
                          'STOPPED', 'COMPLETE', 'COMPLETE_WITH_FAILURES',
                          'COMPLETE_WITH_WARNINGS'
                      )
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
                    (
                        job.batch_id,
                        job.id,
                        "JOB_RECOVERED_AS_INTERRUPTED",
                        _jsonb({"previous_worker_id": job.worker_id}),
                    ),
                ).fetchone()
            paused_rows = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batches batch
                set status = 'PAUSED', version = version + 1
                where batch.status = 'QUEUED'
                  and exists (
                      select 1 from {SCHEMA_NAME}.web_batch_jobs job
                      where job.batch_id = batch.id and job.status = 'QUEUED'
                  )
                returning batch.id
                """
            ).fetchall()
            for paused_row in paused_rows:
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.web_batch_events (
                        batch_id, event_type, payload
                    ) values (%s, %s, %s)
                    returning id
                    """,
                    (
                        UUID(str(paused_row[0])),
                        "BATCH_RECOVERED_PAUSED",
                        _jsonb({"reason": "local_worker_restart"}),
                    ),
                ).fetchone()
            return len(jobs)

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
                select batch_id, job_id, event_type, actor, idempotency_key,
                       payload, created_at
                from {SCHEMA_NAME}.web_batch_events
                where batch_id = %s
                order by id
                """,
                (batch_id,),
            ).fetchall()
        return tuple(_event_from_row(row) for row in rows)


__all__ = ["PostgresWebBatchRepository"]
