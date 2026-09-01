"""Compatibility adapter between the dashboard executor and durable batches."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from uuid import UUID, uuid4

from tenable_reports.application.web_batches import (
    BatchJobResult,
    WebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)
from tenable_reports.webapp.job_queue import DurableJobQueue


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
    ) -> None:
        self.repository = repository
        self.executor = executor
        self._active_lock = threading.RLock()
        self._active_job: WebBatchJob | None = None
        self.executor.progress_sink = self._persist_progress
        self._dispatcher = DurableJobQueue(
            repository=repository,
            runner=self._run_job,
            worker_id=worker_id,
            poll_interval=poll_interval,
            start_worker=start_worker,
        )

    def enqueue(
        self,
        client_ids: Sequence[str],
        request: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return self.enqueue_requests(
            tuple((client_id, request) for client_id in client_ids)
        )

    def enqueue_requests(
        self,
        requests: Sequence[tuple[str, Mapping[str, Any]]],
    ) -> list[dict[str, Any]]:
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
        batch = WebBatch(
            id=batch_id,
            idempotency_key=f"batch:create:{batch_id}",
            kind="GENERATE_ALL" if len(created) > 1 else "GENERATE_ONE",
            status=BatchStatus.QUEUED,
            options={"requests": normalized_requests},
            created_at=created_at,
        )
        jobs = tuple(
            WebBatchJob(
                id=UUID(hex=str(row["job_id"])),
                batch_id=batch_id,
                client_id=str(row["client_id"]),
                position=position,
                status=BatchJobStatus.QUEUED,
                attempt_number=1,
                payload={**dict(row), "batch_id": str(batch_id)},
                created_at=str(row.get("created_at") or created_at),
            )
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
    def snapshot(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        queued_ids: list[str] = []
        for batch in self.repository.list_batches(limit=500):
            events_by_job: dict[UUID, list[WebBatchEvent]] = {}
            for event in self.repository.list_events(batch.id):
                if event.job_id is not None:
                    events_by_job.setdefault(event.job_id, []).append(event)
            for job in self.repository.list_batch_jobs(batch.id):
                row = {
                    key: value
                    for key, value in dict(job.payload).items()
                    if not str(key).startswith("_")
                }
                row.update(
                    {
                        "job_id": job.id.hex,
                        "batch_id": str(batch.id),
                        "client_id": job.client_id,
                        "status": job.status.value,
                        "started_at": job.started_at,
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
            active = self._active_job
        if active is None or active.id.hex != str(job_id):
            return
        self.repository.append_event(
            WebBatchEvent(
                batch_id=active.batch_id,
                job_id=active.id,
                event_type="JOB_PROGRESS",
                payload=dict(event),
            )
        )

    def _run_job(self, job: WebBatchJob) -> BatchJobResult:
        job_id = job.id.hex
        payload = dict(job.payload)
        payload["job_id"] = job_id
        payload["status"] = "QUEUED"
        with self.executor._lock:
            self.executor._jobs[job_id] = payload
        with self._active_lock:
            self._active_job = job
        try:
            self.executor._run(job_id)
            with self.executor._lock:
                result = dict(self.executor._jobs[job_id])
        finally:
            with self._active_lock:
                self._active_job = None
        raw_status = str(result.get("status") or "FAILED").upper()
        if raw_status == "WAITING_WAS_DECISION":
            status = BatchJobStatus.WAITING_WAS_DECISION
        elif raw_status == "COMPLETE" and result.get("warnings"):
            status = BatchJobStatus.COMPLETE_WITH_WARNINGS
        elif raw_status == "COMPLETE":
            status = BatchJobStatus.COMPLETE
        else:
            status = BatchJobStatus.FAILED
        return BatchJobResult(
            status=status,
            exit_code=0 if status in {
                BatchJobStatus.COMPLETE,
                BatchJobStatus.COMPLETE_WITH_WARNINGS,
            } else 1,
            error_code="UNEXPECTED" if status is BatchJobStatus.FAILED else None,
            error_message=(
                str(result.get("error") or "Falha operacional sem detalhe.")[:500]
                if status is BatchJobStatus.FAILED
                else None
            ),
            payload=result,
        )


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
