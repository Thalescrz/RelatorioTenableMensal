from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

import pytest

from tenable_reports.application.report_components import ReportComponentRepository
from tenable_reports.domain.report_components import (
    ComponentAttempt,
    ComponentStage,
    ComponentStatus,
    ReportComponent,
)
from tenable_reports.infrastructure.report_components_postgresql import (
    PostgresReportComponentRepository,
)


ROOT = Path(__file__).resolve().parents[1]


def _attempt(
    component: ReportComponent = ReportComponent.CLOUD,
    status: ComponentStatus = ComponentStatus.FAILED,
    *,
    attempt_number: int = 1,
    retryable: bool = True,
    artifact_references: dict[str, object] | None = None,
) -> ComponentAttempt:
    failed_status = status in {
        ComponentStatus.FAILED,
        ComponentStatus.INTERRUPTED,
    }
    failure_code = None
    if failed_status:
        failure_code = (
            "CLOUD_RENDER_FAILED"
            if component is ReportComponent.CLOUD
            else "COMPONENT_FAILED"
        )
    return ComponentAttempt(
        id=UUID(int=attempt_number),
        client_id="client-a",
        source_run_id="run-a",
        component=component,
        status=status,
        stage=ComponentStage.RENDER,
        attempt_number=attempt_number,
        retryable=retryable,
        failure_code=failure_code,
        failure_message="Falha sanitizada." if status is ComponentStatus.FAILED else None,
        checkpoint_path=str(
            (ROOT / "data/fixtures/checkpoints/client-a/run-a.json").resolve()
        ),
        artifact_references=artifact_references or {},
        created_at="2026-09-01T12:00:00Z",
    )


def _attempt_row(attempt: ComponentAttempt) -> tuple[object, ...]:
    return (
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
        dict(attempt.artifact_references),
        attempt.created_at,
        attempt.started_at,
        attempt.ended_at,
    )


class _Cursor:
    def __init__(self, *, one=None, many=()) -> None:
        self.one = one
        self.many = tuple(many)

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

    @contextmanager
    def connection(self):
        yield self.connection_value


def test_migration_defines_component_attempt_constraints_indexes_and_permissions() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0010_report_component_attempts.sql"
    ).read_text(encoding="utf-8")

    assert "create table if not exists tenable_reports.report_component_attempts" in sql
    for component in ("VM_CORE", "WAS", "CLOUD"):
        assert f"'{component}'" in sql
    for status in (
        "PENDING",
        "RUNNING",
        "COMPLETE",
        "COMPLETE_WITH_WARNINGS",
        "FAILED",
        "INTERRUPTED",
        "SKIPPED",
    ):
        assert f"'{status}'" in sql
    for stage in (
        "COLLECTION",
        "DATASET",
        "RENDER",
        "DOCUMENT_VALIDATION",
        "SNAPSHOT_PUBLICATION",
        "REPORT_PUBLICATION",
    ):
        assert f"'{stage}'" in sql
    assert "unique (source_run_id, component, attempt_number)" in sql
    assert "report_component_attempts_attempt_uq" in sql
    assert "status not in ('FAILED', 'INTERRUPTED') or failure_code is not null" in sql
    assert "status in ('FAILED', 'INTERRUPTED') or failure_code is null" in sql
    assert "report_component_attempts_latest_idx" in sql
    assert "report_component_attempts_retryable_idx" in sql
    assert "revoke all on table tenable_reports.report_component_attempts from public" in sql


def test_repository_creates_and_round_trips_component_attempt() -> None:
    attempt = _attempt(
        artifact_references={"documents": ["cloud.docx"]},
    )
    database = _Database([_Cursor(one=_attempt_row(attempt))])
    repository: ReportComponentRepository = PostgresReportComponentRepository(
        database,
        migrate=False,
    )

    returned = repository.create_attempt(attempt)

    sql, params = database.connection_value.calls[0]
    assert "insert into tenable_reports.report_component_attempts" in sql.lower()
    assert "returning" in sql.lower()
    normalized_sql = " ".join(sql.lower().split())
    assert (
        "on conflict (source_run_id, component, attempt_number) do update"
        in normalized_sql
    )
    assert "report_component_attempts.id = excluded.id" in normalized_sql
    assert (
        "report_component_attempts.artifact_references = excluded.artifact_references"
        in normalized_sql
    )
    assert params is not None
    assert params[:11] == (
        attempt.id,
        "client-a",
        "run-a",
        "CLOUD",
        "FAILED",
        "RENDER",
        1,
        True,
        "CLOUD_RENDER_FAILED",
        "Falha sanitizada.",
        str((ROOT / "data/fixtures/checkpoints/client-a/run-a.json").resolve()),
    )
    assert params[11].obj == {"documents": ["cloud.docx"]}
    assert returned == attempt


def test_repository_rejects_divergent_replay_of_logical_attempt() -> None:
    database = _Database([_Cursor(one=None)])
    repository = PostgresReportComponentRepository(database, migrate=False)

    with pytest.raises(ValueError, match="divergente"):
        repository.create_attempt(_attempt())


def test_repository_lists_latest_attempt_for_each_component_in_enum_order() -> None:
    attempts = (
        _attempt(ReportComponent.VM_CORE, ComponentStatus.COMPLETE, retryable=False),
        _attempt(ReportComponent.WAS, ComponentStatus.FAILED),
        _attempt(ReportComponent.CLOUD, ComponentStatus.FAILED),
    )
    database = _Database([_Cursor(many=tuple(_attempt_row(item) for item in attempts))])
    repository = PostgresReportComponentRepository(database, migrate=False)

    returned = repository.latest_attempts(
        source_run_id="run-a",
        client_id="client-a",
    )

    sql, params = database.connection_value.calls[0]
    normalized_sql = " ".join(sql.lower().split())
    assert "distinct on (component)" in normalized_sql
    assert "attempt_number desc" in normalized_sql
    assert params == ("run-a", "client-a")
    assert tuple(item.component for item in returned) == (
        ReportComponent.VM_CORE,
        ReportComponent.WAS,
        ReportComponent.CLOUD,
    )


def test_repository_rejects_sensitive_artifact_references_before_database_access() -> None:
    database = _Database([])
    repository = PostgresReportComponentRepository(database, migrate=False)
    attempt = _attempt(artifact_references={"secret_key": "fixture-value"})

    with pytest.raises(ValueError, match="credencial"):
        repository.create_attempt(attempt)

    assert database.connection_value.calls == []


@pytest.mark.parametrize("failure_message", ("linha 1\nlinha 2", "x" * 501))
def test_component_attempt_rejects_unsafe_failure_message(
    failure_message: str,
) -> None:
    with pytest.raises(ValueError, match="failure_message"):
        ComponentAttempt(
            id=UUID(int=1),
            client_id="client-a",
            source_run_id="run-a",
            component=ReportComponent.CLOUD,
            status=ComponentStatus.FAILED,
            stage=ComponentStage.RENDER,
            attempt_number=1,
            retryable=True,
            failure_code="CLOUD_RENDER_FAILED",
            failure_message=failure_message,
            artifact_references={},
        )
