from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Mapping

import pytest

from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    TenableVmConfig,
    TransportResponse,
)
from tenable_reports.infrastructure.tenable_was.client import TenableWasClient
from tenable_reports.infrastructure.tenable_was.owasp_analysis import (
    TenableWasOwaspAnalysisClient,
)


class FakeTransport:
    def __init__(self, responses: list[TransportResponse]) -> None:
        self.responses = deque(responses)
        self.calls: list[dict[str, Any]] = []

    def send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> TransportResponse:
        self.calls.append({"method": method, "url": url, "body": body})
        return self.responses.popleft()


def response(status: int, data: Any) -> TransportResponse:
    return TransportResponse(status, {}, json.dumps(data).encode("utf-8"))


def analysis_client(
    responses: list[TransportResponse],
) -> tuple[TenableWasOwaspAnalysisClient, FakeTransport]:
    transport = FakeTransport(responses)
    was_client = TenableWasClient(
        TenableVmConfig(
            access_key="access-fixture",
            secret_key="secret-fixture",
            max_attempts=2,
        ),
        transport=transport,
        sleep=lambda _: None,
    )
    return TenableWasOwaspAnalysisClient(was_client), transport


def group(
    plugin_id: int,
    *,
    severity: str = "LOW",
    vpr: float | None = 3.9,
    instances: int = 1,
) -> dict[str, Any]:
    severity_value = {
        "INFO": 0,
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
        "CRITICAL": 4,
    }[severity]
    return {
        "field": "definition.id",
        "value": plugin_id,
        "stats": {
            "max(severity)": severity_value,
            "first(definition.name)": f"Plugin {plugin_id}",
            "first(definition.family)": "Família WEB",
            "first(definition.vpr.score)": vpr,
            "first(definition.id)": plugin_id,
            "count": instances,
        },
    }


START = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)
END = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)


def test_exclusive_analysis_sends_the_approved_a01_contract() -> None:
    client, transport = analysis_client([
        response(200, {"groups": [group(115540)]})
    ])

    rows = client.list_category_plugins(
        category="A01",
        start_at=START,
        end_at=END,
        page_size=200,
    )

    assert rows[0].category == "A01"
    assert transport.calls[0]["method"] == "POST"
    assert transport.calls[0]["url"].endswith(
        "/api/v3/findings/vulnerabilities/webapp/analysis"
    )
    assert json.loads(transport.calls[0]["body"]) == {
        "filter": {
            "and": [
                {"property": "state", "operator": "neq", "value": ["FIXED"]},
                {"property": "severity", "operator": "neq", "value": [0]},
                {
                    "property": "risk_modified",
                    "operator": "neq",
                    "value": ["ACCEPTED"],
                },
                {"property": "owasp_2025", "operator": "eq", "value": ["A1"]},
                {
                    "property": "last_seen",
                    "operator": "between",
                    "operatorVariation": "between",
                    "value": [
                        "2026-10-01T03:00:00.000Z",
                        "2026-11-01T02:59:59.999Z",
                    ],
                },
            ]
        },
        "template": "group",
        "parameters": {
            "group_by": "definition.id",
            "stats": [
                "max(severity)",
                "first(definition.name)",
                "first(definition.family)",
                "first(definition.vpr.score)",
                "first(definition.id)",
                "count",
            ],
            "sort": [
                {"property": "first(definition.vpr.score)", "order": "desc"}
            ],
        },
    }


def test_exclusive_analysis_paginates_and_sorts_vpr_descending() -> None:
    client, transport = analysis_client([
        response(200, {
            "groups": [group(101, severity="MEDIUM", vpr=5.2, instances=20)],
            "next": "cursor-2",
        }),
        response(200, {
            "groups": [
                group(102, severity="HIGH", vpr=8.7, instances=3),
                group(103, severity="CRITICAL", vpr=None, instances=30),
            ],
        }),
    ])

    rows = client.list_category_plugins(
        category="A10",
        start_at=START,
        end_at=END,
        page_size=2,
    )

    assert [row.plugin_id for row in rows] == [102, 101, 103]
    assert rows[-1].vpr_score is None
    assert json.loads(transport.calls[1]["body"])["next"] == "cursor-2"


def test_exclusive_analysis_normalizes_vpr_to_tenable_precision() -> None:
    client, _ = analysis_client([
        response(200, {"groups": [group(101, vpr=3.9000000953674316)]})
    ])

    rows = client.list_category_plugins(
        category="A01",
        start_at=START,
        end_at=END,
    )

    assert rows[0].vpr_score == 3.9


def test_exclusive_analysis_retries_transient_http_response() -> None:
    client, transport = analysis_client([
        response(502, {"message": "temporário"}),
        response(200, {"groups": []}),
    ])

    assert client.list_category_plugins(
        category="A02",
        start_at=START,
        end_at=END,
    ) == ()
    assert len(transport.calls) == 2


@pytest.mark.parametrize(
    "payload",
    (
        {"next": None},
        {"groups": [group(101, instances=-1)]},
        {"groups": [{"field": "definition.id", "stats": {}}]},
    ),
)
def test_exclusive_analysis_rejects_malformed_responses(payload: Any) -> None:
    client, _ = analysis_client([response(200, payload)])

    with pytest.raises(ApiError, match="analise OWASP 2025"):
        client.list_category_plugins(
            category="A03",
            start_at=START,
            end_at=END,
        )


def test_exclusive_analysis_rejects_invalid_category_and_period() -> None:
    client, _ = analysis_client([])

    with pytest.raises(ValueError, match="A01"):
        client.list_category_plugins(category="A1", start_at=START, end_at=END)
    with pytest.raises(ValueError, match="periodo"):
        client.list_category_plugins(category="A01", start_at=END, end_at=START)


def test_exclusive_analysis_enforces_aggregate_pagination_budget() -> None:
    client, transport = analysis_client([
        response(200, {
            "groups": [group(101)],
            "next": "cursor-2",
        }),
    ])

    with pytest.raises(ApiError, match="limite de paginacao"):
        client.list_category_plugins(
            category="A01",
            start_at=START,
            end_at=END,
            max_pages=1,
        )

    assert len(transport.calls) == 1
