from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tenable_reports import __version__
from tenable_reports.application.collect import CollectionResult
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.models import utc_now_iso
from tenable_reports.domain.was import WasNormalizationResult, normalize_was_findings
from tenable_reports.application.normalize import _collection_records
from tenable_reports.infrastructure.jsonl_io import write_jsonl_gzip_exclusive


@dataclass(frozen=True, slots=True)
class NormalizedWasSnapshotResult:
    result: WasNormalizationResult
    findings_path: Path
    manifest_path: Path


def _write_exclusive(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def normalize_was_collection(
    *, profile: ClientProfile, collection: CollectionResult, output_root: str | Path
) -> NormalizedWasSnapshotResult:
    snapshot = collection.snapshot
    if snapshot.client_id != profile.client_id or snapshot.tenant_id != profile.tenant_id:
        raise ValueError("Snapshot WAS nao pertence ao perfil selecionado.")
    if snapshot.source != "tenable_was_findings":
        raise ValueError("A fonte precisa ser tenable_was_findings.")
    normalized = normalize_was_findings(
        _collection_records(collection), client_id=profile.client_id
    )
    directory = Path(output_root) / "normalized" / profile.client_id / snapshot.run_id
    findings_path = directory / "was-findings.jsonl.gz"
    manifest_path = directory / "was-manifest.json"
    for path in (findings_path, manifest_path):
        if path.exists():
            raise FileExistsError(f"Snapshot WAS normalizado imutavel ja existe: {path}")
    artifact = write_jsonl_gzip_exclusive(
        findings_path,
        (
            item.to_dict()
            for item in sorted(normalized.findings, key=lambda value: value.finding_key)
        ),
    )
    manifest = {
        "schema_version": 1,
        "normalizer_version": __version__,
        "created_at": utc_now_iso(),
        "run_id": snapshot.run_id,
        "client_id": profile.client_id,
        "tenant_id": profile.tenant_id,
        "source_snapshot": {
            "snapshot_id": snapshot.snapshot_id,
            "raw_sha256": snapshot.raw_sha256,
            "record_count": snapshot.record_count,
        },
        "reconciliation": {
            "raw_records": normalized.raw_records,
            "normalized_findings": len(normalized.findings),
            "rejected_records": normalized.rejected_records,
            "duplicate_records": normalized.duplicate_records,
        },
        "artifact": {
            "uri": findings_path.resolve().as_uri(),
            "records": artifact.records,
            "logical_bytes": artifact.logical_bytes,
            "stored_bytes": artifact.stored_bytes,
            "compression": "gzip",
            "sha256": artifact.sha256,
        },
    }
    _write_exclusive(manifest_path, (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return NormalizedWasSnapshotResult(normalized, findings_path, manifest_path)
