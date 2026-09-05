from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from tenable_reports.application.web_batches_memory import (
    InMemoryRemoteComponentRepository,
)
from tenable_reports.domain.remote_components import (
    RemoteComponentState,
    RemoteComponentWindow,
    RemoteIdentifierKind,
    RemoteObservation,
)
from tenable_reports.domain.report_components import ReportComponent
from tenable_reports.infrastructure.web_batch_components_postgresql import (
    PostgresRemoteComponentRepository,
)


JOB_ID = UUID("00000000-0000-0000-0000-000000000301")
DEADLINE = datetime(2026, 9, 4, 20, 0, tzinfo=UTC)


class _Cursor:
    def __init__(self, *, one=None, many=(), rowcount=0) -> None:
        self.one = one
        self.many = tuple(many)
        self.rowcount = rowcount

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class _Connection:
    def __init__(self, cursors) -> None:
        self.cursors = list(cursors)
        self.calls: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self.cursors.pop(0)


class _Database:
    def __init__(self, cursors) -> None:
        self.connection_value = _Connection(cursors)
        self.connection_calls = 0

    @contextmanager
    def connection(self):
        self.connection_calls += 1
        yield self.connection_value


def component_row(
    *,
    component_id: int = 401,
    component: str = "VM_CORE",
    state: str = "PENDING",
    window_number: int = 1,
    attempt_number: int = 1,
    worker_id: str | None = None,
) -> tuple[object, ...]:
    return (
        UUID(int=component_id),
        JOB_ID,
        component,
        state,
        window_number,
        attempt_number,
        None,
        "AUTOMATIC_MONTHLY",
        DEADLINE,
        False,
        False,
        None,
        None,
        None,
        "fingerprint-safe",
        None,
        0,
        None,
        None,
        None,
        None,
        worker_id,
        None,
        None,
        None,
        False,
        datetime(2026, 9, 4, 10, 0, tzinfo=UTC),
        None,
        None,
    )


def test_in_memory_component_claims_are_atomic_and_component_scoped() -> None:
    repository = InMemoryRemoteComponentRepository()
    created = repository.create_for_job(
        batch_job_id=JOB_ID,
        components=(
            ReportComponent.VM_CORE,
            ReportComponent.WAS,
            ReportComponent.CLOUD,
        ),
        window_number=1,
        deadline_at=DEADLINE,
        origin="AUTOMATIC_MONTHLY",
        query_fingerprints={
            ReportComponent.VM_CORE: "vm-safe",
            ReportComponent.WAS: "was-safe",
            ReportComponent.CLOUD: "cloud-safe",
        },
    )

    first = repository.claim_next(worker_id="remote-1")
    second = repository.claim_next(worker_id="remote-2")

    assert tuple(item.component for item in created) == tuple(ReportComponent)
    assert first is not None and first.component is ReportComponent.VM_CORE
    assert second is not None and second.component is ReportComponent.WAS
    assert first.state is RemoteComponentState.RUNNING_WINDOW_1
    assert second.state is RemoteComponentState.RUNNING_WINDOW_1
    assert first.id != second.id


def test_in_memory_component_attempt_key_is_idempotent() -> None:
    repository = InMemoryRemoteComponentRepository()
    arguments = {
        "batch_job_id": JOB_ID,
        "components": (ReportComponent.VM_CORE,),
        "window_number": 1,
        "deadline_at": DEADLINE,
        "origin": "AUTOMATIC_MONTHLY",
        "query_fingerprints": {ReportComponent.VM_CORE: "vm-safe"},
    }

    first = repository.create_for_job(**arguments)
    second = repository.create_for_job(**arguments)

    assert second == first
    assert len(repository.list_for_jobs((JOB_ID,))[JOB_ID]) == 1


def test_postgresql_create_uses_one_short_connection_and_returns_components() -> None:
    database = _Database(
        [
            _Cursor(one=component_row(component_id=401, component="VM_CORE")),
            _Cursor(one=component_row(component_id=402, component="WAS")),
            _Cursor(one=component_row(component_id=403, component="CLOUD")),
        ]
    )
    repository = PostgresRemoteComponentRepository(database)

    created = repository.create_for_job(
        batch_job_id=JOB_ID,
        components=tuple(ReportComponent),
        window_number=1,
        deadline_at=DEADLINE,
        origin="AUTOMATIC_MONTHLY",
        query_fingerprints={component: f"{component.value.lower()}-safe" for component in ReportComponent},
    )

    assert tuple(item.component for item in created) == tuple(ReportComponent)
    assert database.connection_calls == 1
    assert len(database.connection_value.calls) == 3
    sql, params = database.connection_value.calls[0]
    assert "on conflict (batch_job_id, component, attempt_number)" in " ".join(
        sql.lower().split()
    )
    assert params[1:6] == (JOB_ID, "VM_CORE", "PENDING", 1, 1)


def test_postgresql_claim_uses_skip_locked_and_closes_before_runner_work() -> None:
    claimed_row = component_row(
        component="VM_CORE",
        state="RUNNING_WINDOW_1",
        worker_id="remote-1",
    )
    database = _Database([_Cursor(one=claimed_row)])
    repository = PostgresRemoteComponentRepository(database)

    claimed = repository.claim_next(worker_id="remote-1", lease_seconds=60)

    assert claimed is not None
    assert claimed.worker_id == "remote-1"
    assert claimed.state is RemoteComponentState.RUNNING_WINDOW_1
    sql, params = database.connection_value.calls[0]
    normalized = " ".join(sql.lower().split())
    assert "for update skip locked" in normalized
    assert "lease_expires_at" in normalized
    assert params[-2:] == ("remote-1", 60)


def test_postgresql_claim_qualifies_returned_columns_from_update_join() -> None:
    database = _Database([_Cursor(one=None)])
    repository = PostgresRemoteComponentRepository(database)

    assert repository.claim_next(worker_id="remote-1", lease_seconds=60) is None

    sql, _params = database.connection_value.calls[0]
    returning_clause = " ".join(sql.lower().split()).split(" returning ", 1)[1]
    assert returning_clause.startswith("component.id, component.batch_job_id")


def test_postgresql_transition_is_optimistic() -> None:
    completed = list(component_row(state="COMPLETE"))
    completed[28] = datetime(2026, 9, 4, 11, 0, tzinfo=UTC)
    database = _Database([_Cursor(one=tuple(completed))])
    repository = PostgresRemoteComponentRepository(database)

    result = repository.transition(
        UUID(int=401),
        expected_state=RemoteComponentState.RUNNING_WINDOW_1,
        requested_state=RemoteComponentState.COMPLETE,
        completed_units=2,
        total_units=2,
        ended_at=datetime(2026, 9, 4, 11, 0, tzinfo=UTC),
    )

    assert result.state is RemoteComponentState.COMPLETE
    sql, params = database.connection_value.calls[0]
    assert "where id = %s and state = %s" in " ".join(sql.lower().split())
    assert params[-2:] == (UUID(int=401), "RUNNING_WINDOW_1")


def test_postgresql_transition_rejects_stale_expected_state() -> None:
    database = _Database([_Cursor(one=None)])
    repository = PostgresRemoteComponentRepository(database)

    with pytest.raises(RuntimeError, match="concorrente"):
        repository.transition(
            UUID(int=401),
            expected_state=RemoteComponentState.RUNNING_WINDOW_1,
            requested_state=RemoteComponentState.COMPLETE,
        )


def test_postgresql_records_observation_without_holding_a_connection() -> None:
    processing = list(component_row(state="RUNNING_WINDOW_1"))
    processing[16] = 1
    processing[17] = 3
    processing[18] = "PROCESSING"
    database = _Database([_Cursor(one=tuple(processing))])
    repository = PostgresRemoteComponentRepository(database)

    updated = repository.record_observation(
        UUID(int=401),
        RemoteObservation.processing(completed=1, total=3),
    )

    assert updated.completed_units == 1
    assert updated.total_units == 3
    assert database.connection_calls == 1


def test_remote_failure_message_rejects_secret_like_content() -> None:
    with pytest.raises(ValueError, match="sanitizada"):
        RemoteComponentWindow(
            id=UUID(int=401),
            batch_job_id=JOB_ID,
            component=ReportComponent.VM_CORE,
            state=RemoteComponentState.WAITING_MANUAL_RETRY,
            window_number=2,
            attempt_number=2,
            origin="AUTOMATIC_RECOVERY",
            deadline_at=DEADLINE,
            identifier_kind=RemoteIdentifierKind.UUID,
            remote_identifier="00000000-0000-0000-0000-000000000401",
            failure_code="REMOTE_TIMEOUT",
            failure_message="access_key=segredo",
            retryable=True,
        )


def test_postgresql_releases_component_owned_by_worker_from_previous_process() -> None:
    database = _Database([_Cursor(rowcount=1)])
    repository = PostgresRemoteComponentRepository(database)

    reconciled = repository.reconcile_abandoned(
        now=datetime(2026, 9, 4, 9, 59, tzinfo=UTC),
        active_worker_ids={"new-process-worker"},
    )

    assert reconciled == 1
    sql, params = database.connection_value.calls[0]
    normalized_sql = " ".join(sql.lower().split())
    assert "not (worker_id = any(%s))" in normalized_sql
    assert "lease_expires_at <=" not in normalized_sql
    assert params == (["new-process-worker"],)


def test_in_memory_releases_previous_worker_without_extending_deadline() -> None:
    repository = InMemoryRemoteComponentRepository()
    (pending,) = repository.create_for_job(
        batch_job_id=JOB_ID,
        components=(ReportComponent.VM_CORE,),
        window_number=1,
        deadline_at=DEADLINE,
        origin="SCHEDULED",
    )
    claimed = repository.claim_next(
        worker_id="old-process-worker",
        lease_seconds=36_300,
    )

    assert repository.reconcile_abandoned(
        now=datetime.now(UTC),
        active_worker_ids={"new-process-worker"},
    ) == 1
    reclaimed = repository.claim_next(
        worker_id="new-process-worker",
        lease_seconds=36_300,
    )

    assert reclaimed.id == pending.id
    assert reclaimed.deadline_at == claimed.deadline_at == DEADLINE


def test_in_memory_claims_one_vm_per_client_before_secondary_components() -> None:
    repository = InMemoryRemoteComponentRepository()
    for job_id in (UUID(int=601), UUID(int=602)):
        repository.create_for_job(
            batch_job_id=job_id,
            components=tuple(ReportComponent),
            window_number=1,
            deadline_at=DEADLINE,
            origin="SCHEDULED",
        )

    first = repository.claim_next(worker_id="worker-1")
    second = repository.claim_next(worker_id="worker-2")

    assert first.component is ReportComponent.VM_CORE
    assert second.component is ReportComponent.VM_CORE
    assert first.batch_job_id != second.batch_job_id


def test_terminal_remote_failure_requires_a_failure_code() -> None:
    with pytest.raises(ValueError, match="failure_code"):
        RemoteComponentWindow(
            id=UUID(int=401),
            batch_job_id=JOB_ID,
            component=ReportComponent.WAS,
            state=RemoteComponentState.WAITING_MANUAL_RETRY,
            window_number=2,
            attempt_number=2,
            origin="AUTOMATIC_RECOVERY",
            deadline_at=DEADLINE,
            retryable=True,
        )


def test_retryable_flag_is_rejected_for_a_complete_component() -> None:
    with pytest.raises(ValueError, match="retryable"):
        RemoteComponentWindow(
            id=UUID(int=401),
            batch_job_id=JOB_ID,
            component=ReportComponent.CLOUD,
            state=RemoteComponentState.COMPLETE,
            window_number=1,
            attempt_number=1,
            origin="AUTOMATIC_MONTHLY",
            deadline_at=DEADLINE,
            retryable=True,
        )
