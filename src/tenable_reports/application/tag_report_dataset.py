from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from tenable_reports.application.report_dataset import load_report_dataset_inputs
from tenable_reports.application.tag_scope import VmTag
from tenable_reports.config.profile import ClientProfile, TagReportSelection
from tenable_reports.domain.normalization import NormalizedAsset, NormalizedFinding
from tenable_reports.domain.report_dataset import ReportDatasetResult, build_report_dataset
from tenable_reports.domain.reporting import ReportingPeriod


_SAFE_TAG_UUID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True, slots=True)
class TagReportDatasetArtifact:
    tag: VmTag
    result: ReportDatasetResult
    directory: Path
    dataset_path: Path


@dataclass(frozen=True, slots=True)
class TagReportDatasetBundle:
    artifacts: tuple[TagReportDatasetArtifact, ...]
    warnings: tuple[dict[str, Any], ...] = ()


def _write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _slice_rows(
    assets: Iterable[NormalizedAsset],
    findings: Iterable[NormalizedFinding],
    asset_ids: frozenset[str],
) -> tuple[tuple[NormalizedAsset, ...], tuple[NormalizedFinding, ...]]:
    selected_assets = tuple(
        item for item in assets if item.source_asset_id in asset_ids
    )
    selected_keys = {item.asset_key for item in selected_assets}
    selected_findings = tuple(
        item for item in findings if item.asset_key in selected_keys
    )
    return selected_assets, selected_findings


def _snapshot_scopes(
    snapshot: Mapping[str, Any] | None,
) -> tuple[dict[str, Mapping[str, Any]], tuple[dict[str, Any], ...]]:
    if snapshot is None:
        return {}, ()
    rows = snapshot.get("selected_tags")
    by_uuid: dict[str, Mapping[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            tag_uuid = str(row.get("uuid") or row.get("tag_uuid") or "").strip()
            if tag_uuid and tag_uuid not in by_uuid:
                by_uuid[tag_uuid] = row
    warning_rows = snapshot.get("warnings")
    warnings = tuple(
        dict(item)
        for item in warning_rows
        if isinstance(warning_rows, list) and isinstance(item, Mapping)
    ) if isinstance(warning_rows, list) else ()
    return by_uuid, warnings


def _vm_tag(selection: TagReportSelection) -> VmTag:
    return VmTag(
        uuid=selection.tag_uuid,
        category_uuid=selection.category_uuid,
        category_name=selection.category_name,
        value=selection.value,
    )


def _missing_scope_warning(selection: TagReportSelection) -> dict[str, Any]:
    return {
        "code": "TAG_SCOPE_UNAVAILABLE",
        "tag_uuid": selection.tag_uuid,
        "tag_label": f"{selection.category_name}: {selection.value}",
        "stage": "tag_report_dataset",
        "message": "A coleta nao retornou um escopo de ativos para esta TAG.",
    }


def build_tag_report_datasets_from_snapshot(
    *,
    profile: ClientProfile,
    run_id: str,
    period: ReportingPeriod,
    output_root: str | Path,
    include_output: bool = False,
    execution_type: str = "UNSPECIFIED",
) -> TagReportDatasetBundle:
    config = profile.report.tag_reports
    selections = tuple(
        item for item in config.tags if config.enabled and item.generate_report
    )
    if not selections:
        return TagReportDatasetBundle(artifacts=())

    inputs = load_report_dataset_inputs(
        profile=profile,
        run_id=run_id,
        output_root=output_root,
    )
    if include_output and not inputs.plugin_output_collected:
        raise ValueError(
            "Plugin Output foi solicitado, mas nao foi coletado no snapshot selecionado."
        )
    if inputs.tag_scope is not None:
        if str(inputs.tag_scope.get("client_id") or profile.client_id) != profile.client_id:
            raise ValueError("O snapshot de TAGs nao pertence ao cliente selecionado.")
        if str(inputs.tag_scope.get("run_id") or run_id) != run_id:
            raise ValueError("O snapshot de TAGs nao pertence ao run_id selecionado.")

    scopes_by_uuid, snapshot_warnings = _snapshot_scopes(inputs.tag_scope)
    warnings = list(snapshot_warnings)
    available: list[tuple[TagReportSelection, Mapping[str, Any]]] = []
    for selection in selections:
        scope = scopes_by_uuid.get(selection.tag_uuid)
        if scope is None:
            if not any(
                str(item.get("tag_uuid") or "") == selection.tag_uuid
                and str(item.get("code") or "") == "TAG_SCOPE_UNAVAILABLE"
                for item in warnings
            ):
                warnings.append(_missing_scope_warning(selection))
            continue
        if not _SAFE_TAG_UUID.fullmatch(selection.tag_uuid):
            raise ValueError(f"UUID de TAG invalido para publicacao: {selection.tag_uuid}.")
        available.append((selection, scope))

    root = Path(output_root)
    base_directory = (
        root
        / "report-datasets"
        / profile.client_id
        / run_id
        / period.period_id
        / "tags"
    )
    paths = {
        selection.tag_uuid: base_directory / selection.tag_uuid / "report-dataset.json"
        for selection, _ in available
    }
    for path in paths.values():
        if path.exists():
            raise FileExistsError(f"Dataset mensal por TAG imutavel ja existe: {path}")

    generated_at = datetime.now(UTC)
    artifacts: list[TagReportDatasetArtifact] = []
    for selection, scope in available:
        source_asset_ids = frozenset(
            str(value).strip()
            for value in (scope.get("asset_ids") or [])
            if str(value).strip()
        )
        assets, findings = _slice_rows(
            inputs.assets,
            inputs.findings,
            source_asset_ids,
        )
        result = build_report_dataset(
            client_id=profile.client_id,
            run_id=run_id,
            execution_type=execution_type,
            period=period,
            assets=assets,
            findings=findings,
            generated_at=generated_at,
            collection_completed_at=inputs.collection_completed_at,
            finding_query=inputs.finding_query,
            include_info_severity=profile.reporting.include_info_severity,
            include_output=include_output,
            top_assets_limit=profile.reporting.top_assets_limit,
            top_vulnerabilities_limit=profile.reporting.top_vulnerabilities_limit,
            late_collection_grace_days=profile.reporting.late_collection_grace_days,
            tag_scope=None,
            was_findings=(),
            was_collected=False,
        )
        tag = _vm_tag(selection)
        payload = result.dataset.to_dict()
        payload["document_kind"] = "tag"
        payload["tag"] = {
            "tag_uuid": selection.tag_uuid,
            "category_uuid": selection.category_uuid,
            "category_name": selection.category_name,
            "value": selection.value,
            "include_temporal_comparison": selection.include_temporal_comparison,
            "source_asset_count": len(source_asset_ids),
            "matched_normalized_asset_count": len(assets),
        }
        payload["tag_selection_provenance"] = {
            "source": "tenable_vm_tag_scope.snapshot.json",
            "match": "NormalizedAsset.source_asset_id in selected TAG asset_ids",
            "general_collection_reused": True,
            "additional_vm_export_performed": False,
        }
        dataset_content = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        dataset_path = paths[selection.tag_uuid]
        _write_exclusive(dataset_path, dataset_content)
        artifacts.append(
            TagReportDatasetArtifact(
                tag=tag,
                result=result,
                directory=dataset_path.parent,
                dataset_path=dataset_path,
            )
        )

    return TagReportDatasetBundle(
        artifacts=tuple(artifacts),
        warnings=tuple(warnings),
    )

