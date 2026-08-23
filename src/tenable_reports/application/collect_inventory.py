from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from tenable_reports.application.collect import (
    CollectionResult,
    VulnerabilityExportRequest,
    _write_json_replace,
    collect_vm_snapshot_by_state,
    store_chunk_atomic,
)
from tenable_reports.application.normalize import _collection_records
from tenable_reports.application.inventory_resume import load_inventory_resume_state
from tenable_reports.application.plugin_catalog import (
    PluginCatalogRepository,
    enrich_inventory_findings,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.inventory_normalization import normalize_inventory_findings
from tenable_reports.domain.models import utc_now_iso
from tenable_reports.domain.normalization import (
    DataQualityIssue,
    NormalizedAsset,
    NormalizedFinding,
    normalize_findings,
)
from tenable_reports.domain.reporting import ReportingPeriod, iso_utc, parse_utc
from tenable_reports.infrastructure.tenable_inventory.client import (
    InventoryFindingsClient,
)
from tenable_reports.infrastructure.tenable_vm.client import ApiError, TenableVmClient


INVENTORY_EXTRA_PROPERTIES = (
    "finding_detection_id",
    "asset_id",
    "asset_name",
    "asset_ip_address",
    "finding_name",
    "port",
    "protocol",
    "service",
    "first_observed_at",
    "last_observed_at",
    "last_fixed_at",
    "resurfaced_at",
    "cves",
    "cvss2_base_score",
    "cvss3_base_score",
    "cvss3_vector",
    "vpr_score",
    "description",
    "solution",
    "output",
    "exploit_available",
    "exploit_frameworks",
)


@dataclass(frozen=True, slots=True)
class HistoricalCollectionBundle:
    findings: tuple[NormalizedFinding, ...]
    quality_issues: tuple[DataQualityIssue, ...]
    route: str
    manifest_path: Path
    warnings: tuple[Mapping[str, Any], ...]
    sources: tuple[str, ...]


def _in_period(period: ReportingPeriod, value: str | None) -> bool:
    return period.contains(parse_utc(value))


def _legacy_in_period(
    findings: Sequence[NormalizedFinding],
    period: ReportingPeriod,
) -> tuple[NormalizedFinding, ...]:
    return tuple(
        item for item in findings
        if _in_period(
            period,
            item.last_fixed_at if item.state == "FIXED" else item.last_found_at,
        )
    )


def _deduplicate(
    findings: Sequence[NormalizedFinding],
) -> tuple[NormalizedFinding, ...]:
    unique: dict[str, NormalizedFinding] = {}
    for finding in findings:
        unique.setdefault(finding.finding_key, finding)
    order = {"OPEN": 0, "REOPENED": 1, "FIXED": 2}
    return tuple(sorted(
        unique.values(),
        key=lambda item: (order.get(item.state, 9), item.finding_key),
    ))


def _property_names(properties: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    return frozenset(
        str(item.get("name") or "").strip()
        for item in properties
        if str(item.get("name") or "").strip()
    )


def _collect_inventory_segment(
    *,
    client: InventoryFindingsClient,
    profile: ClientProfile,
    period: ReportingPeriod,
    state: str,
    segment: str,
    extra_properties: Sequence[str],
    output_root: str | Path,
    run_id: str,
    page_size: int,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
) -> tuple[dict[str, Any], ...]:
    filters = [
        {"field": "state", "operator": "eq", "value": state},
        {
            "field": "last_observed_at",
            "operator": "between",
            "value": [iso_utc(period.start_at), iso_utc(period.end_at)],
        },
    ]
    directory = (
        Path(output_root)
        / "raw"
        / profile.client_id
        / run_id
        / "tenable_inventory_findings"
        / segment
    )
    partial_manifest = directory / "manifest.partial.json"
    manifest_path = directory / "manifest.json"
    resume = load_inventory_resume_state(
        final_manifest=manifest_path,
        partial_manifest=partial_manifest,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        run_id=run_id,
        segment=segment,
        filters=filters,
    )
    chunks = list(resume.chunks)
    records: list[dict[str, Any]] = list(resume.records)
    offset = len(records)

    def emit(status: str, **details: Any) -> None:
        if progress_callback is not None:
            progress_callback({
                "event": "TENABLE_INVENTORY_PROGRESS",
                "source": "tenable_inventory_findings",
                "segment": segment,
                "status": status,
                "run_id": run_id,
                **details,
            })

    emit("STARTED", offset=offset, records=len(records), resumed=bool(records))
    if resume.complete:
        return tuple(records)
    while True:
        page = client.search_page(
            filters=filters,
            extra_properties=extra_properties,
            offset=offset,
            limit=page_size,
            sort="severity:desc",
        )
        page_records = tuple(dict(item) for item in page.findings)
        if page_records:
            payload = b"".join(
                (json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
                for item in page_records
            )
            stored = store_chunk_atomic(
                directory,
                (payload,),
                chunk_id=len(chunks) + 1,
            )
            chunks.append(stored.to_manifest())
            records.extend(page_records)
            _write_json_replace(partial_manifest, {
                "schema_version": 1,
                "source": "tenable_inventory_findings",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "run_id": run_id,
                "segment": segment,
                "status": "PROCESSING",
                "filters": filters,
                "extra_properties": list(extra_properties),
                "chunks": chunks,
                "updated_at": utc_now_iso(),
            })
        next_offset = offset + len(page_records)
        emit(
            "PROCESSING",
            offset=next_offset,
            records=len(records),
            total=page.total,
            pages=len(chunks),
        )
        if not page_records:
            break
        if page.total is not None and next_offset >= page.total:
            break
        if len(page_records) < page_size:
            break
        if next_offset <= offset:
            raise ApiError("Paginacao Inventory nao avancou durante a coleta historica.")
        offset = next_offset

    _write_json_replace(manifest_path, {
        "schema_version": 1,
        "source": "tenable_inventory_findings",
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "run_id": run_id,
        "segment": segment,
        "status": "FINISHED",
        "filters": filters,
        "extra_properties": list(extra_properties),
        "chunks": chunks,
        "records": len(records),
        "updated_at": utc_now_iso(),
    })
    partial_manifest.unlink(missing_ok=True)
    emit("FINISHED", offset=len(records), records=len(records), pages=len(chunks))
    return tuple(records)


def _legacy_collection(
    *,
    vm_client: TenableVmClient,
    profile: ClientProfile,
    states: Sequence[str],
    period: ReportingPeriod,
    output_root: str | Path,
    run_id: str,
    include_output: bool,
    num_assets: int,
    legacy_collector: Callable[..., CollectionResult],
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
    segment: str,
) -> CollectionResult:
    if progress_callback is not None:
        progress_callback({"segment": segment, "status": "STARTED"})

    def forward(event: Mapping[str, Any]) -> None:
        if progress_callback is not None:
            progress_callback({**event, "segment": segment})

    result = legacy_collector(
        client=vm_client,
        profile=profile,
        request=VulnerabilityExportRequest(
            filters={
                "severity": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                "state": list(states),
                "since": period.start_epoch,
            },
            num_assets=num_assets,
            include_unlicensed=profile.vm_scope.include_unlicensed,
            include_plugin_output=include_output,
        ),
        output_root=output_root,
        run_id=run_id,
        strategy="combined",
        progress_callback=forward,
        snapshot_suffix=segment,
    )
    if progress_callback is not None:
        progress_callback({"segment": segment, "status": "FINISHED"})
    return result


def _normalize_legacy(
    collection: CollectionResult,
    *,
    profile: ClientProfile,
    assets_by_id: Mapping[str, NormalizedAsset],
    period: ReportingPeriod,
) -> tuple[tuple[NormalizedFinding, ...], tuple[DataQualityIssue, ...]]:
    findings, issues, _ = normalize_findings(
        _collection_records(collection),
        client_id=profile.client_id,
        assets_by_id=assets_by_id,
    )
    return _legacy_in_period(findings, period), issues


def collect_bounded_historical_findings(
    *,
    inventory_client: InventoryFindingsClient,
    vm_client: TenableVmClient,
    profile: ClientProfile,
    period: ReportingPeriod,
    assets_by_id: Mapping[str, NormalizedAsset],
    output_root: str | Path,
    run_id: str,
    fallback_policy: str,
    plugin_catalog: PluginCatalogRepository | None = None,
    include_output: bool = False,
    num_assets: int = 1000,
    page_size: int = 1000,
    legacy_collector: Callable[..., CollectionResult] = collect_vm_snapshot_by_state,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> HistoricalCollectionBundle:
    if fallback_policy not in {"fail", "warn_legacy"}:
        raise ValueError("fallback_policy deve ser fail ou warn_legacy.")
    if not 1 <= int(page_size) <= 10000:
        raise ValueError("page_size deve estar entre 1 e 10000.")
    manifest_path = (
        Path(output_root)
        / "raw"
        / profile.client_id
        / run_id
        / "historical-collection-manifest.json"
    )

    try:
        properties = inventory_client.list_properties()
        names = _property_names(properties)
        required = {"state", "last_observed_at"}
        if not required.issubset(names):
            missing = ", ".join(sorted(required - names))
            raise ApiError(f"Inventory API nao oferece propriedades obrigatorias: {missing}")
        extra_properties = tuple(
            name for name in INVENTORY_EXTRA_PROPERTIES
            if not names or name in names
        )
        inventory_by_segment = {
            "inventory_active": _collect_inventory_segment(
                client=inventory_client,
                profile=profile,
                period=period,
                state="ACTIVE",
                segment="inventory_active",
                extra_properties=extra_properties,
                output_root=output_root,
                run_id=run_id,
                page_size=page_size,
                progress_callback=progress_callback,
            ),
            "inventory_resurfaced": _collect_inventory_segment(
                client=inventory_client,
                profile=profile,
                period=period,
                state="RESURFACED",
                segment="inventory_resurfaced",
                extra_properties=extra_properties,
                output_root=output_root,
                run_id=run_id,
                page_size=page_size,
                progress_callback=progress_callback,
            ),
        }
    except ApiError as exc:
        if fallback_policy == "fail":
            raise
        legacy = _legacy_collection(
            vm_client=vm_client,
            profile=profile,
            states=("OPEN", "REOPENED", "FIXED"),
            period=period,
            output_root=output_root,
            run_id=run_id,
            include_output=include_output,
            num_assets=num_assets,
            legacy_collector=legacy_collector,
            progress_callback=progress_callback,
            segment="legacy_fallback",
        )
        findings, issues = _normalize_legacy(
            legacy,
            profile=profile,
            assets_by_id=assets_by_id,
            period=period,
        )
        warning = {
            "code": "INVENTORY_UNAVAILABLE_LEGACY_FALLBACK",
            "message": str(exc),
        }
        _write_json_replace(manifest_path, {
            "schema_version": 1,
            "route": "legacy_historical_fallback",
            "client_id": profile.client_id,
            "tenant_id": profile.tenant_id,
            "run_id": run_id,
            "period": period.to_dict(),
            "sources": ["tenable_vm_vulnerabilities"],
            "counts": {"legacy": len(findings)},
            "warnings": [warning],
            "created_at": utc_now_iso(),
        })
        return HistoricalCollectionBundle(
            findings=findings,
            quality_issues=issues,
            route="legacy_historical_fallback",
            manifest_path=manifest_path,
            warnings=(warning,),
            sources=("tenable_vm_vulnerabilities",),
        )

    inventory_findings: list[NormalizedFinding] = []
    inventory_issues: list[DataQualityIssue] = []
    counts: dict[str, int] = {}
    for segment, records in inventory_by_segment.items():
        normalized, issues, _ = normalize_inventory_findings(
            records,
            client_id=profile.client_id,
            assets_by_id=assets_by_id,
        )
        bounded = tuple(
            item for item in normalized if _in_period(period, item.last_found_at)
        )
        if plugin_catalog is not None:
            bounded, catalog_issues = enrich_inventory_findings(
                bounded,
                client_id=profile.client_id,
                tenant_id=profile.tenant_id,
                repository=plugin_catalog,
            )
            issues = tuple((*issues, *catalog_issues))
        inventory_findings.extend(bounded)
        inventory_issues.extend(issues)
        counts[segment] = len(bounded)

    fixed_collection = _legacy_collection(
        vm_client=vm_client,
        profile=profile,
        states=("FIXED",),
        period=period,
        output_root=output_root,
        run_id=run_id,
        include_output=include_output,
        num_assets=num_assets,
        legacy_collector=legacy_collector,
        progress_callback=progress_callback,
        segment="legacy_fixed",
    )
    fixed, fixed_issues = _normalize_legacy(
        fixed_collection,
        profile=profile,
        assets_by_id=assets_by_id,
        period=period,
    )
    counts["legacy_fixed"] = len(fixed)
    findings = _deduplicate((*inventory_findings, *fixed))
    issues = tuple((*inventory_issues, *fixed_issues))
    warning = {
        "code": "HISTORICAL_RECONSTRUCTION",
        "message": (
            "ACTIVE e RESURFACED foram delimitados pelo Inventory; FIXED usa "
            "export VM legado com recorte superior local."
        ),
    }
    _write_json_replace(manifest_path, {
        "schema_version": 1,
        "route": "inventory_bounded_hybrid",
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "run_id": run_id,
        "period": period.to_dict(),
        "sources": ["tenable_inventory_findings", "tenable_vm_vulnerabilities"],
        "counts": counts,
        "quality_issue_codes": sorted({item.code for item in issues}),
        "warnings": [warning],
        "created_at": utc_now_iso(),
    })
    return HistoricalCollectionBundle(
        findings=findings,
        quality_issues=issues,
        route="inventory_bounded_hybrid",
        manifest_path=manifest_path,
        warnings=(warning,),
        sources=("tenable_inventory_findings", "tenable_vm_vulnerabilities"),
    )
