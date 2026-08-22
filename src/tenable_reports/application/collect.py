from __future__ import annotations

import hashlib
import gzip
import inspect
import json
import os
import uuid
import zlib
from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from tenable_reports import __version__
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import (
    SourceSnapshot,
    build_source_snapshot_from_chunk_hashes,
    sanitized_mapping,
    utc_now_iso,
)
from tenable_reports.application.storage_guard import storage_preflight
from tenable_reports.infrastructure.tenable_vm.client import (
    ExportJob,
    ExportTimeoutError,
    TenableVmClient,
)
from tenable_reports.infrastructure.tenable_vm.parser import iter_chunk_records


@dataclass(frozen=True, slots=True)
class VulnerabilityExportRequest:
    filters: dict[str, Any]
    num_assets: int = 1000
    include_unlicensed: bool = False
    include_software_vulns: bool = False
    include_plugin_output: bool = False
    properties: tuple[str, ...] = ()

    def to_api_query(self) -> dict[str, Any]:
        query: dict[str, Any] = {
            "num_assets": max(50, min(int(self.num_assets), 5000)),
            "include_unlicensed": self.include_unlicensed,
            "include_software_vulns": self.include_software_vulns,
            "include_plugin_output": self.include_plugin_output,
            "filters": dict(self.filters),
        }
        if self.properties:
            query["properties"] = list(self.properties)
        return query


@dataclass(frozen=True, slots=True)
class AssetExportRequest:
    filters: dict[str, Any]
    chunk_size: int = 1000
    include_open_ports: bool = False
    include_resource_tags: bool = False

    def to_api_query(self) -> dict[str, Any]:
        query: dict[str, Any] = {
            "chunk_size": int(self.chunk_size),
            "include_open_ports": self.include_open_ports,
            "include_resource_tags": self.include_resource_tags,
        }
        if self.filters:
            query["filters"] = dict(self.filters)
        return query


@dataclass(frozen=True, slots=True)
class CollectionResult:
    snapshot: SourceSnapshot
    snapshot_path: Path
    raw_manifest_path: Path
    records: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class StoredChunk:
    chunk_id: int
    path: Path
    logical_bytes: int
    stored_bytes: int
    record_count: int
    content_sha256: str
    storage_sha256: str
    encoding: str = "gzip"

    def to_manifest(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "path": self.path.resolve().as_uri(),
            "logical_bytes": self.logical_bytes,
            "stored_bytes": self.stored_bytes,
            "records": self.record_count,
            "content_sha256": self.content_sha256,
            "storage_sha256": self.storage_sha256,
            "encoding": self.encoding,
            "complete": True,
        }


def _write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _write_json_replace(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    _write_exclusive(
        temporary,
        (json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    os.replace(temporary, path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_file_blocks(stream: Any) -> Iterator[bytes]:
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            return
        yield block


def store_chunk_atomic(
    directory: str | Path,
    byte_chunks: Iterable[bytes],
    *,
    chunk_id: int,
) -> StoredChunk:
    target_directory = Path(directory)
    target_directory.mkdir(parents=True, exist_ok=True)
    final = target_directory / f"chunk-{int(chunk_id):06d}.jsonl.gz"
    stored_partial = target_directory / f"chunk-{int(chunk_id):06d}.jsonl.gz.partial"
    if final.exists():
        raise FileExistsError(f"Chunk imutável já existe: {final}")
    stored_partial.unlink(missing_ok=True)
    try:
        blocks = iter(byte_chunks)
        prefix = bytearray()
        while len(prefix) < 2:
            try:
                block = next(blocks)
            except StopIteration:
                break
            if block:
                prefix.extend(bytes(block))
        incoming_gzip = bytes(prefix[:2]) == b"\x1f\x8b"
        decompressor = (
            zlib.decompressobj(16 + zlib.MAX_WBITS) if incoming_gzip else None
        )
        content_digest = hashlib.sha256()
        logical_bytes = 0
        with stored_partial.open("xb") as raw_target:
            with gzip.GzipFile(fileobj=raw_target, mode="wb", mtime=0) as compressed:
                for source_block in chain((bytes(prefix),), blocks):
                    if not source_block:
                        continue
                    content_block = (
                        decompressor.decompress(source_block)
                        if decompressor is not None
                        else source_block
                    )
                    if content_block:
                        content_digest.update(content_block)
                        logical_bytes += len(content_block)
                        compressed.write(content_block)
                if decompressor is not None:
                    tail = decompressor.flush()
                    if not decompressor.eof:
                        raise ValueError("Chunk gzip de origem está incompleto.")
                    if tail:
                        content_digest.update(tail)
                        logical_bytes += len(tail)
                        compressed.write(tail)
            raw_target.flush()
            os.fsync(raw_target.fileno())
        records = sum(1 for _ in iter_chunk_records(stored_partial))
        validation_digest = hashlib.sha256()
        with gzip.open(stored_partial, "rb") as stream:
            for block in _iter_file_blocks(stream):
                validation_digest.update(block)
        content_sha256 = content_digest.hexdigest()
        if validation_digest.hexdigest() != content_sha256:
            raise ValueError("Hash do conteúdo divergiu após a compactação gzip.")
        storage_sha256 = _sha256_file(stored_partial)
        stored_bytes = stored_partial.stat().st_size
        stored_partial.replace(final)
        return StoredChunk(
            chunk_id=int(chunk_id),
            path=final,
            logical_bytes=logical_bytes,
            stored_bytes=stored_bytes,
            record_count=records,
            content_sha256=content_sha256,
            storage_sha256=storage_sha256,
        )
    finally:
        stored_partial.unlink(missing_ok=True)


def reusable_chunk(
    path: str | Path,
    *,
    expected_sha256: str,
    expected_storage_sha256: str | None = None,
    chunk_id: int = 0,
) -> StoredChunk | None:
    source = Path(path)
    if source.name.endswith(".partial") or not source.is_file():
        return None
    try:
        if expected_storage_sha256 and _sha256_file(source) != expected_storage_sha256:
            return None
        content_digest = hashlib.sha256()
        logical_bytes = 0
        with gzip.open(source, "rb") as stream:
            for block in _iter_file_blocks(stream):
                content_digest.update(block)
                logical_bytes += len(block)
        if content_digest.hexdigest() != expected_sha256:
            return None
        records = sum(1 for _ in iter_chunk_records(source))
    except (OSError, EOFError, ValueError):
        return None
    return StoredChunk(
        chunk_id=int(chunk_id),
        path=source,
        logical_bytes=logical_bytes,
        stored_bytes=source.stat().st_size,
        record_count=records,
        content_sha256=content_digest.hexdigest(),
        storage_sha256=_sha256_file(source),
    )


def _path_from_uri(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return Path(value)
    raw_path = unquote(parsed.path)
    if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    if parsed.netloc:
        raw_path = f"//{parsed.netloc}{raw_path}"
    return Path(url2pathname(raw_path))


def _load_resume_chunks(
    path: str | Path | None,
    *,
    source: str,
    client_id: str,
    tenant_id: str,
) -> tuple[str | None, dict[int, StoredChunk]]:
    if path is None:
        return None, {}
    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, {}
    if (
        payload.get("source") != source
        or payload.get("client_id") != client_id
        or payload.get("tenant_id") != tenant_id
    ):
        return None, {}
    reusable: dict[int, StoredChunk] = {}
    for item in payload.get("chunks") or ():
        if not isinstance(item, Mapping) or not item.get("complete"):
            continue
        try:
            chunk_id = int(item.get("chunk_id"))
        except (TypeError, ValueError):
            continue
        content_hash = str(item.get("content_sha256") or "")
        if not content_hash:
            continue
        candidate = reusable_chunk(
            _path_from_uri(str(item.get("path") or "")),
            expected_sha256=content_hash,
            expected_storage_sha256=str(item.get("storage_sha256") or "") or None,
            chunk_id=chunk_id,
        )
        if candidate:
            reusable[chunk_id] = candidate
    return str(payload.get("export_uuid") or "") or None, reusable


def _download_blocks(
    client: Any,
    *,
    export_uuid: str,
    chunk_id: int,
    asset: bool,
) -> Iterable[bytes]:
    iterator_name = "iter_asset_chunk_bytes" if asset else "iter_chunk_bytes"
    download_name = "download_asset_chunk_bytes" if asset else "download_chunk_bytes"
    iterator = getattr(client, iterator_name, None)
    if callable(iterator):
        return iterator(export_uuid, chunk_id)
    return (getattr(client, download_name)(export_uuid, chunk_id),)


def _localize_reused_chunk(
    reused: StoredChunk,
    *,
    raw_directory: Path,
) -> StoredChunk:
    if reused.path.parent.resolve() == raw_directory.resolve():
        return reused
    with gzip.open(reused.path, "rb") as stream:
        return store_chunk_atomic(
            raw_directory,
            _iter_file_blocks(stream),
            chunk_id=reused.chunk_id,
        )


def _raw_extension(content: bytes) -> str:
    return ".jsonl.gz" if content[:2] == b"\x1f\x8b" else ".jsonl"


def collect_vm_snapshot(
    *,
    client: TenableVmClient,
    profile: ClientProfile,
    request: VulnerabilityExportRequest,
    output_root: str | Path,
    run_id: str | None = None,
    export_uuid: str | None = None,
    resume_from: str | Path | None = None,
    minimum_free_gb: int = 10,
    last_success_bytes: int | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    snapshot_suffix: str | None = None,
) -> CollectionResult:
    actual_run_id = run_id or str(uuid.uuid4())
    started_at = utc_now_iso()
    query = request.to_api_query()
    resumed_export_uuid, resumed_chunks = _load_resume_chunks(
        resume_from,
        source="tenable_vm_vulnerabilities",
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
    )
    start_arguments = {
        "filters": request.filters,
        "num_assets": request.num_assets,
        "include_unlicensed": request.include_unlicensed,
        "include_software_vulns": request.include_software_vulns,
        "include_plugin_output": request.include_plugin_output,
        "properties": list(request.properties) or None,
    }
    if export_uuid:
        job = ExportJob(export_uuid=export_uuid, origin="provided")
    elif resumed_export_uuid:
        job = ExportJob(export_uuid=resumed_export_uuid, origin="resumed")
    else:
        starter = getattr(client, "start_vulnerability_export_job", None)
        job = (
            starter(**start_arguments)
            if callable(starter)
            else ExportJob(
                export_uuid=client.start_vulnerability_export(**start_arguments),
                origin="created",
            )
        )
    actual_export_uuid = job.export_uuid

    raw_directory = (
        Path(output_root)
        / "raw"
        / profile.client_id
        / actual_run_id
        / "tenable_vm_vulnerabilities"
        / actual_export_uuid
    )
    state_path = raw_directory / "export-state.json"

    def emit_progress(status: str, **details: Any) -> None:
        payload = {
            "event": "TENABLE_EXPORT_PROGRESS",
            "source": "tenable_vm_vulnerabilities",
            "export_uuid": actual_export_uuid,
            "origin": job.origin,
            "status": status,
            "started_at": started_at,
            **details,
        }
        _write_json_replace(state_path, payload)
        if progress_callback is not None:
            progress_callback(payload)

    emit_progress(
        "STARTED",
        completed_chunks=0,
        total_chunks=0,
        progress_made=False,
        auto_cancelled=False,
        query=sanitized_mapping(query),
    )

    def update_progress(status: Mapping[str, Any]) -> None:
        remote_status = str(status.get("status") or "PROCESSING").upper()
        details = {
            str(key): value
            for key, value in status.items()
            if key not in {"event", "source", "export_uuid", "origin", "status"}
        }
        emit_progress(remote_status, **details)

    try:
        wait_method = client.wait_for_completion
        parameters = inspect.signature(wait_method).parameters
        if "progress_callback" in parameters:
            _, chunk_ids = wait_method(
                actual_export_uuid,
                progress_callback=update_progress,
            )
        else:
            _, chunk_ids = wait_method(actual_export_uuid)
    except ExportTimeoutError as exc:
        exc.export_uuid = actual_export_uuid
        exc.origin = job.origin
        auto_cancelled = False
        cancellation_error: str | None = None
        if job.created_by_current_run and not exc.progress_made:
            try:
                client.cancel_vulnerability_export(actual_export_uuid)
                auto_cancelled = True
            except Exception as cancel_exc:
                cancellation_error = str(cancel_exc).strip()[-500:]
        exc.auto_cancelled = auto_cancelled
        exc.cancellation_error = cancellation_error
        timeout_details = {
            str(key): value
            for key, value in exc.last_status.items()
            if key not in {
                "event", "source", "export_uuid", "origin", "status",
                "auto_cancelled", "cancellation_error", "progress_made",
            }
        }
        emit_progress(
            "TIMED_OUT",
            **timeout_details,
            auto_cancelled=auto_cancelled,
            cancellation_error=cancellation_error,
            progress_made=exc.progress_made,
        )
        raise
    emit_progress(
        "FINISHED",
        completed_chunks=len(chunk_ids),
        total_chunks=len(chunk_ids),
        progress_made=bool(chunk_ids),
        auto_cancelled=False,
    )

    stored_chunks: list[StoredChunk] = []
    for chunk_id in chunk_ids:
        storage_preflight(
            raw_directory,
            last_success_bytes=last_success_bytes,
            minimum_free_gb=minimum_free_gb,
        )
        reused = resumed_chunks.get(chunk_id)
        stored = (
            _localize_reused_chunk(reused, raw_directory=raw_directory)
            if reused is not None
            else store_chunk_atomic(
                raw_directory,
                _download_blocks(
                    client,
                    export_uuid=actual_export_uuid,
                    chunk_id=chunk_id,
                    asset=False,
                ),
                chunk_id=chunk_id,
            )
        )
        stored_chunks.append(stored)

    manifest = {
        "schema_version": 2,
        "run_id": actual_run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "source": "tenable_vm_vulnerabilities",
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
        source="tenable_vm_vulnerabilities",
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
        Path(output_root)
        / "snapshots"
        / profile.client_id
        / actual_run_id
        / (
            f"tenable_vm_vulnerabilities-{snapshot_suffix}.snapshot.json"
            if snapshot_suffix else "tenable_vm_vulnerabilities.snapshot.json"
        )
    )
    snapshot.write_json(snapshot_path)
    return CollectionResult(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        raw_manifest_path=manifest_path,
        records=(),
    )


def collect_vm_snapshot_by_state(
    *,
    client: TenableVmClient,
    profile: ClientProfile,
    request: VulnerabilityExportRequest,
    output_root: str | Path,
    run_id: str | None = None,
    export_uuid: str | None = None,
    minimum_free_gb: int = 10,
    last_success_bytes: int | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> CollectionResult:
    actual_run_id = run_id or str(uuid.uuid4())
    raw_states = request.filters.get("state")
    values = [raw_states] if isinstance(raw_states, str) else list(raw_states or ())
    states = tuple(str(value).strip().upper() for value in values if str(value).strip())
    active_states = tuple(
        state for state in states if state in {"OPEN", "REOPENED"}
    )
    should_split = (
        export_uuid is None
        and bool(active_states)
        and "FIXED" in states
        and set(states).issubset({"OPEN", "REOPENED", "FIXED"})
    )
    if not should_split:
        return collect_vm_snapshot(
            client=client,
            profile=profile,
            request=request,
            output_root=output_root,
            run_id=actual_run_id,
            export_uuid=export_uuid,
            minimum_free_gb=minimum_free_gb,
            last_success_bytes=last_success_bytes,
            progress_callback=progress_callback,
        )

    segments = (
        ("active", "last_found", active_states),
        ("fixed", "last_fixed", ("FIXED",)),
    )
    collected: list[tuple[str, str, CollectionResult]] = []
    for segment_name, date_field, segment_states in segments:
        filters = dict(request.filters)
        filters["state"] = list(segment_states)
        segment_request = VulnerabilityExportRequest(
            filters=filters,
            num_assets=request.num_assets,
            include_unlicensed=request.include_unlicensed,
            include_software_vulns=request.include_software_vulns,
            include_plugin_output=request.include_plugin_output,
            properties=request.properties,
        )

        def forward_progress(
            event: Mapping[str, Any],
            *,
            name: str = segment_name,
            field: str = date_field,
        ) -> None:
            if progress_callback is not None:
                progress_callback({**event, "segment": name, "date_field": field})

        result = collect_vm_snapshot(
            client=client,
            profile=profile,
            request=segment_request,
            output_root=output_root,
            run_id=actual_run_id,
            minimum_free_gb=minimum_free_gb,
            last_success_bytes=last_success_bytes,
            progress_callback=forward_progress,
            snapshot_suffix=segment_name,
        )
        collected.append((segment_name, date_field, result))

    aggregate_chunks: list[dict[str, Any]] = []
    segment_metadata: list[dict[str, Any]] = []
    for segment_name, date_field, result in collected:
        child_manifest = json.loads(
            result.raw_manifest_path.read_text(encoding="utf-8")
        )
        child_chunks = child_manifest.get("chunks")
        if not isinstance(child_chunks, list):
            raise ValueError("Manifesto de segmento VM nao contem chunks validos.")
        segment_metadata.append({
            "name": segment_name,
            "date_field": date_field,
            "export_uuid": result.snapshot.export_uuid,
            "record_count": result.snapshot.record_count,
            "query": result.snapshot.query,
            "manifest_uri": result.raw_manifest_path.resolve().as_uri(),
        })
        for child in child_chunks:
            if not isinstance(child, Mapping):
                continue
            aggregate_chunks.append({
                **dict(child),
                "chunk_id": len(aggregate_chunks) + 1,
                "source_chunk_id": int(child.get("chunk_id") or 0),
                "segment": segment_name,
                "date_field": date_field,
            })

    aggregate_query = {
        "strategy": "state_temporal_split_v1",
        "segments": segment_metadata,
    }
    raw_directory = (
        Path(output_root) / "raw" / profile.client_id / actual_run_id
        / "tenable_vm_vulnerabilities"
    )
    manifest = {
        "schema_version": 3,
        "run_id": actual_run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "source": "tenable_vm_vulnerabilities",
        "strategy": "state_temporal_split_v1",
        "segments": segment_metadata,
        "chunks": aggregate_chunks,
    }
    manifest_path = raw_directory / "manifest.json"
    _write_exclusive(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    record_count = sum(item.snapshot.record_count for _, _, item in collected)
    export_uuids = "+".join(item.snapshot.export_uuid for _, _, item in collected)
    snapshot = build_source_snapshot_from_chunk_hashes(
        run_id=actual_run_id,
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        source="tenable_vm_vulnerabilities",
        export_uuid=export_uuids,
        query=aggregate_query,
        chunk_hashes=(
            (int(item["chunk_id"]), str(item.get("content_sha256") or ""))
            for item in aggregate_chunks
        ),
        record_count=record_count,
        started_at=min(item.snapshot.started_at for _, _, item in collected),
        collector_version=__version__,
        raw_manifest_uri=manifest_path.resolve().as_uri(),
    )
    snapshot_path = (
        Path(output_root) / "snapshots" / profile.client_id / actual_run_id
        / "tenable_vm_vulnerabilities.snapshot.json"
    )
    snapshot.write_json(snapshot_path)
    return CollectionResult(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        raw_manifest_path=manifest_path,
        records=(),
    )


def collect_asset_snapshot(
    *,
    client: TenableVmClient,
    profile: ClientProfile,
    request: AssetExportRequest,
    output_root: str | Path,
    run_id: str | None = None,
    export_uuid: str | None = None,
    resume_from: str | Path | None = None,
    minimum_free_gb: int = 10,
    last_success_bytes: int | None = None,
) -> CollectionResult:
    actual_run_id = run_id or str(uuid.uuid4())
    started_at = utc_now_iso()
    query = request.to_api_query()
    resumed_export_uuid, resumed_chunks = _load_resume_chunks(
        resume_from,
        source="tenable_vm_assets_v2",
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
    )
    actual_export_uuid = export_uuid or resumed_export_uuid or client.start_asset_export_v2(
        filters=request.filters or None,
        chunk_size=request.chunk_size,
        include_open_ports=request.include_open_ports,
        include_resource_tags=request.include_resource_tags,
    )
    _, chunk_ids = client.wait_for_asset_completion(actual_export_uuid)

    raw_directory = (
        Path(output_root)
        / "raw"
        / profile.client_id
        / actual_run_id
        / "tenable_vm_assets_v2"
        / actual_export_uuid
    )
    stored_chunks: list[StoredChunk] = []
    for chunk_id in chunk_ids:
        storage_preflight(
            raw_directory,
            last_success_bytes=last_success_bytes,
            minimum_free_gb=minimum_free_gb,
        )
        reused = resumed_chunks.get(chunk_id)
        stored = (
            _localize_reused_chunk(reused, raw_directory=raw_directory)
            if reused is not None
            else store_chunk_atomic(
                raw_directory,
                _download_blocks(
                    client,
                    export_uuid=actual_export_uuid,
                    chunk_id=chunk_id,
                    asset=True,
                ),
                chunk_id=chunk_id,
            )
        )
        stored_chunks.append(stored)

    manifest = {
        "schema_version": 2,
        "run_id": actual_run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "source": "tenable_vm_assets_v2",
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
        source="tenable_vm_assets_v2",
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
        Path(output_root)
        / "snapshots"
        / profile.client_id
        / actual_run_id
        / "tenable_vm_assets_v2.snapshot.json"
    )
    snapshot.write_json(snapshot_path)
    return CollectionResult(
        snapshot=snapshot,
        snapshot_path=snapshot_path,
        raw_manifest_path=manifest_path,
        records=(),
    )
