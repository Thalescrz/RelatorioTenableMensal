from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

from tenable_reports.application.orchestration import (
    OrchestrationRequest,
    build_client_command,
    load_orchestration_config,
)
from tenable_reports.webapp.server import (
    DashboardApplication,
    DashboardConfigStore,
    JobQueue,
)


ROOT = Path(__file__).resolve().parents[1]

class _InputCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "input":
            self.inputs.append(dict(attrs))


class HistoricalWebUiTests(unittest.TestCase):
    def test_run_dialog_uses_inclusive_calendar_dates(self) -> None:
        parser = _InputCollector()
        parser.feed(
            (ROOT / "src/tenable_reports/webapp/static/index.html").read_text(
                encoding="utf-8"
            )
        )

        by_name = {
            item.get("name"): item
            for item in parser.inputs
            if item.get("name") in {"start_date", "end_date"}
        }
        self.assertEqual(set(by_name), {"start_date", "end_date"})
        self.assertEqual(by_name["start_date"].get("type"), "date")
        self.assertEqual(by_name["end_date"].get("type"), "date")

    def test_inclusive_calendar_range_becomes_exclusive_next_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []

            def runner(command, cwd, progress_callback=None):
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"status": "COMPLETE", "run_id": "run-ok"}) + "\n",
                    stderr="",
                )

            jobs = JobQueue(
                root, root / "orchestration" / "clients.json", runner
            )
            job = jobs.enqueue(["cliente-a"], {
                "mode": "manual",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            })[0]
            jobs._pending.join()

        self.assertEqual(job["start_at"], "2026-07-01")
        self.assertEqual(job["end_at"], "2026-08-01")
        start_index = observed[0].index("--start-at")
        end_index = observed[0].index("--end-at")
        self.assertEqual(observed[0][start_index + 1], "2026-07-01")
        self.assertEqual(observed[0][end_index + 1], "2026-08-01")

    def test_recovery_uuid_is_forwarded_by_web_queue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []
            export_uuid = "00000000-0000-0000-0000-000000000321"

            def runner(command, cwd, progress_callback=None):
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command, 0, stdout="{}", stderr=""
                )

            jobs = JobQueue(
                root, root / "orchestration" / "clients.json", runner
            )
            jobs.enqueue(["cliente-a"], {
                "mode": "manual",
                "start_at": "2026-07-01T03:00:00Z",
                "end_at": "2026-08-01T03:00:00Z",
                "vm_export_uuid": export_uuid,
            })
            jobs._pending.join()

        index = observed[0].index("--vm-export-uuid")
        self.assertEqual(observed[0][index + 1], export_uuid)

    def test_inclusive_calendar_range_rejects_end_before_start(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"status": "COMPLETE"}) + "\n",
                    stderr="",
                )

            jobs = JobQueue(
                root, root / "orchestration" / "clients.json", runner
            )
            with self.assertRaisesRegex(ValueError, "Data final"):
                jobs.enqueue(["cliente-a"], {
                    "mode": "manual",
                    "start_date": "2026-08-01",
                    "end_date": "2026-07-31",
                })

    def test_run_dialog_offers_opt_in_live_collection(self) -> None:
        parser = _InputCollector()
        parser.feed(
            (ROOT / "src/tenable_reports/webapp/static/index.html").read_text(
                encoding="utf-8"
            )
        )

        live_inputs = [
            item for item in parser.inputs
            if item.get("name") == "force_live_collection"
        ]
        self.assertEqual(len(live_inputs), 1)
        self.assertEqual(live_inputs[0].get("type"), "checkbox")
        self.assertNotIn("checked", live_inputs[0])

    def test_historical_source_round_trip_and_invalid_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            created = store.add_client({
                "client_id": "cliente-historico",
                "display_name": "Cliente Histórico",
                "historical_source": "inventory_beta",
            })
            self.assertEqual(created["historical_source"], "inventory_beta")

            updated = store.update_client(
                "cliente-historico", {"historical_source": "legacy"}
            )
            self.assertEqual(updated["historical_source"], "legacy")

            with self.assertRaisesRegex(ValueError, "historical_source"):
                store.update_client(
                    "cliente-historico", {"historical_source": "workbench"}
                )

    def test_job_retains_vm_diagnostics_and_collection_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress_callback({
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "source": "tenable_vm_vulnerabilities",
                    "export_uuid": "job-stuck",
                    "origin": "created",
                    "status": "FINISHED",
                    "completed_chunks": 2,
                    "total_chunks": 2,
                    "idle_seconds": 4,
                    "last_progress_elapsed_seconds": 50,
                    "no_progress_timeout_seconds": 900,
                    "timeout_phase": None,
                    "filters": {"state": ["OPEN", "REOPENED"]},
                })
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "COMPLETE",
                        "run_id": "run-reconstructed",
                        "clients": [{"payload": {
                            "collection_route": "inventory_beta",
                            "reconstruction_status": "HISTORICAL_RECONSTRUCTION",
                            "collection_sources": ["tenable_inventory_findings"],
                        }}],
                    }) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-a"], {
                "mode": "manual",
                "days": 30,
                "historical_source": "inventory-beta",
            })
            jobs._pending.join()
            job = jobs.snapshot()[0]

        self.assertEqual(job["export_progress"]["no_progress_timeout_seconds"], 900)
        self.assertEqual(
            job["export_progress"]["filters"]["state"], ["OPEN", "REOPENED"]
        )
        self.assertEqual(job["collection_route"], "inventory_beta")
        self.assertEqual(job["reconstruction_status"], "HISTORICAL_RECONSTRUCTION")
        self.assertEqual(job["collection_sources"], ["tenable_inventory_findings"])

    def test_explicit_recovery_switches_combined_export_to_split(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []
            calls = 0

            def runner(command, cwd, progress_callback=None):
                nonlocal calls
                calls += 1
                observed.append(list(command))
                if calls == 1:
                    progress_callback({
                        "event": "TENABLE_EXPORT_PROGRESS",
                        "source": "tenable_vm_vulnerabilities",
                        "export_uuid": "job-stuck",
                        "origin": "reused",
                        "status": "TIMED_OUT",
                        "completed_chunks": 0,
                        "total_chunks": 1,
                        "timeout_phase": "no_progress",
                        "auto_cancelled": False,
                    })
                    return subprocess.CompletedProcess(
                        command, 2,
                        stdout=json.dumps({
                            "status": "failed",
                            "error_code": "TENABLE_TEMPORARY",
                            "retryable": True,
                            "message": "Export VM travado.",
                        }) + "\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    command, 0,
                    stdout=json.dumps({"status": "COMPLETE", "run_id": "retry-ok"}) + "\n",
                    stderr="",
                )

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
                export_canceller=lambda _path, _uuid: {"status": "CANCELLED"},
            )
            app.config.add_client({
                "client_id": "cliente-a",
                "display_name": "Cliente A",
                "access_key": "access",
                "secret_key": "secret",
            })
            original = app.jobs.enqueue(
                ["cliente-a"], {"mode": "manual", "days": 30}
            )[0]
            app.jobs._pending.join()
            result = app.cancel_export_and_retry(
                job_id=original["job_id"],
                export_uuid="job-stuck",
                confirmation="CANCELAR job-stuck",
            )
            app.jobs._pending.join()

        self.assertEqual(result["job"]["vm_export_strategy"], "split")
        strategy_index = observed[1].index("--vm-export-strategy")
        self.assertEqual(observed[1][strategy_index + 1], "split")

    def test_exact_inventory_period_requires_backend_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                return subprocess.CompletedProcess(
                    command, 0,
                    stdout=json.dumps({"status": "COMPLETE", "run_id": "run-ok"}) + "\n",
                    stderr="",
                )

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
            )
            app.config.add_client({
                "client_id": "cliente-historico",
                "display_name": "Cliente Histórico",
                "historical_source": "inventory_beta",
            })
            request = {
                "mode": "manual",
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
            }
            with self.assertRaisesRegex(ValueError, "reconstrucao"):
                app.enqueue_jobs(["cliente-historico"], request)

            accepted = app.enqueue_jobs(
                ["cliente-historico"],
                {**request, "confirm_historical_reconstruction": True},
            )
            app.jobs._pending.join()

        self.assertEqual(accepted[0]["historical_source"], "inventory-beta")
        self.assertTrue(accepted[0]["confirm_historical_reconstruction"])

    def test_orchestration_forwards_vm_export_strategy_override(self) -> None:
        config = load_orchestration_config(
            ROOT / "orchestration/clients.example.json"
        )
        command = build_client_command(
            config=config,
            client=config.clients[0],
            request=OrchestrationRequest(
                mode="manual",
                vm_export_strategy="split",
            ),
            client_run_id="run-recovery",
        )
        index = command.index("--vm-export-strategy")
        self.assertEqual(command[index + 1], "split")

    def test_frontend_exposes_historical_source_warning_and_provenance(self) -> None:
        static_root = ROOT / "src/tenable_reports/webapp/static"
        html = (static_root / "index.html").read_text(encoding="utf-8")
        javascript = (static_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('name="historical_source"', html)
        self.assertIn("confirm_historical_reconstruction", javascript)
        self.assertIn("HISTÓRICO RECONSTRUÍDO", javascript)
        self.assertIn('rel="icon" href="data:,"', html)
        server = (ROOT / "src/tenable_reports/webapp/server.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("style-src 'self';", server)
        self.assertNotIn(' style="', javascript)
        self.assertIn("<progress", javascript)
        self.assertIn("no_progress_timeout_seconds", javascript)


if __name__ == "__main__":
    unittest.main()
