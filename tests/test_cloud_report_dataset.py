from __future__ import annotations

import importlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tenable_reports.domain.reporting import explicit_reporting_period


def _dataset_module():
    return importlib.import_module(
        "tenable_reports.application.cloud_report_dataset"
    )


def _domain():
    return importlib.import_module("tenable_reports.domain.cloud")


def _enrichment_module():
    return importlib.import_module(
        "tenable_reports.application.cloud_enrichment"
    )


def _asset(asset_id: str, *, image: bool = False) -> Any:
    domain = _domain()
    kind = (
        domain.CloudAssetKind.CONTAINER_IMAGE
        if image
        else domain.CloudAssetKind.VIRTUAL_MACHINE
    )
    key = domain.CloudAssetKey(kind=kind, asset_id=asset_id)
    return domain.CloudAsset(
        key=key,
        name=f"fixture-{asset_id}",
        account_id="account-fixture",
        digest="sha256:fixture" if image else None,
        repository_uri="registry.invalid/fixture" if image else None,
    )


def _occurrence(
    asset: Any,
    cve: str,
    *,
    severity: str = "CRITICAL",
    vpr: float | None = 9.0,
    cvss: float | None = 9.8,
    software: str = "fixture-component",
) -> Any:
    domain = _domain()
    return domain.CloudVulnerabilityOccurrence(
        asset=asset.key,
        vulnerability_id=cve,
        severity=severity,
        vpr=vpr,
        cvss=cvss,
        software=software,
        description=f"Description for {cve}.",
    )


def _period() -> Any:
    return explicit_reporting_period(
        start_at="2026-07-01T00:00:00Z",
        end_at="2026-08-01T00:00:00Z",
        reference_at="2026-08-26T00:00:00Z",
        timezone_name="UTC",
    )


def _snapshot(
    *,
    assets: tuple[Any, ...] = (),
    occurrences: tuple[Any, ...] = (),
    findings: tuple[Any, ...] = (),
    inventory: tuple[Any, ...] = (),
    lifecycle: tuple[Any, ...] = (),
    source_status: dict[str, str] | None = None,
) -> Any:
    domain = _domain()
    return domain.NormalizedCloudSnapshot(
        collected_at="2026-08-26T12:00:00+00:00",
        assets=assets,
        occurrences=occurrences,
        findings=findings,
        inventory=inventory,
        lifecycle=lifecycle,
        source_status=source_status or {},
        quality_issues=(),
    )


def test_top_critical_keeps_missing_vpr_after_scored_cves() -> None:
    scored = _asset("vm-scored")
    zero = _asset("vm-zero")
    missing = _asset("vm-missing")
    snapshot = _snapshot(
        assets=(scored, zero, missing),
        occurrences=(
            _occurrence(scored, "CVE-2026-0001", vpr=8.0),
            _occurrence(zero, "CVE-2026-0002", vpr=0.0),
            _occurrence(missing, "CVE-2026-0003", vpr=None),
        ),
    )

    dataset = _dataset_module().build_cloud_dataset(
        snapshot=snapshot,
        period=_period(),
    )
    rows = dataset["top_critical_cves"]

    assert [row["cve"] for row in rows] == [
        "CVE-2026-0001",
        "CVE-2026-0002",
        "CVE-2026-0003",
    ]
    assert rows[1]["vpr"] == 0.0
    assert rows[1]["vpr_display"] == "0"
    assert rows[-1]["vpr"] is None
    assert rows[-1]["vpr_display"] == "N/D"


def test_top_correctable_requires_correlated_remediation() -> None:
    domain = _domain()
    enrichment_module = _enrichment_module()
    correlated_asset = _asset("vm-a")
    generic_asset = _asset("vm-b")
    correlated = _occurrence(correlated_asset, "CVE-2099-1000")
    generic = _occurrence(generic_asset, "CVE-2099-2000")
    correction = importlib.import_module(
        "tenable_reports.application.cloud_corrections"
    ).classify_cloud_correction("Apply the vendor security patch.")
    enrichment = enrichment_module.CloudVulnerabilityEnrichment(
        cve=correlated.vulnerability_id,
        asset=correlated.asset,
        description=correlated.description,
        remediation_steps=("Apply the vendor security patch.",),
        correction=correction,
        source_finding_keys=("finding-correlated",),
    )
    generic_finding = domain.CloudFinding(
        finding_key="generic",
        account_id=None,
        account_name=None,
        category="Configuration",
        policy_name="Generic finding",
        provider=None,
        severity="CRITICAL",
        status="OPEN",
        description=None,
        creation_time=None,
        open_time=None,
        status_update_time=None,
        resources=(),
        remediation_steps=("Apply an update.",),
        vulnerability_related=False,
    )

    dataset = _dataset_module().build_cloud_dataset(
        snapshot=_snapshot(
            assets=(correlated_asset, generic_asset),
            occurrences=(correlated, generic),
            findings=(generic_finding,),
        ),
        period=_period(),
        enrichments=(enrichment,),
    )

    assert [
        row["cve"]
        for row in dataset["top_correctable_vulnerabilities"]
    ] == ["CVE-2099-1000"]


def test_resolved_metrics_use_exclusive_period_end() -> None:
    domain = _domain()
    lifecycle = tuple(
        domain.CloudLifecycleInstance(
            resource_id=f"vm-{index}",
            resource_name=f"fixture-{index}",
            vulnerability_id=f"CVE-2026-{index:04d}",
            severity="CRITICAL",
            cvss=9.8,
            software="fixture-component",
            first_scan_time="2026-06-01T00:00:00Z",
            resolution_time=resolution,
            resolved=True,
        )
        for index, resolution in enumerate(
            (
                "2026-07-01T00:00:00Z",
                "2026-08-01T00:00:00Z",
            )
        )
    )

    dataset = _dataset_module().build_cloud_dataset(
        snapshot=_snapshot(lifecycle=lifecycle),
        period=_period(),
    )

    assert dataset["remediation_performance"]["resolved"] == 1


def test_historical_period_without_exact_snapshot_discloses_current_state() -> None:
    dataset = _dataset_module().build_cloud_dataset(
        snapshot=_snapshot(),
        period=_period(),
        snapshot_is_exact=False,
    )

    context = dataset["snapshot_context"]
    assert context["historical_reconstruction"] == "CURRENT_STATE_ONLY"
    assert context["warning"]


def test_top_hosts_images_components_and_sources_are_materialized() -> None:
    vm = _asset("vm-a")
    image = _asset("image-a", image=True)
    snapshot = _snapshot(
        assets=(vm, image),
        occurrences=(
            _occurrence(vm, "CVE-2026-0001", software="component-a"),
            _occurrence(vm, "CVE-2026-0002", software="component-a"),
            _occurrence(image, "CVE-2026-0001", software="component-b"),
        ),
        source_status={
            "virtual_machines": "COMPLETE",
            "findings": "UNAVAILABLE",
        },
    )

    dataset = _dataset_module().build_cloud_dataset(
        snapshot=snapshot,
        period=_period(),
    )

    assert dataset["top_vulnerable_hosts"][0]["asset_id"] == "vm-a"
    assert dataset["top_vulnerable_hosts"][0]["vulnerabilities"] == 2
    assert dataset["top_vulnerable_images"][0]["asset_id"] == "image-a"
    assert dataset["top_components"][0]["component"] == "component-a"
    assert dataset["source_status"]["findings"] == "UNAVAILABLE"
    assert "cloud_top_hosts" in dataset["table_provenance"]["tables"]


def test_workload_status_uses_each_virtual_machine_worst_severity() -> None:
    critical = _asset("vm-critical")
    high = _asset("vm-high")
    clean = _asset("vm-without-occurrence")
    image = _asset("image-critical", image=True)
    dataset = _dataset_module().build_cloud_dataset(
        snapshot=_snapshot(
            assets=(critical, high, clean, image),
            occurrences=(
                _occurrence(critical, "CVE-2026-0100", severity="LOW"),
                _occurrence(critical, "CVE-2026-0101", severity="CRITICAL"),
                _occurrence(high, "CVE-2026-0102", severity="HIGH"),
                _occurrence(image, "CVE-2026-0103", severity="CRITICAL"),
            ),
        ),
        period=_period(),
    )

    assert dataset["workload_status"] == {
        "total_virtual_machines": 3,
        "by_max_severity": {
            "CRITICAL": 1,
            "HIGH": 1,
            "MEDIUM": 0,
            "LOW": 0,
            "NONE": 1,
        },
    }

def test_dataset_artifact_is_atomic_hashed_and_replayable(tmp_path: Path) -> None:
    module = _dataset_module()
    artifact = module.write_cloud_report_dataset(
        dataset=module.build_cloud_dataset(
            snapshot=_snapshot(),
            period=_period(),
        ),
        output_root=tmp_path,
        execution_type="manual",
        client_id="cliente-fixture",
        run_id="run-fixture",
    )

    assert artifact.dataset_path.is_file()
    assert artifact.sha256
    assert artifact.dataset == module.load_cloud_report_dataset(
        artifact.dataset_path
    )
    assert not artifact.dataset_path.with_suffix(".json.tmp").exists()
