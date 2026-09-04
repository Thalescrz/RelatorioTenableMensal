"""Compatibility adapter between the dashboard executor and durable batches."""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from tenable_reports.application.execution_control import FileExecutionControl
from tenable_reports.application.failures import FailureCode, classify_failure
from tenable_reports.application.automatic_recovery import (
    AutomaticRecoveryPolicy,
    RecoveryAction,
    decide_recovery,
)
from tenable_reports.application.component_collection import (
    component_checkpoint_path,
    load_component_checkpoint,
    merge_component_checkpoints,
)
from tenable_reports.application.staged_execution import (
    RemoteCollectionDependencies,
    RemoteCollectionRequest,
    collect_client_remote,
)
from tenable_reports.application.web_batches import (
    BatchClientConflictError,
    BatchConfirmationError,
    BatchJobResult,
    DerivedBatchRequest,
    NoEligibleBatchJobsError,
    RemoteComponentRepository,
    WebBatchRepository,
    assert_sanitized_payload,
)
from tenable_reports.domain.remote_components import (
    RemoteIdentifierKind,
    RemoteObservation,
    RemoteObservationKind,
    RemoteComponentState,
    RemoteComponentWindow,
)
from tenable_reports.domain.report_components import ReportComponent
from tenable_reports.domain.web_batches import (
    BATCH_TERMINAL_STATUSES,
    BatchAction,
    BatchJobPhase,
    BatchJobStatus,
    BatchStatus,
    WebBatch,
    WebBatchEvent,
    WebBatchJob,
)
from tenable_reports.webapp.job_queue import (
    DurableRunner,
    DurableWorkerPool,
    DurableWorkerPoolGroup,
    RemoteComponentWorkerPool,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


_PRIVATE_DASHBOARD_KEYS = frozenset(
    {"checkpoint", "checkpoint_path", "collection_checkpoint_path"}
)
_DEFAULT_REMOTE_PROCESSING_TIMEOUT_SECONDS = 36_000
_REMOTE_COMPONENT_TERMINAL_STATES = frozenset(
    {
        RemoteComponentState.COMPLETE,
        RemoteComponentState.COMPLETE_WITH_WARNINGS,
        RemoteComponentState.NOT_APPLICABLE,
        RemoteComponentState.WAITING_MANUAL_RETRY,
        RemoteComponentState.NON_RETRYABLE_FAILURE,
        RemoteComponentState.INTERRUPTED,
    }
)
_REMOTE_COMPONENT_PUBLISHABLE_STATES = frozenset(
    {
        RemoteComponentState.COMPLETE,
        RemoteComponentState.COMPLETE_WITH_WARNINGS,
        RemoteComponentState.NOT_APPLICABLE,
    }
)
_INVALID_REMOTE_IDENTIFIER_CODES = frozenset(
    {
        "TENABLE_EXPORT_RECOVERY_UNAVAILABLE",
        "REMOTE_IDENTIFIER_INVALID",
        "REMOTE_IDENTIFIER_EXPIRED",
        "REMOTE_EXPORT_CANCELLED",
        "REMOTE_EXPORT_FAILED",
        "REMOTE_EXPORT_ABORTED",
        "CLOUD_CURSOR_INVALID",
        "CHECKPOINT_IDENTITY_MISMATCH",
    }
)


def _checkpoint_ready_for_build(value: str | Path | None) -> bool:
    if not value:
        return False
    path = Path(value).resolve()
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        metadata = payload.get("component_metadata")
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(metadata, Mapping):
        return False
    vm = metadata.get("VM_CORE")
    if not isinstance(vm, Mapping) or str(vm.get("status") or "").upper() not in {
        "COMPLETE",
        "COMPLETE_WITH_WARNINGS",
    }:
        return False
    for name in ("WAS", "CLOUD"):
        component = metadata.get(name)
        if not isinstance(component, Mapping):
            return False
        if str(component.get("status") or "").upper() not in {
            "COMPLETE",
            "FAILED",
            "SKIPPED",
        }:
            return False
    return True


def _safe_dashboard_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_dashboard_value(item)
            for key, item in value.items()
            if not str(key).startswith("_")
            and str(key).casefold() not in _PRIVATE_DASHBOARD_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_safe_dashboard_value(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class DashboardQueueSnapshot:
    jobs: tuple[dict[str, Any], ...]
    batches: tuple[dict[str, Any], ...]
    active_job_count: int


@dataclass(frozen=True, slots=True)
class BatchJobRetryability:
    candidate: bool
    retryable: bool
    recorded_error_code: str | None
    effective_error_code: str | None
    reason: str


def _batch_job_retryability(job: WebBatchJob) -> BatchJobRetryability:
    recorded_code = str(job.error_code or "").strip() or None
    if job.status is BatchJobStatus.FAILED:
        if recorded_code == "RECOVERY_SNAPSHOT_FAILED":
            return BatchJobRetryability(
                candidate=True,
                retryable=True,
                recorded_error_code=recorded_code,
                effective_error_code=recorded_code,
                reason="Falha importada com estado remoto preservado.",
            )
        failure = classify_failure({
            "error_code": recorded_code,
            "message": job.error_message,
        })
        preserved_vm_state = bool(
            job.vm_export_uuid
            or job.vm_resume_manifest_path
            or job.collection_checkpoint_path
            or str(job.payload.get("vm_export_uuid") or "").strip()
            or str(job.payload.get("vm_resume_manifest") or "").strip()
        )
        if (
            recorded_code is None
            and failure.code is FailureCode.UNEXPECTED
            and preserved_vm_state
        ):
            return BatchJobRetryability(
                candidate=True,
                retryable=True,
                recorded_error_code=None,
                effective_error_code=FailureCode.TENABLE_TEMPORARY.value,
                reason="Export VM preservado pode ser consultado novamente.",
            )
        return BatchJobRetryability(
            candidate=True,
            retryable=failure.retryable,
            recorded_error_code=recorded_code,
            effective_error_code=failure.code.value,
            reason=(
                "Falha transitória com nova tentativa permitida."
                if failure.retryable
                else "Falha requer correção antes de uma nova tentativa."
            ),
        )
    if job.status is BatchJobStatus.PARTIALLY_COMPLETE:
        components = tuple(
            str(component)
            for component in job.payload.get("retryable_components", ())
            if str(component) in {"VM_CORE", "WAS", "CLOUD"}
        )
        has_run = bool(
            job.run_id or str(job.payload.get("run_id") or "").strip()
        )
        retryable = bool(components and has_run)
        return BatchJobRetryability(
            candidate=True,
            retryable=retryable,
            recorded_error_code=recorded_code,
            effective_error_code=recorded_code,
            reason=(
                "Conjunto parcial com componentes retentáveis: "
                + ", ".join(components)
                if retryable
                else "Conjunto parcial sem componente retentável identificado."
            ),
        )
    if job.status in {
        BatchJobStatus.INTERRUPTED,
        BatchJobStatus.CANCELLED_BY_USER,
    }:
        return BatchJobRetryability(
            candidate=True,
            retryable=True,
            recorded_error_code=recorded_code,
            effective_error_code=recorded_code,
            reason="Execução interrompida pode ser retomada com o estado preservado.",
        )
    return BatchJobRetryability(
        candidate=False,
        retryable=False,
        recorded_error_code=recorded_code,
        effective_error_code=recorded_code,
        reason="Execução não está pendente de retentativa.",
    )


class DurableDashboardJobQueue:
    """Expose the legacy dashboard API while PostgreSQL owns job state."""

    def __init__(
        self,
        *,
        repository: WebBatchRepository,
        executor: Any,
        worker_id: str,
        poll_interval: float = 5.0,
        start_worker: bool = True,
        remote_runner: DurableRunner | None = None,
        build_runner: DurableRunner | None = None,
        remote_component_repository: RemoteComponentRepository | None = None,
        remote_workers: int = 0,
        enable_staged_executor: bool = False,
        staged_output_root: str | Path | None = None,
        remote_processing_timeout_seconds: int = (
            _DEFAULT_REMOTE_PROCESSING_TIMEOUT_SECONDS
        ),
    ) -> None:
        self.repository = repository
        self.executor = executor
        self._staged_output_root = (
            Path(staged_output_root).resolve()
            if staged_output_root is not None
            else (self.executor.project_root / "data").resolve()
        )
        self._active_lock = threading.RLock()
        self._active_jobs: dict[str, WebBatchJob] = {}
        self._active_components: dict[str, RemoteComponentWindow] = {}
        self._component_finalize_lock = threading.RLock()
        self._progress_events: dict[str, tuple[tuple[Any, ...], datetime]] = {}
        self._remote_processing_timeout_seconds = max(
            1,
            int(remote_processing_timeout_seconds),
        )
        self._remote_component_repository = remote_component_repository
        self._component_pool: RemoteComponentWorkerPool | None = None
        self.executor.progress_sink = self._persist_progress
        self.executor.process_sink = self._persist_process
        self.executor.fallback_sink = self._persist_fallback
        pools = [
            DurableWorkerPool(
                repository=repository,
                runner=self._run_job,
                worker_prefix=f"{worker_id}-legacy",
                phases=(BatchJobPhase.LEGACY,),
                workers=1,
                poll_interval=poll_interval,
                start_workers=False,
                reconcile=False,
            )
        ]
        normalized_remote_workers = int(remote_workers)
        if enable_staged_executor:
            remote_runner = remote_runner or (
                self._initialize_remote_components
                if remote_component_repository is not None
                else self._run_remote_job
            )
            build_runner = build_runner or self._run_build_job
        if remote_runner is not None:
            if normalized_remote_workers < 1:
                raise ValueError(
                    "remote_workers deve ser positivo quando o pool remoto existe."
                )
            pools.append(
                DurableWorkerPool(
                    repository=repository,
                    runner=lambda job: self._run_pool_job(job, remote_runner),
                    worker_prefix=f"tenable-remote-{worker_id}",
                    phases=(BatchJobPhase.REMOTE_QUEUED,),
                    workers=normalized_remote_workers,
                    poll_interval=poll_interval,
                    start_workers=False,
                    reconcile=False,
                    result_handler=(
                        self._handle_component_initializer_result
                        if remote_component_repository is not None
                        else self._handle_remote_result
                    ),
                )
            )
        elif normalized_remote_workers != 0:
            raise ValueError("remote_runner e obrigatorio para o pool remoto.")
        if build_runner is not None:
            pools.append(
                DurableWorkerPool(
                    repository=repository,
                    runner=lambda job: self._run_pool_job(job, build_runner),
                    worker_prefix=f"tenable-build-{worker_id}",
                    phases=(BatchJobPhase.READY_FOR_BUILD,),
                    workers=1,
                    poll_interval=poll_interval,
                    start_workers=False,
                    reconcile=False,
                )
            )
        self._dispatcher = DurableWorkerPoolGroup(
            repository=repository,
            pools=tuple(pools),
            start_workers=False if remote_component_repository is not None else start_worker,
        )
        if remote_component_repository is not None:
            if normalized_remote_workers < 1:
                raise ValueError(
                    "remote_workers deve ser positivo para componentes remotos."
                )
            self._component_pool = RemoteComponentWorkerPool(
                repository=remote_component_repository,
                runner=self._run_remote_component,
                worker_prefix=f"tenable-component-{worker_id}",
                workers=normalized_remote_workers,
                poll_interval=poll_interval,
                lease_seconds=self._remote_processing_timeout_seconds + 300,
                start_workers=False,
            )
            if start_worker:
                self._dispatcher.start()
                self._component_pool.start()

    def enqueue(
        self,
        client_ids: Sequence[str],
        request: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return self.enqueue_requests(
            tuple((client_id, request) for client_id in client_ids)
        )

    def enqueue_component_retry(
        self,
        *,
        run_id: str,
        client_id: str,
        selected_components: Sequence[Any],
        failed_only: bool = True,
    ) -> Mapping[str, Any]:
        components = [
            str(getattr(component, "value", component))
            for component in selected_components
        ]
        if not components or any(
            component not in {"VM_CORE", "WAS", "CLOUD"}
            for component in components
        ):
            raise ValueError("Retentativa sem componente válido.")
        if not failed_only:
            raise ValueError("A interface permite somente retentativas de falhas.")
        batch_id = uuid4()
        conflicts = self.repository.active_client_conflicts(
            (client_id,),
            excluding_batch_id=batch_id,
        )
        if conflicts:
            raise BatchClientConflictError(conflicts)
        job_id = uuid4()
        created_at = _now()
        control_file = self._control_file(
            batch_id=batch_id,
            job_id=job_id,
            mode="manual",
        )
        payload = {
            "job_id": job_id.hex,
            "batch_id": str(batch_id),
            "client_id": client_id,
            "operation": "component_retry",
            "source_run_id": run_id,
            "run_id": run_id,
            "selected_components": components,
            "status": BatchJobStatus.QUEUED.value,
            "mode": "manual",
            "days": None,
            "start_at": None,
            "end_at": None,
            "created_at": created_at,
            "_job_control_file": control_file,
        }
        batch = WebBatch(
            id=batch_id,
            idempotency_key=f"component-retry:{run_id}:{uuid4()}",
            kind=BatchAction.RETRY_INCOMPLETE.value,
            status=BatchStatus.QUEUED,
            options={
                "execution_model": "LEGACY",
                "source_run_id": run_id,
                "selected_components": components,
            },
            created_at=created_at,
        )
        job = WebBatchJob(
            id=job_id,
            batch_id=batch_id,
            client_id=client_id,
            position=1,
            status=BatchJobStatus.QUEUED,
            attempt_number=1,
            phase=BatchJobPhase.LEGACY,
            payload=payload,
            run_id=run_id,
            control_file=control_file,
            created_at=created_at,
        )
        self.repository.create_batch(batch, (job,))
        self._dispatcher.wake()
        return self.batch_snapshot(batch_id)["jobs"][0]

    def _control_file(
        self,
        *,
        batch_id: UUID,
        job_id: UUID,
        mode: str,
    ) -> str:
        scope_name = (
            "automatic-monthly" if mode == "automatic" else "manual"
        )
        return str(
            (
                self.executor.project_root
                / "data"
                / scope_name
                / "control"
                / "web-batches"
                / str(batch_id)
                / f"{job_id.hex}.json"
            ).resolve()
        )

    def enqueue_requests(
        self,
        requests: Sequence[tuple[str, Mapping[str, Any]]],
        *,
        batch_options: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        if batch_options is not None and not isinstance(batch_options, Mapping):
            raise ValueError("batch_options deve ser um objeto.")
        copied_batch_options = deepcopy(dict(batch_options or {}))
        if "requests" in copied_batch_options:
            raise ValueError("batch_options nao pode sobrescrever requests.")
        assert_sanitized_payload(copied_batch_options, path="batch_options")
        execution_model = str(
            copied_batch_options.get("execution_model") or "LEGACY"
        ).strip().upper()
        if execution_model not in {"LEGACY", "STAGED_V1"}:
            raise ValueError("execution_model invalido.")
        batch_id = uuid4()
        requested_client_ids = tuple(client_id for client_id, _ in requests)
        conflicts = self.repository.active_client_conflicts(
            requested_client_ids,
            excluding_batch_id=batch_id,
        )
        if conflicts:
            raise BatchClientConflictError(conflicts)
        created: list[dict[str, Any]] = []
        normalized_requests: list[dict[str, Any]] = []
        for client_id, request in requests:
            rows = self.executor.enqueue([client_id], request)
            created.extend(rows)
            normalized_requests.append(
                {"client_id": client_id, "request": dict(request)}
            )
        if not created:
            return []
        created_at = _now()
        options = {
            "requests": normalized_requests,
            **copied_batch_options,
        }
        assert_sanitized_payload(options, path="batch_options")
        batch = WebBatch(
            id=batch_id,
            idempotency_key=f"batch:create:{batch_id}",
            kind=(
                "GENERATE_ALL"
                if "selected_client_ids" in copied_batch_options or len(created) > 1
                else "GENERATE_ONE"
            ),
            status=BatchStatus.QUEUED,
            options=options,
            created_at=created_at,
        )

        def durable_job(
            position: int,
            row: Mapping[str, Any],
        ) -> WebBatchJob:
            job_id = UUID(hex=str(row["job_id"]))
            control_file = self._control_file(
                batch_id=batch_id,
                job_id=job_id,
                mode=str(row.get("mode") or "manual"),
            )
            return WebBatchJob(
                id=job_id,
                batch_id=batch_id,
                client_id=str(row["client_id"]),
                position=position,
                status=BatchJobStatus.QUEUED,
                attempt_number=1,
                phase=(
                    BatchJobPhase.REMOTE_QUEUED
                    if execution_model == "STAGED_V1"
                    else BatchJobPhase.LEGACY
                ),
                payload={
                    **dict(row),
                    "batch_id": str(batch_id),
                    "_job_control_file": str(control_file),
                },
                control_file=str(control_file),
                created_at=str(row.get("created_at") or created_at),
            )

        jobs = tuple(
            durable_job(position, row)
            for position, row in enumerate(created, start=1)
        )
        try:
            self.repository.create_batch(batch, jobs)
        finally:
            self._drain_executor_pending(len(created))
        self._dispatcher.wake()
        created_ids = {job.id for job in jobs}
        return [
            row
            for row in self.snapshot()
            if UUID(hex=str(row["job_id"])) in created_ids
        ]
    def derive_batch(
        self,
        request: DerivedBatchRequest,
    ) -> dict[str, Any]:
        return self._derive_batch(request)

    def retry(
        self,
        job_id: str,
        *,
        explicit_export_recovery: bool = False,
    ) -> dict[str, Any]:
        del explicit_export_recovery
        try:
            normalized_id = UUID(str(job_id))
        except ValueError:
            normalized_id = UUID(hex=str(job_id))
        source_job = self.repository.get_job(normalized_id)
        if source_job is None:
            raise KeyError("Trabalho nao encontrado.")
        detail = self._derive_batch(
            DerivedBatchRequest(
                source_batch_id=source_job.batch_id,
                kind=BatchAction.RETRY_INCOMPLETE,
                idempotency_key=f"retry-job:{source_job.id}:{uuid4()}",
                actor="interface-local",
                reason="Retentativa individual do export preservado.",
            ),
            selected_job_id=source_job.id,
        )
        return detail["jobs"][0]

    def _derive_batch(
        self,
        request: DerivedBatchRequest,
        *,
        selected_job_id: UUID | None = None,
    ) -> dict[str, Any]:
        source = self.repository.get_batch(request.source_batch_id)
        if source is None:
            raise KeyError("Lote de origem nao encontrado.")
        paused_recovery = (
            source.kind == "RECOVERED"
            and source.status is BatchStatus.PAUSED
        )
        if (
            source.status not in BATCH_TERMINAL_STATUSES
            and not paused_recovery
        ):
            raise ValueError(
                "O lote de origem ainda esta ativo e nao pode ser derivado."
            )
        if request.kind is BatchAction.RERUN_ALL:
            expected = f"GERAR NOVAMENTE {str(source.id)[:8]}"
            if str(request.confirmation_token or "").strip() != expected:
                raise BatchConfirmationError(
                    f'Digite exatamente "{expected}" para confirmar.'
                )

        batch_key = f"batch:derive:{request.idempotency_key}"
        batch_id = uuid5(NAMESPACE_URL, batch_key)
        existing = self.repository.get_batch(batch_id)
        if existing is not None:
            return self.batch_snapshot(existing.id)

        source_jobs = self.repository.list_batch_jobs(source.id)
        replacement_progress: dict[UUID, WebBatchEvent] = {}
        for event in self.repository.list_events(source.id):
            progress = event.payload
            if (
                event.job_id is not None
                and event.event_type == "JOB_PROGRESS"
                and str(progress.get("event") or "")
                == "TENABLE_EXPORT_PROGRESS"
                and str(progress.get("source") or "")
                == "tenable_vm_vulnerabilities"
                and str(progress.get("origin") or "").lower() == "created"
                and str(progress.get("status") or "").upper() == "STARTED"
                and str(progress.get("export_uuid") or "").strip()
            ):
                replacement_progress[event.job_id] = event
        selected = tuple(
            job
            for job in source_jobs
            if (
                (selected_job_id is None or job.id == selected_job_id)
                and (
                    request.kind is BatchAction.RERUN_ALL
                    or _batch_job_retryability(job).retryable
                )
            )
        )
        if request.kind is BatchAction.RETRY_INCOMPLETE:
            selected = tuple(
                job
                for job in selected
                if (
                    job.status is not BatchJobStatus.PARTIALLY_COMPLETE
                    or (
                        any(
                            str(component) in {"VM_CORE", "WAS", "CLOUD"}
                            for component in job.payload.get(
                                "retryable_components", ()
                            )
                        )
                        and bool(
                            job.run_id
                            or str(job.payload.get("run_id") or "").strip()
                        )
                    )
                )
            )
        if not selected:
            raise NoEligibleBatchJobsError(source.id)

        execution_model = str(
            source.options.get("execution_model") or (
                "STAGED_V1" if source.kind == "RECOVERED" else "LEGACY"
            )
        ).strip().upper()
        staged_model = execution_model == "STAGED_V1"

        selected_clients = {job.client_id for job in selected}
        conflicts = self.repository.active_client_conflicts(
            tuple(selected_clients),
            excluding_batch_id=source.id,
        )
        if conflicts:
            raise BatchClientConflictError(conflicts)

        created_at = _now()
        derived = WebBatch(
            id=batch_id,
            idempotency_key=batch_key,
            kind=request.kind.value,
            status=BatchStatus.QUEUED,
            options={
                **dict(source.options),
                "derived_action": request.kind.value,
                "execution_model": execution_model,
            },
            source_batch_id=source.id,
            created_at=created_at,
        )
        jobs: list[WebBatchJob] = []
        transient_keys = {
            "batch_id",
            "job_id",
            "status",
            "progress",
            "queue_position",
            "started_at",
            "ended_at",
            "run_id",
            "error",
            "error_code",
            "exit_code",
            "warnings",
            "export_progress",
            "was_export_progress",
            "cloud_progress",
            "tag_progress",
            "_job_control_file",
        }
        for position, source_job in enumerate(selected, start=1):
            replacement_event = replacement_progress.get(source_job.id)
            replacement_payload = (
                replacement_event.payload if replacement_event is not None else {}
            )
            replacement_uuid = str(
                replacement_payload.get("export_uuid") or ""
            ).strip()
            has_unpersisted_replacement = bool(
                replacement_uuid
                and replacement_uuid != source_job.vm_export_uuid
            )
            retry_vm_export_uuid = (
                replacement_uuid
                if has_unpersisted_replacement
                else source_job.vm_export_uuid
            )
            retry_vm_resume_manifest = (
                str(replacement_payload.get("partial_manifest") or "").strip()
                or None
                if has_unpersisted_replacement
                else source_job.vm_resume_manifest_path
            )
            retry_remote_started_at = (
                replacement_event.created_at
                if has_unpersisted_replacement and replacement_event is not None
                else source_job.remote_export_started_at
            )
            job_id = uuid5(batch_id, str(source_job.id))
            payload = {
                key: value
                for key, value in dict(source_job.payload).items()
                if key not in transient_keys
            }
            is_retry = request.kind is BatchAction.RETRY_INCOMPLETE
            component_only_retry = (
                is_retry
                and source_job.status is BatchJobStatus.PARTIALLY_COMPLETE
            )
            if component_only_retry:
                selected_components = [
                    str(component)
                    for component in source_job.payload.get(
                        "retryable_components", ()
                    )
                    if str(component) in {"VM_CORE", "WAS", "CLOUD"}
                ]
                if not selected_components:
                    raise NoEligibleBatchJobsError(source.id)
                payload.update({
                    "operation": "component_retry",
                    "source_run_id": (
                        source_job.run_id
                        or str(source_job.payload.get("run_id") or "")
                    ),
                    "selected_components": selected_components,
                })
            if is_retry and retry_vm_export_uuid:
                payload["vm_export_uuid"] = retry_vm_export_uuid
            if is_retry and retry_vm_resume_manifest:
                payload["vm_resume_manifest"] = retry_vm_resume_manifest
            mode = str(payload.get("mode") or "manual")
            control_file = self._control_file(
                batch_id=batch_id,
                job_id=job_id,
                mode=mode,
            )
            payload.update(
                {
                    "job_id": job_id.hex,
                    "batch_id": str(batch_id),
                    "client_id": source_job.client_id,
                    "status": BatchJobStatus.QUEUED.value,
                    "created_at": created_at,
                    "_job_control_file": control_file,
                }
            )
            reusable_checkpoint = (
                source_job.collection_checkpoint_path
                if is_retry
                and staged_model
                and source_job.collection_checkpoint_path
                and _checkpoint_ready_for_build(
                    source_job.collection_checkpoint_path
                )
                else None
            )
            jobs.append(
                WebBatchJob(
                    id=job_id,
                    batch_id=batch_id,
                    client_id=source_job.client_id,
                    position=position,
                    status=BatchJobStatus.QUEUED,
                    attempt_number=(
                        source_job.attempt_number + 1 if is_retry else 1
                    ),
                    phase=(
                        BatchJobPhase.LEGACY
                        if component_only_retry
                        else
                        BatchJobPhase.READY_FOR_BUILD
                        if reusable_checkpoint
                        else BatchJobPhase.REMOTE_QUEUED
                        if staged_model
                        else BatchJobPhase.LEGACY
                    ),
                    payload=payload,
                    retry_of_batch_job_id=source_job.id if is_retry else None,
                    logical_job_id=source_job.logical_job_id,
                    collection_checkpoint_path=(
                        reusable_checkpoint
                    ),
                    vm_export_uuid=(
                        retry_vm_export_uuid if is_retry else None
                    ),
                    vm_resume_manifest_path=(
                        retry_vm_resume_manifest if is_retry else None
                    ),
                    remote_export_started_at=(
                        retry_remote_started_at if is_retry else None
                    ),
                    remote_status_at=(
                        None
                        if is_retry and has_unpersisted_replacement
                        else source_job.remote_status_at if is_retry else None
                    ),
                    remote_progress_at=(
                        None
                        if is_retry and has_unpersisted_replacement
                        else source_job.remote_progress_at if is_retry else None
                    ),
                    control_file=control_file,
                    created_at=created_at,
                )
            )
        try:
            self.repository.create_batch(derived, tuple(jobs))
        except ValueError as exc:
            raise BatchClientConflictError(
                tuple(job.client_id for job in jobs)
            ) from exc
        self.repository.append_event(
            WebBatchEvent(
                batch_id=batch_id,
                event_type="BATCH_DERIVED",
                payload={
                    "source_batch_id": str(source.id),
                    "kind": request.kind.value,
                    "job_count": len(jobs),
                    "reason": str(request.reason or "")[:500],
                },
                actor=str(request.actor or "")[:200] or None,
                idempotency_key=f"event:{batch_key}",
            )
        )
        self._dispatcher.wake()
        return self.batch_snapshot(batch_id)

    def request_action(
        self,
        batch_id: UUID | str,
        action: BatchAction,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatch:
        normalized_id = (
            batch_id if isinstance(batch_id, UUID) else UUID(str(batch_id))
        )
        updated = self.repository.request_action(
            normalized_id,
            action,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        if action is BatchAction.STOP:
            for job in self.repository.list_batch_jobs(normalized_id):
                if (
                    job.status is not BatchJobStatus.INTERRUPT_REQUESTED
                    or not job.control_file
                ):
                    continue
                try:
                    FileExecutionControl(job.control_file).request_stop(
                        reason="Solicitacao local do usuario para parar o lote."
                    )
                except Exception as exc:
                    self.repository.append_event(
                        WebBatchEvent(
                            batch_id=normalized_id,
                            job_id=job.id,
                            event_type="JOB_CONTROL_WRITE_FAILED",
                            payload={"message": str(exc)[:500]},
                        )
                    )
                    raise RuntimeError(
                        "A parada foi registrada, mas o sinal local nao pôde "
                        "ser gravado."
                    ) from exc
        elif action is BatchAction.RESUME:
            self._dispatcher.wake()
        return updated

    def request_job_stop(
        self,
        job_id: UUID | str,
        *,
        actor: str | None = None,
        reason: str | None = None,
        idempotency_key: str | None = None,
    ) -> WebBatchJob:
        normalized_id = (
            job_id if isinstance(job_id, UUID) else UUID(str(job_id))
        )
        updated = self.repository.request_job_stop(
            normalized_id,
            actor=actor,
            reason=reason,
            idempotency_key=idempotency_key,
        )
        if (
            updated.status is BatchJobStatus.INTERRUPT_REQUESTED
            and updated.control_file
        ):
            try:
                FileExecutionControl(updated.control_file).request_stop(
                    reason="Solicitacao local do usuario para parar este cliente."
                )
            except Exception as exc:
                self.repository.append_event(
                    WebBatchEvent(
                        batch_id=updated.batch_id,
                        job_id=updated.id,
                        event_type="JOB_CONTROL_WRITE_FAILED",
                        payload={"message": str(exc)[:500]},
                    )
                )
                raise RuntimeError(
                    "A parada foi registrada, mas o sinal local nao pôde "
                    "ser gravado."
                ) from exc
        return updated

    def dashboard_snapshot(
        self,
        *,
        job_batch_limit: int = 500,
        summary_batch_limit: int = 50,
    ) -> DashboardQueueSnapshot:
        normalized_job_limit = max(1, min(int(job_batch_limit), 500))
        normalized_summary_limit = max(0, min(int(summary_batch_limit), 500))
        batches = self.repository.list_batches(limit=normalized_job_limit)
        batch_ids = tuple(batch.id for batch in batches)
        jobs_by_batch = self.repository.list_batch_jobs_for_batches(batch_ids)
        events_by_batch = self.repository.list_events_for_batches(batch_ids)

        summaries: list[dict[str, Any]] = []
        capacities = self.capacity_snapshot()
        remote_capacity = sum(
            int(pool.get("workers") or 0)
            for pool in capacities
            if (
                BatchJobPhase.REMOTE_QUEUED.value
                in tuple(pool.get("phases") or ())
                or "REMOTE_COMPONENT" in tuple(pool.get("phases") or ())
            )
        )
        build_capacity = sum(
            int(pool.get("workers") or 0)
            for pool in capacities
            if BatchJobPhase.READY_FOR_BUILD.value in tuple(pool.get("phases") or ())
        )
        terminal_jobs = {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.PARTIALLY_COMPLETE,
            BatchJobStatus.FAILED,
            BatchJobStatus.INTERRUPTED,
            BatchJobStatus.CANCELLED_BY_USER,
        }
        for batch in batches[:normalized_summary_limit]:
            jobs = jobs_by_batch[batch.id]
            retryability = tuple(_batch_job_retryability(job) for job in jobs)
            counts = {
                status: sum(job.status is status for job in jobs)
                for status in BatchJobStatus
            }
            phase_counts = {
                phase.value: sum(job.phase is phase for job in jobs)
                for phase in BatchJobPhase
            }
            finished = sum(job.status in terminal_jobs for job in jobs)
            current = next(
                (
                    job
                    for job in jobs
                    if job.status
                    in {
                        BatchJobStatus.RUNNING,
                        BatchJobStatus.WAITING_WAS_DECISION,
                        BatchJobStatus.INTERRUPT_REQUESTED,
                    }
                ),
                None,
            )
            requests = list(batch.options.get("requests") or ())
            first_request = (
                dict(requests[0].get("request") or {})
                if requests and isinstance(requests[0], Mapping)
                else {}
            )
            summaries.append(
                {
                    "id": str(batch.id),
                    "kind": batch.kind,
                    "status": batch.status.value,
                    "requested_action": (
                        batch.requested_action.value
                        if batch.requested_action is not None
                        else None
                    ),
                    "source_batch_id": (
                        str(batch.source_batch_id)
                        if batch.source_batch_id is not None
                        else None
                    ),
                    "created_at": batch.created_at,
                    "started_at": batch.started_at,
                    "ended_at": batch.ended_at,
                    "total_count": len(jobs),
                    "completed_count": (
                        counts[BatchJobStatus.COMPLETE]
                        + counts[BatchJobStatus.COMPLETE_WITH_WARNINGS]
                    ),
                    "warning_count": counts[
                        BatchJobStatus.COMPLETE_WITH_WARNINGS
                    ],
                    "partial_count": counts[
                        BatchJobStatus.PARTIALLY_COMPLETE
                    ],
                    "failed_count": counts[BatchJobStatus.FAILED],
                    "interrupted_count": counts[BatchJobStatus.INTERRUPTED],
                    "cancelled_count": counts[
                        BatchJobStatus.CANCELLED_BY_USER
                    ],
                    "queued_count": counts[BatchJobStatus.QUEUED],
                    "retryable_count": sum(
                        decision.retryable for decision in retryability
                    ),
                    "non_retryable_count": sum(
                        decision.candidate and not decision.retryable
                        for decision in retryability
                    ),
                    "progress_percent": (
                        round(100 * finished / len(jobs)) if jobs else 100
                    ),
                    "current_client_id": (
                        current.client_id if current is not None else None
                    ),
                    "current_phase": (
                        current.phase.value if current is not None else None
                    ),
                    "phase_counts": phase_counts,
                    "remote_concurrency": {
                        "active": phase_counts[BatchJobPhase.REMOTE_RUNNING.value],
                        "capacity": remote_capacity,
                    },
                    "build_queue_count": phase_counts[
                        BatchJobPhase.READY_FOR_BUILD.value
                    ],
                    "build_concurrency": {
                        "active": phase_counts[BatchJobPhase.BUILD_RUNNING.value],
                        "capacity": build_capacity,
                    },
                    "mode": str(first_request.get("mode") or ""),
                    "days": first_request.get("days"),
                    "start_at": first_request.get("start_at"),
                    "end_at": first_request.get("end_at"),
                }
            )
        rows: list[dict[str, Any]] = []
        queued_ids: list[str] = []
        active_statuses = {
            BatchJobStatus.QUEUED,
            BatchJobStatus.RUNNING,
            BatchJobStatus.WAITING_WAS_DECISION,
            BatchJobStatus.INTERRUPT_REQUESTED,
        }
        active_job_count = 0
        for batch in batches:
            events_by_job: dict[UUID, list[WebBatchEvent]] = {}
            for event in events_by_batch[batch.id]:
                if event.job_id is not None:
                    events_by_job.setdefault(event.job_id, []).append(event)
            for job in jobs_by_batch[batch.id]:
                retryability = _batch_job_retryability(job)
                if job.status in active_statuses:
                    active_job_count += 1
                row = _safe_dashboard_value(job.payload)
                row.update(
                    {
                        "job_id": job.id.hex,
                        "batch_id": str(batch.id),
                        "batch_status": batch.status.value,
                        "batch_kind": batch.kind,
                        "batch_requested_action": (
                            batch.requested_action.value
                            if batch.requested_action is not None
                            else None
                        ),
                        "client_id": job.client_id,
                        "status": job.status.value,
                        "phase": job.phase.value,
                        "checkpoint_ready": bool(job.collection_checkpoint_path),
                        "created_at": job.created_at,
                        "started_at": job.started_at,
                        "remote_started_at": job.remote_started_at,
                        "remote_ended_at": job.remote_ended_at,
                        "build_started_at": job.build_started_at,
                        "ended_at": job.ended_at,
                        "error": job.error_message,
                        "retryable": (
                            retryability.retryable
                            if retryability.candidate
                            else None
                        ),
                        "recorded_error_code": (
                            retryability.recorded_error_code
                        ),
                        "effective_error_code": (
                            retryability.effective_error_code
                        ),
                        "retryability_reason": retryability.reason,
                        "run_id": job.run_id or row.get("run_id"),
                        "queue_position": None,
                    }
                )
                for event in events_by_job.get(job.id, ()):
                    if event.event_type == "JOB_PROGRESS":
                        _apply_progress(row, event.payload)
                if job.status is BatchJobStatus.QUEUED:
                    queued_ids.append(job.id.hex)
                rows.append(row)
        positions = {
            job_id: position for position, job_id in enumerate(queued_ids, start=1)
        }
        for row in rows:
            row["queue_position"] = positions.get(str(row["job_id"]))
        ordered_rows = tuple(
            sorted(
                rows,
                key=lambda row: str(row.get("created_at") or ""),
                reverse=True,
            )
        )
        return DashboardQueueSnapshot(
            jobs=ordered_rows,
            batches=tuple(summaries),
            active_job_count=active_job_count,
        )

    def batches_snapshot(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return list(
            self.dashboard_snapshot(
                job_batch_limit=limit,
                summary_batch_limit=limit,
            ).batches
        )

    def batch_snapshot(self, batch_id: UUID | str) -> dict[str, Any]:
        normalized_id = (
            batch_id if isinstance(batch_id, UUID) else UUID(str(batch_id))
        )
        batch = self.repository.get_batch(normalized_id)
        if batch is None:
            raise KeyError("Lote nao encontrado.")
        events = self.repository.list_events(normalized_id)
        was_by_job: dict[UUID, dict[str, Any]] = {}
        vm_by_job: dict[UUID, dict[str, Any]] = {}
        for event in events:
            if event.job_id is None or event.event_type != "JOB_PROGRESS":
                continue
            payload = event.payload
            source = str(payload.get("source") or "")
            if source == "tenable_vm_vulnerabilities":
                vm_by_job[event.job_id] = {
                    "export_uuid": str(payload.get("export_uuid") or "") or None,
                    "origin": str(payload.get("origin") or "") or None,
                    "status": str(payload.get("status") or "").upper() or None,
                    "completed_chunks": int(payload.get("completed_chunks") or 0),
                    "total_chunks": int(payload.get("total_chunks") or 0),
                    "persisted_chunks": len(payload.get("persisted_chunks") or ()),
                    "observed_at": event.created_at,
                }
                continue
            if source != "tenable_was_findings":
                continue
            status = str(payload.get("status") or "").upper()
            summary = was_by_job.setdefault(
                event.job_id,
                {"attempts": 0, "outcome": None},
            )
            if status == "STARTED":
                summary["attempts"] += 1
            if status:
                summary["outcome"] = status
        return {
            "batch": {
                "id": str(batch.id),
                "kind": batch.kind,
                "status": batch.status.value,
                "requested_action": (
                    batch.requested_action.value
                    if batch.requested_action is not None
                    else None
                ),
                "source_batch_id": (
                    str(batch.source_batch_id)
                    if batch.source_batch_id is not None
                    else None
                ),
                "version": batch.version,
                "created_at": batch.created_at,
                "started_at": batch.started_at,
                "ended_at": batch.ended_at,
            },
            "jobs": [
                {
                    "id": str(job.id),
                    "client_id": job.client_id,
                    "position": job.position,
                    "status": job.status.value,
                    "phase": job.phase.value,
                    "checkpoint_ready": bool(job.collection_checkpoint_path),
                    "attempt_number": job.attempt_number,
                    "run_id": job.run_id,
                    "exit_code": job.exit_code,
                    "error_code": retryability.effective_error_code,
                    "recorded_error_code": retryability.recorded_error_code,
                    "effective_error_code": retryability.effective_error_code,
                    "error_message": job.error_message,
                    "retryable": (
                        retryability.retryable
                        if retryability.candidate
                        else None
                    ),
                    "retryability_reason": retryability.reason,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "remote_started_at": job.remote_started_at,
                    "remote_ended_at": job.remote_ended_at,
                    "build_started_at": job.build_started_at,
                    "ended_at": job.ended_at,
                    "vm_export_uuid": job.vm_export_uuid,
                    "remote_export_started_at": job.remote_export_started_at,
                    "remote_status_at": job.remote_status_at,
                    "remote_progress_at": job.remote_progress_at,
                    "vm_export": vm_by_job.get(job.id),
                    "was_attempts": int(
                        was_by_job.get(job.id, {}).get("attempts") or 0
                    ),
                    "was_retry_performed": int(
                        was_by_job.get(job.id, {}).get("attempts") or 0
                    ) > 1,
                    "was_retry_outcome": was_by_job.get(job.id, {}).get("outcome"),
                }
                for job in self.repository.list_batch_jobs(normalized_id)
                for retryability in (_batch_job_retryability(job),)
            ],
            "events": [
                {
                    "job_id": str(event.job_id) if event.job_id else None,
                    "event_type": event.event_type,
                    "payload": _safe_dashboard_value(event.payload),
                    "created_at": event.created_at,
                }
                for event in events
            ],
        }

    def snapshot(self) -> list[dict[str, Any]]:
        return list(self.dashboard_snapshot().jobs)

    def wait_until_idle(self, *, timeout: float) -> bool:
        if self._component_pool is None:
            return self._dispatcher.wait_until_idle(timeout=timeout)
        import time

        deadline = time.monotonic() + max(0.0, float(timeout))
        if not self._dispatcher.wait_until_idle(
            timeout=max(0.0, deadline - time.monotonic())
        ):
            return False
        if not self._component_pool.wait_until_idle(
            timeout=max(0.0, deadline - time.monotonic())
        ):
            return False
        return self._dispatcher.wait_until_idle(
            timeout=max(0.0, deadline - time.monotonic())
        )

    def capacity_snapshot(self) -> tuple[dict[str, Any], ...]:
        capacities = self._dispatcher.capacity_snapshot()
        if self._component_pool is None:
            return capacities
        return (
            *tuple(
                item
                for item in capacities
                if tuple(item.get("phases") or ())
                != (BatchJobPhase.REMOTE_QUEUED.value,)
            ),
            self._component_pool.capacity_snapshot(),
        )

    def close(self) -> None:
        errors: list[RuntimeError] = []
        if self._component_pool is not None:
            try:
                self._component_pool.close()
            except RuntimeError as exc:
                errors.append(exc)
        try:
            self._dispatcher.close()
        except RuntimeError as exc:
            errors.append(exc)
        if errors:
            raise errors[0]

    def _drain_executor_pending(self, count: int) -> None:
        for _ in range(count):
            self.executor._pending.get_nowait()
            self.executor._pending.task_done()

    def _persist_progress(
        self,
        job_id: str,
        event: Mapping[str, Any],
    ) -> None:
        with self._active_lock:
            active = self._active_jobs.get(str(job_id))
            active_component = self._active_components.get(str(job_id))
        if active is None:
            return
        if active_component is not None:
            self._persist_component_progress(active_component, event)
        should_append = True
        if (
            str(event.get("event") or "")
            == "TENABLE_EXPORT_RECOVERY_UNAVAILABLE"
            and str(event.get("source") or "")
            == "tenable_vm_vulnerabilities"
            and event.get("replacement_started") is True
            and str(event.get("previous_export_uuid") or "").strip()
            and str(event.get("replacement_export_uuid") or "").strip()
            and str(active.vm_export_uuid or "")
            == str(event.get("previous_export_uuid") or "")
        ):
            observed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            updated = self.repository.record_vm_export_replacement(
                active.id,
                previous_export_uuid=str(event["previous_export_uuid"]),
                replacement_export_uuid=str(event["replacement_export_uuid"]),
                resume_manifest_path=(
                    str(event.get("partial_manifest"))
                    if event.get("partial_manifest")
                    else None
                ),
                origin=str(event.get("replacement_origin") or "") or None,
                observed_at=observed_at,
            )
            with self._active_lock:
                self._active_jobs[str(job_id)] = updated
        if (
            str(event.get("event") or "") == "TENABLE_EXPORT_PROGRESS"
            and str(event.get("source") or "") == "tenable_vm_vulnerabilities"
            and str(event.get("export_uuid") or "").strip()
        ):
            observed = datetime.now(UTC)
            if event.get("status_query_ok") is False:
                error_key = f"{job_id}:status-query-error"
                error_fingerprint = (
                    "STATUS_QUERY_ERROR",
                    str(event.get("status_query_error") or ""),
                )
                previous_error = self._progress_events.get(error_key)
                should_append = bool(
                    previous_error is None
                    or previous_error[0] != error_fingerprint
                    or (observed - previous_error[1]).total_seconds() >= 300
                )
                if should_append:
                    self._progress_events[error_key] = (
                        error_fingerprint,
                        observed,
                    )
                observed_at = observed.isoformat().replace("+00:00", "Z")
                updated = self.repository.record_vm_export_progress(
                    active.id,
                    export_uuid=str(event["export_uuid"]),
                    resume_manifest_path=(
                        str(event.get("partial_manifest"))
                        if event.get("partial_manifest")
                        else None
                    ),
                    origin=str(event.get("origin") or "") or None,
                    remote_status=str(event.get("status") or "STARTED").upper(),
                    observed_at=observed_at,
                    progress_at=None,
                    completed_chunks=int(event.get("completed_chunks") or 0),
                    total_chunks=int(event.get("total_chunks") or 0),
                    persisted_chunks=tuple(
                        int(item) for item in (event.get("persisted_chunks") or ())
                    ),
                    status_confirmed=False,
                )
                with self._active_lock:
                    self._active_jobs[str(job_id)] = updated
            else:
                persisted = tuple(
                    sorted(int(item) for item in (event.get("persisted_chunks") or ()))
                )
                fingerprint = (
                    str(event.get("status") or "").upper(),
                    int(event.get("completed_chunks") or 0),
                    int(event.get("total_chunks") or 0),
                    persisted,
                )
                previous = self._progress_events.get(str(job_id))
                progressed = previous is None or previous[0] != fingerprint
                observed_at = observed.isoformat().replace("+00:00", "Z")
                updated = self.repository.record_vm_export_progress(
                    active.id,
                    export_uuid=str(event["export_uuid"]),
                    resume_manifest_path=(
                        str(event.get("partial_manifest"))
                        if event.get("partial_manifest")
                        else None
                    ),
                    origin=str(event.get("origin") or "") or None,
                    remote_status=str(event.get("status") or "UNKNOWN").upper(),
                    observed_at=observed_at,
                    progress_at=observed_at if progressed else None,
                    completed_chunks=int(event.get("completed_chunks") or 0),
                    total_chunks=int(event.get("total_chunks") or 0),
                    persisted_chunks=persisted,
                    status_confirmed=True,
                )
                with self._active_lock:
                    self._active_jobs[str(job_id)] = updated
                should_append = bool(
                    progressed
                    or previous is None
                    or (observed - previous[1]).total_seconds() >= 300
                )
                if should_append:
                    self._progress_events[str(job_id)] = (fingerprint, observed)
        if not should_append:
            return
        self.repository.append_event(
            WebBatchEvent(
                batch_id=active.batch_id,
                job_id=active.id,
                event_type="JOB_PROGRESS",
                payload=dict(event),
            )
        )

    def _persist_component_progress(
        self,
        component: RemoteComponentWindow,
        event: Mapping[str, Any],
    ) -> None:
        repository = self._remote_component_repository
        if repository is None:
            return
        event_type = str(event.get("event") or "")
        source = str(event.get("source") or "")
        if event_type in {
            "TENABLE_EXPORT_RECOVERY_UNAVAILABLE",
            "TENABLE_CLOUD_RECOVERY_UNAVAILABLE",
        }:
            current = repository.get(component.id)
            if current is None:
                return
            changes: dict[str, Any] = {
                "identifier_kind": None,
                "remote_identifier": None,
                "identifier_origin": "replacement_required",
            }
            if current.window_number == 2:
                changes["replacement_created_in_window_2"] = True
            elif current.window_number == 3:
                changes["replacement_created_in_window_3"] = True
            repository.transition(
                current.id,
                expected_state=current.state,
                requested_state=current.state,
                **changes,
            )
            return
        expected_source = {
            ReportComponent.VM_CORE: "tenable_vm_vulnerabilities",
            ReportComponent.WAS: "tenable_was_findings",
            ReportComponent.CLOUD: "tenable_cloud_security",
        }[component.component]
        if event_type == "TENABLE_EXPORT_PROGRESS" and source != expected_source:
            return
        if event_type == "TENABLE_CLOUD_PROGRESS" and component.component is not ReportComponent.CLOUD:
            return
        if event_type not in {"TENABLE_EXPORT_PROGRESS", "TENABLE_CLOUD_PROGRESS"}:
            return
        completed = int(
            event.get("completed_chunks")
            if event_type == "TENABLE_EXPORT_PROGRESS"
            else event.get("current")
            or 0
        )
        raw_total = (
            event.get("total_chunks")
            if event_type == "TENABLE_EXPORT_PROGRESS"
            else event.get("total")
        )
        total = int(raw_total) if raw_total is not None else None
        status = str(event.get("status") or "PROCESSING").upper()
        observation_kind = (
            RemoteObservationKind.COMPLETE
            if status in {"COMPLETE", "FINISHED", "REPLAYED"}
            else RemoteObservationKind.PROCESSING
        )
        observed = repository.record_observation(
            component.id,
            RemoteObservation(
                kind=observation_kind,
                completed_units=completed,
                total_units=total,
                remote_status=status,
            ),
        )
        identifier = str(
            event.get("export_uuid") or event.get("snapshot_id") or ""
        ).strip()
        if identifier:
            repository.transition(
                component.id,
                expected_state=observed.state,
                requested_state=observed.state,
                identifier_kind=(
                    RemoteIdentifierKind.UUID
                    if event.get("export_uuid")
                    else RemoteIdentifierKind.DATASET
                ),
                remote_identifier=identifier,
                identifier_origin=str(event.get("origin") or "created"),
            )

    def _persist_process(self, job_id: str, process_id: int) -> None:
        with self._active_lock:
            active = self._active_jobs.get(str(job_id))
        if active is None:
            return
        self.repository.record_job_process(
            active.id,
            process_id,
            control_file=active.control_file,
        )

    def _persist_fallback(self, job_id: str, process_id: int) -> None:
        with self._active_lock:
            active = self._active_jobs.get(str(job_id))
        if active is None:
            return
        self.repository.append_event(
            WebBatchEvent(
                batch_id=active.batch_id,
                job_id=active.id,
                event_type="JOB_LOCAL_FALLBACK_TERMINATION",
                payload={"process_id": int(process_id)},
            )
        )

    def _run_job(self, job: WebBatchJob) -> BatchJobResult:
        return self._run_executor_job(job)

    def _checkpoint_file(self, job: WebBatchJob) -> str:
        mode = str(job.payload.get("mode") or "manual")
        scope = "automatic-monthly" if mode == "automatic" else "manual"
        return str(
            (
                self._staged_output_root
                / scope
                / "orchestration"
                / "checkpoints"
                / str(job.batch_id)
                / f"{job.id.hex}.json"
            ).resolve()
        )

    def _component_run_id(self, job: WebBatchJob) -> str:
        return str(
            job.run_id
            or job.payload.get("run_id")
            or f"{job.id.hex}-{job.client_id}"
        )

    def _component_request(
        self,
        job: WebBatchJob,
        *,
        component: ReportComponent,
    ) -> RemoteCollectionRequest:
        run_id = self._component_run_id(job)
        checkpoint_path = Path(self._checkpoint_file(job)).resolve()
        period = {
            "start_at": job.payload.get("start_at"),
            "end_at": job.payload.get("end_at"),
        }
        if not all(period.values()):
            # The CLI resolves relative/manual and automatic periods. The
            # component command owns the authoritative values; this request is
            # used only to derive the isolated path before it runs.
            period = {
                "start_at": "1970-01-01T00:00:00Z",
                "end_at": "1970-01-02T00:00:00Z",
            }
        return RemoteCollectionRequest(
            storage_root=self._staged_output_root,
            checkpoint_path=checkpoint_path,
            client_id=job.client_id,
            tenant_id=job.client_id,
            run_id=run_id,
            logical_job_id=str(job.logical_job_id or job.id.hex),
            execution_type=(
                "AUTOMATIC_MONTHLY"
                if str(job.payload.get("mode") or "manual") == "automatic"
                else "MANUAL"
            ),
            mode=str(job.payload.get("mode") or "manual"),
            origin=(
                "SCHEDULED"
                if str(job.payload.get("mode") or "manual") == "automatic"
                else "MANUAL"
            ),
            attempt_number=job.attempt_number,
            period=period,
        )

    def _initialize_remote_components(self, job: WebBatchJob) -> BatchJobResult:
        if self._remote_component_repository is None:
            raise RuntimeError("Repositório de componentes remotos ausente.")
        deadline = datetime.now(UTC) + timedelta(
            seconds=self._remote_processing_timeout_seconds
        )
        self._remote_component_repository.create_for_job(
            batch_job_id=job.id,
            components=tuple(ReportComponent),
            window_number=1,
            deadline_at=deadline,
            origin=(
                "SCHEDULED"
                if str(job.payload.get("mode") or "manual") == "automatic"
                else "MANUAL"
            ),
            attempt_number=1,
        )
        return BatchJobResult(
            status=BatchJobStatus.COMPLETE,
            payload={"remote_components_initialized": True},
        )

    def _handle_component_initializer_result(
        self,
        job: WebBatchJob,
        result: BatchJobResult,
    ) -> None:
        if result.status is BatchJobStatus.COMPLETE:
            if self._component_pool is not None:
                self._component_pool.wake()
            return
        self.repository.complete_job(job.id, result)

    def _append_component_event(
        self,
        job: WebBatchJob,
        component: RemoteComponentWindow,
        *,
        event_type: str,
        decision: str,
    ) -> None:
        self.repository.append_event(
            WebBatchEvent(
                batch_id=job.batch_id,
                job_id=job.id,
                event_type=event_type,
                payload={
                    "component": component.component.value,
                    "window_number": component.window_number,
                    "attempt_number": component.attempt_number,
                    "origin": component.origin,
                    "deadline_at": component.deadline_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "remote_identifier": component.remote_identifier,
                    "completed_units": component.completed_units,
                    "total_units": component.total_units,
                    "decision": decision,
                },
            )
        )

    def _create_recovery_component(
        self,
        job: WebBatchJob,
        current: RemoteComponentWindow,
        *,
        window_number: int,
        reuse_identifier: bool,
        replacement_created_in_window_2: bool,
        replacement_created_in_window_3: bool,
    ) -> RemoteComponentWindow:
        repository = self._remote_component_repository
        if repository is None:
            raise RuntimeError("Repositório de componentes remotos ausente.")
        rows = repository.list_for_jobs((job.id,)).get(job.id, ())
        attempt_number = 1 + max(
            (
                row.attempt_number
                for row in rows
                if row.component is current.component
            ),
            default=0,
        )
        created = repository.create_for_job(
            batch_job_id=job.id,
            components=(current.component,),
            window_number=window_number,
            deadline_at=datetime.now(UTC)
            + timedelta(seconds=self._remote_processing_timeout_seconds),
            origin="AUTOMATIC_RETRY",
            query_fingerprints=(
                {current.component: current.query_fingerprint}
                if current.query_fingerprint
                else None
            ),
            attempt_number=attempt_number,
            parent_component_id=current.id,
            replacement_created_in_window_2=replacement_created_in_window_2,
            replacement_created_in_window_3=replacement_created_in_window_3,
        )[0]
        changes: dict[str, Any] = {}
        if reuse_identifier and current.remote_identifier is not None:
            changes.update(
                identifier_kind=current.identifier_kind,
                remote_identifier=current.remote_identifier,
                identifier_origin="provided",
                checkpoint_path=current.checkpoint_path,
            )
        if changes:
            created = repository.transition(
                created.id,
                expected_state=RemoteComponentState.PENDING,
                requested_state=RemoteComponentState.PENDING,
                **changes,
            )
        self._append_component_event(
            job,
            created,
            event_type="REMOTE_COMPONENT_WINDOW_CREATED",
            decision=("REUSE_IDENTIFIER" if reuse_identifier else "CREATE_REPLACEMENT"),
        )
        if self._component_pool is not None:
            self._component_pool.wake()
        return created

    def _handle_failed_remote_component(
        self,
        job: WebBatchJob,
        component: RemoteComponentWindow,
        result: BatchJobResult,
    ) -> None:
        repository = self._remote_component_repository
        if repository is None:
            raise RuntimeError("Repositório de componentes remotos ausente.")
        failure = classify_failure(
            {
                **dict(result.payload),
                "error_code": result.error_code,
                "message": result.error_message,
            }
        )
        failure_code = str(result.error_code or failure.code.value).upper()
        error_text = str(result.error_message or "").casefold()
        if (
            failure_code in _INVALID_REMOTE_IDENTIFIER_CODES
            or "estado cancelled" in error_text
            or "status 404" in error_text
        ):
            observation_kind = RemoteObservationKind.INVALID_IDENTIFIER
        elif not failure.retryable and not bool(result.payload.get("retryable")):
            observation_kind = RemoteObservationKind.NON_RETRYABLE_FAILURE
        else:
            observation_kind = RemoteObservationKind.TERMINAL_RETRYABLE_FAILURE
        observed = repository.record_observation(
            component.id,
            RemoteObservation(
                kind=observation_kind,
                completed_units=component.completed_units,
                total_units=component.total_units,
                remote_status="FAILED",
                failure_code=failure_code,
            ),
        )
        decision = decide_recovery(
            observed,
            RemoteObservation(
                kind=observation_kind,
                completed_units=observed.completed_units,
                total_units=observed.total_units,
                remote_status=observed.last_remote_status,
                failure_code=failure_code,
            ),
            now=datetime.now(UTC),
            policy=AutomaticRecoveryPolicy(),
        )
        if decision.action is RecoveryAction.FAIL_NON_RETRYABLE:
            terminal_state = RemoteComponentState.NON_RETRYABLE_FAILURE
            terminal_code = failure_code
            terminal_retryable = False
        elif decision.action is RecoveryAction.WAIT_MANUAL_RETRY:
            terminal_state = RemoteComponentState.WAITING_MANUAL_RETRY
            terminal_code = decision.failure_code or failure_code
            terminal_retryable = True
        else:
            terminal_state = RemoteComponentState.WAITING_MANUAL_RETRY
            terminal_code = "AUTOMATIC_WINDOW_ADVANCED"
            terminal_retryable = True
        terminal = repository.transition(
            observed.id,
            expected_state=observed.state,
            requested_state=terminal_state,
            worker_id=None,
            lease_expires_at=None,
            failure_code=terminal_code,
            failure_message=failure.message[:500],
            retryable=terminal_retryable,
            ended_at=datetime.now(UTC),
        )
        self._append_component_event(
            job,
            terminal,
            event_type="REMOTE_COMPONENT_RECOVERY_DECIDED",
            decision=decision.action.value,
        )
        if decision.action is RecoveryAction.START_NEXT_WINDOW:
            next_window = int(decision.next_window or component.window_number + 1)
            missing_identifier = component.remote_identifier is None
            self._create_recovery_component(
                job,
                terminal,
                window_number=next_window,
                reuse_identifier=not missing_identifier,
                replacement_created_in_window_2=(
                    component.replacement_created_in_window_2
                    or (next_window == 2 and missing_identifier)
                ),
                replacement_created_in_window_3=(
                    component.replacement_created_in_window_3
                ),
            )
            return
        if decision.action is RecoveryAction.CREATE_REPLACEMENT:
            self._create_recovery_component(
                job,
                terminal,
                window_number=component.window_number,
                reuse_identifier=False,
                replacement_created_in_window_2=(
                    component.replacement_created_in_window_2
                    or decision.mark_replacement_in_window_two
                ),
                replacement_created_in_window_3=(
                    component.replacement_created_in_window_3
                    or decision.mark_replacement_in_window_three
                ),
            )
            return
        self._finalize_remote_components(job)

    def _run_remote_component(self, component: RemoteComponentWindow) -> None:
        if self._remote_component_repository is None:
            raise RuntimeError("Repositório de componentes remotos ausente.")
        job = self.repository.get_job(component.batch_job_id)
        if job is None:
            raise RuntimeError("Trabalho pai do componente não foi encontrado.")
        request = self._component_request(job, component=component.component)
        checkpoint = component_checkpoint_path(request, component.component)
        executor_id = component.id.hex
        remaining_window_seconds = max(
            1,
            min(
                self._remote_processing_timeout_seconds,
                int((component.deadline_at - datetime.now(UTC)).total_seconds()),
            ),
        )
        with self._active_lock:
            self._active_components[executor_id] = component
        try:
            result = self._run_executor_job(
                job,
                operation="staged_component",
                executor_job_id=executor_id,
                payload_overrides={
                    "component": component.component.value,
                    "component_checkpoint": str(checkpoint),
                    "window_number": component.window_number,
                    "deadline_at": component.deadline_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "run_id": request.run_id,
                    "logical_job_id": request.logical_job_id,
                    "attempt_number": component.attempt_number,
                    "origin": component.origin,
                    "remote_identifier": component.remote_identifier,
                    "identifier_kind": (
                        component.identifier_kind.value
                        if component.identifier_kind is not None
                        else None
                    ),
                    "identifier_origin": component.identifier_origin,
                    "previous_component_checkpoint": component.checkpoint_path,
                    "remote_processing_timeout_seconds": remaining_window_seconds,
                },
            )
        finally:
            with self._active_lock:
                self._active_components.pop(executor_id, None)
        component = (
            self._remote_component_repository.get(component.id) or component
        )
        if result.status not in {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
        }:
            self._handle_failed_remote_component(job, component, result)
            return
        raw = result.payload.get("_component_result")
        if not isinstance(raw, Mapping) or not raw.get("checkpoint"):
            raise RuntimeError("Coleta do componente não retornou checkpoint.")
        persisted = load_component_checkpoint(
            str(raw["checkpoint"]),
            storage_root=self._staged_output_root,
        )
        if persisted.component is not component.component:
            raise RuntimeError("Checkpoint retornou componente incompatível.")
        if persisted.status not in _REMOTE_COMPONENT_PUBLISHABLE_STATES:
            self._handle_failed_remote_component(
                job,
                component,
                BatchJobResult(
                    status=BatchJobStatus.FAILED,
                    error_code=str(
                        persisted.metadata.get("failure_code")
                        or "COMPONENT_COLLECTION_FAILED"
                    ),
                    error_message=str(
                        persisted.metadata.get("failure_message")
                        or "Falha na coleta remota do componente."
                    ),
                    payload={
                        "retryable": bool(persisted.metadata.get("retryable")),
                    },
                ),
            )
            return
        self._remote_component_repository.transition(
            component.id,
            expected_state=component.state,
            requested_state=persisted.status,
            checkpoint_path=str(persisted.checkpoint_path),
            query_fingerprint=persisted.query_fingerprint,
            worker_id=None,
            lease_expires_at=None,
            ended_at=datetime.now(UTC),
        )
        self._finalize_remote_components(job)

    def _finalize_remote_components(self, job: WebBatchJob) -> None:
        if self._remote_component_repository is None:
            return
        with self._component_finalize_lock:
            grouped = self._remote_component_repository.list_for_jobs((job.id,))
            windows = grouped.get(job.id, ())
            latest: dict[ReportComponent, RemoteComponentWindow] = {}
            for window in windows:
                current = latest.get(window.component)
                if current is None or window.attempt_number > current.attempt_number:
                    latest[window.component] = window
            if set(latest) != set(ReportComponent):
                return
            if any(
                window.state not in _REMOTE_COMPONENT_TERMINAL_STATES
                for window in latest.values()
            ):
                return
            if any(
                window.state not in _REMOTE_COMPONENT_PUBLISHABLE_STATES
                for window in latest.values()
            ):
                return
            checkpoints = tuple(
                load_component_checkpoint(
                    str(latest[component].checkpoint_path),
                    storage_root=self._staged_output_root,
                )
                for component in ReportComponent
            )
            first = checkpoints[0]
            request = RemoteCollectionRequest(
                storage_root=self._staged_output_root,
                checkpoint_path=Path(self._checkpoint_file(job)).resolve(),
                client_id=first.client_id,
                tenant_id=first.tenant_id,
                run_id=first.run_id,
                logical_job_id=first.logical_job_id,
                execution_type=first.execution_type,
                mode=first.mode,
                origin=first.origin,
                attempt_number=first.attempt_number,
                period=dict(first.period),
            )
            merged = merge_component_checkpoints(
                request=request,
                checkpoints=checkpoints,
            )
            collect_client_remote(
                request,
                dependencies=RemoteCollectionDependencies(
                    collect=lambda _: merged
                ),
            )
            self.repository.advance_job_phase(
                job.id,
                expected_phase=BatchJobPhase.REMOTE_RUNNING,
                requested_phase=BatchJobPhase.READY_FOR_BUILD,
                collection_checkpoint_path=request.checkpoint_path,
            )
            self._dispatcher.wake()

    def _run_remote_job(self, job: WebBatchJob) -> BatchJobResult:
        checkpoint = self._checkpoint_file(job)
        result = self._run_executor_job(
            job,
            operation="staged_remote",
            checkpoint_path=checkpoint,
        )
        if result.status in {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
            BatchJobStatus.PARTIALLY_COMPLETE,
        }:
            return replace(
                result,
                payload={
                    **dict(result.payload),
                    "_collection_checkpoint_path": checkpoint,
                },
            )
        return result

    def _run_build_job(self, job: WebBatchJob) -> BatchJobResult:
        return self._run_executor_job(
            job,
            operation="staged_build",
            checkpoint_path=job.collection_checkpoint_path,
        )

    def _handle_remote_result(
        self,
        job: WebBatchJob,
        result: BatchJobResult,
    ) -> None:
        payload = {
            key: value
            for key, value in dict(result.payload).items()
            if key != "_collection_checkpoint_path"
        }
        if result.status in {
            BatchJobStatus.COMPLETE,
            BatchJobStatus.COMPLETE_WITH_WARNINGS,
        }:
            checkpoint = result.payload.get("_collection_checkpoint_path")
            self.repository.advance_job_phase(
                job.id,
                expected_phase=BatchJobPhase.REMOTE_RUNNING,
                requested_phase=BatchJobPhase.READY_FOR_BUILD,
                collection_checkpoint_path=checkpoint,
            )
            self._dispatcher.wake()
            return
        self.repository.complete_job(job.id, replace(result, payload=payload))

    def _run_executor_job(
        self,
        job: WebBatchJob,
        *,
        operation: str | None = None,
        checkpoint_path: str | None = None,
        executor_job_id: str | None = None,
        payload_overrides: Mapping[str, Any] | None = None,
    ) -> BatchJobResult:
        job_id = str(executor_job_id or job.id.hex)
        payload = dict(job.payload)
        payload.pop("vm_resume_budget_seconds", None)
        payload["remote_processing_timeout_seconds"] = (
            self._remote_processing_timeout_seconds
        )
        payload["job_id"] = job_id
        payload["status"] = "QUEUED"
        if operation is not None:
            payload["operation"] = operation
        if checkpoint_path is not None:
            payload["_collection_checkpoint_path"] = checkpoint_path
        payload["attempt_number"] = job.attempt_number
        payload["logical_job_id"] = job.logical_job_id or job.id.hex
        if job.vm_export_uuid:
            payload["vm_export_uuid"] = job.vm_export_uuid
        if job.vm_resume_manifest_path:
            payload["vm_resume_manifest"] = job.vm_resume_manifest_path
        if job.vm_export_uuid and job.remote_export_started_at:
            try:
                remote_started = datetime.fromisoformat(
                    job.remote_export_started_at.replace("Z", "+00:00")
                )
                elapsed = max(
                    0,
                    int((datetime.now(UTC) - remote_started).total_seconds()),
                )
                payload["vm_resume_budget_seconds"] = max(
                    1,
                    self._remote_processing_timeout_seconds - elapsed,
                )
            except (TypeError, ValueError):
                payload["vm_resume_budget_seconds"] = (
                    self._remote_processing_timeout_seconds
                )
        if payload_overrides:
            payload.update(dict(payload_overrides))
        with self.executor._lock:
            self.executor._jobs[job_id] = payload
        with self._active_lock:
            self._active_jobs[job_id] = job
        try:
            self.executor._run(job_id)
            with self.executor._lock:
                result = dict(self.executor._jobs[job_id])
        finally:
            with self._active_lock:
                self._active_jobs.pop(job_id, None)
            self._progress_events.pop(job_id, None)
            self._progress_events.pop(f"{job_id}:status-query-error", None)
        raw_status = str(result.get("status") or "FAILED").upper()
        if raw_status == "WAITING_WAS_DECISION":
            status = BatchJobStatus.WAITING_WAS_DECISION
        elif raw_status == "INTERRUPTED" or int(result.get("exit_code") or 0) == 130:
            status = BatchJobStatus.INTERRUPTED
        elif (
            raw_status in {"COMPLETE", "PARTIALLY_COMPLETE"}
            and str(result.get("component_set_status") or "").upper()
            == "PARTIAL_FAILURE"
        ):
            status = BatchJobStatus.PARTIALLY_COMPLETE
        elif raw_status == "PARTIALLY_COMPLETE":
            status = BatchJobStatus.PARTIALLY_COMPLETE
        elif raw_status == "COMPLETE" and result.get("warnings"):
            status = BatchJobStatus.COMPLETE_WITH_WARNINGS
        elif raw_status == "COMPLETE":
            status = BatchJobStatus.COMPLETE
        else:
            status = BatchJobStatus.FAILED
        failure = classify_failure(result) if status is BatchJobStatus.FAILED else None
        if failure is not None:
            result["error_code"] = failure.code.value
            result["retryable"] = failure.retryable
            result["error"] = failure.message
        return BatchJobResult(
            status=status,
            exit_code=(
                0
                if status in {
                    BatchJobStatus.COMPLETE,
                    BatchJobStatus.COMPLETE_WITH_WARNINGS,
                    BatchJobStatus.PARTIALLY_COMPLETE,
                }
                else 130
                if status is BatchJobStatus.INTERRUPTED
                else int(result.get("exit_code") or 1)
            ),
            error_code=(
                "INTERRUPTED_BY_USER"
                if status is BatchJobStatus.INTERRUPTED
                else failure.code.value
                if status is BatchJobStatus.FAILED
                else None
            ),
            error_message=(
                failure.message[:500]
                if status is BatchJobStatus.FAILED
                else None
            ),
            payload=result,
        )

    def _run_pool_job(
        self,
        job: WebBatchJob,
        runner: DurableRunner,
    ) -> BatchJobResult:
        job_id = job.id.hex
        with self._active_lock:
            self._active_jobs[job_id] = job
        try:
            return runner(job)
        finally:
            with self._active_lock:
                self._active_jobs.pop(job_id, None)


def _apply_progress(row: dict[str, Any], event: Mapping[str, Any]) -> None:
    event_type = str(event.get("event") or "")
    if event_type == "TENABLE_CLOUD_PROGRESS":
        row["cloud_progress"] = {
            key: event.get(key)
            for key in (
                "status",
                "stage",
                "source",
                "current",
                "total",
                "page",
                "records",
                "documents",
                "snapshot_id",
                "run_id",
            )
        }
        row["cloud_status"] = str(event.get("status") or "")
        return
    if event_type == "TENABLE_EXPORT_PROGRESS":
        progress_key = (
            "was_export_progress"
            if str(event.get("source") or "") == "tenable_was_findings"
            else "export_progress"
        )
        row[progress_key] = {
            key: event.get(key)
            for key in (
                "source",
                "export_uuid",
                "origin",
                "segment",
                "date_field",
                "status",
                "completed_chunks",
                "total_chunks",
                "elapsed_seconds",
                "processing_elapsed_seconds",
                "idle_seconds",
                "last_progress_elapsed_seconds",
                "no_progress_timeout_seconds",
                "stalled",
                "timeout_phase",
                "filters",
                "progress_made",
                "auto_cancelled",
                "cancellation_error",
            )
        }
        return
    if event_type == "TAG_REPORT_PROGRESS":
        row["tag_progress"] = {
            "current": int(event.get("current") or 0),
            "total": int(event.get("total") or 0),
            "tag_uuid": str(event.get("tag_uuid") or ""),
            "label": str(event.get("tag_label") or ""),
        }


__all__ = ["DurableDashboardJobQueue"]
