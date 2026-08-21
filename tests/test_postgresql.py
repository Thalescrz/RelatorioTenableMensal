from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.history import SQLiteSnapshotRepository
from tenable_reports.application.postgresql_migration import migrate_legacy_state
from tenable_reports.config.database import DatabaseConfig
from tenable_reports.domain.history import HistorySnapshot, SnapshotCompatibility
from tenable_reports.domain.fingerprints import fingerprint_finding_key
from tenable_reports.infrastructure.postgresql import (
    _compact_legacy_history_payload,
    _history_snapshot_from_storage,
    _history_snapshot_storage,
)


ROOT = Path(__file__).resolve().parents[1]


class _MemorySnapshotTarget:
    def __init__(self) -> None:
        self.values: list[HistorySnapshot] = []

    def publish(self, snapshot: HistorySnapshot) -> None:
        if not any(item.snapshot_id == snapshot.snapshot_id for item in self.values):
            self.values.append(snapshot)


class _MemoryOperationsTarget:
    def __init__(self) -> None:
        self.legacy: list[dict] = []
        self.artifacts = []

    def record_legacy_sqlite(self, **payload) -> None:
        self.legacy.append(payload)

    def register_artifacts(self, records) -> int:
        self.artifacts.extend(records)
        return len(records)

    def record_publication_manifest(self, path) -> None:
        raise AssertionError(f"Manifesto inesperado: {path}")

    def record_orchestration_manifest(self, path) -> None:
        raise AssertionError(f"Manifesto inesperado: {path}")


class PostgreSqlTests(unittest.TestCase):
    def test_database_config_never_exposes_password_in_location(self) -> None:
        config = DatabaseConfig.from_environment({
            "TENABLE_REPORTS_DB_HOST": "127.0.0.1",
            "TENABLE_REPORTS_DB_NAME": "tenable_reports",
            "TENABLE_REPORTS_DB_USER": "tenable_reports_app",
            "TENABLE_REPORTS_DB_PASSWORD": "segredo-local",
        })
        self.assertEqual(
            config.safe_location,
            "postgresql://tenable_reports_app@127.0.0.1:5432/tenable_reports",
        )
        self.assertNotIn("segredo-local", config.safe_location)
        self.assertEqual(config.connection_kwargs()["password"], "segredo-local")

    def test_initial_migration_has_security_constraints_and_query_indexes(self) -> None:
        sql = (
            ROOT
            / "src/tenable_reports/infrastructure/postgresql_migrations/0001_initial.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("revoke all on schema tenable_reports from public", sql)
        self.assertIn("history_snapshots_predecessor_idx", sql)
        self.assertIn("orchestration_clients_run_idx", sql)
        self.assertIn("generated always as identity", sql)
        self.assertIn("timestamptz", sql)

    def test_main_reference_migration_supports_attempts_soft_delete_and_audit(self) -> None:
        sql = (
            ROOT
            / "src/tenable_reports/infrastructure/postgresql_migrations/0002_report_main_and_attempts.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("drop constraint if exists history_snapshots_competence_uq", sql)
        self.assertIn("create table if not exists tenable_reports.report_main_references", sql)
        self.assertIn("create table if not exists tenable_reports.report_reference_events", sql)
        self.assertIn("logical_job_id", sql)
        self.assertIn("attempt_number", sql)
        self.assertIn("deleted_at", sql)
        self.assertIn("period_mode", sql)

    def test_compact_history_migration_adds_binary_fingerprints_and_cleanup_state(self) -> None:
        sql = (
            ROOT
            / "src/tenable_reports/infrastructure/postgresql_migrations/0003_compact_history_and_cleanup.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("fingerprint_version", sql)
        self.assertIn("open_fingerprints bytea", sql)
        self.assertIn("fixed_fingerprints bytea", sql)
        self.assertIn("resurfaced_fingerprints bytea", sql)
        self.assertIn("cleanup_status", sql)
        self.assertIn("cleanup_bytes", sql)

    def test_tag_report_document_migration_adds_optional_metadata(self) -> None:
        sql = (
            ROOT
            / "src/tenable_reports/infrastructure/postgresql_migrations/0004_tag_report_documents.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("document_kind text", sql)
        self.assertIn("tag_uuid text", sql)
        self.assertIn("tag_category text", sql)
        self.assertIn("tag_value text", sql)
        self.assertIn("published_documents_kind_check", sql)
        self.assertIn("published_documents_tag_idx", sql)

    def test_postgresql_history_payload_excludes_fingerprint_lists_and_round_trips(self) -> None:
        snapshot = HistorySnapshot(
            snapshot_id="snapshot-compact",
            run_id="run-compact",
            period_id="2026-07",
            period_start_at="2026-07-01T03:00:00Z",
            period_end_at="2026-08-01T03:00:00Z",
            generated_at="2026-08-01T12:00:00Z",
            compatibility=SnapshotCompatibility(
                client_id="cliente",
                tenant_id="tenant",
                execution_type="AUTOMATIC_MONTHLY",
                period_mode="PREVIOUS_CALENDAR_MONTH",
                timezone="America/Fortaleza",
                metric_definition_version="v1",
                scope_hash="scope",
            ),
            summary={"non_mitigated": 7},
            open_finding_keys=(fingerprint_finding_key("finding-1"),),
            fixed_finding_keys=(fingerprint_finding_key("finding-2"),),
            resurfaced_finding_keys=(),
            tag_snapshots=(),
        )

        payload, version, open_blob, fixed_blob, resurfaced_blob = (
            _history_snapshot_storage(snapshot)
        )

        assert "open_finding_keys" not in payload
        assert "fixed_finding_keys" not in payload
        assert "resurfaced_finding_keys" not in payload
        restored = _history_snapshot_from_storage(
            payload, version, open_blob, fixed_blob, resurfaced_blob
        )
        self.assertEqual(restored, snapshot)

    def test_legacy_history_payload_is_compacted_after_migration(self) -> None:
        payload = {
            "snapshot_id": "legacy-snapshot",
            "run_id": "legacy-run",
            "period_id": "2026-06",
            "period_start_at": "2026-06-01T03:00:00Z",
            "period_end_at": "2026-07-01T03:00:00Z",
            "generated_at": "2026-07-01T12:00:00Z",
            "compatibility": {
                "client_id": "cliente",
                "tenant_id": "tenant",
                "execution_type": "AUTOMATIC_MONTHLY",
                "period_mode": "PREVIOUS_CALENDAR_MONTH",
                "timezone": "America/Fortaleza",
                "metric_definition_version": "v1",
                "scope_hash": "scope",
            },
            "summary": {"non_mitigated": 1},
            "open_finding_keys": ["asset|plugin|443|tcp"],
            "fixed_finding_keys": [],
            "resurfaced_finding_keys": [],
        }
        compact, version, open_blob, fixed_blob, resurfaced_blob = (
            _compact_legacy_history_payload(payload)
        )
        self.assertNotIn("open_finding_keys", compact)
        restored = _history_snapshot_from_storage(
            compact, version, open_blob, fixed_blob, resurfaced_blob
        )
        self.assertEqual(len(restored.open_finding_keys), 1)

    def test_postgresql_registry_exposes_the_domain_operations(self) -> None:
        from tenable_reports.infrastructure.report_registry_postgresql import (
            PostgresReportRegistry,
        )

        expected = {
            "register_report",
            "get_report",
            "get_main",
            "get_main_snapshot",
            "list_main_snapshots_before",
            "promote_main",
            "auto_promote_if_empty",
            "soft_delete",
            "restore",
            "list_reports",
            "reference_events",
        }
        self.assertTrue(expected.issubset(set(dir(PostgresReportRegistry))))

    def test_legacy_migration_preserves_history_and_archives_audit_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            history_path = directory / "history.sqlite"
            source = SQLiteSnapshotRepository(history_path)
            source.publish(HistorySnapshot(
                snapshot_id="snapshot-1",
                run_id="run-1",
                period_id="2026-07",
                period_start_at="2026-07-01T03:00:00Z",
                period_end_at="2026-08-01T03:00:00Z",
                generated_at="2026-08-01T12:00:00Z",
                compatibility=SnapshotCompatibility(
                    client_id="cliente",
                    tenant_id="tenant",
                    execution_type="AUTOMATIC_MONTHLY",
                    period_mode="PREVIOUS_CALENDAR_MONTH",
                    timezone="America/Fortaleza",
                    metric_definition_version="v1",
                    scope_hash="scope",
                ),
                summary={"non_mitigated": 7},
                open_finding_keys=("finding-1",),
                fixed_finding_keys=(),
                resurfaced_finding_keys=(),
                tag_snapshots=(),
            ))
            audit_path = directory / "audit.sqlite"
            connection = sqlite3.connect(audit_path)
            try:
                connection.execute("create table audit_summary (period text, total integer)")
                connection.execute("insert into audit_summary values ('2026-07', 7)")
                connection.commit()
            finally:
                connection.close()
            snapshots = _MemorySnapshotTarget()
            operations = _MemoryOperationsTarget()
            result = migrate_legacy_state(
                roots=(directory,),
                snapshots=snapshots,  # type: ignore[arg-type]
                operations=operations,  # type: ignore[arg-type]
            )
            self.assertEqual(result.history_snapshots, 1)
            self.assertEqual(result.audit_records, 1)
            self.assertEqual(snapshots.values[0].snapshot_id, "snapshot-1")
            self.assertEqual({item["source_kind"] for item in operations.legacy}, {
                "history", "audit"
            })
            self.assertEqual(result.artifacts, 2)


if __name__ == "__main__":
    unittest.main()
