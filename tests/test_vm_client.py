from __future__ import annotations

import json
import unittest
from collections import deque
from typing import Any, Mapping

from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    ExportJob,
    ExportFailedError,
    ExportTimeoutError,
    TenableVmClient,
    TenableVmConfig,
    TransportResponse,
)


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
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": body, "timeout": timeout}
        )
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response


def response(status: int, data: Any, headers: Mapping[str, str] | None = None) -> TransportResponse:
    return TransportResponse(
        status_code=status,
        headers=headers or {},
        content=json.dumps(data).encode("utf-8"),
    )


def client_with(responses: list[TransportResponse | Exception], sleeps: list[float] | None = None) -> tuple[TenableVmClient, FakeTransport]:
    transport = FakeTransport(responses)
    client = TenableVmClient(
        TenableVmConfig(
            access_key="access-fixture",
            secret_key="secret-fixture",
            poll_seconds=0,
            max_wait_seconds=10,
        ),
        transport=transport,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )
    return client, transport


class TenableVmClientTests(unittest.TestCase):
    def test_chunk_download_can_be_consumed_in_bounded_blocks(self) -> None:
        content = b"a" * (1024 * 1024 + 17)
        client, _ = client_with([
            TransportResponse(status_code=200, headers={}, content=content)
        ])

        blocks = list(client.iter_chunk_bytes("job", 1, block_size=1024 * 1024))

        self.assertEqual([len(block) for block in blocks], [1024 * 1024, 17])
        self.assertEqual(b"".join(blocks), content)
    def test_start_export_disables_plugin_output_by_default(self) -> None:
        client, transport = client_with([response(200, {"export_uuid": "job-1"})])
        export_uuid = client.start_vulnerability_export(filters={"state": ["OPEN"]})
        payload = json.loads(transport.calls[0]["body"])
        self.assertEqual(export_uuid, "job-1")
        self.assertIs(payload["include_plugin_output"], False)
        self.assertNotIn("access-fixture", transport.calls[0]["body"].decode())

    def test_reuses_active_job_from_conflict(self) -> None:
        client, _ = client_with([response(409, {"error": {"active_job_id": "job-existing"}})])
        self.assertEqual(
            client.start_vulnerability_export(filters={"state": ["OPEN"]}),
            "job-existing",
        )

    def test_start_job_distinguishes_created_and_reused_exports(self) -> None:
        created, _ = client_with([response(200, {"export_uuid": "job-new"})])
        reused, _ = client_with([
            response(409, {"error": {"active_job_id": "job-existing"}})
        ])

        self.assertEqual(
            created.start_vulnerability_export_job(filters={"state": ["OPEN"]}),
            ExportJob(export_uuid="job-new", origin="created"),
        )
        self.assertEqual(
            reused.start_vulnerability_export_job(filters={"state": ["OPEN"]}),
            ExportJob(export_uuid="job-existing", origin="reused"),
        )

    def test_cancel_vulnerability_export_uses_exact_vm_job_uuid(self) -> None:
        client, transport = client_with([response(200, {"status": "CANCELLED"})])

        result = client.cancel_vulnerability_export("job-to-cancel")

        self.assertEqual(result["status"], "CANCELLED")
        self.assertEqual(transport.calls[0]["method"], "POST")
        self.assertTrue(
            transport.calls[0]["url"].endswith(
                "/vulns/export/job-to-cancel/cancel"
            )
        )

    def test_start_asset_export_v2_uses_documented_path_and_payload(self) -> None:
        client, transport = client_with([response(200, {"export_uuid": "asset-job-1"})])
        export_uuid = client.start_asset_export_v2(
            filters={"types": ["host"]},
            chunk_size=500,
        )
        payload = json.loads(transport.calls[0]["body"])
        self.assertEqual(export_uuid, "asset-job-1")
        self.assertTrue(transport.calls[0]["url"].endswith("/assets/v2/export"))
        self.assertEqual(payload["chunk_size"], 500)
        self.assertFalse(payload["include_open_ports"])
        self.assertFalse(payload["include_resource_tags"])

    def test_asset_export_rejects_incompatible_options(self) -> None:
        client, _ = client_with([])
        with self.assertRaises(ValueError):
            client.start_asset_export_v2(chunk_size=1001, include_resource_tags=True)
        with self.assertRaises(ValueError):
            client.start_asset_export_v2(
                chunk_size=1000,
                include_resource_tags=True,
                include_open_ports=True,
            )

    def test_lists_tag_values_using_documented_endpoint(self) -> None:
        client, transport = client_with([response(200, {
            "values": [{
                "uuid": "tag-fixture",
                "category_name": "Rede",
                "value": "Matriz",
            }],
            "pagination": {"total": 1},
        })])
        records = client.list_tag_values()
        self.assertEqual(records[0]["uuid"], "tag-fixture")
        self.assertIn("/tags/values?", transport.calls[0]["url"])

    def test_lists_assets_for_tag_with_encoded_workbench_filter(self) -> None:
        client, transport = client_with([response(200, {
            "assets": [{"id": "asset-fixture"}],
            "total": 1,
        })])
        records = client.list_assets_for_tag("Segmento de Rede", "Filial A")
        self.assertEqual(records[0]["id"], "asset-fixture")
        url = transport.calls[0]["url"]
        self.assertIn("/workbenches/assets?", url)
        self.assertIn("filter.0.filter=tag.Segmento+de+Rede", url)
        self.assertIn("filter.0.value=Filial+A", url)

    def test_asset_status_and_chunk_use_v2_export_contract(self) -> None:
        chunk = b'[{"id":"asset-fixture"}]'
        client, transport = client_with([
            response(200, {"status": "FINISHED", "chunks_available": [1]}),
            TransportResponse(status_code=200, headers={}, content=chunk),
        ])
        _, chunks = client.wait_for_asset_completion("asset-job")
        records = client.download_asset_chunk("asset-job", chunks[0])
        self.assertEqual(records[0]["id"], "asset-fixture")
        self.assertTrue(transport.calls[0]["url"].endswith("/assets/export/asset-job/status"))
        self.assertTrue(transport.calls[1]["url"].endswith("/assets/export/asset-job/chunks/1"))

    def test_429_honors_retry_after(self) -> None:
        sleeps: list[float] = []
        client, _ = client_with(
            [response(429, {}, {"retry-after": "7"}), response(200, {"status": "PROCESSING"})],
            sleeps,
        )
        status = client.get_export_status("job")
        self.assertEqual(status["status"], "PROCESSING")
        self.assertEqual(sleeps, [7.0])

    def test_finished_without_chunks_is_valid_empty_export(self) -> None:
        client, _ = client_with([response(200, {"status": "FINISHED", "chunks_available": []})])
        status, chunks = client.wait_for_completion("job")
        self.assertEqual(status["status"], "FINISHED")
        self.assertEqual(chunks, [])

    def test_finished_with_failed_chunks_is_not_treated_as_empty(self) -> None:
        client, _ = client_with(
            [
                response(
                    200,
                    {
                        "status": "FINISHED",
                        "chunks_available": [],
                        "chunks_failed": [0, 1],
                        "total_chunks": 2,
                    },
                )
            ]
        )
        with self.assertRaises(ExportFailedError):
            client.wait_for_completion("job")

    def test_timeout_contains_last_progress_and_notifies_each_poll(self) -> None:
        transport = FakeTransport([
            response(200, {
                "status": "QUEUED",
                "chunks_available": [],
                "total_chunks": 0,
            })
        ])
        times = iter((0.0, 11.0))
        client = TenableVmClient(
            TenableVmConfig(
                access_key="access-fixture",
                secret_key="secret-fixture",
                poll_seconds=0,
                max_wait_seconds=10,
            ),
            transport=transport,
            sleep=lambda _: None,
            monotonic=lambda: next(times),
        )
        progress: list[dict[str, Any]] = []

        with self.assertRaises(ExportTimeoutError) as caught:
            client.wait_for_completion(
                "job-stuck", progress_callback=progress.append
            )

        self.assertEqual(caught.exception.export_uuid, "job-stuck")
        self.assertFalse(caught.exception.progress_made)
        self.assertEqual(caught.exception.timeout_phase, "queue")
        self.assertEqual(caught.exception.last_status["total_chunks"], 0)
        self.assertEqual(progress[0]["export_uuid"], "job-stuck")
        self.assertEqual(progress[0]["completed_chunks"], 0)
        self.assertEqual(progress[0]["total_chunks"], 0)

    def test_processing_is_not_cut_off_by_queue_timeout(self) -> None:
        transport = FakeTransport([
            response(200, {
                "status": "PROCESSING",
                "chunks_available": [8],
                "finished_chunks": 1,
                "total_chunks": 8,
            }),
            response(200, {
                "status": "FINISHED",
                "chunks_available": [8],
                "finished_chunks": 8,
                "total_chunks": 8,
            }),
        ])
        times = iter((0.0, 11.0, 12.0))
        client = TenableVmClient(
            TenableVmConfig(
                access_key="access-fixture",
                secret_key="secret-fixture",
                poll_seconds=0,
                max_wait_seconds=10,
                max_processing_wait_seconds=60,
            ),
            transport=transport,
            sleep=lambda _: None,
            monotonic=lambda: next(times),
        )

        status, chunks = client.wait_for_completion("job-processing")

        self.assertEqual(status["status"], "FINISHED")
        self.assertEqual(chunks, [8])

    def test_processing_timeout_reports_remote_progress_and_stall(self) -> None:
        transport = FakeTransport([
            response(200, {
                "status": "PROCESSING",
                "chunks_available": [],
                "finished_chunks": 1,
                "empty_chunks_count": 1,
                "total_chunks": 8,
            }),
            response(200, {
                "status": "PROCESSING",
                "chunks_available": [],
                "finished_chunks": 1,
                "empty_chunks_count": 1,
                "total_chunks": 8,
            }),
        ])
        times = iter((0.0, 1.0, 22.0))
        client = TenableVmClient(
            TenableVmConfig(
                access_key="access-fixture",
                secret_key="secret-fixture",
                poll_seconds=0,
                max_wait_seconds=10,
                max_processing_wait_seconds=20,
                stall_warning_seconds=5,
            ),
            transport=transport,
            sleep=lambda _: None,
            monotonic=lambda: next(times),
        )

        with self.assertRaises(ExportTimeoutError) as caught:
            client.wait_for_completion("job-processing-stalled")

        self.assertEqual(caught.exception.timeout_phase, "processing")
        self.assertTrue(caught.exception.progress_made)
        self.assertTrue(caught.exception.last_status["stalled"])
        self.assertGreaterEqual(caught.exception.last_status["idle_seconds"], 20)

    def test_processing_polling_backs_off_until_new_progress(self) -> None:
        sleeps: list[float] = []
        transport = FakeTransport([
            response(200, {
                "status": "PROCESSING", "chunks_available": [], "total_chunks": 8,
            }),
            response(200, {
                "status": "PROCESSING", "chunks_available": [], "total_chunks": 8,
            }),
            response(200, {
                "status": "PROCESSING", "chunks_available": [], "total_chunks": 8,
            }),
            response(200, {
                "status": "FINISHED", "chunks_available": [], "total_chunks": 8,
            }),
        ])
        times = iter((0.0, 1.0, 2.0, 3.0, 4.0))
        client = TenableVmClient(
            TenableVmConfig(
                access_key="access-fixture",
                secret_key="secret-fixture",
                poll_seconds=10,
                max_poll_seconds=30,
                max_wait_seconds=10,
                max_processing_wait_seconds=60,
            ),
            transport=transport,
            sleep=sleeps.append,
            monotonic=lambda: next(times),
        )

        client.wait_for_completion("job-adaptive-poll")

        self.assertEqual(sleeps, [10, 20, 30])

    def test_chunk_ids_are_sorted_and_deduplicated(self) -> None:
        client, _ = client_with([response(200, {"status": "FINISHED", "chunks_available": [3, 1, 3, 2]})])
        _, chunks = client.wait_for_completion("job")
        self.assertEqual(chunks, [1, 2, 3])

    def test_wait_notifies_each_available_chunk_only_once_before_finish(self) -> None:
        client, _ = client_with(
            [
                response(
                    200,
                    {
                        "status": "PROCESSING",
                        "chunks_available": [2],
                        "total_chunks": 3,
                    },
                ),
                response(
                    200,
                    {
                        "status": "PROCESSING",
                        "chunks_available": [2, 3],
                        "total_chunks": 3,
                    },
                ),
                response(
                    200,
                    {
                        "status": "FINISHED",
                        "chunks_available": [2, 3],
                        "total_chunks": 2,
                    },
                ),
            ]
        )
        received: list[int] = []

        _, chunks = client.wait_for_completion(
            "job", chunk_callback=received.append
        )

        self.assertEqual(received, [2, 3])
        self.assertEqual(chunks, [2, 3])

    def test_error_message_never_contains_credentials_or_response_body(self) -> None:
        client, _ = client_with([response(403, {"debug": "secret-fixture access-fixture"})])
        with self.assertRaises(ApiError) as caught:
            client.get_export_status("job")
        message = str(caught.exception)
        self.assertNotIn("secret-fixture", message)
        self.assertNotIn("access-fixture", message)


if __name__ == "__main__":
    unittest.main()
