from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tenable_reports.application.collect import VulnerabilityExportRequest
from tenable_reports.application.vm_export_policy import (
    collect_vm_snapshot_with_policy,
    compare_vm_exports,
    selective_vm_properties,
    validate_selective_records,
)
from tenable_reports.infrastructure.tenable_vm.client import ApiError


class VmExportPolicyTests(unittest.TestCase):
    def test_selective_properties_cover_report_fields_with_official_paths(self) -> None:
        properties = selective_vm_properties(include_output=False)

        self.assertIn("severity", properties)
        self.assertIn("state", properties)
        self.assertIn("first_observed", properties)
        self.assertIn("last_seen", properties)
        self.assertIn("last_fixed", properties)
        self.assertIn("resurfaced_date", properties)
        self.assertIn("definition.name", properties)
        self.assertIn("definition.family", properties)
        self.assertIn("definition.description", properties)
        self.assertIn("definition.synopsis", properties)
        self.assertIn("definition.solution", properties)
        self.assertIn("definition.see_also", properties)
        self.assertIn("definition.references", properties)
        self.assertIn("definition.cvss2.base_score", properties)
        self.assertIn("definition.cvss3.base_score", properties)
        self.assertIn("definition.cvss3.base_vector", properties)
        self.assertIn("definition.vpr.score", properties)
        self.assertNotIn("definition.cvss3_base_score", properties)
        self.assertNotIn("output", properties)
        self.assertEqual(len(properties), len(set(properties)))

    def test_output_property_is_strictly_opt_in(self) -> None:
        without_output = selective_vm_properties(include_output=False)
        with_output = selective_vm_properties(include_output=True)

        self.assertNotIn("output", without_output)
        self.assertEqual(with_output[-1], "output")
        self.assertEqual(with_output[:-1], without_output)

    def test_selective_contract_reports_missing_narrative_field(self) -> None:
        valid = validate_selective_records([selective_record()])
        invalid_record = selective_record()
        del invalid_record["definition"]["description"]
        invalid = validate_selective_records([invalid_record])

        self.assertTrue(valid.passed)
        self.assertFalse(invalid.passed)
        self.assertIn("definition.description", invalid.missing_properties)

    def test_comparison_accepts_legacy_and_selective_equivalent_records(self) -> None:
        comparison = compare_vm_exports(
            [full_record()],
            [selective_record()],
        )

        self.assertTrue(comparison.passed)
        self.assertEqual(comparison.status, "PASSED")
        self.assertEqual(comparison.differences, ())

    def test_comparison_detects_numerical_divergence_without_sensitive_values(self) -> None:
        selective = selective_record()
        selective["severity"] = "HIGH"

        comparison = compare_vm_exports([full_record()], [selective])
        serialized = json.dumps(comparison.to_dict())

        self.assertFalse(comparison.passed)
        self.assertIn("severity_counts", comparison.differences)
        self.assertNotIn("asset-fixture-1", serialized)
        self.assertNotIn("203.0.113", serialized)

    def test_enabled_mode_falls_back_to_full_on_http_400(self) -> None:
        calls: list[VulnerabilityExportRequest] = []

        def collector(**kwargs):
            export_request = kwargs["request"]
            calls.append(export_request)
            if export_request.properties:
                raise ApiError("properties invalidas", status_code=400)
            return SimpleNamespace(records=(full_record(),))

        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot_with_policy(
                collector=collector,
                client=object(),
                profile=SimpleNamespace(client_id="cliente-a"),
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-fallback-400",
                mode="enabled",
                strategy="combined",
            )

        self.assertEqual(result.outcome, "FALLBACK_FULL")
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].properties)
        self.assertFalse(calls[1].properties)
        self.assertEqual(result.fallback_reason, "HTTP_400")

    def test_enabled_mode_falls_back_when_selective_contract_is_incomplete(self) -> None:
        calls: list[VulnerabilityExportRequest] = []
        incomplete = selective_record()
        del incomplete["definition"]["solution"]

        def collector(**kwargs):
            export_request = kwargs["request"]
            calls.append(export_request)
            records = (incomplete,) if export_request.properties else (full_record(),)
            return SimpleNamespace(records=records)

        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot_with_policy(
                collector=collector,
                client=object(),
                profile=SimpleNamespace(client_id="cliente-a"),
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-fallback-contract",
                mode="enabled",
                strategy="combined",
            )

        self.assertEqual(result.outcome, "FALLBACK_FULL")
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.fallback_reason, "CONTRACT_INVALID")

    def test_enabled_mode_does_not_mask_rate_limit(self) -> None:
        def collector(**kwargs):
            raise ApiError("rate limited", status_code=429)

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ApiError) as caught:
                collect_vm_snapshot_with_policy(
                    collector=collector,
                    client=object(),
                    profile=SimpleNamespace(client_id="cliente-a"),
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-no-fallback",
                    mode="enabled",
                    strategy="combined",
                )

        self.assertEqual(caught.exception.status_code, 429)

    def test_validation_mode_keeps_full_collection_and_writes_sanitized_result(self) -> None:
        full_collection = SimpleNamespace(records=(full_record(),))
        selective_collection = SimpleNamespace(records=(selective_record(),))

        def collector(**kwargs):
            return (
                selective_collection
                if kwargs["request"].properties
                else full_collection
            )

        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot_with_policy(
                collector=collector,
                client=object(),
                profile=SimpleNamespace(client_id="cliente-a"),
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-validation",
                mode="validation",
                strategy="combined",
            )
            payload = json.loads(
                Path(result.comparison_path).read_text(encoding="utf-8")
            )

        self.assertIs(result.collection, full_collection)
        self.assertEqual(result.outcome, "PASSED")
        self.assertEqual(payload["status"], "PASSED")
        self.assertNotIn("asset-fixture-1", json.dumps(payload))


def selective_record() -> dict:
    return {
        "id": "finding-fixture",
        "asset": {"id": "asset-fixture-1"},
        "definition": {
            "id": 100001,
            "name": "Plugin fixture",
            "family": "General",
            "cve": ["CVE-2026-0001"],
            "description": "Description",
            "synopsis": "Synopsis",
            "solution": "Solution",
            "see_also": ["https://example.invalid/reference"],
            "references": ["CWE-79"],
            "cvss2": {"base_score": 5.0},
            "cvss3": {
                "base_score": 9.8,
                "base_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            },
            "vpr": {"score": 9.4},
            "canvas": True,
            "core": False,
            "elliot": False,
            "exploithub": False,
            "metasploit": True,
            "exploitability_ease": "Exploits are available",
            "patch_published": "2026-07-10T00:00:00Z",
        },
        "source": "NESSUS",
        "port": 443,
        "protocol": "TCP",
        "service": "https",
        "severity": "CRITICAL",
        "state": "OPEN",
        "first_observed": "2026-07-01T10:00:00Z",
        "last_seen": "2026-07-31T10:00:00Z",
        "last_fixed": None,
        "resurfaced_date": None,
    }


def full_record() -> dict:
    return {
        "finding_id": "finding-fixture",
        "asset": {"uuid": "asset-fixture-1"},
        "plugin": {
            "id": 100001,
            "name": "Plugin fixture",
            "family": "General",
            "cve": ["CVE-2026-0001"],
            "description": "Description",
            "synopsis": "Synopsis",
            "solution": "Solution",
            "see_also": ["https://example.invalid/reference", "CWE-79"],
            "cvss_base_score": 5.0,
            "cvss3_base_score": 9.8,
            "cvss3_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
            "vpr": {"score": 9.4},
            "exploit_available": True,
            "exploit_framework_canvas": True,
            "exploit_framework_metasploit": True,
            "has_patch": True,
        },
        "source": "NESSUS",
        "port": {"port": 443, "protocol": "TCP", "service": "https"},
        "severity": "CRITICAL",
        "state": "OPEN",
        "first_found": "2026-07-01T10:00:00Z",
        "last_found": "2026-07-31T10:00:00Z",
        "last_fixed": None,
        "resurfaced_date": None,
    }


if __name__ == "__main__":
    unittest.main()
