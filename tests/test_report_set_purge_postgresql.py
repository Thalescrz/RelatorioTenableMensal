from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.domain.report_reference import (
    READY_STATUS,
    ReportCandidate,
    ReportOrigin,
    reference_key_for_candidate,
)
from tenable_reports.infrastructure.report_set_purge_postgresql import (
    PostgresReportSetPurgeRepository,
)


def _candidate(run_id: str) -> ReportCandidate:
    return ReportCandidate(
        run_id=run_id,
        client_id="cliente-a",
        tenant_id="tenant-a",
        origin=ReportOrigin.MANUAL,
        execution_type="MANUAL",
        period_start_at="2026-07-01T03:00:00Z",
        period_end_at="2026-08-01T03:00:00Z",
        period_mode="CUSTOM_DATE_RANGE",
        timezone="America/Fortaleza",
        scope_hash="scope-a",
        metric_definition_version="v1",
        publication_status=READY_STATUS,
        documents_valid=True,
    )


class _Cursor:
    def __init__(self, row=None, rows=()) -> None:
        self.row = row
        self.rows = rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, root: Path) -> None:
        self.root = root

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _parameters=()):
        normalized = " ".join(str(query).split()).lower()
        report_root = self.root / "manual" / "reports" / "cliente-a" / "run-a"
        if "select r.dataset_path" in normalized:
            return _Cursor((
                str(report_root / "dataset.json"),
                str(report_root / "publication-manifest.json"),
            ))
        if "select p.manifest_path" in normalized:
            return _Cursor((
                str(report_root / "publication-manifest.json"),
                str(report_root / "dataset.json"),
                "postgresql://tenable_reports/history_snapshots/run-a",
            ))
        if "select d.path" in normalized:
            return _Cursor(rows=(
                (str(report_root / "base.docx"),),
                (str(report_root / "custom.docx"),),
                (str(report_root / "tag.docx"),),
                (str(report_root / "cloud-base.docx"),),
                (str(report_root / "cloud-expanded.docx"),),
            ))
        if "select a.path" in normalized:
            return _Cursor(rows=(
                (str(report_root / "asset-manifest.json"),),
                (str(report_root / "cloud-report-dataset.json"),),
            ))
        raise AssertionError(normalized)


class _Database:
    def __init__(self, root: Path) -> None:
        self.connection_value = _Connection(root)

    def connection(self):
        return self.connection_value


class PostgresReportSetPurgeRepositoryTests(unittest.TestCase):
    def test_describe_returns_only_exact_files_and_compatible_replacements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            registry = InMemoryReportRegistry()
            old = _candidate("run-a")
            replacement = _candidate("run-b")
            registry.register_report(old)
            registry.register_report(replacement)
            registry.promote_main(
                reference_key_for_candidate(old),
                old.run_id,
                actor="sistema",
                reason="automático",
            )
            repository = PostgresReportSetPurgeRepository(
                database=_Database(data_root),
                registry=registry,
            )

            record = repository.describe(old.run_id)

            self.assertTrue(record.is_main)
            self.assertEqual(record.document_count, 5)
            self.assertEqual(record.compatible_replacement_run_ids, ("run-b",))
            self.assertEqual(len(record.disk_paths), 9)
            self.assertTrue(any(
                path.endswith("cloud-base.docx") for path in record.disk_paths
            ))
            self.assertTrue(any(
                path.endswith("cloud-report-dataset.json")
                for path in record.disk_paths
            ))
            self.assertFalse(any(path.startswith("postgresql:") for path in record.disk_paths))

    def test_purge_delegates_the_atomic_database_removal_to_the_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = InMemoryReportRegistry()
            old = _candidate("run-a")
            replacement = _candidate("run-b")
            key = reference_key_for_candidate(old)
            registry.register_report(old)
            registry.register_report(replacement)
            registry.promote_main(key, old.run_id, actor="sistema", reason="automático")
            repository = PostgresReportSetPurgeRepository(
                database=_Database(Path(directory) / "data"),
                registry=registry,
            )

            repository.purge(
                old.run_id,
                actor="analista",
                reason="conjunto inválido",
                replacement_run_id=replacement.run_id,
            )

            with self.assertRaises(KeyError):
                registry.get_report(old.run_id)
            self.assertEqual(registry.get_main(key).run_id, replacement.run_id)

    def test_purge_can_explicitly_leave_the_period_without_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            registry = InMemoryReportRegistry()
            old = _candidate("run-a")
            key = reference_key_for_candidate(old)
            registry.register_report(old)
            registry.promote_main(key, old.run_id, actor="sistema", reason="automático")
            repository = PostgresReportSetPurgeRepository(
                database=_Database(Path(directory) / "data"),
                registry=registry,
            )

            repository.purge(
                old.run_id,
                actor="analista",
                reason="conjunto inválido",
                replacement_run_id=None,
                allow_main_gap=True,
            )

            with self.assertRaises(KeyError):
                registry.get_report(old.run_id)
            self.assertIsNone(registry.get_main(key))


if __name__ == "__main__":
    unittest.main()
