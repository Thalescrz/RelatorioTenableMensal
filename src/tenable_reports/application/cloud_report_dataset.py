"""Build the auditable dataset consumed by Cloud DOCX renderers."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tenable_reports.application.cloud_enrichment import (
    CloudVulnerabilityEnrichment,
)
from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.domain.cloud import (
    CloudAsset,
    CloudAssetKey,
    CloudAssetKind,
    CloudSoftwareVulnerability,
    NormalizedCloudSnapshot,
)
from tenable_reports.domain.reporting import ReportingPeriod, parse_utc


CLOUD_DATASET_SCHEMA_VERSION = 1
CLOUD_METRIC_DEFINITION_VERSION = "cloud-metrics-v2"

_CORRECTION_LABELS = {
    "patch_update": "Patch/Atualização",
    "version_upgrade": "Upgrade de versão",
    "configuration_change": "Alteração de configuração",
    "remove_replace": "Remoção/Substituição de componente",
    "mitigation": "Mitigação/Contramedida",
    "manual": "Correção manual",
    "undetermined": "Não determinado",
}

_SEVERITY_RANK = {
    "INFORMATIONAL": 0,
    "INFO": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


@dataclass(frozen=True, slots=True)
class CloudReportDatasetArtifact:
    directory: Path
    dataset_path: Path
    sha256: str
    dataset: Mapping[str, Any]


def _score_display(value: float | None) -> str:
    if value is None:
        return "N/D"
    if value == 0:
        return "0"
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _max_optional(first: float | None, second: float | None) -> float | None:
    values = tuple(value for value in (first, second) if value is not None)
    return max(values) if values else None


def _worst_severity(first: str, second: str) -> str:
    return max(
        (first, second),
        key=lambda value: (_SEVERITY_RANK.get(value, -1), value),
    )


def _cve_rank(values: Mapping[str, Any]) -> tuple[Any, ...]:
    vpr = values.get("vpr")
    cvss = values.get("cvss")
    return (
        vpr is None,
        -(float(vpr) if vpr is not None else 0.0),
        -len(values.get("assets") or ()),
        cvss is None,
        -(float(cvss) if cvss is not None else 0.0),
        str(values.get("cve") or ""),
    )


def _asset_row(
    asset: CloudAsset,
    *,
    components: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "kind": asset.key.kind.value,
        "asset_id": asset.key.asset_id,
        "name": asset.name,
        "account_id": asset.account_id,
        "ip_addresses": list(asset.ip_addresses),
        "repository_uri": asset.repository_uri,
        "digest": asset.digest,
        "components": list(components),
    }


def _aggregate_cves(
    snapshot: NormalizedCloudSnapshot,
) -> dict[str, dict[str, Any]]:
    assets = {asset.key: asset for asset in snapshot.assets}
    aggregate: dict[str, dict[str, Any]] = {}
    for occurrence in snapshot.occurrences:
        values = aggregate.setdefault(
            occurrence.vulnerability_id,
            {
                "cve": occurrence.vulnerability_id,
                "severity": occurrence.severity,
                "vpr": None,
                "cvss": None,
                "assets": set(),
                "components": set(),
                "asset_components": {},
                "descriptions": [],
            },
        )
        values["severity"] = _worst_severity(
            values["severity"],
            occurrence.severity,
        )
        values["vpr"] = _max_optional(values["vpr"], occurrence.vpr)
        values["cvss"] = _max_optional(values["cvss"], occurrence.cvss)
        values["assets"].add(occurrence.asset)
        if occurrence.software:
            values["components"].add(occurrence.software)
            values["asset_components"].setdefault(
                occurrence.asset,
                set(),
            ).add(occurrence.software)
        if occurrence.description:
            values["descriptions"].append(occurrence.description)

    for values in aggregate.values():
        values["asset_rows"] = [
            _asset_row(
                assets[key],
                components=tuple(
                    sorted(values["asset_components"].get(key, ()))
                ),
            )
            for key in sorted(values["assets"])
            if key in assets
        ]
    return aggregate


def _cve_row(values: Mapping[str, Any]) -> dict[str, Any]:
    assets = tuple(values.get("assets") or ())
    return {
        "cve": values["cve"],
        "severity": values["severity"],
        "vpr": values["vpr"],
        "vpr_display": _score_display(values["vpr"]),
        "cvss": values["cvss"],
        "cvss_display": _score_display(values["cvss"]),
        "affected_assets": len(assets),
        "affected_virtual_machines": sum(
            key.kind is CloudAssetKind.VIRTUAL_MACHINE for key in assets
        ),
        "affected_container_images": sum(
            key.kind is CloudAssetKind.CONTAINER_IMAGE for key in assets
        ),
        "components": sorted(values.get("components") or ()),
        "description": next(
            iter(dict.fromkeys(values.get("descriptions") or ())),
            None,
        ),
        "assets": list(values.get("asset_rows") or ()),
    }


def _top_assets(
    snapshot: NormalizedCloudSnapshot,
    kind: CloudAssetKind,
) -> list[dict[str, Any]]:
    assets = {asset.key: asset for asset in snapshot.assets}
    grouped: dict[CloudAssetKey, dict[str, Any]] = {}
    for occurrence in snapshot.occurrences:
        if occurrence.asset.kind is not kind:
            continue
        values = grouped.setdefault(
            occurrence.asset,
            {
                "cves": set(),
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
            },
        )
        values["cves"].add(occurrence.vulnerability_id)
        severity_key = occurrence.severity.lower()
        if severity_key in {"critical", "high", "medium", "low"}:
            values[severity_key] += 1

    rows = []
    for key, values in grouped.items():
        asset = assets.get(key)
        if asset is None:
            continue
        rows.append(
            {
                **_asset_row(asset),
                "vulnerabilities": len(values["cves"]),
                "critical": values["critical"],
                "high": values["high"],
                "medium": values["medium"],
                "low": values["low"],
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            -row["vulnerabilities"],
            -row["critical"],
            -row["high"],
            row["name"],
            row["asset_id"],
        ),
    )[:10]


def _software_vulnerabilities(
    snapshot: NormalizedCloudSnapshot,
) -> tuple[CloudSoftwareVulnerability, ...]:
    if snapshot.software_vulnerabilities:
        return snapshot.software_vulnerabilities
    return tuple(
        CloudSoftwareVulnerability(
            asset=item.asset,
            vulnerability_id=item.vulnerability_id,
            severity=item.severity,
            vpr=item.vpr,
            cvss=item.cvss,
            software=item.software,
            fixed_by=None,
        )
        for item in snapshot.occurrences
    )


def _software_vulnerability_rank(
    values: Mapping[str, Any],
) -> tuple[Any, ...]:
    vpr = values.get("vpr")
    cvss = values.get("cvss")
    return (
        vpr is None,
        -(float(vpr) if vpr is not None else 0.0),
        -_SEVERITY_RANK.get(str(values.get("severity") or ""), -1),
        cvss is None,
        -(float(cvss) if cvss is not None else 0.0),
        str(values.get("cve") or ""),
        str(values.get("software") or ""),
    )


def _container_image_vulnerability_overview(
    *,
    snapshot: NormalizedCloudSnapshot,
    top_images: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_asset: dict[str, list[dict[str, Any]]] = {}
    for item in _software_vulnerabilities(snapshot):
        if item.asset.kind is not CloudAssetKind.CONTAINER_IMAGE:
            continue
        by_asset.setdefault(item.asset.asset_id, []).append(
            {
                "cve": item.vulnerability_id,
                "severity": item.severity,
                "vpr": item.vpr,
                "vpr_display": _score_display(item.vpr),
                "cvss": item.cvss,
                "cvss_display": _score_display(item.cvss),
                "software": item.software or "N/D",
                "fixed_by": item.fixed_by,
                "fixed_by_display": item.fixed_by or "N/D",
            }
        )

    overview = []
    for image in top_images[:5]:
        rows = sorted(
            by_asset.get(str(image.get("asset_id") or ""), ()),
            key=_software_vulnerability_rank,
        )[:5]
        overview.append({"asset": dict(image), "rows": rows})
    return overview


def _top_components(
    snapshot: NormalizedCloudSnapshot,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for occurrence in snapshot.occurrences:
        component = occurrence.software or "Não informado"
        values = grouped.setdefault(
            component,
            {"assets": set(), "cves": set(), "occurrences": 0},
        )
        values["assets"].add(occurrence.asset)
        values["cves"].add(occurrence.vulnerability_id)
        values["occurrences"] += 1
    rows = [
        {
            "component": component,
            "affected_assets": len(values["assets"]),
            "vulnerabilities": len(values["cves"]),
            "occurrences": values["occurrences"],
        }
        for component, values in grouped.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -row["affected_assets"],
            -row["vulnerabilities"],
            -row["occurrences"],
            row["component"],
        ),
    )[:10]


def _top_posture(
    snapshot: NormalizedCloudSnapshot,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for finding in snapshot.findings:
        if finding.vulnerability_related:
            continue
        key = (
            finding.policy_name,
            finding.category,
            finding.severity,
            finding.provider or "",
        )
        values = grouped.setdefault(
            key,
            {"findings": 0, "resources": set()},
        )
        values["findings"] += 1
        values["resources"].update(
            item.resource_id for item in finding.resources
        )
    rows = [
        {
            "policy": key[0],
            "category": key[1],
            "severity": key[2],
            "provider": key[3] or None,
            "findings": values["findings"],
            "affected_resources": len(values["resources"]),
        }
        for key, values in grouped.items()
    ]
    return sorted(
        rows,
        key=lambda row: (
            -_SEVERITY_RANK.get(row["severity"], -1),
            -row["findings"],
            row["policy"],
        ),
    )[:10]


def _workload_status(snapshot: NormalizedCloudSnapshot) -> dict[str, Any]:
    virtual_machines = {
        asset.key
        for asset in snapshot.assets
        if asset.key.kind is CloudAssetKind.VIRTUAL_MACHINE
    }
    worst_by_asset = {key: "NONE" for key in virtual_machines}
    for occurrence in snapshot.occurrences:
        if occurrence.asset not in virtual_machines:
            continue
        current = worst_by_asset[occurrence.asset]
        if _SEVERITY_RANK.get(occurrence.severity, -1) > _SEVERITY_RANK.get(
            current,
            -1,
        ):
            worst_by_asset[occurrence.asset] = occurrence.severity
    counts = Counter(worst_by_asset.values())
    return {
        "total_virtual_machines": len(virtual_machines),
        "by_max_severity": {
            severity: counts.get(severity, 0)
            for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE")
        },
    }

def _aging(snapshot: NormalizedCloudSnapshot) -> dict[str, int]:
    buckets = {
        "0-30": 0,
        "31-60": 0,
        "61-90": 0,
        "91-180": 0,
        ">180": 0,
        "data_indisponivel": 0,
    }
    collected_at = parse_utc(snapshot.collected_at)
    for item in snapshot.lifecycle:
        if item.resolved:
            continue
        first_scan = parse_utc(item.first_scan_time)
        if collected_at is None or first_scan is None or first_scan > collected_at:
            buckets["data_indisponivel"] += 1
            continue
        days = (collected_at - first_scan).days
        if days <= 30:
            buckets["0-30"] += 1
        elif days <= 60:
            buckets["31-60"] += 1
        elif days <= 90:
            buckets["61-90"] += 1
        elif days <= 180:
            buckets["91-180"] += 1
        else:
            buckets[">180"] += 1
    return buckets


def _remediation_performance(
    snapshot: NormalizedCloudSnapshot,
    period: ReportingPeriod,
) -> dict[str, Any]:
    resolved = [
        item
        for item in snapshot.lifecycle
        if item.resolved and period.contains(parse_utc(item.resolution_time))
    ]
    durations = []
    for item in resolved:
        first = parse_utc(item.first_scan_time)
        resolution = parse_utc(item.resolution_time)
        if first is not None and resolution is not None and resolution >= first:
            durations.append((resolution - first).total_seconds() / 86400)
    return {
        "resolved": len(resolved),
        "average_resolution_days": (
            round(sum(durations) / len(durations), 1)
            if durations
            else None
        ),
        "period_interval": "[start_at, end_at)",
    }


def _inventory_summary(
    snapshot: NormalizedCloudSnapshot,
) -> dict[str, Any]:
    providers = Counter(
        item.provider or "Não informado" for item in snapshot.inventory
    )
    regions = Counter(
        item.region or "Não informada" for item in snapshot.inventory
    )
    return {
        "total_resources": len(snapshot.inventory),
        "by_provider": [
            {"provider": key, "resources": value}
            for key, value in sorted(
                providers.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "by_region": [
            {"region": key, "resources": value}
            for key, value in sorted(
                regions.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
    }


def _correctable(
    *,
    snapshot: NormalizedCloudSnapshot,
    enrichments: Sequence[CloudVulnerabilityEnrichment],
) -> list[dict[str, Any]]:
    remediation_by_occurrence: dict[
        tuple[CloudAssetKey, str],
        list[CloudVulnerabilityEnrichment],
    ] = {}
    for enrichment in enrichments:
        if not enrichment.remediation_steps:
            continue
        remediation_by_occurrence.setdefault(
            (enrichment.asset, enrichment.cve),
            [],
        ).append(enrichment)

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for item in _software_vulnerabilities(snapshot):
        corrections = remediation_by_occurrence.get(
            (item.asset, item.vulnerability_id),
            (),
        )
        if not item.fixed_by and not corrections:
            continue
        key = (item.vulnerability_id, item.software)
        values = grouped.setdefault(
            key,
            {
                "cve": item.vulnerability_id,
                "software": item.software or "N/D",
                "severity": item.severity,
                "vpr": item.vpr,
                "cvss": item.cvss,
                "assets": set(),
                "fixed_by": set(),
                "steps": [],
                "types": [],
                "origins": [],
                "finding_keys": set(),
            },
        )
        values["severity"] = _worst_severity(
            values["severity"],
            item.severity,
        )
        values["vpr"] = _max_optional(values["vpr"], item.vpr)
        values["cvss"] = _max_optional(values["cvss"], item.cvss)
        values["assets"].add(item.asset)
        if item.fixed_by:
            values["fixed_by"].add(item.fixed_by)
        for correction in corrections:
            values["steps"].extend(correction.remediation_steps)
            values["types"].append(
                correction.correction.correction_type
            )
            values["origins"].append(correction.correction.origin)
            values["finding_keys"].update(
                correction.source_finding_keys
            )

    rows: list[dict[str, Any]] = []
    for correction in grouped.values():
        types = tuple(dict.fromkeys(correction["types"]))
        origins = tuple(dict.fromkeys(correction["origins"]))
        correction_type = (
            types[0]
            if len(types) == 1
            else "version_upgrade"
            if not types and correction["fixed_by"]
            else "undetermined"
        )
        steps = tuple(dict.fromkeys(correction["steps"]))
        fixed_by = tuple(sorted(correction["fixed_by"]))
        rows.append(
            {
                **_cve_row(correction),
                "affected_assets": len(correction["assets"]),
                "software": correction["software"],
                "fixed_by": list(fixed_by),
                "fixed_by_display": ", ".join(fixed_by) or "N/D",
                "correction_type": correction_type,
                "correction_type_display": _CORRECTION_LABELS[
                    correction_type
                ],
                "correction_origin": (
                    origins[0]
                    if len(origins) == 1
                    else "graphql_fixed_by"
                    if not origins and fixed_by
                    else "mixed"
                ),
                "recommended_action": steps[0] if steps else None,
                "remediation_steps": list(steps),
                "correlated_findings": len(correction["finding_keys"]),
            }
        )
    return sorted(
        rows,
        key=lambda row: (*_cve_rank(row), row["software"]),
    )[:10]

def _provenance(
    *,
    snapshot: NormalizedCloudSnapshot,
    period: ReportingPeriod,
) -> dict[str, Any]:
    snapshot_base = {
        "source": "Tenable Cloud Security GraphQL",
        "snapshot_collected_at": snapshot.collected_at,
        "platform_validation_available": True,
    }
    vulnerability_base = {
        **snapshot_base,
        "view": "Cloud Security > Vulnerability Management",
        "platform_filters": {
            "Severity": "Critical, High, Medium, Low",
        },
    }
    return {
        "schema_version": 1,
        "tables": {
            "cloud_overview": {
                **vulnerability_base,
                "rule": (
                    "fotografia GraphQL atual; ocorrências deduplicadas por "
                    "tipo de ativo, Id do recurso e CVE"
                ),
            },
            "cloud_top_critical_cves": {
                **vulnerability_base,
                "group_by": "Vulnerability Id",
                "limit": 5,
                "rule": (
                    "Critical; VPR informado desc, ativos desc, CVSS desc, "
                    "CVE asc"
                ),
            },
            "cloud_top_hosts": {
                **vulnerability_base,
                "platform_filters": {
                    **vulnerability_base["platform_filters"],
                    "Asset type": "Virtual Machine",
                },
                "group_by": "Resource Id",
                "limit": 10,
                "rule": "CVE deduplicada por máquina virtual",
            },
            "cloud_top_images": {
                **vulnerability_base,
                "platform_filters": {
                    **vulnerability_base["platform_filters"],
                    "Asset type": "Container Image",
                },
                "group_by": "Resource Id",
                "limit": 10,
                "rule": "CVE deduplicada por imagem",
            },
            "cloud_container_image_vulnerability_overview": {
                **vulnerability_base,
                "group_by": (
                    "Container Image Id, Vulnerability Id, Software Name"
                ),
                "limit": "5 imagens; 5 combinações CVE/software por imagem",
                "rule": "VPR desc, severidade desc, CVSS desc; FixedBy opcional",
            },
            "cloud_top_components": {
                **vulnerability_base,
                "group_by": "Software Name",
                "limit": 10,
                "rule": "contagem por CVE, componente e recurso",
            },
            "cloud_posture": {
                **snapshot_base,
                "view": "Cloud Security > Findings",
                "platform_filters": {
                    "Finding type": "Configuration, Compliance, Entitlement",
                },
                "group_by": "Policy",
                "rule": "findings de postura; vulnerabilidades excluídas",
            },
            "cloud_aging": {
                **snapshot_base,
                "view": "Cloud Security > Vulnerability Management",
                "rule": "não resolvidas por FirstScanTime até collected_at",
            },
            "cloud_remediation_performance": {
                "source": "Tenable Cloud Security GraphQL",
                "view": "Cloud Security > Vulnerability Management",
                "period_start_at": period.to_dict()["start_at"],
                "period_end_at": period.to_dict()["end_at"],
                "timezone": period.timezone,
                "date_field": "Resolution Time",
                "platform_validation_available": True,
                "rule": "Resolved=true e ResolutionTime em [início, fim)",
            },
            "cloud_top_correctable": {
                **vulnerability_base,
                "view": "Cloud Security > Findings",
                "platform_filters": {
                    "Finding type": (
                        "VirtualMachineOperatingSystemUnpatchedFinding, "
                        "VirtualMachineVulnerabilityFinding"
                    ),
                    "Status": "Open",
                },
                "group_by": "Vulnerability Id, Software Name",
                "limit": 10,
                "rule": (
                    "FixedBy estruturado ou remediação não vazia "
                    "correlacionada ao recurso e à CVE"
                ),
            },
            "cloud_inventory": {
                **snapshot_base,
                "view": "Cloud Security > Inventory",
                "group_by": "Provider, Region",
                "rule": "recurso deduplicado por Entity Id",
            },
        },
    }


def build_cloud_dataset(
    *,
    snapshot: NormalizedCloudSnapshot,
    period: ReportingPeriod,
    enrichments: Sequence[CloudVulnerabilityEnrichment] = (),
    snapshot_is_exact: bool = True,
    connector_version: str = "cloud-graphql-v1",
    capabilities: Mapping[str, Any] | None = None,
    history: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create one canonical dataset for both approved Cloud layouts."""

    cves = _aggregate_cves(snapshot)
    ranked = sorted(cves.values(), key=_cve_rank)
    critical = [
        _cve_row(values)
        for values in ranked
        if values["severity"] == "CRITICAL"
    ][:5]
    severity_counts = Counter(
        item.severity for item in snapshot.occurrences
    )
    warning = None
    historical_reconstruction = "EXACT_SNAPSHOT"
    if not snapshot_is_exact:
        historical_reconstruction = "CURRENT_STATE_ONLY"
        warning = (
            "O período solicitado não possui fotografia Cloud exata; "
            "as tabelas de estado representam a coleta atual."
        )
    top_vulnerable_images = _top_assets(
        snapshot,
        CloudAssetKind.CONTAINER_IMAGE,
    )
    return {
        "schema_version": CLOUD_DATASET_SCHEMA_VERSION,
        "document_kind": "cloud",
        "metric_definition_version": CLOUD_METRIC_DEFINITION_VERSION,
        "connector_version": connector_version,
        "period": period.to_dict(),
        "collected_at": snapshot.collected_at,
        "snapshot_context": {
            "historical_reconstruction": historical_reconstruction,
            "warning": warning,
        },
        "overview": {
            "assets": len(snapshot.assets),
            "virtual_machines": sum(
                item.key.kind is CloudAssetKind.VIRTUAL_MACHINE
                for item in snapshot.assets
            ),
            "container_images": sum(
                item.key.kind is CloudAssetKind.CONTAINER_IMAGE
                for item in snapshot.assets
            ),
            "vulnerability_occurrences": len(snapshot.occurrences),
            "unique_cves": len(cves),
            "severity_counts": {
                severity: severity_counts.get(severity, 0)
                for severity in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
            },
            "posture_findings": sum(
                not item.vulnerability_related
                for item in snapshot.findings
            ),
        },
        "top_critical_cves": critical,
        "top_vulnerable_hosts": _top_assets(
            snapshot,
            CloudAssetKind.VIRTUAL_MACHINE,
        ),
        "top_vulnerable_images": top_vulnerable_images,
        "container_image_vulnerability_overview": (
            _container_image_vulnerability_overview(
                snapshot=snapshot,
                top_images=top_vulnerable_images,
            )
        ),
        "workload_status": _workload_status(snapshot),
        "top_components": _top_components(snapshot),
        "top_posture_findings": _top_posture(snapshot),
        "top_correctable_vulnerabilities": _correctable(
            snapshot=snapshot,
            enrichments=enrichments,
        ),
        "aging": _aging(snapshot),
        "remediation_performance": _remediation_performance(
            snapshot,
            period,
        ),
        "inventory": _inventory_summary(snapshot),
        "source_status": dict(sorted(snapshot.source_status.items())),
        "quality_issues": [
            {
                "code": item.code,
                "source": item.source,
                "message": item.message,
                "record_id": item.record_id,
            }
            for item in snapshot.quality_issues
        ],
        "capabilities": dict(capabilities or {}),
        "history": [dict(item) for item in history],
        "table_provenance": _provenance(
            snapshot=snapshot,
            period=period,
        ),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_cloud_report_dataset(
    *,
    dataset: Mapping[str, Any],
    output_root: str | Path,
    execution_type: str,
    client_id: str,
    run_id: str,
) -> CloudReportDatasetArtifact:
    directory = (
        Path(output_root)
        / execution_type
        / "normalized"
        / client_id
        / run_id
        / "tenable_cloud"
    )
    dataset_path = write_json_atomic(
        directory / "cloud-report-dataset.json",
        dict(dataset),
    )
    return CloudReportDatasetArtifact(
        directory=directory,
        dataset_path=dataset_path,
        sha256=_sha256(dataset_path),
        dataset=load_cloud_report_dataset(dataset_path),
    )


def load_cloud_report_dataset(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Dataset Cloud invalido.") from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != CLOUD_DATASET_SCHEMA_VERSION
        or payload.get("document_kind") != "cloud"
        or payload.get("metric_definition_version")
        != CLOUD_METRIC_DEFINITION_VERSION
    ):
        raise ValueError("Dataset Cloud incompativel.")
    return dict(payload)


__all__ = [
    "CLOUD_DATASET_SCHEMA_VERSION",
    "CLOUD_METRIC_DEFINITION_VERSION",
    "CloudReportDatasetArtifact",
    "build_cloud_dataset",
    "load_cloud_report_dataset",
    "write_cloud_report_dataset",
]
