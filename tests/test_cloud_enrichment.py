from __future__ import annotations

import importlib
from typing import Any


def _module():
    return importlib.import_module(
        "tenable_reports.application.cloud_enrichment"
    )


def _domain():
    return importlib.import_module("tenable_reports.domain.cloud")


def _asset(asset_id: str, *, image: bool = False) -> Any:
    domain = _domain()
    kind = (
        domain.CloudAssetKind.CONTAINER_IMAGE
        if image
        else domain.CloudAssetKind.VIRTUAL_MACHINE
    )
    key = domain.CloudAssetKey(kind=kind, asset_id=asset_id)
    return domain.CloudAsset(key=key, name=f"fixture-{asset_id}", account_id=None)


def _occurrence(
    asset: Any,
    cve: str,
    *,
    severity: str = "CRITICAL",
    vpr: float | None = 9.0,
    cvss: float | None = 9.8,
    description: str | None = None,
) -> Any:
    domain = _domain()
    return domain.CloudVulnerabilityOccurrence(
        asset=asset.key,
        vulnerability_id=cve,
        severity=severity,
        vpr=vpr,
        cvss=cvss,
        software="fixture-component",
        description=description,
    )


def _snapshot(
    assets: tuple[Any, ...],
    occurrences: tuple[Any, ...],
    findings: tuple[Any, ...] = (),
) -> Any:
    domain = _domain()
    return domain.NormalizedCloudSnapshot(
        collected_at="2026-08-26T12:00:00+00:00",
        assets=assets,
        occurrences=occurrences,
        findings=findings,
        inventory=(),
        lifecycle=(),
        source_status={},
        quality_issues=(),
    )


def test_enrichment_targets_limit_description_to_top_five() -> None:
    assets = tuple(_asset(f"vm-{index}") for index in range(6))
    occurrences = tuple(
        _occurrence(
            asset,
            f"CVE-2026-{index:04d}",
            vpr=float(10 - index),
        )
        for index, asset in enumerate(assets)
    )

    targets = _module().select_cloud_enrichment_targets(
        _snapshot(assets, occurrences)
    )

    assert len(targets.detail_cves) == 5
    assert targets.detail_cves == tuple(
        f"CVE-2026-{index:04d}" for index in range(5)
    )
    assert len(targets.remediation_cves) == 6


def test_remediation_requires_same_resource_and_cve() -> None:
    domain = _domain()
    vm_a = _asset("vm-a")
    vm_b = _asset("vm-b")
    occurrence_a = _occurrence(
        vm_a,
        "CVE-2026-0001",
        description="Documented fixture description.",
    )
    occurrence_b = _occurrence(vm_b, "CVE-2026-0002")
    resource = domain.CloudResourceReference(
        resource_id="vm-a",
        name="fixture-vm-a",
        vulnerability_ids=("CVE-2026-0001",),
    )
    finding = domain.CloudFinding(
        finding_key="finding-fixture",
        account_id=None,
        account_name=None,
        category="Vulnerability",
        policy_name="Fixture vulnerability policy",
        provider="AWS",
        severity="CRITICAL",
        status="OPEN",
        description=None,
        creation_time=None,
        open_time=None,
        status_update_time=None,
        resources=(resource,),
        remediation_steps=("Apply the vendor security patch.",),
        vulnerability_related=True,
    )

    enrichments = _module().correlate_cloud_enrichments(
        _snapshot(
            (vm_a, vm_b),
            (occurrence_a, occurrence_b),
            (finding,),
        )
    )

    assert len(enrichments) == 1
    enrichment = enrichments[0]
    assert enrichment.cve == "CVE-2026-0001"
    assert enrichment.asset == vm_a.key
    assert enrichment.description == "Documented fixture description."
    assert enrichment.correction.correction_type == "patch_update"


def test_generic_finding_with_remediation_is_not_correlated() -> None:
    domain = _domain()
    vm = _asset("vm-a")
    occurrence = _occurrence(vm, "CVE-2026-0001")
    generic = domain.CloudFinding(
        finding_key="generic-fixture",
        account_id=None,
        account_name=None,
        category="Configuration",
        policy_name="Generic policy",
        provider="AWS",
        severity="HIGH",
        status="OPEN",
        description=None,
        creation_time=None,
        open_time=None,
        status_update_time=None,
        resources=(
            domain.CloudResourceReference(
                resource_id="vm-a",
                name="fixture-vm-a",
            ),
        ),
        remediation_steps=("Disable anonymous access.",),
        vulnerability_related=False,
    )

    assert _module().correlate_cloud_enrichments(
        _snapshot((vm,), (occurrence,), (generic,))
    ) == ()
