from __future__ import annotations

import json
import gzip
import hashlib
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from typing import Any

from tenable_reports.application.collect import (
    AssetExportRequest,
    VulnerabilityExportRequest,
    collect_asset_snapshot,
    collect_vm_snapshot,
    collect_vm_snapshot_by_state,
    find_resumable_vm_manifest,
    reusable_chunk,
    store_chunk_atomic,
    _write_json_replace,
)
from tenable_reports.application.collect_was import (
    WasExportRequest,
    collect_optional_was_snapshot,
    collect_was_snapshot,
)
from tenable_reports.application.normalize import _collection_records
from tenable_reports.config.profile import load_client_profile
from tenable_reports.domain.execution_control import ExecutionInterruptedError
from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    ExportJob,
    ExportTimeoutError,
)
from tenable_reports.infrastructure.tenable_vm.parser import iter_chunk_records


ROOT = Path(__file__).resolve().parents[1]


class FakeCollectionClient:
    def __init__(self, chunks: dict[int, bytes]) -> None:
        self.chunks = chunks
        self.start_arguments: dict[str, Any] = {}
        self.download_calls: list[int] = []

    def start_vulnerability_export(self, **kwargs: Any) -> str:
        self.start_arguments = kwargs
        return "fixture-export"

    def wait_for_completion(self, export_uuid: str) -> tuple[dict[str, Any], list[int]]:
        return {"status": "FINISHED"}, sorted(self.chunks)

    def download_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        self.download_calls.append(chunk_id)
        return self.chunks[chunk_id]


def _sharing_violation() -> PermissionError:
    error = PermissionError(13, "Access is denied")
    error.winerror = 5
    return error


def test_json_replace_retries_windows_sharing_violation(tmp_path: Path) -> None:
    target = tmp_path / "export-state.json"
    target.write_text('{"status":"OLD"}', encoding="utf-8")
    real_replace = __import__("os").replace
    calls = 0

    def flaky_replace(source, destination):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise _sharing_violation()
        return real_replace(source, destination)

    with patch("tenable_reports.application.collect.os.replace", side_effect=flaky_replace), patch(
        "tenable_reports.application.collect.time.sleep"
    ) as sleeper:
        _write_json_replace(target, {"status": "PROCESSING"})

    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "PROCESSING"
    assert calls == 3
    assert sleeper.call_count == 2
    assert not tuple(tmp_path.glob("*.tmp"))


def test_optional_was_isolates_persistent_export_state_sharing_error(tmp_path: Path) -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    from tenable_reports.application.collect import _write_json_replace as real_write

    def fail_state(path: Path, payload: dict[str, Any]) -> None:
        if path.name == "export-state.json":
            raise _sharing_violation()
        real_write(path, payload)

    with patch(
        "tenable_reports.application.collect_was._write_json_replace",
        side_effect=fail_state,
    ):
        attempt = collect_optional_was_snapshot(
            client=FakeWasCollectionClient({}),  # type: ignore[arg-type]
            profile=profile,
            request=WasExportRequest(filters={"state": ["OPEN"]}),
            output_root=tmp_path,
            run_id="run-was-sharing",
        )

    assert attempt.status == "UNAVAILABLE"
    assert attempt.failure is not None
    assert attempt.failure.code == "WAS_LOCAL_STATE_TRANSIENT"
    assert attempt.failure.retryable is True


class FakeAssetCollectionClient:
    def __init__(self, chunks: dict[int, bytes]) -> None:
        self.chunks = chunks
        self.start_arguments: dict[str, Any] = {}

    def start_asset_export_v2(self, **kwargs: Any) -> str:
        self.start_arguments = kwargs
        return "fixture-asset-export"

    def wait_for_asset_completion(self, export_uuid: str) -> tuple[dict[str, Any], list[int]]:
        return {"status": "FINISHED"}, sorted(self.chunks)

    def download_asset_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        return self.chunks[chunk_id]


class SegmentedCollectionClient:
    def __init__(self) -> None:
        self.started_filters: list[dict[str, Any]] = []

    def start_vulnerability_export_job(self, **kwargs: Any) -> ExportJob:
        filters = dict(kwargs["filters"])
        self.started_filters.append(filters)
        suffix = "fixed" if filters["state"] == ["FIXED"] else "active"
        return ExportJob(export_uuid=f"job-{suffix}", origin="created")

    def wait_for_completion(self, export_uuid: str, *, progress_callback=None):
        if progress_callback is not None:
            progress_callback({
                "export_uuid": export_uuid,
                "status": "FINISHED",
                "completed_chunks": 1,
                "total_chunks": 1,
                "progress_made": True,
            })
        return {"status": "FINISHED"}, [1]

    def download_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        state = "FIXED" if export_uuid.endswith("fixed") else "OPEN"
        return (json.dumps({"id": export_uuid, "state": state}) + "\n").encode()


class TimedOutCollectionClient:
    def __init__(self, *, origin: str) -> None:
        self.origin = origin
        self.cancelled: list[str] = []

    def start_vulnerability_export_job(self, **kwargs: Any) -> ExportJob:
        return ExportJob(export_uuid="fixture-stuck-export", origin=self.origin)

    def wait_for_completion(self, export_uuid: str, *, progress_callback=None):
        status = {
            "export_uuid": export_uuid,
            "status": "PROCESSING",
            "completed_chunks": 0,
            "total_chunks": 1,
            "progress_made": False,
        }
        if progress_callback is not None:
            progress_callback(status)
        raise ExportTimeoutError(
            "Tempo maximo excedido aguardando o export VM.",
            export_uuid=export_uuid,
            last_status=status,
            progress_made=False,
        )

    def cancel_vulnerability_export(self, export_uuid: str) -> dict[str, Any]:
        self.cancelled.append(export_uuid)
        return {"status": "CANCELLED"}


class IncrementalTimeoutCollectionClient(TimedOutCollectionClient):
    def __init__(self, *, origin: str = "created", timeout_phase: str | None = None) -> None:
        super().__init__(origin=origin)
        self.download_calls: list[int] = []
        self.timeout_phase = timeout_phase

    def wait_for_completion(
        self,
        export_uuid: str,
        *,
        progress_callback=None,
        chunk_callback=None,
    ):
        status = {
            "export_uuid": export_uuid,
            "status": "PROCESSING",
            "chunks_available": [2],
            "completed_chunks": 1,
            "total_chunks": 2,
            "progress_made": True,
        }
        if chunk_callback is not None:
            chunk_callback(2)
        if progress_callback is not None:
            progress_callback(status)
        raise ExportTimeoutError(
            "Tempo maximo excedido aguardando o export VM.",
            export_uuid=export_uuid,
            last_status=status,
            progress_made=True,
            timeout_phase=self.timeout_phase,
        )

    def download_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        self.download_calls.append(chunk_id)
        return b'{"id":"finding-partial","state":"OPEN"}\n'


class ProgressWithoutChunkTimeoutCollectionClient(TimedOutCollectionClient):
    def __init__(self) -> None:
        super().__init__(origin="created")

    def wait_for_completion(self, export_uuid: str, *, progress_callback=None):
        status = {
            "export_uuid": export_uuid,
            "status": "PROCESSING",
            "chunks_available": [],
            "completed_chunks": 0,
            "total_chunks": 1,
            "progress_made": True,
        }
        if progress_callback is not None:
            progress_callback(status)
        raise ExportTimeoutError(
            "Tempo maximo excedido durante o processamento do export VM.",
            export_uuid=export_uuid,
            last_status=status,
            progress_made=True,
            timeout_phase="processing",
        )


class StatusAwareResumeClient(FakeCollectionClient):
    def __init__(self, status: dict[str, Any] | ApiError) -> None:
        super().__init__({1: b'{"id":"finding-recovered","state":"OPEN"}\n'})
        self.status = status
        self.status_calls: list[str] = []

    def get_export_status(self, export_uuid: str) -> dict[str, Any]:
        self.status_calls.append(export_uuid)
        if isinstance(self.status, ApiError):
            raise self.status
        return dict(self.status)


class BudgetAwareStatusClient(StatusAwareResumeClient):
    def __init__(self, status: dict[str, Any] | ApiError) -> None:
        super().__init__(status)
        self.wait_budget: float | None = None

    def wait_for_completion(
        self,
        export_uuid: str,
        *,
        max_total_wait_seconds: float | None = None,
    ) -> tuple[dict[str, Any], list[int]]:
        self.wait_budget = max_total_wait_seconds
        return super().wait_for_completion(export_uuid)


class FakeWasCollectionClient:
    def __init__(self, chunks: dict[int, bytes]) -> None:
        self.chunks = chunks
        self.start_arguments: dict[str, Any] = {}

    def start_findings_export_job(self, **kwargs: Any) -> ExportJob:
        self.start_arguments = kwargs
        return ExportJob("fixture-was-export", "created")

    def start_findings_export(self, **kwargs: Any) -> str:
        self.start_arguments = kwargs
        return "fixture-was-export"

    def wait_for_findings_completion(
        self,
        export_uuid: str,
        progress_callback=None,
        chunk_callback=None,
    ) -> tuple[dict[str, Any], list[int]]:
        chunk_ids = sorted(self.chunks)
        for chunk_id in chunk_ids:
            if chunk_callback is not None:
                chunk_callback(chunk_id)
        status = {
            "status": "FINISHED",
            "completed_chunks": len(chunk_ids),
            "total_chunks": len(chunk_ids),
        }
        if progress_callback is not None:
            progress_callback(status)
        return status, chunk_ids

    def download_findings_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        return self.chunks[chunk_id]


class UnavailableWasCollectionClient(FakeWasCollectionClient):
    def start_findings_export_job(self, **kwargs: Any) -> ExportJob:
        raise ApiError("WAS indisponivel.", status_code=403)

    def start_findings_export(self, **kwargs: Any) -> str:
        raise ApiError("WAS indisponivel.", status_code=403)


class TimedOutWasCollectionClient(FakeWasCollectionClient):
    def wait_for_findings_completion(
        self,
        export_uuid: str,
        progress_callback=None,
        chunk_callback=None,
    ) -> tuple[dict[str, Any], list[int]]:
        status = {
            "status": "PROCESSING",
            "completed_chunks": 0,
            "total_chunks": 1,
            "progress_made": False,
        }
        if progress_callback is not None:
            progress_callback(status)
        raise ExportTimeoutError(
            "Tempo maximo excedido aguardando o export WAS.",
            export_uuid=export_uuid,
            last_status=status,
            progress_made=False,
        )


class IncrementalTimeoutWasCollectionClient(FakeWasCollectionClient):
    def __init__(self) -> None:
        super().__init__({1: b'{"finding_id":"was-partial"}\n'})
        self.download_calls: list[int] = []

    def wait_for_findings_completion(
        self,
        export_uuid: str,
        progress_callback=None,
        chunk_callback=None,
    ) -> tuple[dict[str, Any], list[int]]:
        if chunk_callback is not None:
            chunk_callback(1)
        status = {
            "status": "PROCESSING",
            "completed_chunks": 1,
            "total_chunks": 2,
            "progress_made": True,
        }
        if progress_callback is not None:
            progress_callback(status)
        raise ExportTimeoutError(
            "Tempo maximo excedido aguardando o export WAS.",
            export_uuid=export_uuid,
            last_status=status,
            progress_made=True,
        )

    def download_findings_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        self.download_calls.append(chunk_id)
        return self.chunks[chunk_id]


class RecordingWasCollectionClient(FakeWasCollectionClient):
    def __init__(self) -> None:
        super().__init__({
            1: b'{"finding_id":"was-partial"}\n',
            2: b'{"finding_id":"was-final"}\n',
        })
        self.download_calls: list[int] = []

    def download_findings_chunk_bytes(self, export_uuid: str, chunk_id: int) -> bytes:
        self.download_calls.append(chunk_id)
        return self.chunks[chunk_id]


class InterruptedCollectionClient(FakeCollectionClient):
    def wait_for_completion(
        self,
        export_uuid: str,
        *,
        progress_callback=None,
        chunk_callback=None,
        cancellation_probe=None,
    ):
        if cancellation_probe is None:
            raise AssertionError("cancellation_probe nao foi propagado")
        if chunk_callback is not None:
            chunk_callback(1)
        raise ExecutionInterruptedError(
            "Execucao interrompida com export preservado.",
            export_uuid=export_uuid,
        )

class CollectionTests(unittest.TestCase):
    def test_vm_interruption_keeps_partial_manifest_and_downloaded_chunk(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = InterruptedCollectionClient(
            {1: b'{"id":"finding-partial","state":"OPEN"}\n'}
        )
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExecutionInterruptedError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-interrupted",
                    cancellation_probe=lambda: False,
                )

            partial = Path(str(caught.exception.checkpoint))
            payload = json.loads(partial.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "PROCESSING")
            self.assertEqual(payload["export_uuid"], "fixture-export")
            self.assertEqual(len(payload["chunks"]), 1)
            self.assertFalse((partial.parent / "manifest.json").exists())
            self.assertEqual(client.download_calls, [1])
    def test_was_retry_reuses_chunk_from_partial_manifest(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        request = WasExportRequest(filters={"state": ["OPEN", "FIXED"]})
        first = IncrementalTimeoutWasCollectionClient()
        second = RecordingWasCollectionClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError):
                collect_was_snapshot(
                    client=first,  # type: ignore[arg-type]
                    profile=profile,
                    request=request,
                    output_root=directory,
                    run_id="run-was-resume",
                )
            result = collect_was_snapshot(
                client=second,  # type: ignore[arg-type]
                profile=profile,
                request=request,
                output_root=directory,
                run_id="run-was-resume",
                export_uuid="fixture-was-export",
            )
            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(first.download_calls, [1])
        self.assertEqual(second.download_calls, [2])
        self.assertEqual([item["chunk_id"] for item in manifest["chunks"]], [1, 2])
    def test_vm_states_use_one_combined_export_by_default(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = SegmentedCollectionClient()
        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot_by_state(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={
                    "since": 1782860400,
                    "state": ["OPEN", "REOPENED", "FIXED"],
                    "severity": ["low", "medium", "high", "critical"],
                }),
                output_root=directory,
                run_id="run-combined",
            )
            manifest = json.loads(
                result.raw_manifest_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            [filters["state"] for filters in client.started_filters],
            [["OPEN", "REOPENED", "FIXED"]],
        )
        self.assertEqual(manifest["strategy"], "combined")
        self.assertNotIn("segments", manifest)

    def test_vm_state_wrapper_propagates_snapshot_suffix(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = SegmentedCollectionClient()
        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot_by_state(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-selective",
                snapshot_suffix="selective",
            )

        self.assertEqual(
            result.snapshot_path.name,
            "tenable_vm_vulnerabilities-selective.snapshot.json",
        )
    def test_vm_states_are_exported_separately_and_merged_into_one_snapshot(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = SegmentedCollectionClient()
        progress: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot_by_state(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={
                    "since": 1782860400,
                    "state": ["OPEN", "REOPENED", "FIXED"],
                    "severity": ["low", "medium", "high", "critical"],
                }),
                output_root=directory,
                run_id="run-segmented",
                progress_callback=progress.append,
                strategy="split",
            )
            records = list(_collection_records(result))
            manifest = json.loads(
                result.raw_manifest_path.read_text(encoding="utf-8")
            )

        self.assertEqual(
            [filters["state"] for filters in client.started_filters],
            [["OPEN", "REOPENED"], ["FIXED"]],
        )
        self.assertEqual(result.snapshot.record_count, 2)
        self.assertEqual({record["state"] for record in records}, {"OPEN", "FIXED"})
        self.assertEqual(
            result.snapshot_path.name,
            "tenable_vm_vulnerabilities.snapshot.json",
        )
        self.assertEqual(manifest["strategy"], "state_temporal_split_v1")
        self.assertEqual(
            [segment["date_field"] for segment in manifest["segments"]],
            ["last_found", "last_fixed"],
        )
        self.assertEqual(
            {chunk["segment"] for chunk in manifest["chunks"]},
            {"active", "fixed"},
        )
        self.assertEqual({event["segment"] for event in progress}, {"active", "fixed"})

    def test_current_run_stuck_export_is_recorded_and_auto_cancelled(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = TimedOutCollectionClient(origin="created")
        progress: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-stuck-created",
                    progress_callback=progress.append,
                )

            state_path = next(Path(directory).rglob("export-state.json"))
            state = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(client.cancelled, ["fixture-stuck-export"])
        self.assertTrue(caught.exception.auto_cancelled)
        self.assertEqual(state["export_uuid"], "fixture-stuck-export")
        self.assertEqual(state["origin"], "created")
        self.assertEqual(state["status"], "TIMED_OUT")
        self.assertTrue(state["auto_cancelled"])
        self.assertEqual(progress[-1]["event"], "TENABLE_EXPORT_PROGRESS")

    def test_staged_timeout_preserves_created_export_without_auto_cancel(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = TimedOutCollectionClient(origin="created")
        progress: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-staged-timeout",
                    progress_callback=progress.append,
                    auto_cancel_on_timeout=False,
                )

        self.assertEqual(client.cancelled, [])
        self.assertFalse(caught.exception.auto_cancelled)
        self.assertEqual(caught.exception.export_uuid, "fixture-stuck-export")
        self.assertEqual(progress[-1]["status"], "TIMED_OUT")
        self.assertFalse(progress[-1]["auto_cancelled"])

    def test_staged_collection_warns_once_after_remote_inactivity(self) -> None:
        class StalledClient(TimedOutCollectionClient):
            def wait_for_completion(self, export_uuid: str, *, progress_callback=None):
                status = {
                    "status": "PROCESSING",
                    "completed_chunks": 0,
                    "total_chunks": 0,
                    "idle_seconds": 900,
                    "stalled": True,
                    "progress_made": False,
                }
                if progress_callback is not None:
                    progress_callback(status)
                    progress_callback(status)
                raise ExportTimeoutError(
                    "fixture timeout",
                    export_uuid=export_uuid,
                    last_status=status,
                    timeout_phase="no_progress",
                )

        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        progress: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError):
                collect_vm_snapshot(
                    client=StalledClient(origin="created"),  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-staged-warning",
                    progress_callback=progress.append,
                    auto_cancel_on_timeout=False,
                )

        warnings = [
            event
            for event in progress
            if event["event"] == "TENABLE_EXPORT_NO_PROGRESS_WARNING"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["idle_seconds"], 900)

    def test_current_run_no_progress_timeout_cancels_even_after_prior_chunk(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = IncrementalTimeoutCollectionClient(timeout_phase="no_progress")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-stalled-after-progress",
                )

        self.assertEqual(client.cancelled, ["fixture-stuck-export"])
        self.assertTrue(caught.exception.progress_made)
        self.assertTrue(caught.exception.auto_cancelled)

    def test_reused_stuck_export_is_never_auto_cancelled(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = TimedOutCollectionClient(origin="reused")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-stuck-reused",
                )

        self.assertEqual(client.cancelled, [])
        self.assertFalse(caught.exception.auto_cancelled)
        self.assertEqual(caught.exception.origin, "reused")

    def test_available_chunk_is_persisted_before_timeout(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = IncrementalTimeoutCollectionClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-partial",
                    logical_job_id="logical-july",
                )
            partial_path = next(Path(directory).rglob("manifest.partial.json"))
            manifest = json.loads(partial_path.read_text(encoding="utf-8"))
            chunk_path = partial_path.parent / "chunk-000002.jsonl.gz"

            self.assertEqual(client.download_calls, [2])
            self.assertEqual(client.cancelled, [])
            self.assertEqual([item["chunk_id"] for item in manifest["chunks"]], [2])
            self.assertEqual(manifest["logical_job_id"], "logical-july")
            self.assertEqual(manifest["origin"], "created")
            self.assertTrue(chunk_path.is_file())
            self.assertEqual(
                list(iter_chunk_records(chunk_path))[0]["id"],
                "finding-partial",
            )
            self.assertEqual(
                caught.exception.last_status["persisted_chunks"],
                [2],
            )
            self.assertEqual(
                Path(caught.exception.last_status["partial_manifest"]),
                partial_path,
            )

    def test_progress_without_available_chunk_preserves_resumable_uuid(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        request = VulnerabilityExportRequest(filters={"state": ["OPEN"]})
        client = ProgressWithoutChunkTimeoutCollectionClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError) as caught:
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=request,
                    output_root=directory,
                    run_id="run-progress-no-chunk",
                    logical_job_id="logical-july",
                )
            partial_path = next(Path(directory).rglob("manifest.partial.json"))
            manifest = json.loads(partial_path.read_text(encoding="utf-8"))

            self.assertEqual(manifest["export_uuid"], "fixture-stuck-export")
            self.assertEqual(manifest["chunks"], [])
            self.assertEqual(client.cancelled, [])
            self.assertEqual(
                Path(caught.exception.last_status["partial_manifest"]),
                partial_path,
            )
            self.assertEqual(
                find_resumable_vm_manifest(
                    directory,
                    profile=profile,
                    request=request,
                    logical_job_id="logical-july",
                ),
                partial_path,
            )

    def test_finished_resumed_uuid_is_checked_and_downloaded_without_new_export(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            resume_manifest = Path(directory) / "resume.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 3,
                "run_id": "old-run",
                "logical_job_id": "logical-july",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "old-export",
                "chunks": [],
            }), encoding="utf-8")
            client = StatusAwareResumeClient({
                "status": "FINISHED",
                "chunks_available": [1],
                "finished_chunks": 1,
                "total_chunks": 1,
            })

            result = collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=Path(directory) / "retry",
                run_id="run-retry-finished",
                logical_job_id="logical-july",
                resume_from=resume_manifest,
            )

            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(client.status_calls, ["old-export"])
            self.assertEqual(client.start_arguments, {})
            self.assertEqual(manifest["export_uuid"], "old-export")
            self.assertEqual(manifest["origin"], "resumed")
            self.assertEqual(result.snapshot.record_count, 1)

    def test_active_resumed_uuid_continues_without_new_export(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            resume_manifest = Path(directory) / "resume.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 3,
                "run_id": "old-run",
                "logical_job_id": "logical-july",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "active-export",
                "chunks": [],
            }), encoding="utf-8")
            client = StatusAwareResumeClient({
                "status": "PROCESSING",
                "chunks_available": [],
                "total_chunks": 1,
            })

            result = collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=Path(directory) / "retry",
                run_id="run-retry-active",
                logical_job_id="logical-july",
                resume_from=resume_manifest,
            )

            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(client.status_calls, ["active-export"])
            self.assertEqual(client.start_arguments, {})
            self.assertEqual(manifest["export_uuid"], "active-export")
            self.assertEqual(manifest["origin"], "resumed")

    def test_active_resumed_uuid_uses_only_its_remaining_budget(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            resume_manifest = Path(directory) / "resume.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 3,
                "run_id": "old-run",
                "logical_job_id": "logical-july",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "active-budget-export",
                "chunks": [],
            }), encoding="utf-8")
            client = BudgetAwareStatusClient({
                "status": "PROCESSING",
                "chunks_available": [],
                "total_chunks": 1,
            })

            collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=Path(directory) / "retry",
                run_id="run-retry-active-budget",
                logical_job_id="logical-july",
                resume_from=resume_manifest,
                resume_budget_seconds=123,
            )

            self.assertEqual(client.wait_budget, 123)

    def test_resume_status_error_other_than_not_found_remains_visible(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            resume_manifest = Path(directory) / "resume.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 3,
                "run_id": "old-run",
                "logical_job_id": "logical-july",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "protected-export",
                "chunks": [],
            }), encoding="utf-8")
            client = StatusAwareResumeClient(
                ApiError("Limite da API.", status_code=429)
            )

            with self.assertRaises(ApiError):
                collect_vm_snapshot(
                    client=client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=Path(directory) / "retry",
                    run_id="run-retry-rate-limit",
                    logical_job_id="logical-july",
                    resume_from=resume_manifest,
                )

            self.assertEqual(client.status_calls, ["protected-export"])
            self.assertEqual(client.start_arguments, {})

    def test_terminal_or_expired_resumed_uuid_starts_new_export(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        cases = (
            {"status": "CANCELLED", "chunks_available": [], "total_chunks": 1},
            ApiError("Export nao encontrado.", status_code=404),
            {"status": "FINISHED", "chunks_available": [], "total_chunks": 1},
        )
        for index, remote_status in enumerate(cases):
            with self.subTest(remote_status=remote_status):
                with tempfile.TemporaryDirectory() as directory:
                    resume_manifest = Path(directory) / "resume.json"
                    resume_manifest.write_text(json.dumps({
                        "schema_version": 3,
                        "run_id": "old-run",
                        "logical_job_id": "logical-july",
                        "client_id": profile.client_id,
                        "tenant_id": profile.tenant_id,
                        "source": "tenable_vm_vulnerabilities",
                        "export_uuid": f"old-export-{index}",
                        "chunks": [],
                    }), encoding="utf-8")
                    client = StatusAwareResumeClient(remote_status)
                    progress: list[dict[str, Any]] = []

                    result = collect_vm_snapshot(
                        client=client,  # type: ignore[arg-type]
                        profile=profile,
                        request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                        output_root=Path(directory) / "retry",
                        run_id=f"run-retry-new-{index}",
                        logical_job_id="logical-july",
                        resume_from=resume_manifest,
                        progress_callback=progress.append,
                    )

                    manifest = json.loads(
                        result.raw_manifest_path.read_text(encoding="utf-8")
                    )
                    self.assertEqual(client.status_calls, [f"old-export-{index}"])
                    self.assertNotEqual(client.start_arguments, {})
                    self.assertEqual(manifest["export_uuid"], "fixture-export")
                    self.assertEqual(manifest["origin"], "created")
                    recovery = next(
                        item for item in progress
                        if item.get("event") == "TENABLE_EXPORT_RECOVERY_UNAVAILABLE"
                    )
                    self.assertEqual(recovery["previous_export_uuid"], f"old-export-{index}")
                    self.assertEqual(recovery["replacement_export_uuid"], "fixture-export")
                    self.assertTrue(recovery["replacement_started"])

    def test_replacement_export_receives_a_fresh_wait_budget(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            resume_manifest = Path(directory) / "resume.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 3,
                "run_id": "old-run",
                "logical_job_id": "logical-july",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "expired-budget-export",
                "chunks": [],
            }), encoding="utf-8")
            client = BudgetAwareStatusClient(
                ApiError("Export nao encontrado.", status_code=404)
            )

            collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=Path(directory) / "retry",
                run_id="run-retry-replacement-budget",
                logical_job_id="logical-july",
                resume_from=resume_manifest,
                resume_budget_seconds=1,
            )

            self.assertIsNone(client.wait_budget)

    def test_same_unavailable_uuid_is_reported_once_when_manifest_and_argument_match(
        self,
    ) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            resume_manifest = Path(directory) / "resume.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 3,
                "run_id": "old-run",
                "logical_job_id": "logical-july",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "expired-shared-export",
                "chunks": [],
            }), encoding="utf-8")
            client = StatusAwareResumeClient(
                ApiError("Export nao encontrado.", status_code=404)
            )
            progress: list[dict[str, Any]] = []

            collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=Path(directory) / "retry",
                run_id="run-retry-deduplicated",
                logical_job_id="logical-july",
                export_uuid="expired-shared-export",
                resume_from=resume_manifest,
                progress_callback=progress.append,
            )

            recovery_events = [
                event for event in progress
                if event.get("event") == "TENABLE_EXPORT_RECOVERY_UNAVAILABLE"
            ]
            self.assertEqual(len(recovery_events), 1)

    def test_cancelled_provided_uuid_starts_new_export_before_waiting(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = StatusAwareResumeClient({
            "status": "CANCELLED",
            "chunks_available": [1],
            "completed_chunks": 1,
            "finished_chunks": 1,
            "total_chunks": 1,
            "failed_chunks": 0,
            "cancelled_chunks": 0,
        })

        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-retry-cancelled-provided",
                logical_job_id="logical-july",
                export_uuid="cancelled-export",
            )

            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(client.status_calls, ["cancelled-export"])
        self.assertNotEqual(client.start_arguments, {})
        self.assertEqual(manifest["export_uuid"], "fixture-export")
        self.assertEqual(manifest["origin"], "created")

    def test_partial_manifest_reuses_downloaded_chunk_on_retry(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        first_client = IncrementalTimeoutCollectionClient()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError):
                collect_vm_snapshot(
                    client=first_client,  # type: ignore[arg-type]
                    profile=profile,
                    request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                    output_root=directory,
                    run_id="run-partial",
                    logical_job_id="logical-july",
                )
            partial_path = next(Path(directory).rglob("manifest.partial.json"))
            retry_client = FakeCollectionClient(
                {2: b'{"id":"must-not-download"}\n'}
            )

            result = collect_vm_snapshot(
                client=retry_client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=Path(directory) / "retry",
                run_id="run-retry",
                logical_job_id="logical-july",
                resume_from=partial_path,
            )

            self.assertEqual(retry_client.download_calls, [])
            self.assertEqual(result.snapshot.record_count, 1)
            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["chunks"][0]["chunk_id"], 2)
            self.assertEqual(manifest["origin"], "resumed")

    def test_resume_discovery_requires_same_logical_job_and_query(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        request = VulnerabilityExportRequest(filters={"state": ["OPEN"]})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError):
                collect_vm_snapshot(
                    client=IncrementalTimeoutCollectionClient(),  # type: ignore[arg-type]
                    profile=profile,
                    request=request,
                    output_root=directory,
                    run_id="run-partial",
                    logical_job_id="logical-july",
                )
            expected = next(Path(directory).rglob("manifest.partial.json"))

            self.assertEqual(
                find_resumable_vm_manifest(
                    directory,
                    profile=profile,
                    request=request,
                    logical_job_id="logical-july",
                ),
                expected,
            )
            self.assertIsNone(
                find_resumable_vm_manifest(
                    directory,
                    profile=profile,
                    request=VulnerabilityExportRequest(
                        filters={"state": ["FIXED"]}
                    ),
                    logical_job_id="logical-july",
                )
            )
            self.assertIsNone(
                find_resumable_vm_manifest(
                    directory,
                    profile=profile,
                    request=request,
                    logical_job_id="logical-august",
                )
            )

    def test_resume_discovery_skips_remote_terminal_export(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        request = VulnerabilityExportRequest(filters={"state": ["OPEN"]})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExportTimeoutError):
                collect_vm_snapshot(
                    client=IncrementalTimeoutCollectionClient(),  # type: ignore[arg-type]
                    profile=profile,
                    request=request,
                    output_root=directory,
                    run_id="run-cancelled",
                    logical_job_id="logical-july",
                )
            partial = next(Path(directory).rglob("manifest.partial.json"))
            state_path = partial.parent / "export-state.json"
            state_path.write_text(
                json.dumps({
                    "status": "CANCELLED",
                    "auto_cancelled": False,
                    "completed_chunks": 1,
                    "total_chunks": 2,
                }),
                encoding="utf-8",
            )

            self.assertIsNone(find_resumable_vm_manifest(
                directory,
                profile=profile,
                request=request,
                logical_job_id="logical-july",
            ))

    def test_plain_jsonl_is_persisted_as_valid_gzip_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            result = store_chunk_atomic(
                directory,
                [b'{"id":1}\n', b'{"id":2}\n'],
                chunk_id=1,
            )
            self.assertEqual(result.path.suffixes[-2:], [".jsonl", ".gz"])
            with gzip.open(result.path, "rb") as stream:
                self.assertEqual(stream.read(), b'{"id":1}\n{"id":2}\n')
            self.assertEqual(result.record_count, 2)
            self.assertFalse(list(directory.glob("*.partial")))

    def test_gzip_input_is_recompressed_incrementally_without_download_copy(self) -> None:
        payload = b'{"id":1}\n{"id":2}\n'
        compressed = gzip.compress(payload)
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            result = store_chunk_atomic(
                directory,
                (compressed[index:index + 1] for index in range(len(compressed))),
                chunk_id=9,
            )
            with gzip.open(result.path, "rb") as stream:
                self.assertEqual(stream.read(), payload)
            self.assertEqual(result.record_count, 2)
            self.assertFalse(list(directory.glob("*.download.partial")))

    def test_large_json_array_is_read_as_individual_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            source = Path(directory_name) / "large.json.gz"
            expected = [{"id": index} for index in range(5000)]
            with gzip.open(source, "wt", encoding="utf-8") as stream:
                json.dump(expected, stream)
            records = iter_chunk_records(source)
            self.assertEqual(next(records), {"id": 0})
            self.assertEqual(sum(1 for _ in records), 4999)

    def test_invalid_partial_chunk_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory_name:
            partial = Path(directory_name) / "chunk-000001.jsonl.gz.partial"
            partial.write_bytes(b"broken")
            self.assertIsNone(reusable_chunk(partial, expected_sha256="abc"))

    def test_valid_manifest_chunk_skips_network_download(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory_name:
            directory = Path(directory_name)
            stored = store_chunk_atomic(
                directory / "existing",
                [b'{"id":"finding-existing","state":"OPEN"}\n'],
                chunk_id=1,
            )
            resume_manifest = directory / "resume-manifest.json"
            resume_manifest.write_text(json.dumps({
                "schema_version": 2,
                "run_id": "old-run",
                "client_id": profile.client_id,
                "tenant_id": profile.tenant_id,
                "source": "tenable_vm_vulnerabilities",
                "export_uuid": "fixture-export",
                "query": {},
                "chunks": [{
                    "chunk_id": 1,
                    "path": stored.path.resolve().as_uri(),
                    "stored_bytes": stored.stored_bytes,
                    "records": stored.record_count,
                    "content_sha256": stored.content_sha256,
                    "storage_sha256": stored.storage_sha256,
                    "encoding": "gzip",
                    "complete": True,
                }],
            }), encoding="utf-8")
            client = FakeCollectionClient({1: b"should-not-download"})

            result = collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory / "new",
                run_id="resumed-run",
                resume_from=resume_manifest,
            )

            self.assertEqual(client.download_calls, [])
            self.assertTrue(result.raw_manifest_path.is_file())
            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest["chunks"][0]["complete"])
            self.assertEqual(manifest["chunks"][0]["content_sha256"], stored.content_sha256)
    def test_optional_was_failure_does_not_raise(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            attempt = collect_optional_was_snapshot(
                client=UnavailableWasCollectionClient({}),  # type: ignore[arg-type]
                profile=profile,
                request=WasExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-was-unavailable",
            )
            self.assertIsNone(attempt.result)
            self.assertEqual(attempt.status, "UNAVAILABLE")
            self.assertEqual(attempt.warnings[0]["code"], "WAS_NOT_AVAILABLE")
            self.assertIsNotNone(attempt.failure)
            self.assertEqual(attempt.failure.code, "WAS_NOT_AVAILABLE")
            self.assertFalse(attempt.failure.retryable)
            self.assertIsNone(attempt.failure.export_uuid)

    def test_optional_was_timeout_reports_progress_and_does_not_raise(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        progress: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory:
            attempt = collect_optional_was_snapshot(
                client=TimedOutWasCollectionClient({}),  # type: ignore[arg-type]
                profile=profile,
                request=WasExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-was-timeout",
                progress_callback=progress.append,
            )

        self.assertIsNone(attempt.result)
        self.assertEqual(attempt.status, "UNAVAILABLE")
        self.assertEqual(attempt.warnings[0]["code"], "WAS_COLLECTION_UNAVAILABLE")
        self.assertEqual(progress[-1]["source"], "tenable_was_findings")
        self.assertEqual(progress[-1]["export_uuid"], "fixture-was-export")
        self.assertEqual(progress[-1]["origin"], "created")
        self.assertEqual(progress[-1]["status"], "TIMED_OUT")
        self.assertIsNotNone(attempt.failure)
        self.assertEqual(attempt.failure.code, "WAS_COLLECTION_UNAVAILABLE")
        self.assertTrue(attempt.failure.retryable)
        self.assertEqual(attempt.failure.export_uuid, "fixture-was-export")
        self.assertEqual(attempt.failure.origin, "created")
        self.assertEqual(attempt.failure.remote_status, "PROCESSING")
        self.assertEqual(attempt.failure.completed_chunks, 0)
        self.assertEqual(attempt.failure.total_chunks, 1)
        self.assertFalse(attempt.failure.progress_made)
        self.assertTrue(attempt.failure.safe_cancel_available)

    def test_was_timeout_without_chunks_preserves_uuid_in_partial_manifest(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        with tempfile.TemporaryDirectory() as directory:
            attempt = collect_optional_was_snapshot(
                client=TimedOutWasCollectionClient({}),  # type: ignore[arg-type]
                profile=profile,
                request=WasExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-was-timeout-no-chunks",
            )
            partial_path = next(Path(directory).rglob("manifest.partial.json"))
            manifest = json.loads(partial_path.read_text(encoding="utf-8"))

        self.assertEqual(attempt.failure.export_uuid, "fixture-was-export")
        self.assertEqual(manifest["export_uuid"], "fixture-was-export")
        self.assertEqual(manifest["chunks"], [])

    def test_was_collection_writes_immutable_dedicated_snapshot(self) -> None:
        profile = load_client_profile(
            ROOT / "clients/examples/client-profile-intelligence-expanded.json"
        )
        client = FakeWasCollectionClient({1: b'[{"finding_id":"was-finding"}]'})
        progress: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory() as directory:
            result = collect_was_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=WasExportRequest(
                    filters={"since": 1782860400, "state": ["OPEN", "FIXED"]}
                ),
                output_root=directory,
                run_id="run-was-fixture",
                progress_callback=progress.append,
            )
            snapshot = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["source"], "tenable_was_findings")
            self.assertEqual(snapshot["record_count"], 1)
            self.assertEqual(snapshot["query"]["filters"]["since"], 1782860400)
            self.assertEqual(manifest["schema_version"], 3)
            self.assertEqual(manifest["origin"], "created")
            self.assertEqual(manifest["status"], "FINISHED")
            self.assertEqual(progress[-1]["source"], "tenable_was_findings")
            self.assertEqual(progress[-1]["status"], "FINISHED")
            self.assertTrue(
                (result.raw_manifest_path.parent / "export-state.json").is_file()
            )
            chunk = manifest["chunks"][0]
            chunk_path = result.raw_manifest_path.parent / "chunk-000001.jsonl.gz"
            self.assertEqual(chunk["path"], chunk_path.resolve().as_uri())
            self.assertEqual(chunk["encoding"], "gzip")
            self.assertEqual(chunk["records"], 1)
            self.assertGreater(chunk["logical_bytes"], 0)
            self.assertGreater(chunk["stored_bytes"], 0)
            self.assertEqual(
                list(iter_chunk_records(chunk_path)),
                [{"finding_id": "was-finding"}],
            )

    def test_asset_collection_writes_v2_source_snapshot(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = FakeAssetCollectionClient({1: b'[{"id":"asset-fixture"}]'})
        with tempfile.TemporaryDirectory() as directory:
            result = collect_asset_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=AssetExportRequest(filters={}, chunk_size=100),
                output_root=directory,
                run_id="run-asset-fixture",
            )
            snapshot = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            manifest = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot["source"], "tenable_vm_assets_v2")
            self.assertEqual(snapshot["record_count"], 1)
            self.assertEqual(manifest["query"]["chunk_size"], 100)
            self.assertFalse(manifest["query"]["include_open_ports"])
            self.assertFalse(manifest["query"]["include_resource_tags"])

    def test_collection_writes_immutable_raw_and_sanitized_snapshot(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        client = FakeCollectionClient(
            {
                2: b'{"id":"finding-2","state":"REOPENED"}\n',
                1: b'{"id":"finding-1","state":"OPEN"}\n',
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot(
                client=client,  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(
                    filters={"state": ["OPEN", "REOPENED"]},
                    include_plugin_output=False,
                ),
                output_root=directory,
                run_id="run-fixture",
            )
            snapshot_data = json.loads(result.snapshot_path.read_text(encoding="utf-8"))
            manifest_data = json.loads(result.raw_manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(snapshot_data["record_count"], 2)
            self.assertEqual(snapshot_data["availability"], "AVAILABLE")
            self.assertEqual(snapshot_data["query"]["include_plugin_output"], False)
            self.assertEqual([item["chunk_id"] for item in manifest_data["chunks"]], [1, 2])
            self.assertEqual(len(snapshot_data["raw_sha256"]), 64)

    def test_completed_empty_export_produces_no_data_snapshot(self) -> None:
        profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
        with tempfile.TemporaryDirectory() as directory:
            result = collect_vm_snapshot(
                client=FakeCollectionClient({}),  # type: ignore[arg-type]
                profile=profile,
                request=VulnerabilityExportRequest(filters={"state": ["OPEN"]}),
                output_root=directory,
                run_id="run-empty",
            )
            self.assertEqual(result.snapshot.record_count, 0)
            self.assertEqual(result.snapshot.availability.value, "NO_DATA")


if __name__ == "__main__":
    unittest.main()
