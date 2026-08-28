"""PostgreSQL adapter for durable WAS recovery decisions."""

from __future__ import annotations

from typing import Any

from tenable_reports.application.was_recovery import (
    WasRecoveryDecision,
    WasRecoveryRecord,
    WasRecoveryStatus,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _jsonb,
)


_RECOVERY_COLUMNS = """
    run_id, client_id, tenant_id, status, checkpoint_path, checkpoint,
    decision, idempotency_key, created_at, updated_at, decided_at
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _record_from_row(row: Any) -> WasRecoveryRecord:
    return WasRecoveryRecord.from_storage(
        run_id=str(row[0]),
        client_id=str(row[1]),
        tenant_id=str(row[2]),
        status=str(row[3]),
        checkpoint_path=str(row[4]),
        checkpoint=row[5],
        decision=row[6],
        idempotency_key=row[7],
        created_at=_iso(row[8]),
        updated_at=_iso(row[9]),
        decided_at=_iso(row[10]),
    )


class PostgresWasRecoveryRepository:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        migrate: bool = True,
    ) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def upsert(self, record: WasRecoveryRecord) -> WasRecoveryRecord:
        failure = record.checkpoint.was_failure
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.was_recoveries (
                    run_id, client_id, tenant_id, status, checkpoint_path,
                    checkpoint, failure_code, failure_message, retryable,
                    export_uuid, export_origin, remote_status,
                    completed_chunks, total_chunks, progress_made,
                    safe_cancel_available, decision, idempotency_key
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                on conflict (run_id) do update set
                    status = excluded.status,
                    checkpoint_path = excluded.checkpoint_path,
                    checkpoint = excluded.checkpoint,
                    failure_code = excluded.failure_code,
                    failure_message = excluded.failure_message,
                    retryable = excluded.retryable,
                    export_uuid = excluded.export_uuid,
                    export_origin = excluded.export_origin,
                    remote_status = excluded.remote_status,
                    completed_chunks = excluded.completed_chunks,
                    total_chunks = excluded.total_chunks,
                    progress_made = excluded.progress_made,
                    safe_cancel_available = excluded.safe_cancel_available,
                    decision = excluded.decision,
                    idempotency_key = excluded.idempotency_key,
                    decided_at = case
                        when excluded.decision is null then null
                        else {SCHEMA_NAME}.was_recoveries.decided_at
                    end,
                    updated_at = now()
                where {SCHEMA_NAME}.was_recoveries.client_id = excluded.client_id
                  and {SCHEMA_NAME}.was_recoveries.tenant_id = excluded.tenant_id
                returning {_RECOVERY_COLUMNS}
                """,
                (
                    record.run_id,
                    record.client_id,
                    record.tenant_id,
                    record.status.value,
                    record.checkpoint_path,
                    _jsonb(record.checkpoint.to_dict()),
                    failure.code if failure else None,
                    failure.message if failure else None,
                    failure.retryable if failure else False,
                    failure.export_uuid if failure else None,
                    failure.origin if failure else None,
                    failure.remote_status if failure else None,
                    failure.completed_chunks if failure else 0,
                    failure.total_chunks if failure else 0,
                    failure.progress_made if failure else False,
                    failure.safe_cancel_available if failure else False,
                    record.decision.value if record.decision else None,
                    record.idempotency_key,
                ),
            ).fetchone()
        if row is None:
            raise ValueError("Run WAS já pertence a outro cliente ou tenant.")
        return _record_from_row(row)

    def get(
        self,
        run_id: str,
        *,
        client_id: str | None = None,
    ) -> WasRecoveryRecord | None:
        normalized_run_id = _required_text(run_id, "run_id")
        clauses = ["run_id = %s"]
        params: list[Any] = [normalized_run_id]
        if client_id is not None:
            clauses.append("client_id = %s")
            params.append(_required_text(client_id, "client_id"))
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                select {_RECOVERY_COLUMNS}
                from {SCHEMA_NAME}.was_recoveries
                where {' and '.join(clauses)}
                """,
                tuple(params),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def pending(self, *, client_id: str) -> tuple[WasRecoveryRecord, ...]:
        normalized_client_id = _required_text(client_id, "client_id")
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select {_RECOVERY_COLUMNS}
                from {SCHEMA_NAME}.was_recoveries
                where client_id = %s
                  and status in ('WAITING_WAS_DECISION', 'RETRY_AVAILABLE')
                order by updated_at desc, run_id
                """,
                (normalized_client_id,),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def record_decision(
        self,
        run_id: str,
        *,
        client_id: str,
        decision: WasRecoveryDecision,
        idempotency_key: str,
    ) -> WasRecoveryRecord:
        normalized_key = _required_text(idempotency_key, "idempotency_key")
        next_status = (
            WasRecoveryStatus.RETRYING_WAS
            if decision is WasRecoveryDecision.RETRY_WAS
            else WasRecoveryStatus.CONTINUING_WITHOUT_WAS
        )
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.was_recoveries
                set status = %s, decision = %s, idempotency_key = %s,
                    decided_at = coalesce(decided_at, now()), updated_at = now()
                where run_id = %s and client_id = %s
                  and status in (
                      'WAITING_WAS_DECISION', 'RETRY_AVAILABLE',
                      'RETRYING_WAS', 'CONTINUING_WITHOUT_WAS'
                  )
                  and (idempotency_key is null or idempotency_key = %s)
                returning {_RECOVERY_COLUMNS}
                """,
                (
                    next_status.value,
                    decision.value,
                    normalized_key,
                    _required_text(run_id, "run_id"),
                    _required_text(client_id, "client_id"),
                    normalized_key,
                ),
            ).fetchone()
        if row is None:
            raise ValueError("Recuperação WAS indisponível ou decisão conflitante.")
        return _record_from_row(row)

    def mark_complete(self, run_id: str, *, client_id: str) -> WasRecoveryRecord:
        return self._mark_terminal(
            run_id,
            client_id=client_id,
            status=WasRecoveryStatus.COMPLETE,
            timestamp_column="completed_at",
        )

    def mark_expired(self, run_id: str, *, client_id: str) -> WasRecoveryRecord:
        return self._mark_terminal(
            run_id,
            client_id=client_id,
            status=WasRecoveryStatus.EXPIRED,
            timestamp_column="expired_at",
        )

    def _mark_terminal(
        self,
        run_id: str,
        *,
        client_id: str,
        status: WasRecoveryStatus,
        timestamp_column: str,
    ) -> WasRecoveryRecord:
        if timestamp_column not in {"completed_at", "expired_at"}:
            raise ValueError("Coluna terminal WAS inválida.")
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.was_recoveries
                set status = %s, {timestamp_column} = now(), updated_at = now()
                where run_id = %s and client_id = %s
                returning {_RECOVERY_COLUMNS}
                """,
                (
                    status.value,
                    _required_text(run_id, "run_id"),
                    _required_text(client_id, "client_id"),
                ),
            ).fetchone()
        if row is None:
            raise KeyError(f"Recuperação WAS não encontrada: {run_id}")
        return _record_from_row(row)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} não pode ser vazio.")
    return text


__all__ = ["PostgresWasRecoveryRepository"]
