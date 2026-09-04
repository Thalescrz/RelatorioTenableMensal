from __future__ import annotations

import subprocess
from dataclasses import replace
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
    BatchJobPhase,
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
                    **(
                        {
                            "component_set_status": "PARTIAL_FAILURE",
                            "retryable_components": ["WAS"],
                        }
                        if status is BatchJobStatus.PARTIALLY_COMPLETE
                        else {}
                    ),
                },
                logical_job_id=f"logical-{position}",
                error_code=(
                    "TENABLE_TEMPORARY"
                    if status is BatchJobStatus.FAILED
                    else None
                ),
                error_message=(
                    "Tempo maximo excedido aguardando o export VM."
                    if status is BatchJobStatus.FAILED
                    else None
                ),
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


def test_retry_incomplete_selects_partial_failed_interrupted_and_cancelled(
    tmp_path: Path,
) -> None:
    repository = _source_repository(
        (
            BatchJobStatus.COMPLETE,
            BatchJobStatus.FAILED,
            BatchJobStatus.INTERRUPTED,
            BatchJobStatus.CANCELLED_BY_USER,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.PARTIALLY_COMPLETE,
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
        "client-6",
    )
    assert all(job.status is BatchJobStatus.QUEUED for job in jobs)
    assert all(job.attempt_number == 3 for job in jobs)
    assert all(job.retry_of_batch_job_id is not None for job in jobs)
    assert all(job.run_id is None for job in jobs)
    assert all(job.control_file for job in jobs)


def test_retry_incomplete_turns_partial_job_into_component_only_retry(
    tmp_path: Path,
) -> None:
    repository = _source_repository((BatchJobStatus.PARTIALLY_COMPLETE,))
    source = repository.list_batch_jobs(SOURCE_ID)[0]
    repository._jobs[source.id] = replace(
        source,
        payload={
            **dict(source.payload),
            "run_id": "published-run-a",
            "component_set_status": "PARTIAL_FAILURE",
            "retryable_components": ["WAS", "CLOUD"],
        },
        run_id="published-run-a",
    )
    queue = _queue(tmp_path, repository)
    try:
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=SOURCE_ID,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-partial-components",
            )
        )
    finally:
        queue.close()

    retried = repository.list_batch_jobs(UUID(detail["batch"]["id"]))[0]
    assert retried.phase is BatchJobPhase.LEGACY
    assert retried.payload["operation"] == "component_retry"
    assert retried.payload["source_run_id"] == "published-run-a"
    assert retried.payload["selected_components"] == ["WAS", "CLOUD"]


def test_retry_incomplete_skips_non_retryable_partial_without_blocking_failures(
    tmp_path: Path,
) -> None:
    repository = _source_repository((
        BatchJobStatus.PARTIALLY_COMPLETE,
        BatchJobStatus.FAILED,
    ))
    partial = repository.list_batch_jobs(SOURCE_ID)[0]
    repository._jobs[partial.id] = replace(
        partial,
        payload={
            **dict(partial.payload),
            "retryable_components": [],
        },
    )
    queue = _queue(tmp_path, repository)
    try:
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=SOURCE_ID,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-skip-non-retryable-partial",
            )
        )
    finally:
        queue.close()

    jobs = repository.list_batch_jobs(UUID(detail["batch"]["id"]))
    assert tuple(job.client_id for job in jobs) == ("client-2",)


def test_retry_incomplete_excludes_genuinely_non_retryable_failed_job(
    tmp_path: Path,
) -> None:
    repository = _source_repository((
        BatchJobStatus.FAILED,
        BatchJobStatus.FAILED,
    ))
    definitive, transient = repository.list_batch_jobs(SOURCE_ID)
    repository._jobs[definitive.id] = replace(
        definitive,
        error_code="PROFILE_INVALID",
        error_message="Perfil do cliente invalido.",
    )
    repository._jobs[transient.id] = replace(
        transient,
        error_code="UNEXPECTED",
        error_message="Export VM ficou sem progresso por 2598 segundos.",
    )
    queue = _queue(tmp_path, repository)
    try:
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=SOURCE_ID,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-only-effective-retryable-failures",
            )
        )
    finally:
        queue.close()

    jobs = repository.list_batch_jobs(UUID(detail["batch"]["id"]))
    assert tuple(job.client_id for job in jobs) == ("client-2",)


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
    derived_batch = repository.get_batch(UUID(retry["batch"]["id"]))
    assert derived_batch is not None
    assert derived_batch.options["execution_model"] == "STAGED_V1"
    assert len(jobs) == 1
    assert jobs[0].phase is BatchJobPhase.REMOTE_QUEUED
    assert jobs[0].payload["vm_export_uuid"] == export_uuid
    assert jobs[0].payload["start_at"] == "2026-07-01T03:00:00Z"


def test_retry_preserves_uuid_manifest_and_original_remote_budget(tmp_path: Path) -> None:
    repository = InMemoryWebBatchRepository()
    source = WebBatch(
        id=UUID(int=1250),
        idempotency_key="batch:preserved:vm",
        kind="GENERATE_ALL",
        status=BatchStatus.COMPLETE_WITH_FAILURES,
    )
    manifest = (tmp_path / "manifest.partial.json").resolve()
    manifest.write_text("{}", encoding="utf-8")
    repository.create_batch(source, (WebBatchJob(
        id=UUID(int=1251),
        batch_id=source.id,
        client_id="client-preserved",
        position=1,
        status=BatchJobStatus.FAILED,
        phase=BatchJobPhase.TERMINAL,
        attempt_number=1,
        payload={"mode": "manual", "days": 30},
        vm_export_uuid="00000000-0000-0000-0000-000000000888",
        vm_resume_manifest_path=str(manifest),
        remote_export_started_at="2026-09-02T00:00:00Z",
    ),))
    queue = _queue(tmp_path, repository)
    try:
        detail = queue.derive_batch(DerivedBatchRequest(
            source_batch_id=source.id,
            kind=BatchAction.RETRY_INCOMPLETE,
            idempotency_key="retry:preserved:vm",
        ))
    finally:
        queue.close()
    retried = repository.list_batch_jobs(UUID(detail["batch"]["id"]))[0]
    assert retried.payload["vm_export_uuid"].endswith("0888")
    assert retried.payload["vm_resume_manifest"] == str(manifest)
    assert retried.remote_export_started_at == "2026-09-02T00:00:00Z"


def test_individual_retry_derives_only_selected_failed_job(tmp_path: Path) -> None:
    repository = _source_repository(
        (BatchJobStatus.FAILED, BatchJobStatus.FAILED)
    )
    first, second = repository.list_batch_jobs(SOURCE_ID)
    repository.record_vm_export_progress(
        first.id,
        export_uuid="00000000-0000-0000-0000-000000000999",
        resume_manifest_path=str(tmp_path / "manifest.partial.json"),
        origin="created",
        remote_status="PROCESSING",
        observed_at="2026-09-02T10:00:00Z",
        progress_at="2026-09-02T10:00:00Z",
        completed_chunks=1,
        total_chunks=2,
        persisted_chunks=(1,),
    )
    queue = _queue(tmp_path, repository)

    try:
        retried = queue.retry(first.id.hex)
    finally:
        queue.close()

    derived_id = next(
        batch.id for batch in repository.list_batches() if batch.id != SOURCE_ID
    )
    jobs = repository.list_batch_jobs(derived_id)
    assert len(jobs) == 1
    assert jobs[0].client_id == first.client_id
    assert jobs[0].retry_of_batch_job_id == first.id
    assert jobs[0].vm_export_uuid.endswith("0999")
    assert jobs[0].payload["vm_export_uuid"].endswith("0999")
    assert second.id != jobs[0].retry_of_batch_job_id


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
