"""Normalize raw Tenable Cloud Security sources into a stable contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TYPE_CHECKING

from tenable_reports.domain.cloud import (
    CloudAsset,
    CloudAssetKey,
    CloudAssetKind,
    CloudFinding,
    CloudInventoryResource,
    CloudLifecycleInstance,
    CloudQualityIssue,
    CloudResourceReference,
    CloudVulnerabilityOccurrence,
    NormalizedCloudSnapshot,
)
from tenable_reports.infrastructure.jsonl_io import iter_jsonl_objects

if TYPE_CHECKING:
    from tenable_reports.application.collect_cloud import CloudCollectionArtifact


_SEVERITY_RANK = {
    "UNKNOWN": -1,
    "INFO": 0,
    "INFORMATIONAL": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
    "CRITICAL": 4,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _optional_text(value: Any) -> str | None:
    result = _text(value)
    return result or None


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _severity(value: Any) -> str:
    normalized = _text(value).upper()
    aliases = {
        "CRIT": "CRITICAL",
        "CRITICA": "CRITICAL",
        "ALTA": "HIGH",
        "MEDIA": "MEDIUM",
        "BAIXA": "LOW",
    }
    return aliases.get(normalized, normalized or "UNKNOWN")


def _worse_severity(first: str, second: str) -> str:
    return max(
        (first, second),
        key=lambda value: (_SEVERITY_RANK.get(value, -1), value),
    )


def _max_number(first: float | None, second: float | None) -> float | None:
    available = tuple(value for value in (first, second) if value is not None)
    return max(available) if available else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _mappings(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        normalized = value.strip()
        return (normalized,) if normalized else ()
    if not isinstance(value, Sequence) or isinstance(value, bytes):
        return ()
    return tuple(
        normalized
        for item in value
        if (normalized := _text(item))
    )


def _issue(
    issues: list[CloudQualityIssue],
    *,
    code: str,
    source: str,
    message: str,
    record_id: str | None = None,
) -> None:
    issues.append(
        CloudQualityIssue(
            code=code,
            source=source,
            message=message,
            record_id=record_id,
        )
    )


def _merge_asset(
    current: CloudAsset | None,
    candidate: CloudAsset,
) -> CloudAsset:
    if current is None:
        return candidate
    return CloudAsset(
        key=current.key,
        name=current.name or candidate.name,
        account_id=current.account_id or candidate.account_id,
        digest=current.digest or candidate.digest,
        repository_uri=current.repository_uri or candidate.repository_uri,
        ip_addresses=tuple(
            sorted(set(current.ip_addresses) | set(candidate.ip_addresses))
        ),
    )


def _merge_occurrence(
    current: CloudVulnerabilityOccurrence | None,
    candidate: CloudVulnerabilityOccurrence,
) -> CloudVulnerabilityOccurrence:
    if current is None:
        return candidate
    return CloudVulnerabilityOccurrence(
        asset=current.asset,
        vulnerability_id=current.vulnerability_id,
        severity=_worse_severity(current.severity, candidate.severity),
        vpr=_max_number(current.vpr, candidate.vpr),
        cvss=_max_number(current.cvss, candidate.cvss),
        software=current.software or candidate.software,
        description=current.description or candidate.description,
    )


def _normalize_assets(
    sources: Mapping[str, Iterable[Mapping[str, Any]]],
    issues: list[CloudQualityIssue],
) -> tuple[
    dict[CloudAssetKey, CloudAsset],
    dict[tuple[CloudAssetKind, str, str], CloudVulnerabilityOccurrence],
]:
    assets: dict[CloudAssetKey, CloudAsset] = {}
    occurrences: dict[
        tuple[CloudAssetKind, str, str],
        CloudVulnerabilityOccurrence,
    ] = {}
    source_kinds = (
        ("virtual_machines", CloudAssetKind.VIRTUAL_MACHINE),
        ("container_images", CloudAssetKind.CONTAINER_IMAGE),
    )
    for source, kind in source_kinds:
        for raw in sources.get(source, ()):
            asset_id = _text(raw.get("Id"))
            if not asset_id:
                _issue(
                    issues,
                    code="MISSING_ASSET_ID",
                    source=source,
                    message="Recurso Cloud ignorado por nao possuir Id.",
                )
                continue
            key = CloudAssetKey(kind=kind, asset_id=asset_id)
            candidate_asset = CloudAsset(
                key=key,
                name=_text(raw.get("Name")),
                account_id=_optional_text(raw.get("AccountId")),
                digest=_optional_text(raw.get("Digest")),
                repository_uri=_optional_text(raw.get("RepositoryUri")),
            )
            assets[key] = _merge_asset(assets.get(key), candidate_asset)

            for software in _mappings(raw.get("Software")):
                software_name = _text(software.get("Name"))
                for vulnerability in _mappings(software.get("Vulnerabilities")):
                    vulnerability_id = _text(vulnerability.get("Id"))
                    if not vulnerability_id:
                        _issue(
                            issues,
                            code="MISSING_VULNERABILITY_ID",
                            source=source,
                            message=(
                                "Vulnerabilidade Cloud ignorada por nao possuir Id."
                            ),
                            record_id=asset_id,
                        )
                        continue
                    occurrence = CloudVulnerabilityOccurrence(
                        asset=key,
                        vulnerability_id=vulnerability_id,
                        severity=_severity(vulnerability.get("Severity")),
                        vpr=_number(vulnerability.get("VprScore")),
                        cvss=_number(vulnerability.get("CvssScore")),
                        software=software_name,
                        description=_optional_text(
                            vulnerability.get("Description")
                        ),
                    )
                    occurrence_key = (kind, asset_id, vulnerability_id)
                    occurrences[occurrence_key] = _merge_occurrence(
                        occurrences.get(occurrence_key),
                        occurrence,
                    )
    return assets, occurrences


def _extract_ips(raw: Mapping[str, Any]) -> tuple[str, ...]:
    addresses = set(_strings(raw.get("PrivateIpAddresses")))
    for interface in _mappings(raw.get("NetworkInterfaces")):
        addresses.update(_strings(interface.get("PrivateIpAddresses")))
    for resource in _mappings(raw.get("PublicIpAddressResources")):
        address = _optional_text(resource.get("IpAddress"))
        if address:
            addresses.add(address)
    return tuple(sorted(addresses))


def _enrich_compute_ips(
    *,
    assets: dict[CloudAssetKey, CloudAsset],
    records: Iterable[Mapping[str, Any]],
    issues: list[CloudQualityIssue],
) -> None:
    for raw in records:
        asset_id = _text(raw.get("Id"))
        key = CloudAssetKey(
            kind=CloudAssetKind.VIRTUAL_MACHINE,
            asset_id=asset_id,
        )
        if not asset_id or key not in assets:
            _issue(
                issues,
                code="ORPHAN_COMPUTE_METADATA",
                source="compute_ips",
                message="Metadado de rede Cloud sem VM correspondente por Id.",
                record_id=asset_id or None,
            )
            continue
        assets[key] = replace(
            assets[key],
            ip_addresses=tuple(
                sorted(set(assets[key].ip_addresses) | set(_extract_ips(raw)))
            ),
        )


def _enrich_descriptions(
    *,
    occurrences: dict[
        tuple[CloudAssetKind, str, str],
        CloudVulnerabilityOccurrence,
    ],
    records: Iterable[Mapping[str, Any]],
    issues: list[CloudQualityIssue],
) -> None:
    for raw in records:
        resource = _mapping(raw.get("Resource"))
        vulnerability = _mapping(raw.get("Vulnerability"))
        resource_id = _text(resource.get("Id"))
        vulnerability_id = _text(vulnerability.get("Id"))
        description = _optional_text(vulnerability.get("Description"))
        if not resource_id or not vulnerability_id or not description:
            continue
        matching = [
            key
            for key in occurrences
            if key[1] == resource_id and key[2] == vulnerability_id
        ]
        if len(matching) == 1:
            key = matching[0]
            occurrences[key] = replace(
                occurrences[key],
                description=occurrences[key].description or description,
            )
        elif len(matching) > 1:
            _issue(
                issues,
                code="AMBIGUOUS_VULNERABILITY_DETAIL",
                source="vulnerability_details",
                message=(
                    "Detalhe Cloud nao associado porque o Id existe em mais "
                    "de um tipo de ativo."
                ),
                record_id=resource_id,
            )
        else:
            _issue(
                issues,
                code="ORPHAN_VULNERABILITY_DETAIL",
                source="vulnerability_details",
                message="Detalhe Cloud sem ocorrencia correspondente por Id.",
                record_id=resource_id,
            )


def _resource_references(value: Any) -> tuple[CloudResourceReference, ...]:
    references: dict[str, CloudResourceReference] = {}
    for item in _mappings(value):
        resource_id = _text(item.get("Id"))
        if not resource_id:
            continue
        vulnerability_ids = tuple(
            sorted(
                {
                    vulnerability_id
                    for vulnerability in _mappings(
                        item.get("Vulnerabilities")
                    )
                    if (
                        vulnerability_id := _text(
                            vulnerability.get("Id")
                        )
                    )
                }
            )
        )
        candidate = CloudResourceReference(
            resource_id=resource_id,
            name=_text(item.get("Name")),
            vulnerability_ids=vulnerability_ids,
        )
        current = references.get(resource_id)
        if current is None:
            references[resource_id] = candidate
        else:
            references[resource_id] = CloudResourceReference(
                resource_id=resource_id,
                name=current.name or candidate.name,
                vulnerability_ids=tuple(
                    sorted(
                        set(current.vulnerability_ids)
                        | set(candidate.vulnerability_ids)
                    )
                ),
            )
    return tuple(references[key] for key in sorted(references))


def _remediation_steps(raw: Mapping[str, Any]) -> tuple[str, ...]:
    remediation = _mapping(raw.get("Remediation"))
    console = _mapping(remediation.get("Console"))
    return _strings(console.get("Steps"))


def _explicit_correction_type(raw: Mapping[str, Any]) -> str | None:
    remediation = _mapping(raw.get("Remediation"))
    return _optional_text(
        remediation.get("Type") or raw.get("CorrectionType")
    )

def _finding_key(
    *,
    raw: Mapping[str, Any],
    resources: Sequence[CloudResourceReference],
) -> str:
    policy = _mapping(raw.get("Policy"))
    identity = {
        "account": _text(raw.get("AccountId")),
        "category": _text(policy.get("Category")),
        "policy": _text(policy.get("Name")),
        "resources": [
            {
                "id": item.resource_id,
                "cves": list(item.vulnerability_ids),
            }
            for item in resources
        ],
        "severity": _severity(raw.get("Severity")),
        "status": _text(raw.get("Status")).upper(),
    }
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _normalize_findings(
    sources: Mapping[str, Iterable[Mapping[str, Any]]],
) -> tuple[CloudFinding, ...]:
    findings: dict[str, CloudFinding] = {}
    for source, vulnerability_related in (
        ("findings", False),
        ("vulnerability_remediations", True),
    ):
        for raw in sources.get(source, ()):
            policy = _mapping(raw.get("Policy"))
            resources = _resource_references(raw.get("Resources"))
            key = _finding_key(raw=raw, resources=resources)
            candidate = CloudFinding(
                finding_key=key,
                account_id=_optional_text(raw.get("AccountId")),
                account_name=_optional_text(raw.get("AccountName")),
                category=_text(policy.get("Category")),
                policy_name=_text(policy.get("Name")),
                provider=_optional_text(raw.get("Provider")),
                severity=_severity(raw.get("Severity")),
                status=_text(raw.get("Status")).upper(),
                description=_optional_text(raw.get("Description")),
                creation_time=_optional_text(raw.get("CreationTime")),
                open_time=_optional_text(raw.get("OpenTime")),
                status_update_time=_optional_text(raw.get("StatusUpdateTime")),
                resources=resources,
                remediation_steps=_remediation_steps(raw),
                vulnerability_related=vulnerability_related,
                explicit_correction_type=_explicit_correction_type(raw),
            )
            current = findings.get(key)
            if current is None:
                findings[key] = candidate
            else:
                findings[key] = replace(
                    current,
                    description=current.description or candidate.description,
                    remediation_steps=tuple(
                        dict.fromkeys(
                            current.remediation_steps
                            + candidate.remediation_steps
                        )
                    ),
                    vulnerability_related=(
                        current.vulnerability_related
                        or candidate.vulnerability_related
                    ),
                    explicit_correction_type=(
                        current.explicit_correction_type
                        or candidate.explicit_correction_type
                    ),
                )
    return tuple(findings[key] for key in sorted(findings))


def _normalize_inventory(
    records: Iterable[Mapping[str, Any]],
    issues: list[CloudQualityIssue],
) -> tuple[CloudInventoryResource, ...]:
    inventory: dict[str, CloudInventoryResource] = {}
    for raw in records:
        resource_id = _text(raw.get("Id"))
        if not resource_id:
            _issue(
                issues,
                code="MISSING_INVENTORY_ID",
                source="inventory",
                message="Recurso de inventario Cloud ignorado por nao possuir Id.",
            )
            continue
        tags = tuple(
            sorted(
                {
                    (_text(item.get("Key")), _text(item.get("Value")))
                    for item in _mappings(raw.get("Tags"))
                    if _text(item.get("Key"))
                }
            )
        )
        inventory.setdefault(
            resource_id,
            CloudInventoryResource(
                resource_id=resource_id,
                resource_type=_text(
                    raw.get("Type") or raw.get("__typename")
                ),
                name=_text(raw.get("Name")),
                account_id=_optional_text(raw.get("AccountId")),
                account_name=_optional_text(raw.get("AccountName")),
                provider=_optional_text(raw.get("Provider")),
                region=_optional_text(raw.get("Region")),
                creation_time=_optional_text(raw.get("CreationTime")),
                sync_time=_optional_text(raw.get("SyncTime")),
                tags=tags,
            ),
        )
    return tuple(inventory[key] for key in sorted(inventory))


def _normalize_lifecycle(
    records: Iterable[Mapping[str, Any]],
    issues: list[CloudQualityIssue],
) -> tuple[CloudLifecycleInstance, ...]:
    lifecycle: dict[tuple[str, str], CloudLifecycleInstance] = {}
    for raw in records:
        resource = _mapping(raw.get("Resource"))
        vulnerability = _mapping(raw.get("Vulnerability"))
        software = _mapping(raw.get("Software"))
        resource_id = _text(resource.get("Id"))
        vulnerability_id = _text(vulnerability.get("Id"))
        if not resource_id or not vulnerability_id:
            _issue(
                issues,
                code="INCOMPLETE_LIFECYCLE_IDENTITY",
                source="vulnerability_lifecycle",
                message="Ciclo de vida Cloud ignorado por identidade incompleta.",
                record_id=resource_id or None,
            )
            continue
        key = (resource_id, vulnerability_id)
        candidate = CloudLifecycleInstance(
            resource_id=resource_id,
            resource_name=_text(resource.get("Name")),
            vulnerability_id=vulnerability_id,
            severity=_severity(vulnerability.get("Severity")),
            cvss=_number(vulnerability.get("CvssScore")),
            software=_text(software.get("Name")),
            first_scan_time=_optional_text(raw.get("FirstScanTime")),
            resolution_time=_optional_text(raw.get("ResolutionTime")),
            resolved=bool(raw.get("Resolved")),
        )
        current = lifecycle.get(key)
        if current is None:
            lifecycle[key] = candidate
        else:
            lifecycle[key] = replace(
                current,
                severity=_worse_severity(
                    current.severity,
                    candidate.severity,
                ),
                cvss=_max_number(current.cvss, candidate.cvss),
                resolution_time=(
                    current.resolution_time or candidate.resolution_time
                ),
                resolved=current.resolved or candidate.resolved,
            )
    return tuple(lifecycle[key] for key in sorted(lifecycle))


def _normalized_status(
    source_status: Mapping[str, Any] | None,
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, value in (source_status or {}).items():
        if isinstance(value, Mapping):
            status = value.get("status")
        else:
            status = getattr(value, "status", value)
        normalized[str(name)] = _text(status).upper()
    return normalized


def normalize_cloud_sources(
    *,
    collected_at: str,
    sources: Mapping[str, Iterable[Mapping[str, Any]]],
    source_status: Mapping[str, Any] | None = None,
) -> NormalizedCloudSnapshot:
    """Normalize source records without joining by names or network addresses."""

    materialized = {
        name: tuple(dict(item) for item in records)
        for name, records in sources.items()
    }
    issues: list[CloudQualityIssue] = []
    assets, occurrences = _normalize_assets(materialized, issues)
    _enrich_compute_ips(
        assets=assets,
        records=materialized.get("compute_ips", ()),
        issues=issues,
    )
    _enrich_descriptions(
        occurrences=occurrences,
        records=materialized.get("vulnerability_details", ()),
        issues=issues,
    )
    return NormalizedCloudSnapshot(
        collected_at=_text(collected_at),
        assets=tuple(assets[key] for key in sorted(assets)),
        occurrences=tuple(
            occurrences[key] for key in sorted(occurrences)
        ),
        findings=_normalize_findings(materialized),
        inventory=_normalize_inventory(
            materialized.get("inventory", ()),
            issues,
        ),
        lifecycle=_normalize_lifecycle(
            materialized.get("vulnerability_lifecycle", ()),
            issues,
        ),
        source_status=_normalized_status(source_status),
        quality_issues=tuple(issues),
    )


def normalize_cloud_artifact(
    artifact: "CloudCollectionArtifact",
    *,
    collected_at: str,
) -> NormalizedCloudSnapshot:
    sources = {
        name: tuple(iter_jsonl_objects(Path(path)))
        for name, path in artifact.source_paths.items()
    }
    return normalize_cloud_sources(
        collected_at=collected_at,
        sources=sources,
        source_status=artifact.source_status,
    )


__all__ = [
    "normalize_cloud_artifact",
    "normalize_cloud_sources",
]
