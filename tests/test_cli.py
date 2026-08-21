from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tenable_reports.cli as cli_module
from tenable_reports.cli import _period_filters, _scoped_output_root, main
from tenable_reports.domain.reporting import previous_calendar_month
from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.application.publishing import PublicationDocument
from tenable_reports.application.tag_scope import VmTag
from tests.test_report_main_backfill import valid_run as valid_backfill_run


ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def _tag_run_fixture(self, directory: Path):
        dataset = directory / "dataset.json"
        dataset.write_text("{}", encoding="utf-8")
        tag_a_dataset = directory / "tag-a.json"
        tag_b_dataset = directory / "tag-b.json"
        tag_a_dataset.write_text("{}", encoding="utf-8")
        tag_b_dataset.write_text("{}", encoding="utf-8")
        period = previous_calendar_month(
            reference_at="2026-08-01T00:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        selections = (
            SimpleNamespace(
                tag_uuid="tag-a", category_uuid="cat-a",
                category_name="Equipe", value="Infra",
                generate_report=True, include_temporal_comparison=True,
            ),
            SimpleNamespace(
                tag_uuid="tag-b", category_uuid="cat-a",
                category_name="Equipe", value="Sistemas",
                generate_report=True, include_temporal_comparison=False,
            ),
        )
        profile = SimpleNamespace(
            client_id="cliente-a", tenant_id="tenant-a", display_name="CLIENTE A",
            report=SimpleNamespace(
                tag_reports=SimpleNamespace(enabled=True, tags=selections),
            ),
        )
        collected = SimpleNamespace(
            output_root=directory,
            run_id="run-a",
            dataset_path=dataset,
            tag_artifacts=(
                SimpleNamespace(
                    tag=VmTag("tag-a", "cat-a", "Equipe", "Infra"),
                    dataset_path=tag_a_dataset,
                ),
                SimpleNamespace(
                    tag=VmTag("tag-b", "cat-a", "Equipe", "Sistemas"),
                    dataset_path=tag_b_dataset,
                ),
            ),
            tag_enriched_dataset_paths={
                "tag-a": tag_a_dataset,
                "tag-b": tag_b_dataset,
            },
            tag_reports_requested=2,
            warnings=(),
            history_database_path=None,
            history_store=None,
            history_publication=None,
            snapshot_repository=None,
            report_registry=None,
            to_dict=lambda: {"status": "complete", "warnings": []},
        )
        args = SimpleNamespace(
            confirm_live_api=True, profile="profile.json", mode="automatic",
            base_output=None, custom_output=None, template="template.docx",
            assets_dir="assets", mask_sensitive=True, database_env_file=None,
        )
        return period, profile, collected, args

    def test_complete_run_generates_selected_tag_reports_and_typed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            period, profile, collected, args = self._tag_run_fixture(directory)
            manifest = directory / "publication.json"
            manifest.write_text("{}", encoding="utf-8")
            captured: dict[str, object] = {}
            stdout = io.StringIO()

            def capture_manifest(**kwargs):
                captured["documents"] = kwargs["documents"]
                return manifest

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_period_for_mode", return_value=period),
                patch.object(cli_module, "_execute_period", return_value=collected),
                patch.object(cli_module, "generate_full_base_report", return_value=SimpleNamespace(output_path=directory / "base.docx")),
                patch.object(cli_module, "generate_customizations_report", return_value=SimpleNamespace(output_path=directory / "custom.docx", rendered_modules=(), omitted_modules=())),
                patch.object(cli_module, "generate_tag_report", side_effect=lambda **kwargs: SimpleNamespace(output_path=Path(kwargs["output_path"]))),
                patch.object(cli_module, "create_publication_manifest", side_effect=capture_manifest),
                patch.object(cli_module, "_postgres_operations", return_value=None),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli_module.command_run_client(args), 0)

            lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
            progress = [line for line in lines if line.get("event") == "TAG_REPORT_PROGRESS"]
            result = lines[-1]
            self.assertEqual(len(progress), 2)
            self.assertEqual(result["tag_reports_requested"], 2)
            self.assertEqual(result["tag_reports_generated"], 2)
            self.assertEqual(result["tag_reports_failed"], 0)
            documents = captured["documents"]
            self.assertTrue(all(isinstance(item, PublicationDocument) for item in documents))
            self.assertEqual(
                [item.document_kind for item in documents],
                ["base", "custom", "tag", "tag"],
            )

    def test_tag_render_failure_keeps_general_documents_and_emits_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            period, profile, collected, args = self._tag_run_fixture(directory)
            manifest = directory / "publication.json"
            manifest.write_text("{}", encoding="utf-8")
            captured: dict[str, object] = {}
            stdout = io.StringIO()

            def render_tag(**kwargs):
                if str(kwargs["dataset_path"]).endswith("tag-b.json"):
                    raise RuntimeError("falha sintética")
                return SimpleNamespace(output_path=Path(kwargs["output_path"]))

            def capture_manifest(**kwargs):
                captured["documents"] = kwargs["documents"]
                return manifest

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_period_for_mode", return_value=period),
                patch.object(cli_module, "_execute_period", return_value=collected),
                patch.object(cli_module, "generate_full_base_report", return_value=SimpleNamespace(output_path=directory / "base.docx")),
                patch.object(cli_module, "generate_customizations_report", return_value=SimpleNamespace(output_path=directory / "custom.docx", rendered_modules=(), omitted_modules=())),
                patch.object(cli_module, "generate_tag_report", side_effect=render_tag),
                patch.object(cli_module, "create_publication_manifest", side_effect=capture_manifest),
                patch.object(cli_module, "_postgres_operations", return_value=None),
                contextlib.redirect_stdout(stdout),
            ):
                self.assertEqual(cli_module.command_run_client(args), 0)

            result = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(result["status"], "complete_with_warnings")
            self.assertEqual(result["tag_reports_generated"], 1)
            self.assertEqual(result["tag_reports_failed"], 1)
            self.assertEqual(result["warnings"][-1]["tag_uuid"], "tag-b")
            self.assertEqual(
                [item.document_kind for item in captured["documents"]],
                ["base", "custom", "tag"],
            )
    def test_backfill_report_main_is_dry_run_by_default(self) -> None:
        registry = InMemoryReportRegistry()
        registry.register_report(valid_backfill_run("run-backfill"))
        database = SimpleNamespace(apply_migrations=lambda: ())
        config = SimpleNamespace(safe_location="localhost/tenable_reports")
        stdout = io.StringIO()
        with (
            patch.object(cli_module, "_load_database_config", return_value=config),
            patch.object(cli_module, "PostgresDatabase", return_value=database),
            patch.object(cli_module, "PostgresReportRegistry", return_value=registry, create=True),
            patch.object(
                cli_module, "PostgresOperationsRepository",
                return_value=SimpleNamespace(retention_state=lambda: {
                    "history_confirmed_run_ids": (), "main_run_ids": (),
                }),
            ),
            contextlib.redirect_stdout(stdout),
        ):
            self.assertEqual(main(["backfill-report-main"]), 0)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["applied"])
        self.assertEqual(payload["promotions"][0]["run_id"], "run-backfill")
        self.assertIsNone(registry.get_main(cli_module.reference_key_for_candidate(valid_backfill_run("x"))))

    def test_complete_run_uses_editorial_filenames_by_default(self) -> None:
        captured: dict[str, Path] = {}
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            dataset = directory / "dataset.json"
            dataset.write_text("{}", encoding="utf-8")
            period = previous_calendar_month(
                reference_at="2026-08-01T00:00:00-03:00",
                timezone_name="America/Fortaleza",
            )
            profile = SimpleNamespace(
                client_id="cliente-a", tenant_id="tenant-a", display_name="CLIENTE A"
            )
            collected = SimpleNamespace(
                output_root=directory, run_id="run-a", dataset_path=dataset,
                history_database_path=None, history_store=None,
                history_publication=None, snapshot_repository=None, report_registry=None,
                to_dict=lambda: {},
            )
            args = SimpleNamespace(
                confirm_live_api=True, profile="profile.json", mode="automatic",
                base_output=None, custom_output=None, template="template.docx",
                assets_dir="assets", mask_sensitive=True, database_env_file=None,
            )

            def base_generator(**kwargs):
                captured["base"] = Path(kwargs["output_path"])
                return SimpleNamespace(output_path=captured["base"])

            def custom_generator(**kwargs):
                captured["custom"] = Path(kwargs["output_path"])
                return SimpleNamespace(
                    output_path=captured["custom"], rendered_modules=(), omitted_modules=(),
                )

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_period_for_mode", return_value=period),
                patch.object(cli_module, "_execute_period", return_value=collected),
                patch.object(cli_module, "generate_full_base_report", side_effect=base_generator),
                patch.object(cli_module, "generate_customizations_report", side_effect=custom_generator),
                patch.object(cli_module, "create_publication_manifest", return_value=directory / "manifest.json"),
                patch.object(cli_module, "_postgres_operations", return_value=None),
                patch("builtins.print"),
            ):
                self.assertEqual(cli_module.command_run_client(args), 0)

        self.assertEqual(
            captured["base"].name,
            "[CLIENTE A] Relatório de Vulnerabilidades Tenable JUL26.docx",
        )
        self.assertEqual(
            captured["custom"].name,
            "[CLIENTE A] Inteligência e Customizações Tenable JUL26.docx",
        )

    def test_complete_run_records_manifest_only_after_history_is_finalized(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            dataset = directory / "dataset.json"
            dataset.write_text("{}", encoding="utf-8")
            base = directory / "base.docx"
            custom = directory / "custom.docx"
            manifest = directory / "publication.json"
            for category in ("raw", "snapshots", "normalized", "report-datasets"):
                transient = directory / category / "cliente-a" / "run-a"
                transient.mkdir(parents=True)
                (transient / "payload.bin").write_bytes(b"temporary")
            period = SimpleNamespace(
                period_id="2026-07",
                to_dict=lambda: {"period_id": "2026-07"},
            )
            profile = SimpleNamespace(client_id="cliente-a", tenant_id="tenant-a")
            preparation = object()
            snapshot_repository = object()
            registry = object()
            collected = SimpleNamespace(
                output_root=directory,
                run_id="run-a",
                dataset_path=dataset,
                history_database_path=None,
                history_store={"backend": "postgresql", "location": "local"},
                history_publication=preparation,
                snapshot_repository=snapshot_repository,
                report_registry=registry,
                to_dict=lambda: {"status": "complete"},
            )
            operations = SimpleNamespace(
                record_publication_manifest=lambda path: events.append("record_manifest"),
                record_cleanup_status=lambda run_id, status, **kwargs: events.append(
                    f"cleanup_{status.lower()}"
                ),
            )
            args = SimpleNamespace(
                confirm_live_api=True,
                profile="profile.json",
                mode="automatic",
                base_output=str(base),
                custom_output=str(custom),
                template="template.docx",
                assets_dir="assets",
                mask_sensitive=True,
                database_env_file="database.env",
            )

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_period_for_mode", return_value=period),
                patch.object(cli_module, "_execute_period", return_value=collected),
                patch.object(
                    cli_module,
                    "generate_full_base_report",
                    side_effect=lambda **kwargs: (
                        events.append("base_docx")
                        or SimpleNamespace(output_path=base)
                    ),
                ),
                patch.object(
                    cli_module,
                    "generate_customizations_report",
                    side_effect=lambda **kwargs: (
                        events.append("custom_docx")
                        or SimpleNamespace(
                            output_path=custom,
                            rendered_modules=(),
                            omitted_modules=(),
                        )
                    ),
                ),
                patch.object(
                    cli_module,
                    "create_publication_manifest",
                    side_effect=lambda **kwargs: (
                        events.append("validate_and_manifest") or manifest
                    ),
                ),
                patch.object(cli_module, "_postgres_operations", return_value=operations),
                patch.object(
                    cli_module,
                    "finalize_history_publication",
                    side_effect=lambda *args, **kwargs: events.append("finalize_history"),
                    create=True,
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(cli_module.command_run_client(args), 0)

        self.assertEqual(events, [
            "base_docx",
            "custom_docx",
            "validate_and_manifest",
            "finalize_history",
            "record_manifest",
            "cleanup_pending",
            "cleanup_complete",
        ])
        for category in ("raw", "snapshots", "normalized", "report-datasets"):
            self.assertFalse((directory / category / "cliente-a" / "run-a").exists())

    def test_monthly_filters_reduce_remote_population_but_keep_all_report_states(self) -> None:
        period = previous_calendar_month(
            reference_at="2026-08-12T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        asset_filters, finding_filters = _period_filters(
            period=period,
            asset_filters_path=None,
            finding_filters_path=None,
        )
        self.assertEqual(asset_filters["since"], period.start_epoch)
        self.assertEqual(asset_filters["types"], ["host"])
        self.assertEqual(finding_filters["since"], period.start_epoch)
        self.assertEqual(finding_filters["state"], ["OPEN", "REOPENED", "FIXED"])
        self.assertEqual(
            finding_filters["severity"],
            ["low", "medium", "high", "critical"],
        )

    def test_period_filters_reject_tag_filters_for_the_general_report(self) -> None:
        period = previous_calendar_month(
            reference_at="2026-08-12T10:00:00-03:00",
            timezone_name="America/Fortaleza",
        )
        with tempfile.TemporaryDirectory() as directory:
            filters_path = Path(directory) / "finding-filters.json"
            filters_path.write_text(
                json.dumps({"tag.Rede": ["Matriz"]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "nao podem limitar a coleta geral"):
                _period_filters(
                    period=period,
                    asset_filters_path=None,
                    finding_filters_path=str(filters_path),
                )

    def test_preview_period_defaults_to_previous_calendar_month(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main([
                "preview-period",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
                "--reference-at",
                "2026-08-12T10:00:00-03:00",
            ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["period"]["period_id"], "2026-07")
        self.assertEqual(payload["execution_type"], "AUTOMATIC_MONTHLY")
        self.assertTrue(payload["storage_directory"].endswith("automatic-monthly"))

    def test_manual_preview_defaults_to_rolling_calendar_month(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main([
                "preview-period",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
                "--mode",
                "manual",
                "--reference-at",
                "2026-08-13T10:00:00-03:00",
            ])
        self.assertEqual(exit_code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["period"]["mode"], "MANUAL_ROLLING_MONTH")
        self.assertEqual(payload["period"]["start_at"], "2026-07-13T13:00:00Z")
        self.assertEqual(payload["period"]["end_at"], "2026-08-13T13:00:00Z")
        self.assertEqual(payload["execution_type"], "MANUAL")
        self.assertTrue(payload["storage_directory"].endswith("manual"))

    def test_storage_scopes_are_separate_and_not_duplicated(self) -> None:
        self.assertEqual(
            _scoped_output_root("data", "AUTOMATIC_MONTHLY"),
            Path("data/automatic-monthly"),
        )
        self.assertEqual(_scoped_output_root("data", "MANUAL"), Path("data/manual"))
        self.assertEqual(
            _scoped_output_root("data/manual", "MANUAL"), Path("data/manual")
        )

    def test_contract_check_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "contract-check-vm",
                    "--profile",
                    str(ROOT / "clients/examples/client-profile.json"),
                    "--env-file",
                    str(ROOT / ".env"),
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_empty_isolated_env_blocks_before_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "TENABLE_ACCESS=\nTENABLE_SECRET=\nTENABLE_BASE_URL=https://cloud.tenable.com\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(
                    [
                        "contract-check-vm",
                        "--confirm-live-api",
                        "--profile",
                        str(ROOT / "clients/examples/client-profile.json"),
                        "--env-file",
                        str(env_file),
                    ]
                )
            self.assertEqual(exit_code, 2)
            self.assertIn("Preencha TENABLE_ACCESS", stderr.getvalue())

    def test_asset_contract_check_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "contract-check-assets",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_phase3_collection_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "collect-phase3",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_link_contract_check_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "contract-check-link",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
                "--asset-export-uuid",
                "asset-fixture",
                "--vm-export-uuid",
                "vm-fixture",
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_monthly_collection_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "collect-monthly",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_manual_collection_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "collect-manual",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_complete_client_run_requires_explicit_live_confirmation(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main([
                "run-client",
                "--profile",
                str(ROOT / "clients/examples/client-profile.json"),
            ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--confirm-live-api", stderr.getvalue())

    def test_generate_base_docx_cli_uses_only_fixture_and_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "proof.docx"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "generate-base-docx",
                    "--profile",
                    str(ROOT / "clients/examples/client-profile.json"),
                    "--dataset",
                    str(ROOT / "tests/fixtures/report-dataset-phase5.json"),
                    "--template",
                    str(ROOT / "templates/corporate/base-v1.docx"),
                    "--output",
                    str(output),
                    "--mask-sensitive",
                ])
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(output.is_file())
            self.assertEqual(payload["period_id"], "2026-07")
            self.assertTrue(payload["masked_sensitive_fields"])

    def test_generate_full_base_docx_cli_builds_phase6_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "full.docx"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main([
                    "generate-full-base-docx",
                    "--profile",
                    str(ROOT / "clients/examples/client-profile.json"),
                    "--dataset",
                    str(ROOT / "tests/fixtures/report-dataset-phase5.json"),
                    "--template",
                    str(ROOT / "templates/corporate/base-v1.docx"),
                    "--assets-dir",
                    str(ROOT / "templates/corporate/assets"),
                    "--output",
                    str(output),
                    "--mask-sensitive",
                ])
            self.assertEqual(exit_code, 0)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(output.is_file())
            self.assertEqual(payload["template_version"], "base-fiel-v2.0")
            self.assertEqual(payload["top_open_rows"], 5)

    def test_customizations_history_requires_normalized_findings(self) -> None:
        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stderr(stderr):
                exit_code = main([
                    "generate-customizations-docx",
                    "--profile",
                    str(ROOT / "clients/examples/client-profile-intelligence-expanded.json"),
                    "--dataset",
                    str(ROOT / "tests/fixtures/report-dataset-phase5.json"),
                    "--template",
                    str(ROOT / "templates/corporate/base-v1.docx"),
                    "--output",
                    str(Path(directory) / "custom.docx"),
                    "--history-database",
                    str(Path(directory) / "history.sqlite"),
                ])
        self.assertEqual(exit_code, 2)
        self.assertIn("--normalized-findings", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
