from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.historical_materialization import (
    materialize_historical_collection_run,
)
from tenable_reports.application.report_dataset import load_report_dataset_inputs
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.models import build_source_snapshot
from tenable_reports.domain.normalization import normalize_and_link
from tenable_reports.domain.reporting import explicit_reporting_period


ROOT = Path(__file__).resolve().parents[1]


class HistoricalMaterializationTests(unittest.TestCase):
    def test_preserves_bounded_collection_provenance(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at="2026-08-10T12:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        normalized = normalize_and_link(
            asset_records=[{
                "id": "asset-a",
                "name": "host-a.invalid",
                "network": {"ipv4s": ["192.0.2.10"]},
            }],
            finding_records=[{
                "finding_id": "finding-a",
                "asset": {"uuid": "asset-a"},
                "plugin": {"id": 100001, "name": "Plugin A"},
                "port": {"port": 443, "protocol": "TCP"},
                "state": "OPEN",
                "severity": "CRITICAL",
                "last_found": "2026-07-20T12:00:00Z",
            }],
            client_id=profile.client_id,
        )
        asset_source = build_source_snapshot(
            run_id="hybrid-run",
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_vm_assets_v2",
            export_uuid="assets-export",
            query={"filters": {"since": period.start_epoch}},
            chunks=[(1, b"asset")],
            record_count=len(normalized.assets),
            started_at="2026-08-23T12:00:00Z",
            collector_version="test",
            raw_manifest_uri="file:///assets-manifest.json",
        )

        with tempfile.TemporaryDirectory() as directory:
            materialize_historical_collection_run(
                profile=profile,
                run_id="hybrid-run",
                output_root=directory,
                asset_snapshot=asset_source,
                assets=normalized.assets,
                findings=normalized.findings,
                quality_issues=normalized.issues,
                route="inventory_bounded_hybrid",
                reconstruction_status="HISTORICAL_RECONSTRUCTION",
                sources=("tenable_inventory_findings", "tenable_vm_vulnerabilities"),
                source_manifest_uri="file:///historical-manifest.json",
                include_output=False,
            )
            inputs = load_report_dataset_inputs(
                profile=profile,
                run_id="hybrid-run",
                output_root=directory,
            )

        self.assertEqual(
            inputs.collection_provenance["collection_route"],
            "inventory_bounded_hybrid",
        )
        self.assertEqual(
            inputs.collection_provenance["sources"],
            ["tenable_inventory_findings", "tenable_vm_vulnerabilities"],
        )


if __name__ == "__main__":
    unittest.main()
