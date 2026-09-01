from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.report_set_purge import (
    ActiveReportSetError,
    MainReportReplacementRequired,
    ReportSetPurgeRecord,
    ReportSetPurgeService,
    UnsafeReportSetPath,
)


class _MemoryPurgeRepository:
    def __init__(self, *records: ReportSetPurgeRecord) -> None:
        self.records = {record.run_id: record for record in records}
        self.snapshots = {record.run_id for record in records}
        self.main_by_period = {
            record.period_id: record.run_id for record in records if record.is_main
        }
        self.fail_purge = False

    def describe(self, run_id: str) -> ReportSetPurgeRecord:
        try:
            return self.records[run_id]
        except KeyError as exc:
            raise KeyError(f"Relatório não encontrado: {run_id}") from exc

    def purge(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None,
        allow_main_gap: bool = False,
    ) -> None:
        if self.fail_purge:
            raise RuntimeError("falha transacional simulada")
        record = self.describe(run_id)
        if not actor or not reason:
            raise ValueError("ator e motivo são obrigatórios")
        if record.is_main:
            if replacement_run_id in record.compatible_replacement_run_ids:
                self.main_by_period[record.period_id] = str(replacement_run_id)
            elif allow_main_gap and not record.compatible_replacement_run_ids:
                self.main_by_period.pop(record.period_id, None)
            else:
                raise MainReportReplacementRequired("Selecione uma geração substituta.")
        self.records.pop(run_id)
        self.snapshots.discard(run_id)


def _record(
    root: Path,
    *,
    run_id: str = "run-a",
    client_id: str = "cliente-a",
    period_id: str = "2026-07",
    is_main: bool = False,
    replacements: tuple[str, ...] = (),
    paths: tuple[Path, ...] | None = None,
) -> ReportSetPurgeRecord:
    selected_paths = paths or (
        root / "manual" / "reports" / client_id / run_id / "base.docx",
        root / "manual" / "reports" / client_id / run_id / "custom.docx",
        root / "manual" / "reports" / client_id / run_id / "tag.docx",
        root / "manual" / "reports" / client_id / run_id / "publication-manifest.json",
    )
    return ReportSetPurgeRecord(
        run_id=run_id,
        client_id=client_id,
        period_id=period_id,
        disk_paths=tuple(str(path) for path in selected_paths),
        document_count=3,
        is_main=is_main,
        compatible_replacement_run_ids=replacements,
    )


class ReportSetPurgeTests(unittest.TestCase):
    def test_purge_removes_documents_manifest_and_compact_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            record = _record(data_root)
            for index, raw_path in enumerate(record.disk_paths, start=1):
                path = Path(raw_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(bytes([index]) * index)
            repository = _MemoryPurgeRepository(record)
            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
            )

            result = service.purge(
                record.run_id,
                actor="analista-web",
                reason="conjunto de teste obsoleto",
                confirmation="EXCLUIR",
            )

            self.assertEqual(result.deleted_files, 4)
            self.assertEqual(result.deleted_bytes, 10)
            self.assertNotIn(record.run_id, repository.records)
            self.assertNotIn(record.run_id, repository.snapshots)
            self.assertTrue(all(not Path(path).exists() for path in record.disk_paths))

    def test_purge_uses_short_staging_paths_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            run_id = "20260825T002814Z-sanitized-client-attempt-1"
            document = (
                data_root
                / "manual"
                / "reports"
                / "cliente-a"
                / run_id
                / "20260701T000000-20260801T000000"
                / ("relatorio-" + ("x" * 70) + ".docx")
            )
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_bytes(b"conteudo")
            record = _record(data_root, run_id=run_id, paths=(document,))
            repository = _MemoryPurgeRepository(record)
            staged_destinations: list[Path] = []

            def windows_legacy_move(source: Path, destination: Path) -> None:
                staged_destinations.append(destination)
                if len(str(destination)) > 259:
                    raise OSError("caminho temporario excedeu o limite do Windows")
                source.replace(destination)

            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
                move_file=windows_legacy_move,
            )

            result = service.purge(
                record.run_id,
                actor="analista-web",
                reason="conjunto obsoleto",
                confirmation="EXCLUIR",
            )

            self.assertEqual(result.deleted_files, 1)
            self.assertTrue(staged_destinations)
            self.assertLessEqual(
                max(len(str(path)) for path in staged_destinations),
                259,
            )
            self.assertFalse(document.exists())

    def test_main_requires_a_compatible_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            main = _record(
                data_root,
                is_main=True,
                replacements=("run-b",),
                paths=(data_root / "manual" / "reports" / "main.docx",),
            )
            replacement = _record(
                data_root,
                run_id="run-b",
                paths=(data_root / "manual" / "reports" / "replacement.docx",),
            )
            Path(main.disk_paths[0]).parent.mkdir(parents=True, exist_ok=True)
            Path(main.disk_paths[0]).write_bytes(b"main")
            repository = _MemoryPurgeRepository(main, replacement)
            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
            )

            with self.assertRaises(MainReportReplacementRequired):
                service.purge(
                    main.run_id,
                    actor="analista-web",
                    reason="nova coleta aprovada",
                    confirmation="EXCLUIR",
                )

            with self.assertRaises(MainReportReplacementRequired):
                service.purge(
                    main.run_id,
                    actor="analista-web",
                    reason="nova coleta aprovada",
                    confirmation="EXCLUIR",
                    allow_main_gap=True,
                )

            result = service.purge(
                main.run_id,
                actor="analista-web",
                reason="nova coleta aprovada",
                confirmation="EXCLUIR",
                replacement_run_id="run-b",
            )
            self.assertEqual(result.replacement_run_id, "run-b")
            self.assertEqual(repository.main_by_period[main.period_id], "run-b")

    def test_only_main_requires_explicit_gap_confirmation_before_purge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            main = _record(
                data_root,
                is_main=True,
                paths=(data_root / "manual" / "reports" / "main.docx",),
            )
            document = Path(main.disk_paths[0])
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_bytes(b"main")
            repository = _MemoryPurgeRepository(main)
            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
            )

            preview = service.preview(main.run_id).to_dict()
            self.assertTrue(preview["requires_main_gap_confirmation"])

            with self.assertRaises(MainReportReplacementRequired):
                service.purge(
                    main.run_id,
                    actor="analista-web",
                    reason="conjunto de teste obsoleto",
                    confirmation="EXCLUIR",
                )

            result = service.purge(
                main.run_id,
                actor="analista-web",
                reason="conjunto de teste obsoleto",
                confirmation="EXCLUIR",
                allow_main_gap=True,
            )

            self.assertEqual(result.deleted_files, 1)
            self.assertFalse(document.exists())
            self.assertNotIn(main.run_id, repository.records)
            self.assertNotIn(main.period_id, repository.main_by_period)

    def test_purge_blocks_paths_outside_the_configured_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            outside = root / "fora.docx"
            outside.write_bytes(b"preservar")
            record = _record(data_root, paths=(outside,))
            repository = _MemoryPurgeRepository(record)
            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
            )

            with self.assertRaises(UnsafeReportSetPath):
                service.purge(
                    record.run_id,
                    actor="analista-web",
                    reason="teste de proteção",
                    confirmation="EXCLUIR",
                )

            self.assertTrue(outside.exists())
            self.assertIn(record.run_id, repository.records)

    def test_purge_is_blocked_while_same_client_has_an_active_generation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            record = _record(data_root)
            repository = _MemoryPurgeRepository(record)
            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: ({
                    "client_id": record.client_id,
                    "run_id": "another-run",
                    "status": "RUNNING",
                },),
            )

            with self.assertRaises(ActiveReportSetError):
                service.purge(
                    record.run_id,
                    actor="analista-web",
                    reason="teste de concorrência",
                    confirmation="EXCLUIR",
                )

    def test_disk_staging_failure_restores_files_and_preserves_database_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            paths = (
                data_root / "manual" / "reports" / "base.docx",
                data_root / "manual" / "reports" / "custom.docx",
            )
            record = _record(data_root, paths=paths)
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(path.name.encode("utf-8"))
            repository = _MemoryPurgeRepository(record)
            moved: list[Path] = []

            def fail_on_second(source: Path, destination: Path) -> None:
                if moved:
                    raise OSError("falha de disco simulada")
                source.replace(destination)
                moved.append(source)

            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
                move_file=fail_on_second,
            )

            with self.assertRaisesRegex(OSError, "falha de disco"):
                service.purge(
                    record.run_id,
                    actor="analista-web",
                    reason="teste de rollback no disco",
                    confirmation="EXCLUIR",
                )

            self.assertEqual(paths[0].read_bytes(), b"base.docx")
            self.assertEqual(paths[1].read_bytes(), b"custom.docx")
            self.assertIn(record.run_id, repository.records)

    def test_database_failure_restores_staged_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory) / "data"
            record = _record(
                data_root,
                paths=(data_root / "manual" / "reports" / "base.docx",),
            )
            document = Path(record.disk_paths[0])
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_bytes(b"conteudo")
            repository = _MemoryPurgeRepository(record)
            repository.fail_purge = True
            service = ReportSetPurgeService(
                data_root=data_root,
                repository=repository,
                active_jobs=lambda: (),
            )
