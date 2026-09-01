"""Coordinate the optional Tenable Cloud report component."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

from tenable_reports.application.cloud_report_dataset import (
    CLOUD_METRIC_DEFINITION_VERSION,
    build_cloud_dataset,
    load_cloud_report_dataset,
    write_cloud_report_dataset,
)
from tenable_reports.application.cloud_contract import probe_cloud_contract
from tenable_reports.application.cloud_enrichment import (
    CloudVulnerabilityEnrichment,
    correlate_cloud_enrichments,
)
from tenable_reports.application.collect_cloud import (
    CloudCollectionRequest,
    collect_cloud_snapshot,
)
from tenable_reports.application.normalize_cloud import normalize_cloud_artifact
from tenable_reports.application.cloud_snapshots import (
    CLOUD_NORMALIZER_VERSION,
    CLOUD_SNAPSHOT_SCHEMA_VERSION,
    CloudSnapshotCompatibility,
    CloudSnapshotRepository,
    build_cloud_snapshot,
    replay_cloud_snapshot,
)
from tenable_reports.config.environment import CloudCredentialConfig
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.cloud import NormalizedCloudSnapshot
from tenable_reports.domain.execution_control import ExecutionInterruptedError
from tenable_reports.domain.report_components import ComponentStage
from tenable_reports.domain.reporting import ReportingPeriod
from tenable_reports.infrastructure.tenable_cloud.client import (
    CloudAuthError,
    CloudGraphQLClient,
    CloudGraphQLConfig,
)
from tenable_reports.infrastructure.tenable_cloud.queries import (
    CLOUD_CONNECTOR_VERSION,
    CLOUD_SOURCE_QUERIES,
)
from tenable_reports.presentation.cloud_report_docx import generate_cloud_report
from tenable_reports.presentation.report_filenames import cloud_report_filename
from tenable_reports.application.publishing import sha256_file, validate_docx_package


class CloudExecutionStatus(StrEnum):
    DISABLED = "DISABLED"
    COMPLETE = "COMPLETE"
    REPLAYED = "REPLAYED"
    BLOCKED_RECENT_COLLECTION = "BLOCKED_RECENT_COLLECTION"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CloudLiveCollection:
    snapshot: NormalizedCloudSnapshot
    capabilities: Mapping[str, Any]
    enrichments: Sequence[CloudVulnerabilityEnrichment] = ()
    warnings: Sequence[Mapping[str, Any]] = ()
    snapshot_is_exact: bool = False
    connector_version: str = CLOUD_CONNECTOR_VERSION


@dataclass(frozen=True, slots=True)
class CloudResumeContext:
    stage: ComponentStage
    dataset_path: Path
    dataset_sha256: str
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    connector_version: str = CLOUD_CONNECTOR_VERSION

    def __post_init__(self) -> None:
        stage = ComponentStage(self.stage)
        if stage not in {
            ComponentStage.DATASET,
            ComponentStage.RENDER,
            ComponentStage.DOCUMENT_VALIDATION,
            ComponentStage.SNAPSHOT_PUBLICATION,
        }:
            raise ValueError("Etapa de retomada Cloud invalida.")
        digest = str(self.dataset_sha256).strip().lower()
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Hash do dataset Cloud invalido.")
        connector_version = str(self.connector_version).strip()
        if not connector_version:
            raise ValueError("connector_version Cloud obrigatorio.")
        object.__setattr__(self, "stage", stage)
        object.__setattr__(self, "dataset_path", Path(self.dataset_path))
        object.__setattr__(self, "dataset_sha256", digest)
        object.__setattr__(
            self,
            "capabilities",
            MappingProxyType(deepcopy(dict(self.capabilities))),
        )
        object.__setattr__(self, "connector_version", connector_version)


@dataclass(frozen=True, slots=True)
class CloudGeneratedDocument:
    path: Path
    variant: str


@dataclass(frozen=True, slots=True)
class CloudComponentResult:
    status: CloudExecutionStatus
    documents: tuple[CloudGeneratedDocument, ...] = ()
    dataset_path: Path | None = None
    snapshot_id: str | None = None
    warnings: tuple[Mapping[str, Any], ...] = ()
    cleanup_ready: bool = False
    failure_stage: ComponentStage | None = None
    failure_code: str | None = None
    retryable: bool = False


def _cloud_scope_hash(profile: ClientProfile) -> str:
    payload = {
        "environment": profile.cloud_security_scope.environment,
        "include_info_severity": profile.reporting.include_info_severity,
        "sources": sorted(CLOUD_SOURCE_QUERIES),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CloudExecutionRequest:
    profile: ClientProfile
    period: ReportingPeriod
    execution_type: str
    run_id: str
    attempt_number: int
    output_root: Path
    report_directory: Path
    template_path: Path
    force_refresh: bool = False
    bypass_recent_guard: bool = False
    recent_collection_hours: int = 24

    def compatibility(self) -> CloudSnapshotCompatibility:
        return CloudSnapshotCompatibility(
            client_id=self.profile.client_id,
            tenant_id=self.profile.tenant_id,
            execution_type=self.execution_type,
            period_mode=self.period.mode.value,
            timezone=self.period.timezone,
            scope_hash=_cloud_scope_hash(self.profile),
            metric_definition_version=CLOUD_METRIC_DEFINITION_VERSION,
            connector_version=CLOUD_CONNECTOR_VERSION,
            normalizer_version=CLOUD_NORMALIZER_VERSION,
            schema_version=CLOUD_SNAPSHOT_SCHEMA_VERSION,
        )


@dataclass(frozen=True, slots=True)
class CloudExecutionDependencies:
    repository: CloudSnapshotRepository
    collect_live: Callable[
        [CloudExecutionRequest, Callable[[Mapping[str, Any]], None]],
        CloudLiveCollection,
    ]
    build_dataset: Callable[..., Mapping[str, Any]] = build_cloud_dataset
    write_dataset: Callable[..., Any] = write_cloud_report_dataset
    render_report: Callable[..., Any] = generate_cloud_report
    validate_document: Callable[[str | Path], Mapping[str, Any]] = (
        validate_docx_package
    )
    load_dataset: Callable[[str | Path], Mapping[str, Any]] = (
        load_cloud_report_dataset
    )
    hash_file: Callable[[str | Path], str] = sha256_file
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    history_persistent: bool = True


@dataclass(frozen=True, slots=True)
class TenableCloudLiveCollector:
    credentials: CloudCredentialConfig
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    probe: Callable[..., Any] = probe_cloud_contract
    collect: Callable[..., Any] = collect_cloud_snapshot
    normalize: Callable[..., NormalizedCloudSnapshot] = normalize_cloud_artifact
    cancellation_probe: Callable[[], bool] | None = None

    def _client(self, endpoint: str) -> CloudGraphQLClient:
        return CloudGraphQLClient(
            CloudGraphQLConfig(
                endpoint=endpoint,
                api_secret=self.credentials.api_secret,
                timeout_seconds=self.credentials.timeout_seconds,
                retries=self.credentials.retries,
                ca_bundle=self.credentials.ca_bundle,
            )
        )

    def __call__(
        self,
        request: CloudExecutionRequest,
        progress_callback: Callable[[Mapping[str, Any]], None],
    ) -> CloudLiveCollection:
        if not self.credentials.is_complete:
            raise CloudAuthError(
                "Credencial Tenable Cloud Security nao configurada."
            )
        current = self.now()
        collected_at = _utc_iso(current)
        progress_callback(
            {
                "event": "TENABLE_CLOUD_PROGRESS",
                "client_id": request.profile.client_id,
                "run_id": request.run_id,
                "status": "STARTED",
                "stage": "CONTRACT_PROBE",
            }
        )
        capabilities = self.probe(
            request.profile.cloud_security_scope.environment,
            client_factory=self._client,
            now=lambda: current,
        )
        if not capabilities.required_ready:
            raise RuntimeError(
                "As fontes obrigatorias Cloud nao estao disponiveis."
            )
        client = self._client(capabilities.endpoint)
        execution_directory = (
            "automatic-monthly"
            if request.execution_type == "AUTOMATIC_MONTHLY"
            else "manual"
        )
        artifact = self.collect(
            request=CloudCollectionRequest(
                client_id=request.profile.client_id,
                tenant_id=request.profile.tenant_id,
                run_id=request.run_id,
                execution_type=execution_directory,
                output_root=request.output_root,
                collected_at=collected_at,
            ),
            clients={name: client for name in CLOUD_SOURCE_QUERIES},
            capabilities=capabilities,
            progress_callback=progress_callback,
            cancellation_probe=self.cancellation_probe,
        )
        normalized = self.normalize(artifact, collected_at=collected_at)
        end = request.period.end_at.astimezone(UTC)
        grace = timedelta(
            days=request.profile.reporting.late_collection_grace_days
        )
        collected = current.astimezone(UTC)
        snapshot_is_exact = end <= collected <= end + grace
        capability_payload = {
            "required_ready": capabilities.required_ready,
            "sources": {
                item.name: item.status for item in capabilities.sources
            },
        }
        return CloudLiveCollection(
            snapshot=normalized,
            capabilities=capability_payload,
            enrichments=correlate_cloud_enrichments(normalized),
            warnings=artifact.warnings,
            snapshot_is_exact=snapshot_is_exact,
            connector_version=capabilities.connector_version,
        )


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _history_label(dataset: Mapping[str, Any]) -> str:
    period = dataset.get("period")
    period = period if isinstance(period, Mapping) else {}
    period_id = str(period.get("period_id") or "").strip()
    try:
        start = datetime.fromisoformat(
            str(period.get("start_at") or "").replace("Z", "+00:00")
        )
        timezone = ZoneInfo(str(period.get("timezone") or "UTC"))
    except (ValueError, TypeError):
        return period_id
    months = (
        "",
        "Jan",
        "Fev",
        "Mar",
        "Abr",
        "Mai",
        "Jun",
        "Jul",
        "Ago",
        "Set",
        "Out",
        "Nov",
        "Dez",
    )
    local = start.astimezone(timezone)
    return f"{months[local.month]}/{str(local.year)[-2:]}"


def _history_row(dataset: Mapping[str, Any]) -> dict[str, Any]:
    period = dataset.get("period")
    period = period if isinstance(period, Mapping) else {}
    overview = dataset.get("overview")
    overview = overview if isinstance(overview, Mapping) else {}
    return {
        "period_id": str(period.get("period_id") or ""),
        "label": _history_label(dataset),
        "availability": "AVAILABLE",
        "overview": dict(overview),
    }


def _variants(_profile: ClientProfile) -> tuple[str, ...]:
    return ("expanded",)


def _emit(
    callback: Callable[[Mapping[str, Any]], None] | None,
    *,
    request: CloudExecutionRequest,
    status: str,
    stage: str,
    **extra: Any,
) -> None:
    if callback is None:
        return
    callback(
        {
            "event": "TENABLE_CLOUD_PROGRESS",
            "client_id": request.profile.client_id,
            "run_id": request.run_id,
            "status": status,
            "stage": stage,
            **extra,
        }
    )


class _CloudStageFailure(Exception):
    def __init__(
        self,
        *,
        stage: ComponentStage,
        failure_code: str,
        retryable: bool,
    ) -> None:
        super().__init__(failure_code)
        self.stage = stage
        self.failure_code = failure_code
        self.retryable = retryable


@contextmanager
def _cloud_stage(stage: ComponentStage, failure_code: str):
    try:
        yield
    except ExecutionInterruptedError:
        raise
    except _CloudStageFailure:
        raise
    except Exception as exc:
        raise _CloudStageFailure(
            stage=stage,
            failure_code=failure_code,
            retryable=bool(getattr(exc, "retryable", True)),
        ) from exc


def _write_and_render(
    *,
    request: CloudExecutionRequest,
    dependencies: CloudExecutionDependencies,
    dataset: Mapping[str, Any],
) -> tuple[Path, tuple[CloudGeneratedDocument, ...]]:
    execution_directory = (
        "automatic-monthly"
        if request.execution_type == "AUTOMATIC_MONTHLY"
        else "manual"
    )
    with _cloud_stage(ComponentStage.DATASET, "CLOUD_DATASET_FAILED"):
        artifact = dependencies.write_dataset(
            dataset=dataset,
            output_root=request.output_root,
            execution_type=execution_directory,
            client_id=request.profile.client_id,
            run_id=request.run_id,
        )
    documents: list[CloudGeneratedDocument] = []
    for variant in _variants(request.profile):
        output_path = request.report_directory / cloud_report_filename(
            request.profile.display_name,
            request.period,
        )
        with _cloud_stage(ComponentStage.RENDER, "CLOUD_RENDER_FAILED"):
            rendered = dependencies.render_report(
                template_path=request.template_path,
                dataset_path=artifact.dataset_path,
                profile=request.profile,
                output_path=output_path,
                variant=variant,
            )
        with _cloud_stage(
            ComponentStage.DOCUMENT_VALIDATION,
            "CLOUD_DOCUMENT_VALIDATION_FAILED",
        ):
            dependencies.validate_document(rendered.output_path)
        documents.append(
            CloudGeneratedDocument(
                path=Path(rendered.output_path),
                variant=variant,
            )
        )
    return Path(artifact.dataset_path), tuple(documents)


def execute_cloud_component(
    request: CloudExecutionRequest,
    *,
    dependencies: CloudExecutionDependencies,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> CloudComponentResult:
    """Generate the optional Cloud documents without failing the VM component."""

    if not request.profile.cloud_security_scope.enabled:
        return CloudComponentResult(status=CloudExecutionStatus.DISABLED)

    compatibility = request.compatibility()
    period = request.period.to_dict()
    try:
        if not request.force_refresh:
            exact = dependencies.repository.find_exact(
                compatibility=compatibility,
                period_start_at=str(period["start_at"]),
                period_end_at=str(period["end_at"]),
            )
            if exact is not None:
                _emit(
                    progress_callback,
                    request=request,
                    status="REPLAYING",
                    stage="SNAPSHOT_REPLAY",
                    snapshot_id=exact.snapshot_id,
                )
                replay = replay_cloud_snapshot(exact)
                dataset_path, documents = _write_and_render(
                    request=request,
                    dependencies=dependencies,
                    dataset=replay.dataset,
                )
                _emit(
                    progress_callback,
                    request=request,
                    status="FINISHED",
                    stage="PUBLICATION",
                    documents=len(documents),
                )
                return CloudComponentResult(
                    status=CloudExecutionStatus.REPLAYED,
                    documents=documents,
                    dataset_path=dataset_path,
                    snapshot_id=exact.snapshot_id,
                    cleanup_ready=dependencies.history_persistent,
                )

            current = dependencies.now()
            since = current - timedelta(hours=request.recent_collection_hours)
            recent = (
                None
                if request.bypass_recent_guard
                else dependencies.repository.latest_compatible_since(
                    compatibility=compatibility,
                    collected_since=_utc_iso(since),
                )
            )
            if recent is not None:
                warning = {
                    "code": "CLOUD_RECENT_COLLECTION_GUARD",
                    "message": (
                        "Uma coleta Cloud compatível já foi concluída nas últimas "
                        "24 horas; use atualização explícita para repetir a API."
                    ),
                    "retryable": True,
                }
                _emit(
                    progress_callback,
                    request=request,
                    status="BLOCKED",
                    stage="RECENT_COLLECTION_GUARD",
                    snapshot_id=recent.snapshot_id,
                )
                return CloudComponentResult(
                    status=CloudExecutionStatus.BLOCKED_RECENT_COLLECTION,
                    snapshot_id=recent.snapshot_id,
                    warnings=(warning,),
                    cleanup_ready=False,
                )

        _emit(
            progress_callback,
            request=request,
            status="STARTED",
            stage="COLLECTION",
        )
        with _cloud_stage(ComponentStage.COLLECTION, "CLOUD_COLLECTION_FAILED"):
            live = dependencies.collect_live(
                request,
                lambda event: (
                    progress_callback(dict(event))
                    if progress_callback is not None
                    else None
                ),
            )
        history: list[Mapping[str, Any]] = []
        for prior in dependencies.repository.list_main_before(
            compatibility=compatibility,
            period_end_before=str(period["end_at"]),
        ):
            history.append(_history_row(replay_cloud_snapshot(prior).dataset))

        with _cloud_stage(ComponentStage.DATASET, "CLOUD_DATASET_FAILED"):
            dataset = dependencies.build_dataset(
                snapshot=live.snapshot,
                period=request.period,
                enrichments=live.enrichments,
                snapshot_is_exact=live.snapshot_is_exact,
                connector_version=live.connector_version,
                capabilities=live.capabilities,
                history=tuple(history),
            )
        current_history = tuple((*history, _history_row(dataset)))
        with _cloud_stage(ComponentStage.DATASET, "CLOUD_DATASET_FAILED"):
            dataset = dependencies.build_dataset(
                snapshot=live.snapshot,
                period=request.period,
                enrichments=live.enrichments,
                snapshot_is_exact=live.snapshot_is_exact,
                connector_version=live.connector_version,
                capabilities=live.capabilities,
                history=current_history,
            )
        dataset_path, documents = _write_and_render(
            request=request,
            dependencies=dependencies,
            dataset=dataset,
        )
        with _cloud_stage(ComponentStage.DATASET, "CLOUD_DATASET_FAILED"):
            snapshot = build_cloud_snapshot(
                dataset=dataset,
                client_id=request.profile.client_id,
                tenant_id=request.profile.tenant_id,
                run_id=request.run_id,
                attempt_number=request.attempt_number,
                execution_type=request.execution_type,
                period_mode=request.period.mode.value,
                timezone=request.period.timezone,
                period_start_at=str(period["start_at"]),
                period_end_at=str(period["end_at"]),
                scope_hash=compatibility.scope_hash,
                collected_at=live.snapshot.collected_at,
                capabilities=live.capabilities,
                connector_version=live.connector_version,
            )
        with _cloud_stage(
            ComponentStage.SNAPSHOT_PUBLICATION,
            "CLOUD_SNAPSHOT_PUBLICATION_FAILED",
        ):
            dependencies.repository.publish(snapshot)
        _emit(
            progress_callback,
            request=request,
            status="FINISHED",
            stage="PUBLICATION",
            documents=len(documents),
            snapshot_id=snapshot.snapshot_id,
        )
        return CloudComponentResult(
            status=CloudExecutionStatus.COMPLETE,
            documents=documents,
            dataset_path=dataset_path,
            snapshot_id=snapshot.snapshot_id,
            warnings=tuple(dict(item) for item in live.warnings),
            cleanup_ready=dependencies.history_persistent,
        )
    except ExecutionInterruptedError:
        raise
    except _CloudStageFailure as exc:
        warning = {
            "code": "CLOUD_COMPONENT_FAILED",
            "message": (
                "Falha no componente Cloud Security; os demais relatórios "
                "foram preservados."
            ),
            "retryable": exc.retryable,
        }
        _emit(
            progress_callback,
            request=request,
            status="FAILED",
            stage=exc.stage.value,
            failure_code=exc.failure_code,
            retryable=exc.retryable,
        )
        return CloudComponentResult(
            status=CloudExecutionStatus.FAILED,
            warnings=(warning,),
            cleanup_ready=False,
            failure_stage=exc.stage,
            failure_code=exc.failure_code,
            retryable=exc.retryable,
        )
    except Exception as exc:
        retryable = bool(getattr(exc, "retryable", True))
        warning = {
            "code": "CLOUD_COMPONENT_FAILED",
            "message": (
                "Falha no componente Cloud Security; os demais relatórios "
                "foram preservados."
            ),
            "retryable": retryable,
        }
        _emit(
            progress_callback,
            request=request,
            status="FAILED",
            stage="COMPONENT",
            failure_code=str(
                getattr(exc, "failure_code", "TENABLE_CLOUD_UNEXPECTED")
            ),
            retryable=retryable,
        )
        return CloudComponentResult(
            status=CloudExecutionStatus.FAILED,
            warnings=(warning,),
            cleanup_ready=False,
        )


def _resume_failure(
    *,
    request: CloudExecutionRequest,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    stage: ComponentStage,
    failure_code: str,
) -> CloudComponentResult:
    warning = {
        "code": "CLOUD_COMPONENT_FAILED",
        "message": (
            "Falha no componente Cloud Security; os demais relatórios "
            "foram preservados."
        ),
        "retryable": True,
    }
    _emit(
        progress_callback,
        request=request,
        status="FAILED",
        stage=stage.value,
        failure_code=failure_code,
        retryable=True,
    )
    return CloudComponentResult(
        status=CloudExecutionStatus.FAILED,
        warnings=(warning,),
        cleanup_ready=False,
        failure_stage=stage,
        failure_code=failure_code,
        retryable=True,
    )


def _render_resumed_dataset(
    *,
    request: CloudExecutionRequest,
    dependencies: CloudExecutionDependencies,
    dataset_path: Path,
) -> tuple[CloudGeneratedDocument, ...]:
    documents: list[CloudGeneratedDocument] = []
    for variant in _variants(request.profile):
        output_path = request.report_directory / cloud_report_filename(
            request.profile.display_name,
            request.period,
        )
        rendered = dependencies.render_report(
            template_path=request.template_path,
            dataset_path=dataset_path,
            profile=request.profile,
            output_path=output_path,
            variant=variant,
        )
        documents.append(
            CloudGeneratedDocument(
                path=Path(rendered.output_path),
                variant=variant,
            )
        )
    return tuple(documents)


def _resume_cloud_component(
    request: CloudExecutionRequest,
    *,
    dependencies: CloudExecutionDependencies,
    resume: CloudResumeContext,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
) -> CloudComponentResult:
    if not request.profile.cloud_security_scope.enabled:
        return CloudComponentResult(status=CloudExecutionStatus.DISABLED)

    try:
        dataset_path = resume.dataset_path.resolve()
        output_root = request.output_root.resolve()
        dataset_path.relative_to(output_root)
        if not dataset_path.is_file():
            raise ValueError("Dataset Cloud de retomada ausente.")
        actual_sha256 = str(dependencies.hash_file(dataset_path)).strip().lower()
        if not hmac.compare_digest(actual_sha256, resume.dataset_sha256):
            raise ValueError("Hash do dataset Cloud de retomada nao confere.")
        dataset = dict(dependencies.load_dataset(dataset_path))
    except ExecutionInterruptedError:
        raise
    except Exception:
        return _resume_failure(
            request=request,
            progress_callback=progress_callback,
            stage=ComponentStage.DATASET,
            failure_code="CLOUD_RESUME_DATASET_INVALID",
        )

    _emit(
        progress_callback,
        request=request,
        status="STARTED",
        stage=ComponentStage.RENDER.value,
    )
    try:
        documents = _render_resumed_dataset(
            request=request,
            dependencies=dependencies,
            dataset_path=dataset_path,
        )
    except ExecutionInterruptedError:
        raise
    except Exception:
        return _resume_failure(
            request=request,
            progress_callback=progress_callback,
            stage=ComponentStage.RENDER,
            failure_code="CLOUD_RENDER_FAILED",
        )

    _emit(
        progress_callback,
        request=request,
        status="STARTED",
        stage=ComponentStage.DOCUMENT_VALIDATION.value,
    )
    try:
        for document in documents:
            dependencies.validate_document(document.path)
    except ExecutionInterruptedError:
        raise
    except Exception:
        return _resume_failure(
            request=request,
            progress_callback=progress_callback,
            stage=ComponentStage.DOCUMENT_VALIDATION,
            failure_code="CLOUD_DOCUMENT_VALIDATION_FAILED",
        )

    try:
        period = request.period.to_dict()
        snapshot = build_cloud_snapshot(
            dataset=dataset,
            client_id=request.profile.client_id,
            tenant_id=request.profile.tenant_id,
            run_id=request.run_id,
            attempt_number=request.attempt_number,
            execution_type=request.execution_type,
            period_mode=request.period.mode.value,
            timezone=request.period.timezone,
            period_start_at=str(period["start_at"]),
            period_end_at=str(period["end_at"]),
            scope_hash=request.compatibility().scope_hash,
            collected_at=str(dataset.get("collected_at") or ""),
            capabilities=resume.capabilities,
            connector_version=resume.connector_version,
        )
        dependencies.repository.publish(snapshot)
    except ExecutionInterruptedError:
        raise
    except Exception:
        return _resume_failure(
            request=request,
            progress_callback=progress_callback,
            stage=ComponentStage.SNAPSHOT_PUBLICATION,
            failure_code="CLOUD_SNAPSHOT_PUBLICATION_FAILED",
        )

    _emit(
        progress_callback,
        request=request,
        status="FINISHED",
        stage=ComponentStage.SNAPSHOT_PUBLICATION.value,
        documents=len(documents),
        snapshot_id=snapshot.snapshot_id,
    )
    return CloudComponentResult(
        status=CloudExecutionStatus.COMPLETE,
        documents=documents,
        dataset_path=dataset_path,
        snapshot_id=snapshot.snapshot_id,
        cleanup_ready=dependencies.history_persistent,
    )


def retry_cloud_component(
    request: CloudExecutionRequest,
    *,
    dependencies: CloudExecutionDependencies,
    resume: CloudResumeContext | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> CloudComponentResult:
    """Retry only Cloud while preserving exact replay and bypassing the 24h guard."""

    if resume is not None:
        return _resume_cloud_component(
            request,
            dependencies=dependencies,
            resume=resume,
            progress_callback=progress_callback,
        )
    return execute_cloud_component(
        replace(request, bypass_recent_guard=True),
        dependencies=dependencies,
        progress_callback=progress_callback,
    )


__all__ = [
    "CloudComponentResult",
    "CloudExecutionDependencies",
    "CloudExecutionRequest",
    "CloudExecutionStatus",
    "CloudGeneratedDocument",
    "CloudLiveCollection",
    "CloudResumeContext",
    "TenableCloudLiveCollector",
    "execute_cloud_component",
    "retry_cloud_component",
]
