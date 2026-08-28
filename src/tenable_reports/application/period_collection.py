from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from tenable_reports.application.collect import (
    AssetExportRequest,
    VulnerabilityExportRequest,
    collect_asset_snapshot,
    find_resumable_vm_manifest,
)
from tenable_reports.application.collect_inventory import (
    collect_bounded_historical_findings,
)
from tenable_reports.application.collect_was import (
    WasExportRequest,
    collect_optional_was_snapshot,
)
from tenable_reports.application.historical_materialization import (
    materialize_historical_collection_run,
)
from tenable_reports.application.normalize import _collection_records, normalize_collections
from tenable_reports.application.normalize_was import normalize_was_collection
from tenable_reports.application.tag_scope import VmTag, collect_tag_scope_snapshot
from tenable_reports.application.vm_export_policy import (
    collect_vm_snapshot_with_policy,
    selective_vm_properties,
)
from tenable_reports.application.was_recovery import WasFailureDetails
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.normalization import normalize_assets
from tenable_reports.domain.reporting import ReportingPeriod


@dataclass(frozen=True, slots=True)
class ExternalPeriodCollection:
    normalized: Any
    was_collection_status: str
    vm_export_mode: str
    vm_export_outcome: str
    vm_export_comparison_path: Path | None
    warnings: tuple[Mapping[str, Any], ...]
    collection_route: str
    reconstruction_status: str
    collection_sources: tuple[str, ...]
    was_failure: WasFailureDetails | None = None


def collect_external_period(
    *,
    args: Any,
    profile: ClientProfile,
    period: ReportingPeriod,
    output_root: Path,
    run_id: str,
    client: Any,
    was_client: Any,
    inventory_client: Any,
    selected_tags: Sequence[VmTag],
    asset_filters: Mapping[str, object],
    finding_filters: Mapping[str, object],
    vm_strategy: str,
    vm_num_assets: int,
    vm_selective_mode: str,
    route: Any,
    plugin_catalog: Any = None,
    plugin_catalog_callback: Any = None,
    progress_callback: Any = None,
) -> ExternalPeriodCollection:
    if selected_tags:
        collect_tag_scope_snapshot(
            client=client,
            profile=profile,
            tags=selected_tags,
            output_root=output_root,
            run_id=run_id,
        )
    assets = collect_asset_snapshot(
        client=client,
        profile=profile,
        request=AssetExportRequest(
            filters=asset_filters,
            chunk_size=args.asset_chunk_size,
        ),
        output_root=output_root,
        run_id=run_id,
        export_uuid=getattr(args, "asset_export_uuid", None),
    )

    route_name = route.source.value
    reconstruction_status = route.accuracy.value.upper()
    warnings: list[Mapping[str, Any]] = []
    if route.warning:
        warnings.append({
            "code": "COLLECTION_ROUTE_WARNING",
            "message": route.warning,
        })

    if route_name == "inventory_bounded":
        asset_records = tuple(_collection_records(assets))
        normalized_assets, asset_issues, _, _ = normalize_assets(
            asset_records,
            client_id=profile.client_id,
        )
        assets_by_id = {item.source_asset_id: item for item in normalized_assets}
        historical = collect_bounded_historical_findings(
            inventory_client=inventory_client,
            vm_client=client,
            profile=profile,
            period=period,
            assets_by_id=assets_by_id,
            output_root=output_root,
            run_id=run_id,
            fallback_policy=profile.reporting.vm_export.historical_fallback,
            plugin_catalog=plugin_catalog,
            include_output=bool(args.include_output),
            num_assets=vm_num_assets,
            progress_callback=progress_callback,
        )
        normalized = materialize_historical_collection_run(
            profile=profile,
            run_id=run_id,
            output_root=output_root,
            asset_snapshot=assets.snapshot,
            assets=normalized_assets,
            findings=historical.findings,
            quality_issues=tuple((*asset_issues, *historical.quality_issues)),
            route=historical.route,
            reconstruction_status=reconstruction_status,
            sources=historical.sources,
            source_manifest_uri=historical.manifest_path.resolve().as_uri(),
            include_output=bool(args.include_output),
            warnings=historical.warnings,
        )
        warnings.extend(historical.warnings)
        vm_export_mode = "disabled"
        vm_export_outcome = "INVENTORY_BOUNDED"
        vm_export_comparison_path = None
        collection_route = historical.route
        collection_sources = historical.sources
    else:
        finding_request = VulnerabilityExportRequest(
            filters=finding_filters,
            num_assets=vm_num_assets,
            include_unlicensed=profile.vm_scope.include_unlicensed,
            include_software_vulns=bool(args.include_software_vulns),
            include_plugin_output=bool(args.include_output),
        )
        logical_job_id = getattr(args, "logical_job_id", None)
        resume_manifest = getattr(args, "vm_resume_manifest", None)
        resume_request = (
            replace(
                finding_request,
                properties=selective_vm_properties(
                    include_output=finding_request.include_plugin_output
                ),
            )
            if vm_selective_mode == "enabled"
            else finding_request
        )
        if not resume_manifest and vm_strategy == "combined":
            resume_manifest = find_resumable_vm_manifest(
                output_root,
                profile=profile,
                request=resume_request,
                logical_job_id=logical_job_id,
            )
        vm_policy = collect_vm_snapshot_with_policy(
            client=client,
            profile=profile,
            request=finding_request,
            output_root=output_root,
            run_id=run_id,
            export_uuid=getattr(args, "vm_export_uuid", None),
            resume_from=resume_manifest,
            logical_job_id=logical_job_id,
            mode=vm_selective_mode,
            strategy=vm_strategy,
            plugin_catalog_callback=plugin_catalog_callback,
            progress_callback=progress_callback,
        )
        if vm_policy.outcome == "FALLBACK_FULL":
            warnings.append({
                "code": "VM_SELECTIVE_FALLBACK",
                "reason": vm_policy.fallback_reason,
            })
        elif vm_policy.mode == "validation" and vm_policy.outcome == "FAILED":
            warnings.append({
                "code": "VM_SELECTIVE_VALIDATION_FAILED",
                "comparison": str(vm_policy.comparison_path),
            })
        normalized = normalize_collections(
            profile=profile,
            asset_collection=assets,
            finding_collection=vm_policy.collection,
            output_root=output_root,
            allowed_asset_ids=None,
        )
        vm_export_mode = vm_policy.mode
        vm_export_outcome = vm_policy.outcome
        vm_export_comparison_path = vm_policy.comparison_path
        collection_route = route_name
        collection_sources = (
            "tenable_vm_assets_v2",
            "tenable_vm_vulnerabilities",
        )

    was_collection_status = "DISABLED"
    was_failure = None
    if profile.was_scope.enabled:
        was_attempt = collect_optional_was_snapshot(
            client=was_client,
            profile=profile,
            request=WasExportRequest(
                filters={
                    "since": period.start_epoch,
                    "state": ["OPEN", "REOPENED", "FIXED"],
                    "severity": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    **(
                        {"asset_uuid": list(profile.was_scope.application_ids)}
                        if profile.was_scope.application_ids
                        else {}
                    ),
                },
                num_assets=args.was_num_assets,
                include_unlicensed=False,
            ),
            output_root=output_root,
            run_id=run_id,
            export_uuid=getattr(args, "was_export_uuid", None),
            progress_callback=progress_callback,
        )
        if was_attempt.result is not None:
            normalize_was_collection(
                profile=profile,
                collection=was_attempt.result,
                output_root=output_root,
            )
        was_collection_status = was_attempt.status
        was_failure = was_attempt.failure
        warnings.extend(was_attempt.warnings)

    return ExternalPeriodCollection(
        normalized=normalized,
        was_collection_status=was_collection_status,
        vm_export_mode=vm_export_mode,
        vm_export_outcome=vm_export_outcome,
        vm_export_comparison_path=vm_export_comparison_path,
        warnings=tuple(warnings),
        collection_route=collection_route,
        reconstruction_status=reconstruction_status,
        collection_sources=tuple(collection_sources),
        was_failure=was_failure,
    )
