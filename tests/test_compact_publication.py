from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.collection_execution import materialize_compact_snapshot_run
from tenable_reports.application.compact_publication import (
    prepare_compact_run_snapshot,
    publish_compact_run_snapshot,
)
from tenable_reports.application.compact_snapshots import (
    MemoryCompactSnapshotRepository,
    build_compact_snapshot,
)
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.normalization import normalize_and_link
from tenable_reports.domain.reporting import explicit_reporting_period


ROOT = Path(__file__).resolve().parents[1]


class CompactPublicationTests(unittest.TestCase):
    def test_prepares_recovery_snapshot_without_publishing_it(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at="2026-08-10T12:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        normalized = normalize_and_link(
            asset_records=[{"id": "asset-a", "name": "host-a.invalid"}],
            finding_records=[{
                "finding_id": "finding-a",
                "asset": {"uuid": "asset-a"},
                "plugin": {"id": 100001, "name": "Plugin A"},
                "state": "OPEN",
                "severity": "HIGH",
                "last_found": "2026-07-20T12:00:00Z",
            }],
            client_id=profile.client_id,
        )
        source = build_compact_snapshot(
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            run_id="source-run",
            execution_type="AUTOMATIC_MONTHLY",
            period_mode=period.mode.value,
            period_start_at=period.to_dict()["start_at"],
            period_end_at=period.to_dict()["end_at"],
            assets=normalized.assets,
            findings=normalized.findings,
            quality_issues=normalized.issues,
            tag_asset_ids={},
            document_references={"base": "C:/old/base.docx"},
        )
        repository = MemoryCompactSnapshotRepository()
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            materialize_compact_snapshot_run(
                snapshot=source,
                profile=profile,
                run_id="published-run",
                output_root=directory,
            )
            base = directory / "base.docx"
            base.write_bytes(b"base")
            prepared = prepare_compact_run_snapshot(
                profile=profile,
                source_run_id="published-run",
                snapshot_run_id="published-run-was-recovered",
                execution_type="AUTOMATIC_MONTHLY",
                period=period,
                output_root=directory,
                document_references={"base": str(base)},
            )

        self.assertEqual(repository.count, 0)
        self.assertEqual(prepared.run_id, "published-run-was-recovered")
        self.assertEqual(prepared.record_counts["findings"], 1)
    def test_publishes_only_after_document_references_are_available(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        period = explicit_reporting_period(
            start_at="2026-07-01T00:00:00-03:00",
            end_at="2026-08-01T00:00:00-03:00",
            reference_at="2026-08-10T12:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        normalized = normalize_and_link(
            asset_records=[{"id": "asset-a", "name": "host-a.invalid"}],
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
        source = build_compact_snapshot(
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            run_id="source-run",
            execution_type="MANUAL",
            period_mode=period.mode.value,
            period_start_at=period.to_dict()["start_at"],
            period_end_at=period.to_dict()["end_at"],
            assets=normalized.assets,
            findings=normalized.findings,
            quality_issues=normalized.issues,
            tag_asset_ids={},
            document_references={"base": "C:/old/base.docx"},
        )
        repository = MemoryCompactSnapshotRepository()

        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            materialize_compact_snapshot_run(
                snapshot=source,
                profile=profile,
                run_id="published-run",
                output_root=directory,
            )
            base = directory / "base.docx"
            custom = directory / "custom.docx"
            base.write_bytes(b"base")
            custom.write_bytes(b"custom")
            snapshot = publish_compact_run_snapshot(
                repository=repository,
                profile=profile,
                run_id="published-run",
                execution_type="MANUAL",
                period=period,
                output_root=directory,
                document_references={
                    "base": str(base),
                    "custom": str(custom),
                },
                publication_validated=True,
                documents_validated=True,
            )

        self.assertEqual(repository.count, 1)
        self.assertEqual(snapshot.document_references["base"], str(base))
        self.assertEqual(snapshot.record_counts["findings"], 1)


if __name__ == "__main__":
    unittest.main()
