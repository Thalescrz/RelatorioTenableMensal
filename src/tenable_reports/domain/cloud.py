"""Pure domain types for normalized Tenable Cloud Security data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class CloudAssetKind(StrEnum):
    VIRTUAL_MACHINE = "virtual_machine"
    CONTAINER_IMAGE = "container_image"


@dataclass(frozen=True, slots=True, order=True)
class CloudAssetKey:
    kind: CloudAssetKind
    asset_id: str


@dataclass(frozen=True, slots=True)
class CloudAsset:
    key: CloudAssetKey
    name: str
    account_id: str | None
    digest: str | None = None
    repository_uri: str | None = None
    ip_addresses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CloudVulnerabilityOccurrence:
    asset: CloudAssetKey
    vulnerability_id: str
    severity: str
    vpr: float | None
    cvss: float | None
    software: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class CloudResourceReference:
    resource_id: str
    name: str


@dataclass(frozen=True, slots=True)
class CloudFinding:
    finding_key: str
    account_id: str | None
    account_name: str | None
    category: str
    policy_name: str
    provider: str | None
    severity: str
    status: str
    description: str | None
    creation_time: str | None
    open_time: str | None
    status_update_time: str | None
    resources: tuple[CloudResourceReference, ...]
    remediation_steps: tuple[str, ...]
    vulnerability_related: bool


@dataclass(frozen=True, slots=True)
class CloudInventoryResource:
    resource_id: str
    resource_type: str
    name: str
    account_id: str | None
    account_name: str | None
    provider: str | None
    region: str | None
    creation_time: str | None
    sync_time: str | None
    tags: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class CloudLifecycleInstance:
    resource_id: str
    resource_name: str
    vulnerability_id: str
    severity: str
    cvss: float | None
    software: str
    first_scan_time: str | None
    resolution_time: str | None
    resolved: bool


@dataclass(frozen=True, slots=True)
class CloudQualityIssue:
    code: str
    source: str
    message: str
    record_id: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedCloudSnapshot:
    collected_at: str
    assets: tuple[CloudAsset, ...]
    occurrences: tuple[CloudVulnerabilityOccurrence, ...]
    findings: tuple[CloudFinding, ...]
    inventory: tuple[CloudInventoryResource, ...]
    lifecycle: tuple[CloudLifecycleInstance, ...]
    source_status: Mapping[str, str]
    quality_issues: tuple[CloudQualityIssue, ...]


__all__ = [
    "CloudAsset",
    "CloudAssetKey",
    "CloudAssetKind",
    "CloudFinding",
    "CloudInventoryResource",
    "CloudLifecycleInstance",
    "CloudQualityIssue",
    "CloudResourceReference",
    "CloudVulnerabilityOccurrence",
    "NormalizedCloudSnapshot",
]
