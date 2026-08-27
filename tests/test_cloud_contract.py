from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import Any

import pytest


def _contract_module():
    return importlib.import_module("tenable_reports.application.cloud_contract")


def _queries_module():
    return importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.queries"
    )


def _connection(root_field: str) -> dict[str, Any]:
    return {
        root_field: {
            "nodes": [],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    }


class EndpointClient:
    def __init__(self, cloud: Any, *, fail_root: str | None = None) -> None:
        self.cloud = cloud
        self.fail_root = fail_root
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        queries = _queries_module()
        definition = next(
            item for item in queries.CLOUD_SOURCE_QUERIES.values() if item.query == query
        )
        self.calls.append((definition.name, variables))
        if definition.root_field == self.fail_root:
            raise self.cloud.CloudContractError(
                "Fonte opcional indisponivel.", root_field=definition.root_field
            )
        return _connection(definition.root_field)


def test_endpoint_candidates_prefer_documented_route_then_legacy_route() -> None:
    queries = _queries_module()

    assert queries.cloud_endpoint_candidates("global") == (
        "https://app.tenable.com/graphql",
        "https://app.tenable.com/api/graph",
    )
    assert queries.cloud_endpoint_candidates("us_gov") == (
        "https://app.tenable.us/graphql",
        "https://app.tenable.us/api/graph",
    )


def test_probe_falls_back_to_legacy_endpoint_after_contract_rejection() -> None:
    contract = _contract_module()
    cloud = importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.client"
    )
    clients: dict[str, Any] = {}

    class RejectedEndpoint:
        def execute(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
            raise cloud.CloudContractError("Rota GraphQL rejeitada.")

    def factory(endpoint: str) -> Any:
        client = (
            RejectedEndpoint()
            if endpoint.endswith("/graphql")
            else EndpointClient(cloud)
        )
        clients[endpoint] = client
        return client

    report = contract.probe_cloud_contract(
        "global",
        client_factory=factory,
        now=lambda: datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert report.endpoint == "https://app.tenable.com/api/graph"
    assert report.required_ready is True
    assert report.checked_at == "2026-08-26T12:00:00+00:00"
    assert report.source("virtual_machines").status == "AVAILABLE"
    legacy = clients["https://app.tenable.com/api/graph"]
    assert legacy.calls[0][1] == {"first": 1, "after": None}


def test_optional_source_failure_does_not_hide_required_readiness() -> None:
    contract = _contract_module()
    cloud = importlib.import_module(
        "tenable_reports.infrastructure.tenable_cloud.client"
    )

    report = contract.probe_cloud_contract(
        "global",
        client_factory=lambda _: EndpointClient(cloud, fail_root="Findings"),
        now=lambda: datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc),
    )

    assert report.required_ready is True
    assert report.source("findings").status == "UNAVAILABLE"
    assert report.source("virtual_machines").status == "AVAILABLE"
    assert report.source("container_images").status == "AVAILABLE"


def test_required_queries_stay_light_and_enrichment_is_separate() -> None:
    queries = _queries_module()
    virtual_machines = queries.CLOUD_SOURCE_QUERIES["virtual_machines"]
    details = queries.CLOUD_SOURCE_QUERIES["vulnerability_details"]

    assert virtual_machines.required is True
    assert "VprScore" in virtual_machines.query
    assert "Description" not in virtual_machines.query
    assert "Description" in details.query
    assert virtual_machines.page_size <= 100
    assert details.page_size <= virtual_machines.page_size


def test_unknown_cloud_environment_is_rejected_before_network_use() -> None:
    queries = _queries_module()

    with pytest.raises(ValueError, match="environment"):
        queries.cloud_endpoint_candidates("private")
