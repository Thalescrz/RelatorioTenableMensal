from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from uuid import UUID


class BatchStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    PAUSED = "PAUSED"
    STOP_REQUESTED = "STOP_REQUESTED"
    STOPPED = "STOPPED"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_FAILURES = "COMPLETE_WITH_FAILURES"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"


class BatchJobStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_WAS_DECISION = "WAITING_WAS_DECISION"
    COMPLETE = "COMPLETE"
    COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
    FAILED = "FAILED"
    INTERRUPT_REQUESTED = "INTERRUPT_REQUESTED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED_BY_USER = "CANCELLED_BY_USER"


class BatchJobPhase(StrEnum):
    LEGACY = "LEGACY"
    REMOTE_QUEUED = "REMOTE_QUEUED"
    REMOTE_RUNNING = "REMOTE_RUNNING"
    REMOTE_WAITING_DECISION = "REMOTE_WAITING_DECISION"
    READY_FOR_BUILD = "READY_FOR_BUILD"
    BUILD_RUNNING = "BUILD_RUNNING"
    TERMINAL = "TERMINAL"


class BatchAction(StrEnum):
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    STOP = "STOP"
    RETRY_INCOMPLETE = "RETRY_INCOMPLETE"
    RERUN_ALL = "RERUN_ALL"


BATCH_TERMINAL_STATUSES = frozenset(
    {
        BatchStatus.STOPPED,
        BatchStatus.COMPLETE,
        BatchStatus.COMPLETE_WITH_FAILURES,
        BatchStatus.COMPLETE_WITH_WARNINGS,
    }
)
BATCH_JOB_TERMINAL_STATUSES = frozenset(
    {
        BatchJobStatus.COMPLETE,
        BatchJobStatus.COMPLETE_WITH_WARNINGS,
        BatchJobStatus.FAILED,
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.CANCELLED_BY_USER,
    }
)
RETRYABLE_BATCH_JOB_STATUSES = frozenset(
    {
        BatchJobStatus.FAILED,
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.CANCELLED_BY_USER,
    }
)


_ALLOWED_BATCH_TRANSITIONS: Mapping[BatchStatus, frozenset[BatchStatus]] = {
    BatchStatus.QUEUED: frozenset(
        {
            BatchStatus.RUNNING,
            BatchStatus.PAUSED,
            BatchStatus.STOP_REQUESTED,
            BatchStatus.STOPPED,
        }
    ),
    BatchStatus.RUNNING: frozenset(
        {
            BatchStatus.PAUSE_REQUESTED,
            BatchStatus.STOP_REQUESTED,
            BatchStatus.STOPPED,
            BatchStatus.COMPLETE,
            BatchStatus.COMPLETE_WITH_FAILURES,
            BatchStatus.COMPLETE_WITH_WARNINGS,
        }
    ),
    BatchStatus.PAUSE_REQUESTED: frozenset(
        {
            BatchStatus.PAUSED,
            BatchStatus.STOP_REQUESTED,
            BatchStatus.STOPPED,
            BatchStatus.COMPLETE,
            BatchStatus.COMPLETE_WITH_FAILURES,
            BatchStatus.COMPLETE_WITH_WARNINGS,
        }
    ),
    BatchStatus.PAUSED: frozenset(
        {
            BatchStatus.RUNNING,
            BatchStatus.STOP_REQUESTED,
            BatchStatus.STOPPED,
        }
    ),
    BatchStatus.STOP_REQUESTED: frozenset({BatchStatus.STOPPED}),
    BatchStatus.STOPPED: frozenset(),
    BatchStatus.COMPLETE: frozenset(),
    BatchStatus.COMPLETE_WITH_FAILURES: frozenset(),
    BatchStatus.COMPLETE_WITH_WARNINGS: frozenset(),
}

_ALLOWED_BATCH_JOB_TRANSITIONS: Mapping[
    BatchJobStatus, frozenset[BatchJobStatus]
] = {
    BatchJobStatus.QUEUED: frozenset(
        {BatchJobStatus.RUNNING, BatchJobStatus.CANCELLED_BY_USER}
    ),
    BatchJobStatus.RUNNING: frozenset(
        {
            BatchJobStatus.WAITING_WAS_DECISION,
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.FAILED,
            BatchJobStatus.INTERRUPT_REQUESTED,
            BatchJobStatus.INTERRUPTED,
        }
    ),
    BatchJobStatus.WAITING_WAS_DECISION: frozenset(
        {
            BatchJobStatus.RUNNING,
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.FAILED,
            BatchJobStatus.INTERRUPT_REQUESTED,
        }
    ),
    BatchJobStatus.INTERRUPT_REQUESTED: frozenset(
        {
            BatchJobStatus.INTERRUPTED,
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.FAILED,
        }
    ),
    BatchJobStatus.COMPLETE: frozenset(),
    BatchJobStatus.COMPLETE_WITH_WARNINGS: frozenset(),
    BatchJobStatus.FAILED: frozenset(),
    BatchJobStatus.INTERRUPTED: frozenset(),
    BatchJobStatus.CANCELLED_BY_USER: frozenset(),
}


class InvalidBatchTransitionError(ValueError):
    def __init__(self, *, current: BatchStatus, requested: BatchStatus) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"Transicao de lote invalida: {current} -> {requested}.")


class InvalidBatchJobTransitionError(ValueError):
    def __init__(
        self,
        *,
        current: BatchJobStatus,
        requested: BatchJobStatus,
    ) -> None:
        self.current = current
        self.requested = requested
        super().__init__(f"Transicao de trabalho invalida: {current} -> {requested}.")


@dataclass(frozen=True, slots=True)
class WebBatch:
    id: UUID
    idempotency_key: str
    kind: str
    status: BatchStatus
    options: Mapping[str, Any] = field(default_factory=dict)
    source_batch_id: UUID | None = None
    requested_action: BatchAction | None = None
    version: int = 0
    created_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


@dataclass(frozen=True, slots=True)
class WebBatchJob:
    id: UUID
    batch_id: UUID
    client_id: str
    position: int
    status: BatchJobStatus
    attempt_number: int
    phase: BatchJobPhase = BatchJobPhase.LEGACY
    payload: Mapping[str, Any] = field(default_factory=dict)
    retry_of_batch_job_id: UUID | None = None
    worker_id: str | None = None
    process_id: int | None = None
    control_file: str | None = None
    orchestration_run_id: str | None = None
    logical_job_id: str | None = None
    run_id: str | None = None
    exit_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    collection_checkpoint_path: str | None = None
    remote_started_at: str | None = None
    remote_ended_at: str | None = None
    build_started_at: str | None = None
    vm_export_uuid: str | None = None
    vm_resume_manifest_path: str | None = None
    remote_export_started_at: str | None = None
    remote_status_at: str | None = None
    remote_progress_at: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    ended_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class WebBatchEvent:
    batch_id: UUID
    event_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    job_id: UUID | None = None
    actor: str | None = None
    idempotency_key: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


def transition_batch(
    current: BatchStatus,
    requested: BatchStatus,
) -> BatchStatus:
    if current is requested:
        return requested
    if requested not in _ALLOWED_BATCH_TRANSITIONS[current]:
        raise InvalidBatchTransitionError(current=current, requested=requested)
    return requested


def transition_batch_job(
    current: BatchJobStatus,
    requested: BatchJobStatus,
) -> BatchJobStatus:
    if current is requested:
        return requested
    if requested not in _ALLOWED_BATCH_JOB_TRANSITIONS[current]:
        raise InvalidBatchJobTransitionError(current=current, requested=requested)
    return requested


def retryable_batch_job_ids(jobs: Iterable[WebBatchJob]) -> tuple[UUID, ...]:
    return tuple(
        job.id for job in jobs if job.status in RETRYABLE_BATCH_JOB_STATUSES
    )
