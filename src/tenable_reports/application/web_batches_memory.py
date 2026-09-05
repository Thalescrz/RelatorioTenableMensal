"""Thread-safe in-memory implementation used by deterministic offline tests."""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from tenable_reports.application.web_batches import (
    BatchJobResult,
    WebBatchRepository,
    assert_sanitized_payload,
    claimed_job_phase,
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
from tenable_reports.domain.remote_components import (
    RemoteComponentState,
    RemoteComponentWindow,
    RemoteObservation,
    RemoteObservationKind,
)
from tenable_reports.domain.report_components import ReportComponent


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
            if batch.parent_batch_id is not None:
                parent = self._batches.get(batch.parent_batch_id)
                if parent is None:
                    raise ValueError("Lote pai não encontrado.")
                if batch.root_batch_id != parent.root_batch_id:
                    raise ValueError("root_batch_id diverge da família do lote pai.")
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

    def import_recovery_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
        event: WebBatchEvent,
    ) -> WebBatch:
        """Atomically persist the recovered batch and its import audit event."""

        with self._lock:
            existing = self._batches.get(batch.id)
            if existing is not None:
                return existing
            stored = self.create_batch(batch, jobs)
            self.append_event(event)
            return stored

    def get_batch(self, batch_id: UUID) -> WebBatch | None:
        with self._lock:
            return self._batches.get(batch_id)

    def get_job(self, job_id: UUID) -> WebBatchJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_batches(self, *, limit: int = 50) -> tuple[WebBatch, ...]:
        with self._lock:
            ordered = sorted(
                self._batches.values(),
                key=lambda item: (item.created_at or "", str(item.id)),
                reverse=True,
            )
            return tuple(ordered[: max(1, min(int(limit), 500))])

    def list_batch_jobs(self, batch_id: UUID) -> tuple[WebBatchJob, ...]:
        return self.list_batch_jobs_for_batches((batch_id,))[batch_id]

    def list_batch_jobs_for_batches(
        self,
        batch_ids: Sequence[UUID],
    ) -> dict[UUID, tuple[WebBatchJob, ...]]:
        requested = tuple(dict.fromkeys(batch_ids))
        with self._lock:
            return {
                batch_id: tuple(
                    sorted(
                        (
                            job
                            for job in self._jobs.values()
                            if job.batch_id == batch_id
                        ),
                        key=lambda job: (job.position, str(job.id)),
                    )
                )
                for batch_id in requested
            }

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
        with self._lock:
            current = self._jobs[job_id]
            updated = replace(
                current,
                process_id=normalized_process_id,
                control_file=control_file or current.control_file,
            )
            self._jobs[job_id] = updated
            self._events.append(
                WebBatchEvent(
                    batch_id=current.batch_id,
                    job_id=current.id,
                    event_type="JOB_PROCESS_STARTED",
                    payload={"process_id": normalized_process_id},
                    created_at=_now(),
                )
            )
            return updated

    def record_vm_export_progress(
        self,
        job_id: UUID,
        *,
        export_uuid: str,
        resume_manifest_path: str | None,
        origin: str | None,
        remote_status: str,
        observed_at: str,
        progress_at: str | None,
        completed_chunks: int,
        total_chunks: int,
        persisted_chunks: Sequence[int],
        status_confirmed: bool = True,
    ) -> WebBatchJob:
        with self._lock:
            current = self._jobs[job_id]
            if current.vm_export_uuid and current.vm_export_uuid != export_uuid:
                raise ValueError("O UUID VM do trabalho nao pode ser substituido.")
            payload = {
                **dict(current.payload),
                "vm_export_uuid": export_uuid,
                "vm_resume_manifest": (
                    resume_manifest_path or current.vm_resume_manifest_path
                ),
                "vm_remote": {
                    "origin": origin,
                    "status": remote_status,
                    "completed_chunks": int(completed_chunks),
                    "total_chunks": int(total_chunks),
                    "persisted_chunks": [int(item) for item in persisted_chunks],
                    "observed_at": observed_at,
                    "progress_at": progress_at or current.remote_progress_at,
                },
            }
            updated = replace(
                current,
                payload=payload,
                vm_export_uuid=export_uuid,
                vm_resume_manifest_path=(
                    resume_manifest_path or current.vm_resume_manifest_path
                ),
                remote_export_started_at=(
                    current.remote_export_started_at or observed_at
                ),
                remote_status_at=(
                    observed_at if status_confirmed else current.remote_status_at
                ),
                remote_progress_at=progress_at or current.remote_progress_at,
            )
            self._jobs[job_id] = updated
            return updated

    def record_vm_export_replacement(
        self,
        job_id: UUID,
        *,
        previous_export_uuid: str,
        replacement_export_uuid: str,
        resume_manifest_path: str | None,
        origin: str | None,
        observed_at: str,
    ) -> WebBatchJob:
        with self._lock:
            current = self._jobs[job_id]
            if current.vm_export_uuid != previous_export_uuid:
                raise ValueError(
                    "O UUID VM anterior nao corresponde ao trabalho atual."
                )
            payload = {
                **dict(current.payload),
                "vm_export_uuid": replacement_export_uuid,
                "vm_resume_manifest": (
                    resume_manifest_path or current.vm_resume_manifest_path
                ),
                "vm_remote": {
                    "origin": origin,
                    "status": "STARTED",
                    "completed_chunks": 0,
                    "total_chunks": 0,
                    "persisted_chunks": [],
                    "observed_at": observed_at,
                    "progress_at": None,
                },
            }
            updated = replace(
                current,
                payload=payload,
                vm_export_uuid=replacement_export_uuid,
                vm_resume_manifest_path=(
                    resume_manifest_path or current.vm_resume_manifest_path
                ),
                remote_export_started_at=observed_at,
                remote_status_at=None,
                remote_progress_at=None,
            )
            self._jobs[job_id] = updated
            return updated

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
        with self._lock:
            batch = self._batches[batch_id]
            if normalized_key and any(
                event.idempotency_key == normalized_key
                for event in self._events
            ):
                return batch
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
                        BatchJobStatus.INTERRUPT_REQUESTED,
                    }:
                        has_active = True
                        if job.status is not BatchJobStatus.INTERRUPT_REQUESTED:
                            self._jobs[job.id] = replace(
                                job,
                                status=BatchJobStatus.INTERRUPT_REQUESTED,
                            )
                    elif job.status in {
                        BatchJobStatus.QUEUED,
                        BatchJobStatus.WAITING_WAS_DECISION,
                    }:
                        self._jobs[job.id] = replace(
                            job,
                            status=BatchJobStatus.CANCELLED_BY_USER,
                            phase=BatchJobPhase.TERMINAL,
                            worker_id=None,
                            process_id=None,
                            control_file=None,
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
                        "reason": normalized_reason,
                    },
                    actor=normalized_actor,
                    idempotency_key=normalized_key,
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

    def request_job_stop(
        self,
        job_id: UUID,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatchJob:
        normalized_actor = str(actor or "").strip()[:200] or None
        normalized_reason = str(reason or "").strip()[:500]
        normalized_key = str(idempotency_key or "").strip()[:200] or None
        active_statuses = {
            BatchJobStatus.QUEUED,
            BatchJobStatus.RUNNING,
            BatchJobStatus.WAITING_WAS_DECISION,
            BatchJobStatus.INTERRUPT_REQUESTED,
        }
        with self._lock:
            current = self._jobs[job_id]
            if normalized_key:
                previous = next(
                    (
                        event
                        for event in self._events
                        if event.idempotency_key == normalized_key
                    ),
                    None,
                )
                if previous is not None:
                    return current
            if current.status not in active_statuses:
                return current

            changed_at = _now()
            immediate = current.status in {
                BatchJobStatus.QUEUED,
                BatchJobStatus.WAITING_WAS_DECISION,
            }
            updated = replace(
                current,
                status=(
                    BatchJobStatus.CANCELLED_BY_USER
                    if immediate
                    else BatchJobStatus.INTERRUPT_REQUESTED
                ),
                phase=BatchJobPhase.TERMINAL if immediate else current.phase,
                worker_id=None if immediate else current.worker_id,
                process_id=None if immediate else current.process_id,
                control_file=None if immediate else current.control_file,
                ended_at=changed_at if immediate else current.ended_at,
            )
            self._jobs[job_id] = updated

            batch = self._batches[current.batch_id]
            jobs = tuple(
                job
                for job in self._jobs.values()
                if job.batch_id == current.batch_id
            )
            if not any(job.status in active_statuses for job in jobs):
                if batch.status is BatchStatus.STOP_REQUESTED:
                    next_status = BatchStatus.STOPPED
                elif any(
                    job.status in {
                        BatchJobStatus.FAILED,
                        BatchJobStatus.PARTIALLY_COMPLETE,
                    }
                    for job in jobs
                ):
                    next_status = BatchStatus.COMPLETE_WITH_FAILURES
                elif any(
                    job.status is BatchJobStatus.COMPLETE_WITH_WARNINGS
                    for job in jobs
                ):
                    next_status = BatchStatus.COMPLETE_WITH_WARNINGS
                else:
                    next_status = BatchStatus.COMPLETE
                self._batches[current.batch_id] = replace(
                    batch,
                    status=next_status,
                    ended_at=changed_at,
                    version=batch.version + 1,
                )

            self._events.append(
                WebBatchEvent(
                    batch_id=current.batch_id,
                    job_id=current.id,
                    event_type="JOB_STOP_REQUESTED",
                    payload={
                        "status": updated.status.value,
                        "reason": normalized_reason,
                    },
                    actor=normalized_actor,
                    idempotency_key=normalized_key,
                    created_at=changed_at,
                )
            )
            return updated

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
        with self._lock:
            candidates = [
                job
                for job in self._jobs.values()
                if job.status is BatchJobStatus.QUEUED
                and job.phase in normalized_phases
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
            next_phase = claimed_job_phase(job.phase)
            claimed = replace(
                job,
                status=BatchJobStatus.RUNNING,
                phase=next_phase,
                worker_id=normalized_worker,
                started_at=job.started_at or started_at,
                remote_started_at=(
                    job.remote_started_at or started_at
                    if next_phase is BatchJobPhase.REMOTE_RUNNING
                    else job.remote_started_at
                ),
                build_started_at=(
                    job.build_started_at or started_at
                    if next_phase is BatchJobPhase.BUILD_RUNNING
                    else job.build_started_at
                ),
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
                    event_type=(
                        "BUILD_STARTED"
                        if next_phase is BatchJobPhase.BUILD_RUNNING
                        else "JOB_STARTED"
                    ),
                    payload={"worker_id": normalized_worker},
                    created_at=started_at,
                )
            )
            return claimed

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
        with self._lock:
            current = self._jobs[job_id]
            if (
                current.status is not BatchJobStatus.RUNNING
                or current.phase is not expected_phase
            ):
                raise ValueError("O trabalho nao esta na fase remota esperada.")
            changed_at = _now()
            advanced = replace(
                current,
                status=BatchJobStatus.QUEUED,
                phase=requested_phase,
                collection_checkpoint_path=checkpoint_path,
                remote_ended_at=changed_at,
                worker_id=None,
                process_id=None,
                ended_at=None,
            )
            self._jobs[job_id] = advanced
            self._events.append(
                WebBatchEvent(
                    batch_id=current.batch_id,
                    job_id=current.id,
                    event_type="COLLECTION_READY",
                    payload={"phase": requested_phase.value},
                    created_at=changed_at,
                )
            )
            return advanced

    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None:
        assert_sanitized_payload(result.payload, path="job.result")
        with self._lock:
            current = self._jobs[job_id]
            ended_at = _now()
            completed = replace(
                current,
                status=result.status,
                phase=(
                    BatchJobPhase.TERMINAL
                    if result.status in BATCH_JOB_TERMINAL_STATUSES
                    else (
                        BatchJobPhase.REMOTE_WAITING_DECISION
                        if result.status is BatchJobStatus.WAITING_WAS_DECISION
                        and current.phase is BatchJobPhase.REMOTE_RUNNING
                        else current.phase
                    )
                ),
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
                if any(
                    job.status in {
                        BatchJobStatus.FAILED,
                        BatchJobStatus.PARTIALLY_COMPLETE,
                    }
                    for job in batch_jobs
                ):
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
        return self.list_events_for_batches((batch_id,))[batch_id]

    def list_events_for_batches(
        self,
        batch_ids: Sequence[UUID],
    ) -> dict[UUID, tuple[WebBatchEvent, ...]]:
        requested = tuple(dict.fromkeys(batch_ids))
        with self._lock:
            return {
                batch_id: tuple(
                    event for event in self._events if event.batch_id == batch_id
                )
                for batch_id in requested
            }

    def reconcile_abandoned_jobs(
        self,
        *,
        active_worker_ids: set[str],
    ) -> int:
        reconciled = 0
        with self._lock:
            for job_id, job in tuple(self._jobs.items()):
                if (
                    job.status
                    not in {
                        BatchJobStatus.RUNNING,
                        BatchJobStatus.INTERRUPT_REQUESTED,
                    }
                    or job.worker_id in active_worker_ids
                ):
                    continue
                ended_at = _now()
                staged_phase = (
                    {
                        BatchJobPhase.REMOTE_RUNNING: BatchJobPhase.REMOTE_QUEUED,
                        BatchJobPhase.BUILD_RUNNING: BatchJobPhase.READY_FOR_BUILD,
                    }.get(job.phase)
                    if job.status is BatchJobStatus.RUNNING
                    else None
                )
                if staged_phase is not None:
                    self._jobs[job_id] = replace(
                        job,
                        status=BatchJobStatus.QUEUED,
                        phase=staged_phase,
                        worker_id=None,
                        process_id=None,
                        control_file=None,
                        ended_at=None,
                    )
                    self._events.append(
                        WebBatchEvent(
                            batch_id=job.batch_id,
                            job_id=job.id,
                            event_type="JOB_REQUEUED_AFTER_RESTART",
                            payload={"phase": staged_phase.value},
                            created_at=ended_at,
                        )
                    )
                    reconciled += 1
                    continue
                self._jobs[job_id] = replace(
                    job,
                    status=BatchJobStatus.INTERRUPTED,
                    phase=BatchJobPhase.TERMINAL,
                    worker_id=None,
                    process_id=None,
                    control_file=None,
                    ended_at=ended_at,
                    error_code=(
                        "INTERRUPTED_BY_USER"
                        if job.status is BatchJobStatus.INTERRUPT_REQUESTED
                        else "LOCAL_WORKER_RESTARTED"
                    ),
                    error_message=(
                        "Execucao local interrompida por solicitacao do usuario."
                        if job.status is BatchJobStatus.INTERRUPT_REQUESTED
                        else "Execucao local interrompida por reinicio."
                    ),
                )
                batch = self._batches[job.batch_id]
                next_batch_status = (
                    BatchStatus.STOPPED
                    if batch.status is BatchStatus.STOP_REQUESTED
                    else BatchStatus.PAUSED
                )
                self._batches[job.batch_id] = replace(
                    batch,
                    status=next_batch_status,
                    ended_at=(
                        ended_at
                        if next_batch_status is BatchStatus.STOPPED
                        else batch.ended_at
                    ),
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


def _component_state_for_window(window_number: int) -> RemoteComponentState:
    return {
        1: RemoteComponentState.RUNNING_WINDOW_1,
        2: RemoteComponentState.RUNNING_WINDOW_2,
        3: RemoteComponentState.RUNNING_WINDOW_3,
    }[window_number]


class InMemoryRemoteComponentRepository:
    """Thread-safe component repository used by coordinator tests."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._components: dict[UUID, RemoteComponentWindow] = {}
        self._attempt_keys: dict[tuple[UUID, ReportComponent, int], UUID] = {}

    def create_for_job(
        self,
        *,
        batch_job_id: UUID,
        components: Sequence[ReportComponent],
        window_number: int,
        deadline_at: datetime,
        origin: str,
        query_fingerprints: Mapping[ReportComponent, str] | None = None,
        attempt_number: int | None = None,
        parent_component_id: UUID | None = None,
        replacement_created_in_window_2: bool = False,
        replacement_created_in_window_3: bool = False,
    ) -> tuple[RemoteComponentWindow, ...]:
        normalized_components = tuple(dict.fromkeys(ReportComponent(c) for c in components))
        if not normalized_components:
            return ()
        normalized_attempt = int(attempt_number or window_number)
        fingerprints = dict(query_fingerprints or {})
        created: list[RemoteComponentWindow] = []
        with self._lock:
            for component in normalized_components:
                key = (batch_job_id, component, normalized_attempt)
                existing_id = self._attempt_keys.get(key)
                if existing_id is not None:
                    created.append(self._components[existing_id])
                    continue
                component_id = uuid5(
                    NAMESPACE_URL,
                    f"{batch_job_id}:{component.value}:window:{window_number}:attempt:{normalized_attempt}",
                )
                value = RemoteComponentWindow(
                    id=component_id,
                    batch_job_id=batch_job_id,
                    component=component,
                    state=RemoteComponentState.PENDING,
                    window_number=window_number,
                    attempt_number=normalized_attempt,
                    parent_component_id=parent_component_id,
                    origin=origin,
                    deadline_at=deadline_at,
                    replacement_created_in_window_2=replacement_created_in_window_2,
                    replacement_created_in_window_3=replacement_created_in_window_3,
                    query_fingerprint=fingerprints.get(component),
                    created_at=datetime.now(UTC),
                )
                self._components[value.id] = value
                self._attempt_keys[key] = value.id
                created.append(value)
        return tuple(created)

    def get(self, component_id: UUID) -> RemoteComponentWindow | None:
        with self._lock:
            return self._components.get(component_id)

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> RemoteComponentWindow | None:
        normalized_worker = str(worker_id or "").strip()
        if not normalized_worker:
            raise ValueError("worker_id não pode ser vazio.")
        if int(lease_seconds) < 1:
            raise ValueError("lease_seconds deve ser positivo.")
        now = datetime.now(UTC)
        claimable_states = {
            RemoteComponentState.PENDING,
            RemoteComponentState.RUNNING_WINDOW_1,
            RemoteComponentState.RUNNING_WINDOW_2,
            RemoteComponentState.RUNNING_WINDOW_3,
        }
        with self._lock:
            candidates = sorted(
                (
                    item
                    for item in self._components.values()
                    if item.state in claimable_states
                    and (
                        item.worker_id is None
                        or item.lease_expires_at is None
                        or item.lease_expires_at <= now
                    )
                ),
                key=lambda item: (
                    list(ReportComponent).index(item.component),
                    item.created_at or datetime.min.replace(tzinfo=UTC),
                    item.attempt_number,
                ),
            )
            if not candidates:
                return None
            current = candidates[0]
            updated = replace(
                current,
                state=(
                    _component_state_for_window(current.window_number)
                    if current.state is RemoteComponentState.PENDING
                    else current.state
                ),
                worker_id=normalized_worker,
                lease_expires_at=now + timedelta(seconds=int(lease_seconds)),
                started_at=current.started_at or now,
            )
            self._components[current.id] = updated
            return updated

    def record_observation(
        self,
        component_id: UUID,
        observation: RemoteObservation,
    ) -> RemoteComponentWindow:
        now = datetime.now(UTC)
        with self._lock:
            current = self._components[component_id]
            progress = (
                observation.completed_units != current.completed_units
                or observation.total_units != current.total_units
            )
            updated = replace(
                current,
                completed_units=observation.completed_units,
                total_units=observation.total_units,
                last_remote_status=(
                    observation.remote_status or observation.kind.value
                ),
                last_contact_at=now,
                last_progress_at=now if progress else current.last_progress_at,
                failure_code=observation.failure_code,
            )
            self._components[component_id] = updated
            return updated

    def transition(
        self,
        component_id: UUID,
        *,
        expected_state: RemoteComponentState,
        requested_state: RemoteComponentState,
        **changes: Any,
    ) -> RemoteComponentWindow:
        with self._lock:
            current = self._components[component_id]
            if current.state is not expected_state:
                raise RuntimeError("Transição concorrente do componente remoto.")
            allowed = set(RemoteComponentWindow.__dataclass_fields__) - {
                "id",
                "batch_job_id",
                "component",
                "state",
            }
            unknown = set(changes) - allowed
            if unknown:
                raise ValueError(f"Campos de transição inválidos: {sorted(unknown)}")
            updated = replace(current, state=requested_state, **changes)
            self._components[component_id] = updated
            return updated

    def list_for_jobs(
        self,
        job_ids: Sequence[UUID],
    ) -> dict[UUID, tuple[RemoteComponentWindow, ...]]:
        requested = tuple(dict.fromkeys(job_ids))
        with self._lock:
            return {
                job_id: tuple(
                    sorted(
                        (
                            item
                            for item in self._components.values()
                            if item.batch_job_id == job_id
                        ),
                        key=lambda item: (
                            item.attempt_number,
                            list(ReportComponent).index(item.component),
                            str(item.id),
                        ),
                    )
                )
                for job_id in requested
            }

    def reconcile_abandoned(
        self,
        *,
        now: datetime,
        active_worker_ids: set[str],
    ) -> int:
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("now deve usar timezone UTC.")
        reconciled = 0
        with self._lock:
            for component_id, current in tuple(self._components.items()):
                if (
                    current.worker_id is not None
                    and current.worker_id not in active_worker_ids
                ):
                    self._components[component_id] = replace(
                        current,
                        worker_id=None,
                        lease_expires_at=None,
                    )
                    reconciled += 1
        return reconciled


__all__ = ["InMemoryWebBatchRepository"]
