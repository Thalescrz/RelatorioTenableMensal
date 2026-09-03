from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tenable_reports.application.report_archives import (
    ArchiveClient,
    ArchiveDocument,
    ArchiveReportSet,
    EmptyReportArchiveError,
    UnsafeReportArchivePath,
    build_monthly_report_archive,
    build_report_set_archive,
)


class ReportArchiveTests(unittest.TestCase):
    def test_monthly_archive_uses_only_main_and_separates_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            temporary_root = root / "temporary"
            main_a = data_root / "a" / "main-a.docx"
            old_a = data_root / "a" / "old-a.docx"
            main_b = data_root / "b" / "main-b.docx"
            for path, content in (
                (main_a, b"main-a"),
                (old_a, b"old-a"),
                (main_b, b"main-b"),
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)

            result = build_monthly_report_archive(
                data_root=data_root,
                temporary_root=temporary_root,
                period_id="2026-08",
                clients=(
                    ArchiveClient(
                        client_id="cliente-a",
                        display_name="Cliente A",
                        reports=(
                            ArchiveReportSet(
                                client_id="cliente-a",
                                display_name="Cliente A",
                                run_id="run-main-a",
                                period_id="2026-08",
                                is_main=True,
                                documents=(ArchiveDocument(main_a),),
                            ),
                            ArchiveReportSet(
                                client_id="cliente-a",
                                display_name="Cliente A",
                                run_id="run-old-a",
                                period_id="2026-08",
                                is_main=False,
                                documents=(ArchiveDocument(old_a),),
                            ),
                        ),
                    ),
                    ArchiveClient(
                        client_id="cliente-b",
                        display_name="Cliente B",
                        reports=(ArchiveReportSet(
                            client_id="cliente-b",
                            display_name="Cliente B",
                            run_id="run-main-b",
                            period_id="2026-08",
                            is_main=True,
                            documents=(ArchiveDocument(main_b),),
                        ),),
                    ),
                    ArchiveClient(
                        client_id="cliente-c",
                        display_name="Cliente C",
                        reports=(),
                    ),
                ),
            )

            with zipfile.ZipFile(result.path) as package:
                names = package.namelist()
                summary = package.read(
                    "Relatorios-Tenable-2026-08/RESUMO.txt"
                ).decode("utf-8")

            self.assertEqual(result.download_name, "Relatorios-Tenable-2026-08.zip")
            self.assertIn(
                "Relatorios-Tenable-2026-08/Cliente A/main-a.docx", names
            )
            self.assertIn(
                "Relatorios-Tenable-2026-08/Cliente B/main-b.docx", names
            )
            self.assertNotIn(
                "Relatorios-Tenable-2026-08/Cliente A/old-a.docx", names
            )
            self.assertIn("Cliente C: sem conjunto MAIN", summary)
            self.assertEqual(result.included_clients, 2)
            self.assertEqual(result.included_documents, 2)

    def test_monthly_archive_can_identify_responsible_scope_in_download_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            result = build_monthly_report_archive(
                data_root=root / "data",
                temporary_root=root / "temporary",
                period_id="2026-08",
                clients=(),
                download_scope="Analista Principal",
            )

            self.assertEqual(
                result.download_name,
                "Relatorios-Tenable-Analista-Principal-2026-08.zip",
            )

    def test_report_set_archive_accepts_selected_non_main_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            document = data_root / "reports" / "report.docx"
            document.parent.mkdir(parents=True)
            document.write_bytes(b"report")

            result = build_report_set_archive(
                data_root=data_root,
                temporary_root=root / "temporary",
                report=ArchiveReportSet(
                    client_id="cliente-a",
                    display_name="Cliente A",
                    run_id="run-non-main",
                    period_id="2026-08",
                    is_main=False,
                    documents=(ArchiveDocument(document),),
                ),
            )

            with zipfile.ZipFile(result.path) as package:
                self.assertIn(
                    "Relatorios-Tenable-2026-08/Cliente A/report.docx",
                    package.namelist(),
                )

    def test_archive_records_missing_document_and_disambiguates_duplicate_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            first = data_root / "first" / "report.docx"
            second = data_root / "second" / "report.docx"
            missing = data_root / "missing" / "absent.docx"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_bytes(b"first")
            second.write_bytes(b"second")

            result = build_report_set_archive(
                data_root=data_root,
                temporary_root=root / "temporary",
                report=ArchiveReportSet(
                    client_id="cliente-a",
                    display_name="Cliente A",
                    run_id="run-a",
                    period_id="2026-08",
                    is_main=True,
                    documents=(
                        ArchiveDocument(first),
                        ArchiveDocument(second),
                        ArchiveDocument(missing),
                    ),
                ),
            )

            with zipfile.ZipFile(result.path) as package:
                names = package.namelist()
                summary = package.read(
                    "Relatorios-Tenable-2026-08/RESUMO.txt"
                ).decode("utf-8")

            self.assertIn(
                "Relatorios-Tenable-2026-08/Cliente A/report.docx", names
            )
            self.assertIn(
                "Relatorios-Tenable-2026-08/Cliente A/report (2).docx", names
            )
            self.assertIn("absent.docx: arquivo ausente", summary)

    def test_archive_rejects_document_outside_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            outside = root / "outside.docx"
            outside.write_bytes(b"outside")

            with self.assertRaises(UnsafeReportArchivePath):
                build_report_set_archive(
                    data_root=data_root,
                    temporary_root=root / "temporary",
                    report=ArchiveReportSet(
                        client_id="cliente-a",
                        display_name="Cliente A",
                        run_id="run-a",
                        period_id="2026-08",
                        is_main=True,
                        documents=(ArchiveDocument(outside),),
                    ),
                )

    def test_archive_rejects_set_without_available_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(EmptyReportArchiveError):
                build_report_set_archive(
                    data_root=root / "data",
                    temporary_root=root / "temporary",
                    report=ArchiveReportSet(
                        client_id="cliente-a",
                        display_name="Cliente A",
                        run_id="run-a",
                        period_id="2026-08",
                        is_main=True,
                        documents=(),
                    ),
                )

    def test_monthly_archive_with_only_missing_documents_contains_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            missing = data_root / "cliente-a" / "ausente.docx"

            result = build_monthly_report_archive(
                data_root=data_root,
                temporary_root=root / "temporary",
                period_id="2026-08",
                clients=(ArchiveClient(
                    client_id="cliente-a",
                    display_name="Cliente A",
                    reports=(ArchiveReportSet(
                        client_id="cliente-a",
                        display_name="Cliente A",
                        run_id="run-a",
                        period_id="2026-08",
                        is_main=True,
                        documents=(ArchiveDocument(missing),),
                    ),),
                ),),
            )

            with zipfile.ZipFile(result.path) as package:
                self.assertEqual(
                    package.namelist(),
                    ["Relatorios-Tenable-2026-08/RESUMO.txt"],
                )
                summary = package.read(
                    "Relatorios-Tenable-2026-08/RESUMO.txt"
                ).decode("utf-8")

            self.assertIn("ausente.docx: arquivo ausente", summary)
            self.assertEqual(result.included_clients, 0)
            self.assertEqual(result.included_documents, 0)

    def test_monthly_archive_rejects_nonexistent_calendar_month(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "formato AAAA-MM"):
                build_monthly_report_archive(
                    data_root=root / "data",
                    temporary_root=root / "temporary",
                    period_id="2026-99",
                    clients=(),
                )

    def test_monthly_archive_uses_most_recent_main_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            older = data_root / "older.docx"
            newer = data_root / "newer.docx"
            data_root.mkdir(parents=True)
            older.write_bytes(b"older")
            newer.write_bytes(b"newer")

            result = build_monthly_report_archive(
                data_root=data_root,
                temporary_root=root / "temporary",
                period_id="2026-08",
                clients=(ArchiveClient(
                    client_id="cliente-a",
                    display_name="Cliente A",
                    reports=(
                        ArchiveReportSet(
                            client_id="cliente-a",
                            display_name="Cliente A",
                            run_id="run-older",
                            period_id="2026-08",
                            is_main=True,
                            documents=(ArchiveDocument(older),),
                            main_set_at="2026-08-02T10:00:00Z",
                        ),
                        ArchiveReportSet(
                            client_id="cliente-a",
                            display_name="Cliente A",
                            run_id="run-newer",
                            period_id="2026-08",
                            is_main=True,
                            documents=(ArchiveDocument(newer),),
                            main_set_at="2026-08-03T10:00:00Z",
                        ),
                    ),
                ),),
            )

            with zipfile.ZipFile(result.path) as package:
                names = package.namelist()
                summary = package.read(
                    "Relatorios-Tenable-2026-08/RESUMO.txt"
                ).decode("utf-8")

            self.assertIn(
                "Relatorios-Tenable-2026-08/Cliente A/newer.docx", names
            )
            self.assertNotIn(
                "Relatorios-Tenable-2026-08/Cliente A/older.docx", names
            )
            self.assertIn("mais de um MAIN", summary)
            self.assertIn("usado run-newer", summary)


if __name__ == "__main__":
    unittest.main()
