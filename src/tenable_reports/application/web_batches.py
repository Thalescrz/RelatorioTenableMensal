from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID

from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)


@dataclass(frozen=True, slots=True)
class BatchJobResult:
    status: BatchJobStatus
    exit_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)


class WebBatchRepository(Protocol):
    def create_batch(
        self,
        batch: WebBatch,
        jobs: Sequence[WebBatchJob],
    ) -> WebBatch: ...

    def get_batch(self, batch_id: UUID) -> WebBatch | None: ...

    def list_batches(self, *, limit: int = 50) -> tuple[WebBatch, ...]: ...

    def list_batch_jobs(self, batch_id: UUID) -> tuple[WebBatchJob, ...]: ...

    def request_action(
        self,
        batch_id: UUID,
        action: BatchAction,
    ) -> WebBatch: ...

    def claim_next_job(self, *, worker_id: str) -> WebBatchJob | None: ...

    def complete_job(self, job_id: UUID, result: BatchJobResult) -> None: ...

    def append_event(self, event: WebBatchEvent) -> None: ...

    def list_events(self, batch_id: UUID) -> tuple[WebBatchEvent, ...]: ...

    def reconcile_abandoned_jobs(
        self,
        *,
        active_worker_ids: set[str],
    ) -> int: ...
