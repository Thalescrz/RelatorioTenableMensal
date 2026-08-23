from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from tenable_reports.application.collection_execution import (
    materialize_compact_snapshot_run,
    resolve_execution_collection_route,
)
from tenable_reports.application.compact_snapshots import (
    MemoryCompactSnapshotRepository,
    build_compact_snapshot,
)
from tenable_reports.application.report_dataset import (
    build_report_dataset_from_snapshot,
    load_report_dataset_inputs,
)
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.normalization import normalize_and_link
from tenable_reports.domain.reporting import explicit_reporting_period


ROOT = Path(__file__).resolve().parents[1]


class CollectionExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        self.period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at="2026-08-10T12:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        self.normalized = normalize_and_link(
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
            client_id=self.profile.client_id,
        )

    def compact_snapshot(self):
        return build_compact_snapshot(
            client_id=self.profile.client_id,
            tenant_id=self.profile.tenant_id,
            run_id="source-run",
            execution_type="MANUAL",
            period_mode=self.period.mode.value,
            period_start_at=self.period.to_dict()["start_at"],
            period_end_at=self.period.to_dict()["end_at"],
            assets=self.normalized.assets,
            findings=self.normalized.findings,
            quality_issues=self.normalized.issues,
            tag_asset_ids={},
            document_references={"base": "C:/reports/source.docx"},
        )

    def test_exact_snapshot_is_selected_before_any_external_source(self) -> None:
        repository = MemoryCompactSnapshotRepository()
        repository.publish(self.compact_snapshot())

        route, snapshot = resolve_execution_collection_route(
            profile=self.profile,
            period=self.period,
            execution_mode="manual",
            historical_source_override="inventory-beta",
            compact_repository=repository,
            now=datetime(2026, 8, 23, tzinfo=UTC),
        )

        self.assertEqual(route.source.value, "snapshot_replay")
        self.assertEqual(route.accuracy.value, "authoritative_snapshot")
        self.assertEqual(snapshot.run_id, "source-run")

    def test_replay_materializes_existing_report_contract_with_provenance(self) -> None:
        snapshot = self.compact_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            materialize_compact_snapshot_run(
                snapshot=snapshot,
                profile=self.profile,
                run_id="replay-run",
                output_root=directory,
            )
            inputs = load_report_dataset_inputs(
                profile=self.profile,
                run_id="replay-run",
                output_root=directory,
            )
            artifact = build_report_dataset_from_snapshot(
                profile=self.profile,
                run_id="replay-run",
                period=self.period,
                output_root=directory,
                execution_type="MANUAL",
            )

        self.assertEqual(inputs.assets, self.normalized.assets)
        self.assertEqual(inputs.findings, self.normalized.findings)
        self.assertEqual(
            inputs.collection_provenance["collection_route"], "snapshot_replay"
        )
        self.assertEqual(
            artifact.result.dataset.collection_provenance["source_snapshot_id"],
            snapshot.snapshot_id,
        )


if __name__ == "__main__":
    unittest.main()
