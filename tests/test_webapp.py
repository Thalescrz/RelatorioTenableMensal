from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import os
import zipfile
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.application.web_batches_memory import (
    InMemoryWebBatchRepository,
)
from tenable_reports.application.report_archives import ReportArchiveResult
from tenable_reports.application.report_set_purge import (
    ReportSetPurgeRecord,
    ReportSetPurgeService,
)
from tenable_reports.application.postgresql_migration import MainBackfillSourceState
from tenable_reports.application.was_recovery import (
    WasFailureDetails,
    WasRecoveryCheckpoint,
    WasRecoveryRecord,
    WasRecoveryStatus,
    write_was_recovery_checkpoint,
)
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.report_reference import READY_STATUS, ReportCandidate, ReportOrigin, reference_key_for_candidate
from tenable_reports.webapp.server import (
    DashboardApplication,
    DashboardConfigStore,
    DashboardDatabase,
    DashboardHandler,
    DashboardHTTPServer,
    JobQueue,
    slugify_client_id,
    _safe_error,
    _default_runner as _web_default_runner,
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
        self.parameters = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _parameters):
        self.query = query
        self.parameters = _parameters
        return _RowsCursor(self.rows)


class _RowsDatabase:
    def __init__(self, rows):
        self.connection_value = _RowsConnection(rows)

    def connection(self):
        return self.connection_value



class _WebPurgeRepository:
    def __init__(
        self,
        registry: InMemoryReportRegistry,
        records: dict[str, ReportSetPurgeRecord],
    ) -> None:
        self.registry = registry
        self.records = records

    def describe(self, run_id: str) -> ReportSetPurgeRecord:
        return self.records[run_id]

    def purge(
        self,
        run_id: str,
        *,
        actor: str,
        reason: str,
        replacement_run_id: str | None,
        allow_main_gap: bool = False,
    ) -> None:
        self.registry.hard_delete(
            run_id,
            actor=actor,
            reason=reason,
            replacement_run_id=replacement_run_id,
            allow_gap=allow_main_gap,
        )


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

    def download(self, path: str):
        try:
            response = urlopen(self.base + path, timeout=3)
        except HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()
        return response.status, dict(response.headers), response.read()


class _ArchiveDashboardDatabase:
    def __init__(
        self,
        documents_by_client: dict[str, list[dict]],
        documents_by_run: dict[str, list[dict]] | None = None,
    ) -> None:
        self.documents_by_client = documents_by_client
        self.documents_by_run = documents_by_run or {}

    def reports(self, client_id: str):
        return list(self.documents_by_client.get(client_id, ()))

    def cloud_results(self, client_id: str):
        return {}

    def report_documents(self, run_id: str):
        return list(self.documents_by_run.get(run_id, ()))


class WebDashboardTests(unittest.TestCase):
    def test_generate_all_rejects_explicit_empty_client_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = InMemoryWebBatchRepository()

            def runner(command, cwd, progress_callback=None):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps(
                        {"status": "COMPLETE", "run_id": "run-fixture"}
                    ),
                    stderr="",
                )

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
                batch_repository=repository,
            )
            for client_id in ("cliente-a", "cliente-b"):
                app.config.add_client(
                    {
                        "client_id": client_id,
                        "display_name": client_id,
                    }
                )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST",
                    "/api/jobs",
                    {
                        "client_ids": [],
                        "run_scope": "all",
                        "mode": "manual",
                    },
                )
            finally:
                client.close()
                app.jobs.close()

            self.assertEqual(status, 400)
            self.assertIn("EMPTY_CLIENT_SELECTION", payload["error"])
            self.assertEqual(repository.list_batches(), ())

    def test_analyst_crud_and_client_assignment_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST", "/api/analysts", {"display_name": "Analista Um"}
                )
                self.assertEqual(status, 201)
                analyst_id = payload["analyst"]["analyst_id"]

                status, payload = client.request("GET", "/api/state")
                self.assertEqual(status, 200)
                self.assertEqual(payload["analysts"][0]["display_name"], "Analista Um")

                status, payload = client.request(
                    "POST",
                    "/api/clients",
                    {
                        "client_id": "cliente-analista",
                        "display_name": "Cliente Analista",
                        "responsible_analyst_id": analyst_id,
                    },
                )
                self.assertEqual(status, 201)
                self.assertEqual(payload["client"]["responsible_analyst_id"], analyst_id)
                self.assertEqual(
                    payload["client"]["responsible_analyst_name"], "Analista Um"
                )

                status, payload = client.request(
                    "PATCH",
                    f"/api/analysts/{analyst_id}",
                    {"display_name": "Analista Principal", "active": False},
                )
                self.assertEqual(status, 200)
                self.assertFalse(payload["analyst"]["active"])

                status, payload = client.request(
                    "PATCH",
                    "/api/clients/cliente-analista",
                    {"display_name": "Cliente Atualizado"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["client"]["responsible_analyst_id"], analyst_id)
                self.assertFalse(payload["client"]["responsible_analyst_active"])

                status, payload = client.request(
                    "PATCH",
                    "/api/clients/cliente-analista",
                    {"responsible_analyst_id": "desconhecido"},
                )
                self.assertEqual(status, 400)
                self.assertIn("Analista", payload["error"])

                status, payload = client.request(
                    "DELETE", f"/api/analysts/{analyst_id}", {"confirmation": "EXCLUIR"}
                )
                self.assertEqual(status, 409)
                self.assertIn("uso", payload["error"])

                status, payload = client.request(
                    "PATCH",
                    "/api/clients/cliente-analista",
                    {"responsible_analyst_id": None},
                )
                self.assertEqual(status, 200)
                self.assertIsNone(payload["client"]["responsible_analyst_id"])

                status, payload = client.request(
                    "DELETE", f"/api/analysts/{analyst_id}", {"confirmation": "EXCLUIR"}
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["deleted_analyst_id"], analyst_id)
            finally:
                client.close()

    def test_analyst_patch_rejects_wrong_json_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST", "/api/analysts", {"display_name": "Analista Um"}
                )
                self.assertEqual(status, 201)
                analyst_id = payload["analyst"]["analyst_id"]

                status, _ = client.request(
                    "PATCH",
                    f"/api/analysts/{analyst_id}",
                    {"active": "false"},
                )
                self.assertEqual(status, 400)

                status, _ = client.request(
                    "PATCH",
                    f"/api/analysts/{analyst_id}",
                    {"display_name": None},
                )
                self.assertEqual(status, 400)
            finally:
                client.close()

    def test_inactive_analyst_cannot_be_assigned_to_another_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST", "/api/analysts", {"display_name": "Analista Um"}
                )
                self.assertEqual(status, 201)
                analyst_id = payload["analyst"]["analyst_id"]
                status, _ = client.request(
                    "PATCH",
                    f"/api/analysts/{analyst_id}",
                    {"active": False},
                )
                self.assertEqual(status, 200)

                status, payload = client.request(
                    "POST",
                    "/api/clients",
                    {
                        "client_id": "cliente-novo",
                        "display_name": "Cliente Novo",
                        "responsible_analyst_id": analyst_id,
                    },
                )
                self.assertEqual(status, 400)
                self.assertIn("inativo", payload["error"])
            finally:
                client.close()

    def test_analyst_delete_fails_closed_when_client_profile_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST", "/api/analysts", {"display_name": "Analista Um"}
                )
                self.assertEqual(status, 201)
                analyst_id = payload["analyst"]["analyst_id"]
                status, _ = client.request(
                    "POST",
                    "/api/clients",
                    {
                        "client_id": "cliente-analista",
                        "display_name": "Cliente Analista",
                        "responsible_analyst_id": analyst_id,
                    },
                )
                self.assertEqual(status, 201)
                app.config.client_profile_path("cliente-analista").write_text(
                    "{invalid",
                    encoding="utf-8",
                )

                status, payload = client.request(
                    "DELETE",
                    f"/api/analysts/{analyst_id}",
                    {"confirmation": "EXCLUIR"},
                )
                self.assertEqual(status, 409)
                self.assertIn("verificar", payload["error"])
            finally:
                client.close()

    def test_invalid_analyst_catalog_returns_sanitized_json_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            (root / "orchestration" / "analysts.json").write_text(
                "{invalid",
                encoding="utf-8",
            )
            client = LocalClient(app)
            try:
                status, payload = client.request("GET", "/api/analysts")
            finally:
                client.close()

            self.assertEqual(status, 500)
            self.assertIn("error", payload)
            self.assertNotIn("{invalid", json.dumps(payload))

    def test_orphaned_analyst_assignment_is_preserved_as_inactive_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            store.add_client({
                "client_id": "cliente-orfao",
                "display_name": "Cliente Órfão",
            })
            profile_path = store.client_profile_path("cliente-orfao")
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            profile["responsible_analyst_id"] = "analista-ausente"
            profile_path.write_text(
                json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            client = store.list_clients()[0]
            self.assertEqual(
                client["responsible_analyst_id"],
                "analista-ausente",
            )
            self.assertIsNone(client["responsible_analyst_name"])
            self.assertFalse(client["responsible_analyst_active"])

    def test_default_runner_forces_utf8_for_json_protocol(self) -> None:
        script = (
            "import json; "
            "print(json.dumps({'text':'\\ufffd'}, ensure_ascii=False))"
        )

        with patch.dict(
            os.environ,
            {"PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"},
        ):
            completed = _web_default_runner(
                (sys.executable, "-c", script),
                Path(__file__).resolve().parents[1],
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["text"], "�")

    def test_tag_report_configuration_persists_across_store_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "orchestration" / "clients.json"
            store = DashboardConfigStore(project_root=root, config_path=config_path)
            store.add_client({
                "client_id": "cliente-tags",
                "display_name": "Cliente Tags",
                "tag_reports_enabled": True,
                "tag_reports": [
                    {
                        "tag_uuid": "tag-a", "category_uuid": "cat-a",
                        "category_name": "Equipe", "value": "Infra",
                        "generate_report": True,
                        "include_temporal_comparison": True,
                    },
                    {
                        "tag_uuid": "tag-b", "category_uuid": "cat-b",
                        "category_name": "Local", "value": "Filial",
                        "generate_report": True,
                        "include_temporal_comparison": False,
                    },
                ],
            })

            reloaded = DashboardConfigStore(project_root=root, config_path=config_path)
            client = reloaded.list_clients()[0]
            self.assertTrue(client["tag_reports_enabled"])
            self.assertEqual(
                [item["tag_uuid"] for item in client["tag_reports"]],
                ["tag-a", "tag-b"],
            )
            self.assertTrue(client["tag_reports"][0]["include_temporal_comparison"])

    def test_get_client_tags_combines_saved_unavailable_tag_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                tag_lister=lambda _path: [{
                    "tag_uuid": "tag-a", "category_uuid": "cat-a",
                    "category_name": "Equipe", "value": "Infra",
                }],
            )
            app.config.add_client({
                "client_id": "cliente-a", "display_name": "Cliente A",
                "access_key": "access-value", "secret_key": "secret-value",
                "tag_reports_enabled": True,
                "tag_reports": [
                    {
                        "tag_uuid": "tag-a", "category_uuid": "cat-a",
                        "category_name": "Equipe", "value": "Infra",
                        "generate_report": True,
                        "include_temporal_comparison": True,
                    },
                    {
                        "tag_uuid": "tag-old", "category_uuid": "cat-old",
                        "category_name": "Legado", "value": "Ausente",
                        "generate_report": True,
                        "include_temporal_comparison": False,
                    },
                ],
            })
            client = LocalClient(app)
            try:
                status, payload = client.request("GET", "/api/clients/cliente-a/tags")
            finally:
                client.close()

            self.assertEqual(status, 200)
            self.assertTrue(payload["tag_reports_enabled"])
            self.assertEqual(payload["tags"][0]["tag_uuid"], "tag-a")
            self.assertTrue(payload["tags"][0]["available"])
            unavailable = next(item for item in payload["tags"] if item["tag_uuid"] == "tag-old")
            self.assertFalse(unavailable["available"])
            serialized = json.dumps(payload).lower()
            self.assertNotIn("access-value", serialized)
            self.assertNotIn("secret-value", serialized)

    def test_get_client_tags_maps_tenable_auth_and_rate_limit_errors(self) -> None:
        class StatusError(RuntimeError):
            def __init__(self, status_code):
                super().__init__("X-ApiKeys accessKey=valor-secreto; secretKey=outro")
                self.status_code = status_code

        for api_status in (401, 403, 429):
            with self.subTest(api_status=api_status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                app = DashboardApplication(
                    project_root=root,
                    config_path=root / "orchestration" / "clients.json",
                    tag_lister=lambda _path, status=api_status: (_ for _ in ()).throw(StatusError(status)),
                )
                app.config.add_client({
                    "client_id": "cliente-a", "display_name": "Cliente A",
                    "access_key": "access", "secret_key": "secret",
                })
                client = LocalClient(app)
                try:
                    status, payload = client.request("GET", "/api/clients/cliente-a/tags")
                finally:
                    client.close()
                self.assertEqual(status, api_status)
                self.assertNotIn("valor-secreto", json.dumps(payload))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                tag_lister=lambda _path: [],
            )
            client = LocalClient(app)
            try:
                status, _ = client.request("GET", "/api/clients/inexistente/tags")
            finally:
                client.close()
            self.assertEqual(status, 404)

    def test_job_queue_exposes_incremental_tag_progress_and_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress_callback({
                    "event": "TAG_REPORT_PROGRESS", "current": 1, "total": 2,
                    "tag_uuid": "tag-a", "tag_label": "Equipe: Infra",
                })
                return subprocess.CompletedProcess(
                    command, 0,
                    stdout=json.dumps({
                        "status": "COMPLETE", "run_id": "run-web-tags",
                        "clients": [{"payload": {
                            "status": "complete_with_warnings",
                            "warnings": [{"tag_uuid": "tag-b", "message": "falhou"}],
                        }}],
                    }) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-a"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            job = jobs.snapshot()[0]
            self.assertEqual(job["tag_progress"]["current"], 1)
            self.assertEqual(job["tag_progress"]["total"], 2)
            self.assertEqual(job["warnings"][0]["tag_uuid"], "tag-b")

    def test_job_queue_exposes_stuck_vm_export_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress_callback({
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "export_uuid": "job-stuck",
                    "origin": "reused",
                    "segment": "fixed",
                    "date_field": "last_fixed",
                    "status": "TIMED_OUT",
                    "completed_chunks": 0,
                    "total_chunks": 1,
                    "auto_cancelled": False,
                })
                return subprocess.CompletedProcess(
                    command,
                    2,
                    stdout=json.dumps({
                        "status": "failed",
                        "error_code": "TENABLE_TEMPORARY",
                        "retryable": True,
                        "message": "Tempo maximo excedido aguardando o export VM.",
                    }) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-a"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            export = jobs.snapshot()[0]["export_progress"]

        self.assertEqual(export["export_uuid"], "job-stuck")
        self.assertEqual(export["origin"], "reused")
        self.assertEqual(export["status"], "TIMED_OUT")
        self.assertEqual(export["segment"], "fixed")

    def test_job_queue_uses_clean_client_error_from_final_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress = {
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "source": "tenable_vm_vulnerabilities",
                    "export_uuid": "job-cancelled",
                    "origin": "created",
                    "status": "CANCELLED",
                    "completed_chunks": 1,
                    "total_chunks": 2,
                    "chunks_cancelled": [2],
                }
                progress_callback(progress)
                final = {
                    "status": "PARTIAL_FAILURE",
                    "run_id": "run-cancelled",
                    "clients": [{
                        "client_id": "cliente-a",
                        "status": "FAILED",
                        "error": "Export VM terminou com estado cancelled.",
                        "attempts": [{
                            "error_code": "TENABLE_TEMPORARY",
                            "retryable": True,
                            "error": "Export VM terminou com estado cancelled.",
                        }],
                    }],
                }
                return subprocess.CompletedProcess(
                    command,
                    2,
                    stdout=json.dumps(progress) + "\n" + json.dumps(final) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-a"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            job = jobs.snapshot()[0]

        self.assertEqual(job["status"], "FAILED")
        self.assertEqual(job["error"], "Export VM terminou com estado cancelled.")
        self.assertEqual(job["export_progress"]["status"], "CANCELLED")

    def test_job_queue_keeps_vm_and_was_export_progress_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress_callback({
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "source": "tenable_vm_vulnerabilities",
                    "export_uuid": "vm-job",
                    "origin": "created",
                    "status": "FINISHED",
                    "completed_chunks": 2,
                    "total_chunks": 2,
                })
                progress_callback({
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "source": "tenable_was_findings",
                    "export_uuid": "was-job",
                    "origin": "created",
                    "status": "PROCESSING",
                    "completed_chunks": 1,
                    "total_chunks": 2,
                })
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "COMPLETE",
                        "run_id": "run-vm-was",
                    }) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-a"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            job = jobs.snapshot()[0]

        self.assertEqual(job["export_progress"]["export_uuid"], "vm-job")
        self.assertEqual(
            job["was_export_progress"]["export_uuid"],
            "was-job",
        )
        self.assertEqual(job["was_export_progress"]["status"], "PROCESSING")
    def test_job_queue_exposes_running_vm_export_stall_warning(self) -> None:

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress_callback({
                    "event": "TENABLE_EXPORT_PROGRESS",
                    "export_uuid": "job-processing",
                    "origin": "created",
                    "status": "PROCESSING",
                    "completed_chunks": 3,
                    "total_chunks": 8,
                    "idle_seconds": 1900,
                    "stalled": True,
                })
                return subprocess.CompletedProcess(
                    command, 0,
                    stdout=json.dumps({"status": "COMPLETE", "run_id": "run-ok"}) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-a"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            export = jobs.snapshot()[0]["export_progress"]

        self.assertTrue(export["stalled"])
        self.assertEqual(export["idle_seconds"], 1900)

    def test_stuck_export_can_be_cancelled_and_retried_with_exact_uuid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = 0
            cancelled: list[tuple[str, str]] = []

            def runner(command, cwd, progress_callback=None):
                nonlocal calls
                calls += 1
                if calls == 1:
                    progress_callback({
                        "event": "TENABLE_EXPORT_PROGRESS",
                        "export_uuid": "job-stuck",
                        "origin": "reused",
                        "status": "TIMED_OUT",
                        "completed_chunks": 0,
                        "total_chunks": 1,
                        "auto_cancelled": False,
                    })
                    return subprocess.CompletedProcess(
                        command,
                        2,
                        stdout=json.dumps({
                            "status": "failed",
                            "error_code": "TENABLE_TEMPORARY",
                            "retryable": True,
                            "message": "Export VM travado.",
                        }) + "\n",
                        stderr="",
                    )
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({"status": "COMPLETE", "run_id": "retry-ok"}) + "\n",
                    stderr="",
                )

            def cancel_export(path: Path, export_uuid: str):
                cancelled.append((path.name, export_uuid))
                return {"status": "CANCELLED"}

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
                export_canceller=cancel_export,
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
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST",
                    f"/api/jobs/{original['job_id']}/cancel-export-and-retry",
                    {
                        "export_uuid": "job-stuck",
                        "confirmation": "CANCELAR job-stuck",
                    },
                )
            finally:
                client.close()
                app.jobs._pending.join()

        self.assertEqual(status, 202)
        self.assertEqual(cancelled, [("cliente-a.env", "job-stuck")])
        self.assertEqual(payload["cancelled_export"]["export_uuid"], "job-stuck")
        self.assertEqual(payload["job"]["retry_of_job_id"], original["job_id"])

    def test_frontend_exposes_compact_vm_export_controls_and_confirmed_validation(self) -> None:
        static_root = (
            Path(__file__).resolve().parents[1]
            / "src/tenable_reports/webapp/static"
        )
        html = (static_root / "index.html").read_text(encoding="utf-8")
        javascript = (static_root / "app.js").read_text(encoding="utf-8")

        for field in (
            "vm_export_strategy",
            "vm_num_assets_per_chunk",
            "vm_selective_properties",
        ):
            self.assertIn(f'name="{field}"', html)
        self.assertIn('id="validate-vm-export-button"', html)
        self.assertIn("/vm-export/validate", javascript)
        self.assertIn("duas exportações", javascript)
        self.assertIn("window.confirm", javascript)
        self.assertIn("vm_export_validation", javascript)
        self.assertIn('value="1000"', html)
        self.assertIn("sem novos chunks", javascript)

    def test_frontend_offers_confirmed_cancel_and_retry_for_stuck_export(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src/tenable_reports/webapp/static/app.js"
        ).read_text(encoding="utf-8")
        self.assertIn("Cancelar export e tentar novamente", source)
        self.assertIn("cancel-export-and-retry", source)
        self.assertIn("window.confirm", source)

    def test_frontend_warns_before_leaving_a_period_without_main(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "src/tenable_reports/webapp/static/app.js"
        ).read_text(encoding="utf-8")

        self.assertIn("requires_main_gap_confirmation", source)
        self.assertIn("sem referência para comparações futuras", source)
        self.assertIn("body.allow_main_gap = true", source)

    def test_frontend_exposes_report_set_and_monthly_zip_downloads(self) -> None:
        static_root = (
            Path(__file__).resolve().parents[1]
            / "src/tenable_reports/webapp/static"
        )
        html = (static_root / "index.html").read_text(encoding="utf-8")
        javascript = (static_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('id="archive-all-button"', html)
        self.assertIn('id="archive-dialog"', html)
        self.assertIn('id="archive-month-select"', html)
        self.assertIn('id="archive-download-button"', html)
        self.assertIn("Baixar conjunto ZIP", javascript)
        self.assertIn("/api/report-archives/months", javascript)
        self.assertIn("/api/report-archives/prepare", javascript)
        self.assertIn('data-report-action="archive"', javascript)

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

    def test_report_endpoints_preview_and_permanently_delete_a_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            document = data_root / "manual" / "reports" / "cliente-a" / "run-a" / "base.docx"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_bytes(b"doc")
            registry = InMemoryReportRegistry()
            for run_id in ("run-a", "run-b"):
                registry.register_report(valid_run(run_id))
            registry.promote_main(
                reference_key_for_candidate(valid_run("run-a")), "run-a",
                actor="system", reason="primeiro",
            )
            records = {
                "run-a": ReportSetPurgeRecord(
                    run_id="run-a",
                    client_id="cliente-a",
                    period_id="2026-07",
                    disk_paths=(str(document),),
                    document_count=1,
                    is_main=True,
                    compatible_replacement_run_ids=("run-b",),
                ),
                "run-b": ReportSetPurgeRecord(
                    run_id="run-b",
                    client_id="cliente-a",
                    period_id="2026-07",
                    disk_paths=(),
                    document_count=0,
                    is_main=False,
                ),
            }
            purger = ReportSetPurgeService(
                data_root=data_root,
                repository=_WebPurgeRepository(registry, records),
                active_jobs=lambda: (),
            )
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
                report_set_purger=purger,
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

                status, preview = client.request(
                    "GET", "/api/reports/run-a/purge-preview"
                )
                self.assertEqual(status, 200)
                self.assertEqual(preview["file_count"], 1)
                self.assertEqual(preview["total_bytes"], 3)

                status, _ = client.request(
                    "POST", "/api/reports/run-b/main",
                    {"actor": "analista", "reason": ""},
                )
                self.assertEqual(status, 400)

                status, _ = client.request(
                    "DELETE", "/api/reports/run-a",
                    {
                        "actor": "analista",
                        "reason": "incompleto",
                        "confirmation": "EXCLUIR",
                    },
                )
                self.assertEqual(status, 409)

                status, deleted = client.request(
                    "DELETE", "/api/reports/run-a",
                    {
                        "actor": "analista",
                        "reason": "incompleto",
                        "confirmation": "EXCLUIR",
                        "replacement_run_id": "run-b",
                    },
                )
                self.assertEqual(status, 200)
                self.assertEqual(deleted["deleted_files"], 1)
                self.assertEqual(deleted["deleted_bytes"], 3)
                self.assertFalse(document.exists())
                with self.assertRaises(KeyError):
                    registry.get_report("run-a")
                self.assertEqual(
                    registry.get_main(reference_key_for_candidate(valid_run("run-b"))).run_id,
                    "run-b",
                )
            finally:
                client.close()

    def test_report_endpoint_requires_explicit_permission_to_delete_the_only_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            document = data_root / "manual" / "reports" / "cliente-a" / "run-a" / "base.docx"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_bytes(b"doc")
            registry = InMemoryReportRegistry()
            report = valid_run("run-a")
            key = reference_key_for_candidate(report)
            registry.register_report(report)
            registry.promote_main(key, report.run_id, actor="system", reason="primeiro")
            records = {
                "run-a": ReportSetPurgeRecord(
                    run_id="run-a",
                    client_id="cliente-a",
                    period_id="2026-07",
                    disk_paths=(str(document),),
                    document_count=1,
                    is_main=True,
                ),
            }
            purger = ReportSetPurgeService(
                data_root=data_root,
                repository=_WebPurgeRepository(registry, records),
                active_jobs=lambda: (),
            )
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
                report_set_purger=purger,
            )
            client = LocalClient(app)
            try:
                status, preview = client.request(
                    "GET", "/api/reports/run-a/purge-preview"
                )
                self.assertEqual(status, 200)
                self.assertTrue(preview["requires_main_gap_confirmation"])

                payload = {
                    "actor": "analista",
                    "reason": "incompleto",
                    "confirmation": "EXCLUIR",
                }
                status, _ = client.request("DELETE", "/api/reports/run-a", payload)
                self.assertEqual(status, 409)
                self.assertTrue(document.exists())

                payload["allow_main_gap"] = True
                status, deleted = client.request(
                    "DELETE", "/api/reports/run-a", payload
                )
                self.assertEqual(status, 200)
                self.assertEqual(deleted["deleted_files"], 1)
                self.assertFalse(document.exists())
                with self.assertRaises(KeyError):
                    registry.get_report("run-a")
                self.assertIsNone(registry.get_main(key))
            finally:
                client.close()

    def test_report_archive_endpoints_download_set_and_monthly_main(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            main_document = data_root / "reports" / "main.docx"
            old_document = data_root / "reports" / "old.docx"
            main_document.parent.mkdir(parents=True)
            main_document.write_bytes(b"main")
            old_document.write_bytes(b"old")
            registry = InMemoryReportRegistry()
            main_report = valid_run("run-main")
            old_report = valid_run("run-old")
            registry.register_report(main_report)
            registry.register_report(old_report)
            registry.promote_main(
                reference_key_for_candidate(main_report),
                main_report.run_id,
                actor="system",
                reason="principal",
            )
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
            )
            app.config.add_client({
                "client_id": "cliente-a",
                "display_name": "Cliente A",
            })
            app.database = _ArchiveDashboardDatabase({
                "cliente-a": [
                    {
                        "document_id": 1,
                        "name": "main.docx",
                        "path": str(main_document),
                        "size_bytes": 4,
                        "run_id": "run-main",
                        "document_kind": "base",
                    },
                    {
                        "document_id": 2,
                        "name": "old.docx",
                        "path": str(old_document),
                        "size_bytes": 3,
                        "run_id": "run-old",
                        "document_kind": "base",
                    },
                ]
            }, {
                "run-main": [{
                    "document_id": 1,
                    "name": "main.docx",
                    "path": str(main_document),
                    "size_bytes": 4,
                    "run_id": "run-main",
                    "document_kind": "base",
                }],
                "run-old": [{
                    "document_id": 2,
                    "name": "old.docx",
                    "path": str(old_document),
                    "size_bytes": 3,
                    "run_id": "run-old",
                    "document_kind": "base",
                }],
            })
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "GET", "/api/report-archives/months"
                )
                self.assertEqual(status, 200)
                self.assertEqual(payload["periods"], ["2026-07"])

                status, prepared = client.request(
                    "POST",
                    "/api/report-archives/prepare",
                    {"run_id": "run-old"},
                )
                self.assertEqual(status, 201)
                self.assertEqual(
                    prepared["download_name"],
                    "Cliente-A-Relatorios-Tenable-2026-07.zip",
                )
                self.assertRegex(
                    prepared["download_url"],
                    r"^/api/report-archives/download/[a-f0-9]{32}$",
                )
                status, _, content = client.download(prepared["download_url"])
                self.assertEqual(status, 200)
                with zipfile.ZipFile(io.BytesIO(content)) as package:
                    self.assertIn(
                        "Relatorios-Tenable-2026-07/Cliente A/old.docx",
                        package.namelist(),
                    )

                status, prepared = client.request(
                    "POST",
                    "/api/report-archives/prepare",
                    {"period_id": "2026-07"},
                )
                self.assertEqual(status, 201)
                status, headers, content = client.download(
                    prepared["download_url"]
                )
                self.assertEqual(status, 200)
                self.assertIn(
                    "Relatorios-Tenable-2026-07.zip",
                    headers["Content-Disposition"],
                )
                with zipfile.ZipFile(io.BytesIO(content)) as package:
                    names = package.namelist()
                    self.assertIn(
                        "Relatorios-Tenable-2026-07/Cliente A/main.docx",
                        names,
                    )
                    self.assertNotIn(
                        "Relatorios-Tenable-2026-07/Cliente A/old.docx",
                        names,
                    )

                status, _, _ = client.download(
                    "/api/reports/run-old/archive"
                )
                self.assertEqual(status, 404)
                status, _, _ = client.download(
                    "/api/report-archives/monthly?period_id=2026-07"
                )
                self.assertEqual(status, 404)

                deadline = time.monotonic() + 1
                while (
                    list((data_root / ".downloads").glob("*.zip"))
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertEqual(list((data_root / ".downloads").glob("*.zip")), [])
            finally:
                client.close()

    def test_report_archive_uses_complete_run_documents_beyond_listing_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_root = root / "data"
            documents = []
            for index in range(101):
                path = data_root / "reports" / f"report-{index:03}.docx"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(str(index).encode("ascii"))
                documents.append({
                    "document_id": index + 1,
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                    "run_id": "run-main",
                    "document_kind": "base",
                })
            registry = InMemoryReportRegistry()
            report = valid_run("run-main")
            registry.register_report(report)
            registry.promote_main(
                reference_key_for_candidate(report),
                report.run_id,
                actor="system",
                reason="principal",
            )
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=registry,
            )
            app.config.add_client({
                "client_id": "cliente-a",
                "display_name": "Cliente A",
            })
            app.database = _ArchiveDashboardDatabase(
                {"cliente-a": documents[:100]},
                {"run-main": documents},
            )
            client = LocalClient(app)
            try:
                status, prepared = client.request(
                    "POST",
                    "/api/report-archives/prepare",
                    {"run_id": "run-main"},
                )
                self.assertEqual(status, 201)
                status, _, content = client.download(prepared["download_url"])
                self.assertEqual(status, 200)
                with zipfile.ZipFile(io.BytesIO(content)) as package:
                    docx_names = [
                        name for name in package.namelist()
                        if name.endswith(".docx")
                    ]
                self.assertEqual(len(docx_names), 101)
            finally:
                client.close()

    def test_prepared_archive_claim_rejects_expired_deadline_and_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "data" / ".downloads" / "tenable-reports-expired.zip"
            archive_path.parent.mkdir(parents=True)
            archive_path.write_bytes(b"expired")
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=InMemoryReportRegistry(),
            )
            download_id = "a" * 32
            app._prepared_archives[download_id] = (
                ReportArchiveResult(
                    path=archive_path,
                    download_name="expired.zip",
                    included_clients=1,
                    included_documents=1,
                    omissions=(),
                ),
                100.0,
            )

            with patch(
                "tenable_reports.webapp.server.time.monotonic",
                return_value=101.0,
            ):
                with self.assertRaisesRegex(KeyError, "expirado"):
                    app.claim_prepared_archive(download_id)

            self.assertFalse(archive_path.exists())

    def test_dashboard_startup_removes_expired_orphan_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = (
                root / "data" / ".downloads" / "tenable-reports-orphan.zip"
            )
            archive_path.parent.mkdir(parents=True)
            archive_path.write_bytes(b"orphan")
            old = time.time() - 10 * 60
            os.utime(archive_path, (old, old))

            DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                report_registry=InMemoryReportRegistry(),
            )

            self.assertFalse(archive_path.exists())

    def test_dashboard_database_reports_exposes_tag_document_metadata(self) -> None:
        ended_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        created_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
        database = DashboardDatabase.__new__(DashboardDatabase)
        database.database = _RowsDatabase([(
            7, "C:/reports/tag.docx", 1234, "VALID", "run-a", "2026-07",
            "MANUAL", ended_at, created_at, "tag", None,
            "tag-a", "Equipe", "Infra",
        )])

        report = database.reports("cliente-a")[0]

        self.assertEqual(report["document_kind"], "tag")
        self.assertIsNone(report["document_variant"])
        self.assertEqual(report["tag_uuid"], "tag-a")
        self.assertEqual(report["tag_category"], "Equipe")
        self.assertEqual(report["tag_value"], "Infra")
        self.assertIn("d.document_kind", database.database.connection_value.query)

    def test_dashboard_database_report_documents_queries_complete_run(self) -> None:
        ended_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
        created_at = datetime(2026, 8, 2, tzinfo=timezone.utc)
        database = DashboardDatabase.__new__(DashboardDatabase)
        database.database = _RowsDatabase([(
            7, "C:/reports/base.docx", 1234, "VALID", "run-a", "2026-07",
            "MANUAL", ended_at, created_at, "base", None,
            None, None, None,
        )])

        documents = database.report_documents("run-a")

        self.assertEqual([item["name"] for item in documents], ["base.docx"])
        query = database.database.connection_value.query.lower()
        self.assertIn("where r.run_id = %s", query)
        self.assertNotIn("limit", query)
        self.assertEqual(
            database.database.connection_value.parameters,
            ("run-a",),
        )

    def test_archive_download_removes_temporary_zip_when_connection_aborts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "prepared.zip"
            with zipfile.ZipFile(archive_path, "w") as package:
                package.writestr("report.docx", b"report")
            handler = DashboardHandler.__new__(DashboardHandler)
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler.wfile = Mock()
            handler.wfile.write.side_effect = ConnectionAbortedError(
                "cliente desconectou"
            )

            with self.assertRaises(ConnectionAbortedError):
                handler._download_archive(ReportArchiveResult(
                    path=archive_path,
                    download_name="reports.zip",
                    included_clients=1,
                    included_documents=1,
                    omissions=(),
                ))

            self.assertFalse(archive_path.exists())

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

    def test_vm_export_settings_round_trip_and_reject_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            created = store.add_client({
                "client_id": "cliente-vm",
                "display_name": "Cliente VM",
                "vm_export_strategy": "split",
                "vm_num_assets_per_chunk": 400,
                "vm_selective_properties": "enabled",
            })
            self.assertEqual(created["vm_export_strategy"], "split")
            self.assertEqual(created["vm_num_assets_per_chunk"], 400)
            self.assertEqual(created["vm_selective_properties"], "enabled")

            updated = store.update_client("cliente-vm", {
                "vm_export_strategy": "combined",
                "vm_num_assets_per_chunk": 250,
                "vm_selective_properties": "validation",
            })
            self.assertEqual(updated["vm_export_strategy"], "combined")
            self.assertEqual(updated["vm_num_assets_per_chunk"], 250)
            self.assertEqual(updated["vm_selective_properties"], "validation")
            load_client_profile(root / "clients" / "managed" / "cliente-vm.json")

            invalid = (
                ({"vm_export_strategy": "automatic"}, "strategy"),
                ({"vm_num_assets_per_chunk": 49}, "num_assets_per_chunk"),
                ({"vm_selective_properties": "always"}, "selective_properties"),
            )
            for values, message in invalid:
                with self.subTest(values=values):
                    with self.assertRaisesRegex(ValueError, message):
                        store.update_client("cliente-vm", values)

    def test_new_client_uses_recommended_vm_chunk_size(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )

            created = store.add_client({
                "client_id": "cliente-recomendado",
                "display_name": "Cliente Recomendado",
            })

            self.assertEqual(created["vm_num_assets_per_chunk"], 1000)

    def test_vm_export_validation_route_enqueues_explicit_ab_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []

            def runner(command, cwd):
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "complete",
                        "run_id": "run-validation",
                        "clients": [{"payload": {
                            "vm_export_mode": "validation",
                            "vm_export_outcome": "PASSED",
                            "vm_export_comparison": "comparison.json",
                        }}],
                    }) + "\n",
                    stderr="",
                )

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
            )
            app.config.add_client({
                "client_id": "cliente-validacao",
                "display_name": "Cliente Validação",
                "access_key": "access",
                "secret_key": "secret",
            })
            client = LocalClient(app)
            try:
                status, payload = client.request(
                    "POST",
                    "/api/clients/cliente-validacao/vm-export/validate",
                    {},
                )
                self.assertEqual(status, 202)
                self.assertEqual(payload["job"]["vm_selective_mode"], "validation")
                app.jobs._pending.join()
                completed = app.jobs.snapshot()[0]
                self.assertEqual(
                    completed["vm_export_validation"]["outcome"], "PASSED"
                )
            finally:
                client.close()

            self.assertIn("--vm-selective-mode", observed[0])
            self.assertIn("validation", observed[0])
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
            original = jobs.enqueue(["cliente-a"], {
                "mode": "manual",
                "days": 15,
                "was_failure_policy": "retry_then_continue",
            })[0]
            jobs._pending.join()
            retried = jobs.retry(original["job_id"])
            self.assertEqual(retried["mode"], "manual")
            self.assertEqual(retried["days"], 15)
            self.assertEqual(
                retried["was_failure_policy"],
                "retry_then_continue",
            )
            self.assertEqual(retried["retry_of_job_id"], original["job_id"])

    def test_forced_live_collection_reaches_command_and_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []

            def runner(command, cwd):
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command, 1, stdout="", stderr="falha controlada"
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            original = jobs.enqueue(["cliente-a"], {
                "mode": "manual",
                "days": 30,
                "force_live_collection": True,
            })[0]
            jobs._pending.join()
            retried = jobs.retry(original["job_id"])
            jobs._pending.join()

        self.assertTrue(original["force_live_collection"])
        self.assertTrue(retried["force_live_collection"])
        self.assertEqual(len(observed), 2)
        self.assertIn("--force-live-collection", observed[0])
        self.assertIn("--force-live-collection", observed[1])
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

    def test_cloud_settings_save_token_without_returning_it_and_blank_edit_preserves_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = DashboardConfigStore(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
            )
            created = store.add_client({
                "client_id": "cliente-cloud",
                "display_name": "Cliente Cloud",
                "tenant_id": "tenant-cloud",
                "cloud_enabled": True,
                "cloud_api_secret": "cloud-secret-original",
                "cloud_environment": "us_gov",
                "cloud_layout": "comparison",
            })

            self.assertTrue(created["cloud_enabled"])
            self.assertTrue(created["cloud_token_saved"])
            self.assertEqual(created["cloud_environment"], "us_gov")
            self.assertEqual(created["cloud_layout"], "expanded")
            self.assertNotIn("cloud_api_secret", created)
            self.assertNotIn("cloud-secret-original", json.dumps(created))

            updated = store.update_client("cliente-cloud", {
                "cloud_enabled": True,
                "cloud_api_secret": "",
                "cloud_environment": "global",
                "cloud_layout": "expanded",
            })
            env_text = (root / "credentials" / "cliente-cloud.env").read_text(
                encoding="utf-8"
            )
            profile = load_client_profile(
                root / "clients" / "managed" / "cliente-cloud.json"
            )

            self.assertIn("TCS_API_SECRET=cloud-secret-original", env_text)
            self.assertTrue(updated["cloud_token_saved"])
            self.assertEqual(profile.cloud_security_scope.environment, "global")
            self.assertEqual(profile.cloud_security_scope.layout, "expanded")

    def test_connection_check_keeps_vm_and_cloud_results_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                connection_checker=lambda _path: {
                    "ok": True,
                    "latency_ms": 11,
                    "message": "VM pronta",
                    "checked_at": "2026-08-27T12:00:00+00:00",
                },
                cloud_connection_checker=lambda _path, environment: {
                    "ok": False,
                    "latency_ms": 22,
                    "message": f"Cloud indisponivel em {environment}",
                    "checked_at": "2026-08-27T12:00:00+00:00",
                    "retryable": True,
                },
            )
            app.config.add_client({
                "client_id": "cliente-cloud",
                "display_name": "Cliente Cloud",
                "access_key": "access",
                "secret_key": "secret",
                "cloud_enabled": True,
                "cloud_api_secret": "cloud-secret",
                "cloud_environment": "global",
            })

            result = app.check_connections(["cliente-cloud"])[0]

            self.assertTrue(result["ok"])
            self.assertFalse(result["cloud"]["ok"])
            self.assertTrue(result["cloud"]["retryable"])
            self.assertNotIn("cloud-secret", json.dumps(result))

    def test_cloud_token_or_environment_change_invalidates_contract_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalidated: list[tuple[str, str]] = []
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                cloud_contract_invalidator=lambda **values: (
                    invalidated.append((values["client_id"], values["environment"])) or 1
                ),
            )
            app.config.add_client({
                "client_id": "cliente-cloud",
                "display_name": "Cliente Cloud",
                "cloud_enabled": True,
                "cloud_api_secret": "token-original",
                "cloud_environment": "global",
            })

            updated = app.update_client("cliente-cloud", {
                "cloud_api_secret": "token-novo",
                "cloud_environment": "us_gov",
            })

            self.assertEqual(
                set(invalidated),
                {("cliente-cloud", "global"), ("cliente-cloud", "us_gov")},
            )
            self.assertEqual(updated["cloud_contract_cache_invalidated"], 2)
            self.assertNotIn("token-novo", json.dumps(updated))

    def test_cloud_web_controls_and_retry_action_are_exposed(self) -> None:
        static_root = (
            Path(__file__).resolve().parents[1]
            / "src" / "tenable_reports" / "webapp" / "static"
        )
        html = (static_root / "index.html").read_text(encoding="utf-8")
        javascript = (static_root / "app.js").read_text(encoding="utf-8")

        self.assertIn('name="cloud_api_secret"', html)
        self.assertIn('name="cloud_environment"', html)
        self.assertNotIn('name="cloud_layout"', html)
        self.assertNotIn("elements.cloud_layout", javascript)
        self.assertNotIn("Modelo expandido", javascript)
        self.assertIn('id="test-cloud-button"', html)
        self.assertIn("Tentar Cloud novamente", javascript)
        self.assertIn("TENABLE_CLOUD_PROGRESS", javascript)
        self.assertIn("/retry-cloud", javascript)

    def test_was_recovery_controls_are_exposed(self) -> None:
        static_root = (
            Path(__file__).resolve().parents[1]
            / "src" / "tenable_reports" / "webapp" / "static"
        )
        javascript = (static_root / "app.js").read_text(encoding="utf-8")

        self.assertIn("Continuar sem WEB", javascript)
        self.assertIn("Tentar WEB novamente", javascript)
        self.assertIn("/api/was-recoveries/", javascript)
        self.assertIn('was_recovery?.status === "RETRY_AVAILABLE"', javascript)

    def test_published_automatic_was_recovery_rejects_continue_without_was(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checkpoint = WasRecoveryCheckpoint(
                schema_version=1,
                run_id="run-was-publicado",
                client_id="cliente-was",
                tenant_id="cliente-was",
                execution_type="AUTOMATIC_MONTHLY",
                period={
                    "start_at": "2026-07-01T03:00:00Z",
                    "end_at": "2026-08-01T03:00:00Z",
                    "reference_at": "2026-08-01T03:00:00Z",
                    "timezone": "America/Fortaleza",
                    "mode": "PREVIOUS_CALENDAR_MONTH",
                },
                profile_path=str(root / "clients" / "managed" / "cliente-was.json"),
                output_root=str(root / "data" / "automatic-monthly"),
                include_output=False,
                was_status="UNAVAILABLE",
                was_failure=WasFailureDetails(
                    code="WAS_COLLECTION_UNAVAILABLE",
                    retryable=True,
                ),
            )
            checkpoint_path = write_was_recovery_checkpoint(
                root / "was-recovery.json", checkpoint
            )
            record = WasRecoveryRecord(
                run_id=checkpoint.run_id,
                client_id=checkpoint.client_id,
                tenant_id=checkpoint.tenant_id,
                status=WasRecoveryStatus.RETRY_AVAILABLE,
                checkpoint_path=str(checkpoint_path),
                checkpoint=checkpoint,
            )
            repository = Mock()
            repository.get.return_value = record
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                was_recovery_repository=repository,
            )
            app.config.add_client({
                "client_id": "cliente-was",
                "display_name": "Cliente WAS",
                "tenant_id": "cliente-was",
                "was_enabled": True,
                "access_key": "test-access-key",
                "secret_key": "test-secret-key",
            })

            with self.assertRaisesRegex(ValueError, "somente.*retentativa WEB"):
                app.recover_was_report(
                    run_id=checkpoint.run_id,
                    action="continue",
                    confirmation=f"CONTINUAR SEM WAS {checkpoint.run_id}",
                )

    def test_job_queue_preserves_waiting_was_decision_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "WAITING_WAS_DECISION",
                        "run_id": "orchestration-run",
                        "clients": [{
                            "client_id": "cliente-was",
                            "status": "WAITING_WAS_DECISION",
                            "payload": {
                                "status": "waiting_was_decision",
                                "run_id": "run-was-pendente",
                                "client_id": "cliente-was",
                                "checkpoint": str(root / "was-recovery.json"),
                                "was_failure": {
                                    "code": "WAS_COLLECTION_UNAVAILABLE",
                                    "message": "Falha WAS sanitizada.",
                                    "retryable": True,
                                    "export_uuid": "was-job",
                                    "origin": "created",
                                    "remote_status": "PROCESSING",
                                    "completed_chunks": 0,
                                    "total_chunks": 1,
                                },
                            },
                        }],
                    }, ensure_ascii=False) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-was"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            job = jobs.snapshot()[0]

            self.assertEqual(job["status"], "WAITING_WAS_DECISION")
            self.assertEqual(job["run_id"], "run-was-pendente")
            self.assertEqual(job["was_recovery"]["checkpoint"], str(root / "was-recovery.json"))
            self.assertEqual(job["was_recovery"]["failure"]["export_uuid"], "was-job")

    def test_was_recovery_routes_require_confirmation_and_run_only_resume_was(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []
            checkpoint = WasRecoveryCheckpoint(
                schema_version=1,
                run_id="run-was-pendente",
                client_id="cliente-was",
                tenant_id="cliente-was",
                execution_type="MANUAL",
                period={
                    "start_at": "2026-07-01T03:00:00Z",
                    "end_at": "2026-08-01T03:00:00Z",
                    "reference_at": "2026-08-12T13:00:00Z",
                    "timezone": "America/Fortaleza",
                    "mode": "EXPLICIT_RANGE",
                },
                profile_path=str(root / "clients" / "managed" / "cliente-was.json"),
                output_root=str(root / "data" / "manual"),
                include_output=False,
                was_status="UNAVAILABLE",
                was_failure=WasFailureDetails(
                    code="WAS_COLLECTION_UNAVAILABLE",
                    message="Falha WAS sanitizada.",
                    retryable=True,
                    export_uuid="was-job",
                ),
            )
            checkpoint_path = write_was_recovery_checkpoint(
                root / "data" / "manual" / "recovery" / "cliente-was"
                / "run-was-pendente" / "was-recovery.json",
                checkpoint,
            )
            record = WasRecoveryRecord(
                run_id=checkpoint.run_id,
                client_id=checkpoint.client_id,
                tenant_id=checkpoint.tenant_id,
                status=WasRecoveryStatus.WAITING_WAS_DECISION,
                checkpoint_path=str(checkpoint_path),
                checkpoint=checkpoint,
            )
            repository = Mock()
            repository.get.return_value = record
            repository.pending.return_value = (record,)

            def runner(command, cwd, progress_callback=None):
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "complete_with_warnings",
                        "run_id": "run-was-pendente",
                        "client_id": "cliente-was",
                        "warnings": [{
                            "code": "WAS_COLLECTION_UNAVAILABLE",
                            "message": "Relatório concluído sem WEB.",
                        }],
                    }, ensure_ascii=False) + "\n",
                    stderr="",
                )

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
                was_recovery_repository=repository,
            )
            app.config.add_client({
                "client_id": "cliente-was",
                "display_name": "Cliente WAS",
                "tenant_id": "cliente-was",
                "was_enabled": True,
                "access_key": "test-access-key",
                "secret_key": "test-secret-key",
            })
            client = LocalClient(app)
            try:
                bad_status, _ = client.request(
                    "POST",
                    "/api/was-recoveries/run-was-pendente/continue",
                    {"confirmation": "sim"},
                )
                continue_status, continue_payload = client.request(
                    "POST",
                    "/api/was-recoveries/run-was-pendente/continue",
                    {"confirmation": "CONTINUAR SEM WAS run-was-pendente"},
                )
                app.jobs._pending.join()
                retry_status, retry_payload = client.request(
                    "POST",
                    "/api/was-recoveries/run-was-pendente/retry",
                    {"confirmation": "RETENTAR WAS run-was-pendente"},
                )
                app.jobs._pending.join()
            finally:
                client.close()

            self.assertEqual(bad_status, 400)
            self.assertEqual(continue_status, 202)
            self.assertEqual(retry_status, 202)
            self.assertEqual(continue_payload["job"]["operation"], "was_continue")
            self.assertEqual(retry_payload["job"]["operation"], "was_retry")
            self.assertEqual(len(observed), 2)
            for command in observed:
                self.assertIn("resume-was", command)
                self.assertNotIn("orchestrate", command)
                self.assertIn("--checkpoint", command)
            self.assertNotIn("--confirm-live-api", observed[0])
            self.assertIn("--confirm-live-api", observed[1])

    def test_job_queue_exposes_cloud_progress_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def runner(command, cwd, progress_callback=None):
                progress_callback({
                    "event": "TENABLE_CLOUD_PROGRESS",
                    "status": "STARTED",
                    "stage": "COLLECTION",
                    "source": "findings",
                    "current": 2,
                    "total": 5,
                })
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "COMPLETE_WITH_WARNINGS",
                        "run_id": "run-cloud-progress",
                        "clients": [{"payload": {
                            "cloud_status": "COMPLETE",
                            "cloud_warnings": [],
                        }}],
                    }) + "\n",
                    stderr="",
                )

            jobs = JobQueue(root, root / "orchestration" / "clients.json", runner)
            jobs.enqueue(["cliente-cloud"], {"mode": "manual", "days": 30})
            jobs._pending.join()
            job = jobs.snapshot()[0]

            self.assertEqual(job["cloud_progress"]["stage"], "COLLECTION")
            self.assertEqual(job["cloud_progress"]["source"], "findings")
            self.assertEqual(job["cloud_status"], "COMPLETE")

    def test_cloud_retry_route_requires_exact_confirmation_and_runs_only_retry_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []

            def runner(command, cwd, progress_callback=None):
                observed.append(list(command))
                progress_callback({
                    "event": "TENABLE_CLOUD_PROGRESS",
                    "status": "FINISHED",
                    "stage": "PUBLICATION",
                    "documents": 2,
                })
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "complete",
                        "run_id": "run-cloud-failed",
                        "client_id": "cliente-cloud",
                        "cloud_status": "COMPLETE",
                        "warnings": [],
                        "general_collection_repeated": False,
                    }) + "\n",
                    stderr="",
                )

            registry = InMemoryReportRegistry()
            registry.register_report(valid_run("run-cloud-failed", client_id="cliente-cloud"))
            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
                report_registry=registry,
            )
            app.config.add_client({
                "client_id": "cliente-cloud",
                "display_name": "Cliente Cloud",
                "cloud_enabled": True,
                "cloud_api_secret": "cloud-secret",
            })
            app.report_rows = lambda _client_id, include_deleted=False: [{
                "run_id": "run-cloud-failed",
                "cloud_retry_available": True,
            }]
            client = LocalClient(app)
            try:
                bad_status, _ = client.request(
                    "POST",
                    "/api/reports/run-cloud-failed/retry-cloud",
                    {"confirmation": "sim"},
                )
                status, payload = client.request(
                    "POST",
                    "/api/reports/run-cloud-failed/retry-cloud",
                    {"confirmation": "RETENTAR CLOUD run-cloud-failed"},
                )
                app.jobs._pending.join()
            finally:
                client.close()

            self.assertEqual(bad_status, 400)
            self.assertEqual(status, 202)
            self.assertEqual(payload["job"]["operation"], "cloud_retry")
            self.assertEqual(len(observed), 1)
            self.assertIn("retry-cloud", observed[0])
            self.assertNotIn("orchestrate", observed[0])
            self.assertIn("--run-id", observed[0])
            self.assertEqual(app.jobs.snapshot()[0]["cloud_status"], "COMPLETE")
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

    def test_run_all_retries_was_automatically_but_individual_run_waits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            observed: list[list[str]] = []

            def runner(command, cwd, progress_callback=None):
                observed.append(list(command))
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout=json.dumps({
                        "status": "COMPLETE",
                        "run_id": f"run-{len(observed)}",
                    }) + "\n",
                    stderr="",
                )

            app = DashboardApplication(
                project_root=root,
                config_path=root / "orchestration" / "clients.json",
                runner=runner,
            )
            for client_id in ("cliente-a", "cliente-b"):
                app.config.add_client({
                    "client_id": client_id,
                    "display_name": client_id.upper(),
                    "access_key": "access",
                    "secret_key": "secret",
                })

            app.enqueue_jobs(
                ["cliente-a", "cliente-b"],
                {"mode": "manual", "run_scope": "all"},
            )
            app.jobs._pending.join()

            self.assertEqual(len(observed), 2)
            for command in observed:
                policy_index = command.index("--was-failure-policy")
                self.assertEqual(
                    command[policy_index + 1],
                    "retry_then_continue",
                )

            observed.clear()
            app.enqueue_jobs(
                ["cliente-a"],
                {"mode": "manual", "run_scope": "single"},
            )
            app.jobs._pending.join()

            self.assertEqual(len(observed), 1)
            self.assertNotIn("--was-failure-policy", observed[0])


if __name__ == "__main__":
    unittest.main()
