from __future__ import annotations

import json
import threading
from urllib.request import Request, urlopen
from uuid import UUID

import pytest

from tenable_reports.domain.web_batches import BatchAction
from tenable_reports.webapp.server import DashboardHTTPServer


class _BatchApp:
    def __init__(self) -> None:
        self.actions: list[tuple[UUID, BatchAction]] = []

    def batch_state(self, batch_id: str):
        return {
            "batch": {"id": str(batch_id), "status": "RUNNING"},
            "jobs": [],
            "events": [],
        }

    def request_batch_action(self, batch_id: str, action: BatchAction):
        self.actions.append((UUID(str(batch_id)), action))
        return {
            "batch": {"id": str(batch_id), "status": action.value},
            "jobs": [],
            "events": [],
        }


@pytest.fixture
def batch_server():
    app = _BatchApp()
    server = DashboardHTTPServer(("127.0.0.1", 0), app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield app, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_batch_detail_route_returns_durable_state(batch_server) -> None:
    _app, base = batch_server
    batch_id = UUID(int=900)

    with urlopen(f"{base}/api/batches/{batch_id}", timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert response.status == 200
    assert payload["batch"]["id"] == str(batch_id)
    assert payload["batch"]["status"] == "RUNNING"


@pytest.mark.parametrize(
    ("route", "action"),
    (
        ("pause", BatchAction.PAUSE),
        ("resume", BatchAction.RESUME),
        ("stop", BatchAction.STOP),
    ),
)
def test_batch_action_routes_are_local_writes(
    batch_server,
    route: str,
    action: BatchAction,
) -> None:
    app, base = batch_server
    batch_id = UUID(int=901)
    request = Request(
        f"{base}/api/batches/{batch_id}/{route}",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Tenable-UI": "1",
        },
    )

    with urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert response.status == 200
    assert payload["batch"]["status"] == action.value
    assert app.actions == [(batch_id, action)]

