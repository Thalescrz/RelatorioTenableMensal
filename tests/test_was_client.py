from __future__ import annotations

import json
import unittest
from collections import deque
from typing import Any, Mapping

from tenable_reports.infrastructure.tenable_vm.client import TenableVmConfig, TransportResponse
from tenable_reports.infrastructure.tenable_was.client import TenableWasClient


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
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.responses.popleft()


def response(status: int, data: Any) -> TransportResponse:
    return TransportResponse(status, {}, json.dumps(data).encode("utf-8"))


def client_with(responses: list[TransportResponse]) -> tuple[TenableWasClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = TenableWasClient(
        TenableVmConfig(
            access_key="access-fixture",
            secret_key="secret-fixture",
            poll_seconds=0,
            max_wait_seconds=10,
        ),
        transport=transport,
        sleep=lambda _: None,
    )
    return client, transport


class TenableWasClientTests(unittest.TestCase):
    def test_starts_dedicated_was_export_with_explicit_filters(self) -> None:
        client, transport = client_with([response(200, {"export_uuid": "was-job"})])
        export_uuid = client.start_findings_export(
            filters={
                "since": 1782860400,
                "state": ["OPEN", "REOPENED", "FIXED"],
                "severity": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
            },
            num_assets=50,
        )
        payload = json.loads(transport.calls[0]["body"])
        self.assertEqual(export_uuid, "was-job")
        self.assertTrue(transport.calls[0]["url"].endswith("/was/v1/export/vulns"))
        self.assertEqual(payload["filters"]["since"], 1782860400)
        self.assertFalse(payload["include_unlicensed"])

    def test_status_and_chunk_use_was_paths(self) -> None:
        chunk = TransportResponse(200, {}, b'[{"finding_id":"finding-1"}]')
        client, transport = client_with([
            response(200, {"status": "FINISHED", "chunks_available": [2]}),
            chunk,
        ])
        _, chunks = client.wait_for_findings_completion("was-job")
        records = client.download_findings_chunk("was-job", chunks[0])
        self.assertEqual(records[0]["finding_id"], "finding-1")
        self.assertTrue(transport.calls[0]["url"].endswith("/was/v1/export/vulns/was-job/status"))
        self.assertTrue(transport.calls[1]["url"].endswith("/was/v1/export/vulns/was-job/chunks/2"))

    def test_lists_v2_filter_metadata_without_exposing_findings(self) -> None:
        client, _ = client_with([response(200, {"filters": [{"field": "severity"}]})])
        self.assertEqual(client.list_vulnerability_filters()[0]["field"], "severity")


if __name__ == "__main__":
    unittest.main()
