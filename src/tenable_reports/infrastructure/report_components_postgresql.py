"""PostgreSQL adapter for durable report component attempts."""

from __future__ import annotations

from typing import Any, Mapping

from tenable_reports.application.web_batches import assert_sanitized_payload
from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentStage,
    ComponentStatus,
    ReportComponent,
)
from tenable_reports.infrastructure.postgresql import (
    SCHEMA_NAME,
    PostgresDatabase,
    _jsonb,
)


_ATTEMPT_COLUMNS = """
    id, client_id, source_run_id, component, status, stage,
    attempt_number, retryable, failure_code, failure_message,
    checkpoint_path, artifact_references, created_at, started_at, ended_at
"""


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


def _attempt_from_row(row: Any) -> ComponentAttempt:
    return ComponentAttempt(
        id=row[0],
        client_id=str(row[1]),
        source_run_id=str(row[2]),
        component=ReportComponent(str(row[3])),
        status=ComponentStatus(str(row[4])),
        stage=ComponentStage(str(row[5])),
        attempt_number=int(row[6]),
        retryable=bool(row[7]),
        failure_code=str(row[8]) if row[8] is not None else None,
        failure_message=str(row[9]) if row[9] is not None else None,
        checkpoint_path=str(row[10]) if row[10] is not None else None,
        artifact_references=row[11] or {},
        created_at=_iso(row[12]),
        started_at=_iso(row[13]),
        ended_at=_iso(row[14]),
    )


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


class PostgresReportComponentRepository:
    def __init__(
        self,
        database: PostgresDatabase,
        *,
        migrate: bool = True,
    ) -> None:
        self.database = database
        if migrate:
            self.database.apply_migrations()

    def create_attempt(self, attempt: ComponentAttempt) -> ComponentAttempt:
        references = _thaw_json(attempt.artifact_references)
        assert_sanitized_payload(
            references,
            path="component_attempt.artifact_references",
        )
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                insert into {SCHEMA_NAME}.report_component_attempts as existing (
                    id, client_id, source_run_id, component, status, stage,
                    attempt_number, retryable, failure_code, failure_message,
                    checkpoint_path, artifact_references, created_at,
                    started_at, ended_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    coalesce(%s, now()), %s, %s
                )
                on conflict (source_run_id, component, attempt_number) do update
                set id = existing.id
                -- report_component_attempts.id = excluded.id
                -- report_component_attempts.artifact_references = excluded.artifact_references
                where existing.id = excluded.id
                  and existing.client_id = excluded.client_id
                  and existing.status = excluded.status
                  and existing.stage = excluded.stage
                  and existing.retryable = excluded.retryable
                  and existing.failure_code is not distinct from excluded.failure_code
                  and existing.failure_message is not distinct from excluded.failure_message
                  and existing.checkpoint_path is not distinct from excluded.checkpoint_path
                  and existing.artifact_references = excluded.artifact_references
                returning {_ATTEMPT_COLUMNS}
                """,
                (
                    attempt.id,
                    attempt.client_id,
                    attempt.source_run_id,
                    attempt.component.value,
                    attempt.status.value,
                    attempt.stage.value,
                    attempt.attempt_number,
                    attempt.retryable,
                    attempt.failure_code,
                    attempt.failure_message,
                    attempt.checkpoint_path,
                    _jsonb(references),
                    attempt.created_at,
                    attempt.started_at,
                    attempt.ended_at,
                ),
            ).fetchone()
        if row is None:
            raise ValueError(
                "Tentativa de componente divergente para a mesma identidade lógica."
            )
        return _attempt_from_row(row)

    def latest_attempts(
        self,
        *,
        source_run_id: str,
        client_id: str,
    ) -> tuple[ComponentAttempt, ...]:
        normalized_run_id = _required_text(source_run_id, "source_run_id")
        normalized_client_id = _required_text(client_id, "client_id")
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select distinct on (component) {_ATTEMPT_COLUMNS}
                from {SCHEMA_NAME}.report_component_attempts
                where source_run_id = %s and client_id = %s
                order by component, attempt_number desc
                """,
                (normalized_run_id, normalized_client_id),
            ).fetchall()
        order = {component: index for index, component in enumerate(ReportComponent)}
        attempts = tuple(_attempt_from_row(row) for row in rows)
        return tuple(sorted(attempts, key=lambda item: order[item.component]))


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} não pode ser vazio.")
    return text


__all__ = ["PostgresReportComponentRepository"]
