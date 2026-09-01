from __future__ import annotations

import subprocess
from pathlib import Path
from uuid import UUID

import pytest

from tenable_reports.application.web_batches import (
    BatchClientConflictError,
    BatchConfirmationError,
    DerivedBatchRequest,
    NoEligibleBatchJobsError,
)
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchJob,
)
from tenable_reports.webapp.durable_dashboard_queue import (
    DurableDashboardJobQueue,
)
from tenable_reports.webapp.server import JobQueue


SOURCE_ID = UUID(int=1000)


def _source_repository(
    statuses: tuple[BatchJobStatus, ...],
) -> InMemoryWebBatchRepository:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        WebBatch(
            id=SOURCE_ID,
            idempotency_key="batch:source:derivation",
            kind="GENERATE_ALL",
            status=BatchStatus.COMPLETE_WITH_FAILURES,
            options={
                "requests": [
                    {
                        "client_id": f"client-{position}",
                        "request": {"mode": "manual", "days": 30},
                    }
                    for position in range(1, len(statuses) + 1)
                ]
            },
        ),
        tuple(
            WebBatchJob(
                id=UUID(int=1000 + position),
                batch_id=SOURCE_ID,
                client_id=f"client-{position}",
                position=position,
                status=status,
                attempt_number=2,
                payload={
                    "job_id": UUID(int=1000 + position).hex,
                    "client_id": f"client-{position}",
                    "mode": "manual",
                    "days": 30,
                    "status": status.value,
                    "run_id": f"old-run-{position}",
                },
                logical_job_id=f"logical-{position}",
            )
            for position, status in enumerate(statuses, start=1)
        ),
    )
    return repository


def _queue(tmp_path: Path, repository: InMemoryWebBatchRepository):
    def runner(command, cwd, progress_callback=None):
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        runner,
        start_worker=False,
    )
    return DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-derive",
        start_worker=False,
    )


def test_retry_incomplete_selects_only_failed_interrupted_and_cancelled(
    tmp_path: Path,
) -> None:
    repository = _source_repository(
        (
            BatchJobStatus.COMPLETE,
            BatchJobStatus.FAILED,
            BatchJobStatus.INTERRUPTED,
            BatchJobStatus.CANCELLED_BY_USER,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
        )
    )
    queue = _queue(tmp_path, repository)
    request = DerivedBatchRequest(
        source_batch_id=SOURCE_ID,
        kind=BatchAction.RETRY_INCOMPLETE,
        idempotency_key="retry-incomplete:source:one",
        actor="analista-local",
    )
    try:
        first = queue.derive_batch(request)
        second = queue.derive_batch(request)
    finally:
        queue.close()

    assert first["batch"]["id"] == second["batch"]["id"]
    assert len(repository.list_batches()) == 2
    derived_id = UUID(first["batch"]["id"])
    derived = repository.get_batch(derived_id)
    assert derived is not None
    assert derived.source_batch_id == SOURCE_ID
    assert derived.kind == "RETRY_INCOMPLETE"
    jobs = repository.list_batch_jobs(derived_id)
    assert tuple(job.client_id for job in jobs) == (
        "client-2",
        "client-3",
        "client-4",
    )
    assert all(job.status is BatchJobStatus.QUEUED for job in jobs)
    assert all(job.attempt_number == 3 for job in jobs)
    assert all(job.retry_of_batch_job_id is not None for job in jobs)
    assert all(job.run_id is None for job in jobs)
    assert all(job.control_file for job in jobs)


def test_retry_incomplete_accepts_paused_recovery_and_preserves_uuid(
    tmp_path: Path,
) -> None:
    recovered_id = UUID(int=1200)
    export_uuid = "00000000-0000-0000-0000-000000000321"
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        WebBatch(
            id=recovered_id,
            idempotency_key="batch:recovered:paused",
            kind="RECOVERED",
            status=BatchStatus.PAUSED,
        ),
        (
            WebBatchJob(
                id=UUID(int=1201),
                batch_id=recovered_id,
                client_id="client-recovered",
                position=1,
                status=BatchJobStatus.FAILED,
                attempt_number=1,
                payload={
                    "mode": "manual",
                    "days": None,
                    "start_at": "2026-07-01T03:00:00Z",
                    "end_at": "2026-08-01T03:00:00Z",
                    "vm_export_uuid": export_uuid,
                },
            ),
        ),
    )
    queue = _queue(tmp_path, repository)
    try:
        retry = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=recovered_id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry:recovered:paused",
            )
        )
    finally:
        queue.close()

    jobs = repository.list_batch_jobs(UUID(retry["batch"]["id"]))
    assert len(jobs) == 1
    assert jobs[0].payload["vm_export_uuid"] == export_uuid
    assert jobs[0].payload["start_at"] == "2026-07-01T03:00:00Z"


def test_retry_incomplete_rejects_source_without_eligible_jobs(
    tmp_path: Path,
) -> None:
    repository = _source_repository(
        (BatchJobStatus.COMPLETE, BatchJobStatus.COMPLETE_WITH_WARNINGS)
    )
    queue = _queue(tmp_path, repository)
    try:
        with pytest.raises(NoEligibleBatchJobsError):
            queue.derive_batch(
                DerivedBatchRequest(
                    source_batch_id=SOURCE_ID,
                    kind=BatchAction.RETRY_INCOMPLETE,
                    idempotency_key="retry-incomplete:none",
                )
            )
    finally:
        queue.close()


def test_rerun_all_requires_confirmation_and_copies_every_client(
    tmp_path: Path,
) -> None:
    repository = _source_repository(
        (BatchJobStatus.COMPLETE, BatchJobStatus.FAILED)
    )
    queue = _queue(tmp_path, repository)
    request = DerivedBatchRequest(
        source_batch_id=SOURCE_ID,
        kind=BatchAction.RERUN_ALL,
        idempotency_key="rerun-all:source:one",
        confirmation_token=None,
    )
    try:
        with pytest.raises(BatchConfirmationError):
            queue.derive_batch(request)
        rerun = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=SOURCE_ID,
                kind=BatchAction.RERUN_ALL,
                idempotency_key="rerun-all:source:one",
                confirmation_token=f"GERAR NOVAMENTE {str(SOURCE_ID)[:8]}",
            )
        )
    finally:
        queue.close()

    jobs = repository.list_batch_jobs(UUID(rerun["batch"]["id"]))
    assert tuple(job.client_id for job in jobs) == ("client-1", "client-2")
    assert all(job.attempt_number == 1 for job in jobs)
    assert all(job.retry_of_batch_job_id is None for job in jobs)


def test_derived_batch_rejects_client_already_active_elsewhere(
    tmp_path: Path,
) -> None:
    repository = _source_repository((BatchJobStatus.FAILED,))
    queue = _queue(tmp_path, repository)
    active_id = UUID(int=1100)
    repository.create_batch(
        WebBatch(
            id=active_id,
            idempotency_key="batch:active:conflict",
            kind="GENERATE_ONE",
            status=BatchStatus.QUEUED,
        ),
        (
            WebBatchJob(
                id=UUID(int=1101),
                batch_id=active_id,
                client_id="client-1",
                position=1,
                status=BatchJobStatus.QUEUED,
                attempt_number=1,
            ),
        ),
    )
    try:
        with pytest.raises(BatchClientConflictError) as captured:
            queue.derive_batch(
                DerivedBatchRequest(
                    source_batch_id=SOURCE_ID,
                    kind=BatchAction.RETRY_INCOMPLETE,
                    idempotency_key="retry-incomplete:conflict",
                )
            )
    finally:
        queue.close()

    assert captured.value.client_ids == ("client-1",)
    assert len(repository.list_batches()) == 2

