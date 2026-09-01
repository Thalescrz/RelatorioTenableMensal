from __future__ import annotations

import importlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from tenable_reports.domain.execution_control import ExecutionInterruptedError


QUERY = """
query VirtualMachines($first: Int!, $after: String) {
  VirtualMachines(first: $first, after: $after) {
    nodes { Id }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def _module():
    return importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.client"
    )


@dataclass(frozen=True)
class ScriptedItem:
    status: int
    payload: Any
    headers: Mapping[str, str] | None = None
    raw: bytes | None = None


class ScriptedTransport:
    def __init__(self, module: Any, items: list[ScriptedItem]) -> None:
        self.module = module
        self.items = list(items)
        self.requests: list[dict[str, Any]] = []

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> Any:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        item = self.items.pop(0)
        content = item.raw
        if content is None:
            content = json.dumps(item.payload).encode("utf-8")
        return self.module.CloudTransportResponse(
            status_code=item.status,
            headers=dict(item.headers or {}),
            content=content,
        )


def _connection(nodes: list[dict[str, Any]], has_next: bool, cursor: str | None) -> dict[str, Any]:
    return {
        "data": {
            "VirtualMachines": {
                "nodes": nodes,
                "pageInfo": {
                    "hasNextPage": has_next,
                    "endCursor": cursor,
                },
            }
        }
    }


def _config(module: Any, *, retries: int = 0, min_page_size: int = 5) -> Any:
    return module.CloudGraphQLConfig(
        endpoint="https://app.tenable.com/graphql",
        api_secret="fixture-secret",
        retries=retries,
        min_page_size=min_page_size,
    )


def test_paginate_sends_bearer_user_agent_and_follows_all_cursors() -> None:
    module = _module()
    transport = ScriptedTransport(
        module,
        [
            ScriptedItem(200, _connection([{"Id": "asset-a"}], True, "c1")),
            ScriptedItem(200, _connection([{"Id": "asset-b"}], False, None)),
        ],
    )
    events: list[Mapping[str, Any]] = []
    client = module.CloudGraphQLClient(
        _config(module), transport=transport, sleeper=lambda _: None
    )

    nodes = list(
        client.paginate(
            QUERY,
            "VirtualMachines",
            page_size=20,
            progress=events.append,
        )
    )

    assert [item["Id"] for item in nodes] == ["asset-a", "asset-b"]
    first = transport.requests[0]
    assert first["method"] == "POST"
    assert first["headers"]["Authorization"] == "Bearer fixture-secret"
    assert first["headers"]["User-Agent"].startswith("RelatorioTenableMensal/")
    assert json.loads(first["body"])["variables"] == {"first": 20, "after": None}
    assert json.loads(transport.requests[1]["body"])["variables"]["after"] == "c1"
    assert events[-1]["records"] == 2


def test_repeated_cursor_is_rejected_instead_of_looping() -> None:
    module = _module()
    transport = ScriptedTransport(
        module,
        [
            ScriptedItem(200, _connection([], True, "c1")),
            ScriptedItem(200, _connection([], True, "c1")),
        ],
    )
    client = module.CloudGraphQLClient(
        _config(module), transport=transport, sleeper=lambda _: None
    )

    with pytest.raises(module.CloudContractError, match="cursor"):
        list(client.paginate(QUERY, "VirtualMachines", page_size=20))


def test_rate_limit_honors_retry_after_and_is_retryable() -> None:
    module = _module()
    waits: list[float] = []
    transport = ScriptedTransport(
        module,
        [
            ScriptedItem(429, {"message": "limited"}, {"retry-after": "3"}),
            ScriptedItem(200, {"data": {"ok": True}}),
        ],
    )
    client = module.CloudGraphQLClient(
        _config(module, retries=1), transport=transport, sleeper=waits.append
    )

    assert client.execute("query { ok }", {}) == {"ok": True}
    assert waits == [3.0]
    assert len(transport.requests) == 2


def test_complexity_reduction_keeps_cursor_and_retries_smaller_pages() -> None:
    module = _module()
    complexity = {
        "errors": [
            {
                "message": "Query is too complex",
                "extensions": {"code": "QUERY_TOO_COMPLEX"},
            }
        ]
    }
    transport = ScriptedTransport(
        module,
        [
            ScriptedItem(200, complexity),
            ScriptedItem(200, complexity),
            ScriptedItem(200, _connection([{"Id": "asset-a"}], False, None)),
        ],
    )
    client = module.CloudGraphQLClient(
        _config(module, min_page_size=5),
        transport=transport,
        sleeper=lambda _: None,
    )

    assert list(client.paginate(QUERY, "VirtualMachines", page_size=20)) == [
        {"Id": "asset-a"}
    ]
    variables = [json.loads(item["body"])["variables"] for item in transport.requests]
    assert [item["first"] for item in variables] == [20, 10, 5]
    assert [item["after"] for item in variables] == [None, None, None]


def test_paginate_stops_before_next_request_and_preserves_remote_operation() -> None:
    module = _module()
    transport = ScriptedTransport(
        module,
        [ScriptedItem(200, _connection([{"Id": "asset-a"}], True, "c1"))],
    )
    client = module.CloudGraphQLClient(
        _config(module), transport=transport, sleeper=lambda _: None
    )
    probes = iter((False, True))

    with pytest.raises(ExecutionInterruptedError):
        list(
            client.paginate_pages(
                QUERY,
                "VirtualMachines",
                page_size=20,
                cancellation_probe=lambda: next(probes),
            )
        )

    assert len(transport.requests) == 1

def test_auth_and_contract_errors_are_sanitized() -> None:
    module = _module()
    auth_transport = ScriptedTransport(
        module,
        [ScriptedItem(401, {"message": "token=fixture-secret query { private }"})],
    )
    auth_client = module.CloudGraphQLClient(
        _config(module), transport=auth_transport, sleeper=lambda _: None
    )

    with pytest.raises(module.CloudAuthError) as auth_error:
        auth_client.execute("query { private }", {})
    assert "fixture-secret" not in str(auth_error.value)
    assert "query { private }" not in str(auth_error.value)

    invalid_transport = ScriptedTransport(
        module,
        [ScriptedItem(200, None, raw=b"not-json")],
    )
    invalid_client = module.CloudGraphQLClient(
        _config(module), transport=invalid_transport, sleeper=lambda _: None
    )
    with pytest.raises(module.CloudContractError, match="JSON"):
        invalid_client.execute("query { malformed }", {})
