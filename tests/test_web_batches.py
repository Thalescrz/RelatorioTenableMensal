from __future__ import annotations

from uuid import UUID

import pytest

from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    InvalidBatchTransitionError,
    InvalidBatchJobTransitionError,
    WebBatchJob,
    retryable_batch_job_ids,
    transition_batch,
    transition_batch_job,
)


@pytest.mark.parametrize(
    ("current", "requested"),
    (
        (BatchStatus.QUEUED, BatchStatus.RUNNING),
        (BatchStatus.RUNNING, BatchStatus.PAUSE_REQUESTED),
        (BatchStatus.PAUSE_REQUESTED, BatchStatus.PAUSED),
        (BatchStatus.PAUSED, BatchStatus.RUNNING),
        (BatchStatus.RUNNING, BatchStatus.STOP_REQUESTED),
        (BatchStatus.STOP_REQUESTED, BatchStatus.STOPPED),
        (BatchStatus.RUNNING, BatchStatus.COMPLETE),
        (BatchStatus.RUNNING, BatchStatus.COMPLETE_WITH_FAILURES),
        (BatchStatus.RUNNING, BatchStatus.COMPLETE_WITH_WARNINGS),
    ),
)
def test_batch_transition_accepts_the_approved_lifecycle(
    current: BatchStatus,
    requested: BatchStatus,
) -> None:
    assert transition_batch(current, requested) is requested


@pytest.mark.parametrize(
    "terminal",
    (
        BatchStatus.STOPPED,
        BatchStatus.COMPLETE,
        BatchStatus.COMPLETE_WITH_FAILURES,
        BatchStatus.COMPLETE_WITH_WARNINGS,
    ),
)
def test_batch_transition_rejects_leaving_a_terminal_state(
    terminal: BatchStatus,
) -> None:
    with pytest.raises(InvalidBatchTransitionError) as captured:
        transition_batch(terminal, BatchStatus.RUNNING)

    assert captured.value.current is terminal
    assert captured.value.requested is BatchStatus.RUNNING


@pytest.mark.parametrize(
    ("current", "requested"),
    (
        (BatchJobStatus.QUEUED, BatchJobStatus.RUNNING),
        (BatchJobStatus.QUEUED, BatchJobStatus.CANCELLED_BY_USER),
        (BatchJobStatus.RUNNING, BatchJobStatus.WAITING_WAS_DECISION),
        (BatchJobStatus.WAITING_WAS_DECISION, BatchJobStatus.RUNNING),
        (BatchJobStatus.RUNNING, BatchJobStatus.INTERRUPT_REQUESTED),
        (BatchJobStatus.INTERRUPT_REQUESTED, BatchJobStatus.INTERRUPTED),
        (BatchJobStatus.RUNNING, BatchJobStatus.COMPLETE),
        (BatchJobStatus.RUNNING, BatchJobStatus.COMPLETE_WITH_WARNINGS),
        (BatchJobStatus.RUNNING, BatchJobStatus.FAILED),
    ),
)
def test_batch_job_transition_accepts_the_approved_lifecycle(
    current: BatchJobStatus,
    requested: BatchJobStatus,
) -> None:
    assert transition_batch_job(current, requested) is requested


def test_batch_job_transition_rejects_requeueing_an_interrupted_job() -> None:
    with pytest.raises(InvalidBatchJobTransitionError):
        transition_batch_job(BatchJobStatus.INTERRUPTED, BatchJobStatus.QUEUED)


def test_retry_selection_contains_only_failed_interrupted_and_cancelled_jobs() -> None:
    jobs = (
        _job(position=1, status=BatchJobStatus.COMPLETE),
        _job(position=2, status=BatchJobStatus.FAILED),
        _job(position=3, status=BatchJobStatus.INTERRUPTED),
        _job(position=4, status=BatchJobStatus.CANCELLED_BY_USER),
        _job(position=5, status=BatchJobStatus.COMPLETE_WITH_WARNINGS),
    )

    assert retryable_batch_job_ids(jobs) == (
        UUID(int=2),
        UUID(int=3),
        UUID(int=4),
    )


def _job(*, position: int, status: BatchJobStatus) -> WebBatchJob:
    return WebBatchJob(
        id=UUID(int=position),
        batch_id=UUID(int=100),
        client_id=f"client-{position}",
        position=position,
        status=status,
        attempt_number=1,
    )

