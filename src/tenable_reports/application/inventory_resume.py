from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tenable_reports.application.collect import _path_from_uri, reusable_chunk
from tenable_reports.infrastructure.tenable_vm.parser import iter_chunk_records


@dataclass(frozen=True, slots=True)
class InventoryResumeState:
    chunks: tuple[dict[str, Any], ...]
    records: tuple[dict[str, Any], ...]
    complete: bool


def load_inventory_resume_state(
    *,
    final_manifest: Path,
    partial_manifest: Path,
    client_id: str,
    tenant_id: str,
    run_id: str,
    segment: str,
    filters: Sequence[Mapping[str, Any]],
) -> InventoryResumeState:
    candidate = final_manifest if final_manifest.is_file() else partial_manifest
    if not candidate.is_file():
        return InventoryResumeState((), (), False)
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Manifesto Inventory de retomada invalido: {candidate}") from exc
    expected = {
        "source": "tenable_inventory_findings",
        "client_id": client_id,
        "tenant_id": tenant_id,
        "run_id": run_id,
        "segment": segment,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError("Manifesto Inventory nao pertence a esta coleta.")
    if payload.get("filters") != [dict(item) for item in filters]:
        raise ValueError("Filtros Inventory mudaram; a coleta parcial nao pode ser retomada.")

    chunks: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    raw_chunks = payload.get("chunks")
    if not isinstance(raw_chunks, list):
        raise ValueError("Manifesto Inventory nao contem chunks validos.")
    for item in sorted(
        (value for value in raw_chunks if isinstance(value, Mapping)),
        key=lambda value: int(value.get("chunk_id") or 0),
    ):
        content_hash = str(item.get("content_sha256") or "")
        storage_hash = str(item.get("storage_sha256") or "") or None
        if not content_hash:
            raise ValueError("Chunk Inventory sem hash de conteudo.")
        stored = reusable_chunk(
            _path_from_uri(str(item.get("path") or "")),
            expected_sha256=content_hash,
            expected_storage_sha256=storage_hash,
            chunk_id=int(item.get("chunk_id") or 0),
        )
        if stored is None:
            raise ValueError("Chunk Inventory parcial falhou na validacao de integridade.")
        chunks.append(stored.to_manifest())
        records.extend(dict(record) for record in iter_chunk_records(stored.path))
    return InventoryResumeState(
        chunks=tuple(chunks),
        records=tuple(records),
        complete=(candidate == final_manifest and payload.get("status") == "FINISHED"),
    )
