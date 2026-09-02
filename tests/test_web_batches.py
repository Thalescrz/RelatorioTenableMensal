from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest

from tenable_reports.application.web_batches import (
    BatchJobResult,
    build_manual_batch_options,
)
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchJobPhase,
    BatchJobStatus,
    BatchStatus,
    InvalidBatchTransitionError,
    InvalidBatchJobTransitionError,
    WebBatchJob,
    WebBatch,
    WebBatchEvent,
    retryable_batch_job_ids,
    transition_batch,
    transition_batch_job,
)


def _manual_batch_clients() -> list[dict[str, object]]:
    return [
        {
            "client_id": "a",
            "enabled": True,
            "responsible_analyst_id": "ana-1",
            "responsible_analyst_name": "Analista Um",
            "responsible_analyst_active": True,
        },
        {
            "client_id": "b",
            "enabled": True,
            "responsible_analyst_id": "ana-2",
            "responsible_analyst_name": "Analista Dois",
            "responsible_analyst_active": True,
        },
        {
            "client_id": "c",
            "enabled": True,
            "responsible_analyst_id": None,
            "responsible_analyst_name": None,
            "responsible_analyst_active": False,
        },
        {
            "client_id": "d",
            "enabled": False,
            "responsible_analyst_id": None,
            "responsible_analyst_name": None,
            "responsible_analyst_active": False,
        },
    ]


def test_manual_batch_options_persist_exact_selection_exclusions_and_analysts() -> None:
    options = build_manual_batch_options(
        clients=_manual_batch_clients(),
        selected_client_ids=["a", "c"],
        selection_filter_snapshot={"analyst_id": "ana-1", "query": ""},
    )

    assert options["selected_client_ids"] == ["a", "c"]
    assert options["excluded_client_ids"] == ["b"]
    assert options["selection_filter_snapshot"] == {
        "analyst_id": "ana-1",
        "query": "",
    }
    assert options["analyst_snapshot_by_client"] == {
        "a": {
            "analyst_id": "ana-1",
            "display_name": "Analista Um",
            "active": True,
        },
        "c": {
            "analyst_id": None,
            "display_name": None,
            "active": False,
        },
    }


def test_manual_batch_options_reject_empty_selection() -> None:
    with pytest.raises(ValueError, match="EMPTY_CLIENT_SELECTION"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=[],
            selection_filter_snapshot={"analyst_id": None, "query": ""},
        )


@pytest.mark.parametrize("selected_client_id", ("unknown", "d"))
def test_manual_batch_options_reject_unknown_or_inactive_client(
    selected_client_id: str,
) -> None:
    with pytest.raises(ValueError, match="UNKNOWN_CLIENT_SELECTION"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=[selected_client_id],
            selection_filter_snapshot={"analyst_id": None, "query": ""},
        )


def test_manual_batch_options_reject_duplicate_selection() -> None:
    with pytest.raises(ValueError, match="DUPLICATE_CLIENT_SELECTION"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=["a", "a"],
            selection_filter_snapshot={"analyst_id": None, "query": ""},
        )


@pytest.mark.parametrize("invalid_filter", ("", [], 0, False))
def test_manual_batch_options_reject_falsy_invalid_filter_type(
    invalid_filter: object,
) -> None:
    with pytest.raises(ValueError, match="INVALID_SELECTION_FILTER"):
        build_manual_batch_options(
            clients=_manual_batch_clients(),
            selected_client_ids=["a"],
            selection_filter_snapshot=invalid_filter,
        )


def test_manual_batch_options_are_detached_from_mutable_inputs() -> None:
    clients = _manual_batch_clients()
    selection_filter = {"analyst_id": "ana-1", "query": ""}
    options = build_manual_batch_options(
        clients=clients,
        selected_client_ids=["a", "c"],
        selection_filter_snapshot=selection_filter,
    )
    serialized_before = json.dumps(options, ensure_ascii=False, sort_keys=True)

    clients[0]["responsible_analyst_name"] = "Nome alterado"
    clients[2]["responsible_analyst_id"] = "ana-alterada"
    clients.append({"client_id": "novo", "enabled": True})
    selection_filter["analyst_id"] = "ana-alterada"
    selection_filter["query"] = "consulta alterada"

    assert json.dumps(options, ensure_ascii=False, sort_keys=True) == serialized_before


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


def test_memory_repository_lists_jobs_and_events_for_batches_in_bulk() -> None:
    repository = InMemoryWebBatchRepository()
    batch_a = UUID(int=1000)
    batch_b = UUID(int=2000)
    unknown = UUID(int=3000)
    for batch_id, positions in ((batch_a, (2, 1)), (batch_b, (1,))):
        repository.create_batch(
            WebBatch(
                id=batch_id,
                idempotency_key=f"batch:bulk:{batch_id}",
                kind="GENERATE_ALL",
                status=BatchStatus.QUEUED,
            ),
            tuple(
                WebBatchJob(
                    id=UUID(int=int(batch_id) + position),
                    batch_id=batch_id,
                    client_id=f"client-{batch_id}-{position}",
                    position=position,
                    status=BatchJobStatus.QUEUED,
                    attempt_number=1,
                )
                for position in positions
            ),
        )
    for event_type in ("JOB_STARTED", "JOB_PROGRESS", "JOB_FINISHED"):
        repository.append_event(
            WebBatchEvent(
                batch_id=batch_b,
                event_type=event_type,
                payload={},
            )
        )

    jobs_by_batch = repository.list_batch_jobs_for_batches(
        (batch_a, batch_b, unknown)
    )
    events_by_batch = repository.list_events_for_batches(
        (batch_a, batch_b, unknown)
    )

    assert tuple(job.position for job in jobs_by_batch[batch_a]) == (1, 2)
    assert tuple(job.position for job in jobs_by_batch[batch_b]) == (1,)
    assert jobs_by_batch[unknown] == ()
    assert tuple(event.event_type for event in events_by_batch[batch_b])[-3:] == (
        "JOB_STARTED",
        "JOB_PROGRESS",
        "JOB_FINISHED",
    )
    assert events_by_batch[unknown] == ()


def test_legacy_phase_is_default_and_default_claim_remains_compatible() -> None:
    repository = InMemoryWebBatchRepository()
    job = _job(position=1, status=BatchJobStatus.QUEUED)
    _store_jobs(repository, (job,))

    claimed = repository.claim_next_job(worker_id="legacy-worker")

    assert claimed is not None
    assert claimed.status is BatchJobStatus.RUNNING
    assert claimed.phase is BatchJobPhase.LEGACY


def test_phase_specific_claims_keep_remote_and_build_pools_separate() -> None:
    repository = InMemoryWebBatchRepository()
    remote = _job(
        position=1,
        status=BatchJobStatus.QUEUED,
        phase=BatchJobPhase.REMOTE_QUEUED,
    )
    build = _job(
        position=2,
        status=BatchJobStatus.QUEUED,
        phase=BatchJobPhase.READY_FOR_BUILD,
    )
    _store_jobs(repository, (remote, build))

    claimed_remote = repository.claim_next_job(
        worker_id="remote-worker",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
    )
    claimed_build = repository.claim_next_job(
        worker_id="build-worker",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
    )

    assert claimed_remote is not None
    assert claimed_remote.id == remote.id
    assert claimed_remote.phase is BatchJobPhase.REMOTE_RUNNING
    assert claimed_remote.remote_started_at is not None
    assert claimed_remote.build_started_at is None
    assert claimed_build is not None
    assert claimed_build.id == build.id
    assert claimed_build.phase is BatchJobPhase.BUILD_RUNNING
    assert claimed_build.build_started_at is not None


def test_collection_ready_advances_same_job_atomically(tmp_path: Path) -> None:
    repository = InMemoryWebBatchRepository()
    checkpoint = tmp_path / "collection-checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    job = _job(
        position=1,
        status=BatchJobStatus.RUNNING,
        phase=BatchJobPhase.REMOTE_RUNNING,
        worker_id="remote-worker",
    )
    _store_jobs(repository, (job,), batch_status=BatchStatus.RUNNING)

    advanced = repository.advance_job_phase(
        job.id,
        expected_phase=BatchJobPhase.REMOTE_RUNNING,
        requested_phase=BatchJobPhase.READY_FOR_BUILD,
        collection_checkpoint_path=checkpoint,
    )

    assert advanced.id == job.id
    assert advanced.status is BatchJobStatus.QUEUED
    assert advanced.phase is BatchJobPhase.READY_FOR_BUILD
    assert advanced.collection_checkpoint_path == str(checkpoint.resolve())
    assert advanced.remote_ended_at is not None
    assert advanced.worker_id is None
    assert repository.list_events(job.batch_id)[-1].event_type == "COLLECTION_READY"


def test_collection_ready_rejects_missing_checkpoint_without_state_change(
    tmp_path: Path,
) -> None:
    repository = InMemoryWebBatchRepository()
    job = _job(
        position=1,
        status=BatchJobStatus.RUNNING,
        phase=BatchJobPhase.REMOTE_RUNNING,
        worker_id="remote-worker",
    )
    _store_jobs(repository, (job,), batch_status=BatchStatus.RUNNING)

    with pytest.raises(ValueError, match="checkpoint"):
        repository.advance_job_phase(
            job.id,
            expected_phase=BatchJobPhase.REMOTE_RUNNING,
            requested_phase=BatchJobPhase.READY_FOR_BUILD,
            collection_checkpoint_path=tmp_path / "missing.json",
        )

    stored = repository.list_batch_jobs(job.batch_id)[0]
    assert stored.status is BatchJobStatus.RUNNING
    assert stored.phase is BatchJobPhase.REMOTE_RUNNING
    assert stored.collection_checkpoint_path is None


def test_complete_job_marks_the_staged_phase_terminal(tmp_path: Path) -> None:
    repository = InMemoryWebBatchRepository()
    checkpoint = tmp_path / "collection-checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    job = _job(
        position=1,
        status=BatchJobStatus.RUNNING,
        phase=BatchJobPhase.BUILD_RUNNING,
        checkpoint_path=str(checkpoint.resolve()),
        worker_id="build-worker",
    )
    _store_jobs(repository, (job,), batch_status=BatchStatus.RUNNING)

    repository.complete_job(
        job.id,
        BatchJobResult(
            status=BatchJobStatus.FAILED,
            exit_code=1,
            error_code="LOCAL_BUILD_FAILED",
        ),
    )

    completed = repository.list_batch_jobs(job.batch_id)[0]
    assert completed.status is BatchJobStatus.FAILED
    assert completed.phase is BatchJobPhase.TERMINAL
    assert completed.collection_checkpoint_path == str(checkpoint.resolve())


def test_reconcile_abandoned_staged_jobs_returns_each_to_its_queue(
    tmp_path: Path,
) -> None:
    repository = InMemoryWebBatchRepository()
    checkpoint = tmp_path / "collection-checkpoint.json"
    checkpoint.write_text("{}", encoding="utf-8")
    remote = _job(
        position=1,
        status=BatchJobStatus.RUNNING,
        phase=BatchJobPhase.REMOTE_RUNNING,
        worker_id="gone-remote",
    )
    build = _job(
        position=2,
        status=BatchJobStatus.RUNNING,
        phase=BatchJobPhase.BUILD_RUNNING,
        checkpoint_path=str(checkpoint.resolve()),
        worker_id="gone-build",
    )
    _store_jobs(repository, (remote, build), batch_status=BatchStatus.RUNNING)

    reconciled = repository.reconcile_abandoned_jobs(
        active_worker_ids={"current-worker"}
    )

    assert reconciled == 2
    stored_remote, stored_build = repository.list_batch_jobs(remote.batch_id)
    assert (stored_remote.status, stored_remote.phase) == (
        BatchJobStatus.QUEUED,
        BatchJobPhase.REMOTE_QUEUED,
    )
    assert (stored_build.status, stored_build.phase) == (
        BatchJobStatus.QUEUED,
        BatchJobPhase.READY_FOR_BUILD,
    )
    assert stored_build.collection_checkpoint_path == str(checkpoint.resolve())


def test_reconcile_abandoned_stop_request_finishes_batch() -> None:
    repository = InMemoryWebBatchRepository()
    job = _job(
        position=1,
        status=BatchJobStatus.INTERRUPT_REQUESTED,
        worker_id=None,
    )
    _store_jobs(repository, (job,), batch_status=BatchStatus.STOP_REQUESTED)

    reconciled = repository.reconcile_abandoned_jobs(active_worker_ids=set())

    assert reconciled == 1
    (stored_job,) = repository.list_batch_jobs(job.batch_id)
    assert (stored_job.status, stored_job.phase) == (
        BatchJobStatus.INTERRUPTED,
        BatchJobPhase.TERMINAL,
    )
    stored_batch = repository.get_batch(job.batch_id)
    assert stored_batch is not None
    assert stored_batch.status is BatchStatus.STOPPED
    assert stored_batch.ended_at is not None


def _store_jobs(
    repository: InMemoryWebBatchRepository,
    jobs: tuple[WebBatchJob, ...],
    *,
    batch_status: BatchStatus = BatchStatus.QUEUED,
) -> None:
    repository.create_batch(
        WebBatch(
            id=UUID(int=100),
            idempotency_key="batch:phase-tests",
            kind="GENERATE_ALL",
            status=batch_status,
        ),
        jobs,
    )


def _job(
    *,
    position: int,
    status: BatchJobStatus,
    phase: BatchJobPhase = BatchJobPhase.LEGACY,
    checkpoint_path: str | None = None,
    worker_id: str | None = None,
) -> WebBatchJob:
    return WebBatchJob(
        id=UUID(int=position),
        batch_id=UUID(int=100),
        client_id=f"client-{position}",
        position=position,
        status=status,
        attempt_number=1,
        phase=phase,
        collection_checkpoint_path=checkpoint_path,
        worker_id=worker_id,
    )
