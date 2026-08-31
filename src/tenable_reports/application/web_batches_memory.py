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
                ended_at=ended_at,
            )
            self._jobs[job_id] = completed
            batch_jobs = self.list_batch_jobs(current.batch_id)
            batch = self._batches[current.batch_id]
            next_status = batch.status
            if result.status in {
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
        return reconciled


__all__ = ["InMemoryWebBatchRepository"]
