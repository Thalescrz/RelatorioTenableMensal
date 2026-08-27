from __future__ import annotations

import importlib
from contextlib import contextmanager
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


class Cursor:
    def __init__(self, *, one: Any = None, all_rows: tuple[Any, ...] = ()) -> None:
        self.one = one
        self.all_rows = all_rows

    def fetchone(self) -> Any:
        return self.one

    def fetchall(self) -> tuple[Any, ...]:
        return self.all_rows


class RecordingConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...] | None]] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> Cursor:
        self.calls.append((sql, params))
        if "insert into tenable_reports.cloud_report_snapshots" in sql:
            return Cursor(one=("snapshot-fixture",))
        return Cursor()


class FakeDatabase:
    def __init__(self) -> None:
        self.connection_value = RecordingConnection()
        self.migrations = 0

    def apply_migrations(self) -> tuple[str, ...]:
        self.migrations += 1
        return ()

    @contextmanager
    def connection(self):
        yield self.connection_value


def _snapshot() -> Any:
    module = importlib.import_module(
        "tenable_reports.application.cloud_snapshots"
    )
    return module.build_cloud_snapshot(
        dataset={
            "schema_version": 1,
            "document_kind": "cloud",
            "metric_definition_version": "cloud-metrics-v1",
            "overview": {"assets": 0},
        },
        client_id="cliente-fixture",
        tenant_id="tenant-fixture",
        run_id="run-fixture",
        attempt_number=1,
        execution_type="MANUAL",
        period_mode="EXPLICIT_RANGE",
        timezone="UTC",
        period_start_at="2026-07-01T00:00:00Z",
        period_end_at="2026-08-01T00:00:00Z",
        scope_hash="scope-cloud-v1",
        collected_at="2026-08-26T12:00:00Z",
        capabilities={"required_ready": True},
    )


def test_cloud_migration_is_additive_compact_and_supports_document_variants() -> None:
    sql = (
        ROOT
        / "src/tenable_reports/infrastructure/postgresql_migrations/0007_cloud_reports.sql"
    ).read_text(encoding="utf-8")

    assert "create table if not exists tenable_reports.cloud_report_snapshots" in sql
    assert "payload_gzip bytea" in sql
    assert "content_sha256" in sql
    assert "references tenable_reports.report_runs(run_id)" in sql
    assert "create table if not exists tenable_reports.cloud_contract_checks" in sql
    assert "credential_revision" in sql
    assert "api_secret" not in sql.lower()
    assert "document_variant" in sql
    assert "'cloud'" in sql
    assert "'base', 'expanded'" in sql


def test_postgres_repository_publishes_snapshot_without_decoding_payload() -> None:
    module = importlib.import_module(
        "tenable_reports.infrastructure.cloud_snapshots_postgresql"
    )
    database = FakeDatabase()
    repository = module.PostgresCloudSnapshotRepository(
        database,
        migrate=False,
    )

    repository.publish(_snapshot())

    sql, params = database.connection_value.calls[0]
    assert "cloud_report_snapshots" in sql
    assert params is not None
    assert any(
        isinstance(value, bytes) and value.startswith(b"\x1f\x8b")
        for value in params
    )


def test_postgres_repository_exposes_replay_history_and_contract_methods() -> None:
    module = importlib.import_module(
        "tenable_reports.infrastructure.cloud_snapshots_postgresql"
    )
    expected = {
        "publish",
        "find_exact",
        "latest_compatible_since",
        "list_main_before",
        "save_contract_check",
        "latest_contract_check",
        "invalidate_contract_checks",
    }

    assert expected.issubset(set(dir(module.PostgresCloudSnapshotRepository)))


def test_main_history_query_qualifies_snapshot_columns() -> None:
    snapshots = importlib.import_module(
        "tenable_reports.application.cloud_snapshots"
    )
    module = importlib.import_module(
        "tenable_reports.infrastructure.cloud_snapshots_postgresql"
    )
    database = FakeDatabase()
    repository = module.PostgresCloudSnapshotRepository(
        database,
        migrate=False,
    )
    compatibility = snapshots.CloudSnapshotCompatibility(
        client_id="cliente-fixture",
        tenant_id="tenant-fixture",
        execution_type="MANUAL",
        period_mode="EXPLICIT_RANGE",
        timezone="UTC",
        scope_hash="scope-cloud-v1",
        metric_definition_version="cloud-metrics-v1",
        connector_version="cloud-graphql-v1",
        normalizer_version="cloud-normalizer-v1",
        schema_version=1,
    )

    assert repository.list_main_before(
        compatibility=compatibility,
        period_end_before="2026-09-01T00:00:00Z",
    ) == ()

    sql, _ = database.connection_value.calls[-1]
    assert "select s.snapshot_id, s.schema_version" in " ".join(sql.split())
    assert "join tenable_reports.report_main_references" in sql
