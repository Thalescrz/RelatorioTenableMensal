from __future__ import annotations

import json
import subprocess

import threading
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import tenable_reports.webapp.server as server_module

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
from tenable_reports.webapp.durable_dashboard_queue import (
    DurableDashboardJobQueue,
)
from tenable_reports.webapp.job_queue import DurableJobQueue
from tenable_reports.webapp.server import DashboardApplication, JobQueue


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


def test_dashboard_application_groups_generate_all_in_one_durable_batch(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()

    def runner(command, cwd, progress_callback=None):
        client_id = command[command.index("--client") + 1]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {"status": "COMPLETE", "run_id": f"run-{client_id}"}
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
