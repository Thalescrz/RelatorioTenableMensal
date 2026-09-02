"""Compatibility adapter between the dashboard executor and durable batches."""

from __future__ import annotations

import threading
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from tenable_reports.application.execution_control import FileExecutionControl
from tenable_reports.application.web_batches import (
    BatchClientConflictError,
    BatchConfirmationError,
    BatchJobResult,
    DerivedBatchRequest,
    NoEligibleBatchJobsError,
    WebBatchRepository,
    assert_sanitized_payload,
)
from tenable_reports.domain.web_batches import (
    BATCH_TERMINAL_STATUSES,
    RETRYABLE_BATCH_JOB_STATUSES,
    BatchAction,
    BatchJobPhase,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)
from tenable_reports.webapp.job_queue import (
    DurableRunner,
    DurableWorkerPool,
    DurableWorkerPoolGroup,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_PRIVATE_DASHBOARD_KEYS = frozenset(
    {"checkpoint", "checkpoint_path", "collection_checkpoint_path"}
)


def _safe_dashboard_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_dashboard_value(item)
            for key, item in value.items()
            if not str(key).startswith("_")
            and str(key).casefold() not in _PRIVATE_DASHBOARD_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_safe_dashboard_value(item) for item in value]
    return value


class DurableDashboardJobQueue:
    """Expose the legacy dashboard API while PostgreSQL owns job state."""

    def __init__(
        self,
        *,
        repository: WebBatchRepository,
        executor: Any,
        worker_id: str,
        poll_interval: float = 0.25,
        start_worker: bool = True,
        remote_runner: DurableRunner | None = None,
        build_runner: DurableRunner | None = None,
        remote_workers: int = 0,
        enable_staged_executor: bool = False,
        staged_output_root: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self._staged_output_root = (
            Path(staged_output_root).resolve()
            if staged_output_root is not None
            else (self.executor.project_root / "data").resolve()
        )
        self._active_lock = threading.RLock()
        self._active_jobs: dict[str, WebBatchJob] = {}
        self.executor.progress_sink = self._persist_progress
        self.executor.process_sink = self._persist_process
        self.executor.fallback_sink = self._persist_fallback
        pools = [
            DurableWorkerPool(
                repository=repository,
                runner=self._run_job,
                worker_prefix=f"{worker_id}-legacy",
                phases=(BatchJobPhase.LEGACY,),
                workers=1,
                poll_interval=poll_interval,
                start_workers=False,
                reconcile=False,
            )
        ]
        normalized_remote_workers = int(remote_workers)
        if enable_staged_executor:
            remote_runner = remote_runner or self._run_remote_job
            build_runner = build_runner or self._run_build_job
        if remote_runner is not None:
            if normalized_remote_workers < 1:
                raise ValueError(
                    "remote_workers deve ser positivo quando o pool remoto existe."
                )
            pools.append(
                DurableWorkerPool(
                    repository=repository,
                    runner=lambda job: self._run_pool_job(job, remote_runner),
                    worker_prefix=f"tenable-remote-{worker_id}",
                    phases=(BatchJobPhase.REMOTE_QUEUED,),
                    workers=normalized_remote_workers,
                    poll_interval=poll_interval,
                    start_workers=False,
                    reconcile=False,
                    result_handler=self._handle_remote_result,
                )
            )
        elif normalized_remote_workers != 0:
            raise ValueError("remote_runner e obrigatorio para o pool remoto.")
        if build_runner is not None:
            pools.append(
                DurableWorkerPool(
                    repository=repository,
                    runner=lambda job: self._run_pool_job(job, build_runner),
                    worker_prefix=f"tenable-build-{worker_id}",
                    phases=(BatchJobPhase.READY_FOR_BUILD,),
                    workers=1,
                    poll_interval=poll_interval,
                    start_workers=False,
                    reconcile=False,
                )
            )
        self._dispatcher = DurableWorkerPoolGroup(
            repository=repository,
            pools=tuple(pools),
            start_workers=start_worker,
        )

    def enqueue(
        self,
        client_ids: Sequence[str],
        request: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return self.enqueue_requests(
            tuple((client_id, request) for client_id in client_ids)
        )

    def _control_file(
        self,
        *,
        batch_id: UUID,
        job_id: UUID,
        mode: str,
    ) -> str:
        scope_name = (
            "automatic-monthly" if mode == "automatic" else "manual"
        )
        return str(
            (
                self.executor.project_root
                / "data"
                / scope_name
                / "control"
                / "web-batches"
                / str(batch_id)
                / f"{job_id.hex}.json"
            ).resolve()
        )

    def enqueue_requests(
        self,
        requests: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        batch_options: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if batch_options is not None and not isinstance(batch_options, Mapping):
            raise ValueError("batch_options deve ser um objeto.")
        copied_batch_options = deepcopy(dict(batch_options or {}))
        if "requests" in copied_batch_options:
            raise ValueError("batch_options nao pode sobrescrever requests.")
        assert_sanitized_payload(copied_batch_options, path="batch_options")
        execution_model = str(
            copied_batch_options.get("execution_model") or "LEGACY"
        ).strip().upper()
        if execution_model not in {"LEGACY", "STAGED_V1"}:
            raise ValueError("execution_model invalido.")
        created: list[dict[str, Any]] = []
        normalized_requests: list[dict[str, Any]] = []
        for client_id, request in requests:
            rows = self.executor.enqueue([client_id], request)
            created.extend(rows)
            normalized_requests.append(
                {"client_id": client_id, "request": dict(request)}
            )
        if not created:
            return []
        batch_id = uuid4()
        created_at = _now()
        options = {
            "requests": normalized_requests,
            **copied_batch_options,
        }
        assert_sanitized_payload(options, path="batch_options")
        batch = WebBatch(
            id=batch_id,
            idempotency_key=f"batch:create:{batch_id}",
            kind=(
                "GENERATE_ALL"
                if "selected_client_ids" in copied_batch_options or len(created) > 1
                else "GENERATE_ONE"
            ),
            status=BatchStatus.QUEUED,
            options=options,
            created_at=created_at,
        )

        def durable_job(
            position: int,
            row: Mapping[str, Any],
        ) -> WebBatchJob:
            job_id = UUID(hex=str(row["job_id"]))
            control_file = self._control_file(
                batch_id=batch_id,
                job_id=job_id,
                mode=str(row.get("mode") or "manual"),
            )
            return WebBatchJob(
                id=job_id,
                batch_id=batch_id,
                client_id=str(row["client_id"]),
                position=position,
                status=BatchJobStatus.QUEUED,
                attempt_number=1,
                phase=(
                    BatchJobPhase.REMOTE_QUEUED
                    if execution_model == "STAGED_V1"
                    else BatchJobPhase.LEGACY
                ),
                payload={
                    **dict(row),
                    "batch_id": str(batch_id),
                    "_job_control_file": str(control_file),
                },
                control_file=str(control_file),
                created_at=str(row.get("created_at") or created_at),
            )

        jobs = tuple(
            durable_job(position, row)
            for position, row in enumerate(created, start=1)
        )
        try:
            self.repository.create_batch(batch, jobs)
        finally:
            self._drain_executor_pending(len(created))
        self._dispatcher.wake()
        created_ids = {job.id for job in jobs}
        return [
            row
            for row in self.snapshot()
            if UUID(hex=str(row["job_id"])) in created_ids
        ]
    def derive_batch(
        self,
        request: DerivedBatchRequest,
    ) -> dict[str, Any]:
        source = self.repository.get_batch(request.source_batch_id)
        if source is None:
            raise KeyError("Lote de origem nao encontrado.")
        paused_recovery = (
            source.kind == "RECOVERED"
            and source.status is BatchStatus.PAUSED
        )
        if (
            source.status not in BATCH_TERMINAL_STATUSES
            and not paused_recovery
        ):
            raise ValueError(
                "O lote de origem ainda esta ativo e nao pode ser derivado."
            )
        if request.kind is BatchAction.RERUN_ALL:
            expected = f"GERAR NOVAMENTE {str(source.id)[:8]}"
            if str(request.confirmation_token or "").strip() != expected:
                raise BatchConfirmationError(
                    f'Digite exatamente "{expected}" para confirmar.'
                )

        batch_key = f"batch:derive:{request.idempotency_key}"
        batch_id = uuid5(NAMESPACE_URL, batch_key)
        existing = self.repository.get_batch(batch_id)
        if existing is not None:
            return self.batch_snapshot(existing.id)

        source_jobs = self.repository.list_batch_jobs(source.id)
        selected = tuple(
            job
            for job in source_jobs
            if (
                request.kind is BatchAction.RERUN_ALL
                or job.status in RETRYABLE_BATCH_JOB_STATUSES
            )
        )
        if not selected:
            raise NoEligibleBatchJobsError(source.id)

        selected_clients = {job.client_id for job in selected}
        conflicts = self.repository.active_client_conflicts(
            tuple(selected_clients),
            excluding_batch_id=source.id,
        )
        if conflicts:
            raise BatchClientConflictError(conflicts)

        created_at = _now()
        derived = WebBatch(
            id=batch_id,
            idempotency_key=batch_key,
            kind=request.kind.value,
            status=BatchStatus.QUEUED,
            options={
                **dict(source.options),
                "derived_action": request.kind.value,
            },
            source_batch_id=source.id,
            created_at=created_at,
        )
        jobs: list[WebBatchJob] = []
        transient_keys = {
            "batch_id",
            "job_id",
            "status",
            "progress",
            "queue_position",
            "started_at",
            "ended_at",
            "run_id",
            "error",
            "error_code",
            "exit_code",
            "warnings",
            "export_progress",
            "was_export_progress",
            "cloud_progress",
            "tag_progress",
            "_job_control_file",
        }
        for position, source_job in enumerate(selected, start=1):
            job_id = uuid5(batch_id, str(source_job.id))
            payload = {
                key: value
                for key, value in dict(source_job.payload).items()
                if key not in transient_keys
            }
            mode = str(payload.get("mode") or "manual")
            control_file = self._control_file(
                batch_id=batch_id,
                job_id=job_id,
                mode=mode,
            )
            payload.update(
                {
                    "job_id": job_id.hex,
                    "batch_id": str(batch_id),
                    "client_id": source_job.client_id,
                    "status": BatchJobStatus.QUEUED.value,
                    "created_at": created_at,
                    "_job_control_file": control_file,
                }
            )
            is_retry = request.kind is BatchAction.RETRY_INCOMPLETE
            staged_model = (
                str(source.options.get("execution_model") or "").upper()
                == "STAGED_V1"
            )
            reusable_checkpoint = (
                source_job.collection_checkpoint_path
                if is_retry
                and staged_model
                and source_job.collection_checkpoint_path
                and Path(source_job.collection_checkpoint_path).resolve().is_file()
                else None
            )
            jobs.append(
                WebBatchJob(
                    id=job_id,
                    batch_id=batch_id,
                    client_id=source_job.client_id,
                    position=position,
                    status=BatchJobStatus.QUEUED,
                    attempt_number=(
                        source_job.attempt_number + 1 if is_retry else 1
                    ),
                    phase=(
                        BatchJobPhase.READY_FOR_BUILD
                        if reusable_checkpoint
                        else BatchJobPhase.REMOTE_QUEUED
                        if staged_model
                        else BatchJobPhase.LEGACY
                    ),
                    payload=payload,
                    retry_of_batch_job_id=source_job.id if is_retry else None,
                    logical_job_id=source_job.logical_job_id,
                    collection_checkpoint_path=(
                        reusable_checkpoint
                    ),
                    control_file=control_file,
                    created_at=created_at,
                )
            )
        try:
            self.repository.create_batch(derived, tuple(jobs))
        except ValueError as exc:
            raise BatchClientConflictError(
                tuple(job.client_id for job in jobs)
            ) from exc
        self.repository.append_event(
            WebBatchEvent(
                batch_id=batch_id,
                event_type="BATCH_DERIVED",
                payload={
                    "source_batch_id": str(source.id),
                    "kind": request.kind.value,
                    "job_count": len(jobs),
                    "reason": str(request.reason or "")[:500],
                },
                actor=str(request.actor or "")[:200] or None,
                idempotency_key=f"event:{batch_key}",
            )
        )
        self._dispatcher.wake()
        return self.batch_snapshot(batch_id)

    def request_action(
        self,
        batch_id: UUID | str,
        action: BatchAction,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatch:
        normalized_id = (
            batch_id if isinstance(batch_id, UUID) else UUID(str(batch_id))
        )
        updated = self.repository.request_action(
            normalized_id,
            action,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        if action is BatchAction.STOP:
            for job in self.repository.list_batch_jobs(normalized_id):
                if (
                    job.status is not BatchJobStatus.INTERRUPT_REQUESTED
                    or not job.control_file
                ):
                    continue
                try:
                    FileExecutionControl(job.control_file).request_stop(
                        reason="Solicitacao local do usuario para parar o lote."
                    )
                except Exception as exc:
                    self.repository.append_event(
                        WebBatchEvent(
                            batch_id=normalized_id,
                            job_id=job.id,
                            event_type="JOB_CONTROL_WRITE_FAILED",
                            payload={"message": str(exc)[:500]},
                        )
                    )
                    raise RuntimeError(
                        "A parada foi registrada, mas o sinal local nao pôde "
                        "ser gravado."
                    ) from exc
        elif action is BatchAction.RESUME:
            self._dispatcher.wake()
        return updated

    def batches_snapshot(self, *, limit: int = 50) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        capacities = self.capacity_snapshot()
        remote_capacity = sum(
            int(pool.get("workers") or 0)
            for pool in capacities
            if BatchJobPhase.REMOTE_QUEUED.value in tuple(pool.get("phases") or ())
        )
        build_capacity = sum(
            int(pool.get("workers") or 0)
            for pool in capacities
            if BatchJobPhase.READY_FOR_BUILD.value in tuple(pool.get("phases") or ())
        )
        terminal_jobs = {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.FAILED,
            BatchJobStatus.INTERRUPTED,
            BatchJobStatus.CANCELLED_BY_USER,
        }
        for batch in self.repository.list_batches(limit=limit):
            jobs = self.repository.list_batch_jobs(batch.id)
            counts = {
                status: sum(job.status is status for job in jobs)
                for status in BatchJobStatus
            }
            phase_counts = {
                phase.value: sum(job.phase is phase for job in jobs)
                for phase in BatchJobPhase
            }
            finished = sum(job.status in terminal_jobs for job in jobs)
            current = next(
                (
                    job
                    for job in jobs
                    if job.status
                    in {
                        BatchJobStatus.RUNNING,
                        BatchJobStatus.WAITING_WAS_DECISION,
                        BatchJobStatus.INTERRUPT_REQUESTED,
                    }
                ),
                None,
            )
            requests = list(batch.options.get("requests") or ())
            first_request = (
                dict(requests[0].get("request") or {})
                if requests and isinstance(requests[0], Mapping)
                else {}
            )
            summaries.append(
                {
                    "id": str(batch.id),
                    "kind": batch.kind,
                    "status": batch.status.value,
                    "requested_action": (
                        batch.requested_action.value
                        if batch.requested_action is not None
                        else None
                    ),
                    "source_batch_id": (
                        str(batch.source_batch_id)
                        if batch.source_batch_id is not None
                        else None
                    ),
                    "created_at": batch.created_at,
                    "started_at": batch.started_at,
                    "ended_at": batch.ended_at,
                    "total_count": len(jobs),
                    "completed_count": (
                        counts[BatchJobStatus.COMPLETE]
                        + counts[BatchJobStatus.COMPLETE_WITH_WARNINGS]
                    ),
                    "warning_count": counts[
                        BatchJobStatus.COMPLETE_WITH_WARNINGS
                    ],
                    "failed_count": counts[BatchJobStatus.FAILED],
                    "interrupted_count": counts[BatchJobStatus.INTERRUPTED],
                    "cancelled_count": counts[
                        BatchJobStatus.CANCELLED_BY_USER
                    ],
                    "queued_count": counts[BatchJobStatus.QUEUED],
                    "retryable_count": (
                        counts[BatchJobStatus.FAILED]
                        + counts[BatchJobStatus.INTERRUPTED]
                        + counts[BatchJobStatus.CANCELLED_BY_USER]
                    ),
                    "progress_percent": (
                        round(100 * finished / len(jobs)) if jobs else 100
                    ),
                    "current_client_id": (
                        current.client_id if current is not None else None
                    ),
                    "current_phase": (
                        current.phase.value if current is not None else None
                    ),
                    "phase_counts": phase_counts,
                    "remote_concurrency": {
                        "active": phase_counts[BatchJobPhase.REMOTE_RUNNING.value],
                        "capacity": remote_capacity,
                    },
                    "build_queue_count": phase_counts[
                        BatchJobPhase.READY_FOR_BUILD.value
                    ],
                    "build_concurrency": {
                        "active": phase_counts[BatchJobPhase.BUILD_RUNNING.value],
                        "capacity": build_capacity,
                    },
                    "mode": str(first_request.get("mode") or ""),
                    "days": first_request.get("days"),
                    "start_at": first_request.get("start_at"),
                    "end_at": first_request.get("end_at"),
                }
            )
        return summaries

    def batch_snapshot(self, batch_id: UUID | str) -> dict[str, Any]:
        normalized_id = (
            batch_id if isinstance(batch_id, UUID) else UUID(str(batch_id))
        )
        batch = self.repository.get_batch(normalized_id)
        if batch is None:
            raise KeyError("Lote nao encontrado.")
        return {
            "batch": {
                "id": str(batch.id),
                "kind": batch.kind,
                "status": batch.status.value,
                "requested_action": (
                    batch.requested_action.value
                    if batch.requested_action is not None
                    else None
                ),
                "source_batch_id": (
                    str(batch.source_batch_id)
                    if batch.source_batch_id is not None
                    else None
                ),
                "version": batch.version,
                "created_at": batch.created_at,
                "started_at": batch.started_at,
                "ended_at": batch.ended_at,
            },
            "jobs": [
                {
                    "id": str(job.id),
                    "client_id": job.client_id,
                    "position": job.position,
                    "status": job.status.value,
                    "phase": job.phase.value,
                    "checkpoint_ready": bool(job.collection_checkpoint_path),
                    "attempt_number": job.attempt_number,
                    "run_id": job.run_id,
                    "exit_code": job.exit_code,
                    "error_code": job.error_code,
                    "error_message": job.error_message,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "remote_started_at": job.remote_started_at,
                    "remote_ended_at": job.remote_ended_at,
                    "build_started_at": job.build_started_at,
                    "ended_at": job.ended_at,
                }
                for job in self.repository.list_batch_jobs(normalized_id)
            ],
            "events": [
                {
                    "job_id": str(event.job_id) if event.job_id else None,
                    "event_type": event.event_type,
                    "payload": _safe_dashboard_value(event.payload),
                    "created_at": event.created_at,
                }
                for event in self.repository.list_events(normalized_id)
            ],
        }

    def snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        queued_ids: list[str] = []
        for batch in self.repository.list_batches(limit=500):
            events_by_job: dict[UUID, list[WebBatchEvent]] = {}
            for event in self.repository.list_events(batch.id):
                if event.job_id is not None:
                    events_by_job.setdefault(event.job_id, []).append(event)
            for job in self.repository.list_batch_jobs(batch.id):
                row = _safe_dashboard_value(job.payload)
                row.update(
                    {
                        "job_id": job.id.hex,
                        "batch_id": str(batch.id),
                        "batch_status": batch.status.value,
                        "batch_kind": batch.kind,
                        "batch_requested_action": (
                            batch.requested_action.value
                            if batch.requested_action is not None
                            else None
                        ),
                        "client_id": job.client_id,
                        "status": job.status.value,
                        "phase": job.phase.value,
                        "checkpoint_ready": bool(job.collection_checkpoint_path),
                        "started_at": job.started_at,
                        "remote_started_at": job.remote_started_at,
                        "remote_ended_at": job.remote_ended_at,
                        "build_started_at": job.build_started_at,
                        "ended_at": job.ended_at,
                        "error": job.error_message,
                        "run_id": job.run_id or row.get("run_id"),
                        "queue_position": None,
                    }
                )
                for event in events_by_job.get(job.id, ()):
                    if event.event_type == "JOB_PROGRESS":
                        _apply_progress(row, event.payload)
                if job.status is BatchJobStatus.QUEUED:
                    queued_ids.append(job.id.hex)
                rows.append(row)
        positions = {
            job_id: position for position, job_id in enumerate(queued_ids, start=1)
        }
        for row in rows:
            row["queue_position"] = positions.get(str(row["job_id"]))
        return sorted(
            rows,
            key=lambda row: str(row.get("created_at") or ""),
            reverse=True,
        )

    def wait_until_idle(self, *, timeout: float) -> bool:
        return self._dispatcher.wait_until_idle(timeout=timeout)

    def capacity_snapshot(self) -> tuple[dict[str, Any], ...]:
        return self._dispatcher.capacity_snapshot()

    def close(self) -> None:
        self._dispatcher.close()

    def _drain_executor_pending(self, count: int) -> None:
        for _ in range(count):
            self.executor._pending.get_nowait()
            self.executor._pending.task_done()

    def _persist_progress(
        self,
        job_id: str,
        event: Mapping[str, Any],
    ) -> None:
        with self._active_lock:
            active = self._active_jobs.get(str(job_id))
        if active is None:
            return
        self.repository.append_event(
            WebBatchEvent(
                batch_id=active.batch_id,
                job_id=active.id,
                event_type="JOB_PROGRESS",
                payload=dict(event),
            )
        )

    def _persist_process(self, job_id: str, process_id: int) -> None:
        with self._active_lock:
            active = self._active_jobs.get(str(job_id))
        if active is None:
            return
        self.repository.record_job_process(
            active.id,
            process_id,
            control_file=active.control_file,
        )

    def _persist_fallback(self, job_id: str, process_id: int) -> None:
        with self._active_lock:
            active = self._active_jobs.get(str(job_id))
        if active is None:
            return
        self.repository.append_event(
            WebBatchEvent(
                batch_id=active.batch_id,
                job_id=active.id,
                event_type="JOB_LOCAL_FALLBACK_TERMINATION",
                payload={"process_id": int(process_id)},
            )
        )

    def _run_job(self, job: WebBatchJob) -> BatchJobResult:
        return self._run_executor_job(job)

    def _checkpoint_file(self, job: WebBatchJob) -> str:
        mode = str(job.payload.get("mode") or "manual")
        scope = "automatic-monthly" if mode == "automatic" else "manual"
        return str(
            (
                self._staged_output_root
                / scope
                / "orchestration"
                / "checkpoints"
                / str(job.batch_id)
                / f"{job.id.hex}.json"
            ).resolve()
        )

    def _run_remote_job(self, job: WebBatchJob) -> BatchJobResult:
        checkpoint = self._checkpoint_file(job)
        result = self._run_executor_job(
            job,
            operation="staged_remote",
            checkpoint_path=checkpoint,
        )
        if result.status in {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
        }:
            return replace(
                result,
                payload={
                    **dict(result.payload),
                    "_collection_checkpoint_path": checkpoint,
                },
            )
        return result

    def _run_build_job(self, job: WebBatchJob) -> BatchJobResult:
        return self._run_executor_job(
            job,
            operation="staged_build",
            checkpoint_path=job.collection_checkpoint_path,
        )

    def _handle_remote_result(
        self,
        job: WebBatchJob,
        result: BatchJobResult,
    ) -> None:
        payload = {
            key: value
            for key, value in dict(result.payload).items()
            if key != "_collection_checkpoint_path"
        }
        if result.status in {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
        }:
            checkpoint = result.payload.get("_collection_checkpoint_path")
            self.repository.advance_job_phase(
                job.id,
                expected_phase=BatchJobPhase.REMOTE_RUNNING,
                requested_phase=BatchJobPhase.READY_FOR_BUILD,
                collection_checkpoint_path=checkpoint,
            )
            return
        self.repository.complete_job(job.id, replace(result, payload=payload))

    def _run_executor_job(
        self,
        job: WebBatchJob,
        *,
        operation: str | None = None,
        checkpoint_path: str | None = None,
    ) -> BatchJobResult:
        job_id = job.id.hex
        payload = dict(job.payload)
        payload["job_id"] = job_id
        payload["status"] = "QUEUED"
        if operation is not None:
            payload["operation"] = operation
        if checkpoint_path is not None:
            payload["_collection_checkpoint_path"] = checkpoint_path
        payload["attempt_number"] = job.attempt_number
        payload["logical_job_id"] = job.logical_job_id or job.id.hex
        with self.executor._lock:
            self.executor._jobs[job_id] = payload
        with self._active_lock:
            self._active_jobs[job_id] = job
        try:
            self.executor._run(job_id)
            with self.executor._lock:
                result = dict(self.executor._jobs[job_id])
        finally:
            with self._active_lock:
                self._active_jobs.pop(job_id, None)
        raw_status = str(result.get("status") or "FAILED").upper()
        if raw_status == "WAITING_WAS_DECISION":
            status = BatchJobStatus.WAITING_WAS_DECISION
        elif raw_status == "INTERRUPTED" or int(result.get("exit_code") or 0) == 130:
            status = BatchJobStatus.INTERRUPTED
        elif raw_status == "COMPLETE" and result.get("warnings"):
            status = BatchJobStatus.COMPLETE_WITH_WARNINGS
        elif raw_status == "COMPLETE":
            status = BatchJobStatus.COMPLETE
        else:
            status = BatchJobStatus.FAILED
        return BatchJobResult(
            status=status,
            exit_code=(
                0
                if status in {
                    BatchJobStatus.COMPLETE,
                    BatchJobStatus.COMPLETE_WITH_WARNINGS,
                }
                else 130
                if status is BatchJobStatus.INTERRUPTED
                else int(result.get("exit_code") or 1)
            ),
            error_code=(
                "INTERRUPTED_BY_USER"
                if status is BatchJobStatus.INTERRUPTED
                else "UNEXPECTED"
                if status is BatchJobStatus.FAILED
                else None
            ),
            error_message=(
                str(result.get("error") or "Falha operacional sem detalhe.")[:500]
                if status is BatchJobStatus.FAILED
                else None
            ),
            payload=result,
        )

    def _run_pool_job(
        self,
        job: WebBatchJob,
        runner: DurableRunner,
    ) -> BatchJobResult:
        job_id = job.id.hex
        with self._active_lock:
            self._active_jobs[job_id] = job
        try:
            return runner(job)
        finally:
            with self._active_lock:
                self._active_jobs.pop(job_id, None)


def _apply_progress(row: dict[str, Any], event: Mapping[str, Any]) -> None:
    event_type = str(event.get("event") or "")
    if event_type == "TENABLE_CLOUD_PROGRESS":
        row["cloud_progress"] = {
            key: event.get(key)
            for key in (
                "status",
                "stage",
                "source",
                "current",
                "total",
                "page",
                "records",
                "documents",
                "snapshot_id",
                "run_id",
            )
        }
        row["cloud_status"] = str(event.get("status") or "")
        return
    if event_type == "TENABLE_EXPORT_PROGRESS":
        progress_key = (
            "was_export_progress"
            if str(event.get("source") or "") == "tenable_was_findings"
            else "export_progress"
        )
        row[progress_key] = {
            key: event.get(key)
            for key in (
                "source",
                "export_uuid",
                "origin",
                "segment",
                "date_field",
                "status",
                "completed_chunks",
                "total_chunks",
                "elapsed_seconds",
                "processing_elapsed_seconds",
                "idle_seconds",
                "last_progress_elapsed_seconds",
                "no_progress_timeout_seconds",
                "stalled",
                "timeout_phase",
                "filters",
                "progress_made",
                "auto_cancelled",
                "cancellation_error",
            )
        }
        return
    if event_type == "TAG_REPORT_PROGRESS":
        row["tag_progress"] = {
            "current": int(event.get("current") or 0),
            "total": int(event.get("total") or 0),
            "tag_uuid": str(event.get("tag_uuid") or ""),
            "label": str(event.get("tag_label") or ""),
        }


__all__ = ["DurableDashboardJobQueue"]
