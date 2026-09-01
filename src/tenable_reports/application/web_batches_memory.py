"""Thread-safe in-memory implementation used by deterministic offline tests."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Sequence
from uuid import UUID

from tenable_reports.application.web_batches import (
    BatchJobResult,
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


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class InMemoryWebBatchRepository(WebBatchRepository):
    """Real repository semantics without a database or fallback role in production."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._batches: dict[UUID, WebBatch] = {}
        self._jobs: dict[UUID, WebBatchJob] = {}
        self._events: list[WebBatchEvent] = []
        self._idempotency: dict[str, UUID] = {}

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
        client_ids = [job.client_id for job in jobs]
        positions = [job.position for job in jobs]
        if len(set(client_ids)) != len(client_ids):
            raise ValueError("Cliente duplicado no lote.")
        if len(set(positions)) != len(positions):
            raise ValueError("Posicao duplicada no lote.")
        with self._lock:
            existing_id = self._idempotency.get(batch.idempotency_key)
            if existing_id is not None:
                if existing_id != batch.id:
                    raise ValueError("Chave idempotente ja pertence a outro lote.")
                return self._batches[existing_id]
            active_clients = {
                item.client_id
                for item in self._jobs.values()
                if item.status
                in {
                    BatchJobStatus.QUEUED,
                    BatchJobStatus.RUNNING,
                    BatchJobStatus.WAITING_WAS_DECISION,
                    BatchJobStatus.INTERRUPT_REQUESTED,
                }
            }
            conflict = active_clients.intersection(client_ids)
            if conflict:
                raise ValueError("Cliente ja possui trabalho ativo em outro lote.")
            stored_batch = replace(batch, created_at=batch.created_at or _now())
            self._batches[batch.id] = stored_batch
            self._idempotency[batch.idempotency_key] = batch.id
            for job in jobs:
                self._jobs[job.id] = replace(
                    job,
                    created_at=job.created_at or stored_batch.created_at,
                )
            self._events.append(
                WebBatchEvent(
                    batch_id=batch.id,
                    event_type="BATCH_CREATED",
                    payload={"job_count": len(jobs)},
                    created_at=stored_batch.created_at,
                )
            )
            return stored_batch

    def get_batch(self, batch_id: UUID) -> WebBatch | None:
        with self._lock:
            return self._batches.get(batch_id)

    def list_batches(self, *, limit: int = 50) -> tuple[WebBatch, ...]:
        with self._lock:
            ordered = sorted(
                self._batches.values(),
                key=lambda item: (item.created_at or "", str(item.id)),
                reverse=True,
            )
            return tuple(ordered[: max(1, min(int(limit), 500))])

    def list_batch_jobs(self, batch_id: UUID) -> tuple[WebBatchJob, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        job
                        for job in self._jobs.values()
                        if job.batch_id == batch_id
                    ),
                    key=lambda job: (job.position, str(job.id)),
                )
            )

    def request_action(
        self,
        batch_id: UUID,
        action: BatchAction,
    ) -> WebBatch:
        with self._lock:
            batch = self._batches[batch_id]
            jobs = tuple(
                job for job in self._jobs.values() if job.batch_id == batch_id
            )
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
                has_active = any(
                    job.status
                    in {
                        BatchJobStatus.RUNNING,
                        BatchJobStatus.WAITING_WAS_DECISION,
                    }
                    for job in jobs
                )
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
                if not any(job.status is BatchJobStatus.QUEUED for job in jobs):
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
                has_active = False
                for job in jobs:
                    if job.status in {
                        BatchJobStatus.RUNNING,
                        BatchJobStatus.WAITING_WAS_DECISION,
                        BatchJobStatus.INTERRUPT_REQUESTED,
                    }:
                        has_active = True
                        if job.status is not BatchJobStatus.INTERRUPT_REQUESTED:
                            self._jobs[job.id] = replace(
                                job,
                                status=BatchJobStatus.INTERRUPT_REQUESTED,
                            )
                    elif job.status is BatchJobStatus.QUEUED:
                        self._jobs[job.id] = replace(
                            job,
                            status=BatchJobStatus.CANCELLED_BY_USER,
                            ended_at=_now(),
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

            changed_at = _now()
            updated = replace(
                batch,
                status=next_status,
                requested_action=(
                    None if action is BatchAction.RESUME else action
                ),
                ended_at=(
                    changed_at
                    if next_status is BatchStatus.STOPPED
                    else batch.ended_at
                ),
                version=batch.version + 1,
            )
            self._batches[batch_id] = updated
            self._events.append(
                WebBatchEvent(
                    batch_id=batch_id,
                    event_type="BATCH_ACTION_APPLIED",
                    payload={
                        "action": action.value,
                        "status": next_status.value,
                    },
                    created_at=changed_at,
                )
            )
            return updated

    def active_client_conflicts(
        self,
        client_ids: Sequence[str],
        *,
        excluding_batch_id: UUID,
    ) -> tuple[str, ...]:
        requested = {str(client_id) for client_id in client_ids}
        active_statuses = {
            BatchJobStatus.QUEUED,
            BatchJobStatus.RUNNING,
            BatchJobStatus.WAITING_WAS_DECISION,
            BatchJobStatus.INTERRUPT_REQUESTED,
        }
        with self._lock:
            return tuple(
                sorted(
                    {
                        job.client_id
                        for job in self._jobs.values()
                        if (
                            job.batch_id != excluding_batch_id
                            and job.client_id in requested
                            and job.status in active_statuses
                        )
                    }
                )
            )

    def claim_next_job(self, *, worker_id: str) -> WebBatchJob | None:
        normalized_worker = str(worker_id or "").strip()
        if not normalized_worker:
            raise ValueError("worker_id nao pode ser vazio.")
        with self._lock:
            candidates = [
                job
                for job in self._jobs.values()
                if job.status is BatchJobStatus.QUEUED
                and self._batches[job.batch_id].status
                in {BatchStatus.QUEUED, BatchStatus.RUNNING}
                and self._batches[job.batch_id].requested_action is None
            ]
            if not candidates:
                return None
            job = min(
                candidates,
                key=lambda item: (
                    self._batches[item.batch_id].created_at or "",
                    item.position,
                    str(item.id),
                ),
            )
            started_at = _now()
            claimed = replace(
                job,
                status=BatchJobStatus.RUNNING,
                worker_id=normalized_worker,
                started_at=job.started_at or started_at,
            )
            self._jobs[job.id] = claimed
            batch = self._batches[job.batch_id]
            self._batches[job.batch_id] = replace(
                batch,
                status=BatchStatus.RUNNING,
                started_at=batch.started_at or started_at,
                version=batch.version + 1,
            )
            self._events.append(
                WebBatchEvent(
                    batch_id=job.batch_id,
                    job_id=job.id,
                    event_type="JOB_STARTED",
                    payload={"worker_id": normalized_worker},
                    created_at=started_at,
                )
            )
            return claimed

    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None:
        assert_sanitized_payload(result.payload, path="job.result")
        with self._lock:
            current = self._jobs[job_id]
            ended_at = _now()
            completed = replace(
                current,
                status=result.status,
                exit_code=result.exit_code,
                error_code=result.error_code,
                error_message=result.error_message,
                payload={**dict(current.payload), **dict(result.payload)},
                ended_at=(
                    None
                    if result.status is BatchJobStatus.WAITING_WAS_DECISION
                    else ended_at
                ),
            )
            self._jobs[job_id] = completed
            batch_jobs = self.list_batch_jobs(current.batch_id)
            batch = self._batches[current.batch_id]
            next_status = batch.status
            if batch.status is BatchStatus.STOP_REQUESTED:
                next_status = BatchStatus.STOPPED
            elif (
                batch.status is BatchStatus.PAUSE_REQUESTED
                and any(
                    job.status is BatchJobStatus.QUEUED for job in batch_jobs
                )
            ):
                next_status = BatchStatus.PAUSED
            elif result.status in {
                BatchJobStatus.WAITING_WAS_DECISION,
                BatchJobStatus.INTERRUPTED,
            }:
                next_status = BatchStatus.PAUSED
            elif not any(
                job.status
                in {
                    BatchJobStatus.QUEUED,
                    BatchJobStatus.RUNNING,
                    BatchJobStatus.INTERRUPT_REQUESTED,
                }
                for job in batch_jobs
            ):
                if any(job.status is BatchJobStatus.FAILED for job in batch_jobs):
                    next_status = BatchStatus.COMPLETE_WITH_FAILURES
                elif any(
                    job.status is BatchJobStatus.COMPLETE_WITH_WARNINGS
                    for job in batch_jobs
                ):
                    next_status = BatchStatus.COMPLETE_WITH_WARNINGS
                else:
                    next_status = BatchStatus.COMPLETE
            self._batches[current.batch_id] = replace(
                batch,
                status=next_status,
                ended_at=(
                    ended_at
                    if next_status
                    in {
                        BatchStatus.STOPPED,
                        BatchStatus.COMPLETE,
                        BatchStatus.COMPLETE_WITH_FAILURES,
                        BatchStatus.COMPLETE_WITH_WARNINGS,
                    }
                    else batch.ended_at
                ),
                version=batch.version + 1,
            )
            self._events.append(
                WebBatchEvent(
                    batch_id=current.batch_id,
                    job_id=current.id,
                    event_type="JOB_FINISHED",
                    payload={"status": result.status.value},
                    created_at=ended_at,
                )
            )

    def append_event(self, event: WebBatchEvent) -> None:
        assert_sanitized_payload(event.payload, path="event.payload")
        with self._lock:
            if event.idempotency_key and any(
                item.idempotency_key == event.idempotency_key
                for item in self._events
            ):
                return
            self._events.append(
                replace(event, created_at=event.created_at or _now())
            )

    def list_events(self, batch_id: UUID) -> tuple[WebBatchEvent, ...]:
        with self._lock:
            return tuple(
                event for event in self._events if event.batch_id == batch_id
            )

    def reconcile_abandoned_jobs(
        self,
        *,
        active_worker_ids: set[str],
    ) -> int:
        reconciled = 0
        with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                if (
                    job.status is not BatchJobStatus.RUNNING
                    or job.worker_id in active_worker_ids
                ):
                    continue
                ended_at = _now()
                self._jobs[job_id] = replace(
                    job,
                    status=BatchJobStatus.INTERRUPTED,
                    ended_at=ended_at,
                    error_code="LOCAL_WORKER_RESTARTED",
                    error_message="Execucao local interrompida por reinicio.",
                )
                batch = self._batches[job.batch_id]
                self._batches[job.batch_id] = replace(
                    batch,
                    status=BatchStatus.PAUSED,
                    version=batch.version + 1,
                )
                self._events.append(
                    WebBatchEvent(
                        batch_id=job.batch_id,
                        job_id=job.id,
                        event_type="JOB_RECOVERED_AS_INTERRUPTED",
                        payload={"previous_worker_id": job.worker_id},
                        created_at=ended_at,
                    )
                )
                reconciled += 1
            for batch_id, batch in tuple(self._batches.items()):
                if batch.status is not BatchStatus.QUEUED:
                    continue
                if not any(
                    job.batch_id == batch_id
                    and job.status is BatchJobStatus.QUEUED
                    for job in self._jobs.values()
                ):
                    continue
                paused_at = _now()
                self._batches[batch_id] = replace(
                    batch,
                    status=BatchStatus.PAUSED,
                    version=batch.version + 1,
                )
                self._events.append(
                    WebBatchEvent(
                        batch_id=batch_id,
                        event_type="BATCH_RECOVERED_PAUSED",
                        payload={"reason": "local_worker_restart"},
                        created_at=paused_at,
                    )
                )
        return reconciled


__all__ = ["InMemoryWebBatchRepository"]
