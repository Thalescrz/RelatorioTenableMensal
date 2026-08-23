from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

from tenable_reports.domain.normalization import (
    DataQualityIssue,
    NormalizedAsset,
    NormalizedFinding,
    QualitySeverity,
)


COMPACT_SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class CompactFindingSnapshot:
    snapshot_id: str
    schema_version: int
    client_id: str
    tenant_id: str
    run_id: str
    execution_type: str
    period_mode: str
    period_start_at: str
    period_end_at: str
    created_at: str
    content_sha256: str
    payload_gzip: bytes
    record_counts: Mapping[str, int]
    document_references: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ReplayedCompactSnapshot:
    assets: tuple[NormalizedAsset, ...]
    findings: tuple[NormalizedFinding, ...]
    quality_issues: tuple[DataQualityIssue, ...]
    tag_asset_ids: Mapping[str, tuple[str, ...]]
    document_references: Mapping[str, str]


class CompactSnapshotRepository(Protocol):
    def publish(self, snapshot: CompactFindingSnapshot) -> None: ...

    def find_exact(
        self,
        *,
        client_id: str,
        tenant_id: str,
        period_start_at: str,
        period_end_at: str,
    ) -> CompactFindingSnapshot | None: ...


class MemoryCompactSnapshotRepository:
    def __init__(self) -> None:
        self._snapshots: dict[str, CompactFindingSnapshot] = {}

    @property
    def count(self) -> int:
        return len(self._snapshots)

    def publish(self, snapshot: CompactFindingSnapshot) -> None:
        existing = self._snapshots.get(snapshot.snapshot_id)
        if existing is not None and existing != snapshot:
            raise ValueError("Snapshot compacto imutavel ja existe com conteudo diferente.")
        self._snapshots[snapshot.snapshot_id] = snapshot

    def find_exact(
        self,
        *,
        client_id: str,
        tenant_id: str,
        period_start_at: str,
        period_end_at: str,
    ) -> CompactFindingSnapshot | None:
        matches = [
            item for item in self._snapshots.values()
            if item.client_id == client_id
            and item.tenant_id == tenant_id
            and item.period_start_at == period_start_at
            and item.period_end_at == period_end_at
        ]
        return max(matches, key=lambda item: (item.created_at, item.run_id), default=None)


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _snapshot_id(
    *,
    client_id: str,
    tenant_id: str,
    run_id: str,
    period_start_at: str,
    period_end_at: str,
) -> str:
    identity = "|".join((
        str(COMPACT_SNAPSHOT_SCHEMA_VERSION),
        client_id,
        tenant_id,
        run_id,
        period_start_at,
        period_end_at,
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def build_compact_snapshot(
    *,
    client_id: str,
    tenant_id: str,
    run_id: str,
    execution_type: str,
    period_mode: str,
    period_start_at: str,
    period_end_at: str,
    assets: Sequence[NormalizedAsset],
    findings: Sequence[NormalizedFinding],
    quality_issues: Sequence[DataQualityIssue],
    tag_asset_ids: Mapping[str, Sequence[str]],
    document_references: Mapping[str, str],
    created_at: str | None = None,
) -> CompactFindingSnapshot:
    documents = {
        str(key): str(value)
        for key, value in document_references.items()
        if str(key).strip() and str(value).strip()
    }
    tags = {
        str(tag): sorted({str(asset_id) for asset_id in asset_ids if str(asset_id)})
        for tag, asset_ids in tag_asset_ids.items()
    }
    payload = {
        "schema_version": COMPACT_SNAPSHOT_SCHEMA_VERSION,
        "assets": [item.to_dict() for item in assets],
        "findings": [item.to_dict() for item in findings],
        "quality_issues": [item.to_dict() for item in quality_issues],
        "tag_asset_ids": tags,
        "document_references": documents,
    }
    logical = _canonical_json(payload)
    timestamp = created_at or datetime.now(timezone.utc).isoformat()
    counts = {
        "assets": len(assets),
        "findings": len(findings),
        "quality_issues": len(quality_issues),
    }
    return CompactFindingSnapshot(
        snapshot_id=_snapshot_id(
            client_id=client_id,
            tenant_id=tenant_id,
            run_id=run_id,
            period_start_at=period_start_at,
            period_end_at=period_end_at,
        ),
        schema_version=COMPACT_SNAPSHOT_SCHEMA_VERSION,
        client_id=client_id,
        tenant_id=tenant_id,
        run_id=run_id,
        execution_type=execution_type,
        period_mode=period_mode,
        period_start_at=period_start_at,
        period_end_at=period_end_at,
        created_at=timestamp,
        content_sha256=hashlib.sha256(logical).hexdigest(),
        payload_gzip=gzip.compress(logical, compresslevel=9, mtime=0),
        record_counts=counts,
        document_references=documents,
    )


def replay_compact_snapshot(
    snapshot: CompactFindingSnapshot,
) -> ReplayedCompactSnapshot:
    if snapshot.schema_version != COMPACT_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Versao de snapshot compacto nao suportada: {snapshot.schema_version}"
        )
    try:
        logical = gzip.decompress(snapshot.payload_gzip)
    except (OSError, EOFError) as exc:
        raise ValueError("Payload do snapshot compacto esta corrompido.") from exc
    if hashlib.sha256(logical).hexdigest() != snapshot.content_sha256:
        raise ValueError("Checksum do snapshot compacto nao confere.")
    try:
        payload = json.loads(logical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Payload do snapshot compacto nao e JSON valido.") from exc
    if int(payload.get("schema_version") or 0) != snapshot.schema_version:
        raise ValueError("Versao interna do snapshot compacto diverge do envelope.")
    assets = tuple(
        NormalizedAsset.from_dict(item)
        for item in payload.get("assets") or ()
        if isinstance(item, Mapping)
    )
    findings = tuple(
        NormalizedFinding.from_dict(item)
        for item in payload.get("findings") or ()
        if isinstance(item, Mapping)
    )
    issues = tuple(
        DataQualityIssue(
            code=str(item.get("code") or ""),
            severity=QualitySeverity(str(item.get("severity") or "WARNING")),
            source=str(item.get("source") or ""),
            record_index=int(item.get("record_index") or 0),
            message=str(item.get("message") or ""),
            source_id=(
                str(item.get("source_id"))
                if item.get("source_id") is not None else None
            ),
        )
        for item in payload.get("quality_issues") or ()
        if isinstance(item, Mapping)
    )
    tags = {
        str(tag): tuple(str(value) for value in values)
        for tag, values in (payload.get("tag_asset_ids") or {}).items()
        if isinstance(values, list)
    }
    documents = {
        str(key): str(value)
        for key, value in (payload.get("document_references") or {}).items()
    }
    expected = {
        "assets": len(assets),
        "findings": len(findings),
        "quality_issues": len(issues),
    }
    if dict(snapshot.record_counts) != expected:
        raise ValueError("Contagens do snapshot compacto divergem do payload.")
    if documents != dict(snapshot.document_references):
        raise ValueError("Referencias de documentos divergem do envelope compacto.")
    return ReplayedCompactSnapshot(
        assets=assets,
        findings=findings,
        quality_issues=issues,
        tag_asset_ids=tags,
        document_references=documents,
    )
