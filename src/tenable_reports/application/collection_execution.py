from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from tenable_reports import __version__
from tenable_reports.application.collection_routing import (
    CollectionRoute,
    select_collection_route,
)
from tenable_reports.application.compact_snapshots import (
    CompactFindingSnapshot,
    CompactSnapshotRepository,
    replay_compact_snapshot,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import SourceSnapshot, build_source_snapshot, utc_now_iso
from tenable_reports.domain.normalization import (
    DataQualityIssue,
    NormalizedAsset,
    NormalizedFinding,
)
from tenable_reports.domain.reporting import ReportingPeriod
from tenable_reports.infrastructure.jsonl_io import write_jsonl_gzip_exclusive


@dataclass(frozen=True, slots=True)
class MaterializedReplay:
    normalized_manifest_path: Path
    assets_path: Path
    findings_path: Path
    quality_issues_path: Path


def _write_exclusive(path: Path, payload: Mapping[str, Any]) -> None:
    content = (json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _artifact(result: Any) -> dict[str, Any]:
    return {
        "uri": result.path.resolve().as_uri(),
        "records": result.records,
        "logical_bytes": result.logical_bytes,
        "stored_bytes": result.stored_bytes,
        "compression": "gzip",
        "sha256": result.sha256,
    }


def _normalize_source(value: str) -> str:
    return str(value).strip().lower().replace("-", "_")


def resolve_execution_collection_route(
    *,
    profile: ClientProfile,
    period: ReportingPeriod,
    execution_mode: str,
    historical_source_override: str | None,
    compact_repository: CompactSnapshotRepository | None,
    now: datetime | None = None,
) -> tuple[CollectionRoute, CompactFindingSnapshot | None]:
    snapshot = (
        compact_repository.find_exact(
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            period_start_at=period.to_dict()["start_at"],
            period_end_at=period.to_dict()["end_at"],
        )
        if compact_repository is not None else None
    )
    configured = profile.reporting.vm_export
    historical_source = _normalize_source(
        historical_source_override
        or getattr(configured, "historical_source", "legacy")

    )
    route = select_collection_route(
        period=period,
        now=now or datetime.now(UTC),
        execution_mode=execution_mode,
        snapshot_available=snapshot is not None,
        historical_source=historical_source,
        fallback_policy=getattr(configured, "historical_fallback", "warn_legacy"),
    )
    return route, snapshot


def materialize_compact_snapshot_run(
    *,
    snapshot: CompactFindingSnapshot,
    profile: ClientProfile,
    run_id: str,
    output_root: str | Path,
) -> MaterializedReplay:
    if snapshot.client_id != profile.client_id or snapshot.tenant_id != profile.tenant_id:
        raise ValueError("Snapshot compacto nao pertence ao perfil selecionado.")
    replay = replay_compact_snapshot(snapshot)
    root = Path(output_root)
    normalized_directory = root / "normalized" / profile.client_id / run_id
    snapshot_directory = root / "snapshots" / profile.client_id / run_id
    assets_path = normalized_directory / "assets.jsonl.gz"
    findings_path = normalized_directory / "findings.jsonl.gz"
    quality_path = normalized_directory / "quality-issues.jsonl.gz"
    manifest_path = normalized_directory / "manifest.json"

    assets_artifact = write_jsonl_gzip_exclusive(
        assets_path, (item.to_dict() for item in replay.assets)
    )
    findings_artifact = write_jsonl_gzip_exclusive(
        findings_path, (item.to_dict() for item in replay.findings)
    )
    quality_artifact = write_jsonl_gzip_exclusive(
        quality_path, (item.to_dict() for item in replay.quality_issues)
    )

    source_uri = f"compact-snapshot:{snapshot.snapshot_id}"
    asset_payload = json.dumps(
        [item.to_dict() for item in replay.assets],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    finding_payload = json.dumps(
        [item.to_dict() for item in replay.findings],
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    started_at = snapshot.created_at
    asset_source = build_source_snapshot(
        run_id=run_id,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        source="tenable_vm_assets_v2",
        export_uuid=f"replay-{snapshot.snapshot_id[:12]}-assets",
        query={
            "collection_route": "snapshot_replay",
            "source_snapshot_id": snapshot.snapshot_id,
        },
        chunks=[(1, asset_payload)],
        record_count=len(replay.assets),
        started_at=started_at,
        collector_version=__version__,
        raw_manifest_uri=source_uri,
    )
    finding_source = build_source_snapshot(
        run_id=run_id,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        source="tenable_vm_vulnerabilities",
        export_uuid=f"replay-{snapshot.snapshot_id[:12]}-findings",
        query={
            "filters": {"state": ["OPEN", "REOPENED", "FIXED"]},
            "include_plugin_output": any(
                item.plugin_output is not None for item in replay.findings
            ),
            "collection_route": "snapshot_replay",
            "source_snapshot_id": snapshot.snapshot_id,
        },
        chunks=[(1, finding_payload)],
        record_count=len(replay.findings),
        started_at=started_at,
        collector_version=__version__,
        raw_manifest_uri=source_uri,
    )
    asset_snapshot_path = snapshot_directory / "tenable_vm_assets_v2.snapshot.json"
    finding_snapshot_path = snapshot_directory / "tenable_vm_vulnerabilities.snapshot.json"
    asset_source.write_json(asset_snapshot_path)
    finding_source.write_json(finding_snapshot_path)

    if replay.was_findings:
        was_path = normalized_directory / "was-findings.jsonl.gz"
        write_jsonl_gzip_exclusive(
            was_path, (item.to_dict() for item in replay.was_findings)
        )
        was_payload = json.dumps(
            [item.to_dict() for item in replay.was_findings],
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
        was_source = build_source_snapshot(
            run_id=run_id,
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_was_findings",
            export_uuid=f"replay-{snapshot.snapshot_id[:12]}-was",
            query={
                "collection_route": "snapshot_replay",
                "source_snapshot_id": snapshot.snapshot_id,
            },
            chunks=[(1, was_payload)],
            record_count=len(replay.was_findings),
            started_at=started_at,
            collector_version=__version__,
            raw_manifest_uri=source_uri,
        )
        was_source.write_json(snapshot_directory / "tenable_was_findings.snapshot.json")

    if replay.tag_scope is not None:
        tag_scope = {
            **dict(replay.tag_scope),
            "run_id": run_id,
            "client_id": profile.client_id,
            "tenant_id": profile.tenant_id,
            "replayed_from_snapshot_id": snapshot.snapshot_id,
        }
        _write_exclusive(
            snapshot_directory / "tenable_vm_tag_scope.snapshot.json",
            tag_scope,
        )

    provenance = {
        "collection_route": "snapshot_replay",
        "reconstruction_status": "AUTHORITATIVE_SNAPSHOT",
        "source_snapshot_id": snapshot.snapshot_id,
        "source_run_id": snapshot.run_id,
        "sources": ["compact_finding_snapshot"],
        "warning": None,
    }
    manifest = {
        "schema_version": 2,
        "normalizer_version": __version__,
        "run_id": run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "created_at": utc_now_iso(),
        "collection_provenance": provenance,
        "source_snapshots": [
            {"source": asset_source.source, "snapshot_id": asset_source.snapshot_id},
            {"source": finding_source.source, "snapshot_id": finding_source.snapshot_id},
        ],
        "reconciliation": {
            "raw_asset_records": len(replay.assets),
            "normalized_assets": len(replay.assets),
            "rejected_asset_records": 0,
            "duplicate_asset_records": 0,
            "raw_finding_records": len(replay.findings),
            "normalized_findings": len(replay.findings),
            "rejected_finding_records": 0,
            "linked_findings": sum(not item.is_orphan for item in replay.findings),
            "orphan_findings": sum(item.is_orphan for item in replay.findings),
        },
        "quality": {
            "warnings": sum(item.severity.value == "WARNING" for item in replay.quality_issues),
            "errors": sum(item.severity.value == "ERROR" for item in replay.quality_issues),
        },
        "artifacts": {
            "assets": _artifact(assets_artifact),
            "findings": _artifact(findings_artifact),
            "quality_issues": _artifact(quality_artifact),
        },
        "payload_sha256": snapshot.content_sha256,
    }
    _write_exclusive(manifest_path, manifest)
    return MaterializedReplay(
        normalized_manifest_path=manifest_path,
        assets_path=assets_path,
        findings_path=findings_path,
        quality_issues_path=quality_path,
    )
