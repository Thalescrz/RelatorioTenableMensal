from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tenable_reports.application.cloud_execution import (
    CloudExecutionDependencies,
    CloudExecutionRequest,
    CloudExecutionStatus,
    CloudLiveCollection,
    execute_cloud_component,
    retry_cloud_component,
)
from tenable_reports.application.cloud_snapshots import (
    MemoryCloudSnapshotRepository,
    build_cloud_snapshot,
)
from tenable_reports.config.profile import ClientProfile, CloudSecurityScope
from tenable_reports.domain.cloud import NormalizedCloudSnapshot
from tenable_reports.domain.reporting import previous_calendar_month


def _profile(*, enabled: bool = True) -> ClientProfile:
    return ClientProfile(
        schema_version=1,
        client_id="cliente-fixture",
        display_name="CLIENTE FIXTURE",
        tenant_id="tenant-fixture",
        cloud_security_scope=CloudSecurityScope(
            enabled=enabled,
            environment="global",
            layout="comparison",
        ),
    )


def _period():
    return previous_calendar_month(
        reference_at="2026-08-01T12:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def _normalized() -> NormalizedCloudSnapshot:
    return NormalizedCloudSnapshot(
        collected_at="2026-08-01T12:00:00Z",
        assets=(),
        occurrences=(),
        findings=(),
        inventory=(),
        lifecycle=(),
        source_status={
            "virtual_machines": "AVAILABLE",
            "container_images": "AVAILABLE",
        },
        quality_issues=(),
    )


def _dataset(period=None) -> dict:
    selected_period = period or _period()
    return {
        "schema_version": 1,
        "document_kind": "cloud",
        "metric_definition_version": "cloud-metrics-v2",
        "connector_version": "cloud-graphql-v1",
        "period": selected_period.to_dict(),
        "collected_at": "2026-08-01T12:00:00Z",
        "overview": {
            "assets": 0,
            "vulnerability_occurrences": 0,
            "unique_cves": 0,
            "posture_findings": 0,
            "severity_counts": {
                "CRITICAL": 0,
                "HIGH": 0,
                "MEDIUM": 0,
                "LOW": 0,
            },
        },
        "top_critical_cves": [],
        "top_correctable_vulnerabilities": [],
        "history": [],
    }


class _Artifact:
    def __init__(self, path: Path, dataset: dict) -> None:
        self.dataset_path = path
        self.dataset = dataset
        self.sha256 = "fixture-sha"


class _RenderResult:
    def __init__(self, path: Path, variant: str) -> None:
        self.output_path = path
        self.variant = variant


def _request(tmp_path: Path, *, profile: ClientProfile | None = None, force=False):
    return CloudExecutionRequest(
        profile=profile or _profile(),
        period=_period(),
        execution_type="MANUAL",
        run_id="run-fixture",
        attempt_number=1,
        output_root=tmp_path,
        report_directory=tmp_path / "reports",
        template_path=tmp_path / "cloud-template.docx",
        force_refresh=force,
    )


def _dependencies(tmp_path: Path, repository=None):
    calls = {"collect": 0, "write": 0, "render": [], "validate": []}
    repo = repository or MemoryCloudSnapshotRepository()

    def collect(_request, _progress):
        calls["collect"] += 1
        return CloudLiveCollection(
            snapshot=_normalized(),
            capabilities={
                "required_ready": True,
                "sources": {
                    "virtual_machines": "AVAILABLE",
                    "container_images": "AVAILABLE",
                },
            },
            warnings=(),
            snapshot_is_exact=True,
        )

    def build_dataset(**_kwargs):
        return _dataset(_kwargs["period"])

    def write_dataset(**kwargs):
        calls["write"] += 1
        path = tmp_path / f"dataset-{calls['write']}.json"
        path.write_text("{}", encoding="utf-8")
        return _Artifact(path, dict(kwargs["dataset"]))

    def render(**kwargs):
        variant = str(kwargs["variant"])
        calls["render"].append(variant)
        path = Path(kwargs["output_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture-docx")
        return _RenderResult(path, variant)

    def validate(path):
        calls["validate"].append(Path(path).name)
        return {"package_status": "VALID"}

    dependencies = CloudExecutionDependencies(
        repository=repo,
        collect_live=collect,
        build_dataset=build_dataset,
        write_dataset=write_dataset,
        render_report=render,
        validate_document=validate,
        now=lambda: datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
    )
    return dependencies, calls


def test_disabled_cloud_does_not_call_collection_or_rendering(tmp_path: Path) -> None:
    dependencies, calls = _dependencies(tmp_path)

    result = execute_cloud_component(
        _request(tmp_path, profile=_profile(enabled=False)),
        dependencies=dependencies,
    )

    assert result.status is CloudExecutionStatus.DISABLED
    assert result.documents == ()
    assert calls == {"collect": 0, "write": 0, "render": [], "validate": []}


def test_enabled_cloud_renders_one_standard_report_from_live_dataset(tmp_path: Path) -> None:
    dependencies, calls = _dependencies(tmp_path)

    result = execute_cloud_component(
        _request(tmp_path),
        dependencies=dependencies,
    )

    assert result.status is CloudExecutionStatus.COMPLETE
    assert calls["collect"] == 1
    assert calls["write"] == 1
    assert calls["render"] == ["expanded"]
    assert len(result.documents) == 1
    assert {item.variant for item in result.documents} == {"expanded"}
    assert result.dataset_path is not None
    assert result.snapshot_id
    assert result.cleanup_ready is True


def test_exact_snapshot_is_replayed_without_live_collection(tmp_path: Path) -> None:
    repository = MemoryCloudSnapshotRepository()
    dependencies, calls = _dependencies(tmp_path, repository)
    request = _request(tmp_path)
    compatibility = request.compatibility()
    snapshot = build_cloud_snapshot(
        dataset=_dataset(request.period),
        client_id=request.profile.client_id,
        tenant_id=request.profile.tenant_id,
        run_id="previous-run",
        attempt_number=1,
        execution_type=request.execution_type,
        period_mode=request.period.mode.value,
        timezone=request.period.timezone,
        period_start_at=request.period.to_dict()["start_at"],
        period_end_at=request.period.to_dict()["end_at"],
        scope_hash=compatibility.scope_hash,
        collected_at="2026-08-01T11:00:00Z",
        capabilities={"required_ready": True},
    )
    repository.publish(snapshot)

    result = execute_cloud_component(request, dependencies=dependencies)

    assert result.status is CloudExecutionStatus.REPLAYED
    assert calls["collect"] == 0
    assert calls["write"] == 1
    assert calls["render"] == ["expanded"]
    assert result.snapshot_id == snapshot.snapshot_id


def test_recent_compatible_non_exact_snapshot_blocks_duplicate_collection(
    tmp_path: Path,
) -> None:
    repository = MemoryCloudSnapshotRepository()
    dependencies, calls = _dependencies(tmp_path, repository)
    request = _request(tmp_path)
    compatibility = request.compatibility()
    recent = build_cloud_snapshot(
        dataset=_dataset(request.period),
        client_id=request.profile.client_id,
        tenant_id=request.profile.tenant_id,
        run_id="recent-other-period",
        attempt_number=1,
        execution_type=request.execution_type,
        period_mode=request.period.mode.value,
        timezone=request.period.timezone,
        period_start_at="2026-06-01T03:00:00Z",
        period_end_at="2026-07-01T03:00:00Z",
        scope_hash=compatibility.scope_hash,
        collected_at="2026-08-01T10:00:00Z",
        capabilities={"required_ready": True},
    )
    repository.publish(recent)

    result = execute_cloud_component(request, dependencies=dependencies)

    assert result.status is CloudExecutionStatus.BLOCKED_RECENT_COLLECTION
    assert result.documents == ()
    assert result.cleanup_ready is False
    assert calls["collect"] == 0
    assert result.warnings[0]["code"] == "CLOUD_RECENT_COLLECTION_GUARD"

    forced = execute_cloud_component(
        replace(request, force_refresh=True, run_id="forced-run"),
        dependencies=dependencies,
    )
    assert forced.status is CloudExecutionStatus.COMPLETE
    assert calls["collect"] == 1


def test_retry_cloud_bypasses_recent_guard_without_calling_other_components(
    tmp_path: Path,
) -> None:
    repository = MemoryCloudSnapshotRepository()
    dependencies, calls = _dependencies(tmp_path, repository)
    request = _request(tmp_path)
    compatibility = request.compatibility()
    recent = build_cloud_snapshot(
        dataset=_dataset(request.period),
        client_id=request.profile.client_id,
        tenant_id=request.profile.tenant_id,
        run_id="recent-other-period",
        attempt_number=1,
        execution_type=request.execution_type,
        period_mode=request.period.mode.value,
        timezone=request.period.timezone,
        period_start_at="2026-06-01T03:00:00Z",
        period_end_at="2026-07-01T03:00:00Z",
        scope_hash=compatibility.scope_hash,
        collected_at="2026-08-01T10:00:00Z",
        capabilities={"required_ready": True},
    )
    repository.publish(recent)

    result = retry_cloud_component(request, dependencies=dependencies)

    assert result.status is CloudExecutionStatus.COMPLETE
    assert calls["collect"] == 1
    assert calls["render"] == ["expanded"]

def test_cloud_failure_is_isolated_as_a_retryable_component_warning(
    tmp_path: Path,
) -> None:
    dependencies, calls = _dependencies(tmp_path)

    def fail(_request, _progress):
        calls["collect"] += 1
        raise RuntimeError("fixture failure")

    dependencies = replace(dependencies, collect_live=fail)
    result = execute_cloud_component(
        _request(tmp_path),
        dependencies=dependencies,
    )

    assert result.status is CloudExecutionStatus.FAILED
    assert result.documents == ()
    assert result.cleanup_ready is False
    assert result.warnings == (
        {
            "code": "CLOUD_COMPONENT_FAILED",
            "message": (
                "Falha no componente Cloud Security; os demais relatórios "
                "foram preservados."
            ),
            "retryable": True,
        },
    )
