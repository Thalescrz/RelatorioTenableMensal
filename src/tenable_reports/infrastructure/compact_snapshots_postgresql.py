from __future__ import annotations

from tenable_reports.application.compact_snapshots import CompactFindingSnapshot
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _jsonb,
)


class PostgresCompactSnapshotRepository:
    def __init__(self, database: PostgresDatabase, *, migrate: bool = True) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def publish(self, snapshot: CompactFindingSnapshot) -> None:
        with self.database.connection() as connection:
            inserted = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.compact_finding_snapshots (
                    snapshot_id, schema_version, client_id, tenant_id, run_id,
                    execution_type, period_mode, period_start_at, period_end_at,
                    content_sha256, payload_gzip, record_counts,
                    document_references, created_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s::timestamptz,
                    %s::timestamptz, %s, %s, %s, %s, %s::timestamptz
                )
                on conflict (snapshot_id) do nothing
                returning snapshot_id
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.schema_version,
                    snapshot.client_id,
                    snapshot.tenant_id,
                    snapshot.run_id,
                    snapshot.execution_type,
                    snapshot.period_mode,
                    snapshot.period_start_at,
                    snapshot.period_end_at,
                    snapshot.content_sha256,
                    snapshot.payload_gzip,
                    _jsonb(dict(snapshot.record_counts)),
                    _jsonb(dict(snapshot.document_references)),
                    snapshot.created_at,
                ),
            ).fetchone()
            if inserted is not None:
                return
            existing = connection.execute(
                f"""
                select content_sha256, payload_gzip
                from {SCHEMA_NAME}.compact_finding_snapshots
                where snapshot_id = %s
                """,
                (snapshot.snapshot_id,),
            ).fetchone()
            if existing and (
                str(existing[0]) == snapshot.content_sha256
                and bytes(existing[1]) == snapshot.payload_gzip
            ):
                return
            raise ValueError(
                "Snapshot compacto imutavel ja existe com conteudo diferente."
            )

    def find_exact(
        self,
        *,
        client_id: str,
        tenant_id: str,
        period_start_at: str,
        period_end_at: str,
    ) -> CompactFindingSnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select snapshot_id, schema_version, client_id, tenant_id, run_id,
                       execution_type, period_mode, period_start_at, period_end_at,
                       created_at, content_sha256, payload_gzip, record_counts,
                       document_references
                from {SCHEMA_NAME}.compact_finding_snapshots
                where client_id = %s and tenant_id = %s
                  and period_start_at = %s::timestamptz
                  and period_end_at = %s::timestamptz
                order by published_at desc, created_at desc, run_id desc
                limit 1
                """,
                (client_id, tenant_id, period_start_at, period_end_at),
            ).fetchone()
        if row is None:
            return None
        iso = lambda value: value.isoformat().replace("+00:00", "Z")
        return CompactFindingSnapshot(
            snapshot_id=str(row[0]),
            schema_version=int(row[1]),
            client_id=str(row[2]),
            tenant_id=str(row[3]),
            run_id=str(row[4]),
            execution_type=str(row[5]),
            period_mode=str(row[6]),
            period_start_at=iso(row[7]),
            period_end_at=iso(row[8]),
            created_at=iso(row[9]),
            content_sha256=str(row[10]),
            payload_gzip=bytes(row[11]),
            record_counts={str(key): int(value) for key, value in (row[12] or {}).items()},
            document_references={str(key): str(value) for key, value in (row[13] or {}).items()},
        )

    def find_run(
        self,
        *,
        client_id: str,
        tenant_id: str,
        run_id: str,
    ) -> CompactFindingSnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select snapshot_id, schema_version, client_id, tenant_id, run_id,
                       execution_type, period_mode, period_start_at, period_end_at,
                       created_at, content_sha256, payload_gzip, record_counts,
                       document_references
                from {SCHEMA_NAME}.compact_finding_snapshots
                where client_id = %s and tenant_id = %s and run_id = %s
                order by published_at desc, created_at desc
                limit 1
                """,
                (client_id, tenant_id, run_id),
            ).fetchone()
        if row is None:
            return None
        iso = lambda value: value.isoformat().replace("+00:00", "Z")
        return CompactFindingSnapshot(
            snapshot_id=str(row[0]),
            schema_version=int(row[1]),
            client_id=str(row[2]),
            tenant_id=str(row[3]),
            run_id=str(row[4]),
            execution_type=str(row[5]),
            period_mode=str(row[6]),
            period_start_at=iso(row[7]),
            period_end_at=iso(row[8]),
            created_at=iso(row[9]),
            content_sha256=str(row[10]),
            payload_gzip=bytes(row[11]),
            record_counts={str(key): int(value) for key, value in (row[12] or {}).items()},
            document_references={str(key): str(value) for key, value in (row[13] or {}).items()},
        )
