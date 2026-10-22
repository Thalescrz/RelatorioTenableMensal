"""PostgreSQL dump, staging restore, validation and database promotion."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from tenable_reports.application.deployment_transfer import (
    DeploymentDatabaseSnapshot,
)
from tenable_reports.config.database import DatabaseAdminConfig, DatabaseConfig
from tenable_reports.infrastructure.postgresql import SCHEMA_NAME, _driver


class PostgreSQLBackupError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def short_database_name(kind: str, transfer_id: str) -> str:
    prefixes = {"staging": "tr_stg", "previous": "tr_prev", "failed": "tr_fail"}
    try:
        prefix = prefixes[kind]
    except KeyError as exc:
        raise ValueError("Tipo de banco temporário inválido.") from exc
    digest = hashlib.sha256(str(transfer_id).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"[:63]


def _database_name(value: str) -> str:
    normalized = str(value).strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", normalized):
        raise PostgreSQLBackupError("POSTGRESQL_DATABASE_NAME_INVALID")
    return normalized


@dataclass(frozen=True, slots=True)
class _Promotion:
    current: str
    previous: str

    def token(self) -> str:
        return f"{self.current}:{self.previous}"

    @classmethod
    def from_token(cls, value: str) -> "_Promotion":
        try:
            current, previous = str(value).split(":", 1)
        except ValueError as exc:
            raise PostgreSQLBackupError("POSTGRESQL_PROMOTION_TOKEN_INVALID") from exc
        return cls(_database_name(current), _database_name(previous))


Runner = Callable[..., Any]
ExecutableResolver = Callable[[str], str | None]


@dataclass(slots=True)
class PostgreSQLBackupManager:
    application: DatabaseConfig
    admin: DatabaseAdminConfig
    runner: Runner = subprocess.run
    executable_resolver: ExecutableResolver = shutil.which

    def _binary(self, name: str) -> str:
        configured = os.environ.get("TENABLE_REPORTS_POSTGRESQL_BIN", "").strip()
        if configured:
            suffix = ".exe" if os.name == "nt" else ""
            candidate = Path(configured) / f"{name}{suffix}"
            if candidate.is_file():
                return str(candidate)
        found = self.executable_resolver(name)
        if not found:
            raise PostgreSQLBackupError("POSTGRESQL_TOOL_NOT_FOUND")
        return str(found)

    @staticmethod
    def _environment(
        config: DatabaseConfig | DatabaseAdminConfig,
    ) -> dict[str, str]:
        environment = dict(os.environ)
        environment["PGSSLMODE"] = config.sslmode
        environment["PGCONNECT_TIMEOUT"] = str(config.connect_timeout)
        if config.password:
            environment["PGPASSWORD"] = config.password
        else:
            environment.pop("PGPASSWORD", None)
        return environment

    def _run(
        self,
        command: list[str],
        *,
        config: DatabaseConfig | DatabaseAdminConfig,
        code: str,
        timeout: int = 3600,
    ) -> None:
        try:
            completed = self.runner(
                command,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                env=self._environment(config),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PostgreSQLBackupError(code) from exc
        if int(getattr(completed, "returncode", 1)) != 0:
            raise PostgreSQLBackupError(code)

    def create_dump(self, destination: Path) -> None:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        command = [
            self._binary("pg_dump"),
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--no-password",
            f"--schema={SCHEMA_NAME}",
            "--host",
            self.application.host,
            "--port",
            str(self.application.port),
            "--username",
            self.application.user,
            "--dbname",
            self.application.database,
            "--file",
            str(target),
        ]
        try:
            self._run(
                command,
                config=self.application,
                code="POSTGRESQL_DUMP_FAILED",
            )
            if not target.is_file() or target.stat().st_size == 0:
                raise PostgreSQLBackupError("POSTGRESQL_DUMP_FAILED")
        except Exception:
            target.unlink(missing_ok=True)
            raise

    def inventory(self) -> DeploymentDatabaseSnapshot:
        config = self.application
        psycopg = _driver()
        with psycopg.connect(**config.connection_kwargs()) as connection:
            migrations = tuple(
                str(row[0])
                for row in connection.execute(
                    f"select version from {SCHEMA_NAME}.schema_migrations order by version"
                ).fetchall()
            )
            mains = tuple(
                f"{row[0]}:{row[1]}"
                for row in connection.execute(
                    f"select reference_key, run_id from {SCHEMA_NAME}.report_main_references "
                    "order by reference_key"
                ).fetchall()
            )
            rows = connection.execute(
                f"""
                select p.manifest_path
                from {SCHEMA_NAME}.publications p
                join {SCHEMA_NAME}.report_runs r on r.run_id = p.run_id
                where r.deleted_at is null
                union
                select d.path
                from {SCHEMA_NAME}.published_documents d
                join {SCHEMA_NAME}.publications p on p.publication_id = d.publication_id
                join {SCHEMA_NAME}.report_runs r on r.run_id = p.run_id
                where r.deleted_at is null
                order by 1
                """
            ).fetchall()
        return DeploymentDatabaseSnapshot(
            schema_versions=migrations,
            main_references=mains,
            document_paths=tuple(Path(str(row[0])) for row in rows),
        )

    def _admin_command(self, name: str, *arguments: str, code: str) -> None:
        command = [
            self._binary(name),
            "--no-password",
            "--host",
            self.admin.host,
            "--port",
            str(self.admin.port),
            "--username",
            self.admin.user,
            *arguments,
        ]
        self._run(command, config=self.admin, code=code)

    def discard_staging(self, staging_name: str) -> None:
        name = _database_name(staging_name)
        self._admin_command(
            "dropdb",
            "--if-exists",
            "--force",
            name,
            code="POSTGRESQL_STAGING_DROP_FAILED",
        )

    def restore_to_staging(self, dump_path: Path, staging_name: str) -> None:
        dump = Path(dump_path)
        if not dump.is_file():
            raise PostgreSQLBackupError("POSTGRESQL_DUMP_MISSING")
        name = _database_name(staging_name)
        self.discard_staging(name)
        self._admin_command(
            "createdb",
            "--owner",
            self.application.user,
            name,
            code="POSTGRESQL_STAGING_CREATE_FAILED",
        )
        try:
            command = [
                self._binary("pg_restore"),
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--no-password",
                "--role",
                self.application.user,
                "--host",
                self.admin.host,
                "--port",
                str(self.admin.port),
                "--username",
                self.admin.user,
                "--dbname",
                name,
                str(dump),
            ]
            self._run(
                command,
                config=self.admin,
                code="POSTGRESQL_RESTORE_FAILED",
            )
        except Exception:
            self.discard_staging(name)
            raise

    def _inspect(self, database_name: str) -> DeploymentDatabaseSnapshot:
        name = _database_name(database_name)
        psycopg = _driver()
        kwargs = self.admin.connection_kwargs()
        kwargs["dbname"] = name
        with psycopg.connect(**kwargs) as connection:
            migrations = tuple(
                str(row[0])
                for row in connection.execute(
                    f"select version from {SCHEMA_NAME}.schema_migrations order by version"
                ).fetchall()
            )
            mains = tuple(
                f"{row[0]}:{row[1]}"
                for row in connection.execute(
                    f"select reference_key, run_id from {SCHEMA_NAME}.report_main_references "
                    "order by reference_key"
                ).fetchall()
            )
            documents = tuple(
                Path(str(row[0]))
                for row in connection.execute(
                    f"select path from {SCHEMA_NAME}.published_documents order by path"
                ).fetchall()
            )
        return DeploymentDatabaseSnapshot(migrations, mains, documents)

    def inspect_staging(self, staging_name: str) -> DeploymentDatabaseSnapshot:
        return self._inspect(staging_name)

    def rebase_staging_paths(
        self,
        staging_name: str,
        replacements: Mapping[str, str],
    ) -> None:
        name = _database_name(staging_name)
        normalized = {
            str(token): str(path)
            for token, path in replacements.items()
            if len(str(token)) == 64 and Path(str(path)).is_absolute()
        }
        if len(normalized) != len(replacements):
            raise PostgreSQLBackupError("POSTGRESQL_PATH_REBASE_INVALID")
        fields = (
            ("report_runs", "dataset_path"),
            ("report_runs", "publication_manifest_path"),
            ("publications", "manifest_path"),
            ("publications", "source_dataset_path"),
            ("publications", "history_location"),
            ("published_documents", "path"),
            ("artifacts", "path"),
            ("orchestration_runs", "control_directory"),
            ("orchestration_runs", "manifest_path"),
            ("orchestration_runs", "notification_path"),
            ("orchestration_clients", "publication_manifest_path"),
            ("orchestration_clients", "log_path"),
        )
        psycopg = _driver()
        kwargs = self.admin.connection_kwargs()
        kwargs["dbname"] = name
        with psycopg.connect(**kwargs) as connection:
            for table, column in fields:
                query = psycopg.sql.SQL(
                    "select distinct {column} from {schema}.{table} "
                    "where {column} is not null"
                ).format(
                    column=psycopg.sql.Identifier(column),
                    schema=psycopg.sql.Identifier(SCHEMA_NAME),
                    table=psycopg.sql.Identifier(table),
                )
                for row in connection.execute(query).fetchall():
                    original = str(row[0])
                    token = hashlib.sha256(original.encode("utf-8")).hexdigest()
                    replacement = normalized.get(token)
                    if replacement is None:
                        continue
                    update = psycopg.sql.SQL(
                        "update {schema}.{table} set {column} = %s "
                        "where {column} = %s"
                    ).format(
                        schema=psycopg.sql.Identifier(SCHEMA_NAME),
                        table=psycopg.sql.Identifier(table),
                        column=psycopg.sql.Identifier(column),
                    )
                    connection.execute(update, (replacement, original))

    def _maintenance_connection(self) -> Any:
        psycopg = _driver()
        return psycopg.connect(**self.admin.connection_kwargs())

    @staticmethod
    def _identifier(connection: Any, value: str) -> Any:
        psycopg = _driver()
        return psycopg.sql.Identifier(value)

    def _terminate_connections(self, connection: Any, *database_names: str) -> None:
        connection.execute(
            "select pg_terminate_backend(pid) from pg_stat_activity "
            "where datname = any(%s) and pid <> pg_backend_pid()",
            (list(database_names),),
        )

    def promote_staging(self, staging_name: str) -> str:
        staging = _database_name(staging_name)
        current = _database_name(self.application.database)
        previous = short_database_name("previous", staging)
        with self._maintenance_connection() as connection:
            self._terminate_connections(connection, current, staging, previous)
            connection.execute(
                "select 1 from pg_database where datname = %s",
                (staging,),
            )
            with connection.transaction():
                connection.execute(
                    _driver().sql.SQL("alter database {} rename to {}").format(
                        self._identifier(connection, current),
                        self._identifier(connection, previous),
                    )
                )
                connection.execute(
                    _driver().sql.SQL("alter database {} rename to {}").format(
                        self._identifier(connection, staging),
                        self._identifier(connection, current),
                    )
                )
        return _Promotion(current, previous).token()

    def rollback_promotion(self, token: str) -> None:
        promotion = _Promotion.from_token(token)
        failed = short_database_name("failed", promotion.previous)
        with self._maintenance_connection() as connection:
            self._terminate_connections(
                connection,
                promotion.current,
                promotion.previous,
                failed,
            )
            with connection.transaction():
                connection.execute(
                    _driver().sql.SQL("alter database {} rename to {}").format(
                        self._identifier(connection, promotion.current),
                        self._identifier(connection, failed),
                    )
                )
                connection.execute(
                    _driver().sql.SQL("alter database {} rename to {}").format(
                        self._identifier(connection, promotion.previous),
                        self._identifier(connection, promotion.current),
                    )
                )
        self.discard_staging(failed)

    def finalize_promotion(self, token: str) -> None:
        promotion = _Promotion.from_token(token)
        self.discard_staging(promotion.previous)


__all__ = [
    "PostgreSQLBackupError",
    "PostgreSQLBackupManager",
    "short_database_name",
]
