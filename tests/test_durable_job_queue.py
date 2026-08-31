from __future__ import annotations

import threading
from uuid import UUID

from tenable_reports.application.web_batches import BatchJobResult
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchJob,
)
from tenable_reports.webapp.job_queue import DurableJobQueue


def _batch(*, status: BatchStatus = BatchStatus.QUEUED) -> WebBatch:
    return WebBatch(
        id=UUID(int=1),
        idempotency_key="batch:test:one",
        kind="GENERATE_ALL",
        status=status,
        options={"mode": "manual"},
    )


def _job(
    position: int,
    *,
    status: BatchJobStatus = BatchJobStatus.QUEUED,
    worker_id: str | None = None,
) -> WebBatchJob:
    return WebBatchJob(
        id=UUID(int=10 + position),
        batch_id=UUID(int=1),
        client_id=f"client-{position}",
        position=position,
        status=status,
        attempt_number=1,
        worker_id=worker_id,
    )


def test_queue_snapshot_survives_queue_recreation() -> None:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(_batch(), (_job(1), _job(2)))
    first = DurableJobQueue(
        repository=repository,
        runner=_successful_runner,
        worker_id="worker-first",
        start_worker=False,
    )
    first.close()

    second = DurableJobQueue(
        repository=repository,
        runner=_successful_runner,
        worker_id="worker-second",
        start_worker=False,
    )
    try:
        snapshot = second.snapshot(UUID(int=1))
    finally:
        second.close()

    assert snapshot["batch"].id == UUID(int=1)
    assert tuple(job.client_id for job in snapshot["jobs"]) == (
        "client-1",
        "client-2",
    )


def test_queue_runs_at_most_one_client_at_a_time_in_position_order() -> None:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(_batch(), (_job(1), _job(2)))
    first_started = threading.Event()
    release_first = threading.Event()
    lock = threading.Lock()
    order: list[str] = []
    active = 0
    maximum_active = 0

    def runner(job: WebBatchJob) -> BatchJobResult:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
            order.append(job.client_id)
        if job.position == 1:
            first_started.set()
            assert release_first.wait(2)
        with lock:
            active -= 1
        return BatchJobResult(status=BatchJobStatus.COMPLETE)

    queue = DurableJobQueue(
        repository=repository,
        runner=runner,
        worker_id="worker-one",
        poll_interval=0.01,
    )
    try:
        assert first_started.wait(2)
        jobs_while_first_runs = repository.list_batch_jobs(UUID(int=1))
        assert tuple(job.status for job in jobs_while_first_runs) == (
            BatchJobStatus.RUNNING,
            BatchJobStatus.QUEUED,
        )
        release_first.set()
        assert queue.wait_until_idle(timeout=2)
    finally:
        release_first.set()
        queue.close()

    assert order == ["client-1", "client-2"]
    assert maximum_active == 1
    assert repository.get_batch(UUID(int=1)).status is BatchStatus.COMPLETE


def test_queue_reconciles_abandoned_running_job_as_interrupted_and_pauses_batch() -> None:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        _batch(status=BatchStatus.RUNNING),
        (
            _job(1, status=BatchJobStatus.RUNNING, worker_id="old-worker"),
            _job(2),
        ),
    )

    queue = DurableJobQueue(
        repository=repository,
        runner=_successful_runner,
        worker_id="new-worker",
        start_worker=False,
    )
    try:
        snapshot = queue.snapshot(UUID(int=1))
    finally:
        queue.close()

    assert snapshot["batch"].status is BatchStatus.PAUSED
    assert tuple(job.status for job in snapshot["jobs"]) == (
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.QUEUED,
    )
    assert snapshot["events"][-1].event_type == "JOB_RECOVERED_AS_INTERRUPTED"


def _successful_runner(job: WebBatchJob) -> BatchJobResult:
    return BatchJobResult(status=BatchJobStatus.COMPLETE)
