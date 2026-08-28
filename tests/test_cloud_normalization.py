from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Mapping


FIXTURES = Path(__file__).parent / "fixtures" / "tenable_cloud"


def _normalization_module():
    return importlib.import_module("tenable_reports.application.normalize_cloud")


def _domain_module():
    return importlib.import_module("tenable_reports.domain.cloud")


def _fixture(name: str) -> list[Mapping[str, Any]]:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return payload["nodes"]


def _snapshot(**sources: list[Mapping[str, Any]]) -> Any:
    module = _normalization_module()
    return module.normalize_cloud_sources(
        collected_at="2026-08-26T12:00:00+00:00",
        sources=sources,
    )


def test_vm_and_image_with_same_remote_id_do_not_collide() -> None:
    domain = _domain_module()
    vulnerability = {
        "Id": "CVE-2026-1000",
        "Severity": "High",
        "CvssScore": 8.1,
        "VprScore": 7.2,
    }
    common = {
        "Id": "same-id",
        "Name": "fixture-resource",
        "AccountId": "account-fixture",
        "Software": [{"Name": "fixture-software", "Vulnerabilities": [vulnerability]}],
    }

    snapshot = _snapshot(
        virtual_machines=[common],
        container_images=[{**common, "Digest": "sha256:fixture"}],
    )

    assert len(snapshot.assets) == 2
    assert {item.asset.kind for item in snapshot.occurrences} == {
        domain.CloudAssetKind.VIRTUAL_MACHINE,
        domain.CloudAssetKind.CONTAINER_IMAGE,
    }


def test_vpr_zero_and_missing_remain_distinct() -> None:
    snapshot = _snapshot(
        virtual_machines=[
            {
                "Id": "vm-001",
                "Name": "vm-fixture",
                "Software": [
                    {
                        "Name": "fixture-os",
                        "Vulnerabilities": [
                            {
                                "Id": "CVE-2026-0001",
                                "Severity": "Medium",
                                "VprScore": 0,
                                "CvssScore": 5.0,
                            },
                            {
                                "Id": "CVE-2026-0002",
                                "Severity": "Low",
                                "VprScore": None,
                                "CvssScore": 3.0,
                            },
                        ],
                    }
                ],
            }
        ]
    )

    by_id = {item.vulnerability_id: item for item in snapshot.occurrences}
    assert by_id["CVE-2026-0001"].vpr == 0.0
    assert by_id["CVE-2026-0002"].vpr is None


def test_posture_finding_is_not_a_cve_occurrence() -> None:
    snapshot = _snapshot(findings=_fixture("findings-page-1.json"))

    assert len(snapshot.findings) == 1
    assert snapshot.occurrences == ()
    assert snapshot.findings[0].policy_name == "Fixture policy"


def test_duplicate_occurrence_preserves_worst_severity_and_highest_scores() -> None:
    snapshot = _snapshot(
        virtual_machines=[
            {
                "Id": "vm-001",
                "Name": "vm-fixture",
                "Software": [
                    {
                        "Name": "fixture-os",
                        "Vulnerabilities": [
                            {
                                "Id": "CVE-2026-0001",
                                "Severity": "Medium",
                                "VprScore": 4.0,
                                "CvssScore": 5.0,
                            },
                            {
                                "Id": "CVE-2026-0001",
                                "Severity": "Critical",
                                "VprScore": 8.0,
                                "CvssScore": 9.8,
                            },
                        ],
                    }
                ],
            }
        ]
    )

    assert len(snapshot.occurrences) == 1
    occurrence = snapshot.occurrences[0]
    assert occurrence.severity == "CRITICAL"
    assert occurrence.vpr == 8.0
    assert occurrence.cvss == 9.8


def test_software_specific_vulnerabilities_preserve_duplicates_and_optional_fixed_by() -> None:
    shared_vulnerability = {
        "Id": "CVE-2026-0100",
        "Severity": "High",
        "VprScore": 7.5,
        "CvssScore": 8.1,
    }
    snapshot = _snapshot(
        container_images=[
            {
                "Id": "image-001",
                "Name": "fixture-image",
                "Digest": "sha256:fixture",
                "Software": [
                    {
                        "Name": "fixture-library-a",
                        "Vulnerabilities": [shared_vulnerability],
                    },
                    {
                        "Name": "fixture-library-b",
                        "Vulnerabilities": [shared_vulnerability],
                    },
                ],
            }
        ],
        container_image_fix_versions=[
            {
                "Id": "image-001",
                "Software": [
                    {
                        "Name": "fixture-library-a",
                        "Vulnerabilities": [
                            {
                                **shared_vulnerability,
                                "FixedBy": "2.0.1",
                            }
                        ],
                    },
                    {
                        "Name": "fixture-library-b",
                        "Vulnerabilities": [
                            {
                                **shared_vulnerability,
                                "FixedBy": None,
                            }
                        ],
                    },
                ],
            }
        ],
    )

    assert len(snapshot.occurrences) == 1
    assert len(snapshot.software_vulnerabilities) == 2
    by_software = {
        item.software: item for item in snapshot.software_vulnerabilities
    }
    assert by_software["fixture-library-a"].fixed_by == "2.0.1"
    assert by_software["fixture-library-b"].fixed_by is None


def test_compute_ip_is_joined_only_by_remote_id() -> None:
    snapshot = _snapshot(
        virtual_machines=[
            {"Id": "vm-001", "Name": "same-name", "Software": []},
            {"Id": "vm-002", "Name": "other-name", "Software": []},
        ],
        compute_ips=[
            {
                "__typename": "AwsEc2Instance",
                "Id": "vm-001",
                "Name": "unrelated-name",
                "PrivateIpAddresses": ["192.0.2.10"],
            },
            {
                "__typename": "AwsEc2Instance",
                "Id": "missing-id",
                "Name": "same-name",
                "PrivateIpAddresses": ["192.0.2.99"],
            },
        ],
    )

    by_id = {asset.key.asset_id: asset for asset in snapshot.assets}
    assert by_id["vm-001"].ip_addresses == ("192.0.2.10",)
    assert by_id["vm-002"].ip_addresses == ()
    assert any(item.code == "ORPHAN_COMPUTE_METADATA" for item in snapshot.quality_issues)


def test_missing_asset_id_becomes_quality_issue_not_approximate_asset() -> None:
    snapshot = _snapshot(
        virtual_machines=[
            {
                "Name": "vm-without-id",
                "Software": [
                    {
                        "Name": "fixture-os",
                        "Vulnerabilities": [
                            {"Id": "CVE-2026-9999", "Severity": "Critical"}
                        ],
                    }
                ],
            }
        ]
    )

    assert snapshot.assets == ()
    assert snapshot.occurrences == ()
    assert snapshot.quality_issues[0].code == "MISSING_ASSET_ID"


def test_vulnerability_finding_preserves_cves_per_resource() -> None:
    snapshot = _snapshot(
        vulnerability_remediations=[
            {
                "Id": "finding-001",
                "Policy": {
                    "Category": "Vulnerability",
                    "Name": "Fixture vulnerability policy",
                },
                "Status": "Open",
                "Remediation": {
                    "Type": "Patch",
                    "Console": {"Steps": ["Apply the security patch."]},
                },
                "Resources": [
                    {
                        "Id": "vm-001",
                        "Name": "vm-fixture",
                        "Vulnerabilities": [
                            {"Id": "CVE-2026-0001"},
                            {"Id": "CVE-2026-0002"},
                        ],
                    }
                ],
            }
        ]
    )

    finding = snapshot.findings[0]
    assert finding.vulnerability_related is True
    assert finding.explicit_correction_type == "Patch"
    assert finding.resources[0].vulnerability_ids == (
        "CVE-2026-0001",
        "CVE-2026-0002",
    )
