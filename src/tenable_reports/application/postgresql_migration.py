from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tenable_reports.application.history import SQLiteSnapshotRepository
from tenable_reports.infrastructure.postgresql import (
    ArtifactRecord,
    PostgresOperationsRepository,
    PostgresSnapshotRepository,
)


CATALOG_EXTENSIONS = {
    ".csv",
    ".db",
    ".docx",
    ".json",
    ".jsonl",
    ".pdf",
    ".sqlite",
    ".sqlite3",
}
EXCLUDED_DIRECTORY_NAMES = {
    ".git",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "cache",
    "crash",
    "lo-profile",
    "render",
}


@dataclass(frozen=True, slots=True)
class MigrationResult:
    history_files: int
    history_snapshots: int
    audit_files: int
    audit_records: int
    publication_manifests: int
    orchestration_manifests: int
    artifacts: int
    skipped_files: int

    def to_dict(self) -> dict[str, int]:
        return {
            "history_files": self.history_files,
            "history_snapshots": self.history_snapshots,
            "audit_files": self.audit_files,
            "audit_records": self.audit_records,
            "publication_manifests": self.publication_manifests,
            "orchestration_manifests": self.orchestration_manifests,
            "artifacts": self.artifacts,
            "skipped_files": self.skipped_files,
        }


@dataclass(frozen=True, slots=True)
class MainBackfillSourceState:
    used_history_run_ids: frozenset[str]
    existing_main_run_ids: frozenset[str]


def main_backfill_source_state(
    operations: PostgresOperationsRepository,
) -> MainBackfillSourceState:
    state = operations.retention_state()
    return MainBackfillSourceState(
        used_history_run_ids=frozenset(
            str(value) for value in state.get("history_confirmed_run_ids", ())
        ),
        existing_main_run_ids=frozenset(
            str(value) for value in state.get("main_run_ids", ())
        ),
    )


def _sqlite_tables(path: Path) -> tuple[str, ...]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "select name from sqlite_master "
            "where type = 'table' and name not like 'sqlite_%' order by name"
        ).fetchall()
    finally:
        connection.close()
    return tuple(str(row[0]) for row in rows)


def _read_audit_sqlite(path: Path, tables: Sequence[str]) -> tuple[dict[str, Any], int]:
    payload: dict[str, Any] = {"tables": {}}
    record_count = 0
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        for table in tables:
            escaped = table.replace('"', '""')
            rows = [dict(row) for row in connection.execute(f'SELECT * FROM "{escaped}"')]
            payload["tables"][table] = rows
            record_count += len(rows)
    finally:
        connection.close()
    return payload, record_count


def _iter_catalog_files(roots: Iterable[Path]) -> Iterable[tuple[Path, Path]]:
    seen: set[Path] = set()
    for root in roots:
        resolved_root = root.resolve()
        if not resolved_root.exists():
            continue
        candidates = (resolved_root,) if resolved_root.is_file() else resolved_root.rglob("*")
        for path in candidates:
            if not path.is_file() or (
                path.suffix.lower() not in CATALOG_EXTENSIONS
                and not path.name.lower().endswith(".jsonl.gz")
            ):
                continue
            if any(
                part.lower() in EXCLUDED_DIRECTORY_NAMES
                or part.lower().startswith("lo-profile-")
                for part in path.parts
            ):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield resolved_root, resolved


def _artifact_kind(path: Path) -> str:
    name = path.name.lower()
    if name == "publication-manifest.json":
        return "publication_manifest"
    if name == "orchestration-manifest.json":
        return "orchestration_manifest"
    if name == "report-dataset.json" or name == "report-dataset-with-history.json":
        return "report_dataset"
    if name == "manifest.json":
        return "collection_manifest"
    if name.endswith(".snapshot.json"):
        return "api_snapshot"
    if path.name.lower().endswith(".jsonl.gz"):
        return "jsonl_data_gzip"
    if path.suffix.lower() == ".jsonl":
        return "jsonl_data"
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        return "legacy_sqlite"
    return path.suffix.lower().lstrip(".") or "file"


def _identity_from_path(path: Path) -> tuple[str | None, str | None]:
    parts = list(path.parts)
    client_id: str | None = None
    run_id: str | None = None
    for marker in ("raw", "snapshots", "normalized", "report-datasets", "reports"):
        if marker in parts:
            index = parts.index(marker)
            if len(parts) > index + 1:
                client_id = parts[index + 1]
            if len(parts) > index + 2:
                run_id = parts[index + 2]
            break
    return client_id, run_id


def migrate_legacy_state(
    *,
    roots: Sequence[str | Path],
    snapshots: PostgresSnapshotRepository,
    operations: PostgresOperationsRepository,
) -> MigrationResult:
    history_files = 0
    history_snapshots = 0
    audit_files = 0
    audit_records = 0
    publication_manifests = 0
    orchestration_manifests = 0
    artifacts: list[ArtifactRecord] = []
    skipped_files = 0

    for root, path in _iter_catalog_files(Path(item) for item in roots):
        client_id, run_id = _identity_from_path(path)
        artifacts.append(
            ArtifactRecord.from_file(
                path,
                kind=_artifact_kind(path),
                client_id=client_id,
                run_id=run_id,
                source_root=root,
            )
        )
        if path.name.lower() == "publication-manifest.json":
            operations.record_publication_manifest(path)
            publication_manifests += 1
            continue
        if path.name.lower() == "orchestration-manifest.json":
            operations.record_orchestration_manifest(path)
            orchestration_manifests += 1
            continue
        if path.suffix.lower() not in {".sqlite", ".sqlite3", ".db"}:
            continue
        try:
            tables = _sqlite_tables(path)
            if "history_snapshots" in tables:
                legacy = SQLiteSnapshotRepository(path)
                values = legacy.all_snapshots()
                for snapshot in values:
                    snapshots.publish(snapshot)
                operations.record_legacy_sqlite(
                    path=path,
                    source_kind="history",
                    record_count=len(values),
                    payload={"tables": list(tables)},
                )
                history_files += 1
                history_snapshots += len(values)
            elif tables:
                payload, count = _read_audit_sqlite(path, tables)
                operations.record_legacy_sqlite(
                    path=path,
                    source_kind="audit",
                    record_count=count,
                    payload=payload,
                )
                audit_files += 1
                audit_records += count
            else:
                skipped_files += 1
        except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError):
            skipped_files += 1

    registered = operations.register_artifacts(artifacts)
    return MigrationResult(
        history_files=history_files,
        history_snapshots=history_snapshots,
        audit_files=audit_files,
        audit_records=audit_records,
        publication_manifests=publication_manifests,
        orchestration_manifests=orchestration_manifests,
        artifacts=registered,
        skipped_files=skipped_files,
    )
