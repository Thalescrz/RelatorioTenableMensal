"""PostgreSQL adapter for compact Tenable Cloud snapshots."""

from __future__ import annotations

from typing import Any, Mapping

from tenable_reports.application.cloud_snapshots import (
    CloudContractCheck,
    CloudReportSnapshot,
    CloudSnapshotCompatibility,
    _parse,
    _safe_capabilities,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _jsonb,
)


_SNAPSHOT_COLUMNS = """
    snapshot_id, schema_version, connector_version, normalizer_version,
    client_id, tenant_id, run_id, attempt_number, execution_type,
    period_mode, timezone, period_start_at, period_end_at, scope_hash,
    metric_definition_version, collected_at, created_at, content_sha256,
    payload_gzip, capabilities, record_counts
"""


_SNAPSHOT_COLUMNS_QUALIFIED = """
    s.snapshot_id, s.schema_version, s.connector_version,
    s.normalizer_version, s.client_id, s.tenant_id, s.run_id,
    s.attempt_number, s.execution_type, s.period_mode, s.timezone,
    s.period_start_at, s.period_end_at, s.scope_hash,
    s.metric_definition_version, s.collected_at, s.created_at,
    s.content_sha256, s.payload_gzip, s.capabilities, s.record_counts
"""

def _iso(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _snapshot_from_row(row: Any) -> CloudReportSnapshot:
    return CloudReportSnapshot(
        snapshot_id=str(row[0]),
        schema_version=int(row[1]),
        connector_version=str(row[2]),
        normalizer_version=str(row[3]),
        client_id=str(row[4]),
        tenant_id=str(row[5]),
        run_id=str(row[6]),
        attempt_number=int(row[7]),
        execution_type=str(row[8]),
        period_mode=str(row[9]),
        timezone=str(row[10]),
        period_start_at=_iso(row[11]),
        period_end_at=_iso(row[12]),
        scope_hash=str(row[13]),
        metric_definition_version=str(row[14]),
        collected_at=_iso(row[15]),
        created_at=_iso(row[16]),
        content_sha256=str(row[17]),
        payload_gzip=bytes(row[18]),
        capabilities=dict(row[19] or {}),
        record_counts={
            str(key): int(value) for key, value in (row[20] or {}).items()
        },
    )


def _compatibility_values(
    compatibility: CloudSnapshotCompatibility,
) -> tuple[Any, ...]:
    return (
        compatibility.client_id,
        compatibility.tenant_id,
        compatibility.execution_type,
        compatibility.period_mode,
        compatibility.timezone,
        compatibility.scope_hash,
        compatibility.metric_definition_version,
        compatibility.connector_version,
        compatibility.normalizer_version,
        compatibility.schema_version,
    )


class PostgresCloudSnapshotRepository:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        migrate: bool = True,
    ) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def publish(self, snapshot: CloudReportSnapshot) -> None:
        with self.database.connection() as connection:
            inserted = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.cloud_report_snapshots (
                    snapshot_id, schema_version, connector_version,
                    normalizer_version, client_id, tenant_id, run_id,
                    attempt_number, execution_type, period_mode, timezone,
                    period_start_at, period_end_at, scope_hash,
                    metric_definition_version, collected_at, created_at,
                    content_sha256, payload_gzip, capabilities, record_counts
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s::timestamptz, %s::timestamptz, %s, %s,
                    %s::timestamptz, %s::timestamptz, %s, %s, %s, %s
                )
                on conflict (snapshot_id) do nothing
                returning snapshot_id
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.schema_version,
                    snapshot.connector_version,
                    snapshot.normalizer_version,
                    snapshot.client_id,
                    snapshot.tenant_id,
                    snapshot.run_id,
                    snapshot.attempt_number,
                    snapshot.execution_type,
                    snapshot.period_mode,
                    snapshot.timezone,
                    snapshot.period_start_at,
                    snapshot.period_end_at,
                    snapshot.scope_hash,
                    snapshot.metric_definition_version,
                    snapshot.collected_at,
                    snapshot.created_at,
                    snapshot.content_sha256,
                    snapshot.payload_gzip,
                    _jsonb(dict(snapshot.capabilities)),
                    _jsonb(dict(snapshot.record_counts)),
                ),
            ).fetchone()
            if inserted is not None:
                return
            existing = connection.execute(
                f"""
                select content_sha256, payload_gzip
                from {SCHEMA_NAME}.cloud_report_snapshots
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
                "Snapshot Cloud imutavel ja existe com conteudo diferente."
            )

    def find_exact(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        period_start_at: str,
        period_end_at: str,
    ) -> CloudReportSnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select {_SNAPSHOT_COLUMNS}
                from {SCHEMA_NAME}.cloud_report_snapshots
                where client_id = %s and tenant_id = %s
                  and execution_type = %s and period_mode = %s
                  and timezone = %s and scope_hash = %s
                  and metric_definition_version = %s
                  and connector_version = %s and normalizer_version = %s
                  and schema_version = %s
                  and period_start_at = %s::timestamptz
                  and period_end_at = %s::timestamptz
                order by collected_at desc, attempt_number desc, run_id desc
                limit 1
                """,
                (
                    *_compatibility_values(compatibility),
                    period_start_at,
                    period_end_at,
                ),
            ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def latest_compatible_since(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        collected_since: str,
    ) -> CloudReportSnapshot | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select {_SNAPSHOT_COLUMNS}
                from {SCHEMA_NAME}.cloud_report_snapshots
                where client_id = %s and tenant_id = %s
                  and execution_type = %s and period_mode = %s
                  and timezone = %s and scope_hash = %s
                  and metric_definition_version = %s
                  and connector_version = %s and normalizer_version = %s
                  and schema_version = %s
                  and collected_at >= %s::timestamptz
                order by collected_at desc, attempt_number desc, run_id desc
                limit 1
                """,
                (*_compatibility_values(compatibility), collected_since),
            ).fetchone()
        return _snapshot_from_row(row) if row is not None else None

    def list_main_before(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        period_end_before: str,
    ) -> tuple[CloudReportSnapshot, ...]:
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select {_SNAPSHOT_COLUMNS_QUALIFIED}
                from {SCHEMA_NAME}.cloud_report_snapshots s
                join {SCHEMA_NAME}.report_main_references m
                  on m.run_id = s.run_id
                join {SCHEMA_NAME}.report_runs r
                  on r.run_id = s.run_id
                where s.client_id = %s and s.tenant_id = %s
                  and s.execution_type = %s and s.period_mode = %s
                  and s.timezone = %s and s.scope_hash = %s
                  and s.metric_definition_version = %s
                  and s.connector_version = %s
                  and s.normalizer_version = %s
                  and s.schema_version = %s
                  and s.period_end_at < %s::timestamptz
                  and r.deleted_at is null
                order by s.period_end_at, s.run_id
                """,
                (*_compatibility_values(compatibility), period_end_before),
            ).fetchall()
        return tuple(_snapshot_from_row(row) for row in rows)

    def save_contract_check(self, check: CloudContractCheck) -> None:
        _parse(check.checked_at)
        capabilities = _safe_capabilities(check.capabilities)
        with self.database.connection() as connection:
            connection.execute(
                f"""
                insert into {SCHEMA_NAME}.cloud_contract_checks (
                    client_id, environment, connector_version,
                    credential_revision, endpoint, required_ready,
                    capabilities, checked_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s::timestamptz
                )
                """,
                (
                    check.client_id,
                    check.environment,
                    check.connector_version,
                    check.credential_revision,
                    check.endpoint,
                    check.required_ready,
                    _jsonb(capabilities),
                    check.checked_at,
                ),
            )

    def latest_contract_check(
        self,
        *,
        client_id: str,
        environment: str,
        connector_version: str,
        credential_revision: str,
    ) -> CloudContractCheck | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select client_id, environment, connector_version,
                       credential_revision, endpoint, required_ready,
                       capabilities, checked_at
                from {SCHEMA_NAME}.cloud_contract_checks
                where client_id = %s and environment = %s
                  and connector_version = %s
                  and credential_revision = %s
                  and invalidated_at is null
                order by checked_at desc, contract_check_id desc
                limit 1
                """,
                (
                    client_id,
                    environment,
                    connector_version,
                    credential_revision,
                ),
            ).fetchone()
        if row is None:
            return None
        return CloudContractCheck(
            client_id=str(row[0]),
            environment=str(row[1]),
            connector_version=str(row[2]),
            credential_revision=str(row[3]),
            endpoint=str(row[4]),
            required_ready=bool(row[5]),
            capabilities=dict(row[6] or {}),
            checked_at=_iso(row[7]),
        )

    def invalidate_contract_checks(
        self,
        *,
        client_id: str,
        environment: str,
    ) -> int:
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"""
                update {SCHEMA_NAME}.cloud_contract_checks
                set invalidated_at = now()
                where client_id = %s and environment = %s
                  and invalidated_at is null
                """,
                (client_id, environment),
            )
            return max(0, int(cursor.rowcount))


__all__ = ["PostgresCloudSnapshotRepository"]
