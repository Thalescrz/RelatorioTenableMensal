from __future__ import annotations

import importlib
import json
import unittest
from collections import deque
from pathlib import Path
from typing import Any, Mapping

from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    CredentialError,
    TenableVmConfig,
    TransportResponse,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "tenable_inventory"


class FakeTransport:
    def __init__(self, responses: list[TransportResponse | Exception]) -> None:
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
        self.calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "timeout": timeout,
        })
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def response(
    status: int,
    data: Any,
    headers: Mapping[str, str] | None = None,
) -> TransportResponse:
    return TransportResponse(
        status_code=status,
        headers=headers or {},
        content=json.dumps(data).encode("utf-8"),
    )


class InventoryFindingsClientTests(unittest.TestCase):
    def inventory(self):
        try:
            return importlib.import_module(
                "tenable_reports.infrastructure.tenable_inventory.client"
            )
        except ModuleNotFoundError:
            self.fail("cliente da Inventory Findings API ainda nao foi implementado")

    def client_with(
        self,
        responses: list[TransportResponse | Exception],
        sleeps: list[float] | None = None,
    ):
        inventory = self.inventory()
        transport = FakeTransport(responses)
        client = inventory.InventoryFindingsClient(
            TenableVmConfig(
                access_key="access-fixture",
                secret_key="secret-fixture",
                max_attempts=2,
            ),
            transport=transport,
            sleep=(sleeps.append if sleeps is not None else lambda _: None),
        )
        return inventory, client, transport

    def test_lists_supported_finding_properties(self) -> None:
        payload = json.loads((FIXTURES / "properties.json").read_text(encoding="utf-8"))
        _, client, transport = self.client_with([response(200, payload)])

        properties = client.list_properties()

        self.assertEqual(properties[0]["name"], "last_observed_at")
        self.assertTrue(
            transport.calls[0]["url"].endswith(
                "/api/v1/t1/inventory/findings/properties"
            )
        )

    def test_search_page_uses_official_query_parameters_and_bounded_filter(self) -> None:
        _, client, transport = self.client_with([
            response(200, {"findings": [], "pagination": {"total": 0}})
        ])
        filters = [{
            "field": "last_observed_at",
            "operator": "between",
            "value": ["2026-07-01", "2026-07-31"],
        }]

        page = client.search_page(
            filters=filters,
            extra_properties=("asset_name", "solution", "vpr_score"),
            offset=20,
            limit=500,
            sort="severity:desc",
        )

        self.assertEqual(page.total, 0)
        call = transport.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertIn("/api/v1/t1/inventory/findings/search?", call["url"])
        self.assertIn("offset=20", call["url"])
        self.assertIn("limit=500", call["url"])
        self.assertIn("sort=severity%3Adesc", call["url"])
        self.assertIn(
            "extra_properties=asset_name%2Csolution%2Cvpr_score",
            call["url"],
        )
        self.assertEqual(json.loads(call["body"]), {"filters": filters})

    def test_iter_findings_paginates_until_reported_total(self) -> None:
        first = json.loads(
            (FIXTURES / "findings_page_1.json").read_text(encoding="utf-8")
        )
        second = {
            "findings": [{"id": "finding-c", "state": "ACTIVE"}],
            "pagination": {"offset": 2, "limit": 2, "total": 3},
        }
        _, client, transport = self.client_with([
            response(200, first),
            response(200, second),
        ])

        findings = list(client.iter_findings(filters=[], limit=2))

        self.assertEqual(
            [finding["id"] for finding in findings],
            ["finding-a", "finding-b", "finding-c"],
        )
        self.assertIn("offset=0", transport.calls[0]["url"])
        self.assertIn("offset=2", transport.calls[1]["url"])

    def test_rejects_page_size_above_official_limit(self) -> None:
        _, client, _ = self.client_with([])

        with self.assertRaisesRegex(ValueError, "10000"):
            client.search_page(filters=[], limit=10001)

    def test_429_honors_retry_after_through_shared_transport_policy(self) -> None:
        sleeps: list[float] = []
        _, client, _ = self.client_with(
            [
                response(429, {}, {"retry-after": "7"}),
                response(200, {"findings": [], "pagination": {"total": 0}}),
            ],
            sleeps,
        )

        client.search_page(filters=[])

        self.assertEqual(sleeps, [7.0])

    def test_surfaces_authentication_and_permission_failures(self) -> None:
        for status, error_type in ((401, CredentialError), (403, ApiError)):
            with self.subTest(status=status):
                _, client, _ = self.client_with([response(status, {})])
                with self.assertRaises(error_type) as caught:
                    client.search_page(filters=[])
                self.assertEqual(caught.exception.status_code, status)

    def test_rejects_response_without_findings_collection(self) -> None:
        _, client, _ = self.client_with([response(200, {"pagination": {"total": 1}})])

        with self.assertRaisesRegex(ApiError, "findings"):
            client.search_page(filters=[])


if __name__ == "__main__":
    unittest.main()
