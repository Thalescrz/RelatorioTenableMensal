from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from tenable_reports.application.history import SnapshotRepository
from tenable_reports.config.database import DatabaseAdminConfig, DatabaseConfig
from tenable_reports.domain.history import (
    HistorySnapshot,
    SnapshotCompatibility,
    snapshots_compatible,
)
from tenable_reports.domain.fingerprints import (
    FINGERPRINT_VERSION,
    pack_fingerprints,
    unpack_fingerprints,
)


SCHEMA_NAME = "tenable_reports"
MIGRATION_LOCK_NAME = "tenable-reports-schema-migrations"


def _driver() -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "O driver PostgreSQL nao esta instalado. Execute scripts/setup.ps1."
        ) from exc
    return psycopg


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError(
            "O driver PostgreSQL nao esta instalado. Execute scripts/setup.ps1."
        ) from exc
    return Jsonb(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _history_snapshot_storage(
    snapshot: HistorySnapshot,
) -> tuple[dict[str, Any], str, bytes, bytes, bytes]:
    payload = snapshot.to_dict()
    payload.pop("open_finding_keys", None)
    payload.pop("fixed_finding_keys", None)
    payload.pop("resurfaced_finding_keys", None)
    return (
        payload,
        FINGERPRINT_VERSION,
        pack_fingerprints(snapshot.open_finding_keys),
        pack_fingerprints(snapshot.fixed_finding_keys),
        pack_fingerprints(snapshot.resurfaced_finding_keys),
    )


def _compact_legacy_history_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], str, bytes, bytes, bytes]:
    return _history_snapshot_storage(HistorySnapshot.from_dict(payload))


def _history_snapshot_from_storage(
    payload: Mapping[str, Any],
    fingerprint_version: str | None,
    open_fingerprints: bytes | bytearray | memoryview | None,
    fixed_fingerprints: bytes | bytearray | memoryview | None,
    resurfaced_fingerprints: bytes | bytearray | memoryview | None,
) -> HistorySnapshot:
    if not fingerprint_version:
        return HistorySnapshot.from_dict(payload)
    if fingerprint_version != FINGERPRINT_VERSION:
        raise ValueError(
            f"Versão de fingerprint histórico não suportada: {fingerprint_version}"
        )
    data = dict(payload)
    data["fingerprint_version"] = fingerprint_version
    data["open_finding_keys"] = [
        value.hex() for value in unpack_fingerprints(open_fingerprints or b"")
    ]
    data["fixed_finding_keys"] = [
        value.hex() for value in unpack_fingerprints(fixed_fingerprints or b"")
    ]
    data["resurfaced_finding_keys"] = [
        value.hex() for value in unpack_fingerprints(resurfaced_fingerprints or b"")
    ]
    return HistorySnapshot.from_dict(data)


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    path: Path
    kind: str
    sha256: str
    size_bytes: int
    client_id: str | None = None
    run_id: str | None = None
    source_root: Path | None = None
    metadata: Mapping[str, Any] | None = None

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        kind: str,
        client_id: str | None = None,
        run_id: str | None = None,
        source_root: str | Path | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "ArtifactRecord":
        source = Path(path).resolve()
        return cls(
            path=source,
            kind=kind,
            sha256=_sha256(source),
            size_bytes=source.stat().st_size,
            client_id=client_id,
            run_id=run_id,
            source_root=Path(source_root).resolve() if source_root else None,
            metadata=metadata,
        )


class PostgresDatabase:
    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config

    @contextmanager
    def connection(self, *, autocommit: bool = False) -> Iterable[Any]:
        psycopg = _driver()
        connection = psycopg.connect(**self.config.connection_kwargs())
        connection.autocommit = autocommit
        try:
            yield connection
            if not autocommit:
                connection.commit()
        except Exception:
            if not autocommit:
                connection.rollback()
            raise
        finally:
            connection.close()

    @property
    def migration_directory(self) -> Path:
        return Path(__file__).resolve().parent / "postgresql_migrations"

    def apply_migrations(self) -> tuple[str, ...]:
        migrations = tuple(sorted(self.migration_directory.glob("*.sql")))
        if not migrations:
            raise RuntimeError("Nenhuma migration PostgreSQL foi encontrada.")
        applied_now: list[str] = []
        with self.connection(autocommit=True) as connection:
            connection.execute(
                "select pg_advisory_lock(hashtext(%s))", (MIGRATION_LOCK_NAME,)
            )
            try:
                connection.execute(f"create schema if not exists {SCHEMA_NAME}")
                connection.execute(
                    f"""
                    create table if not exists {SCHEMA_NAME}.schema_migrations (
                        version text primary key,
                        checksum text not null,
                        applied_at timestamptz not null default now()
                    )
                    """
                )
                applied = {
                    str(row[0]): str(row[1])
                    for row in connection.execute(
                        f"select version, checksum from {SCHEMA_NAME}.schema_migrations"
                    ).fetchall()
                }
                for path in migrations:
                    version = path.name
                    sql_text = path.read_text(encoding="utf-8")
                    checksum = hashlib.sha256(sql_text.encode("utf-8")).hexdigest()
                    if version in applied:
                        if applied[version] != checksum:
                            raise RuntimeError(
                                f"A migration ja aplicada foi alterada: {version}"
                            )
                        continue
                    with connection.transaction():
                        for statement in sql_text.split(";"):
                            if statement.strip():
                                connection.execute(statement)
                        connection.execute(
                            f"insert into {SCHEMA_NAME}.schema_migrations "
                            "(version, checksum) values (%s, %s)",
                            (version, checksum),
                        )
                    applied_now.append(version)
            finally:
                connection.execute(
                    "select pg_advisory_unlock(hashtext(%s))", (MIGRATION_LOCK_NAME,)
                )
        self.compact_legacy_history()
        return tuple(applied_now)

    def compact_legacy_history(self) -> int:
        """Move listas históricas legadas do JSON para blobs compactos."""
        compacted = 0
        with self.connection() as connection:
            rows = connection.execute(
                f"select snapshot_id, payload from {SCHEMA_NAME}.history_snapshots "
                "where fingerprint_version is null"
            ).fetchall()
            for snapshot_id, payload in rows:
                (
                    compact_payload,
                    version,
                    open_blob,
                    fixed_blob,
                    resurfaced_blob,
                ) = _compact_legacy_history_payload(payload)
                connection.execute(
                    f"update {SCHEMA_NAME}.history_snapshots set "
                    "payload = %s, fingerprint_version = %s, "
                    "open_fingerprints = %s, fixed_fingerprints = %s, "
                    "resurfaced_fingerprints = %s where snapshot_id = %s",
                    (
                        _jsonb(compact_payload),
                        version,
                        open_blob,
                        fixed_blob,
                        resurfaced_blob,
                        str(snapshot_id),
                    ),
                )
                compacted += 1
        return compacted

    def status(self) -> dict[str, Any]:
        tables = (
            "history_snapshots",
            "report_runs",
            "publications",
            "published_documents",
            "orchestration_runs",
            "orchestration_clients",
            "events",
            "report_main_references",
            "report_reference_events",
            "artifacts",
            "legacy_sqlite_imports",
            "plugin_catalog",
        )
        with self.connection() as connection:
            server = connection.execute(
                "select current_database(), current_user, current_setting('server_version')"
            ).fetchone()
            migration_rows = connection.execute(
                f"select version, checksum, applied_at from {SCHEMA_NAME}.schema_migrations "
                "order by version"
            ).fetchall()
            counts = {
                table: int(
                    connection.execute(
                        f"select count(*) from {SCHEMA_NAME}.{table}"
                    ).fetchone()[0]
                )
                for table in tables
            }
        return {
            "location": self.config.safe_location,
            "database": str(server[0]),
            "user": str(server[1]),
            "server_version": str(server[2]),
            "migrations": [
                {
                    "version": str(row[0]),
                    "checksum": str(row[1]),
                    "applied_at": row[2].isoformat(),
                }
                for row in migration_rows
            ],
            "counts": counts,
        }


class PostgresSnapshotRepository(SnapshotRepository):
    def __init__(self, database: PostgresDatabase, *, migrate: bool = True) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    @property
    def location(self) -> str:
        return self.database.config.safe_location

    def publish(self, snapshot: HistorySnapshot) -> None:
        payload, fingerprint_version, open_blob, fixed_blob, resurfaced_blob = (
            _history_snapshot_storage(snapshot)
        )
        try:
            with self.database.connection() as connection:
                inserted = connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.history_snapshots (
                        snapshot_id, client_id, tenant_id, execution_type, period_mode,
                        timezone, metric_definition_version, scope_hash, period_id,
                        period_start_at, period_end_at, run_id, payload,
                        fingerprint_version, open_fingerprints, fixed_fingerprints,
                        resurfaced_fingerprints
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s::timestamptz, %s::timestamptz, %s, %s,
                        %s, %s, %s, %s
                    )
                    on conflict (snapshot_id) do nothing
                    returning snapshot_id
                    """,
                    (
                        snapshot.snapshot_id,
                        snapshot.compatibility.client_id,
                        snapshot.compatibility.tenant_id,
                        snapshot.compatibility.execution_type,
                        snapshot.compatibility.period_mode,
                        snapshot.compatibility.timezone,
                        snapshot.compatibility.metric_definition_version,
                        snapshot.compatibility.scope_hash,
                        snapshot.period_id,
                        snapshot.period_start_at,
                        snapshot.period_end_at,
                        snapshot.run_id,
                        _jsonb(payload),
                        fingerprint_version,
                        open_blob,
                        fixed_blob,
                        resurfaced_blob,
                    ),
                ).fetchone()
                if inserted:
                    return
                existing = connection.execute(
                    f"select payload, fingerprint_version, open_fingerprints, "
                    f"fixed_fingerprints, resurfaced_fingerprints "
                    f"from {SCHEMA_NAME}.history_snapshots "
                    "where snapshot_id = %s",
                    (snapshot.snapshot_id,),
                ).fetchone()
                if existing and (
                    existing[0] == payload
                    and existing[1] == fingerprint_version
                    and bytes(existing[2] or b"") == open_blob
                    and bytes(existing[3] or b"") == fixed_blob
                    and bytes(existing[4] or b"") == resurfaced_blob
                ):
                    return
                raise ValueError(
                    "Ja existe snapshot historico diferente para esta competencia."
                )
        except ValueError:
            raise
        except Exception as exc:
            if getattr(exc, "sqlstate", None) == "23505":
                raise ValueError(
                    "Ja existe snapshot historico diferente para esta competencia."
                ) from exc
            raise

    def compatible_snapshots(
        self,
        compatibility: SnapshotCompatibility,
        *,
        before_period_end_at: str,
    ) -> tuple[HistorySnapshot, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select payload, fingerprint_version, open_fingerprints,
                       fixed_fingerprints, resurfaced_fingerprints
                from {SCHEMA_NAME}.history_snapshots
                where client_id = %s and tenant_id = %s and execution_type = %s
                  and period_mode = %s and timezone = %s
                  and metric_definition_version = %s and scope_hash = %s
                  and period_end_at <= %s::timestamptz
                order by period_end_at asc, snapshot_id asc
                """,
                (
                    compatibility.client_id,
                    compatibility.tenant_id,
                    compatibility.execution_type,
                    compatibility.period_mode,
                    compatibility.timezone,
                    compatibility.metric_definition_version,
                    compatibility.scope_hash,
                    before_period_end_at,
                ),
            ).fetchall()
        snapshots = tuple(_history_snapshot_from_storage(*row) for row in rows)
        if any(
            not snapshots_compatible(compatibility, item.compatibility)
            for item in snapshots
        ):
            raise ValueError("Repositorio retornou snapshot historico incompativel.")
        return snapshots

    def all_snapshots(self) -> tuple[HistorySnapshot, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"select payload, fingerprint_version, open_fingerprints, "
                f"fixed_fingerprints, resurfaced_fingerprints "
                f"from {SCHEMA_NAME}.history_snapshots "
                "order by period_end_at, snapshot_id"
            ).fetchall()
        return tuple(_history_snapshot_from_storage(*row) for row in rows)


class PostgresOperationsRepository:
    def __init__(self, database: PostgresDatabase, *, migrate: bool = True) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def register_artifacts(self, records: Sequence[ArtifactRecord]) -> int:
        if not records:
            return 0
        with self.database.connection() as connection:
            for item in records:
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.artifacts (
                        path, kind, sha256, size_bytes, client_id, run_id,
                        source_root, metadata
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (path) do update set
                        kind = excluded.kind,
                        sha256 = excluded.sha256,
                        size_bytes = excluded.size_bytes,
                        client_id = coalesce(excluded.client_id, {SCHEMA_NAME}.artifacts.client_id),
                        run_id = coalesce(excluded.run_id, {SCHEMA_NAME}.artifacts.run_id),
                        source_root = coalesce(excluded.source_root, {SCHEMA_NAME}.artifacts.source_root),
                        metadata = excluded.metadata,
                        last_seen_at = now()
                    """,
                    (
                        str(item.path.resolve()),
                        item.kind,
                        item.sha256,
                        item.size_bytes,
                        item.client_id,
                        item.run_id,
                        str(item.source_root.resolve()) if item.source_root else None,
                        _jsonb(dict(item.metadata or {})),
                    ),
                )
        return len(records)

    def record_cleanup_status(
        self,
        run_id: str,
        status: str,
        *,
        cleanup_bytes: int = 0,
    ) -> None:
        normalized = str(status).upper()
        allowed = {"NOT_REQUIRED", "PENDING", "COMPLETE", "PARTIAL", "FAILED"}
        if normalized not in allowed:
            raise ValueError("Estado de limpeza inválido.")
        if int(cleanup_bytes) < 0:
            raise ValueError("cleanup_bytes não pode ser negativo.")
        completed = normalized in {"COMPLETE", "PARTIAL", "FAILED"}
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.report_runs
                set cleanup_status = %s,
                    cleanup_bytes = %s,
                    cleanup_completed_at = case when %s then now() else null end,
                    updated_at = now()
                where run_id = %s
                returning run_id
                """,
                (normalized, int(cleanup_bytes), completed, str(run_id)),
            ).fetchone()
        if row is None:
            raise KeyError(f"Execução não encontrada para limpeza: {run_id}")

    def retention_state(self) -> dict[str, Any]:
        with self.database.connection() as connection:
            run_rows = connection.execute(
                f"select run_id, status, client_id, cleanup_status, "
                f"cleanup_completed_at, cleanup_bytes "
                f"from {SCHEMA_NAME}.report_runs"
            ).fetchall()
            history_rows = connection.execute(
                f"select distinct run_id from {SCHEMA_NAME}.history_snapshots"
            ).fetchall()
            main_rows = connection.execute(
                f"select distinct run_id from {SCHEMA_NAME}.report_main_references"
            ).fetchall()
            attempt_rows = connection.execute(
                f"select payload from {SCHEMA_NAME}.orchestration_clients "
                "where payload is not null"
            ).fetchall()
            size_rows = connection.execute(
                f"""
                select client_id, max(run_bytes)
                from (
                    select client_id, run_id, sum(size_bytes) as run_bytes
                    from {SCHEMA_NAME}.artifacts
                    where client_id is not null and run_id is not null
                    group by client_id, run_id
                ) totals
                group by client_id
                """
            ).fetchall()
        retry_required: set[str] = set()
        for row in attempt_rows:
            payload = row[0] if isinstance(row[0], Mapping) else {}
            attempts = payload.get("attempts") or ()
            if not isinstance(attempts, list) or not attempts:
                continue
            last = attempts[-1] if isinstance(attempts[-1], Mapping) else {}
            if last.get("retryable") and last.get("status") != "COMPLETE":
                run_id = str(last.get("run_id") or "")
                if run_id:
                    retry_required.add(run_id)
        return {
            "run_status": {str(row[0]): str(row[1]) for row in run_rows},
            "history_confirmed_run_ids": tuple(str(row[0]) for row in history_rows),
            "main_run_ids": tuple(str(row[0]) for row in main_rows),
            "retry_required_run_ids": tuple(sorted(retry_required)),
            "last_success_bytes_by_client": {
                str(row[0]): int(row[1] or 0) for row in size_rows
            },
            "cleanup_runs": tuple({
                "run_id": str(row[0]),
                "client_id": str(row[2]),
                "status": str(row[3]),
                "completed_at": (
                    row[4].isoformat() if row[4] is not None else None
                ),
                "cleanup_bytes": int(row[5] or 0),
            } for row in run_rows if str(row[3]) in {"PENDING", "PARTIAL", "FAILED"}),
            "pending_cleanup_runs": sum(
                str(row[3]) in {"PENDING", "PARTIAL", "FAILED"}
                for row in run_rows
            ),
            "last_cleanup_at": max(
                (row[4] for row in run_rows if row[4] is not None),
                default=None,
            ).isoformat() if any(row[4] is not None for row in run_rows) else None,
            "last_cleanup_status": next((
                str(row[3])
                for row in sorted(
                    (row for row in run_rows if row[4] is not None),
                    key=lambda row: row[4],
                    reverse=True,
                )
            ), "NEVER_RUN"),
        }

    def record_publication_manifest(self, path: str | Path) -> None:
        source = Path(path).resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        period = payload.get("period") if isinstance(payload.get("period"), Mapping) else {}
        dataset = payload.get("source_dataset")
        dataset = dataset if isinstance(dataset, Mapping) else {}
        history = payload.get("history_store")
        history = history if isinstance(history, Mapping) else {}
        distribution = payload.get("distribution")
        distribution = distribution if isinstance(distribution, Mapping) else {}
        run_id = str(payload.get("run_id") or "")
        with self.database.connection() as connection:
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.report_runs (
                    run_id, client_id, tenant_id, origin, logical_job_id,
                    attempt_number, execution_type, period_id, period_mode,
                    timezone, period_start_at, period_end_at, status, dataset_path,
                    publication_manifest_path, ended_at, metadata
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::timestamptz, %s::timestamptz, %s, %s, %s,
                    %s::timestamptz, %s
                )
                on conflict (run_id) do update set
                    origin = excluded.origin,
                    logical_job_id = excluded.logical_job_id,
                    attempt_number = excluded.attempt_number,
                    status = excluded.status,
                    dataset_path = excluded.dataset_path,
                    publication_manifest_path = excluded.publication_manifest_path,
                    ended_at = excluded.ended_at,
                    metadata = excluded.metadata,
                    updated_at = now()
                """,
                (
                    run_id,
                    str(payload.get("client_id") or ""),
                    str(payload.get("tenant_id") or ""),
                    str(payload.get("origin") or "MANUAL"),
                    str(payload.get("logical_job_id") or run_id),
                    int(payload.get("attempt_number") or 1),
                    str(payload.get("execution_type") or ""),
                    period.get("period_id"),
                    period.get("mode"),
                    period.get("timezone"),
                    period.get("start_at"),
                    period.get("end_at"),
                    str(payload.get("status") or ""),
                    dataset.get("path"),
                    str(source),
                    payload.get("created_at"),
                    _jsonb({"period": dict(period), "documents_valid": True}),
                ),
            )
            publication_id = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.publications (
                    run_id, status, manifest_path, manifest_sha256,
                    source_dataset_path, source_dataset_sha256,
                    history_backend, history_location, distribution_performed,
                    payload, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::timestamptz)
                on conflict (run_id) do update set
                    status = excluded.status,
                    manifest_path = excluded.manifest_path,
                    manifest_sha256 = excluded.manifest_sha256,
                    source_dataset_path = excluded.source_dataset_path,
                    source_dataset_sha256 = excluded.source_dataset_sha256,
                    history_backend = excluded.history_backend,
                    history_location = excluded.history_location,
                    distribution_performed = excluded.distribution_performed,
                    payload = excluded.payload
                returning publication_id
                """,
                (
                    run_id,
                    str(payload.get("status") or ""),
                    str(source),
                    _sha256(source),
                    str(dataset.get("path") or ""),
                    str(dataset.get("sha256") or ""),
                    history.get("backend"),
                    history.get("location"),
                    bool(distribution.get("external_delivery_performed")),
                    _jsonb(payload),
                    payload.get("created_at"),
                ),
            ).fetchone()[0]
            for document in payload.get("documents") or ():
                if not isinstance(document, Mapping):
                    continue
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.published_documents (
                        publication_id, path, sha256, size_bytes, package_status,
                        document_kind, tag_uuid, tag_category, tag_value
                    ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (publication_id, path) do update set
                        sha256 = excluded.sha256,
                        size_bytes = excluded.size_bytes,
                        package_status = excluded.package_status,
                        document_kind = excluded.document_kind,
                        tag_uuid = excluded.tag_uuid,
                        tag_category = excluded.tag_category,
                        tag_value = excluded.tag_value
                    """,
                    (
                        publication_id,
                        document.get("path"),
                        document.get("sha256"),
                        int(document.get("size_bytes") or 0),
                        document.get("package_status"),
                        document.get("document_kind"),
                        document.get("tag_uuid"),
                        document.get("tag_category"),
                        document.get("tag_value"),
                    ),
                )
        records = [
            ArtifactRecord.from_file(
                source,
                kind="publication_manifest",
                client_id=str(payload.get("client_id") or "") or None,
                run_id=run_id or None,
            )
        ]
        dataset_path = Path(str(dataset.get("path") or ""))
        if dataset_path.is_file():
            records.append(
                ArtifactRecord.from_file(
                    dataset_path,
                    kind="report_dataset",
                    client_id=str(payload.get("client_id") or "") or None,
                    run_id=run_id or None,
                )
            )
        for item in payload.get("documents") or ():
            document_path = Path(str(item.get("path") or "")) if isinstance(item, Mapping) else Path()
            if document_path.is_file():
                records.append(
                    ArtifactRecord.from_file(
                        document_path,
                        kind="docx",
                        client_id=str(payload.get("client_id") or "") or None,
                        run_id=run_id or None,
                    )
                )
        self.register_artifacts(records)

    def record_orchestration_manifest(self, path: str | Path) -> None:
        source = Path(path).resolve()
        payload = json.loads(source.read_text(encoding="utf-8"))
        run_id = str(payload.get("run_id") or "")
        clients = payload.get("clients") or ()
        with self.database.connection() as connection:
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.orchestration_runs (
                    run_id, orchestration_id, mode, status, control_directory,
                    manifest_path, notification_path, client_count, failed_count, payload
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (run_id) do update set
                    status = excluded.status,
                    notification_path = excluded.notification_path,
                    client_count = excluded.client_count,
                    failed_count = excluded.failed_count,
                    payload = excluded.payload,
                    updated_at = now()
                """,
                (
                    run_id,
                    payload.get("orchestration_id"),
                    payload.get("mode"),
                    payload.get("status"),
                    payload.get("control_directory"),
                    str(source),
                    payload.get("notifications"),
                    int(payload.get("client_count") or len(clients)),
                    int(payload.get("failed") or 0),
                    _jsonb(payload),
                ),
            )
            for item in clients:
                if not isinstance(item, Mapping):
                    continue
                child_payload = item.get("payload")
                child_payload = child_payload if isinstance(child_payload, Mapping) else None
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.orchestration_clients (
                        orchestration_run_id, client_id, status, exit_code,
                        started_at, ended_at, duration_seconds,
                        publication_manifest_path, log_path, error, payload
                    ) values (
                        %s, %s, %s, %s, %s::timestamptz, %s::timestamptz,
                        %s, %s, %s, %s, %s
                    )
                    on conflict (orchestration_run_id, client_id) do update set
                        status = excluded.status,
                        exit_code = excluded.exit_code,
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        duration_seconds = excluded.duration_seconds,
                        publication_manifest_path = excluded.publication_manifest_path,
                        log_path = excluded.log_path,
                        error = excluded.error,
                        payload = excluded.payload
                    """,
                    (
                        run_id,
                        item.get("client_id"),
                        item.get("status"),
                        item.get("exit_code"),
                        item.get("started_at"),
                        item.get("ended_at"),
                        item.get("duration_seconds"),
                        child_payload.get("publication_manifest") if child_payload else None,
                        item.get("log"),
                        item.get("error"),
                        _jsonb(dict(item)),
                    ),
                )
        notification_path = Path(str(payload.get("notifications") or ""))
        if notification_path.is_file():
            self._record_notification_file(notification_path, run_id)
        self.register_artifacts(
            [ArtifactRecord.from_file(source, kind="orchestration_manifest", run_id=run_id)]
        )

    def _record_notification_file(self, path: Path, orchestration_run_id: str) -> None:
        events: list[Mapping[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                value = json.loads(line)
                if isinstance(value, Mapping):
                    events.append(value)
        with self.database.connection() as connection:
            for index, event in enumerate(events, start=1):
                event_key = hashlib.sha256(
                    f"{path.resolve()}:{index}:{json.dumps(event, sort_keys=True)}".encode()
                ).hexdigest()
                connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.events (
                        event_key, event_at, event_type, orchestration_run_id,
                        client_id, payload
                    ) values (%s, %s::timestamptz, %s, %s, %s, %s)
                    on conflict (event_key) do nothing
                    """,
                    (
                        event_key,
                        event.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                        event.get("event") or "UNKNOWN",
                        orchestration_run_id,
                        event.get("client_id"),
                        _jsonb(dict(event)),
                    ),
                )
        self.register_artifacts(
            [ArtifactRecord.from_file(path, kind="notification_log", run_id=orchestration_run_id)]
        )

    def record_legacy_sqlite(
        self,
        *,
        path: str | Path,
        source_kind: str,
        record_count: int,
        payload: Mapping[str, Any],
    ) -> None:
        source = Path(path).resolve()
        with self.database.connection() as connection:
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.legacy_sqlite_imports (
                    source_path, source_sha256, source_kind, record_count, payload
                ) values (%s, %s, %s, %s, %s)
                on conflict (source_path) do update set
                    source_sha256 = excluded.source_sha256,
                    source_kind = excluded.source_kind,
                    record_count = excluded.record_count,
                    payload = excluded.payload,
                    imported_at = now()
                """,
                (str(source), _sha256(source), source_kind, record_count, _jsonb(dict(payload))),
            )


def provision_postgresql(
    *,
    admin: DatabaseAdminConfig,
    application: DatabaseConfig,
) -> dict[str, Any]:
    if not application.password:
        raise ValueError(
            "TENABLE_REPORTS_DB_PASSWORD e obrigatoria para provisionar o papel de aplicacao."
        )
    psycopg = _driver()
    from psycopg import sql

    connection = psycopg.connect(**admin.connection_kwargs())
    connection.autocommit = True
    try:
        role_exists = connection.execute(
            "select 1 from pg_roles where rolname = %s", (application.user,)
        ).fetchone()
        if role_exists:
            connection.execute(
                sql.SQL(
                    "alter role {} with login password {} nosuperuser nocreatedb "
                    "nocreaterole noinherit connection limit 10"
                ).format(
                    sql.Identifier(application.user),
                    sql.Literal(application.password),
                ),
            )
        else:
            connection.execute(
                sql.SQL(
                    "create role {} with login password {} nosuperuser nocreatedb "
                    "nocreaterole noinherit connection limit 10"
                ).format(
                    sql.Identifier(application.user),
                    sql.Literal(application.password),
                ),
            )
        database_exists = connection.execute(
            "select 1 from pg_database where datname = %s", (application.database,)
        ).fetchone()
        if not database_exists:
            connection.execute(
                sql.SQL("create database {} owner {} encoding 'UTF8'").format(
                    sql.Identifier(application.database),
                    sql.Identifier(application.user),
                )
            )
        else:
            connection.execute(
                sql.SQL("alter database {} owner to {}").format(
                    sql.Identifier(application.database),
                    sql.Identifier(application.user),
                )
            )
        connection.execute(
            sql.SQL("revoke all on database {} from public").format(
                sql.Identifier(application.database)
            )
        )
        connection.execute(
            sql.SQL("grant connect, temporary on database {} to {}").format(
                sql.Identifier(application.database),
                sql.Identifier(application.user),
            )
        )
    finally:
        connection.close()
    database = PostgresDatabase(application)
    applied = database.apply_migrations()
    return {
        "database": application.database,
        "application_user": application.user,
        "location": application.safe_location,
        "migrations_applied": list(applied),
    }
