from __future__ import annotations

import json
import subprocess

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import tenable_reports.webapp.server as server_module

from tenable_reports.application.web_batches import BatchJobResult
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobPhase,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)
from tenable_reports.webapp.durable_dashboard_queue import (
    DurableDashboardJobQueue,
)
from tenable_reports.webapp.job_queue import (
    DurableJobQueue,
    DurableWorkerPool,
    DurableWorkerPoolGroup,
)
from tenable_reports.webapp.server import (
    DashboardApplication,
    DashboardConfigStore,
    JobQueue,
)


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
    phase: BatchJobPhase = BatchJobPhase.LEGACY,
) -> WebBatchJob:
    return WebBatchJob(
        id=UUID(int=10 + position),
        batch_id=UUID(int=1),
        client_id=f"client-{position}",
        position=position,
        status=status,
        attempt_number=1,
        worker_id=worker_id,
        phase=phase,
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
    assert snapshot["batch"].status is BatchStatus.PAUSED
    assert tuple(job.client_id for job in snapshot["jobs"]) == (
        "client-1",
        "client-2",
    )


def test_queue_runs_at_most_one_client_at_a_time_in_position_order() -> None:
    repository = InMemoryWebBatchRepository()
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
    repository.create_batch(_batch(), (_job(1), _job(2)))
    queue.wake()
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


def test_remote_pool_can_claim_twenty_clients_concurrently() -> None:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        _batch(),
        tuple(
            _job(position, phase=BatchJobPhase.REMOTE_QUEUED)
            for position in range(1, 21)
        ),
    )
    all_started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    active_clients: set[str] = set()
    maximum_active = 0

    def runner(job: WebBatchJob) -> BatchJobResult:
        nonlocal maximum_active
        with lock:
            active_clients.add(job.client_id)
            maximum_active = max(maximum_active, len(active_clients))
            if len(active_clients) == 20:
                all_started.set()
        assert release.wait(3)
        with lock:
            active_clients.remove(job.client_id)
        return BatchJobResult(status=BatchJobStatus.COMPLETE)

    pool = DurableWorkerPool(
        repository=repository,
        runner=runner,
        worker_prefix="tenable-remote",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
        workers=20,
        poll_interval=0.01,
    )
    try:
        pool.wake()
        assert all_started.wait(3)
        running = repository.list_batch_jobs(UUID(int=1))
        assert all(job.status is BatchJobStatus.RUNNING for job in running)
        assert all(job.phase is BatchJobPhase.REMOTE_RUNNING for job in running)
        release.set()
        assert pool.wait_until_idle(timeout=3)
    finally:
        release.set()
        pool.close()

    assert maximum_active == 20
    assert not any(
        thread.name.startswith("tenable-remote-")
        for thread in threading.enumerate()
    )
    assert all(
        job.status is BatchJobStatus.COMPLETE
        for job in repository.list_batch_jobs(UUID(int=1))
    )


def test_build_pool_with_one_worker_never_runs_two_jobs() -> None:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        _batch(),
        tuple(
            _job(position, phase=BatchJobPhase.READY_FOR_BUILD)
            for position in range(1, 4)
        ),
    )
    first_started = threading.Event()
    release_first = threading.Event()
    lock = threading.Lock()
    active = 0
    maximum_active = 0

    def runner(job: WebBatchJob) -> BatchJobResult:
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        if job.position == 1:
            first_started.set()
            assert release_first.wait(3)
        with lock:
            active -= 1
        return BatchJobResult(status=BatchJobStatus.COMPLETE)

    pool = DurableWorkerPool(
        repository=repository,
        runner=runner,
        worker_prefix="tenable-build",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
        workers=1,
        poll_interval=0.01,
    )
    try:
        assert first_started.wait(3)
        statuses = tuple(
            job.status for job in repository.list_batch_jobs(UUID(int=1))
        )
        assert statuses == (
            BatchJobStatus.RUNNING,
            BatchJobStatus.QUEUED,
            BatchJobStatus.QUEUED,
        )
        release_first.set()
        assert pool.wait_until_idle(timeout=3)
    finally:
        release_first.set()
        pool.close()

    assert pool.worker_count == 1
    assert maximum_active == 1
    assert not any(
        thread.name.startswith("tenable-build-")
        for thread in threading.enumerate()
    )


def test_pool_group_reconciles_once_with_every_sibling_worker_id() -> None:
    class TrackingRepository(InMemoryWebBatchRepository):
        def __init__(self) -> None:
            super().__init__()
            self.reconcile_calls: list[set[str]] = []

        def reconcile_abandoned_jobs(self, *, active_worker_ids: set[str]) -> int:
            self.reconcile_calls.append(set(active_worker_ids))
            return super().reconcile_abandoned_jobs(
                active_worker_ids=active_worker_ids
            )

    repository = TrackingRepository()
    remote = DurableWorkerPool(
        repository=repository,
        runner=_successful_runner,
        worker_prefix="tenable-remote",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
        workers=2,
        start_workers=False,
        reconcile=False,
    )
    build = DurableWorkerPool(
        repository=repository,
        runner=_successful_runner,
        worker_prefix="tenable-build",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
        workers=1,
        start_workers=False,
        reconcile=False,
    )

    group = DurableWorkerPoolGroup(
        repository=repository,
        pools=(remote, build),
        start_workers=False,
    )
    try:
        assert repository.reconcile_calls == [
            set(remote.worker_ids + build.worker_ids)
        ]
    finally:
        group.close()


def test_pause_blocks_new_remote_claims() -> None:
    repository = InMemoryWebBatchRepository()
    repository.create_batch(
        _batch(),
        (_job(1, phase=BatchJobPhase.REMOTE_QUEUED),),
    )
    repository.request_action(UUID(int=1), BatchAction.PAUSE)
    entered = threading.Event()
    pool = DurableWorkerPool(
        repository=repository,
        runner=lambda job: (
            entered.set() or BatchJobResult(status=BatchJobStatus.COMPLETE)
        ),
        worker_prefix="tenable-remote",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
        workers=1,
        poll_interval=0.01,
    )
    try:
        pool.wake()
        assert not entered.wait(0.2)
    finally:
        pool.close()

    stored = repository.list_batch_jobs(UUID(int=1))[0]
    assert stored.status is BatchJobStatus.QUEUED
    assert stored.phase is BatchJobPhase.REMOTE_QUEUED


def test_repository_prevents_concurrent_jobs_for_the_same_client() -> None:
    repository = InMemoryWebBatchRepository()
    barrier = threading.Barrier(2)

    def create(index: int) -> bool:
        batch = WebBatch(
            id=UUID(int=index),
            idempotency_key=f"batch:same-client:{index}",
            kind="GENERATE_ONE",
            status=BatchStatus.QUEUED,
            options={"mode": "manual"},
        )
        job = WebBatchJob(
            id=UUID(int=100 + index),
            batch_id=batch.id,
            client_id="client-shared",
            position=1,
            status=BatchJobStatus.QUEUED,
            attempt_number=1,
            phase=BatchJobPhase.REMOTE_QUEUED,
        )
        barrier.wait()
        try:
            repository.create_batch(batch, (job,))
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(create, (1, 2)))

    assert sorted(results) == [False, True]
    assert len(repository.list_batches(limit=10)) == 1


def test_pause_preserves_running_build_checkpoint_and_blocks_sibling(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    checkpoint = (tmp_path / "collection-checkpoint.json").resolve()
    checkpoint.write_text("{}", encoding="utf-8")
    repository.create_batch(
        _batch(),
        (
            replace(
                _job(1, phase=BatchJobPhase.READY_FOR_BUILD),
                collection_checkpoint_path=str(checkpoint),
            ),
            _job(2, phase=BatchJobPhase.READY_FOR_BUILD),
        ),
    )
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()

    def runner(job: WebBatchJob) -> BatchJobResult:
        if job.position == 1:
            first_started.set()
            assert release.wait(3)
        else:
            second_started.set()
        return BatchJobResult(status=BatchJobStatus.COMPLETE)

    pool = DurableWorkerPool(
        repository=repository,
        runner=runner,
        worker_prefix="tenable-build",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
        workers=1,
        poll_interval=0.01,
    )
    try:
        assert first_started.wait(3)
        paused = repository.request_action(UUID(int=1), BatchAction.PAUSE)
        assert paused.status is BatchStatus.PAUSE_REQUESTED
        assert not second_started.wait(0.2)
        running = repository.list_batch_jobs(UUID(int=1))[0]
        assert running.status is BatchJobStatus.RUNNING
        assert running.collection_checkpoint_path == str(checkpoint)
    finally:
        release.set()
        pool.close()

    jobs = repository.list_batch_jobs(UUID(int=1))
    assert jobs[0].status is BatchJobStatus.COMPLETE
    assert jobs[0].collection_checkpoint_path == str(checkpoint)
    assert jobs[1].status is BatchJobStatus.QUEUED


def test_legacy_executor_can_run_without_worker_and_forward_progress(tmp_path) -> None:
    forwarded: list[tuple[str, dict[str, object]]] = []

    def runner(command, cwd, progress_callback=None):
        progress_callback(
            {
                "event": "TENABLE_EXPORT_PROGRESS",
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "export-fixture",
                "status": "PROCESSING",
                "completed_chunks": 1,
                "total_chunks": 2,
            }
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"status": "COMPLETE", "run_id": "run-fixture"}),
            stderr="",
        )

    queue = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        runner,
        start_worker=False,
        progress_sink=lambda job_id, event: forwarded.append(
            (job_id, dict(event))
        ),
    )
    created = queue.enqueue(["client-1"], {"mode": "manual", "days": 30})[0]

    queue._run(created["job_id"])

    assert queue.snapshot()[0]["status"] == "COMPLETE"
    assert forwarded == [
        (
            created["job_id"],
            {
                "event": "TENABLE_EXPORT_PROGRESS",
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "export-fixture",
                "status": "PROCESSING",
                "completed_chunks": 1,
                "total_chunks": 2,
            },
        )
    ]


def test_dashboard_queue_persists_completed_jobs_across_recreation(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()

    def runner(command, cwd, progress_callback=None):
        client_id = command[command.index("--client") + 1]
        progress_callback(
            {
                "event": "TENABLE_EXPORT_PROGRESS",
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": f"export-{client_id}",
                "status": "FINISHED",
                "completed_chunks": 1,
                "total_chunks": 1,
            }
        )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {"status": "COMPLETE", "run_id": f"run-{client_id}"}
            ),
            stderr="",
        )

    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        runner,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-one",
        poll_interval=0.01,
    )
    try:
        created = queue.enqueue(
            ["client-1", "client-2"],
            {"mode": "manual", "days": 30},
        )
        assert len({row["batch_id"] for row in created}) == 1
        assert queue.wait_until_idle(timeout=2)
        first_snapshot = queue.snapshot()
    finally:
        queue.close()

    recreated_legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        runner,
        start_worker=False,
    )
    recreated = DurableDashboardJobQueue(
        repository=repository,
        executor=recreated_legacy,
        worker_id="worker-two",
        start_worker=False,
    )
    try:
        second_snapshot = recreated.snapshot()
    finally:
        recreated.close()

    assert [row["client_id"] for row in first_snapshot] == [
        "client-2",
        "client-1",
    ]
    assert {row["status"] for row in first_snapshot} == {"COMPLETE"}
    assert second_snapshot == first_snapshot
    assert all(row["export_progress"]["status"] == "FINISHED" for row in second_snapshot)


def test_dashboard_snapshot_loads_batches_jobs_and_events_once(tmp_path) -> None:
    class TrackingRepository(InMemoryWebBatchRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = {
                "list_batches": 0,
                "list_batch_jobs_for_batches": 0,
                "list_events_for_batches": 0,
            }

        def list_batches(self, *, limit: int = 50):
            self.calls["list_batches"] += 1
            return super().list_batches(limit=limit)

        def list_batch_jobs_for_batches(self, batch_ids):
            self.calls["list_batch_jobs_for_batches"] += 1
            return super().list_batch_jobs_for_batches(batch_ids)

        def list_events_for_batches(self, batch_ids):
            self.calls["list_events_for_batches"] += 1
            return super().list_events_for_batches(batch_ids)

    repository = TrackingRepository()
    for index in range(1, 8):
        batch_id = UUID(int=1000 + index)
        status = (
            BatchJobStatus.QUEUED
            if index <= 2
            else BatchJobStatus.COMPLETE
        )
        repository.create_batch(
            WebBatch(
                id=batch_id,
                idempotency_key=f"batch:snapshot:{index}",
                kind="GENERATE_ONE",
                status=(
                    BatchStatus.RUNNING
                    if index <= 2
                    else BatchStatus.COMPLETE
                ),
                created_at=f"2026-09-01T12:{index:02d}:00Z",
            ),
            (
                WebBatchJob(
                    id=UUID(int=2000 + index),
                    batch_id=batch_id,
                    client_id=f"client-{index}",
                    position=1,
                    status=status,
                    attempt_number=1,
                    created_at=f"2026-09-01T12:{index:02d}:00Z",
                ),
            ),
        )
    repository.calls = {key: 0 for key in repository.calls}
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-snapshot",
        start_worker=False,
    )
    repository.calls = {key: 0 for key in repository.calls}
    try:
        snapshot = queue.dashboard_snapshot()
    finally:
        queue.close()

    assert repository.calls == {
        "list_batches": 1,
        "list_batch_jobs_for_batches": 1,
        "list_events_for_batches": 1,
    }
    assert snapshot.active_job_count == 2
    assert snapshot.jobs[0]["created_at"] >= snapshot.jobs[-1]["created_at"]
    assert len(snapshot.batches) == 7


def test_dashboard_application_groups_generate_all_in_one_durable_batch(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()

    def runner(command, cwd, progress_callback=None):
        subcommand = command[3]
        if subcommand == "collect-client":
            checkpoint = Path(command[command.index("--checkpoint") + 1])
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "status": (
                        "COLLECTION_READY"
                        if subcommand == "collect-client"
                        else "COMPLETE"
                    ),
                    "run_id": "run-fixture",
                }
            ),
            stderr="",
        )

    app = DashboardApplication(
        project_root=tmp_path,
        config_path=tmp_path / "orchestration" / "clients.json",
        runner=runner,
        batch_repository=repository,
    )
    for client_id in ("client-1", "client-2"):
        app.config.add_client(
            {
                "client_id": client_id,
                "display_name": client_id,
                "access_key": "fixture-access",
                "secret_key": "fixture-secret",
            }
        )

    try:
        created = app.enqueue_jobs(
            ["client-1", "client-2"],
            {
                "mode": "manual",
                "days": 30,
                "run_scope": "all",
                "selection_filter_snapshot": {
                    "analyst_id": None,
                    "query": "",
                    "unassigned": False,
                },
            },
        )
        assert len({row["batch_id"] for row in created}) == 1
        assert app.jobs.wait_until_idle(timeout=2)
    finally:
        app.jobs.close()

    assert len(repository.list_batches()) == 1
    batch = repository.list_batches()[0]
    assert batch.status is BatchStatus.COMPLETE
    assert batch.options["selected_client_ids"] == ["client-1", "client-2"]
    assert batch.options["excluded_client_ids"] == []
    assert batch.options["analyst_snapshot_by_client"] == {
        "client-1": {
            "analyst_id": None,
            "display_name": None,
            "active": False,
        },
        "client-2": {
            "analyst_id": None,
            "display_name": None,
            "active": False,
        },
    }
    assert batch.options["selection_filter_snapshot"] == {
        "analyst_id": None,
        "query": "",
        "unassigned": False,
    }


def test_production_server_requires_durable_batches(tmp_path) -> None:
    captured: dict[str, object] = {}

    def build_application(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            config=SimpleNamespace(config_path=tmp_path / "clients.json")
        )

    class FakeServer:
        def __init__(self, address, app) -> None:
            self.address = address
            self.app = app

        def serve_forever(self, poll_interval) -> None:
            return None

        def server_close(self) -> None:
            return None

    with (
        patch.object(server_module, "DashboardApplication", side_effect=build_application),
        patch.object(server_module, "DashboardHTTPServer", FakeServer),
    ):
        server_module.serve_dashboard(
            project_root=tmp_path,
            config_path=tmp_path / "orchestration" / "clients.json",
        )

    assert captured["require_durable_batches"] is True


def test_staged_remote_success_advances_to_build_before_terminal(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    checkpoint = (tmp_path / "collection-checkpoint.json").resolve()
    checkpoint.write_text("{}", encoding="utf-8")
    build_saw: list[tuple[BatchJobPhase, str | None]] = []

    def remote_runner(job: WebBatchJob) -> BatchJobResult:
        return BatchJobResult(
            status=BatchJobStatus.COMPLETE,
            payload={"_collection_checkpoint_path": str(checkpoint)},
        )

    def build_runner(job: WebBatchJob) -> BatchJobResult:
        build_saw.append((job.phase, job.collection_checkpoint_path))
        return BatchJobResult(status=BatchJobStatus.COMPLETE)

    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-staged",
        poll_interval=0.01,
        remote_runner=remote_runner,
        build_runner=build_runner,
        remote_workers=1,
    )
    try:
        created = queue.enqueue_requests(
            (("client-1", {"mode": "manual", "days": 30}),),
            batch_options={"execution_model": "STAGED_V1"},
        )
        assert queue.wait_until_idle(timeout=3)
        snapshot = queue.batch_snapshot(created[0]["batch_id"])
    finally:
        queue.close()

    assert build_saw == [(BatchJobPhase.BUILD_RUNNING, str(checkpoint))]
    assert snapshot["jobs"][0]["status"] == "COMPLETE"
    assert [event["event_type"] for event in snapshot["events"]].count(
        "COLLECTION_READY"
    ) == 1
    assert "collection_checkpoint_path" not in snapshot["jobs"][0]
    assert str(checkpoint) not in json.dumps(snapshot)


def test_staged_retry_with_checkpoint_resumes_at_build_phase(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    checkpoint = (tmp_path / "collection-checkpoint.json").resolve()
    checkpoint.write_text(
        '{"component_metadata":{"VM_CORE":{"status":"COMPLETE"},'
        '"WAS":{"status":"SKIPPED"},"CLOUD":{"status":"SKIPPED"}}}',
        encoding="utf-8",
    )
    source = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_FAILURES),
        options={"execution_model": "STAGED_V1"},
    )
    failed = replace(
        _job(1, status=BatchJobStatus.FAILED, phase=BatchJobPhase.TERMINAL),
        collection_checkpoint_path=str(checkpoint),
        logical_job_id="logical-client-1",
    )
    repository.create_batch(source, (failed,))
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-staged-retry",
        start_worker=False,
    )
    try:
        derived = queue.derive_batch(
            server_module.DerivedBatchRequest(
                source_batch_id=source.id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-staged-build",
            )
        )
        stored = repository.list_batch_jobs(UUID(derived["batch"]["id"]))[0]
    finally:
        queue.close()

    assert stored.phase is BatchJobPhase.READY_FOR_BUILD
    assert stored.collection_checkpoint_path == str(checkpoint)
    assert stored.logical_job_id == "logical-client-1"


def test_staged_retry_with_missing_checkpoint_returns_to_remote_phase(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    missing = (tmp_path / "missing-checkpoint.json").resolve()
    source = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_FAILURES),
        options={"execution_model": "STAGED_V1"},
    )
    failed = replace(
        _job(1, status=BatchJobStatus.FAILED, phase=BatchJobPhase.TERMINAL),
        collection_checkpoint_path=str(missing),
    )
    repository.create_batch(source, (failed,))
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-staged-missing",
        start_worker=False,
    )
    try:
        derived = queue.derive_batch(
            server_module.DerivedBatchRequest(
                source_batch_id=source.id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-staged-missing",
            )
        )
        stored = repository.list_batch_jobs(UUID(derived["batch"]["id"]))[0]
    finally:
        queue.close()

    assert stored.phase is BatchJobPhase.REMOTE_QUEUED
    assert stored.collection_checkpoint_path is None


def test_batch_detail_summarizes_was_retry_events(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    batch = _batch(status=BatchStatus.COMPLETE_WITH_FAILURES)
    job = replace(_job(1, status=BatchJobStatus.FAILED), phase=BatchJobPhase.TERMINAL)
    repository.create_batch(batch, (job,))
    for status in ("STARTED", "STARTED", "TIMED_OUT"):
        repository.append_event(WebBatchEvent(
            batch_id=batch.id,
            job_id=job.id,
            event_type="JOB_PROGRESS",
            payload={"event": "TENABLE_EXPORT_PROGRESS", "source": "tenable_was_findings", "status": status},
        ))
    legacy = JobQueue(tmp_path, tmp_path / "orchestration" / "clients.json", lambda *args, **kwargs: None, start_worker=False)
    queue = DurableDashboardJobQueue(repository=repository, executor=legacy, worker_id="worker-detail", start_worker=False)
    try:
        detail = queue.batch_snapshot(batch.id)
    finally:
        queue.close()
    assert detail["jobs"][0]["was_attempts"] == 2
    assert detail["jobs"][0]["was_retry_performed"] is True
    assert detail["jobs"][0]["was_retry_outcome"] == "TIMED_OUT"


def test_executor_preserves_structured_failure_classification(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    legacy = JobQueue(tmp_path, tmp_path / "orchestration" / "clients.json", lambda *args, **kwargs: None, start_worker=False)
    queue = DurableDashboardJobQueue(repository=repository, executor=legacy, worker_id="worker-failure", start_worker=False)
    job = _job(1)
    def fail(job_id: str) -> None:
        with legacy._lock:
            legacy._jobs[job_id].update(
                status="FAILED",
                exit_code=2,
                error="Tempo maximo excedido na fila do export VM.",
                error_code="TENABLE_TEMPORARY",
                retryable=True,
            )
    legacy._run = fail
    try:
        result = queue._run_executor_job(job)
    finally:
        queue.close()
    assert result.error_code == "TENABLE_TEMPORARY"
    assert result.payload["retryable"] is True


def test_preserved_uuid_budget_does_not_shorten_the_whole_collection(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-vm-budget",
        start_worker=False,
    )
    observed: dict[str, object] = {}
    job = replace(
        _job(1),
        payload={"remote_processing_timeout_seconds": 36_000},
        vm_export_uuid="00000000-0000-0000-0000-000000000701",
        remote_export_started_at="2000-01-01T00:00:00Z",
    )

    def complete(job_id: str) -> None:
        with legacy._lock:
            observed.update(legacy._jobs[job_id])
            legacy._jobs[job_id].update(status="COMPLETE", exit_code=0)

    legacy._run = complete
    try:
        queue._run_executor_job(job)
    finally:
        queue.close()

    assert observed["remote_processing_timeout_seconds"] == 36_000
    assert observed["vm_resume_budget_seconds"] == 1


def test_legacy_remaining_budget_does_not_become_the_global_timeout(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-legacy-vm-budget",
        start_worker=False,
    )
    observed: dict[str, object] = {}
    job = replace(
        _job(1),
        payload={
            "remote_processing_timeout_seconds": 1,
            "vm_resume_budget_seconds": 1,
        },
        vm_export_uuid="00000000-0000-0000-0000-000000000704",
        remote_export_started_at="2000-01-01T00:00:00Z",
    )

    def complete(job_id: str) -> None:
        with legacy._lock:
            observed.update(legacy._jobs[job_id])
            legacy._jobs[job_id].update(status="COMPLETE", exit_code=0)

    legacy._run = complete
    try:
        queue._run_executor_job(job)
    finally:
        queue.close()

    assert observed["remote_processing_timeout_seconds"] == 36_000
    assert observed["vm_resume_budget_seconds"] == 1


def test_recovery_replacement_promotes_new_uuid_and_resets_its_budget(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    old_started_at = "2026-09-02T00:00:00Z"
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING),
        phase=BatchJobPhase.REMOTE_RUNNING,
        vm_export_uuid="00000000-0000-0000-0000-000000000702",
        remote_export_started_at=old_started_at,
        remote_status_at=old_started_at,
        remote_progress_at=old_started_at,
    )
    repository.create_batch(batch, (job,))
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-vm-replacement",
        start_worker=False,
    )
    with queue._active_lock:
        queue._active_jobs[job.id.hex] = job
    replacement_uuid = "00000000-0000-0000-0000-000000000703"
    try:
        queue._persist_progress(job.id.hex, {
            "event": "TENABLE_EXPORT_RECOVERY_UNAVAILABLE",
            "source": "tenable_vm_vulnerabilities",
            "previous_export_uuid": job.vm_export_uuid,
            "replacement_export_uuid": replacement_uuid,
            "replacement_origin": "created",
            "replacement_started": True,
            "reason": "UUID anterior expirou.",
        })
        replaced = repository.get_job(job.id)
        assert replaced is not None
        queue._persist_progress(job.id.hex, {
            "event": "TENABLE_EXPORT_PROGRESS",
            "source": "tenable_vm_vulnerabilities",
            "export_uuid": replacement_uuid,
            "origin": "created",
            "status": "STARTED",
            "status_query_ok": False,
            "completed_chunks": 0,
            "total_chunks": 0,
            "persisted_chunks": [],
            "partial_manifest": str((tmp_path / "replacement.partial.json").resolve()),
        })
        started = repository.get_job(job.id)
    finally:
        queue.close()

    assert replaced.vm_export_uuid == replacement_uuid
    assert replaced.remote_export_started_at != old_started_at
    assert replaced.remote_status_at is None
    assert replaced.remote_progress_at is None
    assert started is not None
    assert started.vm_export_uuid == replacement_uuid
    assert started.vm_resume_manifest_path.endswith("replacement.partial.json")


def test_vm_progress_is_persisted_on_job_before_retry_derivation(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(_job(1, status=BatchJobStatus.RUNNING), phase=BatchJobPhase.REMOTE_RUNNING)
    repository.create_batch(batch, (job,))
    legacy = JobQueue(tmp_path, tmp_path / "orchestration" / "clients.json", lambda *args, **kwargs: None, start_worker=False)
    queue = DurableDashboardJobQueue(repository=repository, executor=legacy, worker_id="worker-vm-state", start_worker=False)
    with queue._active_lock:
        queue._active_jobs[job.id.hex] = job
    try:
        queue._persist_progress(job.id.hex, {
            "event": "TENABLE_EXPORT_PROGRESS",
            "source": "tenable_vm_vulnerabilities",
            "export_uuid": "00000000-0000-0000-0000-000000000777",
            "origin": "created",
            "status": "STARTED",
            "status_query_ok": False,
            "completed_chunks": 0,
            "total_chunks": 0,
            "persisted_chunks": [],
            "partial_manifest": str((tmp_path / "manifest.partial.json").resolve()),
        })
        started = repository.list_batch_jobs(batch.id)[0]
        queue._persist_progress(job.id.hex, {
            "event": "TENABLE_EXPORT_PROGRESS",
            "source": "tenable_vm_vulnerabilities",
            "export_uuid": "00000000-0000-0000-0000-000000000777",
            "origin": "created",
            "status": "PROCESSING",
            "status_query_ok": True,
            "completed_chunks": 1,
            "total_chunks": 2,
            "persisted_chunks": [1],
            "partial_manifest": str((tmp_path / "manifest.partial.json").resolve()),
        })
        stored = repository.list_batch_jobs(batch.id)[0]
        confirmed_at = stored.remote_status_at
        queue._persist_progress(job.id.hex, {
            "event": "TENABLE_EXPORT_PROGRESS",
            "source": "tenable_vm_vulnerabilities",
            "export_uuid": "00000000-0000-0000-0000-000000000777",
            "status": "PROCESSING",
            "status_query_ok": False,
            "status_query_error": "HTTP 503",
        })
        after_transient_error = repository.list_batch_jobs(batch.id)[0]
    finally:
        queue.close()
    assert started.vm_export_uuid == "00000000-0000-0000-0000-000000000777"
    assert started.remote_export_started_at is not None
    assert started.remote_status_at is None
    assert stored.vm_export_uuid == "00000000-0000-0000-0000-000000000777"
    assert stored.vm_resume_manifest_path.endswith("manifest.partial.json")
    assert stored.remote_export_started_at is not None
    assert stored.remote_status_at is not None
    assert stored.remote_progress_at is not None
    assert after_transient_error.remote_status_at == confirmed_at


def test_dashboard_bootstraps_automatic_remote_capacity_and_serial_build(tmp_path) -> None:
    config_path = tmp_path / "orchestration" / "clients.json"
    store = DashboardConfigStore(project_root=tmp_path, config_path=config_path)
    for index in range(20):
        store.add_client(
            {
                "client_id": f"client-{index:02d}",
                "display_name": f"Client {index:02d}",
                "access_key": "fixture-access",
                "secret_key": "fixture-secret",
            }
        )
    app = DashboardApplication(
        project_root=tmp_path,
        config_path=config_path,
        runner=lambda *args, **kwargs: None,
        batch_repository=InMemoryWebBatchRepository(),
    )
    try:
        capacities = app.jobs.capacity_snapshot()
    finally:
        app.jobs.close()

    by_phase = {tuple(item["phases"]): item["workers"] for item in capacities}
    assert by_phase[(BatchJobPhase.LEGACY.value,)] == 1
    assert by_phase[(BatchJobPhase.REMOTE_QUEUED.value,)] == 20
    assert by_phase[(BatchJobPhase.READY_FOR_BUILD.value,)] == 1
    assert {
        item["idle_poll_interval_seconds"] for item in capacities
    } == {5.0}


def test_component_retry_is_queued_for_exact_published_run(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-component-retry",
        start_worker=False,
    )
    try:
        queued = queue.enqueue_component_retry(
            run_id="published-run-a",
            client_id="client-a",
            selected_components=("WAS", "CLOUD"),
        )
        stored = repository.list_batch_jobs(repository.list_batches()[0].id)[0]
    finally:
        queue.close()

    assert stored.phase is BatchJobPhase.LEGACY
    assert stored.payload["operation"] == "component_retry"
    assert stored.payload["source_run_id"] == "published-run-a"
    assert stored.payload["selected_components"] == ["WAS", "CLOUD"]


def test_staged_workers_construct_collect_then_build_commands(tmp_path) -> None:
    config_path = tmp_path / "orchestration" / "clients.json"
    store = DashboardConfigStore(project_root=tmp_path, config_path=config_path)
    store.add_client(
        {
            "client_id": "client-01",
            "display_name": "Client 01",
            "access_key": "fixture-access",
            "secret_key": "fixture-secret",
        }
    )
    raw_config = store.raw()
    custom_output_root = (tmp_path / "custom-data").resolve()
    raw_config["defaults"]["output_root"] = str(custom_output_root)
    config_path.write_text(
        json.dumps(raw_config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def runner(command, cwd, progress_callback=None):
        commands.append(list(command))
        subcommand = command[3]
        if subcommand == "collect-client":
            checkpoint = Path(command[command.index("--checkpoint") + 1])
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text("{}", encoding="utf-8")
            payload = {"status": "COLLECTION_READY", "run_id": "run-staged"}
        else:
            payload = {"status": "COMPLETE", "run_id": "run-staged"}
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload),
            stderr="",
        )

    app = DashboardApplication(
        project_root=tmp_path,
        config_path=config_path,
        runner=runner,
        batch_repository=InMemoryWebBatchRepository(),
    )
    try:
        created = app.enqueue_jobs(
            ["client-01"],
            {"mode": "manual", "days": 30, "run_scope": "single"},
        )
        assert app.jobs.wait_until_idle(timeout=3)
        snapshot = app.jobs.batch_snapshot(created[0]["batch_id"])
    finally:
        app.jobs.close()

    assert [command[3] for command in commands] == ["collect-client", "build-client"]
    collect_command, build_command = commands
    assert "--confirm-live-api" in collect_command
    assert "--confirm-live-api" not in build_command
    assert collect_command[collect_command.index("--remote-processing-timeout-seconds") + 1] == "36000"
    assert collect_command[collect_command.index("--remote-progress-warning-seconds") + 1] == "900"
    assert "--job-control-file" in collect_command
    assert "--job-control-file" in build_command
    checkpoint = Path(collect_command[collect_command.index("--checkpoint") + 1])
    assert checkpoint.is_relative_to(custom_output_root)
    assert snapshot["jobs"][0]["status"] == "COMPLETE"
