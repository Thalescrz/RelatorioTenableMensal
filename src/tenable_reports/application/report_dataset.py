from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from tenable_reports import __version__
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.normalization import NormalizedAsset, NormalizedFinding
from tenable_reports.domain.report_dataset import ReportDatasetResult, build_report_dataset
from tenable_reports.domain.reporting import ReportingPeriod, parse_utc
from tenable_reports.domain.was import NormalizedWasFinding
from tenable_reports.application.tag_scope import read_tag_scope_snapshot
from tenable_reports.application.current_intelligence import build_current_intelligence
from tenable_reports.infrastructure.jsonl_io import (
    iter_jsonl_objects,
    resolve_jsonl_artifact,
)


@dataclass(frozen=True, slots=True)
class ReportDatasetArtifact:
    result: ReportDatasetResult
    directory: Path
    dataset_path: Path
    manifest_path: Path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Nao foi possivel ler o artefato: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON invalido no artefato {path}, linha {exc.lineno}.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"O artefato precisa conter um objeto JSON: {path}")
    return value


def _read_jsonl(path: Path, factory: Any) -> tuple[Any, ...]:
    return tuple(factory(value) for value in iter_jsonl_objects(path))


def _write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _artifact(path: Path, content: bytes) -> dict[str, Any]:
    return {
        "uri": path.resolve().as_uri(),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def build_report_dataset_from_snapshot(
    *,
    profile: ClientProfile,
    run_id: str,
    period: ReportingPeriod,
    output_root: str | Path,
    include_output: bool = False,
    execution_type: str = "UNSPECIFIED",
) -> ReportDatasetArtifact:
    root = Path(output_root)
    normalized_directory = root / "normalized" / profile.client_id / run_id
    normalized_manifest_path = normalized_directory / "manifest.json"
    normalized_manifest = _read_json(normalized_manifest_path)
    if normalized_manifest.get("client_id") != profile.client_id:
        raise ValueError("O snapshot normalizado nao pertence ao cliente selecionado.")
    if normalized_manifest.get("run_id") != run_id:
        raise ValueError("O snapshot normalizado nao pertence ao run_id selecionado.")

    assets = _read_jsonl(
        resolve_jsonl_artifact(normalized_directory, "assets"),
        NormalizedAsset.from_dict,
    )
    findings = _read_jsonl(
        resolve_jsonl_artifact(normalized_directory, "findings"),
        NormalizedFinding.from_dict,
    )
    try:
        was_findings_path = resolve_jsonl_artifact(
            normalized_directory, "was-findings"
        )
    except FileNotFoundError:
        was_findings = ()
    else:
        was_findings = _read_jsonl(was_findings_path, NormalizedWasFinding.from_dict)
    snapshot_directory = root / "snapshots" / profile.client_id / run_id
    finding_snapshot_path = snapshot_directory / "tenable_vm_vulnerabilities.snapshot.json"
    finding_snapshot = _read_json(finding_snapshot_path)
    asset_snapshot_path = snapshot_directory / "tenable_vm_assets_v2.snapshot.json"
    asset_snapshot = _read_json(asset_snapshot_path)
    was_snapshot_path = snapshot_directory / "tenable_was_findings.snapshot.json"
    was_snapshot = _read_json(was_snapshot_path) if was_snapshot_path.is_file() else None

    finding_query = finding_snapshot.get("query")
    if not isinstance(finding_query, Mapping):
        finding_query = {}
    plugin_output_collected = bool(finding_query.get("include_plugin_output", False))
    if include_output and not plugin_output_collected:
        raise ValueError(
            "Plugin Output foi solicitado, mas nao foi coletado no snapshot selecionado."
        )
    completed_dates = tuple(
        value for value in (
            parse_utc(str(asset_snapshot.get("completed_at") or "")),
            parse_utc(str(finding_snapshot.get("completed_at") or "")),
            parse_utc(str(was_snapshot.get("completed_at") or "")) if was_snapshot else None,
        )
        if value is not None
    )
    if not completed_dates:
        raise ValueError("Snapshots de origem nao possuem completed_at valido.")
    collection_completed_at = max(completed_dates)
    generated_at = datetime.now(UTC)
    tag_scope_path = snapshot_directory / "tenable_vm_tag_scope.snapshot.json"
    tag_scope = read_tag_scope_snapshot(tag_scope_path) if tag_scope_path.is_file() else None
    result = build_report_dataset(
        client_id=profile.client_id,
        run_id=run_id,
        execution_type=execution_type,
        period=period,
        assets=assets,
        findings=findings,
        generated_at=generated_at,
        collection_completed_at=collection_completed_at,
        finding_query=finding_query,
        include_info_severity=profile.reporting.include_info_severity,
        include_output=include_output,
        top_assets_limit=profile.reporting.top_assets_limit,
        top_vulnerabilities_limit=profile.reporting.top_vulnerabilities_limit,
        late_collection_grace_days=profile.reporting.late_collection_grace_days,
        tag_scope=tag_scope,
        was_findings=was_findings,
        was_collected=was_snapshot is not None,
    )
    intelligence = build_current_intelligence(
        assets=result.observed_assets,
        findings=result.included_findings,
        was_findings=was_findings,
        period=period,
        open_collected=bool(result.dataset.source_coverage["open_metrics_collected"]),
        fixed_collected=bool(result.dataset.source_coverage["fixed_metrics_collected"]),
        was_collected=was_snapshot is not None,
    )
    customizations = dict(result.dataset.customizations or {})
    customizations.update(intelligence.data)
    customizations["customization_statuses"] = intelligence.statuses
    customizations["customization_provenance"] = intelligence.provenance
    result = replace(
        result,
        dataset=replace(result.dataset, customizations=customizations),
    )

    directory = root / "report-datasets" / profile.client_id / run_id / period.period_id
    dataset_path = directory / "report-dataset.json"
    manifest_path = directory / "manifest.json"
    for path in (dataset_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"Dataset mensal imutavel ja existe: {path}")
    dataset_content = (
        json.dumps(result.dataset.to_dict(), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    _write_exclusive(dataset_path, dataset_content)
    manifest = {
        "schema_version": 1,
        "builder_version": __version__,
        "client_id": profile.client_id,
        "run_id": run_id,
        "execution_type": execution_type,
        "period": period.to_dict(),
        "normalized_manifest": {
            "uri": normalized_manifest_path.resolve().as_uri(),
            "sha256": hashlib.sha256(normalized_manifest_path.read_bytes()).hexdigest(),
        },
        "source_snapshots": [
            {
                "source": "tenable_vm_assets_v2",
                "snapshot_id": asset_snapshot.get("snapshot_id"),
                "uri": asset_snapshot_path.resolve().as_uri(),
            },
            {
                "source": "tenable_vm_vulnerabilities",
                "snapshot_id": finding_snapshot.get("snapshot_id"),
                "uri": finding_snapshot_path.resolve().as_uri(),
            },
            *([{
                "source": "tenable_was_findings",
                "snapshot_id": was_snapshot.get("snapshot_id"),
                "uri": was_snapshot_path.resolve().as_uri(),
            }] if was_snapshot else []),
        ],
        "tag_scope_snapshot": (
            {
                "uri": tag_scope_path.resolve().as_uri(),
                "selected_tag_count": len(tag_scope.get("selected_tags") or []),
                "selected_asset_count": tag_scope.get("selected_asset_count"),
            }
            if tag_scope else None
        ),
        "population_reconciliation": result.dataset.populations,
        "collection_timing": result.dataset.collection_timing,
        "artifact": _artifact(dataset_path, dataset_content),
    }
    _write_exclusive(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return ReportDatasetArtifact(
        result=result,
        directory=directory,
        dataset_path=dataset_path,
        manifest_path=manifest_path,
    )
