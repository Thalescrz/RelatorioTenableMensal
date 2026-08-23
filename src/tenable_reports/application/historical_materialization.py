from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tenable_reports import __version__
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import SourceSnapshot, build_source_snapshot, utc_now_iso
from tenable_reports.domain.normalization import (
    DataQualityIssue,
    NormalizedAsset,
    NormalizedFinding,
)
from tenable_reports.infrastructure.jsonl_io import write_jsonl_gzip_exclusive


@dataclass(frozen=True, slots=True)
class MaterializedHistoricalRun:
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


def materialize_historical_collection_run(
    *,
    profile: ClientProfile,
    run_id: str,
    output_root: str | Path,
    asset_snapshot: SourceSnapshot,
    assets: Sequence[NormalizedAsset],
    findings: Sequence[NormalizedFinding],
    quality_issues: Sequence[DataQualityIssue],
    route: str,
    reconstruction_status: str,
    sources: Sequence[str],
    source_manifest_uri: str,
    include_output: bool,
    warnings: Sequence[Mapping[str, Any]] = (),
) -> MaterializedHistoricalRun:
    """Converge a rota híbrida para o contrato normalizado já usado pelos relatórios."""
    if asset_snapshot.run_id != run_id:
        raise ValueError("Snapshot de ativos nao pertence ao run_id selecionado.")
    if (
        asset_snapshot.client_id != profile.client_id
        or asset_snapshot.tenant_id != profile.tenant_id
    ):
        raise ValueError("Snapshot de ativos nao pertence ao perfil selecionado.")

    normalized_directory = (
        Path(output_root) / "normalized" / profile.client_id / run_id
    )
    snapshot_directory = Path(output_root) / "snapshots" / profile.client_id / run_id
    assets_path = normalized_directory / "assets.jsonl.gz"
    findings_path = normalized_directory / "findings.jsonl.gz"
    quality_path = normalized_directory / "quality-issues.jsonl.gz"
    manifest_path = normalized_directory / "manifest.json"

    ordered_assets = tuple(sorted(assets, key=lambda item: item.source_asset_id))
    ordered_findings = tuple(sorted(findings, key=lambda item: item.finding_key))
    ordered_issues = tuple(sorted(
        quality_issues,
        key=lambda item: (item.source, item.record_index, item.code, item.source_id or ""),
    ))
    assets_artifact = write_jsonl_gzip_exclusive(
        assets_path, (item.to_dict() for item in ordered_assets)
    )
    findings_artifact = write_jsonl_gzip_exclusive(
        findings_path, (item.to_dict() for item in ordered_findings)
    )
    quality_artifact = write_jsonl_gzip_exclusive(
        quality_path, (item.to_dict() for item in ordered_issues)
    )

    asset_snapshot_path = snapshot_directory / "tenable_vm_assets_v2.snapshot.json"
    if not asset_snapshot_path.exists():
        asset_snapshot.write_json(asset_snapshot_path)
    finding_payload = b"".join(
        (
            json.dumps(item.to_dict(), ensure_ascii=False, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        for item in ordered_findings
    )
    finding_source = build_source_snapshot(
        run_id=run_id,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        source="tenable_vm_vulnerabilities",
        export_uuid=f"historical-{run_id}",
        query={
            "filters": {"state": ["OPEN", "REOPENED", "FIXED"]},
            "include_plugin_output": include_output,
            "collection_route": route,
            "reconstruction_status": reconstruction_status,
            "sources": list(sources),
        },
        chunks=[(1, finding_payload)],
        record_count=len(ordered_findings),
        started_at=asset_snapshot.started_at,
        collector_version=__version__,
        raw_manifest_uri=source_manifest_uri,
    )
    finding_source.write_json(
        snapshot_directory / "tenable_vm_vulnerabilities.snapshot.json"
    )

    provenance = {
        "collection_route": route,
        "reconstruction_status": reconstruction_status,
        "sources": list(sources),
        "source_manifest_uri": source_manifest_uri,
        "warnings": [dict(item) for item in warnings],
    }
    raw_asset_records = max(asset_snapshot.record_count, len(ordered_assets))
    manifest = {
        "schema_version": 2,
        "normalizer_version": __version__,
        "run_id": run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "created_at": utc_now_iso(),
        "collection_provenance": provenance,
        "source_snapshots": [
            {
                "source": asset_snapshot.source,
                "snapshot_id": asset_snapshot.snapshot_id,
                "raw_sha256": asset_snapshot.raw_sha256,
                "record_count": asset_snapshot.record_count,
            },
            {
                "source": finding_source.source,
                "snapshot_id": finding_source.snapshot_id,
                "raw_sha256": finding_source.raw_sha256,
                "record_count": finding_source.record_count,
            },
        ],
        "reconciliation": {
            "raw_asset_records": raw_asset_records,
            "normalized_assets": len(ordered_assets),
            "rejected_asset_records": raw_asset_records - len(ordered_assets),
            "duplicate_asset_records": 0,
            "raw_finding_records": len(ordered_findings),
            "normalized_findings": len(ordered_findings),
            "rejected_finding_records": 0,
            "linked_findings": sum(not item.is_orphan for item in ordered_findings),
            "orphan_findings": sum(item.is_orphan for item in ordered_findings),
        },
        "quality": {
            "warnings": sum(
                item.severity.value == "WARNING" for item in ordered_issues
            ),
            "errors": sum(item.severity.value == "ERROR" for item in ordered_issues),
        },
        "artifacts": {
            "assets": _artifact(assets_artifact),
            "findings": _artifact(findings_artifact),
            "quality_issues": _artifact(quality_artifact),
        },
    }
    _write_exclusive(manifest_path, manifest)
    return MaterializedHistoricalRun(
        normalized_manifest_path=manifest_path,
        assets_path=assets_path,
        findings_path=findings_path,
        quality_issues_path=quality_path,
    )
