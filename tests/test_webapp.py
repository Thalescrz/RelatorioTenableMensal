from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import unittest
import os
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.application.postgresql_migration import MainBackfillSourceState
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.report_reference import READY_STATUS, ReportCandidate, ReportOrigin, reference_key_for_candidate
from tenable_reports.webapp.server import (
    DashboardApplication,
    DashboardConfigStore,
    DashboardDatabase,
    DashboardHTTPServer,
    JobQueue,
    slugify_client_id,
    _safe_error,
)


class _RowsCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _RowsConnection:
    def __init__(self, rows):
        self.rows = rows
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _parameters):
        self.query = query
        return _RowsCursor(self.rows)


class _RowsDatabase:
    def __init__(self, rows):
        self.connection_value = _RowsConnection(rows)

    def connection(self):
        return self.connection_value


def valid_run(run_id: str, *, client_id: str = "cliente-a") -> ReportCandidate:
    return ReportCandidate(
        run_id=run_id, client_id=client_id, tenant_id=client_id,
        origin=ReportOrigin.MANUAL, execution_type="MANUAL",
        period_start_at="2026-07-01T03:00:00Z",
        period_end_at="2026-08-01T03:00:00Z",
        period_mode="PREVIOUS_CALENDAR_MONTH", timezone="America/Fortaleza",
        scope_hash="scope", metric_definition_version="report-definition-v1.2",
        publication_status=READY_STATUS, documents_valid=True,
    )


class LocalClient:
    def __init__(self, app: DashboardApplication) -> None:
        self.server = DashboardHTTPServer(("127.0.0.1", 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base + path, data=body, method=method,
            headers={"Content-Type": "application/json", "X-Tenable-UI": "1"},
        )
        try:
            response = urlopen(request, timeout=3)
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        return response.status, json.loads(response.read().decode("utf-8"))


class WebDashboardTests(unittest.TestCase):
    def test_backfill_routes_analyze_and_apply_only_safe_promotions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InMemoryReportRegistry()
            safe = valid_run("run-safe")
            ambiguous_a = replace(
                valid_run("run-ambiguous-a"),
                period_start_at="2026-08-01T03:00:00Z",
                period_end_at="2026-09-01T03:00:00Z",
            )
            ambiguous_b = replace(ambiguous_a, run_id="run-ambiguous-b")
            for candidate in (safe, ambiguous_a, ambiguous_b):
                registry.register_report(candidate)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
                backfill_state_provider=lambda: MainBackfillSourceState(
                    used_history_run_ids=frozenset(),
                    existing_main_run_ids=frozenset(),
                ),
            )
            client = LocalClient(app)
            try:
                status, plan = client.request("GET", "/api/admin/backfill")
                self.assertEqual(status, 200)
                self.assertEqual(
                    [item["run_id"] for item in plan["promotions"]], ["run-safe"]
                )
                self.assertEqual(plan["alerts"][0]["code"], "MAIN_SELECTION_REQUIRED")

                status, _ = client.request(
                    "POST", "/api/admin/backfill/apply", {"confirmation": "errada"}
                )
                self.assertEqual(status, 400)
                self.assertIsNone(registry.get_main(reference_key_for_candidate(safe)))

                status, applied = client.request(
                    "POST", "/api/admin/backfill/apply",
                    {"confirmation": "APLICAR BACKFILL"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(applied["applied_promotions"], ["run-safe"])
                self.assertEqual(
                    registry.get_main(reference_key_for_candidate(safe)).run_id,
                    "run-safe",
                )
                self.assertIsNone(
                    registry.get_main(reference_key_for_candidate(ambiguous_a))
                )
            finally:
                client.close()

    def test_backfill_apply_never_replaces_an_existing_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InMemoryReportRegistry()
            for candidate in (valid_run("run-main"), valid_run("run-alternate")):
                registry.register_report(candidate)
            registry.promote_main(
                reference_key_for_candidate(valid_run("run-main")), "run-main",
                actor="analista", reason="seleção validada",
            )
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
                backfill_state_provider=lambda: MainBackfillSourceState(
                    used_history_run_ids=frozenset(),
                    existing_main_run_ids=frozenset({"run-main"}),
                ),
            )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST", "/api/admin/backfill/apply",
                    {"confirmation": "APLICAR BACKFILL"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["applied_promotions"], [])
                self.assertEqual(
                    registry.get_main(reference_key_for_candidate(valid_run("x"))).run_id,
                    "run-main",
                )
            finally:
                client.close()

    def test_safe_error_removes_secrets_paths_and_traceback_source_lines(self) -> None:
        raw = (
            'Traceback (most recent call last):\n'
            '  File "C:\\Codex\\projeto\\modulo.py", line 137, in executar\n'
            '    TENABLE_ACCESS=valor-secreto\n'
            'MemoryError: memória insuficiente'
        )
        safe = _safe_error(raw)
        self.assertNotIn("C:\\Codex", safe)
        self.assertNotIn("valor-secreto", safe)
        self.assertNotIn("line 137", safe)
        self.assertIn("MemoryError", safe)

    def test_safe_error_collapses_single_line_traceback_from_database(self) -> None:
        raw = (
            'file=profile, ... allowed_asset_ids=None,) File '
            '"C:\\Codex\\Relatorio\\normalize.py", line 137, in normalize '
            'findings_content = jsonl(findings) MemoryError'
        )
        safe = _safe_error(raw)
        self.assertEqual(safe, "MemoryError")

    def test_report_endpoints_manage_main_delete_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry = InMemoryReportRegistry()
            for run_id in ("run-a", "run-b"):
                registry.register_report(valid_run(run_id))
            registry.promote_main(
                reference_key_for_candidate(valid_run("run-a")), "run-a",
                actor="system", reason="primeiro",
            )
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
            )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "GET", "/api/clients/cliente-a/reports?include_deleted=true"
                )
                self.assertEqual(status, 200)
                row = payload["reports"][0]
                self.assertTrue({
                    "run_id", "origin", "is_main", "deleted_at",
                    "reference_run_id", "size_bytes", "documents",
                } <= row.keys())

                status, _ = client.request(
                    "POST", "/api/reports/run-b/main",
                    {"actor": "analista", "reason": ""},
                )
                self.assertEqual(status, 400)

                status, _ = client.request(
                    "DELETE", "/api/reports/run-a",
                    {"actor": "analista", "reason": "incompleto"},
                )
                self.assertEqual(status, 409)

                client.request(
                    "DELETE", "/api/reports/run-a",
                    {"actor": "analista", "reason": "incompleto", "allow_gap": True},
                )
                status, _ = client.request(
                    "POST", "/api/reports/run-a/restore",
                    {"actor": "analista", "reason": "recuperação"},
                )
                self.assertEqual(status, 200)
                self.assertIsNone(registry.get_main(reference_key_for_candidate(valid_run("run-a"))))
            finally:
                client.close()

    def test_dashboard_database_reports_exposes_tag_document_metadata(self) -> None:
        ended_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        created_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
        database = DashboardDatabase.__new__(DashboardDatabase)
        database.database = _RowsDatabase([(
            7, "C:/reports/tag.docx", 1234, "VALID", "run-a", "2026-07",
            "MANUAL", ended_at, created_at, "tag", "tag-a", "Equipe", "Infra",
        )])

        report = database.reports("cliente-a")[0]

        self.assertEqual(report["document_kind"], "tag")
        self.assertEqual(report["tag_uuid"], "tag-a")
        self.assertEqual(report["tag_category"], "Equipe")
        self.assertEqual(report["tag_value"], "Infra")
        self.assertIn("d.document_kind", database.database.connection_value.query)

    def test_storage_endpoint_reports_free_space_and_queue_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=InMemoryReportRegistry(),
            )
            client = LocalClient(app)
            try:
                status, payload = client.request("GET", "/api/storage")
                self.assertEqual(status, 200)
                self.assertGreater(payload["available_bytes"], 0)
                self.assertIn("queue_reserved_bytes", payload)
                self.assertIn("by_client", payload)
            finally:
                client.close()

    def test_storage_cleanup_preview_does_not_remove_and_apply_removes_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=InMemoryReportRegistry(),
                retention_state_provider=lambda: {
                    "run_status": {"run-failed": "FAILED"},
                    "history_confirmed_run_ids": (),
                    "main_run_ids": (),
                    "retry_required_run_ids": (),
                    "cleanup_runs": (),
                    "pending_cleanup_runs": 0,
                    "last_cleanup_at": None,
                    "last_cleanup_status": "NEVER_RUN",
                },
            )
            residue = (
                root / "data" / "manual" / "raw" / "cliente-a" / "run-failed"
            )
            residue.mkdir(parents=True)
            (residue / "chunk.jsonl.gz").write_bytes(b"compressed-fixture")
            old = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
            os.utime(residue, (old, old))
            client = LocalClient(app)
            try:
                status, preview = client.request(
                    "POST", "/api/storage/cleanup/preview", {}
                )
                self.assertEqual(status, 200)
                self.assertFalse(preview["applied"])
                self.assertEqual(preview["candidate_count"], 1)
                self.assertEqual(preview["removed_bytes"], 0)
                self.assertTrue(residue.is_dir())

                status, applied = client.request(
                    "POST", "/api/storage/cleanup/apply", {}
                )
                self.assertEqual(status, 200)
                self.assertTrue(applied["applied"])
                self.assertEqual(applied["candidate_count"], 1)
                self.assertGreater(applied["removed_bytes"], 0)
                self.assertFalse(residue.exists())
            finally:
                client.close()

    def test_storage_cleanup_protects_active_run_and_reconciles_completed_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recorded: list[tuple[str, str, int]] = []
            state = {
                "run_status": {"run-active": "FAILED", "run-pending": "COMPLETE"},
                "history_confirmed_run_ids": ("run-pending",),
                "main_run_ids": (),
                "retry_required_run_ids": (),
                "cleanup_runs": ({
                    "run_id": "run-pending",
                    "client_id": "cliente-a",
                    "status": "PENDING",
                },),
                "pending_cleanup_runs": 1,
                "last_cleanup_at": None,
                "last_cleanup_status": "PENDING",
            }
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=InMemoryReportRegistry(),
                retention_state_provider=lambda: state,
                cleanup_status_recorder=lambda run_id, status, cleanup_bytes=0: (
                    recorded.append((run_id, status, cleanup_bytes))
                ),
            )
            app.jobs.snapshot = lambda: [{
                "status": "RUNNING", "run_id": "run-active", "client_id": "cliente-a"
            }]
            for run_id in ("run-active", "run-pending"):
                path = root / "data" / "manual" / "raw" / "cliente-a" / run_id
                path.mkdir(parents=True)
                (path / "chunk.gz").write_bytes(b"payload")
                old = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp()
                os.utime(path, (old, old))

            preview = app.cleanup_safe_residues(apply=False)
            self.assertEqual(
                [item["run_id"] for item in preview["candidates"]], ["run-pending"]
            )
            applied = app.cleanup_safe_residues(apply=True)

            self.assertTrue(
                (root / "data" / "manual" / "raw" / "cliente-a" / "run-active").is_dir()
            )
            self.assertFalse(
                (root / "data" / "manual" / "raw" / "cliente-a" / "run-pending").exists()
            )
            self.assertEqual(recorded[-1][0:2], ("run-pending", "COMPLETE"))
            self.assertGreater(recorded[-1][2], 0)

    def test_show_source_filters_is_created_and_edited_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            created = store.add_client({
                "client_id": "cliente-filtros", "display_name": "Cliente Filtros",
                "show_source_filters": True,
            })
            self.assertTrue(created["show_source_filters"])
            updated = store.update_client(
                "cliente-filtros", {"show_source_filters": False}
            )
            self.assertFalse(updated["show_source_filters"])
            profile = json.loads(
                (root / "clients" / "managed" / "cliente-filtros.json").read_text(encoding="utf-8")
            )
            self.assertFalse(profile["presentation"]["show_source_filters"])

    def test_failed_job_can_be_requeued_with_same_period(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def runner(command, cwd):
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="falha")
            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            original = jobs.enqueue(["cliente-a"], {"mode": "manual", "days": 15})[0]
            jobs._pending.join()
            retried = jobs.retry(original["job_id"])
            self.assertEqual(retried["mode"], "manual")
            self.assertEqual(retried["days"], 15)
            self.assertEqual(retried["retry_of_job_id"], original["job_id"])
    def test_connection_check_supports_multiple_clients(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                connection_checker=lambda path: {
                    "ok": path.name.startswith("cliente-a"),
                    "latency_ms": 12,
                    "message": "resultado seguro",
                    "checked_at": "2026-08-14T12:00:00+00:00",
                },
            )
            for client_id in ("cliente-a", "cliente-b"):
                app.config.add_client({
                    "client_id": client_id,
                    "display_name": client_id,
                    "tenant_id": client_id,
                    "access_key": "access",
                    "secret_key": "secret",
                })
            results = app.check_connections(["cliente-a", "cliente-b"])
            self.assertEqual([item["client_id"] for item in results], ["cliente-a", "cliente-b"])
            self.assertTrue(results[0]["ok"])
            self.assertFalse(results[1]["ok"])

    def test_client_id_is_generated_from_display_name(self) -> None:
        self.assertEqual(slugify_client_id("DETRAN Ceará"), "detran-ceara")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            client = store.add_client({
                "display_name": "Cliente São Luís",
                "access_key": "",
                "secret_key": "",
            })
            self.assertEqual(client["client_id"], "cliente-sao-luis")
            self.assertEqual(client["tenant_id"], "cliente-sao-luis")

    def test_client_creation_keeps_secrets_out_of_api_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            client = store.add_client({
                "client_id": "cliente-teste",
                "display_name": "Cliente Teste",
                "tenant_id": "tenant-teste",
                "access_key": "access-value",
                "secret_key": "secret-value",
                "tags": "Rede: Matriz, Rede: Filial",
                "intelligence_enabled": True,
            })
            self.assertTrue(client["credentials_ready"])
            self.assertNotIn("access_key", client)
            self.assertNotIn("secret_key", client)
            profile = json.loads(
                (root / "clients" / "managed" / "cliente-teste.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(profile["display_name"], "Cliente Teste")
            self.assertEqual(
                profile["report"]["network_comparison_tags"],
                ["Rede: Matriz", "Rede: Filial"],
            )
            raw_config = json.loads(store.config_path.read_text(encoding="utf-8"))
            self.assertNotIn("access-value", json.dumps(raw_config))
            self.assertNotIn("secret-value", json.dumps(raw_config))
            loaded = load_client_profile(
                root / "clients" / "managed" / "cliente-teste.json"
            )
            self.assertEqual(loaded.client_id, "cliente-teste")

    def test_client_can_be_edited_without_replacing_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            store.add_client({
                "client_id": "cliente-editavel",
                "display_name": "Cliente Antigo",
                "tenant_id": "tenant-antigo",
                "access_key": "access-original",
                "secret_key": "secret-original",
            })
            updated = store.update_client("cliente-editavel", {
                "display_name": "Cliente Atualizado",
                "tenant_id": "tenant-atualizado",
                "tags": ["Rede: Matriz"],
                "intelligence_enabled": True,
                "was_enabled": True,
                "cloud_enabled": False,
                "include_output": True,
                "access_key": "",
                "secret_key": "",
            })
            self.assertEqual(updated["display_name"], "Cliente Atualizado")
            self.assertEqual(updated["tenant_id"], "tenant-atualizado")
            self.assertTrue(updated["was_enabled"])
            self.assertFalse(updated["cloud_enabled"])
            self.assertTrue(updated["include_output"])
            env_text = (root / "credentials" / "cliente-editavel.env").read_text(
                encoding="utf-8"
            )
            self.assertIn("access-original", env_text)
            profile = json.loads(
                (root / "clients" / "managed" / "cliente-editavel.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("was_unsupported_tech", profile["report"]["intelligence_modules"])
            self.assertNotIn("cloud_container_images", profile["report"]["intelligence_modules"])
            load_client_profile(root / "clients" / "managed" / "cliente-editavel.json")

    def test_queue_runs_one_client_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []

            def runner(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout='{"status":"complete","run_id":"run-web-1"}\n',
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            created = jobs.enqueue(["cliente-teste"], {"mode": "manual", "days": 15})
            self.assertEqual(len(created), 1)
            jobs._pending.join()
            snapshot = jobs.snapshot()
            self.assertEqual(snapshot[0]["status"], "COMPLETE")
            self.assertEqual(snapshot[0]["run_id"], "run-web-1")
            self.assertIn("cliente-teste", observed[0])
            self.assertIn("15", observed[0])


if __name__ == "__main__":
    unittest.main()
