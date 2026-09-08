from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from tenable_reports.application.publishing import (
    PublicationDocument,
    PublicationDocumentReplacement,
    _backup_path_for_transaction,
    create_publication_manifest,
    refresh_publication_documents_atomically,
    replace_publication_documents_atomically,
    sha256_file,
    write_json_atomic,
)


def _document(path: Path, text: str) -> Path:
    document = Document()
    document.add_paragraph(text)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    return path


class AtomicPublicationTests(unittest.TestCase):
    def test_transaction_backup_name_does_not_repeat_long_document_name(self) -> None:
        destination = Path("C:/reports") / ("relatorio-" + ("x" * 180) + ".docx")

        backup = _backup_path_for_transaction(
            destination,
            transaction_id="a" * 32,
            operation="backfill",
        )

        self.assertEqual(backup.parent, destination.parent)
        self.assertTrue(backup.name.startswith(".backfill-" + ("a" * 32) + "-"))
        self.assertTrue(backup.name.endswith(".bak"))
        self.assertLess(len(backup.name), 70)
        self.assertNotIn(destination.name, backup.name)

    def test_manifest_can_use_cloud_as_primary_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            dataset = directory / "cloud-report.json"
            dataset.write_text("{}", encoding="utf-8")
            document = _document(directory / "cloud.docx", "Documento Cloud")

            manifest = create_publication_manifest(
                output_path=directory / "publication-manifest.json",
                client_id="cliente-a",
                tenant_id="tenant-a",
                run_id="run-cloud-first",
                execution_type="AUTOMATIC_MONTHLY",
                period={"period_id": "2026-08"},
                dataset_path=dataset,
                primary_dataset_component="cloud",
                documents=(
                    PublicationDocument(
                        document,
                        "cloud",
                        document_variant="expanded",
                    ),
                ),
                history_database=None,
            )

            payload = json.loads(manifest.read_text(encoding="utf-8"))

        self.assertEqual(
            payload["source_datasets"],
            {"cloud": payload["source_dataset"]},
        )

    def test_json_write_retries_windows_sharing_violation(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            target = Path(directory_name) / "state.json"
            real_replace = __import__("os").replace
            calls = 0

            def flaky_replace(source, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    error = PermissionError(13, "Access is denied")
                    error.winerror = 5
                    raise error
                return real_replace(source, destination)

            with (
                patch(
                    "tenable_reports.application.publishing.os.replace",
                    side_effect=flaky_replace,
                ),
                patch("tenable_reports.application.publishing.time.sleep") as sleeper,
            ):
                write_json_atomic(target, {"status": "PROCESSING"})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {
                "status": "PROCESSING"
            })
            sleeper.assert_called_once()

    def _manifest(self, directory: Path) -> tuple[Path, Path, Path, Path]:
        dataset = directory / "dataset.json"
        dataset.write_text("{}", encoding="utf-8")
        base = _document(directory / "base.docx", "Documento original")
        cloud = _document(directory / "cloud.docx", "Documento Cloud")
        manifest = create_publication_manifest(
            output_path=directory / "publication-manifest.json",
            client_id="cliente-a",
            tenant_id="tenant-a",
            run_id="run-a",
            execution_type="AUTOMATIC_MONTHLY",
            period={
                "period_id": "2026-07",
                "mode": "PREVIOUS_CALENDAR_MONTH",
                "timezone": "America/Fortaleza",
                "start_at": "2026-07-01T03:00:00Z",
                "end_at": "2026-08-01T03:00:00Z",
            },
            dataset_path=dataset,
            documents=(
                PublicationDocument(base, "base"),
                PublicationDocument(cloud, "cloud", document_variant="expanded"),
            ),
            history_database=None,
        )
        return manifest, dataset, base, cloud

    def test_invalid_staged_document_preserves_original_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            manifest, dataset, base, _ = self._manifest(directory)
            original_hash = sha256_file(base)
            original_manifest = manifest.read_bytes()
            invalid = directory / ".repair" / "base.docx"
            invalid.parent.mkdir(parents=True)
            invalid.write_bytes(b"not-a-docx")

            with self.assertRaisesRegex(ValueError, "DOCX invalido"):
                replace_publication_documents_atomically(
                    manifest_path=manifest,
                    dataset_path=dataset,
                    replacements=(PublicationDocumentReplacement(
                        staged_path=invalid,
                        destination=PublicationDocument(base, "base"),
                    ),),
                )

            self.assertEqual(sha256_file(base), original_hash)
            self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_valid_vm_replacement_preserves_cloud_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            manifest, dataset, base, cloud = self._manifest(directory)
            old_hash = sha256_file(base)
            staged = _document(directory / ".repair" / "base.docx", "Documento reparado")

            replace_publication_documents_atomically(
                manifest_path=manifest,
                dataset_path=dataset,
                replacements=(PublicationDocumentReplacement(
                    staged_path=staged,
                    destination=PublicationDocument(base, "base"),
                ),),
            )

            payload = json.loads(manifest.read_text(encoding="utf-8"))
            documents = payload["documents"]
            base_entry = next(item for item in documents if item["document_kind"] == "base")
            cloud_entry = next(item for item in documents if item["document_kind"] == "cloud")
            self.assertNotEqual(sha256_file(base), old_hash)
            self.assertEqual(base_entry["sha256"], sha256_file(base))
            self.assertEqual(cloud_entry["path"], str(cloud.resolve()))
            self.assertTrue(cloud.is_file())

    def test_commit_failure_restores_documents_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            manifest, dataset, base, _ = self._manifest(directory)
            original_hash = sha256_file(base)
            original_manifest = manifest.read_bytes()
            staged = _document(
                directory / ".repair" / "base.docx",
                "Documento que nao pode ser confirmado",
            )

            def fail_commit() -> None:
                raise RuntimeError("falha de persistencia")

            with self.assertRaisesRegex(RuntimeError, "falha de persistencia"):
                replace_publication_documents_atomically(
                    manifest_path=manifest,
                    dataset_path=dataset,
                    replacements=(PublicationDocumentReplacement(
                        staged_path=staged,
                        destination=PublicationDocument(base, "base"),
                    ),),
                    commit_callback=fail_commit,
                )

            self.assertEqual(sha256_file(base), original_hash)
            self.assertEqual(manifest.read_bytes(), original_manifest)

    def test_refresh_documents_preserves_cleaned_dataset_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            manifest, dataset, base, cloud = self._manifest(directory)
            source_dataset = json.loads(manifest.read_text(encoding="utf-8"))[
                "source_dataset"
            ]
            dataset.unlink()
            staged_base = _document(
                directory / ".translation" / "base.docx",
                "Descrição traduzida",
            )
            staged_cloud = _document(
                directory / ".translation" / "cloud.docx",
                "Descrição Cloud traduzida",
            )

            refresh_publication_documents_atomically(
                manifest_path=manifest,
                replacements=(
                    PublicationDocumentReplacement(
                        staged_path=staged_base,
                        destination=PublicationDocument(base, "base"),
                    ),
                    PublicationDocumentReplacement(
                        staged_path=staged_cloud,
                        destination=PublicationDocument(
                            cloud,
                            "cloud",
                            document_variant="expanded",
                        ),
                    ),
                ),
                audit_metadata={"operation": "TRANSLATION_BACKFILL"},
            )
            payload = json.loads(manifest.read_text(encoding="utf-8"))

            self.assertEqual(payload["source_dataset"], source_dataset)
            self.assertEqual(
                payload["document_backfill"],
                {"operation": "TRANSLATION_BACKFILL"},
            )
            self.assertEqual(Document(base).paragraphs[0].text, "Descrição traduzida")
            self.assertEqual(
                Document(cloud).paragraphs[0].text,
                "Descrição Cloud traduzida",
            )
            hashes = {item["document_kind"]: item["sha256"] for item in payload["documents"]}
            self.assertEqual(hashes["base"], sha256_file(base))
            self.assertEqual(hashes["cloud"], sha256_file(cloud))


if __name__ == "__main__":
    unittest.main()
