from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tenable_reports.application.staged_execution import (
    CheckpointArtifact,
    CheckpointValidationError,
    CollectionCheckpoint,
    LocalBuildDependencies,
    LocalBuildRequest,
    RemoteCollectionDependencies,
    RemoteCollectionRequest,
    build_client_local,
    collect_client_remote,
    load_collection_checkpoint,
)


VM_CONTENT = b"vm-fixture"
VM_SHA256 = "b7c425cbb44e560fc27deae0c95f1f4d42f2117b5ee229e7c3d80c64958366bf"
CLOUD_CONTENT = b"cloud-fixture"
CLOUD_SHA256 = "df35410d229829db6bca80cd3b833cff3501778989bfcae9a9eb77e4013f284f"


def _remote_request(tmp_path: Path) -> RemoteCollectionRequest:
    return RemoteCollectionRequest(
        storage_root=tmp_path,
        checkpoint_path=tmp_path / "checkpoints" / "client-a" / "run-a.json",
        client_id="client-a",
        tenant_id="tenant-a",
        run_id="run-a",
        logical_job_id="job-a",
        execution_type="MANUAL",
        mode="manual",
        origin="MANUAL",
        attempt_number=1,
        period={
            "period_id": "2026-08",
            "start_at": "2026-08-01T03:00:00Z",
            "end_at": "2026-09-01T03:00:00Z",
            "timezone": "America/Fortaleza",
        },
    )


def _checkpoint(
    request: RemoteCollectionRequest,
    *,
    vm_path: Path,
    vm_sha256: str = VM_SHA256,
    cloud_path: Path | None = None,
) -> CollectionCheckpoint:
    artifacts = [
        CheckpointArtifact(
            component="VM_CORE",
            kind="vm_findings",
            path=vm_path,
            sha256=vm_sha256,
        )
    ]
    hashes = {"vm_findings": vm_sha256}
    if cloud_path is not None:
        artifacts.append(
            CheckpointArtifact(
                component="CLOUD",
                kind="cloud_raw",
                path=cloud_path,
                sha256=CLOUD_SHA256,
            )
        )
        hashes["cloud_raw"] = CLOUD_SHA256
    return CollectionCheckpoint(
        schema_version=1,
        client_id=request.client_id,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        logical_job_id=request.logical_job_id,
        execution_type=request.execution_type,
        mode=request.mode,
        origin=request.origin,
        attempt_number=request.attempt_number,
        period=dict(request.period),
        component_metadata={
            "VM_CORE": {
                "status": "COMPLETE",
                "export_uuid": "00000000-0000-0000-0000-000000000101",
                "chunks_complete": 1,
                "chunks_total": 1,
            },
            "WAS": {"status": "SKIPPED"},
            "CLOUD": {
                "status": "COMPLETE" if cloud_path is not None else "SKIPPED"
            },
        },
        artifacts=tuple(artifacts),
        hashes=hashes,
    )


def _persist_valid_checkpoint(
    tmp_path: Path,
) -> tuple[RemoteCollectionRequest, CollectionCheckpoint]:
    request = _remote_request(tmp_path)
    vm_path = (tmp_path / "raw" / "vm.gz").resolve()
    cloud_path = (tmp_path / "raw" / "cloud.jsonl").resolve()
    vm_path.parent.mkdir(parents=True)
    vm_path.write_bytes(VM_CONTENT)
    cloud_path.write_bytes(CLOUD_CONTENT)
    expected = _checkpoint(
        request,
        vm_path=vm_path,
        cloud_path=cloud_path,
    )
    result = collect_client_remote(
        request,
        dependencies=RemoteCollectionDependencies(
            collect=lambda _: expected,
        ),
    )
    return request, result


def test_complete_checkpoint_round_trips_atomically_without_secrets(
    tmp_path: Path,
) -> None:
    request, written = _persist_valid_checkpoint(tmp_path)

    loaded = load_collection_checkpoint(
        request.checkpoint_path,
        storage_root=request.storage_root,
    )
    payload_text = request.checkpoint_path.read_text(encoding="utf-8")
    payload = json.loads(payload_text)

    assert loaded == written
    assert loaded.schema_version == 1
    assert loaded.client_id == "client-a"
    assert loaded.tenant_id == "tenant-a"
    assert loaded.mode == "manual"
    assert loaded.period == {
        "period_id": "2026-08",
        "start_at": "2026-08-01T03:00:00Z",
        "end_at": "2026-09-01T03:00:00Z",
        "timezone": "America/Fortaleza",
    }
    assert [str(item.path) for item in loaded.artifacts] == [
        str((tmp_path / "raw" / "vm.gz").resolve()),
        str((tmp_path / "raw" / "cloud.jsonl").resolve()),
    ]
    assert loaded.hashes == {
        "vm_findings": VM_SHA256,
        "cloud_raw": CLOUD_SHA256,
    }
    assert payload["component_metadata"]["VM_CORE"]["chunks_complete"] == 1
    assert list(request.checkpoint_path.parent.iterdir()) == [request.checkpoint_path]
    lowered = payload_text.lower()
    for forbidden in (
        "access_key",
        "secret_key",
        "password",
        "api_token",
        "authorization",
        "fixture-secret",
    ):
        assert forbidden not in lowered


def test_checkpoint_rejects_artifact_outside_storage_root(tmp_path: Path) -> None:
    request = _remote_request(tmp_path)
    outside = (tmp_path / ".." / "outside" / "vm.gz").resolve()
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(VM_CONTENT)

    with pytest.raises(CheckpointValidationError) as raised:
        collect_client_remote(
            request,
            dependencies=RemoteCollectionDependencies(
                collect=lambda _: _checkpoint(request, vm_path=outside),
            ),
        )

    assert raised.value.failure_code == "CHECKPOINT_PATH_OUTSIDE_ROOT"
    assert not request.checkpoint_path.exists()


def test_tampered_artifact_is_rejected_before_local_builder(tmp_path: Path) -> None:
    request, _ = _persist_valid_checkpoint(tmp_path)
    (tmp_path / "raw" / "vm.gz").write_bytes(b"tampered")
    builder_calls: list[CollectionCheckpoint] = []

    with pytest.raises(CheckpointValidationError) as raised:
        build_client_local(
            LocalBuildRequest(
                storage_root=request.storage_root,
                checkpoint_path=request.checkpoint_path,
            ),
            dependencies=LocalBuildDependencies(
                build=lambda checkpoint: builder_calls.append(checkpoint),
            ),
        )

    assert raised.value.failure_code == "CHECKPOINT_HASH_MISMATCH"
    assert builder_calls == []


def test_missing_artifact_is_classified_before_local_builder(tmp_path: Path) -> None:
    request, _ = _persist_valid_checkpoint(tmp_path)
    (tmp_path / "raw" / "vm.gz").unlink()
    with pytest.raises(CheckpointValidationError) as raised:
        build_client_local(
            LocalBuildRequest(
                storage_root=request.storage_root,
                checkpoint_path=request.checkpoint_path,
            ),
            dependencies=LocalBuildDependencies(build=lambda checkpoint: checkpoint),
        )
    assert raised.value.failure_code == "CHECKPOINT_ARTIFACT_MISSING"


def test_local_build_validates_checkpoint_and_has_no_live_api_dependency(
    tmp_path: Path,
) -> None:
    request, checkpoint = _persist_valid_checkpoint(tmp_path)
    received: list[CollectionCheckpoint] = []

    def local_builder(value: CollectionCheckpoint) -> dict[str, Any]:
        received.append(value)
        return {
            "status": "COMPLETE",
            "run_id": value.run_id,
            "documents": ["general.docx", "custom.docx"],
        }

    dependencies = LocalBuildDependencies(build=local_builder)
    with pytest.raises(TypeError):
        LocalBuildDependencies(
            build=local_builder,
            live_api=lambda: pytest.fail("live API must not exist in local build"),
        )

    result = build_client_local(
        LocalBuildRequest(
            storage_root=request.storage_root,
            checkpoint_path=request.checkpoint_path,
        ),
        dependencies=dependencies,
    )

    assert not hasattr(dependencies, "live_api")
    assert received == [checkpoint]
    assert result == {
        "status": "COMPLETE",
        "run_id": "run-a",
        "documents": ["general.docx", "custom.docx"],
    }


def test_remote_collection_persists_valid_checkpoint_without_local_render(
    tmp_path: Path,
) -> None:
    request = _remote_request(tmp_path)
    vm_path = (tmp_path / "raw" / "vm.gz").resolve()
    vm_path.parent.mkdir(parents=True)
    vm_path.write_bytes(VM_CONTENT)
    remote_calls: list[RemoteCollectionRequest] = []

    def remote_collector(value: RemoteCollectionRequest) -> CollectionCheckpoint:
        remote_calls.append(value)
        return _checkpoint(request, vm_path=vm_path)

    result = collect_client_remote(
        request,
        dependencies=RemoteCollectionDependencies(collect=remote_collector),
    )

    assert remote_calls == [request]
    assert result.run_id == "run-a"
    assert result.component_metadata["VM_CORE"]["status"] == "COMPLETE"
    assert request.checkpoint_path.is_file()
    assert load_collection_checkpoint(
        request.checkpoint_path,
        storage_root=request.storage_root,
    ) == result
