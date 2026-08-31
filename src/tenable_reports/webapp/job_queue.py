"""Sequential dispatcher whose durable state lives in a batch repository."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID

from tenable_reports.application.web_batches import (
    BatchJobResult,
    WebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    WebBatchJob,
)


DurableRunner = Callable[[WebBatchJob], BatchJobResult]


class DurableJobQueue:
    def __init__(
        self,
        *,
        repository: WebBatchRepository,
        runner: DurableRunner,
        worker_id: str,
        poll_interval: float = 0.25,
        start_worker: bool = True,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.worker_id = str(worker_id or "").strip()
        if not self.worker_id:
            raise ValueError("worker_id nao pode ser vazio.")
        self.poll_interval = max(0.01, float(poll_interval))
        self._stopping = threading.Event()
        self._wake_event = threading.Event()
        self._worker: threading.Thread | None = None
        self.repository.reconcile_abandoned_jobs(
            active_worker_ids={self.worker_id}
        )
        if start_worker:
            self._worker = threading.Thread(
                target=self._work,
                name="tenable-durable-web-queue",
                daemon=True,
            )
            self._worker.start()
            self.wake()

    def snapshot(self, batch_id: UUID) -> dict[str, Any]:
        batch = self.repository.get_batch(batch_id)
        if batch is None:
            raise KeyError("Lote nao encontrado.")
        return {
            "batch": batch,
            "jobs": self.repository.list_batch_jobs(batch_id),
            "events": self.repository.list_events(batch_id),
        }

    def wake(self) -> None:
        self._wake_event.set()

    def wait_until_idle(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() <= deadline:
            active = False
            for batch in self.repository.list_batches(limit=500):
                if any(
                    job.status
                    in {
                        BatchJobStatus.QUEUED,
                        BatchJobStatus.RUNNING,
                        BatchJobStatus.INTERRUPT_REQUESTED,
                    }
                    for job in self.repository.list_batch_jobs(batch.id)
                ):
                    active = True
                    break
            if not active:
                return True
            time.sleep(min(self.poll_interval, 0.05))
        return False

    def close(self) -> None:
        self._stopping.set()
        self._wake_event.set()
        if self._worker is not None:
            self._worker.join(timeout=2)

    def _work(self) -> None:
        while not self._stopping.is_set():
            job = self.repository.claim_next_job(worker_id=self.worker_id)
            if job is None:
                self._wake_event.wait(self.poll_interval)
                self._wake_event.clear()
                continue
            try:
                result = self.runner(job)
            except Exception as exc:
                result = BatchJobResult(
                    status=BatchJobStatus.FAILED,
                    exit_code=1,
                    error_code="UNEXPECTED",
                    error_message=str(exc)[:500] or "Falha operacional sem detalhe.",
                )
            self.repository.complete_job(job.id, result)


__all__ = ["DurableJobQueue", "DurableRunner"]
