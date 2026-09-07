from __future__ import annotations

import hashlib
import json
import subprocess

import threading
import time
import pytest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

import tenable_reports.webapp.server as server_module

from tenable_reports.application.web_batches import BatchJobResult, DerivedBatchRequest
from tenable_reports.application.component_collection import (
    ComponentCollectionCheckpoint,
    component_checkpoint_path,
    persist_component_checkpoint,
)
from tenable_reports.application.staged_execution import (
    CheckpointArtifact,
    CollectionCheckpoint,
    RemoteCollectionDependencies,
    RemoteCollectionRequest,
    collect_client_remote,
    load_collection_checkpoint,
)
from tenable_reports.application.web_batches_memory import (
    InMemoryRemoteComponentRepository,
    InMemoryWebBatchRepository,
)
from tenable_reports.domain.remote_components import RemoteComponentState
from tenable_reports.domain.remote_components import RemoteIdentifierKind
from tenable_reports.domain.report_components import ReportComponent
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
    _component_is_retryable,
)
from tenable_reports.webapp.job_queue import (
    RemoteComponentWorkerPool,
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


def test_remote_component_pool_claims_each_component_once_with_bounded_parallelism() -> None:
    repository = InMemoryRemoteComponentRepository()
    job_ids = (UUID(int=301), UUID(int=302))
    deadline = datetime.now(UTC) + timedelta(hours=10)
    for job_id in job_ids:
        repository.create_for_job(
            batch_job_id=job_id,
            components=tuple(ReportComponent),
            window_number=1,
            deadline_at=deadline,
            origin="SCHEDULED",
        )
    observed: list[UUID] = []
    active = 0
    peak = 0
    lock = threading.Lock()

    def runner(component) -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            observed.append(component.id)
        time.sleep(0.02)
        repository.transition(
            component.id,
            expected_state=component.state,
            requested_state=RemoteComponentState.COMPLETE,
            worker_id=None,
            lease_expires_at=None,
            ended_at=datetime.now(UTC),
        )
        with lock:
            active -= 1

    pool = RemoteComponentWorkerPool(
        repository=repository,
        runner=runner,
        worker_prefix="remote-component-test",
        workers=3,
        poll_interval=0.01,
    )
    try:
        assert pool.wait_until_idle(timeout=3)
    finally:
        pool.close()

    assert len(observed) == 6
    assert len(set(observed)) == 6
    assert 1 < peak <= 3


def test_job_queue_builds_collect_component_command_and_keeps_terminal_payload(
    tmp_path,
) -> None:
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
    commands: list[list[str]] = []

    def runner(command, cwd, progress_callback=None):
        commands.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "event": "TENABLE_COMPONENT_CHECKPOINT",
                    "status": "COMPLETE",
                    "component": "VM_CORE",
                    "checkpoint": str(tmp_path / "component.json"),
                }
            ),
            stderr="",
        )

    jobs = JobQueue(tmp_path, config_path, runner, start_worker=False)
    job_id = "component-executor-01"
    jobs._jobs[job_id] = {
        "job_id": job_id,
        "client_id": "client-01",
        "operation": "staged_component",
        "component": "VM_CORE",
        "component_checkpoint": str(
            tmp_path / "checkpoints" / "run-01" / "vm_core" / "checkpoint.json"
        ),
        "window_number": 1,
        "deadline_at": "2026-09-05T08:00:00Z",
        "mode": "automatic",
        "days": None,
        "start_at": None,
        "end_at": None,
        "run_id": "run-01",
        "logical_job_id": "logical-01",
        "attempt_number": 1,
        "status": "QUEUED",
        "warnings": [],
    }

    jobs._run(job_id)

    command = commands[0]
    assert command[3] == "collect-component"
    assert command[command.index("--component") + 1] == "VM_CORE"
    assert command[command.index("--window-number") + 1] == "1"
    assert command[command.index("--deadline-at") + 1] == "2026-09-05T08:00:00Z"
    assert jobs._jobs[job_id]["_component_result"]["status"] == "COMPLETE"


def test_staged_vm_asset_retry_forwards_uuid_to_asset_export_argument(tmp_path) -> None:
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
    commands: list[list[str]] = []

    def runner(command, cwd, progress_callback=None):
        del cwd, progress_callback
        commands.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "event": "TENABLE_COMPONENT_CHECKPOINT",
                    "status": "COMPLETE",
                    "component": "VM_CORE",
                    "checkpoint": str(tmp_path / "component.json"),
                }
            ),
            stderr="",
        )

    jobs = JobQueue(tmp_path, config_path, runner, start_worker=False)
    job_id = "component-asset-retry"
    asset_uuid = "00000000-0000-0000-0000-000000000111"
    jobs._jobs[job_id] = {
        "job_id": job_id,
        "client_id": "client-01",
        "operation": "staged_component",
        "component": "VM_CORE",
        "component_checkpoint": str(tmp_path / "component.json"),
        "window_number": 2,
        "deadline_at": "2026-09-05T18:00:00Z",
        "mode": "automatic",
        "days": None,
        "start_at": None,
        "end_at": None,
        "run_id": "run-01",
        "logical_job_id": "logical-01",
        "attempt_number": 2,
        "remote_identifier": asset_uuid,
        "identifier_kind": "UUID",
        "identifier_origin": "asset_export:provided",
        "status": "QUEUED",
        "warnings": [],
    }

    jobs._run(job_id)

    command = commands[0]
    assert command[command.index("--asset-export-uuid") + 1] == asset_uuid
    assert "--remote-identifier" not in command


def test_asset_progress_is_preserved_as_vm_component_recovery_identifier(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={"mode": "automatic", "run_id": "run-asset-progress"},
    )
    repository.create_batch(_batch(status=BatchStatus.RUNNING), (job,))
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-asset-progress",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    component = component_repository.create_for_job(
        batch_job_id=job.id,
        components=(ReportComponent.VM_CORE,),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="SCHEDULED",
    )[0]
    component = component_repository.claim_next(worker_id="asset-progress")

    try:
        queue._persist_component_progress(
            component,
            {
                "event": "TENABLE_EXPORT_PROGRESS",
                "source": "tenable_vm_assets_v2",
                "status": "PROCESSING",
                "completed_chunks": 0,
                "total_chunks": 1,
                "export_uuid": "00000000-0000-0000-0000-000000000112",
                "origin": "created",
            },
        )
        stored = component_repository.get(component.id)
        queue._handle_failed_remote_component(
            job,
            stored,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="UNEXPECTED",
                error_message=(
                    "[WinError 206] filename too long: "
                    "raw\\client-1\\tenable_vm_assets_v2\\"
                    "00000000-0000-0000-0000-000000000112"
                ),
            ),
        )
        windows = component_repository.list_for_jobs((job.id,))[job.id]
        failed_window = windows[-2]
        next_window = windows[-1]
    finally:
        queue.close()

    assert stored.remote_identifier == "00000000-0000-0000-0000-000000000112"
    assert stored.identifier_origin == "asset_export:created"
    assert stored.state is RemoteComponentState.RUNNING_WINDOW_1
    assert failed_window.failure_code == "LOCAL_FILESYSTEM_TRANSIENT"
    assert next_window.window_number == 2
    assert next_window.remote_identifier == stored.remote_identifier
    assert next_window.identifier_origin == "asset_export:provided"


def test_retry_incomplete_recovers_asset_uuid_from_legacy_staging_failure(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    source_batch = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_FAILURES),
        options={"execution_model": "STAGED_V1"},
    )
    source_job = replace(
        _job(
            1,
            status=BatchJobStatus.FAILED,
            phase=BatchJobPhase.TERMINAL,
        ),
        run_id="run-staging-failure",
        error_code="UNEXPECTED",
        error_message="Componente VM do checkpoint não está completo.",
        payload={
            "mode": "manual",
            "run_id": "run-staging-failure",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    repository.create_batch(source_batch, (source_job,))
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-recover-legacy-staging",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    rows = component_repository.create_for_job(
        batch_job_id=source_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL",
    )
    by_component = {row.component: row for row in rows}
    asset_uuid = "00000000-0000-0000-0000-000000000113"
    component_repository.transition(
        by_component[ReportComponent.VM_CORE].id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.NON_RETRYABLE_FAILURE,
        failure_code="UNEXPECTED",
        failure_message=(
            "[WinError 3] Caminho não encontrado: "
            f"raw\\client-1\\tenable_vm_assets_v2\\{asset_uuid}"
        ),
        retryable=False,
        ended_at=datetime.now(UTC),
    )
    for component in (ReportComponent.WAS, ReportComponent.CLOUD):
        component_repository.transition(
            by_component[component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.NOT_APPLICABLE,
            ended_at=datetime.now(UTC),
        )

    try:
        dashboard = queue.dashboard_snapshot()
        family = queue.batch_family_snapshot(source_batch.id)
        summary = next(
            item for item in dashboard.batches if item["id"] == str(source_batch.id)
        )
        source_row = next(
            item for item in dashboard.jobs if item["batch_id"] == str(source_batch.id)
        )
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=source_batch.id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-legacy-staging-path",
                actor="test",
                reason="A falha local foi corrigida.",
            )
        )
        retry_job = repository.list_batch_jobs(UUID(detail["batch"]["id"]))[0]
        queue._initialize_remote_components(retry_job)
        retry_rows = component_repository.list_for_jobs((retry_job.id,))[retry_job.id]
        retry_vm = next(
            row for row in retry_rows if row.component is ReportComponent.VM_CORE
        )
    finally:
        queue.close()

    assert summary["retryable_count"] == 1
    assert source_row["retryable"] is True
    assert family["clients"][0]["effective_status"] == "WAITING_MANUAL_RETRY"
    assert retry_job.logical_job_id == source_job.id.hex
    assert retry_job.payload["asset_export_uuid"] == asset_uuid
    assert retry_vm.remote_identifier == asset_uuid
    assert retry_vm.identifier_origin == "asset_export:provided"


def test_required_vm_failure_does_not_release_build_for_disabled_optional_components(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={"mode": "automatic", "run_id": "run-vm-failed"},
    )
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-required-vm",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    repository.create_batch(batch, (job,))
    request = queue._component_request(job, component=ReportComponent.VM_CORE)
    created = component_repository.create_for_job(
        batch_job_id=job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="SCHEDULED",
    )
    by_component = {row.component: row for row in created}

    try:
        component_repository.transition(
            by_component[ReportComponent.VM_CORE].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.NON_RETRYABLE_FAILURE,
            failure_code="LOCAL_STAGING_PATH_ERROR",
            failure_message="Falha local ao preparar os artefatos temporários.",
            retryable=False,
            ended_at=datetime.now(UTC),
        )
        for component in (ReportComponent.WAS, ReportComponent.CLOUD):
            checkpoint = ComponentCollectionCheckpoint(
                schema_version=1,
                checkpoint_path=component_checkpoint_path(request, component),
                component=component,
                client_id=request.client_id,
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                logical_job_id=request.logical_job_id,
                execution_type=request.execution_type,
                mode=request.mode,
                origin=request.origin,
                attempt_number=request.attempt_number,
                period=dict(request.period),
                status=RemoteComponentState.NOT_APPLICABLE,
                artifacts=(),
                metadata={"reason_code": f"{component.value}_DISABLED"},
                query_fingerprint=("b" if component is ReportComponent.WAS else "c")
                * 64,
            )
            persist_component_checkpoint(checkpoint, storage_root=tmp_path)
            component_repository.transition(
                by_component[component].id,
                expected_state=RemoteComponentState.PENDING,
                requested_state=RemoteComponentState.NOT_APPLICABLE,
                checkpoint_path=str(checkpoint.checkpoint_path),
                query_fingerprint=checkpoint.query_fingerprint,
                ended_at=datetime.now(UTC),
            )

        queue._finalize_remote_components(job)
        stored = repository.get_job(job.id)
    finally:
        queue.close()

    assert stored.status is BatchJobStatus.FAILED
    assert stored.phase is BatchJobPhase.TERMINAL
    assert stored.error_code == "LOCAL_STAGING_PATH_ERROR"
    assert stored.error_message == "Falha local ao preparar os artefatos temporários."


def test_staged_component_workers_merge_once_then_release_serial_build(tmp_path) -> None:
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
    raw = store.raw()
    output_root = (tmp_path / "data").resolve()
    raw["defaults"]["output_root"] = str(output_root)
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    command_components: list[str] = []
    build_saw: list[str] = []

    def executor_runner(command, cwd, progress_callback=None):
        assert command[3] == "collect-component"
        component = ReportComponent(command[command.index("--component") + 1])
        checkpoint_path = Path(
            command[command.index("--component-checkpoint") + 1]
        ).resolve()
        run_id = command[command.index("--run-id") + 1]
        logical_job_id = command[command.index("--logical-job-id") + 1]
        command_components.append(component.value)
        if component is ReportComponent.CLOUD:
            progress_callback(
                {
                    "event": "TENABLE_CLOUD_PROGRESS",
                    "source": "tenable_cloud_security",
                    "status": "COMPLETE",
                    "current": 1,
                    "total": 1,
                    "snapshot_id": "cloud-dataset-01",
                    "origin": "created",
                }
            )
        else:
            progress_callback(
                {
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "source": (
                        "tenable_vm_vulnerabilities"
                        if component is ReportComponent.VM_CORE
                        else "tenable_was_findings"
                    ),
                    "status": "FINISHED",
                    "completed_chunks": 2,
                    "total_chunks": 2,
                    "export_uuid": (
                        "00000000-0000-0000-0000-000000000401"
                        if component is ReportComponent.VM_CORE
                        else "00000000-0000-0000-0000-000000000402"
                    ),
                    "origin": "created",
                }
            )
        checkpoint = ComponentCollectionCheckpoint(
            schema_version=1,
            checkpoint_path=checkpoint_path,
            component=component,
            client_id="client-01",
            tenant_id="tenant-01",
            run_id=run_id,
            logical_job_id=logical_job_id,
            execution_type="MANUAL",
            mode="manual",
            origin="MANUAL",
            attempt_number=1,
            period={
                "start_at": "2026-08-01T00:00:00Z",
                "end_at": "2026-09-01T00:00:00Z",
            },
            status=RemoteComponentState.COMPLETE,
            artifacts=(),
            metadata={"status": "COMPLETE"},
            query_fingerprint=(
                {
                    ReportComponent.VM_CORE: "a",
                    ReportComponent.WAS: "b",
                    ReportComponent.CLOUD: "c",
                }[component]
                * 64
            ),
        )
        persist_component_checkpoint(checkpoint, storage_root=output_root)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "event": "TENABLE_COMPONENT_CHECKPOINT",
                    "status": "COMPLETE",
                    "component": component.value,
                    "checkpoint": str(checkpoint_path),
                }
            ),
            stderr="",
        )

    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    executor = JobQueue(tmp_path, config_path, executor_runner, start_worker=False)

    def build_runner(job: WebBatchJob) -> BatchJobResult:
        build_saw.append(str(job.collection_checkpoint_path))
        return BatchJobResult(status=BatchJobStatus.COMPLETE)

    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-components",
        poll_interval=0.01,
        build_runner=build_runner,
        remote_workers=3,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=output_root,
    )
    try:
        created = queue.enqueue_requests(
            (
                (
                    "client-01",
                    {
                        "mode": "manual",
                        "start_at": "2026-08-01T00:00:00Z",
                        "end_at": "2026-09-01T00:00:00Z",
                    },
                ),
            ),
            batch_options={"execution_model": "STAGED_V1"},
        )
        assert queue.wait_until_idle(timeout=5)
        detail = queue.batch_snapshot(created[0]["batch_id"])
        durable_job = repository.list_batch_jobs(UUID(created[0]["batch_id"]))[0]
        component_rows = component_repository.list_for_jobs((durable_job.id,))[
            durable_job.id
        ]
    finally:
        queue.close()

    assert sorted(command_components) == sorted(item.value for item in ReportComponent)
    assert len(build_saw) == 1
    assert Path(build_saw[0]).is_file()
    assert detail["jobs"][0]["status"] == "COMPLETE"
    assert [event["event_type"] for event in detail["events"]].count(
        "COLLECTION_READY"
    ) == 1
    assert {row.window_number for row in component_rows} == {1}
    assert {row.state for row in component_rows} == {RemoteComponentState.COMPLETE}
    assert all(row.last_contact_at is not None for row in component_rows)
    assert all(row.query_fingerprint is not None for row in component_rows)
    assert {
        row.remote_identifier for row in component_rows
    } == {
        "00000000-0000-0000-0000-000000000401",
        "00000000-0000-0000-0000-000000000402",
        "cloud-dataset-01",
    }


def test_component_recovery_reuses_uuid_then_allows_only_one_replacement_window(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(
            1,
            status=BatchJobStatus.RUNNING,
            phase=BatchJobPhase.REMOTE_RUNNING,
        ),
        payload={"mode": "automatic", "run_id": "run-recovery"},
    )
    repository.create_batch(batch, (job,))
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-recovery",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    uuid_a = "00000000-0000-0000-0000-000000000501"
    uuid_b = "00000000-0000-0000-0000-000000000502"
    try:
        first = component_repository.create_for_job(
            batch_job_id=job.id,
            components=(ReportComponent.VM_CORE,),
            window_number=1,
            deadline_at=datetime.now(UTC) + timedelta(hours=10),
            origin="SCHEDULED",
        )[0]
        first = component_repository.claim_next(worker_id="first")
        first = component_repository.transition(
            first.id,
            expected_state=first.state,
            requested_state=first.state,
            identifier_kind=RemoteIdentifierKind.UUID,
            remote_identifier=uuid_a,
            identifier_origin="created",
        )
        queue._handle_failed_remote_component(
            job,
            first,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_TEMPORARY",
                error_message="Tempo limite aguardando export VM.",
                payload={"retryable": True},
            ),
        )
        second = component_repository.list_for_jobs((job.id,))[job.id][-1]
        assert second.window_number == 2
        assert second.remote_identifier == uuid_a
        assert second.replacement_created_in_window_2 is False

        second = component_repository.claim_next(worker_id="second")
        queue._handle_failed_remote_component(
            job,
            second,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_EXPORT_RECOVERY_UNAVAILABLE",
                error_message="Identificador remoto expirou.",
                payload={"retryable": True},
            ),
        )
        replacement = component_repository.list_for_jobs((job.id,))[job.id][-1]
        assert replacement.window_number == 2
        assert replacement.remote_identifier is None
        assert replacement.replacement_created_in_window_2 is True

        replacement = component_repository.claim_next(worker_id="replacement")
        replacement = component_repository.transition(
            replacement.id,
            expected_state=replacement.state,
            requested_state=replacement.state,
            identifier_kind=RemoteIdentifierKind.UUID,
            remote_identifier=uuid_b,
            identifier_origin="created",
        )
        queue._handle_failed_remote_component(
            job,
            replacement,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_TEMPORARY",
                error_message="Tempo limite aguardando export VM.",
                payload={"retryable": True},
            ),
        )
        third = component_repository.list_for_jobs((job.id,))[job.id][-1]
        assert third.window_number == 3
        assert third.remote_identifier == uuid_b

        third = component_repository.claim_next(worker_id="third")
        queue._handle_failed_remote_component(
            job,
            third,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_TEMPORARY",
                error_message="Tempo limite aguardando export VM.",
                payload={"retryable": True},
            ),
        )
        rows = component_repository.list_for_jobs((job.id,))[job.id]
    finally:
        queue.close()

    assert len(rows) == 4
    assert rows[-1].state is RemoteComponentState.WAITING_MANUAL_RETRY
    assert max(row.window_number for row in rows) == 3


def test_component_auth_failure_stops_without_opening_automatic_retry(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(
            1,
            status=BatchJobStatus.RUNNING,
            phase=BatchJobPhase.REMOTE_RUNNING,
        ),
        payload={"mode": "automatic", "run_id": "run-auth-failure"},
    )
    repository.create_batch(batch, (job,))
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-auth-failure",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    repository.create_batch(batch, (job,))
    try:
        component_repository.create_for_job(
            batch_job_id=job.id,
            components=(ReportComponent.CLOUD,),
            window_number=1,
            deadline_at=datetime.now(UTC) + timedelta(hours=10),
            origin="SCHEDULED",
        )
        running = component_repository.claim_next(worker_id="cloud-auth")
        queue._handle_failed_remote_component(
            job,
            running,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_AUTH_INVALID",
                error_message="Autenticação Cloud recusada.",
                payload={"retryable": False},
            ),
        )
        rows = component_repository.list_for_jobs((job.id,))[job.id]
    finally:
        queue.close()

    assert len(rows) == 1
    assert rows[0].state is RemoteComponentState.NON_RETRYABLE_FAILURE
    assert rows[0].retryable is False


def test_explicit_manual_retry_never_opens_another_automatic_window(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={"mode": "manual", "run_id": "run-manual-retry"},
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-manual-retry",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    repository.create_batch(batch, (job,))
    try:
        component_repository.create_for_job(
            batch_job_id=job.id,
            components=(ReportComponent.VM_CORE,),
            window_number=1,
            deadline_at=datetime.now(UTC) + timedelta(hours=10),
            origin="MANUAL_RETRY",
        )
        running = component_repository.claim_next(worker_id="manual-retry")
        queue._handle_failed_remote_component(
            job,
            running,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_TEMPORARY",
                error_message="Falha temporária.",
                payload={"retryable": True},
            ),
        )
        rows = component_repository.list_for_jobs((job.id,))[job.id]
    finally:
        queue.close()
    assert len(rows) == 1
    assert rows[0].state is RemoteComponentState.WAITING_MANUAL_RETRY


def test_replacement_inside_same_window_preserves_original_deadline(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={"mode": "automatic", "run_id": "run-deadline"},
    )
    repository.create_batch(batch, (job,))
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(tmp_path, tmp_path / "orchestration" / "clients.json", lambda *args, **kwargs: None, start_worker=False),
        worker_id="worker-deadline",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    deadline = datetime.now(UTC) + timedelta(hours=4)
    try:
        component_repository.create_for_job(
            batch_job_id=job.id,
            components=(ReportComponent.VM_CORE,),
            window_number=2,
            deadline_at=deadline,
            origin="AUTOMATIC_RETRY",
            replacement_created_in_window_2=False,
        )
        running = component_repository.claim_next(worker_id="invalid-uuid")
        queue._handle_failed_remote_component(
            job,
            running,
            BatchJobResult(
                status=BatchJobStatus.FAILED,
                error_code="TENABLE_EXPORT_RECOVERY_UNAVAILABLE",
                error_message="Identificador expirado.",
                payload={"retryable": True},
            ),
        )
        replacement = component_repository.list_for_jobs((job.id,))[job.id][-1]
    finally:
        queue.close()
    assert replacement.window_number == 2
    assert replacement.deadline_at == deadline


def test_automatic_retry_keeps_stable_checkpoint_identity_origin(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={
            "mode": "manual",
            "run_id": "run-stable-origin",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    repository.create_batch(batch, (job,))
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-stable-origin",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    component = component_repository.create_for_job(
        batch_job_id=job.id,
        components=(ReportComponent.VM_CORE,),
        window_number=2,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="AUTOMATIC_RETRY",
        attempt_number=2,
    )[0]
    captured: dict[str, object] = {}

    def fail_without_running(*_args, **kwargs):
        captured.update(dict(kwargs["payload_overrides"]))
        return BatchJobResult(
            status=BatchJobStatus.FAILED,
            error_code="LOCAL_TEST_FAILURE",
            error_message="Falha local controlada.",
            payload={"retryable": False},
        )

    try:
        with patch.object(queue, "_run_executor_job", side_effect=fail_without_running):
            queue._run_remote_component(component)
    finally:
        queue.close()

    assert captured["origin"] == "MANUAL"
    assert captured["component_checkpoint"] == str(
        component_checkpoint_path(
            queue._component_request(job, component=ReportComponent.VM_CORE),
            ReportComponent.VM_CORE,
        )
    )


def test_recovery_window_preserves_local_checkpoint_without_remote_identifier(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={"mode": "manual", "run_id": "run-cloud-checkpoint"},
    )
    repository.create_batch(batch, (job,))
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-cloud-checkpoint",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    checkpoint_path = str((tmp_path / "cloud" / "checkpoint.json").resolve())
    current = component_repository.create_for_job(
        batch_job_id=job.id,
        components=(ReportComponent.CLOUD,),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL",
    )[0]
    current = component_repository.transition(
        current.id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.WAITING_MANUAL_RETRY,
        checkpoint_path=checkpoint_path,
        failure_code="CLOUD_SNAPSHOT_PUBLICATION_FAILED",
        failure_message="Falha local ao publicar o snapshot.",
        retryable=True,
        ended_at=datetime.now(UTC),
    )

    try:
        recovered = queue._create_recovery_component(
            job,
            current,
            window_number=2,
            reuse_identifier=False,
            replacement_created_in_window_2=True,
            replacement_created_in_window_3=False,
        )
    finally:
        queue.close()

    assert recovered.remote_identifier is None
    assert recovered.checkpoint_path == checkpoint_path


def test_cloud_failure_with_preserved_local_checkpoint_can_be_retried(
    tmp_path,
) -> None:
    repository = InMemoryRemoteComponentRepository()
    checkpoint_path = (tmp_path / "cloud" / "checkpoint.json").resolve()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text("{}", encoding="utf-8")
    component = repository.create_for_job(
        batch_job_id=UUID(int=8801),
        components=(ReportComponent.CLOUD,),
        window_number=2,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="AUTOMATIC_RETRY",
    )[0]
    component = repository.transition(
        component.id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.NON_RETRYABLE_FAILURE,
        checkpoint_path=str(checkpoint_path),
        failure_code="UNEXPECTED",
        failure_message="Falha local após a coleta.",
        retryable=False,
        ended_at=datetime.now(UTC),
    )

    assert _component_is_retryable(component) is True


def test_failed_component_checkpoint_is_forwarded_to_automatic_retry(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={
            "mode": "manual",
            "run_id": "run-cloud-failure",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    repository.create_batch(batch, (job,))
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-cloud-failure",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    component_repository.create_for_job(
        batch_job_id=job.id,
        components=(ReportComponent.CLOUD,),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL",
    )
    running = component_repository.claim_next(worker_id="cloud-worker")
    request = queue._component_request(job, component=ReportComponent.CLOUD)
    failed_checkpoint = ComponentCollectionCheckpoint(
        schema_version=1,
        checkpoint_path=component_checkpoint_path(request, ReportComponent.CLOUD),
        component=ReportComponent.CLOUD,
        client_id=request.client_id,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        logical_job_id=request.logical_job_id,
        execution_type=request.execution_type,
        mode=request.mode,
        origin=request.origin,
        attempt_number=request.attempt_number,
        period=dict(request.period),
        status=RemoteComponentState.WAITING_MANUAL_RETRY,
        artifacts=(),
        metadata={
            "failure_code": "CLOUD_SNAPSHOT_PUBLICATION_FAILED",
            "failure_message": "Falha local controlada.",
            "retryable": True,
        },
        query_fingerprint="c" * 64,
    )
    persist_component_checkpoint(failed_checkpoint, storage_root=tmp_path)
    result = BatchJobResult(
        status=BatchJobStatus.COMPLETE,
        payload={
            "_component_result": {
                "checkpoint": str(failed_checkpoint.checkpoint_path),
            }
        },
    )

    try:
        with patch.object(queue, "_run_executor_job", return_value=result):
            queue._run_remote_component(running)
        rows = component_repository.list_for_jobs((job.id,))[job.id]
    finally:
        queue.close()

    retry = max(rows, key=lambda item: item.attempt_number)
    assert retry.window_number == 2
    assert retry.checkpoint_path == str(failed_checkpoint.checkpoint_path)


def test_component_initializer_reconciles_terminal_checkpoints_after_restart(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.QUEUED, phase=BatchJobPhase.REMOTE_QUEUED),
        payload={
            "mode": "manual",
            "run_id": "run-reconcile-terminal",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-reconcile-terminal",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    repository.create_batch(batch, (job,))
    job = repository.claim_next_job(
        worker_id="restart-initializer",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
    )
    assert job is not None
    request = queue._component_request(job, component=ReportComponent.VM_CORE)
    rows = component_repository.create_for_job(
        batch_job_id=job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL",
        attempt_number=job.attempt_number,
    )
    by_component = {row.component: row for row in rows}
    for offset, component in enumerate(ReportComponent, start=1):
        status = (
            RemoteComponentState.NOT_APPLICABLE
            if component is ReportComponent.CLOUD
            else RemoteComponentState.COMPLETE
        )
        checkpoint = ComponentCollectionCheckpoint(
            schema_version=1,
            checkpoint_path=component_checkpoint_path(request, component),
            component=component,
            client_id=request.client_id,
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            logical_job_id=request.logical_job_id,
            execution_type=request.execution_type,
            mode=request.mode,
            origin=request.origin,
            attempt_number=request.attempt_number,
            period={
                **dict(request.period),
                "reference_at": f"2026-09-05T1{offset}:00:00Z",
            },
            status=status,
            artifacts=(),
            metadata={"reason_code": "CLOUD_DISABLED"} if component is ReportComponent.CLOUD else {},
            query_fingerprint=str(offset) * 64,
        )
        persist_component_checkpoint(checkpoint, storage_root=tmp_path)
        component_repository.transition(
            by_component[component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=status,
            checkpoint_path=str(checkpoint.checkpoint_path),
            query_fingerprint=checkpoint.query_fingerprint,
            ended_at=datetime.now(UTC),
        )

    try:
        queue._handle_component_initializer_result(
            job,
            BatchJobResult(status=BatchJobStatus.COMPLETE),
        )
        queue._handle_component_initializer_result(
            job,
            BatchJobResult(status=BatchJobStatus.COMPLETE),
        )
        stored = repository.get_job(job.id)
        event_types = [
            event.event_type for event in repository.list_events(batch.id)
        ]
    finally:
        queue.close()

    assert stored.phase is BatchJobPhase.READY_FOR_BUILD
    assert stored.collection_checkpoint_path
    assert event_types.count("REMOTE_COMPONENTS_CONSOLIDATING") == 1
    assert event_types.count("COLLECTION_READY") == 1


def test_component_initializer_does_not_reapply_uuid_to_terminal_component(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        payload={
            "mode": "manual",
            "run_id": "run-terminal-uuid",
            "vm_export_uuid": "00000000-0000-0000-0000-000000000991",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-terminal-uuid",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    rows = component_repository.create_for_job(
        batch_job_id=job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL",
        attempt_number=1,
    )
    vm = next(row for row in rows if row.component is ReportComponent.VM_CORE)
    component_repository.transition(
        vm.id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.COMPLETE,
        checkpoint_path=str((tmp_path / "vm.json").resolve()),
        ended_at=datetime.now(UTC),
    )

    try:
        result = queue._initialize_remote_components(job)
        stored_vm = component_repository.get(vm.id)
    finally:
        queue.close()

    assert result.status is BatchJobStatus.COMPLETE
    assert stored_vm.state is RemoteComponentState.COMPLETE
    assert stored_vm.remote_identifier is None


def test_explicit_retry_consolidates_complete_components_without_remote_collection(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    source_batch = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_FAILURES),
        options={"execution_model": "STAGED_V1"},
    )
    source_job = replace(
        _job(1, status=BatchJobStatus.FAILED, phase=BatchJobPhase.TERMINAL),
        logical_job_id="logical-ready-client",
        run_id="run-ready-client",
        error_code="UNEXPECTED",
        payload={
            "mode": "manual",
            "run_id": "run-ready-client",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    repository.create_batch(source_batch, (source_job,))
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-local-consolidation",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    source_request = queue._component_request(
        source_job,
        component=ReportComponent.VM_CORE,
    )
    rows = component_repository.create_for_job(
        batch_job_id=source_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL",
        attempt_number=1,
    )
    by_component = {row.component: row for row in rows}
    for offset, component in enumerate(ReportComponent, start=1):
        status = (
            RemoteComponentState.NOT_APPLICABLE
            if component is ReportComponent.CLOUD
            else RemoteComponentState.COMPLETE
        )
        checkpoint = ComponentCollectionCheckpoint(
            schema_version=1,
            checkpoint_path=component_checkpoint_path(source_request, component),
            component=component,
            client_id=source_request.client_id,
            tenant_id=source_request.tenant_id,
            run_id=source_request.run_id,
            logical_job_id=source_request.logical_job_id,
            execution_type=source_request.execution_type,
            mode=source_request.mode,
            origin=source_request.origin,
            attempt_number=source_request.attempt_number,
            period=dict(source_request.period),
            status=status,
            artifacts=(),
            metadata={"reason_code": "CLOUD_DISABLED"} if component is ReportComponent.CLOUD else {},
            query_fingerprint=str(offset) * 64,
        )
        persist_component_checkpoint(checkpoint, storage_root=tmp_path)
        component_repository.transition(
            by_component[component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=status,
            checkpoint_path=str(checkpoint.checkpoint_path),
            query_fingerprint=checkpoint.query_fingerprint,
            ended_at=datetime.now(UTC),
        )

    try:
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=source_batch.id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-local-consolidation",
                actor="test",
                reason="Montagem local explícita.",
            )
        )
        retry_batch_id = UUID(detail["batch"]["id"])
        retry_job = repository.list_batch_jobs(retry_batch_id)[0]
        retry_rows = component_repository.list_for_jobs((retry_job.id,))[retry_job.id]
        event_types = [
            event.event_type for event in repository.list_events(retry_batch_id)
        ]
    finally:
        queue.close()

    assert retry_job.phase is BatchJobPhase.READY_FOR_BUILD
    assert retry_job.status is BatchJobStatus.QUEUED
    assert retry_job.collection_checkpoint_path
    assert all(
        row.state in {
            RemoteComponentState.COMPLETE,
            RemoteComponentState.NOT_APPLICABLE,
        }
        for row in retry_rows
    )
    assert "REMOTE_COMPONENTS_CONSOLIDATING" in event_types
    assert "COLLECTION_READY" in event_types


def _seed_complete_component_jobs_for_retry(
    *,
    repository: InMemoryWebBatchRepository,
    component_repository: InMemoryRemoteComponentRepository,
    queue: DurableDashboardJobQueue,
    tmp_path: Path,
    count: int,
) -> tuple[WebBatch, tuple[WebBatchJob, ...]]:
    source_batch = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_FAILURES),
        options={"execution_model": "STAGED_V1"},
    )
    source_jobs = tuple(
        replace(
            _job(
                position,
                status=BatchJobStatus.FAILED,
                phase=BatchJobPhase.TERMINAL,
            ),
            logical_job_id=f"logical-ready-{position}",
            run_id=f"run-ready-{position}",
            error_code="UNEXPECTED",
            payload={
                "mode": "manual",
                "run_id": f"run-ready-{position}",
                "start_at": "2026-08-01T00:00:00Z",
                "end_at": "2026-09-01T00:00:00Z",
            },
        )
        for position in range(1, count + 1)
    )
    repository.create_batch(source_batch, source_jobs)
    for source_job in source_jobs:
        source_request = queue._component_request(
            source_job,
            component=ReportComponent.VM_CORE,
        )
        rows = component_repository.create_for_job(
            batch_job_id=source_job.id,
            components=tuple(ReportComponent),
            window_number=1,
            deadline_at=datetime.now(UTC) + timedelta(hours=10),
            origin="MANUAL",
            attempt_number=1,
        )
        by_component = {row.component: row for row in rows}
        for offset, component in enumerate(ReportComponent, start=1):
            status = (
                RemoteComponentState.NOT_APPLICABLE
                if component is ReportComponent.CLOUD
                else RemoteComponentState.COMPLETE
            )
            checkpoint = ComponentCollectionCheckpoint(
                schema_version=1,
                checkpoint_path=component_checkpoint_path(
                    source_request,
                    component,
                ),
                component=component,
                client_id=source_request.client_id,
                tenant_id=source_request.tenant_id,
                run_id=source_request.run_id,
                logical_job_id=source_request.logical_job_id,
                execution_type=source_request.execution_type,
                mode=source_request.mode,
                origin=source_request.origin,
                attempt_number=source_request.attempt_number,
                period=dict(source_request.period),
                status=status,
                artifacts=(),
                metadata=(
                    {"reason_code": "CLOUD_DISABLED"}
                    if component is ReportComponent.CLOUD
                    else {}
                ),
                query_fingerprint=str(offset) * 64,
            )
            persist_component_checkpoint(checkpoint, storage_root=tmp_path)
            component_repository.transition(
                by_component[component].id,
                expected_state=RemoteComponentState.PENDING,
                requested_state=status,
                checkpoint_path=str(checkpoint.checkpoint_path),
                query_fingerprint=checkpoint.query_fingerprint,
                ended_at=datetime.now(UTC),
            )
    return source_batch, source_jobs


def test_collective_local_consolidation_hides_pending_components_from_live_workers(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-collective-consolidation",
        start_worker=False,
        remote_workers=4,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
        poll_interval=0.01,
    )
    source_batch, source_jobs = _seed_complete_component_jobs_for_retry(
        repository=repository,
        component_repository=component_repository,
        queue=queue,
        tmp_path=tmp_path,
        count=20,
    )
    original_create = component_repository.create_for_job
    worker_claimed = threading.Event()
    release_worker = threading.Event()
    first_retry_create = threading.Event()

    def remote_runner(component) -> None:
        worker_claimed.set()
        release_worker.wait(2)

    def create_with_race_window(**kwargs):
        rows = original_create(**kwargs)
        if kwargs["batch_job_id"] not in {job.id for job in source_jobs} and not first_retry_create.is_set():
            first_retry_create.set()
            worker_claimed.wait(0.5)
        return rows

    component_repository.create_for_job = create_with_race_window
    assert queue._component_pool is not None
    queue._component_pool.runner = remote_runner
    queue._component_pool.start()
    try:
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=source_batch.id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-collective-consolidation",
                actor="test",
                reason="Montagem local coletiva.",
            )
        )
        retry_batch_id = UUID(detail["batch"]["id"])
        retry_jobs = repository.list_batch_jobs(retry_batch_id)
    finally:
        release_worker.set()
        queue.close()

    assert worker_claimed.is_set() is False
    assert len(retry_jobs) == 20
    assert all(job.status is BatchJobStatus.QUEUED for job in retry_jobs)
    assert all(job.phase is BatchJobPhase.READY_FOR_BUILD for job in retry_jobs)
    assert all(job.collection_checkpoint_path for job in retry_jobs)


def test_collective_local_consolidation_failure_does_not_abandon_siblings(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-consolidation-isolation",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    source_batch, source_jobs = _seed_complete_component_jobs_for_retry(
        repository=repository,
        component_repository=component_repository,
        queue=queue,
        tmp_path=tmp_path,
        count=2,
    )
    original_restore = queue._restore_publishable_components_for_local_consolidation

    def restore_with_one_failure(*, source_job, retry_job, latest):
        if source_job.id == source_jobs[0].id:
            raise RuntimeError("fixture preparation failure")
        return original_restore(
            source_job=source_job,
            retry_job=retry_job,
            latest=latest,
        )

    try:
        with patch.object(
            queue,
            "_restore_publishable_components_for_local_consolidation",
            side_effect=restore_with_one_failure,
        ):
            detail = queue.derive_batch(
                DerivedBatchRequest(
                    source_batch_id=source_batch.id,
                    kind=BatchAction.RETRY_INCOMPLETE,
                    idempotency_key="retry-consolidation-isolation",
                    actor="test",
                    reason="Isolar falha de preparação.",
                )
            )
        retry_jobs = repository.list_batch_jobs(UUID(detail["batch"]["id"]))
    finally:
        queue.close()

    assert retry_jobs[0].status is BatchJobStatus.FAILED
    assert retry_jobs[0].phase is BatchJobPhase.TERMINAL
    assert retry_jobs[0].error_code == "LOCAL_CONSOLIDATION_PREPARATION_FAILED"
    assert "fixture preparation failure" not in str(retry_jobs[0].error_message)
    assert retry_jobs[1].status is BatchJobStatus.QUEUED
    assert retry_jobs[1].phase is BatchJobPhase.READY_FOR_BUILD


def test_local_consolidation_recovers_components_from_retry_ancestor(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-ancestor-consolidation",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    root_batch, root_jobs = _seed_complete_component_jobs_for_retry(
        repository=repository,
        component_repository=component_repository,
        queue=queue,
        tmp_path=tmp_path,
        count=2,
    )
    interrupted_batch_id = UUID(int=1700)
    interrupted_jobs = tuple(
        replace(
            source_job,
            id=UUID(int=1700 + position),
            batch_id=interrupted_batch_id,
            position=position,
            status=BatchJobStatus.INTERRUPTED,
            phase=BatchJobPhase.TERMINAL,
            retry_of_batch_job_id=source_job.id,
            collection_checkpoint_path=None,
        )
        for position, source_job in enumerate(root_jobs, start=1)
    )
    repository.create_batch(
        WebBatch(
            id=interrupted_batch_id,
            idempotency_key="batch:interrupted-before-component-copy",
            kind="RETRY_INCOMPLETE",
            status=BatchStatus.PAUSED,
            options={"execution_model": "STAGED_V1"},
            source_batch_id=root_batch.id,
        ),
        interrupted_jobs,
    )

    try:
        detail = queue.derive_batch(
            DerivedBatchRequest(
                source_batch_id=interrupted_batch_id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key="retry-components-from-ancestor",
                actor="test",
                reason="Recuperar preparação interrompida.",
            )
        )
        retry_jobs = repository.list_batch_jobs(UUID(detail["batch"]["id"]))
        retry_components = component_repository.list_for_jobs(
            tuple(job.id for job in retry_jobs)
        )
    finally:
        queue.close()

    assert len(retry_jobs) == 2
    assert all(job.status is BatchJobStatus.QUEUED for job in retry_jobs)
    assert all(job.phase is BatchJobPhase.READY_FOR_BUILD for job in retry_jobs)
    assert all(job.collection_checkpoint_path for job in retry_jobs)
    assert all(
        {component.state for component in retry_components[job.id]}
        == {
            RemoteComponentState.COMPLETE,
            RemoteComponentState.NOT_APPLICABLE,
        }
        for job in retry_jobs
    )


def test_component_finalization_failure_is_recorded_in_parent_job(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.QUEUED, phase=BatchJobPhase.REMOTE_QUEUED),
        payload={"mode": "manual", "run_id": "run-finalization-failure"},
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-finalization-failure",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    repository.create_batch(batch, (job,))
    job = repository.claim_next_job(
        worker_id="restart-finalization",
        phases=(BatchJobPhase.REMOTE_QUEUED,),
    )
    assert job is not None

    try:
        with patch.object(
            queue,
            "_finalize_remote_components",
            side_effect=RuntimeError("fixture detail must stay private"),
        ):
            queue._handle_component_initializer_result(
                job,
                BatchJobResult(status=BatchJobStatus.COMPLETE),
            )
        stored = repository.get_job(job.id)
    finally:
        queue.close()

    assert stored.status is BatchJobStatus.FAILED
    assert stored.phase is BatchJobPhase.TERMINAL
    assert stored.error_code == "CHECKPOINT_COMPONENT_INCOMPLETE"
    assert "fixture detail" not in str(stored.error_message)


def test_component_finalization_is_isolated_per_client(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=JobQueue(
            tmp_path,
            tmp_path / "orchestration" / "clients.json",
            lambda *args, **kwargs: None,
            start_worker=False,
        ),
        worker_id="worker-finalization-isolation",
        start_worker=False,
        remote_workers=2,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    batch = _batch(status=BatchStatus.RUNNING)
    failed_job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        client_id="client-failed",
    )
    ready_job = replace(
        _job(2, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        client_id="client-ready",
    )
    repository.create_batch(batch, (failed_job, ready_job))
    ready_checkpoint = tmp_path / "ready.json"
    ready_checkpoint.write_text("{}", encoding="utf-8")

    def finalize(job):
        if job.id == failed_job.id:
            raise RuntimeError("isolated fixture failure")
        repository.advance_job_phase(
            job.id,
            expected_phase=BatchJobPhase.REMOTE_RUNNING,
            requested_phase=BatchJobPhase.READY_FOR_BUILD,
            collection_checkpoint_path=ready_checkpoint,
        )

    try:
        with patch.object(queue, "_finalize_remote_components", side_effect=finalize):
            queue._finalize_remote_components_safely(failed_job)
            queue._finalize_remote_components_safely(ready_job)
        failed = repository.get_job(failed_job.id)
        ready = repository.get_job(ready_job.id)
    finally:
        queue.close()

    assert failed.status is BatchJobStatus.FAILED
    assert failed.error_code == "CHECKPOINT_COMPONENT_INCOMPLETE"
    assert ready.status is BatchJobStatus.QUEUED
    assert ready.phase is BatchJobPhase.READY_FOR_BUILD


def test_restarted_component_uses_only_remaining_original_window(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(
            1,
            status=BatchJobStatus.RUNNING,
            phase=BatchJobPhase.REMOTE_RUNNING,
        ),
        payload={
            "mode": "manual",
            "run_id": "run-restarted-window",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    repository.create_batch(batch, (job,))
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-restarted-window",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    deadline = datetime.now(UTC) + timedelta(seconds=5)
    component_repository.create_for_job(
        batch_job_id=job.id,
        components=(ReportComponent.VM_CORE,),
        window_number=1,
        deadline_at=deadline,
        origin="MANUAL",
    )
    running = component_repository.claim_next(worker_id="old-process")
    captured: dict[str, object] = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs["payload_overrides"])
        return BatchJobResult(
            status=BatchJobStatus.FAILED,
            error_code="TENABLE_AUTH_INVALID",
            error_message="Autenticação recusada.",
            payload={"retryable": False},
        )

    queue._run_executor_job = fake_run
    try:
        queue._run_remote_component(running)
        stored = component_repository.list_for_jobs((job.id,))[job.id][0]
    finally:
        queue.close()

    assert 1 <= int(captured["remote_processing_timeout_seconds"]) <= 5
    assert stored.deadline_at == deadline


@pytest.mark.parametrize(
    ("available_component", "waiting_component", "not_applicable_component"),
    (
        (ReportComponent.VM_CORE, ReportComponent.WAS, ReportComponent.CLOUD),
        (ReportComponent.CLOUD, ReportComponent.VM_CORE, ReportComponent.WAS),
    ),
)
def test_available_checkpoint_releases_partial_build_after_retries_exhausted(
    tmp_path,
    available_component,
    waiting_component,
    not_applicable_component,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(
            1,
            status=BatchJobStatus.RUNNING,
            phase=BatchJobPhase.REMOTE_RUNNING,
        ),
        payload={
            "mode": "manual",
            "run_id": "run-partial-build",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-partial-build",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    repository.create_batch(batch, (job,))
    request = queue._component_request(job, component=ReportComponent.VM_CORE)
    deadline = datetime.now(UTC) + timedelta(hours=10)
    try:
        created = component_repository.create_for_job(
            batch_job_id=job.id,
            components=tuple(ReportComponent),
            window_number=1,
            deadline_at=deadline,
            origin="MANUAL",
        )
        by_component = {row.component: row for row in created}
        for component, state in (
            (available_component, RemoteComponentState.COMPLETE),
            (not_applicable_component, RemoteComponentState.NOT_APPLICABLE),
        ):
            checkpoint = ComponentCollectionCheckpoint(
                schema_version=1,
                checkpoint_path=(
                    request.checkpoint_path.parent
                    / request.run_id
                    / component.value.lower()
                    / "checkpoint.json"
                ),
                component=component,
                client_id=request.client_id,
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                logical_job_id=request.logical_job_id,
                execution_type=request.execution_type,
                mode=request.mode,
                origin=request.origin,
                attempt_number=request.attempt_number,
                period=dict(request.period),
                status=state,
                artifacts=(),
                metadata={"status": state.value},
                query_fingerprint=("a" if component is ReportComponent.VM_CORE else "c")
                * 64,
            )
            persist_component_checkpoint(checkpoint, storage_root=tmp_path)
            component_repository.transition(
                by_component[component].id,
                expected_state=RemoteComponentState.PENDING,
                requested_state=state,
                checkpoint_path=str(checkpoint.checkpoint_path),
                query_fingerprint=checkpoint.query_fingerprint,
                ended_at=datetime.now(UTC),
            )
        component_repository.transition(
            by_component[waiting_component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.WAITING_MANUAL_RETRY,
            failure_code="AUTOMATIC_RETRY_EXHAUSTED",
            failure_message="As janelas automáticas do WAS foram esgotadas.",
            retryable=True,
            ended_at=datetime.now(UTC),
        )

        queue._finalize_remote_components(job)
        stored_job = repository.get_job(job.id)
        merged = load_collection_checkpoint(
            stored_job.collection_checkpoint_path,
            storage_root=tmp_path,
        )
    finally:
        queue.close()

    assert stored_job.phase is BatchJobPhase.READY_FOR_BUILD
    assert merged.component_metadata[available_component.value]["status"] == "COMPLETE"
    assert merged.component_metadata[waiting_component.value]["status"] == "FAILED"
    assert (
        merged.component_metadata[not_applicable_component.value]["status"]
        == "NOT_APPLICABLE"
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


@pytest.mark.parametrize("operation", ("staged_component", "staged_build"))
def test_staged_executor_drops_inherited_partial_outcome(
    tmp_path,
    operation,
) -> None:
    config_path = tmp_path / "orchestration" / "clients.json"
    store = DashboardConfigStore(project_root=tmp_path, config_path=config_path)
    store.add_client(
        {
            "client_id": "client-1",
            "display_name": "Client 1",
            "access_key": "fixture-access",
            "secret_key": "fixture-secret",
        }
    )

    def runner(command, cwd, progress_callback=None):
        del cwd, progress_callback
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(
                {
                    "event": "TENABLE_COMPONENT_CHECKPOINT",
                    "status": "COMPLETE",
                    "component": "CLOUD",
                    "checkpoint": str(tmp_path / "cloud-checkpoint.json"),
                    "artifact_count": 1,
                }
            ),
            stderr="",
        )

    repository = InMemoryWebBatchRepository()
    legacy = JobQueue(tmp_path, config_path, runner, start_worker=False)
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-clean-component-outcome",
        start_worker=False,
    )
    job = replace(
        _job(1),
        logical_job_id="logical-cloud",
        run_id="run-cloud",
        payload={
            "client_id": "client-1",
            "mode": "manual",
            "days": None,
            "start_at": "2026-08-01T03:00:00Z",
            "end_at": "2026-09-01T03:00:00Z",
            "component_set_status": "PARTIAL_FAILURE",
            "retryable_components": ["CLOUD"],
            "components": [{"component": "CLOUD", "status": "FAILED"}],
            "cloud_status": "FAILED",
            "warnings": [{"code": "CLOUD_COMPONENT_FAILED"}],
        },
    )

    try:
        result = queue._run_executor_job(
            job,
            operation=operation,
            checkpoint_path=(
                str(tmp_path / "collection-checkpoint.json")
                if operation == "staged_build"
                else None
            ),
            executor_job_id="isolated-cloud",
            payload_overrides=(
                {
                    "component": "CLOUD",
                    "component_checkpoint": str(tmp_path / "cloud-checkpoint.json"),
                    "window_number": 1,
                    "deadline_at": "2026-09-07T12:00:00Z",
                    "run_id": "run-cloud",
                    "logical_job_id": "logical-cloud",
                    "attempt_number": 2,
                    "origin": "MANUAL",
                    "remote_identifier": None,
                    "identifier_kind": None,
                    "identifier_origin": None,
                    "previous_component_checkpoint": None,
                }
                if operation == "staged_component"
                else None
            ),
        )
    finally:
        queue.close()

    assert result.status is BatchJobStatus.COMPLETE
    if operation == "staged_component":
        assert result.payload["_component_result"]["status"] == "COMPLETE"
    assert "component_set_status" not in result.payload
    assert result.payload["warnings"] == []


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
        remote_component_repository=InMemoryRemoteComponentRepository(),
    )
    try:
        capacities = app.jobs.capacity_snapshot()
    finally:
        app.jobs.close()

    by_phase = {tuple(item["phases"]): item["workers"] for item in capacities}
    assert by_phase[(BatchJobPhase.LEGACY.value,)] == 1
    assert by_phase[("REMOTE_COMPONENT",)] == 20
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


def test_staged_component_retry_selects_vm_and_preserves_complete_cloud(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-staged-component-retry",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    source_batch = _batch(status=BatchStatus.COMPLETE_WITH_WARNINGS)
    source_job = replace(
        _job(
            1,
            status=BatchJobStatus.PARTIALLY_COMPLETE,
            phase=BatchJobPhase.TERMINAL,
        ),
        run_id="published-run-staged",
        payload={
            "run_id": "published-run-staged",
            "mode": "automatic",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
            "retryable_components": ["VM_CORE"],
        },
    )
    repository.create_batch(source_batch, (source_job,))
    deadline = datetime.now(UTC) + timedelta(hours=10)
    source_components = component_repository.create_for_job(
        batch_job_id=source_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=deadline,
        origin="SCHEDULED",
    )
    by_component = {item.component: item for item in source_components}
    component_repository.transition(
        by_component[ReportComponent.VM_CORE].id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.WAITING_MANUAL_RETRY,
        identifier_kind=RemoteIdentifierKind.UUID,
        remote_identifier="00000000-0000-0000-0000-000000000901",
        identifier_origin="created",
        failure_code="AUTOMATIC_RETRY_EXHAUSTED",
        failure_message="As janelas automáticas foram esgotadas.",
        retryable=True,
        ended_at=datetime.now(UTC),
    )
    component_repository.transition(
        by_component[ReportComponent.WAS].id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.NOT_APPLICABLE,
        checkpoint_path=str((tmp_path / "was-checkpoint.json").resolve()),
        ended_at=datetime.now(UTC),
    )
    component_repository.transition(
        by_component[ReportComponent.CLOUD].id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.COMPLETE,
        checkpoint_path=str((tmp_path / "cloud-checkpoint.json").resolve()),
        ended_at=datetime.now(UTC),
    )

    try:
        queued = queue.enqueue_component_retry(
            run_id="published-run-staged",
            client_id="client-1",
            selected_components=("VM_CORE",),
        )
        retry_batch = next(
            batch for batch in repository.list_batches() if batch.id != source_batch.id
        )
        retry_job = repository.list_batch_jobs(retry_batch.id)[0]
        retry_components = component_repository.list_for_jobs((retry_job.id,))[
            retry_job.id
        ]
    finally:
        queue.close()

    retry_by_component = {item.component: item for item in retry_components}
    assert retry_job.phase is BatchJobPhase.REMOTE_RUNNING
    assert retry_job.payload["selected_components"] == ["VM_CORE"]
    assert retry_by_component[ReportComponent.VM_CORE].state is RemoteComponentState.PENDING
    assert (
        retry_by_component[ReportComponent.VM_CORE].remote_identifier
        == "00000000-0000-0000-0000-000000000901"
    )
    assert retry_by_component[ReportComponent.CLOUD].state is RemoteComponentState.COMPLETE
    assert retry_by_component[ReportComponent.CLOUD].checkpoint_path.endswith(
        "cloud-checkpoint.json"
    )


def test_staged_component_retry_publishes_only_selected_component_atomically(
    tmp_path,
) -> None:
    class RaceProbeComponentRepository(InMemoryRemoteComponentRepository):
        def __init__(self) -> None:
            super().__init__()
            self.claimed_during_publication = None

        def create_for_job(self, **values):
            created = super().create_for_job(**values)
            if values.get("origin") == "MANUAL_RETRY":
                self.claimed_during_publication = self.claim_next(
                    worker_id="concurrent-worker"
                )
            return created

        def create_windows(self, windows):
            created = super().create_windows(windows)
            if windows and windows[0].origin == "MANUAL_RETRY":
                self.claimed_during_publication = self.claim_next(
                    worker_id="concurrent-worker"
                )
            return created

    repository = InMemoryWebBatchRepository()
    component_repository = RaceProbeComponentRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-atomic-component-retry",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    source_batch = _batch(status=BatchStatus.COMPLETE_WITH_WARNINGS)
    source_job = replace(
        _job(
            1,
            status=BatchJobStatus.PARTIALLY_COMPLETE,
            phase=BatchJobPhase.TERMINAL,
        ),
        run_id="published-run-cloud-retry",
        payload={
            "run_id": "published-run-cloud-retry",
            "mode": "manual",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
            "retryable_components": ["CLOUD"],
        },
    )
    repository.create_batch(source_batch, (source_job,))
    source_components = component_repository.create_for_job(
        batch_job_id=source_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="SCHEDULED",
    )
    source_by_component = {item.component: item for item in source_components}
    for component in (ReportComponent.VM_CORE, ReportComponent.WAS):
        component_repository.transition(
            source_by_component[component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.COMPLETE,
            checkpoint_path=str((tmp_path / f"{component.value.lower()}.json").resolve()),
            completed_units=2,
            total_units=2,
            ended_at=datetime.now(UTC),
        )
    component_repository.transition(
        source_by_component[ReportComponent.CLOUD].id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.WAITING_MANUAL_RETRY,
        failure_code="AUTOMATIC_RETRY_EXHAUSTED",
        failure_message="As janelas automáticas foram esgotadas.",
        retryable=True,
        ended_at=datetime.now(UTC),
    )

    try:
        queue.enqueue_component_retry(
            run_id="published-run-cloud-retry",
            client_id="client-1",
            selected_components=("CLOUD",),
        )
        retry_batch = next(
            batch for batch in repository.list_batches() if batch.id != source_batch.id
        )
        retry_job = repository.list_batch_jobs(retry_batch.id)[0]
        retry_components = component_repository.list_for_jobs((retry_job.id,))[
            retry_job.id
        ]
    finally:
        queue.close()

    claimed = component_repository.claimed_during_publication
    retry_by_component = {item.component: item for item in retry_components}
    assert claimed is not None
    assert claimed.component is ReportComponent.CLOUD
    assert retry_by_component[ReportComponent.VM_CORE].state is RemoteComponentState.COMPLETE
    assert retry_by_component[ReportComponent.WAS].state is RemoteComponentState.COMPLETE
    assert (
        retry_by_component[ReportComponent.CLOUD].state
        is RemoteComponentState.RUNNING_WINDOW_1
    )


def test_staged_component_retry_ignores_newer_ineligible_descendant(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-eligible-source-selection",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    source_batch = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_WARNINGS),
        created_at="2026-09-06T10:00:00Z",
    )
    source_job = replace(
        _job(
            1,
            status=BatchJobStatus.PARTIALLY_COMPLETE,
            phase=BatchJobPhase.TERMINAL,
        ),
        run_id="published-run-source-selection",
        payload={"run_id": "published-run-source-selection"},
        created_at="2026-09-06T10:00:00Z",
    )
    repository.create_batch(source_batch, (source_job,))
    source_rows = component_repository.create_for_job(
        batch_job_id=source_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="SCHEDULED",
    )
    for row in source_rows:
        is_cloud = row.component is ReportComponent.CLOUD
        component_repository.transition(
            row.id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=(
                RemoteComponentState.WAITING_MANUAL_RETRY
                if is_cloud
                else RemoteComponentState.COMPLETE
            ),
            failure_code="AUTOMATIC_RETRY_EXHAUSTED" if is_cloud else None,
            failure_message="Cloud falhou." if is_cloud else None,
            retryable=is_cloud,
            ended_at=datetime.now(UTC),
        )

    descendant_batch = replace(
        _batch(status=BatchStatus.STOPPED),
        id=UUID(int=2),
        idempotency_key="batch:test:descendant",
        created_at="2026-09-06T11:00:00Z",
    )
    descendant_job = replace(
        _job(
            1,
            status=BatchJobStatus.FAILED,
            phase=BatchJobPhase.TERMINAL,
        ),
        id=UUID(int=21),
        batch_id=descendant_batch.id,
        run_id="published-run-source-selection",
        payload={"run_id": "published-run-source-selection"},
        created_at="2026-09-06T11:00:00Z",
    )
    repository.create_batch(descendant_batch, (descendant_job,))
    descendant_rows = component_repository.create_for_job(
        batch_job_id=descendant_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL_RETRY",
    )
    for row in descendant_rows:
        component_repository.transition(
            row.id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.NON_RETRYABLE_FAILURE,
            failure_code="UNEXPECTED",
            failure_message="Execução interrompida.",
            retryable=False,
            ended_at=datetime.now(UTC),
        )

    try:
        selected = queue._staged_retry_source_job(
            run_id="published-run-source-selection",
            client_id="client-1",
            selected_components=(ReportComponent.CLOUD,),
        )
    finally:
        queue.close()

    assert selected is not None
    assert selected.id == source_job.id


def test_staged_cloud_retry_prefers_older_resumable_dataset_over_newer_failure(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-cloud-resume-source-selection",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    older_batch = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_WARNINGS),
        created_at="2026-09-06T10:00:00Z",
    )
    older_job = replace(
        _job(1, status=BatchJobStatus.PARTIALLY_COMPLETE, phase=BatchJobPhase.TERMINAL),
        run_id="published-run-cloud-resumable",
        payload={"run_id": "published-run-cloud-resumable"},
        created_at="2026-09-06T10:00:00Z",
    )
    repository.create_batch(older_batch, (older_job,))
    older_request = queue._component_request(
        older_job,
        component=ReportComponent.CLOUD,
    )
    cloud_checkpoint_path = component_checkpoint_path(
        older_request,
        ReportComponent.CLOUD,
    )
    dataset_path = (cloud_checkpoint_path.parent / "cloud-resume-dataset.json").resolve()
    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    dataset_path.write_text("{}", encoding="utf-8")
    checkpoint = ComponentCollectionCheckpoint(
        schema_version=1,
        checkpoint_path=cloud_checkpoint_path,
        component=ReportComponent.CLOUD,
        client_id=older_request.client_id,
        tenant_id=older_request.tenant_id,
        run_id=older_request.run_id,
        logical_job_id=older_request.logical_job_id,
        execution_type=older_request.execution_type,
        mode=older_request.mode,
        origin=older_request.origin,
        attempt_number=older_request.attempt_number,
        period=dict(older_request.period),
        status=RemoteComponentState.COMPLETE,
        artifacts=(
            CheckpointArtifact(
                component=ReportComponent.CLOUD,
                kind="cloud_dataset",
                path=dataset_path,
                sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
            ),
        ),
        metadata={"status": "COMPLETE"},
        query_fingerprint="c" * 64,
    )
    persist_component_checkpoint(checkpoint, storage_root=tmp_path)
    older_rows = component_repository.create_for_job(
        batch_job_id=older_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL_RETRY",
    )
    for row in older_rows:
        is_cloud = row.component is ReportComponent.CLOUD
        component_repository.transition(
            row.id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=(
                RemoteComponentState.WAITING_MANUAL_RETRY
                if is_cloud
                else RemoteComponentState.COMPLETE
            ),
            checkpoint_path=(str(checkpoint.checkpoint_path) if is_cloud else None),
            failure_code="CLOUD_SNAPSHOT_PUBLICATION_FAILED" if is_cloud else None,
            failure_message="Dataset preservado." if is_cloud else None,
            retryable=is_cloud,
            ended_at=datetime.now(UTC),
        )

    newer_batch = replace(
        _batch(status=BatchStatus.COMPLETE_WITH_FAILURES),
        id=UUID(int=2),
        idempotency_key="batch:test:newer-cloud-failure",
        created_at="2026-09-06T11:00:00Z",
    )
    newer_job = replace(
        _job(1, status=BatchJobStatus.FAILED, phase=BatchJobPhase.TERMINAL),
        id=UUID(int=21),
        batch_id=newer_batch.id,
        run_id="published-run-cloud-resumable",
        payload={"run_id": "published-run-cloud-resumable"},
        created_at="2026-09-06T11:00:00Z",
    )
    repository.create_batch(newer_batch, (newer_job,))
    newer_rows = component_repository.create_for_job(
        batch_job_id=newer_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL_RETRY",
    )
    for row in newer_rows:
        is_cloud = row.component is ReportComponent.CLOUD
        component_repository.transition(
            row.id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=(
                RemoteComponentState.WAITING_MANUAL_RETRY
                if is_cloud
                else RemoteComponentState.COMPLETE
            ),
            failure_code="CLOUD_SNAPSHOT_PUBLICATION_FAILED" if is_cloud else None,
            failure_message="Falha sem dataset local." if is_cloud else None,
            retryable=is_cloud,
            ended_at=datetime.now(UTC),
        )

    try:
        selected = queue._staged_retry_source_job(
            run_id="published-run-cloud-resumable",
            client_id="client-1",
            selected_components=(ReportComponent.CLOUD,),
        )
    finally:
        queue.close()

    assert selected is not None
    assert selected.id == older_job.id


def test_staged_component_retry_rebinds_preserved_checkpoints_before_merge(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-staged-component-rebind",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    source_batch = _batch(status=BatchStatus.COMPLETE_WITH_WARNINGS)
    source_job = replace(
        _job(
            1,
            status=BatchJobStatus.PARTIALLY_COMPLETE,
            phase=BatchJobPhase.TERMINAL,
        ),
        run_id="published-run-rebind",
        payload={
            "run_id": "published-run-rebind",
            "mode": "automatic",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
            "retryable_components": ["VM_CORE"],
        },
    )
    repository.create_batch(source_batch, (source_job,))
    source_request = queue._component_request(
        source_job,
        component=ReportComponent.VM_CORE,
    )
    source_components = component_repository.create_for_job(
        batch_job_id=source_job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="SCHEDULED",
    )
    source_by_component = {item.component: item for item in source_components}
    component_repository.transition(
        source_by_component[ReportComponent.VM_CORE].id,
        expected_state=RemoteComponentState.PENDING,
        requested_state=RemoteComponentState.WAITING_MANUAL_RETRY,
        identifier_kind=RemoteIdentifierKind.UUID,
        remote_identifier="00000000-0000-0000-0000-000000000911",
        identifier_origin="asset_export:created",
        failure_code="LOCAL_FILESYSTEM_TRANSIENT",
        failure_message="[WinError 206] caminho longo em tenable_vm_assets_v2",
        retryable=True,
        ended_at=datetime.now(UTC),
    )
    for component, status, fingerprint in (
        (ReportComponent.WAS, RemoteComponentState.NOT_APPLICABLE, "b" * 64),
        (ReportComponent.CLOUD, RemoteComponentState.COMPLETE, "c" * 64),
    ):
        checkpoint = ComponentCollectionCheckpoint(
            schema_version=1,
            checkpoint_path=component_checkpoint_path(source_request, component),
            component=component,
            client_id=source_request.client_id,
            tenant_id=source_request.tenant_id,
            run_id=source_request.run_id,
            logical_job_id=source_request.logical_job_id,
            execution_type=source_request.execution_type,
            mode=source_request.mode,
            origin=source_request.origin,
            attempt_number=source_request.attempt_number,
            period=dict(source_request.period),
            status=status,
            artifacts=(),
            metadata={"status": status.value},
            query_fingerprint=fingerprint,
        )
        persist_component_checkpoint(checkpoint, storage_root=tmp_path)
        component_repository.transition(
            source_by_component[component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=status,
            checkpoint_path=str(checkpoint.checkpoint_path),
            query_fingerprint=fingerprint,
            ended_at=datetime.now(UTC),
        )

    try:
        queue.enqueue_component_retry(
            run_id="published-run-rebind",
            client_id="client-1",
            selected_components=("VM_CORE",),
        )
        retry_batch = next(
            batch for batch in repository.list_batches() if batch.id != source_batch.id
        )
        retry_job = repository.list_batch_jobs(retry_batch.id)[0]
        retry_request = queue._component_request(
            retry_job,
            component=ReportComponent.VM_CORE,
        )
        retry_components = component_repository.list_for_jobs((retry_job.id,))[
            retry_job.id
        ]
        retry_by_component = {item.component: item for item in retry_components}
        vm_checkpoint = ComponentCollectionCheckpoint(
            schema_version=1,
            checkpoint_path=component_checkpoint_path(
                retry_request,
                ReportComponent.VM_CORE,
            ),
            component=ReportComponent.VM_CORE,
            client_id=retry_request.client_id,
            tenant_id=retry_request.tenant_id,
            run_id=retry_request.run_id,
            logical_job_id=retry_request.logical_job_id,
            execution_type=retry_request.execution_type,
            mode=retry_request.mode,
            origin=retry_request.origin,
            attempt_number=retry_request.attempt_number,
            period=dict(retry_request.period),
            status=RemoteComponentState.COMPLETE,
            artifacts=(),
            metadata={"status": "COMPLETE"},
            query_fingerprint="a" * 64,
        )
        persist_component_checkpoint(vm_checkpoint, storage_root=tmp_path)
        component_repository.transition(
            retry_by_component[ReportComponent.VM_CORE].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.COMPLETE,
            checkpoint_path=str(vm_checkpoint.checkpoint_path),
            query_fingerprint=vm_checkpoint.query_fingerprint,
            ended_at=datetime.now(UTC),
        )

        queue._finalize_remote_components(retry_job)
        stored = repository.get_job(retry_job.id)
        merged = load_collection_checkpoint(
            stored.collection_checkpoint_path,
            storage_root=tmp_path,
        )
    finally:
        queue.close()

    assert stored.phase is BatchJobPhase.READY_FOR_BUILD
    assert {artifact.kind for artifact in merged.artifacts} == set()
    assert merged.component_metadata["WAS"]["status"] == "NOT_APPLICABLE"
    assert merged.component_metadata["CLOUD"]["status"] == "COMPLETE"


def test_cloud_retry_merges_new_cloud_checkpoint_with_prior_vm_and_was(
    tmp_path,
) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    legacy = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=legacy,
        worker_id="worker-cloud-finalization",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    batch = _batch(status=BatchStatus.RUNNING)
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.REMOTE_RUNNING),
        run_id="published-run-cloud-finalization",
        logical_job_id="logical-cloud-finalization",
        attempt_number=2,
        retry_of_batch_job_id=UUID(int=900),
        payload={
            "run_id": "published-run-cloud-finalization",
            "mode": "manual",
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )
    repository.create_batch(batch, (job,))
    current_request = queue._component_request(
        job,
        component=ReportComponent.CLOUD,
    )
    prior_request = queue._component_request(
        replace(job, attempt_number=1),
        component=ReportComponent.VM_CORE,
    )
    rows = component_repository.create_for_job(
        batch_job_id=job.id,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="MANUAL_RETRY",
    )
    by_component = {item.component: item for item in rows}
    for component in ReportComponent:
        request = (
            current_request
            if component is ReportComponent.CLOUD
            else prior_request
        )
        checkpoint = ComponentCollectionCheckpoint(
            schema_version=1,
            checkpoint_path=component_checkpoint_path(request, component),
            component=component,
            client_id=request.client_id,
            tenant_id=request.tenant_id,
            run_id=request.run_id,
            logical_job_id=request.logical_job_id,
            execution_type=request.execution_type,
            mode=request.mode,
            origin=request.origin,
            attempt_number=request.attempt_number,
            period=dict(request.period),
            status=RemoteComponentState.COMPLETE,
            artifacts=(),
            metadata={"status": "COMPLETE"},
            query_fingerprint=hashlib.sha256(component.value.encode()).hexdigest(),
        )
        persist_component_checkpoint(checkpoint, storage_root=tmp_path)
        component_repository.transition(
            by_component[component].id,
            expected_state=RemoteComponentState.PENDING,
            requested_state=RemoteComponentState.COMPLETE,
            checkpoint_path=str(checkpoint.checkpoint_path),
            query_fingerprint=checkpoint.query_fingerprint,
            ended_at=datetime.now(UTC),
        )

    try:
        queue._finalize_remote_components(job)
        stored = repository.get_job(job.id)
        merged = load_collection_checkpoint(
            stored.collection_checkpoint_path,
            storage_root=tmp_path,
        )
    finally:
        queue.close()

    assert stored.phase is BatchJobPhase.READY_FOR_BUILD
    assert merged.attempt_number == 2
    assert merged.component_metadata["VM_CORE"]["status"] == "COMPLETE"
    assert merged.component_metadata["WAS"]["status"] == "COMPLETE"
    assert merged.component_metadata["CLOUD"]["status"] == "COMPLETE"


def test_family_counts_latest_effective_client_state_once(tmp_path) -> None:
    repository = InMemoryWebBatchRepository()
    component_repository = InMemoryRemoteComponentRepository()
    root_id = UUID(int=2100)
    root_job_id = UUID(int=2101)
    retry_id = UUID(int=2110)
    retry_job_id = UUID(int=2111)
    root = WebBatch(
        id=root_id,
        idempotency_key="family-root",
        kind="GENERATE_ALL",
        status=BatchStatus.COMPLETE_WITH_FAILURES,
        options={},
    )
    root_job = WebBatchJob(
        id=root_job_id,
        batch_id=root_id,
        client_id="client-a",
        position=1,
        status=BatchJobStatus.FAILED,
        attempt_number=1,
        phase=BatchJobPhase.TERMINAL,
        error_code="TENABLE_TEMPORARY",
        error_message="Falha temporária sanitizada.",
        created_at="2026-09-01T00:00:00Z",
    )
    repository.create_batch(root, (root_job,))
    retry = WebBatch(
        id=retry_id,
        idempotency_key="family-retry",
        kind="RETRY_INCOMPLETE",
        status=BatchStatus.RUNNING,
        options={},
        source_batch_id=root_id,
        root_batch_id=root_id,
        parent_batch_id=root_id,
    )
    retry_job = WebBatchJob(
        id=retry_job_id,
        batch_id=retry_id,
        client_id="client-a",
        position=1,
        status=BatchJobStatus.RUNNING,
        attempt_number=2,
        phase=BatchJobPhase.REMOTE_RUNNING,
        retry_of_batch_job_id=root_job_id,
        created_at="2026-09-01T10:00:00Z",
    )
    repository.create_batch(retry, (retry_job,))
    component_repository.create_for_job(
        batch_job_id=retry_job_id,
        components=(ReportComponent.VM_CORE,),
        window_number=2,
        deadline_at=datetime.now(UTC) + timedelta(hours=10),
        origin="AUTOMATIC_RETRY",
    )
    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        lambda *args, **kwargs: None,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=repository,
        executor=executor,
        worker_id="worker-family",
        start_worker=False,
        remote_workers=1,
        enable_staged_executor=True,
        remote_component_repository=component_repository,
        staged_output_root=tmp_path,
    )
    try:
        snapshot = queue.batch_family_snapshot(retry_id)
    finally:
        queue.close()

    assert snapshot["root_batch_id"] == str(root_id)
    assert snapshot["total_count"] == 1
    assert snapshot["counts"]["automatic_retry"] == 1
    assert snapshot["counts"]["failed"] == 0
    assert snapshot["clients"][0]["effective_status"] == "AUTOMATIC_RETRY"


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


def test_staged_build_reuses_valid_existing_publication(tmp_path) -> None:
    checkpoint_path = (tmp_path / "checkpoints" / "existing.json").resolve()
    request = RemoteCollectionRequest(
        storage_root=tmp_path.resolve(),
        checkpoint_path=checkpoint_path,
        client_id="client-01",
        tenant_id="client-01",
        run_id="published-run",
        logical_job_id="logical-run",
        execution_type="MANUAL",
        mode="manual",
        origin="MANUAL",
        attempt_number=1,
        period={
            "start_at": "2026-08-01T03:00:00Z",
            "end_at": "2026-09-01T03:00:00Z",
        },
    )
    checkpoint = CollectionCheckpoint(
        schema_version=1,
        client_id=request.client_id,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        logical_job_id=request.logical_job_id,
        execution_type=request.execution_type,
        mode=request.mode,
        origin=request.origin,
        attempt_number=request.attempt_number,
        period=request.period,
        component_metadata={},
        artifacts=(),
        hashes={},
    )
    collect_client_remote(
        request,
        dependencies=RemoteCollectionDependencies(collect=lambda _: checkpoint),
    )
    executor_calls: list[str] = []
    resolver_calls: list[tuple[str, str]] = []

    def legacy_runner(*_args, **_kwargs):
        executor_calls.append("called")
        return subprocess.CompletedProcess([], 0, stdout='{"status":"COMPLETE"}')

    def resolve_existing(job, loaded_checkpoint):
        resolver_calls.append((job.client_id, loaded_checkpoint.run_id))
        return BatchJobResult(
            status=BatchJobStatus.COMPLETE,
            payload={
                "status": "COMPLETE",
                "run_id": loaded_checkpoint.run_id,
                "reused_existing_publication": True,
            },
        )

    executor = JobQueue(
        tmp_path,
        tmp_path / "orchestration" / "clients.json",
        legacy_runner,
        start_worker=False,
    )
    queue = DurableDashboardJobQueue(
        repository=InMemoryWebBatchRepository(),
        executor=executor,
        worker_id="worker-existing-publication",
        start_worker=False,
        staged_output_root=tmp_path,
        published_build_resolver=resolve_existing,
    )
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.BUILD_RUNNING),
        client_id="client-01",
        collection_checkpoint_path=str(checkpoint_path),
    )
    try:
        result = queue._run_build_job(job)
    finally:
        queue.close()

    assert result.status is BatchJobStatus.COMPLETE
    assert result.payload["reused_existing_publication"] is True
    assert resolver_calls == [("client-01", "published-run")]
    assert executor_calls == []


def test_staged_build_runs_normally_without_existing_publication(tmp_path) -> None:
    executor_calls: list[list[str]] = []
    config_path = tmp_path / "orchestration" / "clients.json"
    store = DashboardConfigStore(project_root=tmp_path, config_path=config_path)
    store.add_client(
        {
            "client_id": "client-1",
            "display_name": "Client 1",
            "access_key": "fixture-access",
            "secret_key": "fixture-secret",
        }
    )

    def legacy_runner(command, *_args, **_kwargs):
        executor_calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"status":"COMPLETE","run_id":"new-run"}',
            stderr="",
        )

    executor = JobQueue(
        tmp_path,
        config_path,
        legacy_runner,
        start_worker=False,
    )
    checkpoint_path = (tmp_path / "checkpoint.json").resolve()
    request = RemoteCollectionRequest(
        storage_root=tmp_path.resolve(),
        checkpoint_path=checkpoint_path,
        client_id="client-1",
        tenant_id="client-1",
        run_id="new-run",
        logical_job_id="logical-run",
        execution_type="MANUAL",
        mode="manual",
        origin="MANUAL",
        attempt_number=1,
        period={
            "start_at": "2026-08-01T03:00:00Z",
            "end_at": "2026-09-01T03:00:00Z",
        },
    )
    collect_client_remote(
        request,
        dependencies=RemoteCollectionDependencies(
            collect=lambda _: CollectionCheckpoint(
                schema_version=1,
                client_id=request.client_id,
                tenant_id=request.tenant_id,
                run_id=request.run_id,
                logical_job_id=request.logical_job_id,
                execution_type=request.execution_type,
                mode=request.mode,
                origin=request.origin,
                attempt_number=request.attempt_number,
                period=request.period,
                component_metadata={},
                artifacts=(),
                hashes={},
            )
        ),
    )
    queue = DurableDashboardJobQueue(
        repository=InMemoryWebBatchRepository(),
        executor=executor,
        worker_id="worker-no-existing-publication",
        start_worker=False,
        staged_output_root=tmp_path,
        published_build_resolver=lambda *_: None,
    )
    job = replace(
        _job(1, status=BatchJobStatus.RUNNING, phase=BatchJobPhase.BUILD_RUNNING),
        payload={
            "client_id": "client-1",
            "mode": "manual",
            "run_id": "new-run",
            "selected_components": ["CLOUD"],
        },
        collection_checkpoint_path=str(checkpoint_path.resolve()),
    )
    try:
        result = queue._run_build_job(job)
    finally:
        queue.close()

    assert result.status is BatchJobStatus.COMPLETE
    assert [command[3] for command in executor_calls] == ["build-client"]
    command = executor_calls[0]
    option_index = command.index("--compact-snapshot-run-id")
    assert command[option_index + 1] == (
        f"new-run-component-retry-{job.id.hex}"
    )
