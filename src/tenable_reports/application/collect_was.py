from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tenable_reports import __version__
from tenable_reports.application.collect import (
    CollectionResult,
    _write_exclusive,
    store_chunk_atomic,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import (
    build_source_snapshot_from_chunk_hashes,
    sanitized_mapping,
    utc_now_iso,
)
from tenable_reports.infrastructure.tenable_vm.client import ApiError, ExportTimeoutError
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
) -> CollectionResult:
    if not profile.was_scope.enabled:
        raise ValueError("A coleta WAS requer scope.was.enabled=true no perfil.")
    actual_run_id = run_id or str(uuid.uuid4())
    started_at = utc_now_iso()
    query = request.to_api_query()
    actual_export_uuid = export_uuid or client.start_findings_export(
        filters=request.filters,
        num_assets=request.num_assets,
        include_unlicensed=request.include_unlicensed,
    )
    _, chunk_ids = client.wait_for_findings_completion(actual_export_uuid)
    raw_directory = (
        Path(output_root) / "raw" / profile.client_id / actual_run_id
        / "tenable_was_findings" / actual_export_uuid
    )
    stored_chunks = []
    for chunk_id in chunk_ids:
        content = client.download_findings_chunk_bytes(actual_export_uuid, chunk_id)
        stored_chunks.append(
            store_chunk_atomic(raw_directory, (content,), chunk_id=chunk_id)
        )
    manifest = {
        "schema_version": 2,
        "run_id": actual_run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "source": "tenable_was_findings",
        "export_uuid": actual_export_uuid,
        "query": sanitized_mapping(query),
        "chunks": [item.to_manifest() for item in stored_chunks],
    }
    manifest_path = raw_directory / "manifest.json"
    _write_exclusive(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    record_count = sum(item.record_count for item in stored_chunks)
    snapshot = build_source_snapshot_from_chunk_hashes(
        run_id=actual_run_id,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        source="tenable_was_findings",
        export_uuid=actual_export_uuid,
        query=query,
        chunk_hashes=(
            (item.chunk_id, item.content_sha256) for item in stored_chunks
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
