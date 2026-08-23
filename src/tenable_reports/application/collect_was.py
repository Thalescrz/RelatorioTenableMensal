from __future__ import annotations

import hashlib
import inspect
import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from tenable_reports import __version__
from tenable_reports.application.collect import (
    CollectionResult,
    StoredChunk,
    _write_exclusive,
    _write_json_replace,
    store_chunk_atomic,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import (
    build_source_snapshot_from_chunk_hashes,
    sanitized_mapping,
    utc_now_iso,
)
from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    ExportJob,
    ExportTimeoutError,
)
from tenable_reports.infrastructure.tenable_was.client import TenableWasClient


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WasExportRequest:
    filters: dict[str, Any]
    num_assets: int = 1000
    include_unlicensed: bool = False

    def to_api_query(self) -> dict[str, Any]:
        return {
            "num_assets": max(50, min(int(self.num_assets), 5000)),
            "include_unlicensed": bool(self.include_unlicensed),
            "filters": dict(self.filters),
        }


@dataclass(frozen=True, slots=True)
class WasCollectionAttempt:
    result: CollectionResult | None
    status: str
    warnings: tuple[Mapping[str, Any], ...] = ()


def collect_optional_was_snapshot(
    *,
    client: TenableWasClient,
    profile: ClientProfile,
    request: WasExportRequest,
    output_root: str | Path,
    run_id: str | None = None,
    export_uuid: str | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> WasCollectionAttempt:
    """Tenta o WAS sem permitir que uma capacidade opcional derrube o VM."""
    try:
        result = collect_was_snapshot(
            client=client,
            profile=profile,
            request=request,
            output_root=output_root,
            run_id=run_id,
            export_uuid=export_uuid,
            progress_callback=progress_callback,
        )
    except (ApiError, ExportTimeoutError) as exc:
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403, 404}:
            code = "WAS_NOT_AVAILABLE"
            message = (
                "A API WAS nao esta habilitada ou acessivel para este cliente. "
                "O relatorio VM continuou normalmente."
            )
        else:
            code = "WAS_COLLECTION_UNAVAILABLE"
            message = (
                "A coleta WAS ficou indisponivel nesta execucao. "
                "O relatorio VM continuou normalmente."
            )
        warning = {"code": code, "message": message, "status_code": status_code}
        LOGGER.warning("%s", message, extra={"was_status_code": status_code})
        return WasCollectionAttempt(
            result=None,
            status="UNAVAILABLE",
            warnings=(warning,),
        )
    return WasCollectionAttempt(
        result=result,
        status=result.snapshot.availability.value,
    )


def collect_was_snapshot(
    *,
    client: TenableWasClient,
    profile: ClientProfile,
    request: WasExportRequest,
    output_root: str | Path,
    run_id: str | None = None,
    export_uuid: str | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> CollectionResult:
    if not profile.was_scope.enabled:
        raise ValueError("A coleta WAS requer scope.was.enabled=true no perfil.")
    actual_run_id = run_id or str(uuid.uuid4())
    started_at = utc_now_iso()
    query = request.to_api_query()
    query_sha256 = hashlib.sha256(
        json.dumps(
            query, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    if export_uuid:
        job = ExportJob(export_uuid, "provided")
    else:
        starter = getattr(client, "start_findings_export_job", None)
        job = (
            starter(
                filters=request.filters,
                num_assets=request.num_assets,
                include_unlicensed=request.include_unlicensed,
            )
            if callable(starter)
            else ExportJob(
                client.start_findings_export(
                    filters=request.filters,
                    num_assets=request.num_assets,
                    include_unlicensed=request.include_unlicensed,
                ),
                "created",
            )
        )
    actual_export_uuid = job.export_uuid
    raw_directory = (
        Path(output_root)
        / "raw"
        / profile.client_id
        / actual_run_id
        / "tenable_was_findings"
        / actual_export_uuid
    )
    state_path = raw_directory / "export-state.json"
    partial_manifest_path = raw_directory / "manifest.partial.json"
    manifest_path = raw_directory / "manifest.json"
    stored_chunks: dict[int, StoredChunk] = {}

    def manifest_payload(*, status: str) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "run_id": actual_run_id,
            "client_id": profile.client_id,
            "tenant_id": profile.tenant_id,
            "source": "tenable_was_findings",
            "export_uuid": actual_export_uuid,
            "origin": job.origin,
            "status": status,
            "query": sanitized_mapping(query),
            "query_sha256": query_sha256,
            "updated_at": utc_now_iso(),
            "chunks": [
                stored_chunks[chunk_id].to_manifest()
                for chunk_id in sorted(stored_chunks)
            ],
        }

    def emit_progress(status: str, **details: Any) -> None:
        payload = {
            "event": "TENABLE_EXPORT_PROGRESS",
            "source": "tenable_was_findings",
            "export_uuid": actual_export_uuid,
            "origin": job.origin,
            "status": status,
            "started_at": started_at,
            "persisted_chunks": sorted(stored_chunks),
            "partial_manifest": (
                str(partial_manifest_path.resolve())
                if partial_manifest_path.exists()
                else None
            ),
            **details,
        }
        _write_json_replace(state_path, payload)
        if progress_callback is not None:
            progress_callback(payload)

    def persist_chunk(chunk_id: int) -> None:
        actual_chunk_id = int(chunk_id)
        if actual_chunk_id in stored_chunks:
            return
        content = client.download_findings_chunk_bytes(actual_export_uuid, chunk_id)
        stored_chunks[actual_chunk_id] = store_chunk_atomic(
            raw_directory,
            (content,),
            chunk_id=actual_chunk_id,
        )
        _write_json_replace(
            partial_manifest_path,
            manifest_payload(status="PROCESSING"),
        )

    emit_progress(
        "STARTED",
        completed_chunks=0,
        total_chunks=0,
        progress_made=False,
    )

    def update_progress(status: Mapping[str, Any]) -> None:
        remote_status = str(status.get("status") or "PROCESSING").upper()
        details = {
            str(key): value
            for key, value in status.items()
            if key not in {
                "event", "source", "export_uuid", "origin", "status",
                "persisted_chunks", "partial_manifest",
            }
        }
        emit_progress(remote_status, **details)

    try:
        wait_method = client.wait_for_findings_completion
        parameters = inspect.signature(wait_method).parameters
        wait_arguments: dict[str, Any] = {}
        if "progress_callback" in parameters:
            wait_arguments["progress_callback"] = update_progress
        if "chunk_callback" in parameters:
            wait_arguments["chunk_callback"] = persist_chunk
        _, chunk_ids = wait_method(actual_export_uuid, **wait_arguments)
        for chunk_id in chunk_ids:
            persist_chunk(chunk_id)
    except ExportTimeoutError as exc:
        exc.export_uuid = actual_export_uuid
        exc.origin = job.origin
        exc.last_status = {
            **exc.last_status,
            "persisted_chunks": sorted(stored_chunks),
            "partial_manifest": (
                str(partial_manifest_path.resolve())
                if partial_manifest_path.exists()
                else None
            ),
        }
        exc.progress_made = bool(exc.progress_made or stored_chunks)
        timeout_details = {
            str(key): value
            for key, value in exc.last_status.items()
            if key not in {
                "event", "source", "export_uuid", "origin", "status",
                "persisted_chunks", "partial_manifest", "completed_chunks",
                "progress_made",
            }
        }
        emit_progress(
            "TIMED_OUT",
            **timeout_details,
            completed_chunks=max(
                len(stored_chunks),
                int(exc.last_status.get("completed_chunks") or 0),
            ),
            progress_made=exc.progress_made,
            auto_cancelled=False,
        )
        raise
    except ApiError as exc:
        emit_progress(
            "FAILED",
            completed_chunks=len(stored_chunks),
            progress_made=bool(stored_chunks),
            error=str(exc).strip()[-500:],
        )
        raise

    emit_progress(
        "FINISHED",
        completed_chunks=len(stored_chunks),
        total_chunks=len(chunk_ids),
        progress_made=bool(stored_chunks),
        auto_cancelled=False,
    )
    stored_in_order = [stored_chunks[key] for key in sorted(stored_chunks)]
    manifest = manifest_payload(status="FINISHED")
    _write_exclusive(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    partial_manifest_path.unlink(missing_ok=True)
    record_count = sum(item.record_count for item in stored_in_order)
    snapshot = build_source_snapshot_from_chunk_hashes(
        run_id=actual_run_id,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        source="tenable_was_findings",
        export_uuid=actual_export_uuid,
        query=query,
        chunk_hashes=(
            (item.chunk_id, item.content_sha256) for item in stored_in_order
        ),
        record_count=record_count,
        started_at=started_at,
        collector_version=__version__,
        raw_manifest_uri=manifest_path.resolve().as_uri(),
    )
    snapshot_path = (
        Path(output_root) / "snapshots" / profile.client_id / actual_run_id
        / "tenable_was_findings.snapshot.json"
    )
    snapshot.write_json(snapshot_path)
    return CollectionResult(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        raw_manifest_path=manifest_path,
        records=(),
    )
