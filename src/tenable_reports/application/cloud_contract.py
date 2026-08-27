"""Cloud GraphQL endpoint and capability probing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from tenable_reports.infrastructure.tenable_cloud.client import (
    CloudContractError,
    CloudGraphQLError,
)
from tenable_reports.infrastructure.tenable_cloud.queries import (
    CLOUD_CONNECTOR_VERSION,
    CLOUD_SOURCE_QUERIES,
    CloudQueryDefinition,
    cloud_endpoint_candidates,
)


class CloudProbeClient(Protocol):
    def execute(
        self,
        query: str,
        variables: Mapping[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CloudSourceCapability:
    """Availability of one independently collected GraphQL source."""

    name: str
    root_field: str
    required: bool
    status: str
    query_version: str
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CloudCapabilityReport:
    """Sanitized readiness report for one compatible endpoint."""

    endpoint: str
    checked_at: str
    connector_version: str
    sources: tuple[CloudSourceCapability, ...]

    @property
    def required_ready(self) -> bool:
        required = tuple(item for item in self.sources if item.required)
        return bool(required) and all(item.status == "AVAILABLE" for item in required)

    def source(self, name: str) -> CloudSourceCapability:
        for item in self.sources:
            if item.name == name:
                return item
        raise KeyError(name)


def _validate_probe_response(
    data: Mapping[str, Any],
    definition: CloudQueryDefinition,
) -> None:
    connection = data.get(definition.root_field)
    if not isinstance(connection, Mapping):
        raise CloudContractError(
            "A resposta GraphQL nao contem a conexao solicitada.",
            root_field=definition.root_field,
        )
    nodes = connection.get("nodes")
    page_info = connection.get("pageInfo")
    if not isinstance(nodes, list) or any(
        not isinstance(item, Mapping) for item in nodes
    ):
        raise CloudContractError(
            "A conexao GraphQL retornou nodes invalidos.",
            root_field=definition.root_field,
        )
    if not isinstance(page_info, Mapping) or not isinstance(
        page_info.get("hasNextPage"), bool
    ):
        raise CloudContractError(
            "A conexao GraphQL retornou pageInfo invalido.",
            root_field=definition.root_field,
        )


def _probe_source(
    client: CloudProbeClient,
    definition: CloudQueryDefinition,
) -> CloudSourceCapability:
    data = client.execute(
        definition.query,
        {"first": 1, "after": None},
    )
    _validate_probe_response(data, definition)
    return CloudSourceCapability(
        name=definition.name,
        root_field=definition.root_field,
        required=definition.required,
        status="AVAILABLE",
        query_version=definition.version,
    )


def _timestamp(now: Callable[[], datetime]) -> str:
    checked_at = now()
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    return checked_at.astimezone(timezone.utc).isoformat()


def probe_cloud_contract(
    environment: str,
    *,
    client_factory: Callable[[str], CloudProbeClient],
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> CloudCapabilityReport:
    """Find a compatible endpoint and report required/optional capabilities.

    Contract rejection on a required source tries the next supported endpoint.
    Authentication, rate-limit and temporary failures remain visible to the
    caller because changing the route cannot safely resolve those conditions.
    """

    last_contract_error: CloudContractError | None = None
    definitions = tuple(CLOUD_SOURCE_QUERIES.values())
    required = tuple(item for item in definitions if item.required)
    optional = tuple(item for item in definitions if not item.required)

    for endpoint in cloud_endpoint_candidates(environment):
        client = client_factory(endpoint)
        capabilities: list[CloudSourceCapability] = []
        try:
            for definition in required:
                capabilities.append(_probe_source(client, definition))
        except CloudContractError as exc:
            last_contract_error = exc
            continue

        for definition in optional:
            try:
                capabilities.append(_probe_source(client, definition))
            except CloudGraphQLError as exc:
                capabilities.append(
                    CloudSourceCapability(
                        name=definition.name,
                        root_field=definition.root_field,
                        required=False,
                        status="UNAVAILABLE",
                        query_version=definition.version,
                        message=str(exc),
                    )
                )

        return CloudCapabilityReport(
            endpoint=endpoint,
            checked_at=_timestamp(now),
            connector_version=CLOUD_CONNECTOR_VERSION,
            sources=tuple(capabilities),
        )

    if last_contract_error is not None:
        raise last_contract_error
    raise CloudContractError(
        "Nenhum endpoint GraphQL Cloud compativel foi encontrado."
    )


__all__ = [
    "CloudCapabilityReport",
    "CloudProbeClient",
    "CloudSourceCapability",
    "probe_cloud_contract",
]
