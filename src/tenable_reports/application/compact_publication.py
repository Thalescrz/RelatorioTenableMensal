from __future__ import annotations

from pathlib import Path
from typing import Mapping

from tenable_reports.application.compact_snapshots import (
    CompactFindingSnapshot,
    CompactSnapshotRepository,
    build_compact_snapshot,
)
from tenable_reports.application.history import finalize_compact_snapshot
from tenable_reports.application.report_dataset import load_report_dataset_inputs
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.normalization import DataQualityIssue, QualitySeverity
from tenable_reports.domain.reporting import ReportingPeriod
from tenable_reports.infrastructure.jsonl_io import (
    iter_jsonl_objects,
    resolve_jsonl_artifact,
)


def _quality_issues(directory: Path) -> tuple[DataQualityIssue, ...]:
    try:
        path = resolve_jsonl_artifact(directory, "quality-issues")
    except FileNotFoundError:
        return ()
    return tuple(DataQualityIssue(
        code=str(item.get("code") or ""),
        severity=QualitySeverity(str(item.get("severity") or "WARNING")),
        source=str(item.get("source") or ""),
        record_index=int(item.get("record_index") or 0),
        message=str(item.get("message") or ""),
        source_id=(
            str(item.get("source_id"))
            if item.get("source_id") is not None
            else None
        ),
    ) for item in iter_jsonl_objects(path))


def _tag_asset_ids(tag_scope: Mapping[str, object] | None) -> dict[str, tuple[str, ...]]:
    if not tag_scope:
        return {}
    result: dict[str, tuple[str, ...]] = {}
    selected = tag_scope.get("selected_tags")
    if not isinstance(selected, list):
        return result
    for item in selected:
        if not isinstance(item, Mapping):
            continue
        tag_uuid = str(item.get("uuid") or "").strip()
        asset_ids = item.get("asset_ids")
        if tag_uuid and isinstance(asset_ids, list):
            result[tag_uuid] = tuple(str(value) for value in asset_ids if str(value))
    return result


def prepare_compact_run_snapshot(
    *,
    profile: ClientProfile,
    source_run_id: str,
    snapshot_run_id: str | None = None,
    execution_type: str,
    period: ReportingPeriod,
    output_root: str | Path,
    document_references: Mapping[str, str],
) -> CompactFindingSnapshot:
    references = {
        str(key): str(value)
        for key, value in document_references.items()
        if str(key).strip() and str(value).strip()
    }
    if not references or any(not Path(value).is_file() for value in references.values()):
        raise ValueError("Os documentos publicados precisam existir antes do snapshot.")

    root = Path(output_root)
    inputs = load_report_dataset_inputs(
        profile=profile,
        run_id=source_run_id,
        output_root=root,
    )
    normalized_directory = root / "normalized" / profile.client_id / source_run_id
    return build_compact_snapshot(
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        run_id=snapshot_run_id or source_run_id,
        execution_type=execution_type,
        period_mode=period.mode.value,
        period_start_at=period.to_dict()["start_at"],
        period_end_at=period.to_dict()["end_at"],
        assets=inputs.assets,
        findings=inputs.findings,
        quality_issues=_quality_issues(normalized_directory),
        tag_asset_ids=_tag_asset_ids(inputs.tag_scope),
        tag_scope=inputs.tag_scope,
        was_findings=inputs.was_findings,
        document_references=references,
    )


def publish_compact_run_snapshot(
    *,
    repository: CompactSnapshotRepository,
    profile: ClientProfile,
    run_id: str,
    execution_type: str,
    period: ReportingPeriod,
    output_root: str | Path,
    document_references: Mapping[str, str],
    publication_validated: bool,
    documents_validated: bool,
) -> CompactFindingSnapshot:
    if not publication_validated or not documents_validated:
        raise ValueError("Publicacao e documentos precisam estar confirmados.")
    snapshot = prepare_compact_run_snapshot(
        profile=profile,
        source_run_id=run_id,
        execution_type=execution_type,
        period=period,
        output_root=output_root,
        document_references=document_references,
    )
    finalize_compact_snapshot(
        repository=repository,
        snapshot=snapshot,
        publication_validated=publication_validated,
        documents_validated=documents_validated,
    )
    return snapshot
