"""Select and correlate Cloud vulnerability enrichment safely."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tenable_reports.application.cloud_corrections import (
    CloudCorrectionClassification,
    classify_cloud_correction,
)
from tenable_reports.domain.cloud import (
    CloudAssetKey,
    CloudAssetKind,
    NormalizedCloudSnapshot,
)


@dataclass(frozen=True, slots=True)
class CloudEnrichmentTargets:
    detail_cves: tuple[str, ...]
    remediation_cves: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CloudVulnerabilityEnrichment:
    cve: str
    asset: CloudAssetKey
    description: str | None
    remediation_steps: tuple[str, ...]
    correction: CloudCorrectionClassification
    source_finding_keys: tuple[str, ...]


def _max_optional(first: float | None, second: float | None) -> float | None:
    values = tuple(value for value in (first, second) if value is not None)
    return max(values) if values else None


def _rank_key(item: tuple[str, dict[str, Any]]) -> tuple[Any, ...]:
    cve, values = item
    vpr = values["vpr"]
    cvss = values["cvss"]
    return (
        vpr is None,
        -(vpr if vpr is not None else 0.0),
        -len(values["assets"]),
        cvss is None,
        -(cvss if cvss is not None else 0.0),
        cve,
    )


def _ranked_cves(snapshot: NormalizedCloudSnapshot) -> list[tuple[str, dict[str, Any]]]:
    aggregate: dict[str, dict[str, Any]] = {}
    severity_rank = {
        "INFORMATIONAL": 0,
        "INFO": 0,
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
        "CRITICAL": 4,
    }
    for occurrence in snapshot.occurrences:
        values = aggregate.setdefault(
            occurrence.vulnerability_id,
            {
                "severity": occurrence.severity,
                "vpr": None,
                "cvss": None,
                "assets": set(),
            },
        )
        if severity_rank.get(occurrence.severity, -1) > severity_rank.get(
            values["severity"],
            -1,
        ):
            values["severity"] = occurrence.severity
        values["vpr"] = _max_optional(values["vpr"], occurrence.vpr)
        values["cvss"] = _max_optional(values["cvss"], occurrence.cvss)
        values["assets"].add(occurrence.asset)
    return sorted(aggregate.items(), key=_rank_key)


def select_cloud_enrichment_targets(
    snapshot: NormalizedCloudSnapshot,
) -> CloudEnrichmentTargets:
    """Select only report candidates before requesting heavier fields."""

    ranked = _ranked_cves(snapshot)
    detail_cves = tuple(
        cve
        for cve, values in ranked
        if values["severity"] == "CRITICAL"
    )[:5]
    remediation_cves = tuple(cve for cve, _ in ranked)
    return CloudEnrichmentTargets(
        detail_cves=detail_cves,
        remediation_cves=remediation_cves,
    )


def correlate_cloud_enrichments(
    snapshot: NormalizedCloudSnapshot,
) -> tuple[CloudVulnerabilityEnrichment, ...]:
    """Require an explicit resource+CVE relation for every remediation."""

    occurrence_by_key = {
        (
            occurrence.asset.kind,
            occurrence.asset.asset_id,
            occurrence.vulnerability_id,
        ): occurrence
        for occurrence in snapshot.occurrences
    }
    correlated: dict[
        tuple[str, CloudAssetKey],
        dict[str, Any],
    ] = {}
    open_statuses = {"OPEN", "ACTIVE", "NEW", "REOPENED"}

    for finding in snapshot.findings:
        if (
            not finding.vulnerability_related
            or finding.status not in open_statuses
            or not finding.remediation_steps
        ):
            continue
        for resource in finding.resources:
            asset = CloudAssetKey(
                kind=CloudAssetKind.VIRTUAL_MACHINE,
                asset_id=resource.resource_id,
            )
            for cve in resource.vulnerability_ids:
                occurrence = occurrence_by_key.get(
                    (asset.kind, asset.asset_id, cve)
                )
                if occurrence is None:
                    continue
                key = (cve, asset)
                value = correlated.setdefault(
                    key,
                    {
                        "description": occurrence.description,
                        "steps": [],
                        "finding_keys": [],
                        "explicit_types": [],
                    },
                )
                value["steps"].extend(finding.remediation_steps)
                value["finding_keys"].append(finding.finding_key)
                if finding.explicit_correction_type:
                    value["explicit_types"].append(
                        finding.explicit_correction_type
                    )

    result: list[CloudVulnerabilityEnrichment] = []
    for (cve, asset), value in sorted(
        correlated.items(),
        key=lambda item: (
            item[0][0],
            item[0][1].kind.value,
            item[0][1].asset_id,
        ),
    ):
        steps = tuple(dict.fromkeys(value["steps"]))
        explicit_types = tuple(dict.fromkeys(value["explicit_types"]))
        explicit_type = (
            explicit_types[0] if len(explicit_types) == 1 else None
        )
        correction = classify_cloud_correction(
            " ".join(steps),
            explicit_type=explicit_type,
        )
        result.append(
            CloudVulnerabilityEnrichment(
                cve=cve,
                asset=asset,
                description=value["description"],
                remediation_steps=steps,
                correction=correction,
                source_finding_keys=tuple(
                    dict.fromkeys(value["finding_keys"])
                ),
            )
        )
    return tuple(result)


__all__ = [
    "CloudEnrichmentTargets",
    "CloudVulnerabilityEnrichment",
    "correlate_cloud_enrichments",
    "select_cloud_enrichment_targets",
]
