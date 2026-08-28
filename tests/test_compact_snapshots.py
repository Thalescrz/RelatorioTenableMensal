from __future__ import annotations

from dataclasses import replace
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.compact_snapshots import (
    MemoryCompactSnapshotRepository,
    build_compact_snapshot,
    compact_vm_content_sha256,
    replay_compact_snapshot,
)
from tenable_reports.application.history import finalize_compact_snapshot
from tenable_reports.application.retention import plan_published_run_cleanup
from tenable_reports.domain.normalization import (
    DataQualityIssue,
    QualitySeverity,
    normalize_and_link,
)
from tenable_reports.domain.was import normalize_was_findings


ROOT = Path(__file__).resolve().parents[1]


def normalized_fixture():
    return normalize_and_link(
        asset_records=[{
            "id": "asset-a",
            "name": "host-a.invalid",
            "network": {"ipv4s": ["192.0.2.10"]},
        }],
        finding_records=[{
            "finding_id": "finding-a",
            "asset": {"uuid": "asset-a"},
            "plugin": {
                "id": 100001,
                "name": "Plugin A",
                "description": "Descricao",
                "solution": "Solucao",
                "vpr": {"score": 9.1},
            },
            "port": {"port": 443, "protocol": "TCP"},
            "state": "OPEN",
            "severity": "CRITICAL",
            "last_found": "2026-07-20T12:00:00Z",
        }],
        client_id="client-a",
    )


class CompactSnapshotTests(unittest.TestCase):
    def test_vm_hash_is_unchanged_when_only_was_and_documents_change(self) -> None:
        normalized = normalized_fixture()
        was = normalize_was_findings([{
            "finding_id": "was-a",
            "asset": {"uuid": "web-a"},
            "plugin": {"id": 200001, "name": "WEB A"},
            "state": "OPEN",
            "severity": "HIGH",
            "last_found": "2026-07-20T12:00:00Z",
        }], client_id="client-a").findings
        common = {
            "client_id": "client-a",
            "tenant_id": "tenant-a",
            "execution_type": "AUTOMATIC_MONTHLY",
            "period_mode": "PREVIOUS_CALENDAR_MONTH",
            "period_start_at": "2026-07-01T03:00:00Z",
            "period_end_at": "2026-08-01T03:00:00Z",
            "assets": normalized.assets,
            "findings": normalized.findings,
            "quality_issues": normalized.issues,
            "tag_asset_ids": {"tag-a": ("asset-a",)},
        }
        before = build_compact_snapshot(
            **common,
            run_id="run-before",
            document_references={"base": "C:/reports/base-before.docx"},
        )
        after = build_compact_snapshot(
            **common,
            run_id="run-after",
            document_references={"base": "C:/reports/base-after.docx"},
            was_findings=was,
        )

        self.assertEqual(
            compact_vm_content_sha256(before),
            compact_vm_content_sha256(after),
        )
        self.assertNotEqual(before.content_sha256, after.content_sha256)
    def test_migration_stores_compressed_payload_and_exact_period_index(self) -> None:
        sql = (
            ROOT
            / "src/tenable_reports/infrastructure/postgresql_migrations/0006_compact_finding_snapshots.sql"
        ).read_text(encoding="utf-8")

        self.assertIn("create table if not exists tenable_reports.compact_finding_snapshots", sql)
        self.assertIn("payload_gzip bytea", sql)
        self.assertIn("content_sha256", sql)
        self.assertIn("schema_version", sql)
        self.assertIn("compact_finding_snapshots_exact_idx", sql)
        self.assertIn("document_references jsonb", sql)

    def test_normalized_dataset_round_trips_with_tags_quality_and_documents(self) -> None:
        normalized = normalized_fixture()
        issue = DataQualityIssue(
            code="FIXTURE_WARNING",
            severity=QualitySeverity.WARNING,
            source="fixture",
            record_index=0,
            message="Aviso de fixture.",
            source_id="finding-a",
        )
        snapshot = build_compact_snapshot(
            client_id="client-a",
            tenant_id="tenant-a",
            run_id="run-a",
            execution_type="MANUAL",
            period_mode="EXPLICIT_RANGE",
            period_start_at="2026-07-01T03:00:00Z",
            period_end_at="2026-08-01T03:00:00Z",
            assets=normalized.assets,
            findings=normalized.findings,
            quality_issues=(*normalized.issues, issue),
            tag_asset_ids={"tag-a": ("asset-a",)},
            document_references={
                "base": "C:/reports/base.docx",
                "custom": "C:/reports/custom.docx",
            },
        )

        replay = replay_compact_snapshot(snapshot)

        self.assertEqual(replay.assets, normalized.assets)
        self.assertEqual(replay.findings, normalized.findings)
        self.assertEqual(replay.quality_issues[-1], issue)
        self.assertEqual(replay.tag_asset_ids, {"tag-a": ("asset-a",)})
        self.assertEqual(replay.document_references["base"], "C:/reports/base.docx")
        self.assertEqual(snapshot.record_counts, {
            "assets": 1,
            "findings": 1,
            "quality_issues": 1,
            "was_findings": 0,
        })
        self.assertEqual(len(snapshot.content_sha256), 64)
        self.assertTrue(snapshot.payload_gzip.startswith(b"\x1f\x8b"))

    def test_repository_prefers_latest_exact_snapshot_and_is_idempotent(self) -> None:
        normalized = normalized_fixture()
        repository = MemoryCompactSnapshotRepository()

        def snapshot(run_id: str):
            return build_compact_snapshot(
                client_id="client-a",
                tenant_id="tenant-a",
                run_id=run_id,
                execution_type="MANUAL",
                period_mode="EXPLICIT_RANGE",
                period_start_at="2026-07-01T03:00:00Z",
                period_end_at="2026-08-01T03:00:00Z",
                assets=normalized.assets,
                findings=normalized.findings,
                quality_issues=(),
                tag_asset_ids={},
                document_references={"base": f"C:/reports/{run_id}.docx"},
            )

        first = snapshot("run-a")
        second = snapshot("run-b")
        repository.publish(first)
        repository.publish(first)
        repository.publish(second)

        exact = repository.find_exact(
            client_id="client-a",
            tenant_id="tenant-a",
            period_start_at="2026-07-01T03:00:00Z",
            period_end_at="2026-08-01T03:00:00Z",
        )
        self.assertEqual(repository.count, 2)
        self.assertEqual(exact.run_id, "run-b")

    def test_repository_finds_the_original_snapshot_by_run_id(self) -> None:
        normalized = normalized_fixture()
        repository = MemoryCompactSnapshotRepository()

        def snapshot(run_id: str):
            return build_compact_snapshot(
                client_id="client-a",
                tenant_id="tenant-a",
                run_id=run_id,
                execution_type="AUTOMATIC_MONTHLY",
                period_mode="PREVIOUS_CALENDAR_MONTH",
                period_start_at="2026-07-01T03:00:00Z",
                period_end_at="2026-08-01T03:00:00Z",
                assets=normalized.assets,
                findings=normalized.findings,
                quality_issues=(),
                tag_asset_ids={},
                document_references={"base": f"C:/reports/{run_id}.docx"},
            )

        original = snapshot("run-original")
        repaired = snapshot("run-original-was-recovered")
        repository.publish(original)
        repository.publish(repaired)

        found = repository.find_run(
            client_id="client-a",
            tenant_id="tenant-a",
            run_id="run-original",
        )

        self.assertEqual(found, original)

    def test_replay_recovers_hostname_from_legacy_blank_display_name(self) -> None:
        normalized = normalized_fixture()
        snapshot = build_compact_snapshot(
            client_id="client-a",
            tenant_id="tenant-a",
            run_id="run-a",
            execution_type="MANUAL",
            period_mode="EXPLICIT_RANGE",
            period_start_at="2026-07-01T03:00:00Z",
            period_end_at="2026-08-01T03:00:00Z",
            assets=normalized.assets,
            findings=normalized.findings,
            quality_issues=(),
            tag_asset_ids={},
            document_references={"base": "C:/reports/base.docx"},
        )
        payload = json.loads(gzip.decompress(snapshot.payload_gzip).decode("utf-8"))
        payload["assets"][0]["display_name"] = ""
        payload["assets"][0]["hostnames"] = ["host-a.invalid"]
        logical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        legacy_snapshot = replace(
            snapshot,
            content_sha256=hashlib.sha256(logical).hexdigest(),
            payload_gzip=gzip.compress(logical, compresslevel=9, mtime=0),
        )

        replay = replay_compact_snapshot(legacy_snapshot)

        self.assertEqual(replay.assets[0].display_name, "host-a.invalid")

    def test_history_finalization_requires_publication_and_documents(self) -> None:
        normalized = normalized_fixture()
        snapshot = build_compact_snapshot(
            client_id="client-a",
            tenant_id="tenant-a",
            run_id="run-a",
            execution_type="MANUAL",
            period_mode="EXPLICIT_RANGE",
            period_start_at="2026-07-01T03:00:00Z",
            period_end_at="2026-08-01T03:00:00Z",
            assets=normalized.assets,
            findings=normalized.findings,
            quality_issues=(),
            tag_asset_ids={},
            document_references={"base": "C:/reports/base.docx"},
        )
        repository = MemoryCompactSnapshotRepository()
        for publication, documents in ((False, True), (True, False)):
            with self.assertRaisesRegex(ValueError, "confirmad"):
                finalize_compact_snapshot(
                    repository=repository,
                    snapshot=snapshot,
                    publication_validated=publication,
                    documents_validated=documents,
                )
        finalize_compact_snapshot(
            repository=repository,
            snapshot=snapshot,
            publication_validated=True,
            documents_validated=True,
        )
        self.assertEqual(repository.count, 1)

    def test_cleanup_is_blocked_without_compact_snapshot_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw" / "client-a" / "run-a"
            path.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "snapshot compacto"):
                plan_published_run_cleanup(
                    scoped_output_root=directory,
                    client_id="client-a",
                    run_id="run-a",
                    publication_confirmed=True,
                    history_confirmed=True,
                    compact_snapshot_confirmed=False,
                )


if __name__ == "__main__":
    unittest.main()
