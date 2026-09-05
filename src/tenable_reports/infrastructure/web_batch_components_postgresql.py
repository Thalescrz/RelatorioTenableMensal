"""PostgreSQL persistence for independently recoverable remote components."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from tenable_reports.domain.remote_components import (
    RemoteComponentState,
    RemoteComponentWindow,
    RemoteIdentifierKind,
    RemoteObservation,
)
from tenable_reports.domain.report_components import ReportComponent
from tenable_reports.infrastructure.postgresql import PostgresDatabase, SCHEMA_NAME


_COMPONENT_COLUMNS = """
    id, batch_job_id, component, state, window_number, attempt_number,
    parent_component_id, origin, deadline_at,
    replacement_created_in_window_2, replacement_created_in_window_3,
    identifier_kind, remote_identifier, identifier_origin, query_fingerprint,
    checkpoint_path, completed_units, total_units, last_remote_status,
    last_contact_at, last_progress_at, worker_id, lease_expires_at,
    failure_code, failure_message, retryable, created_at, started_at, ended_at
"""
_QUALIFIED_COMPONENT_COLUMNS = ", ".join(
    f"component.{column.strip()}" for column in _COMPONENT_COLUMNS.split(",")
)


def _utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _component_from_row(row: Sequence[Any]) -> RemoteComponentWindow:
    return RemoteComponentWindow(
        id=UUID(str(row[0])),
        batch_job_id=UUID(str(row[1])),
        component=ReportComponent(str(row[2])),
        state=RemoteComponentState(str(row[3])),
        window_number=int(row[4]),
        attempt_number=int(row[5]),
        parent_component_id=UUID(str(row[6])) if row[6] is not None else None,
        origin=str(row[7]),
        deadline_at=_utc_datetime(row[8]),  # type: ignore[arg-type]
        replacement_created_in_window_2=bool(row[9]),
        replacement_created_in_window_3=bool(row[10]),
        identifier_kind=(
            RemoteIdentifierKind(str(row[11])) if row[11] is not None else None
        ),
        remote_identifier=str(row[12]) if row[12] is not None else None,
        identifier_origin=str(row[13]) if row[13] is not None else None,
        query_fingerprint=str(row[14]) if row[14] is not None else None,
        checkpoint_path=str(row[15]) if row[15] is not None else None,
        completed_units=int(row[16]),
        total_units=int(row[17]) if row[17] is not None else None,
        last_remote_status=str(row[18]) if row[18] is not None else None,
        last_contact_at=_utc_datetime(row[19]),
        last_progress_at=_utc_datetime(row[20]),
        worker_id=str(row[21]) if row[21] is not None else None,
        lease_expires_at=_utc_datetime(row[22]),
        failure_code=str(row[23]) if row[23] is not None else None,
        failure_message=str(row[24]) if row[24] is not None else None,
        retryable=bool(row[25]),
        created_at=_utc_datetime(row[26]),
        started_at=_utc_datetime(row[27]),
        ended_at=_utc_datetime(row[28]),
    )


class PostgresRemoteComponentRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def create_for_job(
        self,
        *,
        batch_job_id: UUID,
        components: Sequence[ReportComponent],
        window_number: int,
        deadline_at: datetime,
        origin: str,
        query_fingerprints: Mapping[ReportComponent, str] | None = None,
        attempt_number: int | None = None,
        parent_component_id: UUID | None = None,
        replacement_created_in_window_2: bool = False,
        replacement_created_in_window_3: bool = False,
    ) -> tuple[RemoteComponentWindow, ...]:
        normalized = tuple(dict.fromkeys(ReportComponent(value) for value in components))
        if not normalized:
            return ()
        normalized_attempt = int(attempt_number or window_number)
        fingerprints = dict(query_fingerprints or {})
        rows: list[Sequence[Any]] = []
        with self.database.connection() as connection:
            for component in normalized:
                component_id = uuid5(
                    NAMESPACE_URL,
                    f"{batch_job_id}:{component.value}:window:{window_number}:attempt:{normalized_attempt}",
                )
                row = connection.execute(
                    f"""
                    insert into {SCHEMA_NAME}.web_batch_remote_components (
                        id, batch_job_id, component, state, window_number,
                        attempt_number, parent_component_id, origin, deadline_at,
                        replacement_created_in_window_2,
                        replacement_created_in_window_3, query_fingerprint
                    ) values (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    on conflict (batch_job_id, component, attempt_number)
                    do update set batch_job_id = excluded.batch_job_id
                    returning {_COMPONENT_COLUMNS}
                    """,
                    (
                        component_id,
                        batch_job_id,
                        component.value,
                        RemoteComponentState.PENDING.value,
                        window_number,
                        normalized_attempt,
                        parent_component_id,
                        str(origin),
                        deadline_at,
                        replacement_created_in_window_2,
                        replacement_created_in_window_3,
                        fingerprints.get(component),
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("Não foi possível persistir o componente remoto.")
                rows.append(row)
        return tuple(_component_from_row(row) for row in rows)

    def get(self, component_id: UUID) -> RemoteComponentWindow | None:
        with self.database.connection() as connection:
            row = connection.execute(
                f"select {_COMPONENT_COLUMNS} from {SCHEMA_NAME}.web_batch_remote_components where id = %s",
                (component_id,),
            ).fetchone()
        return _component_from_row(row) if row is not None else None

    def claim_next(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> RemoteComponentWindow | None:
        normalized_worker = str(worker_id or "").strip()
        normalized_lease = int(lease_seconds)
        if not normalized_worker:
            raise ValueError("worker_id não pode ser vazio.")
        if normalized_lease < 1:
            raise ValueError("lease_seconds deve ser positivo.")
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                with candidate as (
                    select component.id
                    from {SCHEMA_NAME}.web_batch_remote_components component
                    join {SCHEMA_NAME}.web_batch_jobs job
                      on job.id = component.batch_job_id
                    where component.state in (
                        'PENDING', 'RUNNING_WINDOW_1',
                        'RUNNING_WINDOW_2', 'RUNNING_WINDOW_3'
                    )
                      and (
                          component.worker_id is null
                          or component.lease_expires_at <= now()
                      )
                    order by
                        case component.component
                            when 'VM_CORE' then 1
                            when 'WAS' then 2
                            when 'CLOUD' then 3
                        end,
                        job.position,
                        component.created_at,
                        component.attempt_number,
                        component.id
                    for update skip locked
                    limit 1
                )
                update {SCHEMA_NAME}.web_batch_remote_components component
                set state = case component.window_number
                        when 1 then 'RUNNING_WINDOW_1'
                        when 2 then 'RUNNING_WINDOW_2'
                        when 3 then 'RUNNING_WINDOW_3'
                    end,
                    worker_id = %s,
                    lease_expires_at = now() + (%s * interval '1 second'),
                    started_at = coalesce(component.started_at, now())
                from candidate
                where component.id = candidate.id
                returning {_QUALIFIED_COMPONENT_COLUMNS}
                """,
                (normalized_worker, normalized_lease),
            ).fetchone()
        return _component_from_row(row) if row is not None else None

    def record_observation(
        self,
        component_id: UUID,
        observation: RemoteObservation,
    ) -> RemoteComponentWindow:
        observed_at = datetime.now(UTC)
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_remote_components
                set completed_units = %s,
                    total_units = %s,
                    last_remote_status = %s,
                    last_contact_at = %s,
                    last_progress_at = case
                        when completed_units is distinct from %s
                          or total_units is distinct from %s
                        then %s else last_progress_at
                    end,
                    failure_code = %s
                where id = %s
                returning {_COMPONENT_COLUMNS}
                """,
                (
                    observation.completed_units,
                    observation.total_units,
                    observation.remote_status or observation.kind.value,
                    observed_at,
                    observation.completed_units,
                    observation.total_units,
                    observed_at,
                    observation.failure_code,
                    component_id,
                ),
            ).fetchone()
        if row is None:
            raise KeyError("Componente remoto não encontrado.")
        return _component_from_row(row)

    def transition(
        self,
        component_id: UUID,
        *,
        expected_state: RemoteComponentState,
        requested_state: RemoteComponentState,
        **changes: Any,
    ) -> RemoteComponentWindow:
        allowed = {
            "parent_component_id",
            "origin",
            "deadline_at",
            "replacement_created_in_window_2",
            "replacement_created_in_window_3",
            "identifier_kind",
            "remote_identifier",
            "identifier_origin",
            "query_fingerprint",
            "checkpoint_path",
            "completed_units",
            "total_units",
            "last_remote_status",
            "last_contact_at",
            "last_progress_at",
            "worker_id",
            "lease_expires_at",
            "failure_code",
            "failure_message",
            "retryable",
            "started_at",
            "ended_at",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Campos de transição inválidos: {sorted(unknown)}")
        assignments = ["state = %s"]
        params: list[Any] = [RemoteComponentState(requested_state).value]
        for field_name, value in changes.items():
            assignments.append(f"{field_name} = %s")
            if isinstance(value, StrEnum):
                params.append(value.value)
            else:
                params.append(value)
        params.extend((component_id, RemoteComponentState(expected_state).value))
        with self.database.connection() as connection:
            row = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_remote_components
                set {', '.join(assignments)}
                where id = %s and state = %s
                returning {_COMPONENT_COLUMNS}
                """,
                tuple(params),
            ).fetchone()
        if row is None:
            raise RuntimeError("Transição concorrente do componente remoto.")
        return _component_from_row(row)

    def list_for_jobs(
        self,
        job_ids: Sequence[UUID],
    ) -> dict[UUID, tuple[RemoteComponentWindow, ...]]:
        requested = tuple(dict.fromkeys(job_ids))
        if not requested:
            return {}
        with self.database.connection() as connection:
            rows = connection.execute(
                f"""
                select {_COMPONENT_COLUMNS}
                from {SCHEMA_NAME}.web_batch_remote_components
                where batch_job_id = any(%s)
                order by batch_job_id, attempt_number, component, id
                """,
                (list(requested),),
            ).fetchall()
        grouped: dict[UUID, list[RemoteComponentWindow]] = {
            job_id: [] for job_id in requested
        }
        for row in rows:
            component = _component_from_row(row)
            grouped[component.batch_job_id].append(component)
        return {job_id: tuple(grouped[job_id]) for job_id in requested}

    def reconcile_abandoned(
        self,
        *,
        now: datetime,
        active_worker_ids: set[str],
    ) -> int:
        if now.tzinfo is None:
            raise ValueError("now deve conter timezone.")
        with self.database.connection() as connection:
            cursor = connection.execute(
                f"""
                update {SCHEMA_NAME}.web_batch_remote_components
                set worker_id = null, lease_expires_at = null
                where worker_id is not null
                  and not (worker_id = any(%s))
                  and state in (
                      'RUNNING_WINDOW_1', 'RUNNING_WINDOW_2', 'RUNNING_WINDOW_3'
                  )
                """,
                (sorted(active_worker_ids),),
            )
        return max(0, int(cursor.rowcount))


__all__ = ["PostgresRemoteComponentRepository"]
