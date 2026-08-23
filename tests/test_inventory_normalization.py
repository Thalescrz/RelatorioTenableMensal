from __future__ import annotations

import json
import unittest
from pathlib import Path

from tenable_reports.domain.inventory_normalization import normalize_inventory_findings
from tenable_reports.domain.normalization import normalize_assets


ROOT = Path(__file__).resolve().parents[1]


def inventory_findings() -> list[dict[str, object]]:
    return json.loads(
        (ROOT / "tests/fixtures/tenable_inventory/findings_normalization.json").read_text(
            encoding="utf-8"
        )
    )


def normalized_assets():
    assets, issues, rejected, duplicates = normalize_assets(
        [
            {
                "id": "asset-fixture-1",
                "name": "host-fixture-1.invalid",
                "network": {
                    "hostnames": ["host-fixture-1.invalid"],
                    "ipv4s": ["192.0.2.10"],
                },
            }
        ],
        client_id="client-fixture",
    )
    assert not issues
    assert rejected == duplicates == 0
    return {item.source_asset_id: item for item in assets}


class InventoryNormalizationTests(unittest.TestCase):
    def test_maps_inventory_states_and_fields_without_inventing_plugin_id(self) -> None:
        findings, issues, rejected = normalize_inventory_findings(
            inventory_findings(),
            client_id="client-fixture",
            assets_by_id=normalized_assets(),
        )

        self.assertEqual(rejected, 0)
        self.assertEqual([item.state for item in findings], ["OPEN", "REOPENED"])
        first = findings[0]
        self.assertEqual(first.source, "tenable_inventory_findings")
        self.assertEqual(first.source_finding_id, "det-2026-0001")
        self.assertIsNone(first.plugin_id)
        self.assertEqual(first.plugin_name, "Inventory finding de exemplo")
        self.assertEqual(first.cves, ("CVE-2026-0001",))
        self.assertEqual(first.cvss3_base_score, 9.8)
        self.assertEqual(first.vpr_score, 9.1)
        self.assertEqual(first.description, "Descricao de exemplo retornada pelo Inventory.")
        self.assertEqual(first.solution, "Solucao de exemplo retornada pelo Inventory.")
        self.assertEqual(first.plugin_output, "Evidencia especifica do ativo.")
        self.assertEqual(first.port, 443)
        self.assertEqual(first.protocol, "tcp")
        self.assertEqual(first.first_found_at, "2026-07-01T10:00:00Z")
        self.assertEqual(first.last_found_at, "2026-07-31T10:00:00Z")
        self.assertTrue(first.exploitable)
        self.assertIsNotNone(first.asset_key)
        self.assertEqual(
            [issue.code for issue in issues],
            ["INVENTORY_PLUGIN_ID_UNRESOLVED", "INVENTORY_PLUGIN_ID_UNRESOLVED"],
        )

    def test_detection_id_and_fingerprint_form_a_stable_source_identity(self) -> None:
        record = inventory_findings()[0]
        first, _, _ = normalize_inventory_findings(
            [record], client_id="client-fixture", assets_by_id=normalized_assets()
        )
        again, _, _ = normalize_inventory_findings(
            [dict(record)], client_id="client-fixture", assets_by_id=normalized_assets()
        )
        changed = dict(record)
        changed["port"] = 8443
        different, _, _ = normalize_inventory_findings(
            [changed], client_id="client-fixture", assets_by_id=normalized_assets()
        )

        self.assertEqual(first[0].finding_key, again[0].finding_key)
        self.assertNotEqual(first[0].finding_key, different[0].finding_key)
        self.assertNotIn("det-2026-0001", str(first[0].plugin_id))

    def test_missing_optional_values_stay_none_and_quality_is_structured(self) -> None:
        record = {
            "asset_id": "asset-not-exported",
            "finding_name": "Finding sem metadados",
            "state": "FIXED",
            "severity": "LOW",
        }
        findings, issues, rejected = normalize_inventory_findings(
            [record], client_id="client-fixture", assets_by_id=normalized_assets()
        )

        self.assertEqual(rejected, 0)
        finding = findings[0]
        self.assertEqual(finding.state, "FIXED")
        self.assertIsNone(finding.source_finding_id)
        self.assertIsNone(finding.plugin_id)
        self.assertIsNone(finding.description)
        self.assertIsNone(finding.solution)
        self.assertIsNone(finding.plugin_output)
        self.assertIsNone(finding.first_found_at)
        self.assertIsNone(finding.last_found_at)
        self.assertIsNone(finding.last_fixed_at)
        self.assertEqual(finding.port, 0)
        self.assertEqual(finding.protocol, "unknown")
        self.assertIsNone(finding.asset_key)
        self.assertEqual(
            {issue.code for issue in issues},
            {
                "INVENTORY_FINDING_DETECTION_ID_MISSING",
                "INVENTORY_FINDING_PORT_MISSING",
                "INVENTORY_PLUGIN_ID_UNRESOLVED",
                "INVENTORY_FINDING_ASSET_ORPHAN",
            },
        )

    def test_rejects_record_without_asset_identity_or_finding_name(self) -> None:
        records = [
            {"finding_name": "Sem ativo", "state": "ACTIVE"},
            {"asset_id": "asset-fixture-1", "state": "ACTIVE"},
        ]
        findings, issues, rejected = normalize_inventory_findings(
            records, client_id="client-fixture", assets_by_id=normalized_assets()
        )

        self.assertEqual(findings, ())
        self.assertEqual(rejected, 2)
        self.assertEqual(
            [issue.code for issue in issues],
            ["INVENTORY_ASSET_ID_MISSING", "INVENTORY_FINDING_NAME_MISSING"],
        )


if __name__ == "__main__":
    unittest.main()
