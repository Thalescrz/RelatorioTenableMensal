from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tenable_reports.cli as cli_module
from tenable_reports.domain.reporting import previous_calendar_month


class CliCollectionRoutingTests(unittest.TestCase):
    def test_exact_snapshot_replay_does_not_load_credentials_or_call_tenable(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            period = previous_calendar_month(
                reference_at="2026-08-12T10:00:00-03:00",
                timezone_name="America/Fortaleza",
            )
            profile = SimpleNamespace(
                client_id="cliente-a",
                tenant_id="tenant-a",
                reporting=SimpleNamespace(
                    vm_export=SimpleNamespace(
                        strategy="combined",
                        num_assets_per_chunk=1000,
                        selective_properties="disabled",
                        historical_source="inventory_beta",
                        historical_fallback="warn_legacy",
                    )
                ),
                report=SimpleNamespace(
                    tag_reports=SimpleNamespace(enabled=False, tags=()),
                ),
            )
            args = SimpleNamespace(
                profile=directory / "profile.json",
                env_file=directory / "client.env",
                database_env_file=directory / "database.env",
                output_root=directory,
                run_id="replay-run",
                historical_source=None,
                num_assets=None,
                vm_export_strategy=None,
                vm_selective_mode=None,
                include_output=False,
                skip_history=True,
            )
            route = SimpleNamespace(
                source=SimpleNamespace(value="snapshot_replay"),
                accuracy=SimpleNamespace(value="authoritative_snapshot"),
                reconstruction_status="AUTHORITATIVE_SNAPSHOT",
                warning=None,
            )
            materialized = SimpleNamespace(
                findings_path=directory / "normalized" / "findings.jsonl.gz"
            )
            artifact = SimpleNamespace(
                dataset_path=directory / "dataset.json",
                directory=directory,
            )
            tag_bundle = SimpleNamespace(artifacts=(), warnings=())

            with (
                patch.object(cli_module, "load_client_profile", return_value=profile),
                patch.object(cli_module, "_compact_snapshot_repository", return_value=object()),
                patch.object(
                    cli_module,
                    "resolve_execution_collection_route",
                    return_value=(route, object()),
                ),
                patch.object(
                    cli_module,
                    "materialize_compact_snapshot_run",
                    return_value=materialized,
                ),
                patch.object(
                    cli_module,
                    "load_report_dataset_inputs",
                    return_value=SimpleNamespace(
                        tag_scope=None,
                        was_snapshot=None,
                        collection_provenance={"sources": ["compact_finding_snapshot"]},
                    ),
                ),
                patch.object(
                    cli_module,
                    "build_report_dataset_from_snapshot",
                    return_value=artifact,
                ),
                patch.object(
                    cli_module,
                    "build_tag_report_datasets_from_snapshot",
                    return_value=tag_bundle,
                ),
                patch.object(cli_module, "_load_credentials") as load_credentials,
                patch.object(cli_module, "collect_asset_snapshot") as collect_assets,
                patch.object(cli_module, "collect_vm_snapshot_with_policy") as collect_vm,
            ):
                result = cli_module._execute_period(
                    args,
                    execution_type="MANUAL",
                    period=period,
                )

            load_credentials.assert_not_called()
            collect_assets.assert_not_called()
            collect_vm.assert_not_called()
            self.assertEqual(result.collection_route, "snapshot_replay")
            self.assertEqual(result.reconstruction_status, "AUTHORITATIVE_SNAPSHOT")


if __name__ == "__main__":
    unittest.main()
