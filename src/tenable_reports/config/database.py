from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from tenable_reports.config.environment import EnvironmentError


_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


def _positive_int(name: str, raw: str, *, default: int) -> int:
    try:
        value = int(raw or default)
    except ValueError as exc:
        raise EnvironmentError(f"{name} deve ser um numero inteiro.") from exc
    if value < 1:
        raise EnvironmentError(f"{name} deve ser maior que zero.")
    return value


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    host: str
    port: int
    database: str
    user: str
    password: str | None
    sslmode: str = "prefer"
    connect_timeout: int = 10
    application_name: str = "tenable-reports"

    @classmethod
    def is_configured(cls, environ: Mapping[str, str] | None = None) -> bool:
        values = os.environ if environ is None else environ
        return any(
            str(values.get(name) or "").strip()
            for name in (
                "TENABLE_REPORTS_DB_HOST",
                "TENABLE_REPORTS_DB_NAME",
                "TENABLE_REPORTS_DB_USER",
                "TENABLE_REPORTS_DB_PASSWORD",
            )
        )

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DatabaseConfig":
        values = os.environ if environ is None else environ
        host = str(values.get("TENABLE_REPORTS_DB_HOST") or "127.0.0.1").strip()
        database = str(values.get("TENABLE_REPORTS_DB_NAME") or "").strip()
        user = str(values.get("TENABLE_REPORTS_DB_USER") or "").strip()
        if not database:
            raise EnvironmentError("TENABLE_REPORTS_DB_NAME nao foi configurado.")
        if not user:
            raise EnvironmentError("TENABLE_REPORTS_DB_USER nao foi configurado.")
        sslmode = str(values.get("TENABLE_REPORTS_DB_SSLMODE") or "prefer").strip()
        if sslmode not in _SSL_MODES:
            raise EnvironmentError(
                "TENABLE_REPORTS_DB_SSLMODE deve ser disable, allow, prefer, "
                "require, verify-ca ou verify-full."
            )
        password = str(values.get("TENABLE_REPORTS_DB_PASSWORD") or "") or None
        return cls(
            host=host,
            port=_positive_int(
                "TENABLE_REPORTS_DB_PORT",
                str(values.get("TENABLE_REPORTS_DB_PORT") or "5432"),
                default=5432,
            ),
            database=database,
            user=user,
            password=password,
            sslmode=sslmode,
            connect_timeout=_positive_int(
                "TENABLE_REPORTS_DB_CONNECT_TIMEOUT",
                str(values.get("TENABLE_REPORTS_DB_CONNECT_TIMEOUT") or "10"),
                default=10,
            ),
            application_name=str(
                values.get("TENABLE_REPORTS_DB_APPLICATION_NAME")
                or "tenable-reports"
            ).strip(),
        )

    @property
    def safe_location(self) -> str:
        return f"postgresql://{self.user}@{self.host}:{self.port}/{self.database}"

    def connection_kwargs(self) -> dict[str, object]:
        result: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout,
            "application_name": self.application_name,
        }
        if self.password:
            result["password"] = self.password
        return result


@dataclass(frozen=True, slots=True)
class DatabaseAdminConfig:
    host: str
    port: int
    database: str
    user: str
    password: str | None
    sslmode: str
    connect_timeout: int

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> "DatabaseAdminConfig":
        values = os.environ if environ is None else environ
        app = DatabaseConfig.from_environment(values)
        return cls(
            host=str(
                values.get("TENABLE_REPORTS_ADMIN_HOST") or app.host
            ).strip(),
            port=_positive_int(
                "TENABLE_REPORTS_ADMIN_PORT",
                str(values.get("TENABLE_REPORTS_ADMIN_PORT") or app.port),
                default=app.port,
            ),
            database=str(
                values.get("TENABLE_REPORTS_ADMIN_DB") or "postgres"
            ).strip(),
            user=str(values.get("TENABLE_REPORTS_ADMIN_USER") or "postgres").strip(),
            password=(
                str(values.get("TENABLE_REPORTS_ADMIN_PASSWORD") or "") or None
            ),
            sslmode=str(
                values.get("TENABLE_REPORTS_ADMIN_SSLMODE") or app.sslmode
            ).strip(),
            connect_timeout=app.connect_timeout,
        )

    def connection_kwargs(self) -> dict[str, object]:
        result: dict[str, object] = {
            "host": self.host,
            "port": self.port,
            "dbname": self.database,
            "user": self.user,
            "sslmode": self.sslmode,
            "connect_timeout": self.connect_timeout,
            "application_name": "tenable-reports-bootstrap",
        }
        if self.password:
            result["password"] = self.password
        return result
