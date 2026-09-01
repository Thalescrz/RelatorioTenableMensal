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
from tenable_reports.domain.execution_control import ExecutionInterruptedError
from tenable_reports.domain.models import (
    SourceSnapshot,
    build_source_snapshot_from_chunk_hashes,
    sanitized_mapping,
    utc_now_iso,
)
from tenable_reports.application.storage_guard import storage_preflight
from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    ExportJob,
    ExportTimeoutError,
    FAILURE_STATES,
    SUCCESS_STATES,
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


def _resumable_export_is_available(
    client: Any,
    export_uuid: str,
    resumed_chunks: Mapping[int, StoredChunk],
) -> bool:
    status_loader = getattr(client, "get_export_status", None)
    if not callable(status_loader):
        return True
    try:
        status = status_loader(export_uuid)
    except ApiError as exc:
        if exc.status_code == 404:
            return False
        raise
    state = str(status.get("status") or "").strip().lower()
    if state in FAILURE_STATES or state == "aborted":
        return False
    if state not in SUCCESS_STATES:
        return True

    available_chunks = set(TenableVmClient.completed_chunk_ids(status))
    persisted_chunks = {int(chunk_id) for chunk_id in resumed_chunks}
    total_chunks = max(
        TenableVmClient.chunk_count(status, "total_chunks"),
        TenableVmClient.chunk_count(status, "finished_chunks"),
        TenableVmClient.chunk_count(status, "completed_chunks"),
    )
    if total_chunks == 0:
        return True
    return len(available_chunks | persisted_chunks) >= total_chunks


def find_resumable_vm_manifest(
    output_root: str | Path,
    *,
    profile: ClientProfile,
    request: VulnerabilityExportRequest,
    logical_job_id: str | None,
) -> Path | None:
    if not logical_job_id:
        return None
    query = request.to_api_query()
    expected_query_sha256 = hashlib.sha256(
        json.dumps(
            query, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    search_root = Path(output_root) / "raw" / profile.client_id
    if not search_root.is_dir():
        return None
    candidates = sorted(
        search_root.rglob("manifest.partial.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        state_path = candidate.with_name("export-state.json")
        try:
            export_state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            export_state = {}
        remote_status = str(export_state.get("status") or "").upper()
        if (
            remote_status in {
                "CANCELLED", "CANCELED", "FAILED", "ERROR", "ABORTED",
            }
            or bool(export_state.get("auto_cancelled"))
        ):
            continue
        if (
            payload.get("source") != "tenable_vm_vulnerabilities"
            or payload.get("client_id") != profile.client_id
            or payload.get("tenant_id") != profile.tenant_id
            or payload.get("logical_job_id") != logical_job_id
            or payload.get("query_sha256") != expected_query_sha256
        ):
            continue
        export_uuid, reusable = _load_resume_chunks(
            candidate,
            source="tenable_vm_vulnerabilities",
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
        )
        if export_uuid:
            return candidate
    return None


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
    logical_job_id: str | None = None,
    minimum_free_gb: int = 10,
    last_success_bytes: int | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    plugin_catalog_callback: Callable[[Iterable[Mapping[str, Any]]], None] | None = None,
    snapshot_suffix: str | None = None,
    cancellation_probe: Callable[[], bool] | None = None,
) -> CollectionResult:
    actual_run_id = run_id or str(uuid.uuid4())
    started_at = utc_now_iso()
    query = request.to_api_query()
    query_sha256 = hashlib.sha256(
        json.dumps(
            query, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    resumed_export_uuid, resumed_chunks = _load_resume_chunks(
        resume_from,
        source="tenable_vm_vulnerabilities",
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
    )
    if resumed_export_uuid and not _resumable_export_is_available(
        client,
        resumed_export_uuid,
        resumed_chunks,
    ):
        resumed_export_uuid = None
        resumed_chunks = {}
    provided_export_uuid = export_uuid
    if provided_export_uuid and not _resumable_export_is_available(
        client,
        provided_export_uuid,
        {},
    ):
        provided_export_uuid = None
    start_arguments = {
        "filters": request.filters,
        "num_assets": request.num_assets,
        "include_unlicensed": request.include_unlicensed,
        "include_software_vulns": request.include_software_vulns,
        "include_plugin_output": request.include_plugin_output,
        "properties": list(request.properties) or None,
    }
    if provided_export_uuid:
        job = ExportJob(export_uuid=provided_export_uuid, origin="provided")
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
    partial_manifest_path = raw_directory / "manifest.partial.json"
    manifest_path = raw_directory / "manifest.json"
    stored_chunks: dict[int, StoredChunk] = {}

    def manifest_payload(*, status: str) -> dict[str, Any]:
        return {
            "schema_version": 3,
            "run_id": actual_run_id,
            "logical_job_id": logical_job_id,
            "client_id": profile.client_id,
            "tenant_id": profile.tenant_id,
            "source": "tenable_vm_vulnerabilities",
            "strategy": "combined",
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
            "source": "tenable_vm_vulnerabilities",
            "export_uuid": actual_export_uuid,
            "origin": job.origin,
            "status": status,
            "started_at": started_at,
            "persisted_chunks": sorted(stored_chunks),
            "partial_manifest": (
                str(partial_manifest_path.resolve())
                if partial_manifest_path.exists() else None
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
        if cancellation_probe is not None and cancellation_probe():
            raise ExecutionInterruptedError(
                f"Execucao interrompida com export {actual_export_uuid} preservado para retomada.",
                export_uuid=actual_export_uuid,
                checkpoint=str(partial_manifest_path.resolve()),
            )
        storage_preflight(
            raw_directory,
            last_success_bytes=last_success_bytes,
            minimum_free_gb=minimum_free_gb,
        )
        reused = resumed_chunks.get(actual_chunk_id)
        stored = (
            _localize_reused_chunk(reused, raw_directory=raw_directory)
            if reused is not None
            else store_chunk_atomic(
                raw_directory,
                _download_blocks(
                    client,
                    export_uuid=actual_export_uuid,
                    chunk_id=actual_chunk_id,
                    asset=False,
                ),
                chunk_id=actual_chunk_id,
            )
        )
        if plugin_catalog_callback is not None:
            plugin_catalog_callback(iter_chunk_records(stored.path))
        stored_chunks[actual_chunk_id] = stored
        _write_json_replace(
            partial_manifest_path,
            manifest_payload(status="PROCESSING"),
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
        auto_cancelled=False,
        query=sanitized_mapping(query),
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
        wait_method = client.wait_for_completion
        parameters = inspect.signature(wait_method).parameters
        wait_arguments: dict[str, Any] = {}
        if "progress_callback" in parameters:
            wait_arguments["progress_callback"] = update_progress
        if "chunk_callback" in parameters:
            wait_arguments["chunk_callback"] = persist_chunk
        if "cancellation_probe" in parameters:
            wait_arguments["cancellation_probe"] = cancellation_probe
        _, chunk_ids = wait_method(actual_export_uuid, **wait_arguments)
    except ExecutionInterruptedError as exc:
        exc.export_uuid = exc.export_uuid or actual_export_uuid
        exc.checkpoint = exc.checkpoint or str(partial_manifest_path.resolve())
        emit_progress(
            "INTERRUPTED",
            completed_chunks=len(stored_chunks),
            total_chunks=len(stored_chunks),
            progress_made=bool(stored_chunks),
        )
        raise
    except ExportTimeoutError as exc:
        exc.export_uuid = actual_export_uuid
        exc.origin = job.origin
        exc.last_status = {
            **exc.last_status,
            "origin": job.origin,
            "query": sanitized_mapping(query),
            "persisted_chunks": sorted(stored_chunks),
            "partial_manifest": (
                str(partial_manifest_path.resolve())
                if partial_manifest_path.exists() else None
            ),
        }
        local_progress = bool(stored_chunks)
        exc.progress_made = bool(exc.progress_made or local_progress)
        auto_cancelled = False
        cancellation_error: str | None = None
        if job.created_by_current_run and (
            not exc.progress_made
            or exc.timeout_phase == "no_progress"
        ):
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
                "persisted_chunks", "partial_manifest", "completed_chunks",
            }
        }
        emit_progress(
            "TIMED_OUT",
            **timeout_details,
            completed_chunks=max(
                len(stored_chunks),
                int(exc.last_status.get("completed_chunks") or 0),
            ),
            persisted_chunks=sorted(stored_chunks),
            partial_manifest=(
                str(partial_manifest_path.resolve())
                if partial_manifest_path.exists() else None
            ),
            auto_cancelled=auto_cancelled,
            cancellation_error=cancellation_error,
            progress_made=exc.progress_made,
        )
        raise

    for chunk_id in chunk_ids:
        persist_chunk(chunk_id)

    emit_progress(
        "FINISHED",
        completed_chunks=len(stored_chunks),
        total_chunks=len(chunk_ids),
        progress_made=bool(stored_chunks),
        auto_cancelled=False,
    )
    stored_in_order = [stored_chunks[chunk_id] for chunk_id in sorted(stored_chunks)]
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
        source="tenable_vm_vulnerabilities",
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
    resume_from: str | Path | None = None,
    logical_job_id: str | None = None,
    strategy: str = "combined",
    minimum_free_gb: int = 10,
    last_success_bytes: int | None = None,
    plugin_catalog_callback: Callable[[Iterable[Mapping[str, Any]]], None] | None = None,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    snapshot_suffix: str | None = None,
    cancellation_probe: Callable[[], bool] | None = None,
) -> CollectionResult:
    actual_run_id = run_id or str(uuid.uuid4())
    normalized_strategy = str(strategy).strip().lower()
    if normalized_strategy not in {"combined", "split"}:
        raise ValueError("strategy deve ser combined ou split.")
    raw_states = request.filters.get("state")
    values = [raw_states] if isinstance(raw_states, str) else list(raw_states or ())
    states = tuple(str(value).strip().upper() for value in values if str(value).strip())
    active_states = tuple(
        state for state in states if state in {"OPEN", "REOPENED"}
    )
    should_split = (
        normalized_strategy == "split"
        and export_uuid is None
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
            resume_from=resume_from,
            logical_job_id=logical_job_id,
            minimum_free_gb=minimum_free_gb,
            last_success_bytes=last_success_bytes,
            plugin_catalog_callback=plugin_catalog_callback,
            progress_callback=progress_callback,
            snapshot_suffix=snapshot_suffix,
            cancellation_probe=cancellation_probe,
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
            logical_job_id=(
                f"{logical_job_id}:{segment_name}" if logical_job_id else None
            ),
            minimum_free_gb=minimum_free_gb,
            last_success_bytes=last_success_bytes,
            plugin_catalog_callback=plugin_catalog_callback,
            progress_callback=forward_progress,
            snapshot_suffix=(f"{snapshot_suffix}-{segment_name}" if snapshot_suffix else segment_name),
            cancellation_probe=cancellation_probe,
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
    cancellation_probe: Callable[[], bool] | None = None,
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
    wait_method = client.wait_for_asset_completion
    wait_arguments: dict[str, Any] = {}
    if "cancellation_probe" in inspect.signature(wait_method).parameters:
        wait_arguments["cancellation_probe"] = cancellation_probe
    _, chunk_ids = wait_method(actual_export_uuid, **wait_arguments)

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
        if cancellation_probe is not None and cancellation_probe():
            raise ExecutionInterruptedError(
                f"Execucao interrompida com export {actual_export_uuid} preservado para retomada.",
                export_uuid=actual_export_uuid,
            )
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
