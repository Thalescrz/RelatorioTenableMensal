from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tenable_reports.application.monthly_schedule import MonthlyScheduleService
from tenable_reports.infrastructure.monthly_scheduler import SchedulerBackend, SchedulerState, SchedulerStatus
from tenable_reports.webapp.server import DashboardConfigStore, DashboardHTTPServer


@pytest.fixture
def monthly_api(tmp_path, monkeypatch):
    store = DashboardConfigStore(project_root=tmp_path, config_path=tmp_path / "clients.json")
    clients = [
        {"client_id": "a", "display_name": "Cliente A", "enabled": True, "credentials_ready": True},
        {"client_id": "b", "display_name": "Cliente B", "enabled": True, "credentials_ready": True},
    ]
    monkeypatch.setattr(store, "list_clients", lambda: clients)
    scheduler = SimpleNamespace(query=lambda config: SchedulerState(
        backend=SchedulerBackend.EXTERNAL, status=SchedulerStatus.SYNCHRONIZED,
    ))
    service = MonthlyScheduleService(store=store, scheduler=scheduler)
    app = SimpleNamespace(monthly_schedule=service)
    server = DashboardHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, suffix="", payload=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        req = Request(
            f"http://127.0.0.1:{server.server_port}/api/admin/monthly-schedule{suffix}",
            method=method, data=body,
            headers={"X-Tenable-UI": "1", "Content-Type": "application/json"},
        )
        try:
            response = urlopen(req, timeout=3)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.loads(response.read())

    try:
        yield store, request
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_monthly_api_persists_selection_across_reload_and_matches_preview(monthly_api):
    store, request = monthly_api
    status, saved = request("PATCH", payload={"selected_client_ids": ["b"]})
    assert status == 200
    assert saved["eligible_client_ids"] == ["b"]
    reloaded = DashboardConfigStore(project_root=store.project_root, config_path=store.config_path)
    assert reloaded.monthly_schedule().selected_client_ids == ("b",)
    assert store.raw()["clients"] == []  # The policy never changes client registration.
    status, preview = request("POST", "/validate", {})
    assert status == 200
    assert preview["eligible_client_ids"] == ["b"]
    assert "scheduler" not in preview  # Validation does not contact the OS scheduler.
    status, loaded = request("GET")
    assert status == 200
    assert loaded["config"]["selected_client_ids"] == ["b"]


def test_monthly_api_rejects_unknown_selection_and_preserves_previous_policy(monthly_api):
    store, request = monthly_api
    request("PATCH", payload={"selected_client_ids": ["a"]})
    status, rejected = request("PATCH", payload={"selected_client_ids": ["unknown"]})
    assert status == 400
    assert "cadastrados" in rejected["error"]
    assert store.monthly_schedule().selected_client_ids == ("a",)


def test_monthly_api_saves_empty_selection_and_can_restore_all(monthly_api):
    store, request = monthly_api
    status, empty = request("PATCH", payload={"selected_client_ids": []})
    assert status == 200
    assert empty["eligible_client_ids"] == []
    assert store.monthly_schedule().selected_client_ids == ()
    status, all_clients = request("PATCH", payload={"selected_client_ids": None})
    assert status == 200
    assert all_clients["eligible_client_ids"] == ["a", "b"]
