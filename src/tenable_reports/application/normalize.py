from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from tenable_reports import __version__
from tenable_reports.application.collect import CollectionResult
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import utc_now_iso
from tenable_reports.domain.normalization import NormalizationResult, normalize_and_link
from tenable_reports.infrastructure.tenable_vm.parser import iter_chunk_records
from tenable_reports.infrastructure.jsonl_io import (
    JsonlWriteResult,
    write_jsonl_gzip_exclusive,
)


@dataclass(frozen=True, slots=True)
class NormalizedSnapshotResult:
    result: NormalizationResult
    directory: Path
    manifest_path: Path
    assets_path: Path
    findings_path: Path
    quality_issues_path: Path


def _write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _artifact(result: JsonlWriteResult) -> dict[str, Any]:
    return {
        "uri": result.path.resolve().as_uri(),
        "records": result.records,
        "logical_bytes": result.logical_bytes,
        "stored_bytes": result.stored_bytes,
        "compression": "gzip",
        "sha256": result.sha256,
    }


def _manifest_path(value: str) -> Path:
    parsed = urlparse(value)
    if parsed.scheme != "file":
        return Path(value)
    raw_path = unquote(parsed.path)
    if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    return Path(url2pathname(raw_path))


def _collection_records(collection: CollectionResult) -> Iterable[dict[str, Any]]:
    if collection.records:
        return collection.records
    try:
        manifest = json.loads(collection.raw_manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(
            f"Manifesto bruto não encontrado: {collection.raw_manifest_path}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise ValueError("Manifesto bruto inválido.") from exc
    chunks = manifest.get("chunks") if isinstance(manifest, dict) else None
    if not isinstance(chunks, list):
        raise ValueError("Manifesto bruto não contém chunks válidos.")
    ordered_chunks = tuple(sorted(
        (item for item in chunks if isinstance(item, dict)),
        key=lambda item: int(item.get("chunk_id") or 0),
    ))

    def records() -> Iterable[dict[str, Any]]:
        for item in ordered_chunks:
            path = _manifest_path(str(item.get("path") or ""))
            yield from iter_chunk_records(path)

    return records()


def filter_records_to_asset_scope(
    *,
    asset_records: Iterable[dict[str, Any]],
    finding_records: Iterable[dict[str, Any]],
    allowed_asset_ids: frozenset[str],
) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    assets = tuple(
        record for record in asset_records
        if str(record.get("id") or record.get("uuid") or "") in allowed_asset_ids
    )
    findings: list[dict[str, Any]] = []
    for record in finding_records:
        asset = record.get("asset")
        asset_id = (
            asset.get("uuid") or asset.get("id")
            if isinstance(asset, dict)
            else record.get("asset_uuid")
        )
        if str(asset_id or "") in allowed_asset_ids:
            findings.append(record)
    return assets, tuple(findings)


def normalize_collections(
    *,
    profile: ClientProfile,
    asset_collection: CollectionResult,
    finding_collection: CollectionResult,
    output_root: str | Path,
    allowed_asset_ids: frozenset[str] | None = None,
) -> NormalizedSnapshotResult:
    asset_snapshot = asset_collection.snapshot
    finding_snapshot = finding_collection.snapshot
    if asset_snapshot.run_id != finding_snapshot.run_id:
        raise ValueError("Snapshots de ativos e findings precisam pertencer ao mesmo run_id.")
    if asset_snapshot.client_id != profile.client_id or finding_snapshot.client_id != profile.client_id:
        raise ValueError("Snapshots nao pertencem ao cliente do perfil selecionado.")
    if asset_snapshot.tenant_id != profile.tenant_id or finding_snapshot.tenant_id != profile.tenant_id:
        raise ValueError("Snapshots nao pertencem ao tenant do perfil selecionado.")
    if asset_snapshot.source != "tenable_vm_assets_v2":
        raise ValueError("A fonte de ativos deve ser tenable_vm_assets_v2.")
    if finding_snapshot.source != "tenable_vm_vulnerabilities":
        raise ValueError("A fonte de findings deve ser tenable_vm_vulnerabilities.")

    input_asset_records = _collection_records(asset_collection)
    input_finding_records = _collection_records(finding_collection)
    asset_records = input_asset_records
    finding_records = input_finding_records
    if allowed_asset_ids is not None:
        asset_records, finding_records = filter_records_to_asset_scope(
            asset_records=asset_records,
            finding_records=finding_records,
            allowed_asset_ids=allowed_asset_ids,
        )

    normalized = normalize_and_link(
        asset_records=asset_records,
        finding_records=finding_records,
        client_id=profile.client_id,
    )
    directory = (
        Path(output_root)
        / "normalized"
        / profile.client_id
        / asset_snapshot.run_id
    )
    assets_path = directory / "assets.jsonl.gz"
    findings_path = directory / "findings.jsonl.gz"
    quality_path = directory / "quality-issues.jsonl.gz"
    manifest_path = directory / "manifest.json"
    for path in (assets_path, findings_path, quality_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"Snapshot normalizado imutavel ja existe: {path}")

    assets = sorted(normalized.assets, key=lambda item: item.source_asset_id)
    findings = sorted(normalized.findings, key=lambda item: item.finding_key)
    issues = sorted(
        normalized.issues,
        key=lambda item: (item.source, item.record_index, item.code, item.source_id or ""),
    )
    assets_artifact = write_jsonl_gzip_exclusive(
        assets_path, (item.to_dict() for item in assets)
    )
    findings_artifact = write_jsonl_gzip_exclusive(
        findings_path, (item.to_dict() for item in findings)
    )
    quality_artifact = write_jsonl_gzip_exclusive(
        quality_path, (item.to_dict() for item in issues)
    )

    warning_count = sum(item.severity.value == "WARNING" for item in issues)
    error_count = sum(item.severity.value == "ERROR" for item in issues)
    manifest = {
        "schema_version": 1,
        "normalizer_version": __version__,
        "run_id": asset_snapshot.run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "created_at": utc_now_iso(),
        "identity_contract": {
            "asset": "client_id + Tenable asset.id",
            "finding": "asset.uuid + plugin.id + port.port + port.protocol",
            "link": "finding.asset.uuid == asset.id",
            "ip_hostname_fallback": False,
        },
        "tag_scope": {
            "applied": allowed_asset_ids is not None,
            "selected_asset_count": len(allowed_asset_ids) if allowed_asset_ids is not None else None,
            "input_asset_records": asset_snapshot.record_count,
            "scoped_asset_records": normalized.reconciliation.raw_asset_records,
            "excluded_asset_records": max(
                0,
                asset_snapshot.record_count
                - normalized.reconciliation.raw_asset_records,
            ),
            "input_finding_records": finding_snapshot.record_count,
            "scoped_finding_records": normalized.reconciliation.raw_finding_records,
            "excluded_finding_records": max(
                0,
                finding_snapshot.record_count
                - normalized.reconciliation.raw_finding_records,
            ),
        },
        "source_snapshots": [
            {
                "source": asset_snapshot.source,
                "snapshot_id": asset_snapshot.snapshot_id,
                "raw_sha256": asset_snapshot.raw_sha256,
                "record_count": asset_snapshot.record_count,
            },
            {
                "source": finding_snapshot.source,
                "snapshot_id": finding_snapshot.snapshot_id,
                "raw_sha256": finding_snapshot.raw_sha256,
                "record_count": finding_snapshot.record_count,
            },
        ],
        "reconciliation": normalized.reconciliation.to_dict(),
        "quality": {
            "warnings": warning_count,
            "errors": error_count,
        },
        "artifacts": {
            "assets": _artifact(assets_artifact),
            "findings": _artifact(findings_artifact),
            "quality_issues": _artifact(quality_artifact),
        },
    }
    _write_exclusive(
        manifest_path,
        (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )
    return NormalizedSnapshotResult(
        result=normalized,
        directory=directory,
        manifest_path=manifest_path,
        assets_path=assets_path,
        findings_path=findings_path,
        quality_issues_path=quality_path,
    )
