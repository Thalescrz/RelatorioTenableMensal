from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID

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
from tenable_reports.webapp.server import JobQueue, _default_runner


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
        sibling = repository.claim_next_job(worker_id="worker-control-sibling")
        assert sibling is not None
        queue._active_jobs = {
            active.id.hex: active,
            sibling.id.hex: sibling,
        }

        batch = queue.request_action(batch_id, BatchAction.STOP)

        assert batch.status is BatchStatus.STOP_REQUESTED
        control = json.loads(Path(active.control_file).read_text(encoding="utf-8"))
        assert control["stop_requested"] is True
        assert "local" in control["reason"].lower()
        sibling_control = json.loads(
            Path(sibling.control_file).read_text(encoding="utf-8")
        )
        assert sibling_control["stop_requested"] is True
        assert tuple(
            job.status for job in repository.list_batch_jobs(batch.id)
        ) == (
            BatchJobStatus.INTERRUPT_REQUESTED,
            BatchJobStatus.INTERRUPT_REQUESTED,
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

def test_default_runner_terminates_only_local_process_after_grace(
    tmp_path: Path,
) -> None:
    started: list[int] = []
    fallback: list[int] = []
    before = time.monotonic()

    completed = _default_runner(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        tmp_path,
        cancellation_probe=lambda: True,
        process_started_callback=started.append,
        fallback_callback=fallback.append,
        stop_grace_seconds=0.05,
    )

    assert completed.returncode != 0
    assert len(started) == 1
    assert fallback == started
    assert time.monotonic() - before < 10


def test_durable_queue_persists_process_and_fallback_event(
    tmp_path: Path,
) -> None:
    def runner(
        command,
        cwd,
        progress_callback=None,
        cancellation_probe=None,
        process_started_callback=None,
        fallback_callback=None,
    ):
        assert callable(cancellation_probe)
        assert cancellation_probe() is False
        process_started_callback(4242)
        fallback_callback(4242)
        return subprocess.CompletedProcess(
            command,
            130,
            stdout=json.dumps({"status": "INTERRUPTED"}),
            stderr="",
        )

    repository, queue = _queue(tmp_path, runner)
    try:
        created = queue.enqueue(
            ["client-a"],
            {"mode": "manual", "days": 30},
        )
        batch_id = UUID(str(created[0]["batch_id"]))
        claimed = repository.claim_next_job(worker_id="worker-control")
        assert claimed is not None

        result = queue._run_job(claimed)
        stored = repository.list_batch_jobs(batch_id)[0]
        events = repository.list_events(batch_id)
    finally:
        queue.close()

    assert result.status is BatchJobStatus.INTERRUPTED
    assert stored.process_id == 4242
    assert any(
        event.event_type == "JOB_LOCAL_FALLBACK_TERMINATION"
        and event.payload == {"process_id": 4242}
        for event in events
    )


def test_progress_and_process_sinks_route_to_each_active_job(
    tmp_path: Path,
) -> None:
    def runner(command, cwd, progress_callback=None):
        raise AssertionError("Nenhuma execução local deve ocorrer neste teste.")

    repository, queue = _queue(tmp_path, runner)
    try:
        created = queue.enqueue(
            ["client-a", "client-b"],
            {"mode": "manual", "days": 30},
        )
        batch_id = UUID(str(created[0]["batch_id"]))
        first = repository.claim_next_job(worker_id="worker-a")
        second = repository.claim_next_job(worker_id="worker-b")
        assert first is not None
        assert second is not None
        queue._active_jobs = {
            first.id.hex: first,
            second.id.hex: second,
        }

        queue._persist_progress(first.id.hex, {"stage": "REMOTE"})
        queue._persist_process(second.id.hex, 4242)
        queue._persist_fallback(first.id.hex, 5151)

        stored = repository.list_batch_jobs(batch_id)
        events = repository.list_events(batch_id)
    finally:
        queue.close()

    assert stored[0].process_id is None
    assert stored[1].process_id == 4242
    assert any(
        event.job_id == first.id
        and event.event_type == "JOB_PROGRESS"
        and event.payload == {"stage": "REMOTE"}
        for event in events
    )
    assert any(
        event.job_id == first.id
        and event.event_type == "JOB_LOCAL_FALLBACK_TERMINATION"
        and event.payload == {"process_id": 5151}
        for event in events
    )


def test_stop_signals_all_staged_jobs_and_preserves_recovery_artifacts(
    tmp_path: Path,
) -> None:
    def runner(command, cwd, progress_callback=None):
        raise AssertionError("A parada local nao deve executar nem cancelar coleta.")

    repository, queue = _queue(tmp_path, runner)
    batch_id = UUID(int=900)
    checkpoint = str((tmp_path / "checkpoint.json").resolve())
    manifest = str((tmp_path / "manifest.partial.json").resolve())
    payloads = (
        {
            "vm_export_uuid": "00000000-0000-0000-0000-000000000901",
            "downloaded_chunks": [0, 1],
            "manifest_path": manifest,
        },
        {
            "vm_export_uuid": "00000000-0000-0000-0000-000000000902",
            "downloaded_chunks": [0],
            "manifest_path": manifest,
        },
    )
    jobs = tuple(
        WebBatchJob(
            id=UUID(int=910 + index),
            batch_id=batch_id,
            client_id=f"client-{index}",
            position=index,
            status=BatchJobStatus.QUEUED,
            attempt_number=1,
            phase=BatchJobPhase.READY_FOR_BUILD,
            payload=payloads[index - 1],
            control_file=str((tmp_path / f"control-{index}.json").resolve()),
            collection_checkpoint_path=checkpoint,
        )
        for index in (1, 2)
    )
    repository.create_batch(
        WebBatch(
            id=batch_id,
            idempotency_key="batch:staged-stop",
            kind="GENERATE_ALL",
            status=BatchStatus.QUEUED,
            options={"execution_model": "STAGED_V1"},
        ),
        jobs,
    )
    first = repository.claim_next_job(
        worker_id="tenable-build-1",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
    )
    second = repository.claim_next_job(
        worker_id="tenable-build-2",
        phases=(BatchJobPhase.READY_FOR_BUILD,),
    )
    assert first is not None
    assert second is not None

    try:
        queue.request_action(batch_id, BatchAction.STOP)
        stopped = repository.list_batch_jobs(batch_id)
    finally:
        queue.close()

    assert all(job.status is BatchJobStatus.INTERRUPT_REQUESTED for job in stopped)
    assert tuple(dict(job.payload) for job in stopped) == payloads
    assert all(job.collection_checkpoint_path == checkpoint for job in stopped)
    assert all(
        json.loads(Path(job.control_file).read_text(encoding="utf-8"))[
            "stop_requested"
        ]
        is True
        for job in stopped
    )


def test_stop_single_running_job_signals_only_that_client(tmp_path: Path) -> None:
    def runner(command, cwd, progress_callback=None):
        raise AssertionError("A parada individual nao deve executar coleta.")

    repository, queue = _queue(tmp_path, runner)
    try:
        created = queue.enqueue(
            ["client-a", "client-b"],
            {"mode": "manual", "days": 30},
        )
        batch_id = UUID(str(created[0]["batch_id"]))
        first = repository.claim_next_job(worker_id="worker-a")
        second = repository.claim_next_job(worker_id="worker-b")
        assert first is not None
        assert second is not None

        stopped = queue.request_job_stop(
            first.id,
            actor="analista-local",
            reason="substituir a geracao deste cliente",
            idempotency_key="job-stop:running",
        )
        stored = repository.list_batch_jobs(batch_id)
    finally:
        queue.close()

    assert stopped.status is BatchJobStatus.INTERRUPT_REQUESTED
    assert stored[0].status is BatchJobStatus.INTERRUPT_REQUESTED
    assert stored[1].status is BatchJobStatus.RUNNING
    assert repository.get_batch(batch_id).status is BatchStatus.RUNNING
    assert json.loads(Path(first.control_file).read_text(encoding="utf-8"))[
        "stop_requested"
    ] is True
    assert not Path(second.control_file).exists()
