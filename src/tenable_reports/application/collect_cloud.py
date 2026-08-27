"""Persistent, resumable Tenable Cloud Security collection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from tenable_reports.application.cloud_contract import CloudCapabilityReport
from tenable_reports.application.publishing import write_json_atomic
from tenable_reports.infrastructure.tenable_cloud.client import CloudGraphQLError
from tenable_reports.infrastructure.tenable_cloud.queries import (
    CLOUD_SOURCE_QUERIES,
    CloudQueryDefinition,
)


class CloudRequiredSourceError(RuntimeError):
    """A required Cloud source failed without affecting other report families."""

    failure_code = "TENABLE_CLOUD_REQUIRED"
    retryable = False

    def __init__(self, source: str, cause: Exception) -> None:
        super().__init__(f"Fonte Cloud obrigatoria falhou: {source}.")
        self.source = source
        self.retryable = bool(getattr(cause, "retryable", False))


class CloudCheckpointError(RuntimeError):
    """A Cloud checkpoint cannot be safely resumed."""


class CloudPageClient(Protocol):
    def paginate_pages(
        self,
        query: str,
        root_field: str,
        *,
        page_size: int,
        after: str | None = None,
        pages_completed: int = 0,
        records_completed: int = 0,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Any:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class CloudCollectionRequest:
    client_id: str
    tenant_id: str
    run_id: str
    execution_type: str
    output_root: Path
    collected_at: str

    def __post_init__(self) -> None:
        for label, value in (
            ("client_id", self.client_id),
            ("tenant_id", self.tenant_id),
            ("run_id", self.run_id),
            ("execution_type", self.execution_type),
        ):
            normalized = str(value or "").strip()
            if (
                not normalized
                or Path(normalized).name != normalized
                or "/" in normalized
                or "\\" in normalized
            ):
                raise ValueError(f"{label} Cloud invalido.")
        if not str(self.collected_at or "").strip():
            raise ValueError("collected_at Cloud nao pode ser vazio.")


@dataclass(frozen=True, slots=True)
class CloudSourceStatus:
    name: str
    required: bool
    status: str
    pages: int
    records: int
    sha256: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CloudCollectionArtifact:
    manifest_path: Path
    source_paths: Mapping[str, Path]
    source_status: Mapping[str, CloudSourceStatus]
    warnings: Sequence[Mapping[str, Any]]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonl_payload(records: Sequence[Mapping[str, Any]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                dict(record),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        for record in records
    )


def _write_bytes_atomic(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)
    return path


def _raw_directory(request: CloudCollectionRequest) -> Path:
    return (
        Path(request.output_root)
        / request.execution_type
        / "raw"
        / request.client_id
        / request.run_id
        / "tenable_cloud"
    )


def _checkpoint_path(directory: Path, source: str) -> Path:
    return directory / f"{source}.checkpoint.json"


def _chunk_path(directory: Path, source: str, page: int) -> Path:
    return directory / f".{source}.page-{page:06d}.jsonl"


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CloudCheckpointError(
            f"Checkpoint Cloud invalido: {path.name}."
        ) from exc
    if not isinstance(payload, Mapping):
        raise CloudCheckpointError(
            f"Checkpoint Cloud invalido: {path.name}."
        )
    return payload


def _validate_checkpoint(
    *,
    checkpoint: Mapping[str, Any],
    request: CloudCollectionRequest,
    definition: CloudQueryDefinition,
    endpoint: str,
    directory: Path,
) -> None:
    expected = {
        "client_id": request.client_id,
        "tenant_id": request.tenant_id,
        "run_id": request.run_id,
        "source": definition.name,
        "endpoint": endpoint,
        "query_version": definition.version,
    }
    if any(checkpoint.get(key) != value for key, value in expected.items()):
        raise CloudCheckpointError(
            f"Checkpoint Cloud incompativel para {definition.name}."
        )
    chunks = checkpoint.get("chunks")
    if not isinstance(chunks, list):
        raise CloudCheckpointError(
            f"Checkpoint Cloud sem paginas para {definition.name}."
        )
    if checkpoint.get("status") == "COMPLETE":
        source_path = directory / f"{definition.name}.jsonl"
        if (
            not source_path.is_file()
            or _sha256(source_path) != checkpoint.get("sha256")
        ):
            raise CloudCheckpointError(
                f"Artefato Cloud completo invalido para {definition.name}."
            )
        return
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise CloudCheckpointError(
                f"Checkpoint Cloud possui pagina invalida para {definition.name}."
            )
        name = str(chunk.get("path") or "")
        path = directory / name
        if (
            not name
            or Path(name).name != name
            or not path.is_file()
            or _sha256(path) != chunk.get("sha256")
        ):
            raise CloudCheckpointError(
                f"Checkpoint Cloud possui pagina corrompida para {definition.name}."
            )


def _initial_checkpoint(
    *,
    request: CloudCollectionRequest,
    definition: CloudQueryDefinition,
    endpoint: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "client_id": request.client_id,
        "tenant_id": request.tenant_id,
        "run_id": request.run_id,
        "source": definition.name,
        "root_field": definition.root_field,
        "endpoint": endpoint,
        "query_version": definition.version,
        "status": "PROCESSING",
        "cursor": None,
        "pages": 0,
        "records": 0,
        "chunks": [],
        "collected_at": request.collected_at,
    }


def _combine_chunks(
    *,
    directory: Path,
    source: str,
    chunks: Sequence[Mapping[str, Any]],
) -> Path:
    destination = directory / f"{source}.jsonl"
    temporary = destination.with_name(f".{destination.name}.tmp")
    with temporary.open("wb") as output:
        for chunk in chunks:
            chunk_path = directory / str(chunk["path"])
            with chunk_path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    output.write(block)
    temporary.replace(destination)
    return destination


def _emit(
    callback: Callable[[Mapping[str, Any]], None] | None,
    *,
    request: CloudCollectionRequest,
    source: str,
    status: str,
    pages: int,
    records: int,
    stage: str = "COLLECTION",
) -> None:
    if callback is not None:
        callback(
            {
                "event": "TENABLE_CLOUD_PROGRESS",
                "stage": stage,
                "source": source,
                "status": status,
                "pages": pages,
                "records": records,
                "run_id": request.run_id,
                "client_id": request.client_id,
            }
        )


def _collect_source(
    *,
    request: CloudCollectionRequest,
    definition: CloudQueryDefinition,
    endpoint: str,
    client: CloudPageClient,
    directory: Path,
    progress_callback: Callable[[Mapping[str, Any]], None] | None,
) -> tuple[Path, CloudSourceStatus]:
    checkpoint_path = _checkpoint_path(directory, definition.name)
    if checkpoint_path.is_file():
        checkpoint = dict(_load_json(checkpoint_path))
        try:
            _validate_checkpoint(
                checkpoint=checkpoint,
                request=request,
                definition=definition,
                endpoint=endpoint,
                directory=directory,
            )
        except CloudCheckpointError:
            _cleanup_optional_partial(directory, definition.name)
            checkpoint = _initial_checkpoint(
                request=request,
                definition=definition,
                endpoint=endpoint,
            )
            write_json_atomic(checkpoint_path, checkpoint)
    else:
        checkpoint = _initial_checkpoint(
            request=request,
            definition=definition,
            endpoint=endpoint,
        )
        write_json_atomic(checkpoint_path, checkpoint)

    pages = int(checkpoint.get("pages") or 0)
    records = int(checkpoint.get("records") or 0)
    cursor = checkpoint.get("cursor")
    cursor = str(cursor) if cursor is not None else None
    chunks = list(checkpoint.get("chunks") or [])
    source_path = directory / f"{definition.name}.jsonl"

    if checkpoint.get("status") == "COMPLETE":
        if not source_path.is_file() or _sha256(source_path) != checkpoint.get("sha256"):
            raise CloudCheckpointError(
                f"Artefato Cloud completo invalido para {definition.name}."
            )
        return source_path, CloudSourceStatus(
            name=definition.name,
            required=definition.required,
            status="COMPLETE",
            pages=pages,
            records=records,
            sha256=str(checkpoint["sha256"]),
        )

    _emit(
        progress_callback,
        request=request,
        source=definition.name,
        status="STARTED",
        pages=pages,
        records=records,
    )
    for page in client.paginate_pages(
        definition.query,
        definition.root_field,
        page_size=definition.page_size,
        after=cursor,
        pages_completed=pages,
        records_completed=records,
    ):
        page_nodes = tuple(dict(item) for item in page.nodes)
        page_number = pages + 1
        chunk_path = _chunk_path(directory, definition.name, page_number)
        _write_bytes_atomic(chunk_path, _jsonl_payload(page_nodes))
        chunks.append(
            {
                "page": page_number,
                "path": chunk_path.name,
                "records": len(page_nodes),
                "sha256": _sha256(chunk_path),
            }
        )
        pages = page_number
        records += len(page_nodes)
        cursor = page.end_cursor if page.has_next_page else None
        checkpoint.update(
            {
                "status": "PROCESSING",
                "cursor": cursor,
                "pages": pages,
                "records": records,
                "chunks": chunks,
            }
        )
        write_json_atomic(checkpoint_path, checkpoint)
        _emit(
            progress_callback,
            request=request,
            source=definition.name,
            status="PROCESSING",
            pages=pages,
            records=records,
        )

    source_path = _combine_chunks(
        directory=directory,
        source=definition.name,
        chunks=chunks,
    )
    digest = _sha256(source_path)
    checkpoint.update(
        {
            "status": "COMPLETE",
            "cursor": None,
            "pages": pages,
            "records": records,
            "sha256": digest,
            "chunks": chunks,
        }
    )
    write_json_atomic(checkpoint_path, checkpoint)
    _emit(
        progress_callback,
        request=request,
        source=definition.name,
        status="COMPLETE",
        pages=pages,
        records=records,
    )
    return source_path, CloudSourceStatus(
        name=definition.name,
        required=definition.required,
        status="COMPLETE",
        pages=pages,
        records=records,
        sha256=digest,
    )


def _cleanup_optional_partial(directory: Path, source: str) -> None:
    checkpoint_path = _checkpoint_path(directory, source)
    if checkpoint_path.is_file():
        checkpoint = _load_json(checkpoint_path)
        chunks = checkpoint.get("chunks")
        if isinstance(chunks, list):
            for chunk in chunks:
                if isinstance(chunk, Mapping):
                    name = str(chunk.get("path") or "")
                    if name and Path(name).name == name:
                        (directory / name).unlink(missing_ok=True)
        checkpoint_path.unlink(missing_ok=True)
    (directory / f"{source}.jsonl").unlink(missing_ok=True)


def _manifest_payload(
    *,
    request: CloudCollectionRequest,
    capabilities: CloudCapabilityReport,
    source_paths: Mapping[str, Path],
    statuses: Mapping[str, CloudSourceStatus],
    warnings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source": "tenable_cloud",
        "client_id": request.client_id,
        "tenant_id": request.tenant_id,
        "run_id": request.run_id,
        "execution_type": request.execution_type,
        "collected_at": request.collected_at,
        "endpoint": capabilities.endpoint,
        "connector_version": capabilities.connector_version,
        "status": "FINISHED",
        "sources": {
            name: {
                **asdict(status),
                "path": source_paths[name].name if name in source_paths else None,
            }
            for name, status in statuses.items()
        },
        "warnings": [
            {
                "source": str(item.get("source") or ""),
                "code": str(item.get("code") or ""),
            }
            for item in warnings
        ],
    }


def collect_cloud_snapshot(
    *,
    request: CloudCollectionRequest,
    clients: Mapping[str, CloudPageClient],
    capabilities: CloudCapabilityReport,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> CloudCollectionArtifact:
    """Collect every available Cloud source while isolating optional failures."""

    directory = _raw_directory(request)
    directory.mkdir(parents=True, exist_ok=True)
    source_paths: dict[str, Path] = {}
    statuses: dict[str, CloudSourceStatus] = {}
    warnings: list[Mapping[str, Any]] = []
    capability_by_name = {item.name: item for item in capabilities.sources}

    for definition in CLOUD_SOURCE_QUERIES.values():
        capability = capability_by_name.get(definition.name)
        if capability is None or capability.status != "AVAILABLE":
            if definition.required:
                cause = CloudCheckpointError(
                    f"Capacidade obrigatoria indisponivel: {definition.name}."
                )
                _emit(
                    progress_callback,
                    request=request,
                    source=definition.name,
                    status="FAILED",
                    pages=0,
                    records=0,
                )
                raise CloudRequiredSourceError(definition.name, cause)
            message = (
                capability.message
                if capability is not None
                else "Capacidade nao sondada."
            )
            statuses[definition.name] = CloudSourceStatus(
                name=definition.name,
                required=False,
                status="UNAVAILABLE",
                pages=0,
                records=0,
                message=message,
            )
            warnings.append(
                {"source": definition.name, "code": "CLOUD_SOURCE_UNAVAILABLE"}
            )
            continue

        client = clients.get(definition.name)
        if client is None:
            error: Exception = CloudCheckpointError(
                f"Cliente de coleta ausente para {definition.name}."
            )
        else:
            error = RuntimeError("unused")
        try:
            if client is None:
                raise error
            source_path, status = _collect_source(
                request=request,
                definition=definition,
                endpoint=capabilities.endpoint,
                client=client,
                directory=directory,
                progress_callback=progress_callback,
            )
        except CloudGraphQLError as exc:
            if definition.required:
                checkpoint = _checkpoint_path(directory, definition.name)
                pages = records = 0
                if checkpoint.is_file():
                    state = _load_json(checkpoint)
                    pages = int(state.get("pages") or 0)
                    records = int(state.get("records") or 0)
                _emit(
                    progress_callback,
                    request=request,
                    source=definition.name,
                    status="FAILED",
                    pages=pages,
                    records=records,
                )
                raise CloudRequiredSourceError(definition.name, exc) from exc
            _cleanup_optional_partial(directory, definition.name)
            statuses[definition.name] = CloudSourceStatus(
                name=definition.name,
                required=False,
                status="UNAVAILABLE",
                pages=0,
                records=0,
                message=str(exc),
            )
            warnings.append(
                {"source": definition.name, "code": "CLOUD_SOURCE_UNAVAILABLE"}
            )
            _emit(
                progress_callback,
                request=request,
                source=definition.name,
                status="UNAVAILABLE",
                pages=0,
                records=0,
            )
            continue
        except Exception as exc:
            if definition.required:
                _emit(
                    progress_callback,
                    request=request,
                    source=definition.name,
                    status="FAILED",
                    pages=0,
                    records=0,
                )
                raise CloudRequiredSourceError(definition.name, exc) from exc
            raise

        source_paths[definition.name] = source_path
        statuses[definition.name] = status

    manifest_path = write_json_atomic(
        directory / "manifest.json",
        _manifest_payload(
            request=request,
            capabilities=capabilities,
            source_paths=source_paths,
            statuses=statuses,
            warnings=warnings,
        ),
    )
    for checkpoint_path in directory.glob("*.checkpoint.json"):
        checkpoint = dict(_load_json(checkpoint_path))
        chunks = checkpoint.get("chunks")
        if isinstance(chunks, list):
            for chunk in chunks:
                if isinstance(chunk, Mapping):
                    name = str(chunk.get("path") or "")
                    if name and Path(name).name == name:
                        (directory / name).unlink(missing_ok=True)
        if checkpoint.get("status") == "COMPLETE":
            checkpoint["chunks"] = []
            write_json_atomic(checkpoint_path, checkpoint)
        else:
            checkpoint_path.unlink(missing_ok=True)
    _emit(
        progress_callback,
        request=request,
        source="tenable_cloud",
        status="FINISHED",
        pages=sum(item.pages for item in statuses.values()),
        records=sum(item.records for item in statuses.values()),
        stage="FINALIZATION",
    )
    return CloudCollectionArtifact(
        manifest_path=manifest_path,
        source_paths=dict(source_paths),
        source_status=dict(statuses),
        warnings=tuple(warnings),
    )


__all__ = [
    "CloudCheckpointError",
    "CloudCollectionArtifact",
    "CloudCollectionRequest",
    "CloudPageClient",
    "CloudRequiredSourceError",
    "CloudSourceStatus",
    "collect_cloud_snapshot",
]
