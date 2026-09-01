from __future__ import annotations

import json
import subprocess
from pathlib import Path
from uuid import UUID

from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.web_batches import (
    BatchAction,
    BatchJobStatus,
    BatchStatus,
)
from tenable_reports.webapp.durable_dashboard_queue import (
    DurableDashboardJobQueue,
)
from tenable_reports.webapp.server import JobQueue


def _queue(tmp_path: Path, runner):
    repository = InMemoryWebBatchRepository()
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        runner,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-control",
        start_worker=False,
    )
    return repository, queue


def test_durable_job_owns_control_file_and_forwards_it_to_orchestrate(
    tmp_path: Path,
) -> None:
    commands: list[list[str]] = []

    def runner(command, cwd, progress_callback=None):
        commands.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"status": "COMPLETE", "run_id": "run-ok"}),
            stderr="",
        )

    repository, queue = _queue(tmp_path, runner)
    try:
        created = queue.enqueue(
            ["client-a"],
            {"mode": "manual", "days": 30},
        )
        batch_id = UUID(str(created[0]["batch_id"]))
        job = repository.list_batch_jobs(batch_id)[0]
        assert job.control_file is not None
        assert Path(job.control_file).name == f"{job.id.hex}.json"
        assert "web-batches" in Path(job.control_file).parts

        claimed = repository.claim_next_job(worker_id="worker-control")
        assert claimed is not None
        result = queue._run_job(claimed)
    finally:
        queue.close()

    assert result.status is BatchJobStatus.COMPLETE
    command = commands[0]
    option = command.index("--job-control-file")
    assert command[option + 1] == job.control_file


def test_stop_writes_cooperative_control_without_remote_export_cancel(
    tmp_path: Path,
) -> None:
    def runner(command, cwd, progress_callback=None):
        raise AssertionError("A coleta nao deve ser iniciada neste teste.")

    repository, queue = _queue(tmp_path, runner)
    try:
        created = queue.enqueue(
            ["client-a", "client-b"],
            {"mode": "manual", "days": 30},
        )
        batch_id = UUID(str(created[0]["batch_id"]))
        active = repository.claim_next_job(worker_id="worker-control")
        assert active is not None
        queue._active_job = active

        batch = queue.request_action(batch_id, BatchAction.STOP)

        assert batch.status is BatchStatus.STOP_REQUESTED
        control = json.loads(Path(active.control_file).read_text(encoding="utf-8"))
        assert control["stop_requested"] is True
        assert "local" in control["reason"].lower()
        assert tuple(
            job.status for job in repository.list_batch_jobs(batch.id)
        ) == (
            BatchJobStatus.INTERRUPT_REQUESTED,
            BatchJobStatus.CANCELLED_BY_USER,
        )
    finally:
        queue.close()


def test_exit_130_is_persisted_as_interrupted_instead_of_failed(
    tmp_path: Path,
) -> None:
    def runner(command, cwd, progress_callback=None):
        return subprocess.CompletedProcess(
            command,
            130,
            stdout=json.dumps(
                {
                    "status": "INTERRUPTED",
                    "error_code": "INTERRUPTED_BY_USER",
                }
            ),
            stderr="",
        )

    repository, queue = _queue(tmp_path, runner)
    try:
        queue.enqueue(["client-a"], {"mode": "manual", "days": 30})
        claimed = repository.claim_next_job(worker_id="worker-control")
        assert claimed is not None

        result = queue._run_job(claimed)
    finally:
        queue.close()

    assert result.status is BatchJobStatus.INTERRUPTED
    assert result.exit_code == 130
    assert result.error_code == "INTERRUPTED_BY_USER"

