from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    SOURCE_FAILED = "SOURCE_FAILED"
    NO_DATA = "NO_DATA"


class SnapshotStatus(StrEnum):
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


SENSITIVE_KEY_MARKERS = (
    "access",
    "secret",
    "password",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "x-apikeys",
    "plugin_output_text",
    "output_text",
)


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sanitized_mapping(value: Any) -> Any:
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            name = str(key)
            normalized = name.strip().lower().replace("-", "_")
            if any(marker in normalized for marker in SENSITIVE_KEY_MARKERS):
                clean[name] = "[REDACTED]"
            else:
                clean[name] = sanitized_mapping(child)
        return clean
    if isinstance(value, (list, tuple)):
        return [sanitized_mapping(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    snapshot_id: str
    run_id: str
    client_id: str
    tenant_id: str
    source: str
    export_uuid: str
    status: SnapshotStatus
    availability: Availability
    started_at: str
    completed_at: str
    record_count: int
    chunk_ids: tuple[int, ...]
    raw_sha256: str
    raw_manifest_uri: str
    query: dict[str, Any]
    collector_version: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["availability"] = self.availability.value
        data["chunk_ids"] = list(self.chunk_ids)
        return data

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        # Snapshots publicados sao imutaveis. O mesmo run_id nao pode
        # sobrescrever silenciosamente evidencia de uma coleta anterior.
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n")
        return output


def build_source_snapshot(
    *,
    run_id: str,
    client_id: str,
    tenant_id: str,
    source: str,
    export_uuid: str,
    query: Mapping[str, Any],
    chunks: Iterable[tuple[int, bytes]],
    record_count: int,
    started_at: str,
    collector_version: str,
    raw_manifest_uri: str,
) -> SourceSnapshot:
    ordered_chunks = sorted((int(chunk_id), bytes(content)) for chunk_id, content in chunks)
    digest = hashlib.sha256()
    for chunk_id, content in ordered_chunks:
        digest.update(str(chunk_id).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    availability = Availability.AVAILABLE if record_count else Availability.NO_DATA
    return SourceSnapshot(
        snapshot_id=str(uuid.uuid4()),
        run_id=run_id,
        client_id=client_id,
        tenant_id=tenant_id,
        source=source,
        export_uuid=export_uuid,
        status=SnapshotStatus.COMPLETE,
        availability=availability,
        started_at=started_at,
        completed_at=utc_now_iso(),
        record_count=record_count,
        chunk_ids=tuple(chunk_id for chunk_id, _ in ordered_chunks),
        raw_sha256=digest.hexdigest(),
        raw_manifest_uri=raw_manifest_uri,
        query=sanitized_mapping(dict(query)),
        collector_version=collector_version,
    )


def build_source_snapshot_from_chunk_hashes(
    *,
    run_id: str,
    client_id: str,
    tenant_id: str,
    source: str,
    export_uuid: str,
    query: Mapping[str, Any],
    chunk_hashes: Iterable[tuple[int, str]],
    record_count: int,
    started_at: str,
    collector_version: str,
    raw_manifest_uri: str,
) -> SourceSnapshot:
    ordered = sorted((int(chunk_id), str(content_hash)) for chunk_id, content_hash in chunk_hashes)
    digest = hashlib.sha256()
    for chunk_id, content_hash in ordered:
        digest.update(str(chunk_id).encode("ascii"))
        digest.update(b"\0")
        digest.update(content_hash.encode("ascii"))
        digest.update(b"\0")
    return SourceSnapshot(
        snapshot_id=str(uuid.uuid4()),
        run_id=run_id,
        client_id=client_id,
        tenant_id=tenant_id,
        source=source,
        export_uuid=export_uuid,
        status=SnapshotStatus.COMPLETE,
        availability=Availability.AVAILABLE if record_count else Availability.NO_DATA,
        started_at=started_at,
        completed_at=utc_now_iso(),
        record_count=record_count,
        chunk_ids=tuple(chunk_id for chunk_id, _ in ordered),
        raw_sha256=digest.hexdigest(),
        raw_manifest_uri=raw_manifest_uri,
        query=sanitized_mapping(dict(query)),
        collector_version=collector_version,
    )
