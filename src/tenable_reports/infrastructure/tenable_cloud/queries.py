"""Versioned GraphQL queries for Tenable Cloud Security.

The required queries intentionally contain only the fields needed to build the
base report. Enrichment is collected through optional, smaller queries so a
schema or permission difference does not hide the essential Cloud result.
"""

from __future__ import annotations

from dataclasses import dataclass


CLOUD_CONNECTOR_VERSION = "cloud-graphql-v1"

_ENDPOINTS = {
    "global": (
        "https://app.tenable.com/graphql",
        "https://app.tenable.com/api/graph",
    ),
    "us_gov": (
        "https://app.tenable.us/graphql",
        "https://app.tenable.us/api/graph",
    ),
}


@dataclass(frozen=True, slots=True)
class CloudQueryDefinition:
    """One independently probeable Cloud GraphQL source."""

    name: str
    root_field: str
    query: str
    required: bool
    page_size: int
    version: str = "v1"


def cloud_endpoint_candidates(environment: str) -> tuple[str, ...]:
    """Return supported endpoints in documented-first compatibility order."""

    normalized = str(environment or "").strip().lower()
    try:
        return _ENDPOINTS[normalized]
    except KeyError as exc:
        raise ValueError(
            f"Cloud environment nao suportado: {environment!r}."
        ) from exc


_VIRTUAL_MACHINES = r"""
query CloudVulnerableVirtualMachines($first: Int, $after: String) {
  VirtualMachines(
    first: $first
    after: $after
    filter: { VulnerabilitySeverities: [Critical, High, Medium, Low] }
  ) {
    nodes {
      Id
      Name
      AccountId
      Software {
        Name
        Vulnerabilities { Id Severity CvssScore VprScore VprSeverity }
      }
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_CONTAINER_IMAGES = r"""
query CloudVulnerableContainerImages($first: Int, $after: String) {
  ContainerImages(
    first: $first
    after: $after
    filter: { VulnerabilitySeverities: [Critical, High, Medium, Low] }
  ) {
    nodes {
      Id
      Name
      AccountId
      Digest
      RepositoryUri
      Software {
        Name
        Vulnerabilities { Id Severity CvssScore VprScore VprSeverity }
      }
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_COMPUTE_IPS = r"""
query CloudComputeAssetIps($first: Int, $after: String) {
  Entities(
    first: $first
    after: $after
    filter: {
      Types: [
        AwsEc2Instance
        AzureComputeVirtualMachine
        AzureComputeVirtualMachineScaleSetVirtualMachine
        GcpComputeInstance
      ]
    }
  ) {
    nodes {
      __typename
      Id
      Name
      AccountId
      ... on AwsEc2Instance {
        PrivateIpAddresses
        NetworkInterfaces { PrivateIpAddresses }
      }
      ... on AzureComputeVirtualMachine {
        PrivateIpAddresses
        PublicIpAddressResources { IpAddress }
      }
      ... on AzureComputeVirtualMachineScaleSetVirtualMachine {
        PrivateIpAddresses
        PublicIpAddressResources { IpAddress }
      }
      ... on GcpComputeInstance { PrivateIpAddresses }
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_INVENTORY = r"""
query CloudInventory($first: Int, $after: String) {
  Entities(first: $first, after: $after) {
    nodes {
      Type: __typename
      Id
      AccountId
      AccountName
      CreationTime
      Labels
      Name
      Provider
      Region
      SyncTime
      Tags { Key Value }
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_FINDINGS = r"""
query CloudSecurityFindings($first: Int, $after: String) {
  Findings(first: $first, after: $after) {
    nodes {
      AccountId
      AccountName
      AccountPath
      CreationTime
      Description
      OpenTime
      Policy { Category Name }
      Provider
      Resources { Id Name }
      Severity
      Status
      SubStatus
      StatusUpdateTime
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_VULNERABILITY_LIFECYCLE = r"""
query CloudVulnerabilityLifecycle($first: Int, $after: String) {
  VulnerabilityInstances(
    first: $first
    after: $after
    filter: { VulnerabilitySeverities: [Critical, High] }
  ) {
    nodes {
      FirstScanTime
      ResolutionTime
      Resolved
      Software { Name }
      Resource { Id Name }
      Vulnerability { Id Severity CvssScore }
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_VULNERABILITY_DETAILS = r"""
query CloudVulnerabilityDetails($first: Int, $after: String) {
  VulnerabilityInstances(
    first: $first
    after: $after
    filter: { VulnerabilitySeverities: [Critical] }
  ) {
    nodes {
      Resource { Id Name }
      Software { Name }
      Vulnerability { Id Severity CvssScore Description }
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()

_VULNERABILITY_REMEDIATIONS = r"""
query CloudVulnerabilityRemediations($first: Int, $after: String) {
  Findings(first: $first, after: $after) {
    nodes {
      AccountId
      AccountName
      Description
      Policy { Category Name }
      Provider
      Remediation { Console { Steps } }
      Resources { Id Name }
      Severity
      Status
    }
    pageInfo { endCursor hasNextPage }
  }
}
""".strip()


CLOUD_SOURCE_QUERIES = {
    "virtual_machines": CloudQueryDefinition(
        name="virtual_machines",
        root_field="VirtualMachines",
        query=_VIRTUAL_MACHINES,
        required=True,
        page_size=50,
    ),
    "container_images": CloudQueryDefinition(
        name="container_images",
        root_field="ContainerImages",
        query=_CONTAINER_IMAGES,
        required=True,
        page_size=50,
    ),
    "compute_ips": CloudQueryDefinition(
        name="compute_ips",
        root_field="Entities",
        query=_COMPUTE_IPS,
        required=False,
        page_size=100,
    ),
    "inventory": CloudQueryDefinition(
        name="inventory",
        root_field="Entities",
        query=_INVENTORY,
        required=False,
        page_size=100,
    ),
    "findings": CloudQueryDefinition(
        name="findings",
        root_field="Findings",
        query=_FINDINGS,
        required=False,
        page_size=100,
    ),
    "vulnerability_lifecycle": CloudQueryDefinition(
        name="vulnerability_lifecycle",
        root_field="VulnerabilityInstances",
        query=_VULNERABILITY_LIFECYCLE,
        required=False,
        page_size=50,
    ),
    "vulnerability_details": CloudQueryDefinition(
        name="vulnerability_details",
        root_field="VulnerabilityInstances",
        query=_VULNERABILITY_DETAILS,
        required=False,
        page_size=20,
    ),
    "vulnerability_remediations": CloudQueryDefinition(
        name="vulnerability_remediations",
        root_field="Findings",
        query=_VULNERABILITY_REMEDIATIONS,
        required=False,
        page_size=50,
    ),
}


__all__ = [
    "CLOUD_CONNECTOR_VERSION",
    "CLOUD_SOURCE_QUERIES",
    "CloudQueryDefinition",
    "cloud_endpoint_candidates",
]
