from __future__ import annotations

import gzip
import importlib
from dataclasses import replace


def _module():
    return importlib.import_module(
        "tenable_reports.application.cloud_snapshots"
    )


def _dataset() -> dict:
    return {
        "schema_version": 1,
        "document_kind": "cloud",
        "metric_definition_version": "cloud-metrics-v1",
        "overview": {"assets": 2, "unique_cves": 3},
        "top_critical_cves": [{"cve": "CVE-2026-0001"}],
    }


def _identity(run_id: str = "run-jul") -> dict:
    return {
        "client_id": "cliente-fixture",
        "tenant_id": "tenant-fixture",
        "run_id": run_id,
        "attempt_number": 1,
        "execution_type": "AUTOMATIC_MONTHLY",
        "period_mode": "PREVIOUS_CALENDAR_MONTH",
        "timezone": "America/Fortaleza",
        "period_start_at": "2026-07-01T03:00:00Z",
        "period_end_at": "2026-08-01T03:00:00Z",
        "scope_hash": "scope-cloud-v1",
        "collected_at": "2026-08-01T12:00:00Z",
        "capabilities": {
            "required_ready": True,
            "sources": {"findings": "AVAILABLE"},
        },
    }


def _compatibility() -> object:
    module = _module()
    return module.CloudSnapshotCompatibility(
        client_id="cliente-fixture",
        tenant_id="tenant-fixture",
        execution_type="AUTOMATIC_MONTHLY",
        period_mode="PREVIOUS_CALENDAR_MONTH",
        timezone="America/Fortaleza",
        scope_hash="scope-cloud-v1",
        metric_definition_version="cloud-metrics-v1",
        connector_version="cloud-graphql-v1",
        normalizer_version="cloud-normalizer-v1",
        schema_version=1,
    )


def test_compact_cloud_snapshot_round_trip_preserves_dataset() -> None:
    module = _module()
    snapshot = module.build_cloud_snapshot(
        dataset=_dataset(),
        **_identity(),
    )

    replay = module.replay_cloud_snapshot(snapshot)

    assert replay.dataset == _dataset()
    assert snapshot.content_sha256 == replay.content_sha256
    assert snapshot.payload_gzip.startswith(b"\x1f\x8b")
    assert snapshot.record_counts["assets"] == 2
    assert "secret" not in snapshot.capabilities


def test_corrupted_cloud_snapshot_is_rejected() -> None:
    module = _module()
    snapshot = module.build_cloud_snapshot(
        dataset=_dataset(),
        **_identity(),
    )

    corrupted = replace(snapshot, payload_gzip=gzip.compress(b"{}"))

    try:
        module.replay_cloud_snapshot(corrupted)
    except ValueError as exc:
        assert "Checksum" in str(exc)
    else:
        raise AssertionError("Snapshot Cloud corrompido deveria ser rejeitado.")


def test_memory_repository_is_immutable_and_finds_exact_snapshot() -> None:
    module = _module()
    repository = module.MemoryCloudSnapshotRepository()
    snapshot = module.build_cloud_snapshot(
        dataset=_dataset(),
        **_identity(),
    )
    repository.publish(snapshot)
    repository.publish(snapshot)

    found = repository.find_exact(
        compatibility=_compatibility(),
        period_start_at=snapshot.period_start_at,
        period_end_at=snapshot.period_end_at,
    )

    assert repository.count == 1
    assert found == snapshot
    with_different_content = replace(snapshot, content_sha256="0" * 64)
    try:
        repository.publish(with_different_content)
    except ValueError as exc:
        assert "imutavel" in str(exc)
    else:
        raise AssertionError("Conflito imutável deveria falhar.")


def test_history_returns_only_compatible_main_runs_before_boundary() -> None:
    module = _module()
    repository = module.MemoryCloudSnapshotRepository()

    def publish(
        run_id: str,
        start_at: str,
        end_at: str,
        *,
        main: bool,
        scope_hash: str = "scope-cloud-v1",
    ) -> None:
        snapshot = module.build_cloud_snapshot(
            dataset=_dataset(),
            **{
                **_identity(run_id),
                "period_start_at": start_at,
                "period_end_at": end_at,
                "scope_hash": scope_hash,
            },
        )
        repository.publish(snapshot)
        if main:
            repository.mark_main(run_id)

    publish(
        "run-jun",
        "2026-06-01T03:00:00Z",
        "2026-07-01T03:00:00Z",
        main=True,
    )
    publish(
        "run-jul",
        "2026-07-01T03:00:00Z",
        "2026-08-01T03:00:00Z",
        main=True,
    )
    publish(
        "run-test",
        "2026-07-01T03:00:00Z",
        "2026-08-01T03:00:00Z",
        main=False,
    )
    publish(
        "run-other-scope",
        "2026-07-01T03:00:00Z",
        "2026-08-01T03:00:00Z",
        main=True,
        scope_hash="other-scope",
    )

    rows = repository.list_main_before(
        compatibility=_compatibility(),
        period_end_before="2026-09-01T03:00:00Z",
    )

    assert [row.run_id for row in rows] == ["run-jun", "run-jul"]


def test_recent_compatible_snapshot_and_contract_cache_can_be_invalidated() -> None:
    module = _module()
    repository = module.MemoryCloudSnapshotRepository()
    snapshot = module.build_cloud_snapshot(
        dataset=_dataset(),
        **_identity(),
    )
    repository.publish(snapshot)

    recent = repository.latest_compatible_since(
        compatibility=_compatibility(),
        collected_since="2026-08-01T00:00:00Z",
    )
    assert recent == snapshot

    check = module.CloudContractCheck(
        client_id="cliente-fixture",
        environment="global",
        connector_version="cloud-graphql-v1",
        credential_revision="credential-file-revision-2",
        endpoint="https://app.tenable.com/graphql",
        required_ready=True,
        capabilities={"virtual_machines": "AVAILABLE"},
        checked_at="2026-08-26T12:00:00Z",
    )
    repository.save_contract_check(check)
    assert repository.latest_contract_check(
        client_id=check.client_id,
        environment=check.environment,
        connector_version=check.connector_version,
        credential_revision=check.credential_revision,
    ) == check
    assert repository.invalidate_contract_checks(
        client_id=check.client_id,
        environment=check.environment,
    ) == 1
    assert repository.latest_contract_check(
        client_id=check.client_id,
        environment=check.environment,
        connector_version=check.connector_version,
        credential_revision=check.credential_revision,
    ) is None
