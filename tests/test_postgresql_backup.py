from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tenable_reports.config.database import DatabaseAdminConfig, DatabaseConfig
from tenable_reports.infrastructure.postgresql_backup import (
    PostgreSQLBackupError,
    PostgreSQLBackupManager,
    short_database_name,
)


def _application() -> DatabaseConfig:
    return DatabaseConfig(
        host="db.invalid",
        port=5432,
        database="tenable_reports",
        user="application_role",
        password="do-not-print",
    )


def _admin() -> DatabaseAdminConfig:
    return DatabaseAdminConfig(
        host="db.invalid",
        port=5432,
        database="postgres",
        user="database_admin",
        password="admin-do-not-print",
        sslmode="prefer",
        connect_timeout=10,
    )


def test_pg_dump_uses_custom_format_without_secret_in_arguments(tmp_path: Path) -> None:
    observed: dict[str, object] = {}

    def run(command, **kwargs):
        observed["command"] = tuple(str(item) for item in command)
        observed["env"] = dict(kwargs["env"])
        destination = Path(command[command.index("--file") + 1])
        destination.write_bytes(b"dump")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    manager = PostgreSQLBackupManager(
        application=_application(),
        admin=_admin(),
        runner=run,
        executable_resolver=lambda name: f"/tools/{name}",
    )
    destination = tmp_path / "database.dump"

    manager.create_dump(destination)

    command = observed["command"]
    assert "--format=custom" in command
    assert "--no-owner" in command
    assert "--no-acl" in command
    assert "--schema=tenable_reports" in command
    assert "--no-password" in command
    assert "do-not-print" not in " ".join(command)
    assert observed["env"]["PGPASSWORD"] == "do-not-print"
    assert destination.read_bytes() == b"dump"


def test_pg_dump_failure_is_sanitized_and_removes_partial_file(tmp_path: Path) -> None:
    destination = tmp_path / "database.dump"

    def run(_command, **_kwargs):
        destination.write_bytes(b"partial")
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="password=do-not-print host=db.invalid",
        )

    manager = PostgreSQLBackupManager(
        application=_application(),
        admin=_admin(),
        runner=run,
        executable_resolver=lambda name: f"/tools/{name}",
    )

    with pytest.raises(PostgreSQLBackupError) as error:
        manager.create_dump(destination)

    assert error.value.code == "POSTGRESQL_DUMP_FAILED"
    assert "do-not-print" not in str(error.value)
    assert "db.invalid" not in str(error.value)
    assert not destination.exists()


def test_staging_database_names_are_short_and_identifier_safe() -> None:
    first = short_database_name("staging", "transfer-with-a-very-long-identifier" * 8)
    second = short_database_name("previous", "transfer-with-a-very-long-identifier" * 8)

    assert len(first) <= 63
    assert len(second) <= 63
    assert first.startswith("tr_stg_")
    assert second.startswith("tr_prev_")
    assert first.replace("_", "").isalnum()
