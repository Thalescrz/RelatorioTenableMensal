from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tenable_reports.application.component_collection import (
    ComponentCollectionCheckpoint,
    component_checkpoint_path,
    load_component_checkpoint,
    merge_component_checkpoints,
    persist_component_checkpoint,
)
from tenable_reports.application.publishing import sha256_file
from tenable_reports.application.staged_execution import (
    CheckpointArtifact,
    CheckpointValidationError,
    RemoteCollectionRequest,
)
from tenable_reports.domain.remote_components import RemoteComponentState
from tenable_reports.domain.report_components import ReportComponent
from tenable_reports.domain.reporting import previous_calendar_month


def _request(root: Path) -> RemoteCollectionRequest:
    period = previous_calendar_month(
        reference_at="2026-09-04T12:00:00-03:00",
        timezone_name="America/Fortaleza",
    )
    return RemoteCollectionRequest(
        storage_root=root.resolve(),
        checkpoint_path=(root / "checkpoints" / "client-a" / "run-a.json").resolve(),
        client_id="client-a",
        tenant_id="tenant-a",
        run_id="run-a",
        logical_job_id="job-a",
        execution_type="AUTOMATIC_MONTHLY",
        mode="automatic",
        origin="SCHEDULED",
        attempt_number=1,
        period=period.to_dict(),
    )


def _checkpoint(
    root: Path,
    component: ReportComponent,
    *,
    status: RemoteComponentState = RemoteComponentState.COMPLETE,
    artifact_name: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ComponentCollectionCheckpoint:
    request = _request(root)
    artifacts: tuple[CheckpointArtifact, ...] = ()
    if artifact_name is not None:
        artifact_path = (
            component_checkpoint_path(request, component).parent / artifact_name
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(component.value, encoding="utf-8")
        artifacts = (
            CheckpointArtifact(
                component=component,
                kind=f"{component.value.lower()}_artifact",
                path=artifact_path,
                sha256=sha256_file(artifact_path),
            ),
        )
    return ComponentCollectionCheckpoint(
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
        status=status,
        artifacts=artifacts,
        metadata=metadata or {},
        query_fingerprint=hashlib.sha256(component.value.encode()).hexdigest(),
    )


def test_component_checkpoints_write_to_disjoint_directories(tmp_path: Path) -> None:
    checkpoints = tuple(
        persist_component_checkpoint(
            _checkpoint(
                tmp_path,
                component,
                artifact_name=f"{component.value.lower()}.json",
            ),
            storage_root=tmp_path,
        )
        for component in ReportComponent
    )

    assert [item.checkpoint_path.parent.name for item in checkpoints] == [
        "vm_core",
        "was",
        "cloud",
    ]
    artifact_sets = tuple({item.path for item in cp.artifacts} for cp in checkpoints)
    assert not (artifact_sets[0] & artifact_sets[1])
    assert not (artifact_sets[0] & artifact_sets[2])
    assert not (artifact_sets[1] & artifact_sets[2])


def test_component_workspace_keeps_windows_asset_paths_below_classic_limit() -> None:
    root = Path("C:/Codex/RelatorioTenableMensalv2/data").resolve()
    run_id = "20260904T235800Z-12345678-cliente-exemplo-attempt-1"
    request = RemoteCollectionRequest(
        storage_root=root,
        checkpoint_path=(
            root
            / "manual"
            / "orchestration"
            / "checkpoints"
            / "69443250-0252-44de-85e3-17dc0019766c"
            / "1234567890abcdef1234567890abcdef-cliente-exemplo"
            / "checkpoint.json"
        ).resolve(),
        client_id="cliente-exemplo",
        tenant_id="tenant-exemplo",
        run_id=run_id,
        logical_job_id="logical-job-exemplo",
        execution_type="MANUAL",
        mode="manual",
        origin="MANUAL",
        attempt_number=1,
        period={
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-09-01T00:00:00Z",
        },
    )

    raw_asset_path = (
        component_checkpoint_path(request, ReportComponent.VM_CORE).parent
        / "raw"
        / request.client_id
        / request.run_id
        / "tenable_vm_assets_v2"
        / "00000000-0000-0000-0000-000000000001"
        / "chunk-000001.jsonl.gz"
    )

    assert ".components" in raw_asset_path.parts
    assert len(str(raw_asset_path)) < 240


def test_merge_accepts_complete_vm_failed_was_and_complete_cloud(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    merged = merge_component_checkpoints(
        request=request,
        checkpoints=(
            _checkpoint(tmp_path, ReportComponent.VM_CORE, artifact_name="vm.json"),
            _checkpoint(
                tmp_path,
                ReportComponent.WAS,
                status=RemoteComponentState.WAITING_MANUAL_RETRY,
                metadata={
                    "failure_code": "WAS_TIMEOUT",
                    "retryable": True,
                },
            ),
            _checkpoint(
                tmp_path,
                ReportComponent.CLOUD,
                artifact_name="cloud.json",
            ),
        ),
    )

    assert merged.component_metadata["VM_CORE"]["status"] == "COMPLETE"
    assert merged.component_metadata["WAS"] == {
        "status": "FAILED",
        "failure_code": "WAS_TIMEOUT",
        "retryable": True,
    }
    assert merged.component_metadata["CLOUD"]["status"] == "COMPLETE"
    assert {artifact.component for artifact in merged.artifacts} == {
        "VM_CORE",
        "CLOUD",
    }


def test_merge_maps_not_applicable_without_inventing_an_artifact(
    tmp_path: Path,
) -> None:
    request = _request(tmp_path)
    merged = merge_component_checkpoints(
        request=request,
        checkpoints=(
            _checkpoint(tmp_path, ReportComponent.VM_CORE, artifact_name="vm.json"),
            _checkpoint(
                tmp_path,
                ReportComponent.WAS,
                status=RemoteComponentState.NOT_APPLICABLE,
                metadata={"reason_code": "WAS_DISABLED"},
            ),
            _checkpoint(
                tmp_path,
                ReportComponent.CLOUD,
                status=RemoteComponentState.NOT_APPLICABLE,
                metadata={"reason_code": "CLOUD_DISABLED"},
            ),
        ),
    )

    assert merged.component_metadata["WAS"]["status"] == "NOT_APPLICABLE"
    assert merged.component_metadata["CLOUD"]["status"] == "NOT_APPLICABLE"


def test_persist_rejects_artifact_from_another_component(tmp_path: Path) -> None:
    checkpoint = _checkpoint(
        tmp_path,
        ReportComponent.VM_CORE,
        artifact_name="vm.json",
    )
    wrong = CheckpointArtifact(
        component=ReportComponent.WAS,
        kind="wrong_component",
        path=checkpoint.artifacts[0].path,
        sha256=checkpoint.artifacts[0].sha256,
    )

    with pytest.raises(CheckpointValidationError, match="componente"):
        ComponentCollectionCheckpoint(
            **{
                **checkpoint.to_dict(),
                "checkpoint_path": checkpoint.checkpoint_path,
                "artifacts": (wrong,),
            }
        )


def test_load_rejects_tampered_component_artifact(tmp_path: Path) -> None:
    checkpoint = persist_component_checkpoint(
        _checkpoint(
            tmp_path,
            ReportComponent.VM_CORE,
            artifact_name="vm.json",
        ),
        storage_root=tmp_path,
    )
    checkpoint.artifacts[0].path.write_text("tampered", encoding="utf-8")

    with pytest.raises(CheckpointValidationError, match="Hash"):
        load_component_checkpoint(
            checkpoint.checkpoint_path,
            storage_root=tmp_path,
        )


def test_merge_rejects_duplicate_or_foreign_identity(tmp_path: Path) -> None:
    request = _request(tmp_path)
    vm = _checkpoint(tmp_path, ReportComponent.VM_CORE, artifact_name="vm.json")

    with pytest.raises(CheckpointValidationError, match="duplicado"):
        merge_component_checkpoints(request=request, checkpoints=(vm, vm))

    foreign = ComponentCollectionCheckpoint(
        **{
            **vm.to_dict(),
            "checkpoint_path": vm.checkpoint_path,
            "client_id": "client-b",
            "artifacts": vm.artifacts,
        }
    )
    with pytest.raises(CheckpointValidationError, match="identidade"):
        merge_component_checkpoints(request=request, checkpoints=(foreign,))
