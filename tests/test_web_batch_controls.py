from __future__ import annotations

from uuid import UUID

from tenable_reports.application.web_batches import BatchJobResult
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchJob,
    transition_batch,
)


BATCH_ID = UUID(int=700)


def _repository(
    *,
    batch_status: BatchStatus,
    job_statuses: tuple[BatchJobStatus, ...],
) -> InMemoryWebBatchRepository:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        WebBatch(
            id=BATCH_ID,
            idempotency_key="batch:controls",
            kind="GENERATE_ALL",
            status=batch_status,
        ),
        tuple(
            WebBatchJob(
                id=UUID(int=700 + position),
                batch_id=BATCH_ID,
                client_id=f"client-{position}",
                position=position,
                status=status,
                attempt_number=1,
                worker_id="worker-one"
                if status
                in {
                    BatchJobStatus.RUNNING,
                    BatchJobStatus.WAITING_WAS_DECISION,
                }
                else None,
            )
            for position, status in enumerate(job_statuses, start=1)
        ),
    )
    return repository


def test_pause_waits_for_current_job_and_keeps_next_job_queued() -> None:
    repository = _repository(
        batch_status=BatchStatus.RUNNING,
        job_statuses=(BatchJobStatus.RUNNING, BatchJobStatus.QUEUED),
    )

    paused = repository.request_action(BATCH_ID, BatchAction.PAUSE)

    assert paused.status is BatchStatus.PAUSE_REQUESTED
    assert paused.requested_action is BatchAction.PAUSE
    assert tuple(job.status for job in repository.list_batch_jobs(BATCH_ID)) == (
        BatchJobStatus.RUNNING,
        BatchJobStatus.QUEUED,
    )

    repository.complete_job(
        UUID(int=701),
        BatchJobResult(status=BatchJobStatus.COMPLETE, exit_code=0),
    )

    batch = repository.get_batch(BATCH_ID)
    assert batch is not None
    assert batch.status is BatchStatus.PAUSED
    assert repository.claim_next_job(worker_id="worker-two") is None
    assert repository.list_batch_jobs(BATCH_ID)[1].status is BatchJobStatus.QUEUED


def test_pause_without_active_job_is_immediate_and_idempotent() -> None:
    repository = _repository(
        batch_status=BatchStatus.QUEUED,
        job_statuses=(BatchJobStatus.QUEUED,),
    )

    first = repository.request_action(BATCH_ID, BatchAction.PAUSE)
    second = repository.request_action(BATCH_ID, BatchAction.PAUSE)

    assert first.status is BatchStatus.PAUSED
    assert second.status is BatchStatus.PAUSED
    assert second.version == first.version


def test_resume_only_releases_jobs_that_were_still_queued() -> None:
    repository = _repository(
        batch_status=BatchStatus.PAUSED,
        job_statuses=(
            BatchJobStatus.INTERRUPTED,
            BatchJobStatus.FAILED,
            BatchJobStatus.QUEUED,
        ),
    )

    resumed = repository.request_action(BATCH_ID, BatchAction.RESUME)
    claimed = repository.claim_next_job(worker_id="worker-two")

    assert resumed.status is BatchStatus.RUNNING
    assert resumed.requested_action is None
    assert claimed is not None
    assert claimed.client_id == "client-3"
    assert tuple(job.status for job in repository.list_batch_jobs(BATCH_ID)) == (
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.FAILED,
        BatchJobStatus.RUNNING,
    )


def test_stop_interrupts_active_job_and_cancels_only_queued_jobs() -> None:
    repository = _repository(
        batch_status=BatchStatus.RUNNING,
        job_statuses=(
            BatchJobStatus.COMPLETE,
            BatchJobStatus.RUNNING,
            BatchJobStatus.QUEUED,
        ),
    )

    stopping = repository.request_action(BATCH_ID, BatchAction.STOP)

    assert stopping.status is BatchStatus.STOP_REQUESTED
    assert stopping.requested_action is BatchAction.STOP
    assert tuple(job.status for job in repository.list_batch_jobs(BATCH_ID)) == (
        BatchJobStatus.COMPLETE,
        BatchJobStatus.INTERRUPT_REQUESTED,
        BatchJobStatus.CANCELLED_BY_USER,
    )

    repository.complete_job(
        UUID(int=702),
        BatchJobResult(
            status=BatchJobStatus.INTERRUPTED,
            exit_code=130,
            error_code="INTERRUPTED_BY_USER",
        ),
    )

    batch = repository.get_batch(BATCH_ID)
    assert batch is not None
    assert batch.status is BatchStatus.STOPPED
    assert repository.request_action(BATCH_ID, BatchAction.STOP) == batch


def test_stop_without_active_job_is_immediate() -> None:
    repository = _repository(
        batch_status=BatchStatus.PAUSED,
        job_statuses=(BatchJobStatus.INTERRUPTED, BatchJobStatus.QUEUED),
    )

    stopped = repository.request_action(BATCH_ID, BatchAction.STOP)

    assert stopped.status is BatchStatus.STOPPED
    assert tuple(job.status for job in repository.list_batch_jobs(BATCH_ID)) == (
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.CANCELLED_BY_USER,
    )


def test_stop_race_preserves_a_job_that_completed_before_interrupting() -> None:
    repository = _repository(
        batch_status=BatchStatus.RUNNING,
        job_statuses=(BatchJobStatus.RUNNING, BatchJobStatus.QUEUED),
    )
    repository.request_action(BATCH_ID, BatchAction.STOP)

    repository.complete_job(
        UUID(int=701),
        BatchJobResult(status=BatchJobStatus.COMPLETE, exit_code=0),
    )

    batch = repository.get_batch(BATCH_ID)
    assert batch is not None
    assert batch.status is BatchStatus.STOPPED
    assert repository.list_batch_jobs(BATCH_ID)[0].status is BatchJobStatus.COMPLETE

def test_domain_accepts_immediate_pause_and_stop_without_active_job() -> None:
    assert transition_batch(
        BatchStatus.QUEUED,
        BatchStatus.PAUSED,
    ) is BatchStatus.PAUSED
    assert transition_batch(
        BatchStatus.QUEUED,
        BatchStatus.STOPPED,
    ) is BatchStatus.STOPPED
    assert transition_batch(
        BatchStatus.RUNNING,
        BatchStatus.STOPPED,
    ) is BatchStatus.STOPPED
    assert transition_batch(
        BatchStatus.PAUSED,
        BatchStatus.STOPPED,
    ) is BatchStatus.STOPPED
    assert transition_batch(
        BatchStatus.PAUSE_REQUESTED,
        BatchStatus.STOPPED,
    ) is BatchStatus.STOPPED
