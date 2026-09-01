from __future__ import annotations

import json
import threading
from urllib.request import Request, urlopen
from uuid import UUID

import pytest

from tenable_reports.application.web_batches import BatchClientConflictError
from tenable_reports.domain.web_batches import BatchAction
from tenable_reports.webapp.server import DashboardHTTPServer


class _BatchApp:
    def __init__(self) -> None:
        self.actions: list[tuple[UUID, BatchAction, str, str, str]] = []
        self.derivations = []
        self.conflict = False

    def batch_state(self, batch_id: str):
        return {
            "batch": {"id": str(batch_id), "status": "RUNNING"},
            "jobs": [],
            "events": [],
        }

    def request_batch_action(
        self,
        batch_id: str,
        action: BatchAction,
        *,
        actor: str,
        reason: str,
        idempotency_key: str,
    ):
        self.actions.append(
            (UUID(str(batch_id)), action, actor, reason, idempotency_key)
        )
        return {
            "batch": {"id": str(batch_id), "status": action.value},
            "jobs": [],
            "events": [],
        }

    def derive_batch(self, request):
        if self.conflict:
            raise BatchClientConflictError(("client-fixture",))
        self.derivations.append(request)
        return {
            "batch": {
                "id": str(UUID(int=902)),
                "status": "QUEUED",
                "kind": request.kind.value,
            },
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
    body = {
        "idempotency_key": f"fixture:{route}",
        "actor": "analista-local",
        "reason": "teste local",
    }
    if action is BatchAction.STOP:
        body["confirmation"] = f"PARAR {str(batch_id)[:8]}"
    request = Request(
        f"{base}/api/batches/{batch_id}/{route}",
        data=json.dumps(body).encode("utf-8"),
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
    assert app.actions == [
        (batch_id, action, "analista-local", "teste local", f"fixture:{route}")
    ]

@pytest.mark.parametrize(
    ("route", "kind"),
    (
        ("retry-incomplete", BatchAction.RETRY_INCOMPLETE),
        ("rerun-all", BatchAction.RERUN_ALL),
    ),
)
def test_batch_derivation_routes_receive_idempotency_and_confirmation(
    batch_server,
    route: str,
    kind: BatchAction,
) -> None:
    app, base = batch_server
    batch_id = UUID(int=903)
    request = Request(
        f"{base}/api/batches/{batch_id}/{route}",
        data=json.dumps(
            {
                "idempotency_key": f"fixture:{route}",
                "confirmation": f"GERAR NOVAMENTE {str(batch_id)[:8]}",
                "actor": "analista-local",
                "reason": "teste local",
            }
        ).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Tenable-UI": "1",
        },
    )

    with urlopen(request, timeout=3) as response:
        payload = json.loads(response.read().decode("utf-8"))

    assert response.status == 202
    assert payload["batch"]["kind"] == kind.value
    assert len(app.derivations) == 1
    derived = app.derivations[0]
    assert derived.source_batch_id == batch_id
    assert derived.kind is kind
    assert derived.idempotency_key == f"fixture:{route}"
    assert derived.actor == "analista-local"


def test_batch_derivation_conflict_returns_409(batch_server) -> None:
    from urllib.error import HTTPError

    app, base = batch_server
    app.conflict = True
    batch_id = UUID(int=904)
    request = Request(
        f"{base}/api/batches/{batch_id}/retry-incomplete",
        data=json.dumps({"idempotency_key": "fixture:conflict"}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Tenable-UI": "1",
        },
    )

    with pytest.raises(HTTPError) as captured:
        urlopen(request, timeout=3)

    assert captured.value.code == 409
    payload = json.loads(captured.value.read().decode("utf-8"))
    assert "client-fixture" in payload["error"]

def test_stop_requires_short_batch_confirmation(batch_server) -> None:
    from urllib.error import HTTPError

    _app, base = batch_server
    batch_id = UUID(int=905)
    request = Request(
        f"{base}/api/batches/{batch_id}/stop",
        data=b"{}",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Tenable-UI": "1",
        },
    )

    with pytest.raises(HTTPError) as captured:
        urlopen(request, timeout=3)

    assert captured.value.code == 400
    payload = json.loads(captured.value.read().decode("utf-8"))
    assert f"PARAR {str(batch_id)[:8]}" in payload["error"]

def test_batch_action_requires_idempotency_key(batch_server) -> None:
    from urllib.error import HTTPError

    _app, base = batch_server
    batch_id = UUID(int=906)
    request = Request(
        f"{base}/api/batches/{batch_id}/pause",
        data=json.dumps({"actor": "analista-local", "reason": "teste"}).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Tenable-UI": "1"},
    )

    with pytest.raises(HTTPError) as captured:
        urlopen(request, timeout=3)

    assert captured.value.code == 400
    payload = json.loads(captured.value.read().decode("utf-8"))
    assert "idempot" in payload["error"].lower()
